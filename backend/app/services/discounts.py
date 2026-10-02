"""Упити за приказ на денешните попусти.

Првата верзија не бара од корисникот да избира што следи: ги враќа СИТЕ
попусти за денот, подредени по групи на употреба. Филтрите (град, маркет,
група) се необврзни.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

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


# Категоријата на производот може да е под-категорија; групата е нејзиниот
# родител. Затоа ProductCategory влегува двапати во упитот.
Category = aliased(ProductCategory, name="kategorija")
Group = aliased(ProductCategory, name="grupa")


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
    # Под-категоријата, ако производот стигнал до второ ниво.
    subcategory_slug: str | None = None
    subcategory_name: str | None = None
    # Истиот производ по иста цена често е на попуст во десетици
    # продавници. Се прикажува еднаш, со број колку се.
    store_count: int = 1
    chain_count: int = 1

    @property
    def where_label(self) -> str:
        """Каде важи попустот, во една линија.

        Една продавница се именува; повеќе се бројат, зашто списокот од 22
        имиња не му помага никому.
        """
        if self.store_count <= 1:
            return f"{self.chain_name} · {self.store_name}"
        if self.chain_count > 1:
            return f"{self.store_count} продавници во {self.chain_count} маркети"
        return f"{self.chain_name} · {self.store_count} продавници"

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


def _join_and_filter(query: Select, filters: DiscountFilter) -> Select:
    """Ги додава join-овите и условите на било кој упит врз price_row.

    Одделена од изборот на колони: `with_only_columns` врз готов упит ја
    губи врската со outerjoin-овите, па броењето по група даваше погрешни
    бројки (сите редови паѓаа во првата група).
    """
    query = (
        query.join(PriceRow.product)
        .join(PriceRow.store)
        .join(Store.chain)
        .outerjoin(Store.city)
        .outerjoin(Category, Product.category_id == Category.id)
        .outerjoin(Group, Category.parent_id == Group.id)
        .where(PriceRow.run_date == filters.run_date, PriceRow.is_discount.is_(True))
    )

    if filters.city_slug:
        query = query.where(City.slug == filters.city_slug)
    if filters.store_ids:
        query = query.where(PriceRow.store_id.in_(filters.store_ids))
    if filters.group_slug:
        # Истиот филтер прима и група и под-категорија: „hrana" ја дава
        # цела Храна, „slatki" само слатките во неа.
        query = query.where(
            or_(
                Category.slug == filters.group_slug,
                Group.slug == filters.group_slug,
            )
        )
    if not filters.include_loyalty:
        query = query.where(PriceRow.promo_type != PromoType.LOYALTY)
    if filters.only_single_day:
        query = query.where(PriceRow.is_single_day.is_(True))

    return query


# Колоните по кои се спојуваат редовите. Цената е меѓу нив намерно:
# ист производ по РАЗЛИЧНА цена останува одделен запис, за да не измислиме
# цена што ја нема никаде.
_GROUPING = (
    Product.id,
    Category.id,
    Group.id,
    PriceRow.discount_price,
    PriceRow.regular_price,
    PriceRow.discount_pct,
    PriceRow.unit_price_base,
    PriceRow.promo_type,
    PriceRow.valid_from,
    PriceRow.valid_to,
    PriceRow.is_single_day,
)


def _aggregated_ordering(filters: DiscountFilter):
    """Подредување врз споените редови.

    Колоните се истите како кај единечните редови - сите се во GROUP BY,
    па смеат да се користат директно.
    """
    match filters.sort_by:
        case SortBy.DISCOUNT_PCT:
            return (PriceRow.discount_pct.desc().nullslast(), Product.raw_name)
        case SortBy.PRICE_ASC:
            return (PriceRow.discount_price.asc().nullslast(), Product.raw_name)
        case SortBy.UNIT_PRICE:
            return (
                Product.base_unit.asc().nullslast(),
                PriceRow.unit_price_base.asc().nullslast(),
                Product.raw_name,
            )
        case SortBy.STORE:
            return (func.min(Chain.name), Product.raw_name)
        case _:
            return (Product.raw_name,)


def _aggregate_query(filters: DiscountFilter) -> Select:
    """Еден ред по производ и цена, со број на продавници.

    Без ова списокот е преполн со повторување: ист попуст важи во сите 36
    продавници на Рамстор, па корисникот ја гледа истата картичка 36 пати.
    """
    return _join_and_filter(
        select(
            Product.id.label("product_id"),
            Product.raw_name,
            Product.raw_description,
            Product.package_value,
            Product.package_unit,
            Product.base_unit,
            PriceRow.regular_price,
            PriceRow.discount_price,
            PriceRow.discount_pct,
            PriceRow.unit_price_base,
            PriceRow.promo_type,
            PriceRow.valid_from,
            PriceRow.valid_to,
            PriceRow.is_single_day,
            Category.slug.label("category_slug"),
            Category.name.label("category_name"),
            Group.slug.label("group_slug"),
            Group.name.label("group_name"),
            func.count(func.distinct(PriceRow.store_id)).label("store_count"),
            func.count(func.distinct(Store.chain_id)).label("chain_count"),
            func.min(Chain.name).label("chain_name"),
            func.min(Store.name).label("store_name"),
            func.min(City.name).label("city_name"),
            func.min(PriceRow.promo_type_raw).label("promo_type_raw"),
            func.min(PriceRow.id).label("price_row_id"),
        ),
        filters,
    ).group_by(*_GROUPING)


async def list_discounts(
    session: AsyncSession, filters: DiscountFilter
) -> list[DiscountRow]:
    query = (
        _aggregate_query(filters)
        .order_by(*_aggregated_ordering(filters))
        .limit(filters.limit)
        .offset(filters.offset)
    )
    return [_from_aggregate(row) for row in await session.execute(query)]


async def count_discounts(session: AsyncSession, filters: DiscountFilter) -> int:
    """Колку РАЗЛИЧНИ попусти има - спојување како во списокот."""
    inner = _join_and_filter(select(*_GROUPING), filters).group_by(*_GROUPING)
    return await session.scalar(select(func.count()).select_from(inner.subquery())) or 0


async def counts_by_group(
    session: AsyncSession, filters: DiscountFilter
) -> list[tuple[str, str, int]]:
    """(slug, име, број) по група - за менито, без вчитување на редовите."""
    # Се брои по ГРУПА, врз СПОЕНИ редови - истото спојување како во
    # списокот. Инаку копчето вели „Храна 31.721" а кликнато дава 1.772.
    slug = func.coalesce(Group.slug, Category.slug)
    name = func.coalesce(Group.name, Category.name)
    order = func.coalesce(Group.sort_order, Category.sort_order)

    merged = (
        _join_and_filter(
            select(slug.label("slug"), name.label("name"), order.label("sort_order")),
            filters,
        )
        .group_by(*_GROUPING, slug, name, order)
        .subquery()
    )

    query = (
        select(merged.c.slug, merged.c.name, func.count(), merged.c.sort_order)
        .group_by(merged.c.slug, merged.c.name, merged.c.sort_order)
        .order_by(merged.c.sort_order)
    )
    rows = await session.execute(query)
    return [(s or "drugo", n or "Друго", count) for s, n, count, _ in rows]


async def counts_by_subcategory(
    session: AsyncSession, filters: DiscountFilter, group_slug: str
) -> list[tuple[str, str, int]]:
    """(slug, име, број) за под-категориите во една група.

    Служи за второто ниво копчиња: кога ќе се избере „Храна", се појавуваат
    нејзините под-категории.
    """
    inner = replace(filters, group_slug=group_slug)
    merged = (
        _join_and_filter(
            select(
                Category.slug.label("slug"),
                Category.name.label("name"),
                Category.sort_order.label("sort_order"),
            ),
            inner,
        )
        .where(Category.parent_id.isnot(None))
        .group_by(*_GROUPING, Category.slug, Category.name, Category.sort_order)
        .subquery()
    )

    query = (
        select(merged.c.slug, merged.c.name, func.count(), merged.c.sort_order)
        .group_by(merged.c.slug, merged.c.name, merged.c.sort_order)
        .order_by(merged.c.sort_order)
    )
    rows = await session.execute(query)
    return [(slug, name, count) for slug, name, count, _ in rows]


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


async def read_quality(
    session: AsyncSession, run_date: date
) -> list[tuple[str, int, int, int]]:
    """(маркет, прочитани редови, прескокнати, продавници со прескокнати).

    Прескокнатите редови се запишуваат при читањето, но досега никаде не
    се гледаа: ако читач почне тивко да губи 5% од редовите, тоа не беше
    видливо никому. Правилото е „никогаш тивок празен резултат" - ова го
    затвора кругот и за делумната загуба.
    """
    query = (
        select(
            Chain.name,
            func.coalesce(func.sum(PricelistRun.rows_total), 0),
            func.coalesce(func.sum(PricelistRun.rows_skipped), 0),
            func.count(PricelistRun.id).filter(PricelistRun.rows_skipped > 0),
        )
        .join(Chain, PricelistRun.chain_id == Chain.id)
        .where(PricelistRun.run_date == run_date)
        .group_by(Chain.name)
        .order_by(func.sum(PricelistRun.rows_skipped).desc().nullslast())
    )
    return [tuple(row) for row in await session.execute(query)]  # type: ignore[misc]


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


def _from_aggregate(row) -> DiscountRow:
    """DiscountRow од споен ред (производ + цена + број продавници)."""
    package = None
    if row.package_value is not None and row.package_unit:
        package = f"{row.package_value.normalize()} {row.package_unit}"

    return DiscountRow(
        price_row_id=row.price_row_id,
        product_name=row.raw_name,
        product_description=row.raw_description,
        package=package,
        chain_name=row.chain_name or "",
        store_name=row.store_name or "",
        city_name=row.city_name,
        store_count=row.store_count,
        chain_count=row.chain_count,
        regular_price=row.regular_price,
        discount_price=row.discount_price,
        discount_pct=row.discount_pct,
        unit_price_base=row.unit_price_base,
        base_unit=row.base_unit.value if row.base_unit else None,
        promo_type=row.promo_type,
        promo_type_raw=row.promo_type_raw,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        is_single_day=row.is_single_day,
        group_slug=row.group_slug or row.category_slug,
        group_name=row.group_name or row.category_name,
        subcategory_slug=row.category_slug if row.group_slug else None,
        subcategory_name=row.category_name if row.group_slug else None,
    )


__all__ = [
    "DiscountFilter",
    "DiscountRow",
    "SortBy",
    "available_cities",
    "available_stores",
    "count_discounts",
    "counts_by_group",
    "counts_by_subcategory",
    "latest_run_date",
    "list_discounts",
    "read_quality",
    "run_summary",
]
