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
    """Сметка без лозинка и без корисничко име.

    Адресата е сè: со неа се отвора сметката, се потврдува со линк, и се
    влегува. Едно поле за пополнување наместо две.

    `username` и `password_hash` останале од поранешните верзии и се празни
    кај новите сметки. Кога ќе се испразнат сосема, колоните може да паднат.
    Името што се гледа на екран се вади од адресата (`display`).
    """

    __tablename__ = "app_user"  # "user" е резервиран збор во PostgreSQL

    id: Mapped[int] = mapped_column(primary_key=True)
    # Се чува секогаш со мали букви (нормализирано при регистрација), за да
    # „Darko" и „darko" не бидат два различни корисника.
    # Остаток од верзијата со корисничко име; новите сметки го немаат.
    username: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)

    # Адресата е единствена: едно сандаче, една сметка. Може да фали само
    # кај старите сметки направени пред да има пошта.
    email: Mapped[str | None] = mapped_column(String(254), unique=True, index=True)
    # Празно значи непотврдена адреса - сметката постои, но не се отвора.
    email_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # Еднократниот код во последниот испратен линк. Се брише при влегување,
    # па линкот важи еднаш; нов линк го поништува претходниот.
    login_code: Mapped[str | None] = mapped_column(String(64))

    # Останува од верзијата со лозинки; новите сметки го немаат.
    password_hash: Mapped[str | None] = mapped_column(String(255))

    city_id: Mapped[int | None] = mapped_column(
        ForeignKey("city.id", ondelete="SET NULL"), index=True
    )
    # „Сите маркети во градот": наместо да снимаме снимка од тогашните
    # продавници, го паметиме намерата - нови продавници влегуваат сами.
    follow_all_stores: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    # Затворена сметка - одлука на администраторот, одделно од потврдата.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_confirmed(self) -> bool:
        return self.email_confirmed_at is not None

    @property
    def display(self) -> str:
        """Како се обраќаме на екран.

        Делот од адресата пред „@": „darko@primer.mk" станува „darko". Не е
        единствено и не служи за најава - само за поздрав.
        """
        if self.username:
            return self.username
        return (self.email or "").split("@")[0] or "корисник"

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
