"""add empty run status

Празен ценовник добива свој статус. Дотогаш се водеше како FAILED, што
мешаше две различни работи: „не можевме да прочитаме" и „изворот одговори
но нема што да објави". Кипер во Штип и Зајас враќа recordsTotal=0 секој
ден - со FAILED состојбата изгледаше алармантно без причина.

Revision ID: b3f41c7d2e90
Revises: e17a05a824b6
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b3f41c7d2e90"
down_revision: str | None = "e17a05a824b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = ("running", "success", "failed", "structure_changed", "unchanged", "skipped")
NEW = (*OLD, "empty")


def _apply(values: tuple[str, ...]) -> None:
    allowed = ", ".join(f"'{value}'" for value in values)
    op.drop_constraint("status_valid", "pricelist_run", type_="check")
    op.create_check_constraint("status_valid", "pricelist_run", f"status IN ({allowed})")


def upgrade() -> None:
    _apply(NEW)


def downgrade() -> None:
    # Записите со новиот статус мора да се вратат пред ограничувањето.
    op.execute("UPDATE pricelist_run SET status = 'failed' WHERE status = 'empty'")
    _apply(OLD)
