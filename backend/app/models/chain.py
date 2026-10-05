"""Синџири (маркети) и поединечни продавници.

Клучно: цените се по продавница, не по синџир. Еден Chain има многу Store,
и секоја Store има свој ценовник со свои цени.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SeenMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.geo import City
    from app.models.pricing import PricelistRun, PriceRow


class Chain(Base, TimestampMixin):
    """Синџир маркети: Веро, Рамстор, Кипер, КАМ, Тинекс."""

    __tablename__ = "chain"

    id: Mapped[int] = mapped_column(primary_key=True)
    # code го поврзува записот со модулот-читач (readers/<code>.py)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    website: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    stores: Mapped[list[Store]] = relationship(
        back_populates="chain", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Chain {self.code}>"


class Store(Base, TimestampMixin, SeenMixin):
    """Една продавница со свој ценовник."""

    __tablename__ = "store"
    __table_args__ = (
        # external_id е идентификаторот од изворниот сајт: Веро page id,
        # Кипер post_id, КАМ ShopFiles[0].RelativePath, Рамстор slug на страница.
        UniqueConstraint("chain_id", "external_id", name="store_chain_external"),
        Index("ix_store_chain_city", "chain_id", "city_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain_id: Mapped[int] = mapped_column(
        ForeignKey("chain.id", ondelete="CASCADE"), index=True
    )
    city_id: Mapped[int | None] = mapped_column(
        ForeignKey("city.id", ondelete="SET NULL"), index=True
    )

    external_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(String(255))
    # Страницата/PDF-от од кој се чита ценовникот на оваа продавница.
    source_url: Mapped[str | None] = mapped_column(String(1024))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    chain: Mapped[Chain] = relationship(back_populates="stores")
    city: Mapped[City | None] = relationship(back_populates="stores")
    runs: Mapped[list[PricelistRun]] = relationship(back_populates="store")
    price_rows: Mapped[list[PriceRow]] = relationship(back_populates="store")

    def __repr__(self) -> str:
        return f"<Store {self.chain_id}/{self.external_id} {self.name!r}>"
