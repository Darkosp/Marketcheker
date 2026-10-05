"""Каталог: општи категории, речник со клучни зборови, и производи.

Производот се чува ТОЧНО како што пишува во ценовникот (raw_name), затоа што
корисникот треба да го види така. Nescafe 100 g и Nescafe 200 g се два
различни Product записа - грамажата е дел од идентитетот.

Имињата се разликуваат меѓу синџирите, затоа Product е врзан за Chain.
Споредбата „каде е најевтино" НЕ се прави преку спојување на идентични
производи (ненадежно), туку во рамки на општа категорија, преку
PriceRow.unit_price_base (цена за kg/l).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SeenMixin, TimestampMixin, enum_column
from app.models.enums import BaseUnit, CategoryStatus

if TYPE_CHECKING:
    from app.models.chain import Chain
    from app.models.pricing import PriceRow


class ProductCategory(Base, TimestampMixin):
    """Општа категорија што ја бира корисникот.

    Пример: „инстант кафе", „кафе во зрно", „маслиново масло",
    „сончогледово масло". parent_id дозволува плитка хиерархија
    (пр. „кафе" -> „инстант кафе"), но не е задолжителна.
    """

    __tablename__ = "product_category"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_category.id", ondelete="SET NULL")
    )
    # Во која единица има смисла да се споредува цената во оваа категорија.
    default_base_unit: Mapped[BaseUnit | None] = mapped_column(
        enum_column(BaseUnit, length=16), nullable=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    parent: Mapped[ProductCategory | None] = relationship(
        remote_side="ProductCategory.id", back_populates="children"
    )
    children: Mapped[list[ProductCategory]] = relationship(back_populates="parent")
    keywords: Mapped[list[CategoryKeyword]] = relationship(
        back_populates="category", cascade="all, delete-orphan"
    )
    products: Mapped[list[Product]] = relationship(back_populates="category")

    def __repr__(self) -> str:
        return f"<ProductCategory {self.slug}>"


class CategoryKeyword(Base, TimestampMixin):
    """Клучен збор што мапира текст од ценовникот во општа категорија.

    Се бара во колоната „опис" и во називот. is_negative служи за исклучоци:
    „кафе машина" не смее да падне во „инстант кафе". Негативните се
    проверуваат прво. priority решава кога повеќе зборови фаќаат (поголем
    приоритет победува) - пр. „кафе во зрно" (10) пред „кафе" (1).
    """

    __tablename__ = "category_keyword"
    __table_args__ = (
        UniqueConstraint("category_id", "keyword", name="category_keyword_unique"),
        Index("ix_category_keyword_lookup", "keyword", "is_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("product_category.id", ondelete="CASCADE"), index=True
    )
    # Нормализиран: мали букви, без двојни празни места.
    keyword: Mapped[str] = mapped_column(String(160))
    priority: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    is_negative: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    category: Mapped[ProductCategory] = relationship(back_populates="keywords")

    def __repr__(self) -> str:
        sign = "-" if self.is_negative else "+"
        return f"<CategoryKeyword {sign}{self.keyword!r}>"


class Product(Base, TimestampMixin, SeenMixin):
    """Производ како што се појавува во ценовниците на еден синџир."""

    __tablename__ = "product"
    __table_args__ = (
        # fingerprint = хеш од нормализирано (назив + опис); го спречува
        # дуплирање на истиот производ при секое дневно читање.
        UniqueConstraint("chain_id", "fingerprint", name="product_chain_fingerprint"),
        Index("ix_product_category_status", "category_id", "category_status"),
        CheckConstraint(
            "package_value IS NULL OR package_value > 0",
            name="package_value_positive",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain_id: Mapped[int] = mapped_column(
        ForeignKey("chain.id", ondelete="CASCADE"), index=True
    )

    # ---- Како пишува во ценовникот (ова го гледа корисникот) ----
    raw_name: Mapped[str] = mapped_column(Text)
    raw_description: Mapped[str | None] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))

    # ---- Извлечено со парсирање (може да е непополнето) ----
    brand: Mapped[str | None] = mapped_column(String(160), index=True)
    package_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    package_unit: Mapped[str | None] = mapped_column(String(16))
    # Истата грамажа сведена на основна единица, за споредба на цени.
    base_quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 5))
    base_unit: Mapped[BaseUnit | None] = mapped_column(enum_column(BaseUnit, length=16))

    # ---- Категоризација ----
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_category.id", ondelete="SET NULL"), index=True
    )
    category_status: Mapped[CategoryStatus] = mapped_column(
        enum_column(CategoryStatus, length=16),
        default=CategoryStatus.UNKNOWN,
        server_default="unknown",
    )
    # Кој клучен збор го донесе тука - за да може да се провери одлуката.
    category_matched_keyword: Mapped[str | None] = mapped_column(String(160))

    chain: Mapped[Chain] = relationship()
    category: Mapped[ProductCategory | None] = relationship(back_populates="products")
    price_rows: Mapped[list[PriceRow]] = relationship(back_populates="product")

    def __repr__(self) -> str:
        return f"<Product {self.id} {self.raw_name[:40]!r}>"
