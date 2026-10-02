"""Дневно закажување: читај ги ценовниците секој ден во 11:00 (Europe/Skopje).

Се врти како посебен контејнер (`scheduler` во docker compose), не во
апликацијата. Причина: ако апликацијата еден ден се врти во повеќе копии,
секоја би го правела истото читање.

Зошто 11:00: Рамстор ажурира во 4:00, КАМ околу 5:50, Веро околу 7:00.
Во 11:00 сите се освежени со добра резерва.

    docker compose up -d scheduler          # закажано читање
    docker compose logs -f scheduler        # што прави
    docker compose exec api python -m app.cli citaj   # рачно, сега

Три работи што ги прави сам:

1. **Не чита двапати истовремено.** max_instances=1; ако вчерашното читање
   некако сè уште трае, новото се прескокнува наместо да се удвои.

2. **Не го губи денот поради доцнење.** misfire_grace_time дозволува
   читањето да почне и подоцна ако серверот бил зафатен во 11:00.

3. **Надокнадува пропуштен ден.** Ако контејнерот бил спуштен во 11:00 и
   се крева во 15:00, а за денес нема успешно читање - чита веднаш.
   Ако веќе има, не чита повторно.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from datetime import date, datetime, time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.logging import get_logger, setup_logging
from app.db.session import SessionLocal, dispose_engine
from app.models import PricelistRun
from app.models.enums import RunStatus
from app.services.ingest import today_local
from app.services.retention import cleanup_old_history
from app.services.runner import run_all

log = get_logger(__name__)

JOB_ID = "dnevno-citanje"

# Колку доцна смее да почне пропуштено читање: 6 часа. Подоцна од тоа и
# ценовникот веќе не е „денешен" во корисна смисла.
MISFIRE_GRACE_SECONDS = 6 * 60 * 60

# Состојби што значат „овој ден е прочитан, не повторувај".
DONE_STATUSES = (RunStatus.SUCCESS, RunStatus.UNCHANGED, RunStatus.EMPTY)


def should_catch_up(now: datetime, *, scheduled: time, already_read: bool) -> bool:
    """Дали да се чита веднаш штом закажувачот се крене.

    Да, само ако закажаниот час веќе поминал денес а читањето го нема.
    Чиста функција - затоа е тестирана без часовник и без база.
    """
    if already_read:
        return False
    return now.timetz().replace(tzinfo=None) >= scheduled


async def has_successful_run(run_date: date) -> bool:
    """Дали за овој ден веќе има барем едно читање што стигнало до крај."""
    async with SessionLocal() as session:
        found = await session.scalar(
            select(PricelistRun.id)
            .where(
                PricelistRun.run_date == run_date,
                PricelistRun.status.in_(DONE_STATUSES),
            )
            .limit(1)
        )
    return found is not None


async def daily_read() -> None:
    """Работата што ја врти закажувачот."""
    run_date = today_local()
    log.info("Почнувам дневно читање за %s", run_date)
    try:
        outcomes = await run_all(run_date=run_date)
    except Exception:
        # Закажувачот мора да преживее; утре пак се обидува.
        log.exception("Дневното читање падна целосно")
        return

    # Старата историја се чисти по читањето, не пред: ако читањето падне,
    # барем не сме бришеле без причина.
    try:
        async with SessionLocal() as session:
            await cleanup_old_history(session)
            await session.commit()
    except Exception:
        log.exception("Чистењето на старата историја падна")

    for outcome in outcomes:
        log.info(
            "%s: %d/%d продавници, %d попусти, %d непроменети, %d без цени, %d паднати",
            outcome.chain_code,
            outcome.succeeded,
            outcome.stores,
            outcome.discounts,
            outcome.unchanged,
            outcome.empty,
            outcome.failed,
        )


def build_scheduler(settings: Settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.tz)
    scheduler.add_job(
        daily_read,
        trigger=CronTrigger(
            hour=settings.scheduler_hour,
            minute=settings.scheduler_minute,
            timezone=settings.tz,
        ),
        id=JOB_ID,
        name="Дневно читање на ценовниците",
        # Никогаш две читања истовремено.
        max_instances=1,
        # Ако се натрупале пропуштени термини, врти едно, не сите.
        coalesce=True,
        misfire_grace_time=MISFIRE_GRACE_SECONDS,
    )
    return scheduler


async def main() -> int:
    settings = get_settings()
    setup_logging(settings.log_level)

    if not settings.scheduler_enabled:
        log.warning("SCHEDULER_ENABLED=false - закажувачот не стартува")
        return 0

    scheduled = time(settings.scheduler_hour, settings.scheduler_minute)
    scheduler = build_scheduler(settings)
    scheduler.start()
    log.info(
        "Закажувачот работи: секој ден во %02d:%02d (%s)",
        settings.scheduler_hour,
        settings.scheduler_minute,
        settings.scheduler_timezone,
    )

    now = datetime.now(settings.tz)
    already = await has_successful_run(today_local())
    if should_catch_up(now, scheduled=scheduled, already_read=already):
        log.info(
            "Закажаниот час (%s) веќе помина, а за денес нема читање - читам сега",
            scheduled.strftime("%H:%M"),
        )
        await daily_read()
    elif already:
        log.info("За денес веќе има читање; чекам го следниот термин")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    await stop.wait()
    log.info("Закажувачот запира")
    scheduler.shutdown(wait=True)
    await dispose_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
