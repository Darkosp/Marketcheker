"""index what the pages actually ask for

Две мерени тесни грла, не претпоставени:

**Пребарување по назив.** `ILIKE '%кафе%'` не може да користи обичен
индекс - Postgres го чита целото `product`. Триграмски индекс го решава тоа:
„кафе" падна од 427 на 107 ms, „нескафе" на 9 ms.

**Денешните попусти.** Постоечкиот делумен индекс почнува со `store_id`, па
упит што филтрира само по `run_date` не можеше да го користи како што
треба. Новиот почнува со денот, бидејќи секоја страница прашува „што е на
попуст ДЕНЕС".

Двата се прават `CONCURRENTLY`: на 280.000 реда обичното создавање ја
заклучува табелата, а тоа на сервер значи страница што виси.

Revision ID: 3e640ced7086
Revises: bb5b6bba46b5
Create Date: 2026-10-05 18:57:06.761480+02:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3e640ced7086"
down_revision: str | None = "bb5b6bba46b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # CONCURRENTLY не смее да биде во трансакција.
    with op.get_context().autocommit_block():
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_product_name_trgm "
            "ON product USING gin (raw_name gin_trgm_ops)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_price_row_day "
            "ON price_row (run_date, product_id) WHERE is_discount"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_price_row_day")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_product_name_trgm")
    # Проширувањето останува: може да го користи и нешто друго.
