"""Запишување на прочитан ценовник во базата.

Тестовите бараат жива PostgreSQL; се прескокнуваат ако ја нема.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.catalog import default_grouper
from app.catalog.groups import GROUPS
from app.models import PricelistRun, PriceRow, Product, Store
from app.models.enums import BaseUnit, CategoryStatus, PromoType, RunStatus
from app.readers.base import RawPriceRow, ReaderResult, SourceUnavailable, StoreRef
from app.services import ingest

pytestmark = pytest.mark.db

RUN_DATE = date(2026, 10, 2)


class _FakeReader:
    """Читач што враќа подготвен резултат или фрла грешка."""

    chain_code = "vero"
    chain_name = "Веро"
    website = "https://pricelist.vero.com.mk/"

    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    async def discover_stores(self):
        return [_STORE]

    async def read_store(self, store):
        if self._error is not None:
            raise self._error
        return self._result


_STORE = StoreRef(
    external_id="89",
    name="ВЕРО 1",
    city="Аеродром",
    address="Бул. Јане Сандански бр.111 – Аеродром",
    source_url="https://pricelist.vero.com.mk/89_1.html",
)


def _row(name: str, **kwargs) -> RawPriceRow:
    defaults = {
        "description": "КАФЕ - ИНСТАНТ КАФЕ",
        "sale_price": Decimal("189"),
        "regular_price": Decimal("239"),
        "discount_price": Decimal("189"),
        "discount_pct": Decimal("20"),
        "promo_type_raw": "Акциска цена",
        "valid_from": date(2026, 10, 1),
        "valid_to": date(2026, 10, 7),
        "availability": "Да",
    }
    defaults.update(kwargs)
    return RawPriceRow(name=name, **defaults)


def _result(rows: list[RawPriceRow], **kwargs) -> ReaderResult:
    defaults = {
        "store": _STORE,
        "source_url": _STORE.source_url,
        "pricelist_date": RUN_DATE,
        "content_hash": "hash-1",
    }
    defaults.update(kwargs)
    return ReaderResult(rows=rows, **defaults)


async def _ingest(session, result=None, error=None):
    reader = _FakeReader(result=result, error=error)
    chain = await ingest.ensure_chain(session, _FakeReader)
    groups = await ingest.ensure_groups(session, GROUPS)
    run = await ingest.ingest_store(
        session,
        reader,
        chain,
        _STORE,
        run_date=RUN_DATE,
        grouper=default_grouper(),
        groups=groups,
    )
    await session.flush()
    return run


# ==========================================================================
# Што се зачувува
# ==========================================================================
async def test_only_discount_rows_are_stored(db_session) -> None:
    """Ценовникот има и редови без попуст; се чуваат само попустите."""
    result = _result(
        [
            _row("НЕСКАФЕ КЛАСИК 100ГР"),
            _row("ЧАЈ ЛИПА 20 ВРЕЌИЧКИ", discount_price=None, discount_pct=None),
            _row("НЕСКАФЕ ГОЛД 200ГР"),
        ]
    )
    run = await _ingest(db_session, result)

    stored = await db_session.scalar(select(func.count()).select_from(PriceRow))
    assert stored == 2
    # Но бројот на ПРОЧИТАНИ редови се памети, за да се знае од колку се.
    assert run.rows_total == 3
    assert run.rows_discount == 2
    assert run.status is RunStatus.SUCCESS


async def test_store_and_city_are_created(db_session) -> None:
    await _ingest(db_session, _result([_row("НЕСКАФЕ 100ГР")]))

    # Во async SQLAlchemy врските мора да се вчитаат изрично.
    store = await db_session.scalar(
        select(Store).where(Store.external_id == "89").options(selectinload(Store.city))
    )
    assert store is not None
    assert store.name == "ВЕРО 1"
    # Веро пишува општина; се сведува на Скопје.
    assert store.city is not None
    assert store.city.slug == "skopje"


async def test_product_keeps_name_exactly_as_in_pricelist(db_session) -> None:
    await _ingest(db_session, _result([_row("НЕСКАФЕ КЛАСИК 100ГР")]))

    product = await db_session.scalar(select(Product))
    assert product is not None
    assert product.raw_name == "НЕСКАФЕ КЛАСИК 100ГР"
    assert product.raw_description == "КАФЕ - ИНСТАНТ КАФЕ"


async def test_grammage_is_parsed_into_base_units(db_session) -> None:
    await _ingest(db_session, _result([_row("НЕСКАФЕ КЛАСИК 100ГР")]))

    product = await db_session.scalar(select(Product))
    assert product.package_value == Decimal("100")
    assert product.package_unit == "ГР"
    assert product.base_quantity == Decimal("0.1")
    # Регресија: колоната мора да се чита како enum, не како str.
    assert product.base_unit is BaseUnit.KG


async def test_unit_price_is_computed_for_comparison(db_session) -> None:
    await _ingest(db_session, _result([_row("НЕСКАФЕ КЛАСИК 100ГР")]))

    row = await db_session.scalar(select(PriceRow))
    # 189 ден за 0.1 кг = 1890 ден/кг
    assert row.unit_price_base == Decimal("1890")


async def test_product_is_grouped_by_usage(db_session) -> None:
    await _ingest(db_session, _result([_row("НЕСКАФЕ КЛАСИК 100ГР")]))

    product = await db_session.scalar(
        select(Product).options(selectinload(Product.category))
    )
    assert product.category is not None
    assert product.category.slug == "pijaloci"
    assert product.category_status is CategoryStatus.AUTO


async def test_unknown_product_still_stored_in_fallback_group(db_session) -> None:
    """Непознат производ се прикажува, не се крие."""
    await _ingest(
        db_session,
        _result([_row("ЧУДЕН ПРЕДМЕТ XZ-900", description="НЕПОЗНАТА ЕТИКЕТА")]),
    )

    product = await db_session.scalar(
        select(Product).options(selectinload(Product.category))
    )
    assert product.category.slug == "drugo"
    assert product.category_status is CategoryStatus.UNKNOWN


async def test_loyalty_is_marked_separately(db_session) -> None:
    await _ingest(
        db_session,
        _result([_row("НЕСКАФЕ 100ГР", promo_type_raw="ЛОЈАЛНОСТ")]),
    )

    row = await db_session.scalar(select(PriceRow))
    assert row.promo_type is PromoType.LOYALTY


async def test_single_day_discount_is_flagged(db_session) -> None:
    day = date(2026, 10, 2)
    await _ingest(
        db_session,
        _result([_row("НЕСКАФЕ 100ГР", valid_from=day, valid_to=day)]),
    )

    row = await db_session.scalar(select(PriceRow))
    assert row.is_single_day is True


# ==========================================================================
# Дупликати - отвореното прашање од чекор 3
# ==========================================================================
async def test_duplicate_name_gives_one_product_and_two_price_rows(db_session) -> None:
    """Ист назив и опис, различна цена - се случува кај Веро.

    Производот е еден (fingerprint е ист), но и двата реда се чуваат, за да
    не изгубиме цена. Во приказот ќе се видат како два записа.
    """
    await _ingest(
        db_session,
        _result(
            [
                _row(
                    "ПЕРНИЦА 45х45",
                    description="ДОМ - ТЕКСТИЛ",
                    regular_price=Decimal("399"),
                    discount_price=Decimal("299"),
                ),
                _row(
                    "ПЕРНИЦА 45х45",
                    description="ДОМ - ТЕКСТИЛ",
                    regular_price=Decimal("499"),
                    discount_price=Decimal("399"),
                ),
            ]
        ),
    )

    products = await db_session.scalar(select(func.count()).select_from(Product))
    rows = await db_session.scalar(select(func.count()).select_from(PriceRow))
    assert products == 1
    assert rows == 2


async def test_different_grammage_is_a_different_product(db_session) -> None:
    """Nescafe 100 g и 200 g се различни производи."""
    await _ingest(
        db_session,
        _result([_row("НЕСКАФЕ КЛАСИК 100ГР"), _row("НЕСКАФЕ КЛАСИК 200ГР")]),
    )

    products = await db_session.scalar(select(func.count()).select_from(Product))
    assert products == 2


# ==========================================================================
# Грешки - никогаш тивок празен резултат
# ==========================================================================
async def test_failed_read_is_recorded_with_message(db_session) -> None:
    run = await _ingest(
        db_session,
        error=SourceUnavailable("https://pricelist.vero.com.mk/89_1.html врати 404"),
    )

    assert run.status is RunStatus.FAILED
    assert run.error_type == "SourceUnavailable"
    assert "404" in run.error_message
    assert run.finished_at is not None

    rows = await db_session.scalar(select(func.count()).select_from(PriceRow))
    assert rows == 0


async def test_structure_change_gets_its_own_status(db_session) -> None:
    from app.readers.base import StructureChanged

    run = await _ingest(db_session, error=StructureChanged("нема колона 'назив'"))
    # Одделен статус, за да се види дека бара поправка на читачот, не чекање.
    assert run.status is RunStatus.STRUCTURE_CHANGED


async def test_empty_pricelist_is_recorded_not_silently_zero(db_session) -> None:
    """Празен ценовник се запишува со свој статус, не исчезнува тивко."""
    from app.readers.base import EmptyPricelist

    run = await _ingest(db_session, error=EmptyPricelist("нема редови"))
    assert run.status is RunStatus.EMPTY
    assert run.rows_discount == 0
    assert run.finished_at is not None


# ==========================================================================
# Непроменет ценовник
# ==========================================================================
async def test_unchanged_pricelist_is_not_written_twice(db_session) -> None:
    result = _result([_row("НЕСКАФЕ 100ГР")], content_hash="ист-хеш")

    first = await _ingest(db_session, result)
    assert first.status is RunStatus.SUCCESS

    second = await _ingest(db_session, result)
    assert second.status is RunStatus.UNCHANGED

    rows = await db_session.scalar(select(func.count()).select_from(PriceRow))
    assert rows == 1


async def test_changed_pricelist_is_written_again(db_session) -> None:
    await _ingest(db_session, _result([_row("НЕСКАФЕ 100ГР")], content_hash="хеш-1"))
    run = await _ingest(
        db_session,
        _result(
            [_row("НЕСКАФЕ 100ГР", discount_price=Decimal("179"))],
            content_hash="хеш-2",
        ),
    )
    assert run.status is RunStatus.SUCCESS

    rows = await db_session.scalar(select(func.count()).select_from(PriceRow))
    assert rows == 2


async def test_every_attempt_leaves_a_run_record(db_session) -> None:
    await _ingest(db_session, _result([_row("НЕСКАФЕ 100ГР")], content_hash="a"))
    await _ingest(db_session, error=SourceUnavailable("404"))

    runs = await db_session.scalar(select(func.count()).select_from(PricelistRun))
    assert runs == 2


# ==========================================================================
# Процент кога изворот не го дава
# ==========================================================================
async def test_discount_pct_is_derived_when_missing(db_session) -> None:
    await _ingest(
        db_session,
        _result(
            [
                _row(
                    "НЕСКАФЕ 100ГР",
                    regular_price=Decimal("200"),
                    discount_price=Decimal("150"),
                    discount_pct=None,
                )
            ]
        ),
    )

    row = await db_session.scalar(select(PriceRow))
    assert row.discount_pct == Decimal("25.00")


async def test_source_percent_is_kept_as_given(db_session) -> None:
    await _ingest(
        db_session,
        _result([_row("НЕСКАФЕ 100ГР", discount_pct=Decimal("23.73"))]),
    )
    row = await db_session.scalar(select(PriceRow))
    assert row.discount_pct == Decimal("23.73")


async def test_store_details_are_refreshed_on_rename(db_session) -> None:
    await _ingest(db_session, _result([_row("НЕСКАФЕ 100ГР")], content_hash="a"))

    renamed = StoreRef(
        external_id="89",
        name="ВЕРО 01 АЕРОДРОМ",
        city="Аеродром",
        address="нова адреса",
        source_url=_STORE.source_url,
    )
    chain = await ingest.ensure_chain(db_session, _FakeReader)
    store = await ingest.ensure_store(db_session, chain, renamed)
    await db_session.flush()

    assert store.name == "ВЕРО 01 АЕРОДРОМ"
    assert store.address == "нова адреса"
    stores = await db_session.scalar(select(func.count()).select_from(Store))
    assert stores == 1


def test_today_local_uses_skopje_timezone() -> None:
    # Дневниот клуч е по Europe/Skopje, не по UTC.
    today = ingest.today_local()
    assert isinstance(today, date)
    assert abs((today - datetime.now(UTC).date()).days) <= 1


async def test_empty_pricelist_is_not_a_failure(db_session) -> None:
    """Изворот одговори, но нема што да објави - не е наша грешка.

    Кипер во Штип и Зајас враќа recordsTotal=0 секој ден. Со статус FAILED
    состојбата би изгледала алармантно без причина.
    """
    from app.readers.base import EmptyPricelist

    run = await _ingest(db_session, error=EmptyPricelist("нема редови"))
    assert run.status is RunStatus.EMPTY
    assert run.status is not RunStatus.FAILED
    assert run.error_message
