"""Градови."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.chain import Store
    from app.models.user import User


class City(Base, TimestampMixin):
    """Град. Изворите го пишуваат различно („СКОПЈЕ", „Скопје", „Skopje"),
    затоа slug-от е нормализиран клуч за спојување.
    """

    __tablename__ = "city"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))

    stores: Mapped[list[Store]] = relationship(back_populates="city")
    users: Mapped[list[User]] = relationship(back_populates="city")

    def __repr__(self) -> str:
        return f"<City {self.slug}>"
