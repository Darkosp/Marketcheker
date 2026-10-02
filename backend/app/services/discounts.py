"""Упити за приказ на денешните попусти.

Првата верзија не бара од корисникот да избира што следи: ги враќа СИТЕ
попусти за денот, подредени по групи на употреба. Филтрите (град, маркет,
група) се необврзни.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import (
    Chain,
    City,
    PricelistRun,
    PriceRow,
    Product,
    ProductCategory,
    Store,
)
from app.models.enums import PromoType

# Основните единици се чуваат латинично; на екран одат на македонски.
UNIT_LABELS = {
    "kg": "кг",
    "l": "л",
    "kom": "ком",
    "m": "м",
    "m2": "м2",
    "pranje": "перење",
}


class SortBy(StrEnum):
    """Начини на подредување што ги бара спецификацијата."""

    DISCOUNT_PCT = "popust"  # најголем попуст прво
    PRICE_ASC = "cena"  # најниска цена прво
    UNIT_PRICE = "edinecna"  # најевтино по кг/л - „каде е најевтино"
    STORE = "market"
    NAME = "naziv"


@dataclass(slots=True)
class DiscountFilter:
    """Што се прикажува. Празно значи сè."""

    run_date: date
    city_slug: str | None = None
    store_ids: list[int] = field(default_factory=list)
    group_slug: str | None = None
    # Попустите само со картичка за лојалност се прикажуваат означено;
    # со ова може и да се исклучат.
    include_loyalty: bool = True
    only_single_day: bool = False
    sort_by: SortBy = SortBy.DISCOUNT_PCT
    limit: int = 500
    offset: int = 0


@dataclass(slots=True)
class DiscountRow:
    """Еден попуст, подготвен за приказ."""

    price_row_id: int
    product_name: str  # точно како во ценовникот
    product_description: str | None
    package: str | None  # „100 ГР", ако називот го кажува
    chain_name: str
    store_name: str
    city_name: str | None
    regular_price: Decimal | None
    discount_price: Decimal | None
    discount_pct: Decimal | None
    unit_price_base: Decimal | None
    base_unit: str | None
    promo_type: PromoType
    promo_type_raw: str | None
    valid_from: date | None
    valid_to: date | None
    is_single_day: bool
    group_slug: str | None
    group_name: str | None

    @property
    def is_loyalty_only(self) -> bool:
        """Важи само со картичка за лојалност - се прикажува означено."""
        return self.promo_type is PromoType.LOYALTY

    @property
    def unit_price_label(self) -> str | None:
        """„1420 ден/кг" - помошната цена за споредба „каде е најевтино"."""
        if self.unit_price_base is None or not self.base_unit:
            return None
        unit = UNIT_LABELS.get(self.base_unit, self.base_unit)
        return f"{self.unit_price_base:.0f} ден/{unit}"


@dataclass(slots=True)
class DiscountGroup:
    """Група по употреба со своите попусти."""

    slug: str
    name: str
    rows: list[DiscountRow]

    @property
    def count(self) -> int:
        return len(self.rows)


def _base_query(filters: DiscountFilter) -> Select:
    query = (
        select(PriceRow)
        .join(PriceRow.product)
        .join(PriceRow.store)
        .join(Store.chain)
        .outerjoin(Store.city)
        .outerjoin(Product.category)
        .where(PriceRow.run_date == filters.run_date, PriceRow.is_discount.is_(True))
        .options(
            joinedload(PriceRow.product).joinedload(Product.category),
            joinedload(PriceRow.store).joinedload(Store.chain),
            joinedload(PriceRow.store).joinedload(Store.city),
        )
    )

    if filters.city_slug:
        query = query.where(City.slug == filters.city_slug)
    if filters.store_ids:
        query = query.where(PriceRow.store_id.in_(filters.store_ids))
    if filters.group_slug:
        query = query.where(ProductCategory.slug == filters.group_slug)
    if not filters.include_loyalty:
        query = query.where(PriceRow.promo_type != PromoType.LOYALTY)
    if filters.only_single_day:
        query = query.where(PriceRow.is_single_day.is_(True))

    return query


def _ordering(filters: DiscountFilter):
    match filters.sort_by:
        case SortBy.DISCOUNT_PCT:
            # Без попуст-процент одат на крај, не на почеток.
            return (PriceRow.discount_pct.desc().nullslast(), Product.raw_name)
        case SortBy.PRICE_ASC:
            return (PriceRow.discount_price.asc().nullslast(), Product.raw_name)
        case SortBy.UNIT_PRICE:
            return (PriceRow.unit_price_base.asc().nullslast(), Product.raw_name)
        case SortBy.STORE:
            return (Chain.name, Store.name, Product.raw_name)
        case _:
            return (Product.raw_name,)


async def list_discounts(
    session: AsyncSession, filters: DiscountFilter
) -> list[DiscountRow]:
    query = (
        _base_query(filters)
        .order_by(*_ordering(filters))
        .limit(filters.limit)
        .offset(filters.offset)
    )
    rows = (await session.scalars(query)).unique().all()
    return [_to_row(row) for row in rows]


async def count_discounts(session: AsyncSession, filters: DiscountFilter) -> int:
    inner = _base_query(filters).options().with_only_columns(PriceRow.id)
    return await session.scalar(select(func.count()).select_from(inner.subquery())) or 0


async def group_discounts(
    session: AsyncSession, filters: DiscountFilter
) -> list[DiscountGroup]:
    """Ги враќа попустите подредени по групи, за приказ по секции."""
    rows = await list_discounts(session, filters)

    buckets: dict[str, DiscountGroup] = {}
    for row in rows:
        slug = row.group_slug or "drugo"
        bucket = buckets.get(slug)
        if bucket is None:
            bucket = DiscountGroup(slug=slug, name=row.group_name or "Друго", rows=[])
            buckets[slug] = bucket
        bucket.rows.append(row)

    order = await _group_order(session)
    return sorted(buckets.values(), key=lambda g: (order.get(g.slug, 9999), g.name))


async def _group_order(session: AsyncSession) -> dict[str, int]:
    rows = await session.execute(
        select(ProductCategory.slug, ProductCategory.sort_order).where(
            ProductCategory.parent_id.is_(None)
        )
    )
    return dict(rows.all())


async def counts_by_group(
    session: AsyncSession, filters: DiscountFilter
) -> list[tuple[str, str, int]]:
    """(slug, име, број) по група - за менито, без вчитување на редовите."""
    query = (
        _base_query(filters)
        .options()
        .with_only_columns(
            ProductCategory.slug,
            ProductCategory.name,
            func.count(PriceRow.id),
            ProductCategory.sort_order,
        )
        .group_by(ProductCategory.slug, ProductCategory.name, ProductCategory.sort_order)
        .order_by(ProductCategory.sort_order)
    )
    rows = await session.execute(query)
    return [(slug or "drugo", name or "Друго", count) for slug, name, count, _ in rows]


async def available_cities(
    session: AsyncSession, run_date: date
) -> list[tuple[str, str, int]]:
    """Градови што имаат попусти за денот, со број на продавници."""
    query = (
        select(City.slug, City.name, func.count(func.distinct(Store.id)))
        .join(Store, Store.city_id == City.id)
        .join(PriceRow, PriceRow.store_id == Store.id)
        .where(PriceRow.run_date == run_date, PriceRow.is_discount.is_(True))
        .group_by(City.slug, City.name)
        .order_by(City.name)
    )
    return [tuple(row) for row in await session.execute(query)]  # type: ignore[misc]


async def available_stores(
    session: AsyncSession, run_date: date, city_slug: str | None = None
) -> list[tuple[int, str, str, int]]:
    """(id, маркет, продавница, број попусти) за денот."""
    query = (
        select(Store.id, Chain.name, Store.name, func.count(PriceRow.id))
        .join(Chain, Store.chain_id == Chain.id)
        .join(PriceRow, PriceRow.store_id == Store.id)
        .outerjoin(City, Store.city_id == City.id)
        .where(PriceRow.run_date == run_date, PriceRow.is_discount.is_(True))
        .group_by(Store.id, Chain.name, Store.name)
        .order_by(Chain.name, Store.name)
    )
    if city_slug:
        query = query.where(City.slug == city_slug)
    return [tuple(row) for row in await session.execute(query)]  # type: ignore[misc]


async def latest_run_date(session: AsyncSession) -> date | None:
    """Последниот ден со запишани попусти - за кога денешното читање го нема."""
    return await session.scalar(
        select(func.max(PriceRow.run_date)).where(PriceRow.is_discount.is_(True))
    )


async def run_summary(
    session: AsyncSession, run_date: date
) -> list[tuple[str, str, int, int]]:
    """(маркет, состојба, број читања, вкупно попусти) - за страницата со состојба.

    Тука се гледа кога изворот паднал: читањата со грешка не се кријат.
    """
    query = (
        select(
            Chain.name,
            PricelistRun.status,
            func.count(PricelistRun.id),
            func.coalesce(func.sum(PricelistRun.rows_discount), 0),
        )
        .join(Chain, PricelistRun.chain_id == Chain.id)
        .where(PricelistRun.run_date == run_date)
        .group_by(Chain.name, PricelistRun.status)
        .order_by(Chain.name, PricelistRun.status)
    )
    return [tuple(row) for row in await session.execute(query)]  # type: ignore[misc]


def _to_row(row: PriceRow) -> DiscountRow:
    product = row.product
    store = row.store
    category = product.category
    package = None
    if product.package_value is not None and product.package_unit:
        value = product.package_value.normalize()
        package = f"{value} {product.package_unit}"

    return DiscountRow(
        price_row_id=row.id,
        product_name=product.raw_name,
        product_description=product.raw_description,
        package=package,
        chain_name=store.chain.name,
        store_name=store.name,
        city_name=store.city.name if store.city else None,
        regular_price=row.regular_price,
        discount_price=row.discount_price,
        discount_pct=row.discount_pct,
        unit_price_base=row.unit_price_base,
        base_unit=product.base_unit.value if product.base_unit else None,
        promo_type=row.promo_type,
        promo_type_raw=row.promo_type_raw,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        is_single_day=row.is_single_day,
        group_slug=category.slug if category else None,
        group_name=category.name if category else None,
    )


__all__ = [
    "DiscountFilter",
    "DiscountGroup",
    "DiscountRow",
    "SortBy",
    "available_cities",
    "available_stores",
    "count_discounts",
    "counts_by_group",
    "group_discounts",
    "latest_run_date",
    "list_discounts",
    "run_summary",
]
