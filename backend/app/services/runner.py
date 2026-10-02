"""Дневното читање: прочитај ги сите ценовници и запиши ги попустите.

Еден маркет што падне не ги соборува останатите - секоја продавница е свое
читање со свој запис во PricelistRun.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import default_grouper
from app.catalog.groups import GROUPS
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.enums import RunStatus
from app.readers import PoliteClient, get_reader_class
from app.readers.base import PricelistReader, ReaderError
from app.readers.registry import READERS
from app.services.ingest import ensure_chain, ensure_groups, ingest_store, today_local

log = get_logger(__name__)


@dataclass(slots=True)
class ChainOutcome:
    """Исходот од еден синџир."""

    chain_code: str
    stores: int = 0
    succeeded: int = 0
    failed: int = 0
    unchanged: int = 0
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
    groups = await ensure_groups(session, GROUPS)
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

    for ref in refs:
        run = await ingest_store(
            session,
            reader,
            chain,
            ref,
            run_date=run_date,
            grouper=grouper,
            groups=groups,
        )
        # Секоја продавница се потврдува одделно: ако следната падне,
        # претходните остануваат запишани.
        await session.commit()

        match run.status:
            case RunStatus.SUCCESS:
                outcome.succeeded += 1
                outcome.discounts += run.rows_discount
            case RunStatus.UNCHANGED:
                outcome.unchanged += 1
            case _:
                outcome.failed += 1
                if run.error_message and len(outcome.errors) < 5:
                    outcome.errors.append(f"{ref.name}: {run.error_message[:160]}")

    return outcome


async def run_all(
    *,
    run_date: date | None = None,
    chain_codes: list[str] | None = None,
    store_limit: int | None = None,
) -> list[ChainOutcome]:
    """Го врти дневното читање за сите (или одбрани) синџири."""
    run_date = run_date or today_local()
    codes = chain_codes or list(READERS)
    outcomes: list[ChainOutcome] = []

    log.info("Дневно читање за %s: %s", run_date, ", ".join(codes))

    for code in codes:
        reader_class = get_reader_class(code)
        client = PoliteClient()
        reader = reader_class(client)  # type: ignore[call-arg]
        try:
            async with SessionLocal() as session:
                outcome = await run_chain(
                    session, reader, run_date=run_date, store_limit=store_limit
                )
        finally:
            await client.aclose()

        outcomes.append(outcome)
        log.info(
            "%s: %d/%d продавници, %d попусти, %d непроменети, %d паднати",
            code,
            outcome.succeeded,
            outcome.stores,
            outcome.discounts,
            outcome.unchanged,
            outcome.failed,
        )

    return outcomes
