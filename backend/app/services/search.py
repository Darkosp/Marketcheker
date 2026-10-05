"""Што значи тоа што човекот го напишал.

Корисникот не размислува во нивоа. Пишува „кафе", „нескафе", „компир" - и
очекува апликацијата да разбере. Трите случаи бараат различен одговор:

    „кафе"      широко  → понуди му видови и брендови да стесни
    „нескафе"   точно   → тоа е изборот, понуди му само грамажи
    „компир"    точно   → тоа е изборот, нема што да се стеснува

Разликата не е во зборот, туку во тоа **колку производи фаќа** и дали
постои категорија со тоа име. Затоа тука не се погодува - се брои.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.groups import PARENT_OF, category_names
from app.catalog.picks import Pick, clean_term, like_pattern
from app.models import Product
from app.services.catalog import catalog_tree

# Колку производи прави еден збор „широк". Под ова, изборот е доволно
# точен за да нема што да се стеснува.
WIDE = 60

# Колку примери називи да се покажат, за да се види што ќе влезе во листата.
EXAMPLES = 4

# Повеќе од ова не стеснува, само го оптоварува упитот.
MAX_WORDS = 4

_WORDS = "[^0-9A-Za-zЀ-ӿ]+"
_SPLIT = re.compile(_WORDS)

_NAMES = category_names()


@dataclass(slots=True)
class CategoryHit:
    """Категорија чие име личи на напишаното."""

    slug: str
    name: str
    products: int

    @property
    def is_group(self) -> bool:
        return self.slug not in PARENT_OF


@dataclass(slots=True)
class Found:
    """Што значи напишаното."""

    text: str
    words: tuple[str, ...] = ()
    categories: list[CategoryHit] = field(default_factory=list)
    products: int = 0
    examples: list[str] = field(default_factory=list)
    narrowing: list[str] = field(default_factory=list)

    @property
    def is_wide(self) -> bool:
        """Фаќа премногу за да биде готов избор."""
        return self.products > WIDE

    @property
    def found_anything(self) -> bool:
        return bool(self.categories or self.products)

    @property
    def as_pick(self) -> Pick:
        """Напишаното како избор по назив, без категорија.

        Повеќе зборови се ЕДЕН избор со повеќе услови: „кафе нескафе" бара
        називи што ги имаат обата, не два одделни избора.
        """
        return Pick(terms=self.words)

    def plus(self, word: str) -> str:
        """Напишаното плус уште еден збор - за врските што стеснуваат."""
        return " ".join((*self.words, word.lower()))


def _matching_categories(typed: str) -> list[str]:
    """Категории чие име го содржи напишаното, или обратно.

    Споредбата е по ЦЕЛ ЗБОР: „сок" не смее да ја фати „Сокови и нектари"
    преку половина збор, но „кафе" мора да ја фати „Кафе". Затоа се гледаат
    зборовите во името, не само дали текстот е подниз.
    """
    needle = typed.lower()
    hits = []
    for slug, name in _NAMES.items():
        words = [w for w in _SPLIT.split(name.lower()) if w]
        if needle in words or any(w.startswith(needle) for w in words):
            hits.append(slug)
    return hits


def _words_of(typed: str) -> tuple[str, ...]:
    """Напишаното во зборови. Секој е услов за себе."""
    seen: list[str] = []
    for piece in typed.split():
        word = clean_term(piece)
        if word and word not in seen:
            seen.append(word)
    return tuple(seen[:MAX_WORDS])


async def understand(session: AsyncSession, typed: str) -> Found:
    """Што значи напишаното, со бројки наместо погодување.

    Повеќе зборови се спојуваат со И: „кафе нескафе" бара називи што ги
    имаат обата. Така стеснувањето е само уште еден збор, а не друг
    механизам.
    """
    words = _words_of(typed)
    if not words:
        return Found(text=typed.strip())

    term = " ".join(words)
    found = Found(text=term, words=words)
    matches = [
        Product.raw_name.ilike(like_pattern(word), escape="\\") for word in words
    ]

    # 1. Дали постои категорија со такво име - само за еден збор.
    slugs = _matching_categories(term) if len(words) == 1 else []
    if slugs:
        counts = await _category_counts(session, slugs)
        found.categories = [
            CategoryHit(slug=slug, name=_NAMES[slug], products=counts.get(slug, 0))
            for slug in slugs
            if counts.get(slug, 0) > 0
        ]
        found.categories.sort(key=lambda hit: hit.products, reverse=True)

    # 2. Колку производи ги носат сите зборови, и неколку примери.
    rows = await session.execute(
        select(Product.raw_name)
        .where(*matches)
        .order_by(func.length(Product.raw_name))
        .limit(EXAMPLES)
    )
    found.examples = [name for (name,) in rows]
    found.products = (
        await session.scalar(select(func.count(Product.id)).where(*matches)) or 0
    )

    # 3. Ако фаќа премногу, со што може да се стесни.
    if found.is_wide:
        found.narrowing = await narrowing_words(session, words)

    return found


async def _category_counts(
    session: AsyncSession, slugs: list[str]
) -> dict[str, int]:
    """Колку производи има во секоја од овие категории.

    Истото броење како на страницата со избор - групата го вклучува она што
    е под неа. Се користи `catalog_tree` наместо нов упит, за да бројката на
    две места никогаш не се разликува.
    """
    totals: dict[str, int] = {}
    for group in await catalog_tree(session):
        totals[group.slug] = group.product_count
        for child in group.children:
            totals[child.slug] = child.product_count
    return {slug: totals.get(slug, 0) for slug in slugs}


async def narrowing_words(
    session: AsyncSession, words: tuple[str, ...], limit: int = 18
) -> list[str]:
    """Зборовите со кои може да се стесни оној што го напишал.

    Се вадат од називите што веќе ги содржат сите напишани зборови: кој
    напишал „кафе" добива „ИНСТАНТ", „НЕСКАФЕ", „КАПСУЛИ" - тоа што
    навистина постои во тие називи, не измислена поделба.
    """
    conditions = " AND ".join(
        f"raw_name ILIKE :p{index} ESCAPE '\\'" for index in range(len(words))
    )
    query = text(
        f"""
        WITH najdeni AS (
            SELECT id, upper(raw_name) AS naziv
            FROM product
            WHERE {conditions}
        ), zborovi AS (
            SELECT n.id, w AS zbor
            FROM najdeni n,
                 unnest(regexp_split_to_array(n.naziv, '{_WORDS}')) AS w
            WHERE length(w) >= 3
        )
        SELECT zbor, count(DISTINCT id) AS kolku
        FROM zborovi
        GROUP BY zbor
        HAVING count(DISTINCT id) >= 3
        ORDER BY kolku DESC
        LIMIT :limit
        """
    )
    params = {f"p{index}": like_pattern(word) for index, word in enumerate(words)}
    rows = await session.execute(query, {**params, "limit": limit * 2})

    # Самите напишани зборови не стеснуваат ништо - се во секој погодок.
    typed_words = {w for word in words for w in _SPLIT.split(word.upper()) if w}
    offered = [word for word, _ in rows if word not in typed_words]
    return offered[:limit]


__all__ = [
    "EXAMPLES",
    "MAX_WORDS",
    "WIDE",
    "CategoryHit",
    "Found",
    "narrowing_words",
    "understand",
]
