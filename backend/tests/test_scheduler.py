"""Дневното закажување.

Се тестира одлуката и поставеноста, не самиот APScheduler.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.core.config import Settings
from app.scheduler import (
    DONE_STATUSES,
    JOB_ID,
    MISFIRE_GRACE_SECONDS,
    build_scheduler,
    should_catch_up,
)

SKOPJE = ZoneInfo("Europe/Skopje")
ELEVEN = time(11, 0)


def _settings(**overrides) -> Settings:
    base = {
        "secret_key": "test-secret-key-dolga-najmalku-16",
        "postgres_password": "test",
    }
    base.update(overrides)
    return Settings(**base)


# ---- надокнада на пропуштен ден -------------------------------------------
def test_catches_up_when_started_after_the_hour() -> None:
    """Контејнерот бил спуштен во 11:00, се крева во 15:00 - читај веднаш."""
    now = datetime(2026, 10, 2, 15, 0, tzinfo=SKOPJE)
    assert should_catch_up(now, scheduled=ELEVEN) is True


def test_catches_up_even_if_something_was_already_read() -> None:
    """Читањето ги прескокнува веќе прочитаните, па повторувањето го
    ДОВРШУВА денот ако претходното паднало на половина."""
    now = datetime(2026, 10, 2, 15, 0, tzinfo=SKOPJE)
    assert should_catch_up(now, scheduled=ELEVEN) is True


def test_does_not_catch_up_before_the_hour() -> None:
    """Во 09:00 нема што да се надокнадува - термин во 11:00 допрва доаѓа."""
    now = datetime(2026, 10, 2, 9, 0, tzinfo=SKOPJE)
    assert should_catch_up(now, scheduled=ELEVEN) is False


def test_catches_up_exactly_at_the_hour() -> None:
    now = datetime(2026, 10, 2, 11, 0, tzinfo=SKOPJE)
    assert should_catch_up(now, scheduled=ELEVEN) is True


def test_catches_up_one_minute_after() -> None:
    now = datetime(2026, 10, 2, 11, 1, tzinfo=SKOPJE)
    assert should_catch_up(now, scheduled=ELEVEN) is True


def test_does_not_catch_up_one_minute_before() -> None:
    now = datetime(2026, 10, 2, 10, 59, tzinfo=SKOPJE)
    assert should_catch_up(now, scheduled=ELEVEN) is False


# ---- поставеност на работата ----------------------------------------------
def test_job_is_scheduled_at_configured_time() -> None:
    scheduler = build_scheduler(_settings(scheduler_hour=11, scheduler_minute=0))
    job = scheduler.get_job(JOB_ID)
    assert job is not None
    fields = {field.name: str(field) for field in job.trigger.fields}
    assert fields["hour"] == "11"
    assert fields["minute"] == "0"


def test_job_uses_skopje_timezone() -> None:
    """Дневниот клуч е по Europe/Skopje, не по UTC."""
    scheduler = build_scheduler(_settings())
    job = scheduler.get_job(JOB_ID)
    assert str(job.trigger.timezone) == "Europe/Skopje"


def test_custom_time_is_respected() -> None:
    scheduler = build_scheduler(_settings(scheduler_hour=6, scheduler_minute=30))
    fields = {f.name: str(f) for f in scheduler.get_job(JOB_ID).trigger.fields}
    assert fields["hour"] == "6"
    assert fields["minute"] == "30"


def test_never_two_reads_at_once() -> None:
    # Читањето трае минути; две истовремени би се биеле за истите редови.
    job = build_scheduler(_settings()).get_job(JOB_ID)
    assert job.max_instances == 1


def test_missed_runs_are_coalesced() -> None:
    # По долго спуштање не смее да врти по едно читање за секој пропуштен ден.
    job = build_scheduler(_settings()).get_job(JOB_ID)
    assert job.coalesce is True


def test_late_start_is_still_allowed() -> None:
    job = build_scheduler(_settings()).get_job(JOB_ID)
    assert job.misfire_grace_time == MISFIRE_GRACE_SECONDS
    assert MISFIRE_GRACE_SECONDS == 6 * 60 * 60


# ---- кои состојби значат „прочитано" --------------------------------------
def test_done_statuses_include_unchanged_and_empty() -> None:
    """Непроменет ценовник и извор без цени се исто така завршено читање.

    Инаку закажувачот би читал повторно на секое кревање.
    """
    values = {status.value for status in DONE_STATUSES}
    assert values == {"success", "unchanged", "empty"}


def test_failed_is_not_done() -> None:
    from app.models.enums import RunStatus

    assert RunStatus.FAILED not in DONE_STATUSES
    assert RunStatus.STRUCTURE_CHANGED not in DONE_STATUSES


# ---- исклучување -----------------------------------------------------------
def test_scheduler_can_be_disabled() -> None:
    assert _settings(scheduler_enabled=False).scheduler_enabled is False


def test_invalid_hour_is_rejected() -> None:
    with pytest.raises(ValueError, match="scheduler_hour"):
        _settings(scheduler_hour=25)
