"""Чистење на стара историја.

Што расте и колку:

- `price_change` - само при промена на цена, околу 10-15 MB дневно.
- `price_row` - дневните попусти, околу 15 MB дневно.
- `pricelist_run` - по еден ред за секое читање, занемарливо.
- `current_price` - НЕ расте; се пребришува.
- `daily_store_stats` - 153 реда дневно, околу 56 илјади годишно.
  Занемарливо, и токму тоа е што ја носи долгорочната статистика -
  затоа НЕ се чисти.

Чистењето се врти по дневното читање. Без него, за две години базата би
била околу 20 GB, што не е страшно, но нема причина да се чува цена на
леб од пред три години.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import PriceChange, PricelistRun, PriceRow
from app.services.ingest import today_local

log = get_logger(__name__)

# Колку долго се чува историјата.
KEEP_YEARS = 2
KEEP_DAYS = KEEP_YEARS * 365


@dataclass(slots=True)
class CleanupResult:
    cutoff: date
    price_changes: int = 0
    price_rows: int = 0
    runs: int = 0

    @property
    def total(self) -> int:
        return self.price_changes + self.price_rows + self.runs


def cutoff_date(today: date | None = None, *, keep_days: int = KEEP_DAYS) -> date:
    """Сè постаро од овој датум се брише."""
    return (today or today_local()) - timedelta(days=keep_days)


async def cleanup_old_history(
    session: AsyncSession, *, today: date | None = None, keep_days: int = KEEP_DAYS
) -> CleanupResult:
    """Ја брише историјата постара од две години.

    daily_store_stats НЕ се чисти: таа е мала, а токму таа ја носи
    долгорочната слика („кој маркет имаше најголеми попусти лани").
    """
    cutoff = cutoff_date(today, keep_days=keep_days)
    result = CleanupResult(cutoff=cutoff)

    changes = await session.execute(
        delete(PriceChange).where(PriceChange.changed_on < cutoff)
    )
    result.price_changes = changes.rowcount or 0

    rows = await session.execute(delete(PriceRow).where(PriceRow.run_date < cutoff))
    result.price_rows = rows.rowcount or 0

    # Читањата се бришат последни: price_row упатува кон нив.
    runs = await session.execute(
        delete(PricelistRun).where(PricelistRun.run_date < cutoff)
    )
    result.runs = runs.rowcount or 0

    if result.total:
        log.info(
            "Исчистена историја пред %s: %d промени, %d попусти, %d читања",
            cutoff,
            result.price_changes,
            result.price_rows,
            result.runs,
        )
    return result
