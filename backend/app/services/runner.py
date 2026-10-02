"""Дневното читање: прочитај ги сите ценовници и запиши ги попустите.

Еден маркет што падне не ги соборува останатите - секоја продавница е свое
читање со свој запис во PricelistRun.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import default_grouper
from app.catalog.groups import GROUPS, SUBCATEGORIES
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models import Chain, ProductCategory
from app.models.enums import RunStatus
from app.readers import PoliteClient, get_reader_class
from app.readers.base import PricelistReader, ReaderError
from app.readers.registry import default_chain_codes
from app.services.ingest import (
    ensure_categories,
    ensure_chain,
    ingest_store,
    today_local,
)

log = get_logger(__name__)


@dataclass(slots=True)
class ChainOutcome:
    """Исходот од еден синџир."""

    chain_code: str
    stores: int = 0
    succeeded: int = 0
    failed: int = 0
    unchanged: int = 0
    empty: int = 0
    discounts: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0 and not self.errors


async def run_chain(
    session: AsyncSession,
    reader: PricelistReader,
    *,
    run_date: date,
    store_limit: int | None = None,
) -> ChainOutcome:
    """Чита еден синџир: откриј продавници, па прочитај ги една по една."""
    outcome = ChainOutcome(chain_code=reader.chain_code)
    chain = await ensure_chain(session, type(reader))
    categories = await ensure_categories(session, GROUPS, SUBCATEGORIES)
    grouper = default_grouper()

    try:
        refs = await reader.discover_stores()
    except ReaderError as error:
        # Откривањето падна - нема што да се чита. Се запишува како грешка.
        outcome.errors.append(f"discover_stores: {error}")
        log.error("%s: откривањето на продавници падна: %s", reader.chain_code, error)
        return outcome

    if store_limit is not None:
        refs = refs[:store_limit]
    outcome.stores = len(refs)

    chain_id = chain.id
    category_slugs = {slug: row.id for slug, row in categories.items()}
    await session.commit()

    concurrency = max(1, reader.store_concurrency)
    limit = asyncio.Semaphore(concurrency)

    # Секоја напоредна задача добива СВОЈ клиент, за да паузата меѓу
    # барања важи по врска. Со еден споделен клиент сите би чекале во ист
    # ред и напоредноста не би дала ништо.
    pool: list[PoliteClient] = [PoliteClient() for _ in range(concurrency)]
    readers = [type(reader)(client) for client in pool]  # type: ignore[call-arg]
    free: asyncio.Queue = asyncio.Queue()
    for worker in readers:
        free.put_nowait(worker)

    async def one(ref) -> tuple[RunStatus, int]:
        """Една продавница, во своја сесија.

        Своја сесија е задолжително: AsyncSession не смее да се дели меѓу
        напоредни задачи. Така и потврдувањето е по продавница - ако
        следната падне, претходните остануваат запишани.
        """
        own_reader = await free.get()
        try:
            async with limit, SessionLocal() as own:
                own_chain = await own.get(Chain, chain_id)
                own_categories = {
                    slug: await own.get(ProductCategory, cid)
                    for slug, cid in category_slugs.items()
                }
                run = await ingest_store(
                    own,
                    own_reader,
                    own_chain,
                    ref,
                    run_date=run_date,
                    grouper=grouper,
                    categories=own_categories,
                )
                status = run.status
                discounts = run.rows_discount
                message = run.error_message
                await own.commit()
        finally:
            free.put_nowait(own_reader)

        failed = status in (RunStatus.FAILED, RunStatus.STRUCTURE_CHANGED)
        if failed and message and len(outcome.errors) < 5:
            outcome.errors.append(f"{ref.name}: {message[:160]}")
        return status, discounts

    try:
        results = await asyncio.gather(
            *(one(ref) for ref in refs), return_exceptions=True
        )
    finally:
        for client in pool:
            await client.aclose()

    for ref, result in zip(refs, results, strict=True):
        if isinstance(result, BaseException):
            outcome.failed += 1
            if len(outcome.errors) < 5:
                outcome.errors.append(f"{ref.name}: {result}")
            continue

        status, discounts = result
        match status:
            case RunStatus.SUCCESS:
                outcome.succeeded += 1
                outcome.discounts += discounts
            case RunStatus.UNCHANGED:
                outcome.unchanged += 1
            case RunStatus.EMPTY:
                # Изворот нема што да објави - не е наша грешка.
                outcome.empty += 1
            case _:
                outcome.failed += 1

    return outcome


async def _run_one(code: str, *, run_date: date, store_limit: int | None) -> ChainOutcome:
    """Еден синџир, со свој HTTP клиент и своја сесија кон базата."""
    reader_class = get_reader_class(code)
    client = PoliteClient()
    reader = reader_class(client)  # type: ignore[call-arg]
    try:
        async with SessionLocal() as session:
            outcome = await run_chain(
                session, reader, run_date=run_date, store_limit=store_limit
            )
    except Exception as error:
        log.exception("%s: читањето падна неочекувано", code)
        return ChainOutcome(chain_code=code, errors=[str(error)[:200]])
    finally:
        await client.aclose()

    log.info(
        "%s: %d/%d продавници, %d попусти, %d непроменети, %d паднати",
        code,
        outcome.succeeded,
        outcome.stores,
        outcome.discounts,
        outcome.unchanged,
        outcome.failed,
    )
    return outcome


async def run_all(
    *,
    run_date: date | None = None,
    chain_codes: list[str] | None = None,
    store_limit: int | None = None,
) -> list[ChainOutcome]:
    """Го врти дневното читање за сите (или одбрани) синџири.

    Синџирите се читаат напоредно: секој има свој HTTP клиент, па паузата
    меѓу барања останува по домаќин - Веро не чека на Рамстор.

    Веро има 21 страница по продавница, што со учтивата пауза е околу
    минута по продавница; напоредно читањето трае колку најбавниот синџир,
    не колку збирот.
    """
    run_date = run_date or today_local()
    # Прескокнатите синџири (паднат извор) влегуваат само изрично.
    codes = chain_codes or default_chain_codes()

    log.info("Дневно читање за %s: %s", run_date, ", ".join(codes))

    tasks = [_run_one(code, run_date=run_date, store_limit=store_limit) for code in codes]
    return list(await asyncio.gather(*tasks))
