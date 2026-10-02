"""Статистика од собраните податоци.

Ова е она што го овозможи чувањето на целиот асортиман, не само на
попустите: без него „кој маркет има најголеми попусти" не може да се
одговори, зашто попустите се броеле без да се знае од колку производи.

Се чита од daily_store_stats, која се полни при читањето - затоа не бара
пребројување на милиони редови.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chain, DailyStoreStats, PriceChange, Store


@dataclass(slots=True)
class ChainStats:
    """Збир за еден синџир во даден период."""

    chain_name: str
    stores: int
    products_total: int
    discounts_total: int
    avg_discount_pct: Decimal | None
    max_discount_pct: Decimal | None
    total_savings: Decimal | None

    @property
    def discount_share(self) -> float:
        """Колкав дел од асортиманот е на попуст - главната мерка.

        Бројот на попусти сам по себе не значи ништо: маркет со 20.000
        производи и 1.000 попусти не е подарежлив од маркет со 2.000
        производи и 300 попусти.
        """
        if not self.products_total:
            return 0.0
        return self.discounts_total / self.products_total


async def chain_stats(session: AsyncSession, *, run_date: date) -> list[ChainStats]:
    """Споредба на синџирите за еден ден, подредена по удел на попусти."""
    query = (
        select(
            Chain.name,
            func.count(func.distinct(DailyStoreStats.store_id)),
            func.sum(DailyStoreStats.products_total),
            func.sum(DailyStoreStats.discounts_total),
            func.avg(DailyStoreStats.avg_discount_pct),
            func.max(DailyStoreStats.max_discount_pct),
            func.sum(DailyStoreStats.total_savings),
        )
        .join(Store, DailyStoreStats.store_id == Store.id)
        .join(Chain, Store.chain_id == Chain.id)
        .where(DailyStoreStats.run_date == run_date)
        .group_by(Chain.name)
    )

    rows = [
        ChainStats(
            chain_name=name,
            stores=stores,
            products_total=products or 0,
            discounts_total=discounts or 0,
            avg_discount_pct=(
                Decimal(avg_pct).quantize(Decimal("0.01")) if avg_pct else None
            ),
            max_discount_pct=max_pct,
            total_savings=savings,
        )
        for name, stores, products, discounts, avg_pct, max_pct, savings in (
            await session.execute(query)
        )
    ]
    return sorted(rows, key=lambda row: row.discount_share, reverse=True)


async def top_stores(
    session: AsyncSession, *, run_date: date, limit: int = 10
) -> list[tuple[str, str, int, int, Decimal | None]]:
    """(маркет, продавница, асортиман, попусти, просечен попуст).

    Подредено по удел на попусти, не по број - види ChainStats.discount_share.
    """
    query = (
        select(
            Chain.name,
            Store.name,
            DailyStoreStats.products_total,
            DailyStoreStats.discounts_total,
            DailyStoreStats.avg_discount_pct,
        )
        .join(Store, DailyStoreStats.store_id == Store.id)
        .join(Chain, Store.chain_id == Chain.id)
        .where(
            DailyStoreStats.run_date == run_date,
            DailyStoreStats.products_total > 0,
        )
        .order_by(
            (
                DailyStoreStats.discounts_total
                * 1.0
                / func.nullif(DailyStoreStats.products_total, 0)
            ).desc()
        )
        .limit(limit)
    )
    return [tuple(row) for row in await session.execute(query)]  # type: ignore[misc]


async def price_movement(
    session: AsyncSession, *, since: date | None = None, days: int = 30
) -> tuple[int, int, int]:
    """(поскапувања, поевтинувања, вкупно промени) во изминатиот период.

    Ова е причината поради која историјата се чува: без неа прашањето
    „дали цените растат" нема одговор.
    """
    since = since or (date.today() - timedelta(days=days))

    up = func.count().filter(
        PriceChange.previous_sale_price.isnot(None),
        PriceChange.sale_price > PriceChange.previous_sale_price,
    )
    down = func.count().filter(
        PriceChange.previous_sale_price.isnot(None),
        PriceChange.sale_price < PriceChange.previous_sale_price,
    )

    row = (
        await session.execute(
            select(up, down, func.count()).where(PriceChange.changed_on >= since)
        )
    ).one()
    return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)


async def latest_stats_date(session: AsyncSession) -> date | None:
    return await session.scalar(select(func.max(DailyStoreStats.run_date)))
