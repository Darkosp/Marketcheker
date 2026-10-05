"""Истакнати секојдневни производи, за почетната страница.

Човек што првпат ја отвора страницата не знае дали вреди. Список од 6.000
попусти не му кажува ништо - бројка што ја препознава, кажува: толку
можеше да заштеди на маслото, на сирењето, на кафето.

Затоа тука НЕ се прикажуваат денешните попусти, туку **најдобрите од
последниот месец**, со датумот на кој биле. Два разлога:

- денес маслото можеби нема попуст, а примерот мора да постои секој ден;
- тоа е пример, не понуда - и страницата го кажува тоа отворено.

Списокот производи е краток и рачно одбран. Автоматски би го составил од
најчестите називи, а најчести се лековите за садови и чоколадата - не тоа
што човек го купува секоја недела.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.catalog.picks import Pick, like_pattern
from app.core.cache import Cache
from app.models import Chain, PriceRow, Product, ProductCategory, Store

# Колку наназад се гледа за најдобриот пример.
WINDOW_DAYS = 30

# Истите десет упити за секој посетител правеа две секунди по отворање на
# почетната. Одговорот се менува еднаш дневно, по читањето во 11:00.
_CACHE = Cache(seconds=600)


@dataclass(frozen=True, slots=True)
class Everyday:
    """Производ што се купува секоја недела."""

    label: str
    pick: Pick


# Рачно одбрани. Секој е проверен дека навистина постои во ценовниците.
EVERYDAY: tuple[Everyday, ...] = (
    Everyday("Масло за јадење", Pick("masla", ("масло",))),
    Everyday("Сирење", Pick("mlecni", ("сирење",))),
    Everyday("Млеко", Pick("mlecni", ("млеко",))),
    Everyday("Кафе", Pick("kafe")),
    Everyday("Пилешко месо", Pick("meso", ("пилешк",))),
    # „Леб" како цела категорија фаќаше и тостер-апарат (погрешно
    # категоризиран); зборот во називот го решава тоа.
    Everyday("Леб", Pick("leb", ("леб",))),
    # Без категоријата, „јајца" фаќаше јуфки „со јајца".
    Everyday("Јајца", Pick("osnovni", ("јајца",))),
    Everyday("Детергент за алишта", Pick("perenje", ("детергент",))),
    Everyday("Тоалетна хартија", Pick("hartija-salfeti", ("тоалетна",))),
    Everyday("Шеќер", Pick("osnovni", ("шеќер",))),
)


@dataclass(slots=True)
class Highlight:
    """Најдобриот попуст на еден секојдневен производ."""

    label: str
    product_name: str
    chain_name: str
    regular_price: Decimal
    discount_price: Decimal
    savings: Decimal
    seen_on: date


Category = aliased(ProductCategory, name="kategorija_h")
Group = aliased(ProductCategory, name="grupa_h")


def _pick_clause(pick: Pick):
    conditions = []
    if pick.category:
        conditions.append(
            or_(Category.slug == pick.category, Group.slug == pick.category)
        )
    conditions.extend(
        Product.raw_name.ilike(like_pattern(term), escape="\\")
        for term in pick.terms
    )
    return and_(*conditions)


async def best_recently(
    session: AsyncSession, today: date, days: int = WINDOW_DAYS
) -> list[Highlight]:
    """Како `_best_recently`, но запаметено до десет минути."""
    return await _CACHE.get(
        ("best", today, days), lambda: _best_recently(session, today, days)
    )


async def _best_recently(
    session: AsyncSession, today: date, days: int
) -> list[Highlight]:
    """Најдобриот попуст на секој секојдневен производ, во последниве денови.

    Еден упит по производ: десет кратки упити се побрзи и почитливи од еден
    со десет гранки, а страницата ги кешира.
    """
    since = today - timedelta(days=days)
    savings = PriceRow.regular_price - PriceRow.discount_price

    highlights: list[Highlight] = []
    for item in EVERYDAY:
        query = (
            select(
                Product.raw_name,
                Chain.name,
                PriceRow.regular_price,
                PriceRow.discount_price,
                savings.label("savings"),
                PriceRow.run_date,
            )
            .join(Product, Product.id == PriceRow.product_id)
            .join(Store, Store.id == PriceRow.store_id)
            .join(Chain, Chain.id == Store.chain_id)
            .outerjoin(Category, Product.category_id == Category.id)
            .outerjoin(Group, Category.parent_id == Group.id)
            .where(
                PriceRow.is_discount.is_(True),
                PriceRow.run_date >= since,
                PriceRow.regular_price.isnot(None),
                PriceRow.discount_price.isnot(None),
                _pick_clause(item.pick),
            )
            .order_by(savings.desc())
            .limit(1)
        )
        row = (await session.execute(query)).first()
        if row is None:
            continue
        name, chain, regular, discounted, saved, seen = row
        highlights.append(
            Highlight(
                label=item.label,
                product_name=name,
                chain_name=chain,
                regular_price=regular,
                discount_price=discounted,
                savings=saved,
                seen_on=seen,
            )
        )

    return highlights


def total_savings(highlights: list[Highlight]) -> Decimal:
    """Колку би заштедил кој ги купил сите овие по тие цени."""
    return sum((item.savings for item in highlights), Decimal(0))


async def shopping_scale(session: AsyncSession) -> tuple[int, int]:
    """(маркети, продавници) што се читаат - за реченицата „од колку места"."""

    async def count() -> tuple[int, int]:
        chains = await session.scalar(
            select(func.count(func.distinct(Store.chain_id)))
        )
        stores = await session.scalar(select(func.count(Store.id)))
        return int(chains or 0), int(stores or 0)

    return await _CACHE.get("scale", count)


__all__ = [
    "EVERYDAY",
    "WINDOW_DAYS",
    "Everyday",
    "Highlight",
    "best_recently",
    "shopping_scale",
    "total_savings",
]
