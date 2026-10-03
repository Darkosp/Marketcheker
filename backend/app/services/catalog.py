"""Каталогот како дрво, за страницата со избор.

Разликата од `discounts.py`: тука НЕ се гледа денешниот ден. Изборот е
траен - корисникот одбира што следи воопшто, па бројките мора да кажуваат
колку производи постојат во категоријата, а не колку биле на попуст денес.
Инаку „Кафе 0" би изгледало како причина да не се избере кафе.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product, ProductCategory


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


__all__ = ["CategoryNode", "catalog_tree"]
