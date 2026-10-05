"""Декларативна основа и заеднички mixin-и за моделите."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Експлицитни имена на ограничувања - без ова Alembic генерира анонимни
# имена што подоцна не може да ги измени (пр. при ALTER на constraint).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_N_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def enum_column[T: StrEnum](enum_class: type[T], *, length: int = 32) -> Enum:
    """VARCHAR + CHECK наместо PostgreSQL ENUM тип.

    Зошто не native enum: додавање нова вредност во PostgreSQL ENUM е
    посебна миграција со ALTER TYPE; со VARCHAR + CHECK е обична.

    values_callable е задолжително: без него SQLAlchemy го чува ИМЕТО на
    членот („DISCOUNT") наместо вредноста („discount").

    Без ова колоната се чита како обичен str и анотацијата лаже - што веќе
    се случи: product.base_unit.value фрлаше AttributeError.
    """
    return Enum(
        enum_class,
        native_enum=False,
        length=length,
        values_callable=lambda cls: [member.value for member in cls],
        validate_strings=True,
    )


class TimestampMixin:
    """created_at / updated_at што ги поставува базата, не апликацијата."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SeenMixin:
    """Кога изворот прв пат го покажа записот и кога последно беше виден.

    Продавниците и производите се појавуваат и исчезнуваат од ценовниците;
    ова ни дава да знаеме што е сè уште актуелно без да бришеме историја.
    """

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
