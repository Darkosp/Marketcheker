"""store enums as checked varchar

Колоните со набројувања се чуваат како VARCHAR (не како PostgreSQL ENUM тип,
за да додавање нова вредност остане обична миграција). Моделите веќе ги
читаат како Python enum преку db.base.enum_column.

Ова додава CHECK ограничувања, за да базата одбие непозната вредност дури и
ако некој пишува со psql, настрана од апликацијата.

Alembic НЕ ги споредува CHECK ограничувањата при autogenerate, затоа тие се
напишани рачно и не се појавуваат во идни автоматски миграции.

Revision ID: e17a05a824b6
Revises: 8ae205c0b9f8
Create Date: 2026-10-02 08:10:58.697989+02:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e17a05a824b6"
down_revision: str | None = "8ae205c0b9f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (табела, колона, дозволено е NULL, вредности)
CHECKS: tuple[tuple[str, str, bool, tuple[str, ...]], ...] = (
    (
        "pricelist_run",
        "status",
        False,
        ("running", "success", "failed", "structure_changed", "unchanged", "skipped"),
    ),
    (
        "price_row",
        "promo_type",
        False,
        ("discount", "loyalty", "multibuy", "other", "none"),
    ),
    (
        "product",
        "category_status",
        False,
        ("unknown", "auto", "confirmed", "rejected"),
    ),
    ("product", "base_unit", True, ("kg", "l", "kom", "m", "m2", "pranje")),
    (
        "product_category",
        "default_base_unit",
        True,
        ("kg", "l", "kom", "m", "m2", "pranje"),
    ),
)


def _name(column: str) -> str:
    """Само делот од името; конвенцијата од db.base додава „ck_<табела>_".

    Со целосно име излегува дупло: ck_price_row_ck_price_row_promo_type_valid.
    """
    return f"{column}_valid"


def upgrade() -> None:
    for table, column, nullable, values in CHECKS:
        allowed = ", ".join(f"'{value}'" for value in values)
        condition = f"{column} IN ({allowed})"
        if nullable:
            condition = f"{column} IS NULL OR {condition}"
        op.create_check_constraint(_name(column), table, condition)


def downgrade() -> None:
    for table, column, _nullable, _values in CHECKS:
        op.drop_constraint(_name(column), table, type_="check")
