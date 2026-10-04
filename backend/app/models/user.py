"""Корисници и нивните избори (град, продавници, производи)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.chain import Store
    from app.models.geo import City


class User(Base, TimestampMixin):
    """Најава со корисничко име + лозинка (argon2)."""

    __tablename__ = "app_user"  # "user" е резервиран збор во PostgreSQL

    id: Mapped[int] = mapped_column(primary_key=True)
    # Се чува секогаш со мали букви (нормализирано при регистрација), за да
    # „Darko" и „darko" не бидат два различни корисника.
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))

    city_id: Mapped[int | None] = mapped_column(
        ForeignKey("city.id", ondelete="SET NULL"), index=True
    )
    # „Сите маркети во градот": наместо да снимаме снимка од тогашните
    # продавници, го паметиме намерата - нови продавници влегуваат сами.
    follow_all_stores: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    city: Mapped[City | None] = relationship(back_populates="users")
    store_links: Mapped[list[UserStore]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    picks: Mapped[list[UserPick]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="UserPick.position",
    )

    def __repr__(self) -> str:
        return f"<User {self.username!r}>"


class UserStore(Base):
    """Одбрани продавници на корисникот."""

    __tablename__ = "user_store"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True
    )
    store_id: Mapped[int] = mapped_column(
        ForeignKey("store.id", ondelete="CASCADE"), primary_key=True
    )

    user: Mapped[User] = relationship(back_populates="store_links")
    store: Mapped[Store] = relationship()


class UserPick(Base, TimestampMixin):
    """Едно нешто што корисникот следи.

    Се чува ТОЧНО во истиот запис како во URL-то и во колачето:
    „kafe" за цела категорија, „kafe~нескафе" за бренд во неа, „~нескафе"
    за бренд насекаде. Еден запис, еден парсер (`app.catalog.picks`), едно
    правило за чистење - наместо трета претстава што треба да се држи во
    чекор со другите две.

    Затоа тука нема врска кон `product_category`: слугот е идентитетот низ
    целата апликација, а категориите се создаваат ОД код. Непознат слуг не
    е грешка во базата - се игнорира при читање, исто како во URL-то.
    """

    __tablename__ = "user_pick"
    __table_args__ = (
        UniqueConstraint("user_id", "pick_key", name="user_pick_unique"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    pick_key: Mapped[str] = mapped_column(String(200))
    # Редоследот го одредува каталогот, не корисникот, но се запишува за да
    # листата изгледа исто при секое читање.
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    user: Mapped[User] = relationship(back_populates="picks")

    def __repr__(self) -> str:
        return f"<UserPick {self.pick_key!r}>"
