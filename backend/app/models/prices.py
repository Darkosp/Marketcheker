"""Цени на целиот асортиман, не само на попустите.

Три табели, со различна намена и различен раст:

**current_price** - по еден ред за секој производ во секоја продавница,
кој се ПРЕБРИШУВА при секое читање. Одговара на „колку чини ова денес
тука" и е основата за корпа („јајца, млеко и леб - каде е најевтино").
Големина: околу 1,5 милиони реда вкупно, и останува толку.

**price_change** - се запишува САМО кога цената се смени. Без тоа,
чувањето на целиот асортиман секој ден би било 310 MB дневно, а 99% од
тоа идентични редови. Одговара на „колку чинеше ова пред месец".

**daily_store_stats** - по еден ред за продавница и ден, со збирни
бројки. Одговара на „кој маркет има најголеми попусти". 153 продавници
по 365 дена се 56 илјади реда годишно - занемарливо.

Историјата се чисти по две години (app/services/retention.py).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, enum_column
from app.models.enums import PromoType

if TYPE_CHECKING:
    from app.models.catalog import Product
    from app.models.chain import Store


class CurrentPrice(Base):
    """Тековната цена на еден производ во една продавница.

    Клучот е (продавница, производ) - по еден ред, кој се пребришува. Нема
    историја тука; за тоа е PriceChange.
    """

    __tablename__ = "current_price"
    __table_args__ = (
        # Главното прашање за корпа: „цените на овие производи во оваа
        # продавница".
        Index("ix_current_price_store_product", "store_id", "product_id"),
        Index("ix_current_price_product", "product_id"),
        Index(
            "ix_current_price_discounts",
            "store_id",
            postgresql_where=text("is_discount"),
        ),
    )

    store_id: Mapped[int] = mapped_column(
        ForeignKey("store.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("product.id", ondelete="CASCADE"), primary_key=True
    )

    sale_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    regular_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    discount_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    discount_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    unit_price_base: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))

    promo_type: Mapped[PromoType] = mapped_column(
        enum_column(PromoType, length=16), default=PromoType.NONE, server_default="none"
    )
    is_discount: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    is_single_day: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    availability: Mapped[str | None] = mapped_column(String(96))

    # Денот на последното читање во кое производот се појави. Ако заостане,
    # производот веќе не е во ценовникот на таа продавница.
    run_date: Mapped[date] = mapped_column(Date, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    store: Mapped[Store] = relationship()
    product: Mapped[Product] = relationship()

    def __repr__(self) -> str:
        return f"<CurrentPrice store={self.store_id} product={self.product_id}>"


class PriceChange(Base):
    """Запис дека цената се сменила. Се пишува само при промена.

    Цените во маркет се менуваат ретко: најголемиот дел од асортиманот
    стои со иста цена со недели. Затоа историјата е мала - се чуваат
    промените, не состојбите.
    """

    __tablename__ = "price_change"
    __table_args__ = (
        Index("ix_price_change_product_date", "product_id", "changed_on"),
        Index("ix_price_change_store_date", "store_id", "changed_on"),
        # Иста промена не се запишува двапати во ист ден.
        UniqueConstraint(
            "store_id", "product_id", "changed_on", name="price_change_once_a_day"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("store.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"))
    changed_on: Mapped[date] = mapped_column(Date, index=True)

    # Новата состојба.
    sale_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    discount_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    is_discount: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    # Претходната цена, за да „колку се смени" не бара втор упит.
    previous_sale_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    previous_discount_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<PriceChange {self.changed_on} store={self.store_id} "
            f"product={self.product_id}>"
        )


class DailyStoreStats(Base):
    """Збирни бројки по продавница и ден.

    Се пресметува при читањето, за да „кој маркет има најголеми попусти"
    не бара пребројување на милиони редови.
    """

    __tablename__ = "daily_store_stats"
    __table_args__ = (
        UniqueConstraint("store_id", "run_date", name="daily_store_stats_once_a_day"),
        Index("ix_daily_store_stats_date", "run_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("store.id", ondelete="CASCADE"))
    run_date: Mapped[date] = mapped_column(Date)

    products_total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    discounts_total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    loyalty_total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    single_day_total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    changed_total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    avg_discount_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    max_discount_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    # Колку пари вкупно се „симнати" од редовните цени - груба мерка колку
    # маркетот навистина попушта, не само колку производи означил.
    total_savings: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    store: Mapped[Store] = relationship()

    @property
    def discount_share(self) -> float:
        """Колкав дел од асортиманот е на попуст."""
        if not self.products_total:
            return 0.0
        return self.discounts_total / self.products_total

    def __repr__(self) -> str:
        return f"<DailyStoreStats {self.run_date} store={self.store_id}>"
