"""Дневното читање: продолжување и поврзаност на деловите.

Читањето е прекинливо: ако падне на половина - заспана машина, рестарт,
прекин на мрежа - следното пуштање ги прескокнува веќе прочитаните
продавници наместо да почне од нула.
"""

from __future__ import annotations

import inspect

import pytest

from app.models.enums import RunStatus
from app.readers.base import StoreRef
from app.services import runner
from app.services.ingest import ensure_chain, ensure_store, start_run

pytestmark = pytest.mark.db

from datetime import UTC, date, datetime  # noqa: E402

RUN_DATE = date(2026, 10, 2)


class _Reader:
    chain_code = "vero"
    chain_name = "Веро"
    website = "https://pricelist.vero.com.mk/"


async def _store_with_run(session, external_id: str, status: RunStatus):
    chain = await ensure_chain(session, _Reader)
    store = await ensure_store(
        session, chain, StoreRef(external_id=external_id, name=f"ВЕРО {external_id}")
    )
    run = await start_run(session, chain, store, run_date=RUN_DATE)
    run.status = status
    run.finished_at = datetime.now(UTC)
    await session.flush()
    return chain, store


# ==========================================================================
# Што се смета за „веќе прочитано"
# ==========================================================================
@pytest.mark.parametrize(
    "status",
    [RunStatus.SUCCESS, RunStatus.UNCHANGED, RunStatus.EMPTY],
)
async def test_finished_runs_count_as_read(db_session, status) -> None:
    """Успешно, непроменето и без цени се подеднакво завршени."""
    chain, _ = await _store_with_run(db_session, "89", status)
    done = await runner._already_read(db_session, chain.id, RUN_DATE)
    assert done == {"89"}


@pytest.mark.parametrize(
    "status",
    [RunStatus.FAILED, RunStatus.STRUCTURE_CHANGED, RunStatus.RUNNING],
)
async def test_unfinished_runs_are_retried(db_session, status) -> None:
    """Паднато читање НЕ се прескокнува - вреди да се обидеме повторно."""
    chain, _ = await _store_with_run(db_session, "89", status)
    done = await runner._already_read(db_session, chain.id, RUN_DATE)
    assert done == set()


async def test_another_day_does_not_count(db_session) -> None:
    chain, _ = await _store_with_run(db_session, "89", RunStatus.SUCCESS)
    done = await runner._already_read(db_session, chain.id, date(2026, 10, 1))
    assert done == set()


async def test_only_this_chain_counts(db_session) -> None:
    chain, _ = await _store_with_run(db_session, "89", RunStatus.SUCCESS)
    done = await runner._already_read(db_session, chain.id + 999, RUN_DATE)
    assert done == set()


async def test_empty_when_nothing_was_read(db_session) -> None:
    chain = await ensure_chain(db_session, _Reader)
    await db_session.flush()
    assert await runner._already_read(db_session, chain.id, RUN_DATE) == set()


async def test_several_stores_are_all_remembered(db_session) -> None:
    chain = None
    for external_id in ("89", "91", "94"):
        chain, _ = await _store_with_run(db_session, external_id, RunStatus.SUCCESS)
    done = await runner._already_read(db_session, chain.id, RUN_DATE)
    assert done == {"89", "91", "94"}


# ==========================================================================
# Поврзаност: resume мора да помине низ сите нивоа
# ==========================================================================
def test_resume_is_passed_through_every_level() -> None:
    """Регресија: run_all не го предаваше resume на _run_one.

    Тестовите не го фатија - ниеден не ја вика run_all - па падна дури при
    рачно пуштање. Овде барем потписите мора да се совпаѓаат.
    """
    for function in (runner.run_all, runner._run_one, runner.run_chain):
        assert "resume" in inspect.signature(function).parameters, function.__name__


def test_resume_is_on_by_default() -> None:
    """Прекинливоста е стандардна; повторното читање е свесен избор."""
    for function in (runner.run_all, runner.run_chain):
        assert inspect.signature(function).parameters["resume"].default is True


def test_outcome_reports_skipped_stores() -> None:
    outcome = runner.ChainOutcome(chain_code="vero", skipped=5)
    assert outcome.skipped == 5
    # Прескокнатите не се грешка.
    assert outcome.ok is True
