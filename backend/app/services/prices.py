"""Запишување на целиот асортиман, не само на попустите.

Тече вака, по продавница:

1. Се вчитуваат тековните цени за таа продавница (еден упит).
2. Секој прочитан ред се споредува со тековната цена.
3. Сите редови одат во current_price со едно групно запишување
   (INSERT ... ON CONFLICT DO UPDATE).
4. Само РАЗЛИКИТЕ одат во price_change.
5. Збирните бројки одат во daily_store_stats.

Зошто вака: 1,5 милиони реда дневно × 205 бајти би биле 310 MB дневно ако
се чуваат сите состојби. Но цените во маркет се менуваат ретко - најголем
дел од асортиманот стои исто со недели. Со чување на промените наместо
состојбите, историјата останува мала, а „колку чинеше ова пред месец" и
натаму има одговор.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import parse_quantity, unit_price
from app.core.logging import get_logger
from app.models import CurrentPrice, DailyStoreStats, PriceChange, Store
from app.models.enums import PromoType
from app.readers.base import RawPriceRow
from app.readers.promo import map_promo_type

log = get_logger(__name__)

# Колку редови одат во едно групно запишување. Премногу голема серија
# прави долга трансакција; премногу мала - многу повратни патувања.
CHUNK = 1000


@dataclass(slots=True)
class PriceWriteResult:
    """Што се случи при запишувањето на една продавница."""

    products_total: int = 0
    discounts_total: int = 0
    changed_total: int = 0
    new_total: int = 0


def _snapshot(row: RawPriceRow, product_id: int) -> dict[str, Any]:
    """Еден прочитан ред, подготвен за запишување."""
    quantity = parse_quantity(row.name)
    is_discount = row.is_discount
    return {
        "product_id": product_id,
        "sale_price": row.sale_price,
        "regular_price": row.regular_price,
        "discount_price": row.discount_price,
        "discount_pct": row.discount_pct,
        "unit_price_base": unit_price(row.discount_price or row.sale_price, quantity),
        "promo_type": map_promo_type(row.promo_type_raw, has_discount=is_discount),
        "is_discount": is_discount,
        "is_single_day": row.is_single_day,
        "valid_from": row.valid_from,
        "valid_to": row.valid_to,
        "availability": row.availability,
    }


@dataclass(frozen=True, slots=True)
class _Known:
    """Тековната цена како што е во базата, само колоните за споредба."""

    sale_price: Decimal | None
    discount_price: Decimal | None
    is_discount: bool


def _is_different(new: dict[str, Any], old: _Known) -> bool:
    """Дали цената навистина се смени.

    Се гледаат само цените и состојбата на попуст. Промена на достапност
    или на датум на важење не е промена на цена и не оди во историјата -
    инаку историјата би се полнела со шум.
    """
    return (
        new["sale_price"] != old.sale_price
        or new["discount_price"] != old.discount_price
        or new["is_discount"] != old.is_discount
    )


async def _load_current(session: AsyncSession, store_id: int) -> dict[int, CurrentPrice]:
    rows = await session.scalars(
        select(CurrentPrice).where(CurrentPrice.store_id == store_id)
    )
    return {row.product_id: row for row in rows}


async def touch_current_prices(
    session: AsyncSession, store: Store, *, run_date: date
) -> int:
    """Го поместува денот на тековните цени, без да ги менува цените.

    Се вика кога ценовникот е непроменет: цените се исти, само се потврдува
    дека производите сè уште се во ценовникот. Враќа колку редови зафатило;
    нула значи дека за таа продавница воопшто нема тековни цени, па
    прескокнувањето не смее да се направи.
    """
    result = await session.execute(
        update(CurrentPrice)
        .where(CurrentPrice.store_id == store.id)
        .values(run_date=run_date)
    )
    return result.rowcount or 0


async def write_prices(
    session: AsyncSession,
    store: Store,
    rows: list[tuple[RawPriceRow, int]],
    *,
    run_date: date,
) -> PriceWriteResult:
    """Ги запишува сите прочитани редови за една продавница.

    rows се парови (прочитан ред, id на производ). Повикувачот ги создава
    производите; тука се занимаваме само со цените.
    """
    result = PriceWriteResult(products_total=len(rows))
    if not rows:
        return result

    existing = await _load_current(session, store.id)

    snapshots: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    seen: set[int] = set()

    for raw, product_id in rows:
        if product_id in seen:
            # Ист производ двапати во ист ценовник (се случува кај Веро).
            # Првиот запис важи; вториот би го прегазил во истата серија.
            continue
        seen.add(product_id)

        snapshot = _snapshot(raw, product_id)
        if snapshot["is_discount"]:
            result.discounts_total += 1

        previous = existing.get(product_id)
        if previous is None:
            result.new_total += 1
        elif _is_different(snapshot, previous):
            result.changed_total += 1
            changes.append(
                {
                    "store_id": store.id,
                    "product_id": product_id,
                    "changed_on": run_date,
                    "sale_price": snapshot["sale_price"],
                    "discount_price": snapshot["discount_price"],
                    "is_discount": snapshot["is_discount"],
                    "previous_sale_price": previous.sale_price,
                    "previous_discount_price": previous.discount_price,
                }
            )

        snapshots.append({**snapshot, "store_id": store.id, "run_date": run_date})

    await _upsert_current(session, snapshots)
    await _insert_changes(session, changes)

    # Групното запишување оди мимо ORM-от, па објекти што веќе се во
    # сесијата би останале со старите вредности. Се поништуваат само тие,
    # не целата сесија - производите штотуку се запишани и не се менети.
    _expire_cached_prices(session)
    return result


def _expire_cached_prices(session: AsyncSession) -> None:
    for obj in list(session.sync_session.identity_map.values()):
        if isinstance(obj, CurrentPrice):
            session.expire(obj)


async def _upsert_current(session: AsyncSession, snapshots: list[dict[str, Any]]) -> None:
    """Групно запишување во current_price: нов ред, или освежување."""
    if not snapshots:
        return

    for start in range(0, len(snapshots), CHUNK):
        batch = snapshots[start : start + CHUNK]
        statement = insert(CurrentPrice).values(batch)
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[CurrentPrice.store_id, CurrentPrice.product_id],
                set_={
                    column: getattr(statement.excluded, column)
                    for column in (
                        "sale_price",
                        "regular_price",
                        "discount_price",
                        "discount_pct",
                        "unit_price_base",
                        "promo_type",
                        "is_discount",
                        "is_single_day",
                        "valid_from",
                        "valid_to",
                        "availability",
                        "run_date",
                    )
                },
            )
        )


async def _insert_changes(session: AsyncSession, changes: list[dict[str, Any]]) -> None:
    """Промените се запишуваат еднаш дневно по производ и продавница.

    Ако читањето се повтори истиот ден (пр. рачно), втората промена не
    создава втор запис.
    """
    if not changes:
        return

    for start in range(0, len(changes), CHUNK):
        batch = changes[start : start + CHUNK]
        await session.execute(
            insert(PriceChange)
            .values(batch)
            .on_conflict_do_nothing(constraint="price_change_once_a_day")
        )


async def write_daily_stats(
    session: AsyncSession,
    store: Store,
    rows: list[tuple[RawPriceRow, int]],
    *,
    run_date: date,
    changed_total: int,
) -> None:
    """Збирни бројки за една продавница и ден.

    Се пресметува тука, при читањето, за да „кој маркет има најголеми
    попусти" не бара пребројување на милиони редови подоцна.
    """
    discounts = [raw for raw, _ in rows if raw.is_discount]
    percents = [raw.discount_pct for raw in discounts if raw.discount_pct is not None]

    savings = sum(
        (raw.regular_price - raw.discount_price)
        for raw in discounts
        if raw.regular_price is not None
        and raw.discount_price is not None
        and raw.regular_price > raw.discount_price
    )

    loyalty = sum(
        1
        for raw in discounts
        if map_promo_type(raw.promo_type_raw, has_discount=True) is PromoType.LOYALTY
    )

    values = {
        "store_id": store.id,
        "run_date": run_date,
        "products_total": len(rows),
        "discounts_total": len(discounts),
        "loyalty_total": loyalty,
        "single_day_total": sum(1 for raw in discounts if raw.is_single_day),
        "changed_total": changed_total,
        "avg_discount_pct": (
            (sum(percents) / len(percents)).quantize(Decimal("0.01"))
            if percents
            else None
        ),
        "max_discount_pct": max(percents) if percents else None,
        "total_savings": Decimal(savings) if savings else None,
    }

    statement = insert(DailyStoreStats).values(values)
    await session.execute(
        statement.on_conflict_do_update(
            constraint="daily_store_stats_once_a_day",
            set_={
                column: getattr(statement.excluded, column)
                for column in (
                    "products_total",
                    "discounts_total",
                    "loyalty_total",
                    "single_day_total",
                    "changed_total",
                    "avg_discount_pct",
                    "max_discount_pct",
                    "total_savings",
                )
            },
        )
    )
