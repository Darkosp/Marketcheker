"""Корисници и нивни избори (град, продавници, категории)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.catalog import ProductCategory
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
    category_links: Mapped[list[UserCategory]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
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


class UserCategory(Base):
    """Одбрани општи категории на корисникот."""

    __tablename__ = "user_category"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("product_category.id", ondelete="CASCADE"), primary_key=True
    )

    user: Mapped[User] = relationship(back_populates="category_links")
    category: Mapped[ProductCategory] = relationship()
