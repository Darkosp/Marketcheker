"""Каталогот како дрво, за страницата со избор.

Разликата од `discounts.py`: тука НЕ се гледа денешниот ден. Изборот е
траен - корисникот одбира што следи воопшто, па бројките мора да кажуваат
колку производи постојат во категоријата, а не колку биле на попуст денес.
Инаку „Кафе 0" би изгледало како причина да не се избере кафе.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.catalog.groups import category_names
from app.core.cache import Cache
from app.models import Product, ProductCategory

# Бројот производи по категорија се менува само по дневното читање, а
# дрвото се гради на секое отворање на изборот и на секое пребарување.
_CACHE = Cache(seconds=600)


@dataclass(slots=True)
class CategoryNode:
    """Една категорија во дрвото на избор."""

    slug: str
    name: str
    product_count: int
    children: list[CategoryNode] = field(default_factory=list)

    @property
    def has_children(self) -> bool:
        return bool(self.children)


async def catalog_tree(session: AsyncSession) -> list[CategoryNode]:
    """Како `_catalog_tree`, но запаметено до десет минути."""
    return await _CACHE.get("tree", lambda: _catalog_tree(session))


async def _catalog_tree(session: AsyncSession) -> list[CategoryNode]:
    """Групите со своите под-категории, со број производи во секоја.

    Бројот на групата го вклучува и она што седи директно на неа: кога
    речникот ја погодил групата но не и под-категоријата, производот виси
    на првото ниво. Без собирање, „Храна 1.240" а под-категориите 24.000 -
    и бројките изгледаат расипани.
    """
    rows = await session.execute(
        select(
            ProductCategory.id,
            ProductCategory.slug,
            ProductCategory.name,
            ProductCategory.parent_id,
            ProductCategory.sort_order,
            func.count(Product.id).label("products"),
        )
        .outerjoin(Product, Product.category_id == ProductCategory.id)
        .where(ProductCategory.is_active.is_(True))
        .group_by(ProductCategory.id)
        .order_by(ProductCategory.sort_order, ProductCategory.name)
    )

    groups: dict[int, CategoryNode] = {}
    own: dict[int, int] = {}
    children: list[tuple[int, CategoryNode]] = []

    for cat_id, slug, name, parent_id, _order, products in rows:
        node = CategoryNode(slug=slug, name=name, product_count=products)
        if parent_id is None:
            groups[cat_id] = node
            own[cat_id] = products
        else:
            children.append((parent_id, node))

    for parent_id, node in children:
        parent = groups.get(parent_id)
        if parent is None:
            # Под-категорија без родител не би требало да постои; ако се
            # појави, не се крие - оди како самостојна група.
            groups[-id(node)] = node
            continue
        parent.children.append(node)
        parent.product_count += node.product_count

    # Празна група не се прикажува: копче што води во ништо само го
    # оптоварува изборот.
    return [node for node in groups.values() if node.product_count > 0]


# ==========================================================================
# Трето ниво: вид, бренд и грамажа - извлечени од вистинските називи
# ==========================================================================
# Зборовите НЕ се измислуваат однапред. „Кафе во зрно" звучи како очигледно
# подниво, а има 12 производа; „готови ладни кафиња" никому не му падна на
# памет, а има 294. Затоа списокот расте од тоа што навистина пишува во
# ценовниците.
#
# Поделбата на букви и бројки е доволна за да се одвои грамажата („200ГР")
# од останатото („НЕСКАФЕ", „ИНСТАНТ"). Вид и бренд НЕ се делат: за тоа
# треба човек да потврди - „НЕСКАФЕ" е бренд, „КАПУЧИНО" е вид, а
# фреквенцијата не ги разликува.
_WORDS = "[^0-9A-Za-zЀ-ӿ]+"

# Поретко од ова не е избор, туку случајност.
MIN_TERM_PRODUCTS = 3

# Збор што е во речиси секој назив не стеснува ништо.
MAX_TERM_COVERAGE = 0.9

# Единици мерка и предлози: се појавуваат насекаде и не кажуваат ништо за
# тоа што е производот. Пократките од три букви („ГР", „ВО", „ЗА") паѓаат
# уште во упитот.
_NOISE = frozenset({"КОМ", "ПАР", "ПАК", "ЛИТ", "МЛТ", "ГРА", "ДЕН"})

_PACKAGE = re.compile(r"^\d+([.,]\d+)?(Г|ГР|КГ|МЛ|Л|КОМ|Г\.|X\d+)?$")


@dataclass(slots=True)
class Term:
    """Збор од називите по кој може да се стесни изборот."""

    text: str
    products: int

    @property
    def is_package(self) -> bool:
        """Грамажа („200ГР") наспроти вид или бренд („НЕСКАФЕ")."""
        return bool(_PACKAGE.match(self.text))


async def suggested_terms(
    session: AsyncSession, category_slug: str, limit: int = 30
) -> list[Term]:
    """Зборовите по кои вреди да се стесни една категорија.

    Се вадат од називите во таа категорија и се подредуваат по бројот на
    производи. Зборот од самото име на категоријата се вади: „КАФЕ" е во
    565 од 925 кафиња и не стеснува ништо.
    """
    query = text(
        f"""
        WITH vo_kategorija AS (
            SELECT p.id, upper(p.raw_name) AS naziv
            FROM product p
            JOIN product_category c ON c.id = p.category_id
            LEFT JOIN product_category g ON g.id = c.parent_id
            WHERE c.slug = :slug OR g.slug = :slug
        ), zborovi AS (
            SELECT k.id, w AS zbor
            FROM vo_kategorija k,
                 unnest(regexp_split_to_array(k.naziv, '{_WORDS}')) AS w
            WHERE length(w) >= 3
        )
        SELECT zbor, count(DISTINCT id) AS kolku
        FROM zborovi
        GROUP BY zbor
        HAVING count(DISTINCT id) >= :minimum
        ORDER BY kolku DESC
        LIMIT :limit
        """
    )
    rows = (
        await session.execute(
            query,
            {"slug": category_slug, "minimum": MIN_TERM_PRODUCTS, "limit": limit * 2},
        )
    ).all()
    if not rows:
        return []

    parent = aliased(ProductCategory, name="roditel")
    total = await session.scalar(
        select(func.count(Product.id))
        .join(ProductCategory, ProductCategory.id == Product.category_id)
        .outerjoin(parent, parent.id == ProductCategory.parent_id)
        .where(
            or_(
                ProductCategory.slug == category_slug,
                parent.slug == category_slug,
            )
        )
    )

    own_words = set(re.split(_WORDS, category_names().get(category_slug, "").upper()))
    ceiling = (total or 0) * MAX_TERM_COVERAGE

    terms = [
        Term(text=word, products=count)
        for word, count in rows
        if word not in own_words and word not in _NOISE and count <= ceiling
    ]
    return terms[:limit]


__all__ = ["CategoryNode", "Term", "catalog_tree", "suggested_terms"]
