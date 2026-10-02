"""Чување на целиот асортиман, не само на попустите.

Трите табели имаат различна намена и различен раст:
- current_price се ПРЕБРИШУВА, не расте;
- price_change се пишува САМО при промена;
- daily_store_stats дава збир по продавница и ден.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.catalog import default_grouper
from app.catalog.groups import GROUPS, SUBCATEGORIES
from app.models import CurrentPrice, DailyStoreStats, PriceChange, PriceRow
from app.models.enums import PromoType
from app.readers.base import RawPriceRow, ReaderResult, StoreRef
from app.services import ingest
from app.services.retention import cleanup_old_history, cutoff_date

pytestmark = pytest.mark.db

RUN_DATE = date(2026, 10, 2)


class _Reader:
    chain_code = "vero"
    chain_name = "Веро"
    website = "https://pricelist.vero.com.mk/"


STORE = StoreRef(external_id="89", name="ВЕРО 1", city="Скопје")


def _row(name: str, **kwargs) -> RawPriceRow:
    defaults = {
        "description": "КАФЕ - ИНСТАНТ КАФЕ",
        "sale_price": Decimal("100"),
    }
    defaults.update(kwargs)
    return RawPriceRow(name=name, **defaults)


async def _ingest(session, rows: list[RawPriceRow], *, run_date=RUN_DATE, hash_="h1"):
    chain = await ingest.ensure_chain(session, _Reader)
    categories = await ingest.ensure_categories(session, GROUPS, SUBCATEGORIES)
    store = await ingest.ensure_store(session, chain, STORE)
    run = await ingest.start_run(session, chain, store, run_date=run_date)
    result = ReaderResult(
        store=STORE, rows=rows, source_url="https://x/", content_hash=hash_
    )
    await ingest.save_result(
        session, run, store, result, grouper=default_grouper(), categories=categories
    )
    await session.flush()
    return run


# ==========================================================================
# Целиот асортиман
# ==========================================================================
async def test_all_rows_are_stored_not_only_discounts(db_session) -> None:
    """Без цената на јајцата кога НЕ се на попуст, корпа не може да се состави."""
    await _ingest(
        db_session,
        [
            _row("ЈАЈЦА 10/1", sale_price=Decimal("120")),
            _row("МЛЕКО 1Л", sale_price=Decimal("60")),
            _row(
                "ЛЕБ 500Г",
                sale_price=Decimal("40"),
                regular_price=Decimal("50"),
                discount_price=Decimal("40"),
                discount_pct=Decimal("20"),
            ),
        ],
    )

    assert await db_session.scalar(select(func.count()).select_from(CurrentPrice)) == 3
    # А во price_row и натаму одат само попустите - тоа ја чита страницата.
    assert await db_session.scalar(select(func.count()).select_from(PriceRow)) == 1


async def test_current_price_keeps_the_prices(db_session) -> None:
    await _ingest(db_session, [_row("МЛЕКО 1Л", sale_price=Decimal("60"))])

    price = await db_session.scalar(select(CurrentPrice))
    assert price.sale_price == Decimal("60")
    assert price.is_discount is False
    assert price.promo_type is PromoType.NONE
    assert price.run_date == RUN_DATE


async def test_unit_price_is_computed_for_regular_prices_too(db_session) -> None:
    """Споредбата по кг/л мора да работи и за производи без попуст."""
    await _ingest(db_session, [_row("КАФЕ 250ГР", sale_price=Decimal("250"))])

    price = await db_session.scalar(select(CurrentPrice))
    assert price.unit_price_base == Decimal("1000")  # 250 ден за 0.25 кг


# ==========================================================================
# Пребришување, не растење
# ==========================================================================
async def test_second_run_overwrites_instead_of_adding(db_session) -> None:
    """current_price НЕ расте - тоа е целата поента."""
    await _ingest(db_session, [_row("МЛЕКО 1Л", sale_price=Decimal("60"))], hash_="a")
    await _ingest(
        db_session,
        [_row("МЛЕКО 1Л", sale_price=Decimal("65"))],
        run_date=RUN_DATE + timedelta(days=1),
        hash_="b",
    )

    assert await db_session.scalar(select(func.count()).select_from(CurrentPrice)) == 1
    price = await db_session.scalar(select(CurrentPrice))
    assert price.sale_price == Decimal("65")


# ==========================================================================
# Историја само при промена
# ==========================================================================
async def test_no_change_means_no_history_row(db_session) -> None:
    """Истата цена два дена не пишува ништо - инаку историјата би била
    310 MB дневно, од кои 99% идентични редови."""
    await _ingest(db_session, [_row("МЛЕКО 1Л", sale_price=Decimal("60"))], hash_="a")
    await _ingest(
        db_session,
        [_row("МЛЕКО 1Л", sale_price=Decimal("60"))],
        run_date=RUN_DATE + timedelta(days=1),
        hash_="b",
    )

    assert await db_session.scalar(select(func.count()).select_from(PriceChange)) == 0


async def test_price_change_is_recorded(db_session) -> None:
    await _ingest(db_session, [_row("МЛЕКО 1Л", sale_price=Decimal("60"))], hash_="a")
    await _ingest(
        db_session,
        [_row("МЛЕКО 1Л", sale_price=Decimal("72"))],
        run_date=RUN_DATE + timedelta(days=1),
        hash_="b",
    )

    change = await db_session.scalar(select(PriceChange))
    assert change is not None
    assert change.previous_sale_price == Decimal("60")
    assert change.sale_price == Decimal("72")
    assert change.changed_on == RUN_DATE + timedelta(days=1)


async def test_first_sighting_is_not_a_change(db_session) -> None:
    # Нов производ нема претходна цена - не е поскапување.
    await _ingest(db_session, [_row("МЛЕКО 1Л", sale_price=Decimal("60"))])
    assert await db_session.scalar(select(func.count()).select_from(PriceChange)) == 0


async def test_discount_appearing_is_a_change(db_session) -> None:
    await _ingest(db_session, [_row("МЛЕКО 1Л", sale_price=Decimal("60"))], hash_="a")
    await _ingest(
        db_session,
        [
            _row(
                "МЛЕКО 1Л",
                sale_price=Decimal("48"),
                regular_price=Decimal("60"),
                discount_price=Decimal("48"),
            )
        ],
        run_date=RUN_DATE + timedelta(days=1),
        hash_="b",
    )

    change = await db_session.scalar(select(PriceChange))
    assert change.is_discount is True
    assert change.discount_price == Decimal("48")


# ==========================================================================
# Статистика
# ==========================================================================
async def test_daily_stats_are_written(db_session) -> None:
    await _ingest(
        db_session,
        [
            _row("А", sale_price=Decimal("100")),
            _row("Б", sale_price=Decimal("100")),
            _row(
                "В",
                sale_price=Decimal("80"),
                regular_price=Decimal("100"),
                discount_price=Decimal("80"),
                discount_pct=Decimal("20"),
            ),
            _row(
                "Г",
                sale_price=Decimal("50"),
                regular_price=Decimal("100"),
                discount_price=Decimal("50"),
                discount_pct=Decimal("50"),
                promo_type_raw="ЛОЈАЛНОСТ",
            ),
        ],
    )

    stats = await db_session.scalar(select(DailyStoreStats))
    assert stats.products_total == 4
    assert stats.discounts_total == 2
    assert stats.loyalty_total == 1
    assert stats.avg_discount_pct == Decimal("35.00")
    assert stats.max_discount_pct == Decimal("50")
    # 20 + 50 денари симнати од редовните цени.
    assert stats.total_savings == Decimal("70")


async def test_discount_share_is_the_useful_measure(db_session) -> None:
    """Број на попусти без асортиман не значи ништо."""
    await _ingest(
        db_session,
        [
            _row("А", sale_price=Decimal("100")),
            _row(
                "Б",
                sale_price=Decimal("80"),
                regular_price=Decimal("100"),
                discount_price=Decimal("80"),
            ),
        ],
    )
    stats = await db_session.scalar(select(DailyStoreStats))
    assert stats.discount_share == 0.5


async def test_stats_are_rewritten_not_duplicated(db_session) -> None:
    await _ingest(db_session, [_row("А", sale_price=Decimal("100"))], hash_="a")
    await _ingest(db_session, [_row("А", sale_price=Decimal("110"))], hash_="b")

    assert await db_session.scalar(select(func.count()).select_from(DailyStoreStats)) == 1


# ==========================================================================
# Чистење по две години
# ==========================================================================
def test_cutoff_is_two_years_back() -> None:
    # 2 x 365 дена наназад. 2024 е престапна, но бројот на денови е фиксен
    # намерно: подобро предвидливо правило отколку календарска аритметика.
    today = date(2026, 10, 2)
    assert cutoff_date(today) == date(2024, 10, 2)
    assert (today - cutoff_date(today)).days == 730


async def test_old_history_is_deleted(db_session) -> None:
    old = RUN_DATE - timedelta(days=800)
    await _ingest(db_session, [_row("МЛЕКО 1Л")], run_date=old, hash_="a")
    await _ingest(db_session, [_row("МЛЕКО 1Л")], run_date=RUN_DATE, hash_="b")

    result = await cleanup_old_history(db_session, today=RUN_DATE)
    assert result.runs == 1
    assert result.cutoff < RUN_DATE


async def test_recent_history_survives(db_session) -> None:
    await _ingest(db_session, [_row("МЛЕКО 1Л")], run_date=RUN_DATE)
    result = await cleanup_old_history(db_session, today=RUN_DATE)
    assert result.total == 0


async def test_daily_stats_are_never_cleaned(db_session) -> None:
    """Статистиката е мала а носи долгорочна слика - не се чисти."""
    old = RUN_DATE - timedelta(days=900)
    await _ingest(db_session, [_row("МЛЕКО 1Л")], run_date=old)

    await cleanup_old_history(db_session, today=RUN_DATE)
    assert await db_session.scalar(select(func.count()).select_from(DailyStoreStats)) == 1
