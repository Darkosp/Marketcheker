"""Упитите за приказ на денешните попусти."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.catalog import default_grouper
from app.catalog.groups import GROUPS, SUBCATEGORIES
from app.readers.base import RawPriceRow, ReaderResult, StoreRef
from app.services import ingest
from app.services.discounts import (
    DiscountFilter,
    SortBy,
    available_cities,
    available_stores,
    count_discounts,
    counts_by_group,
    group_discounts,
    latest_run_date,
    list_discounts,
)

pytestmark = pytest.mark.db

RUN_DATE = date(2026, 10, 2)


class _Reader:
    chain_code = "vero"
    chain_name = "Веро"
    website = "https://pricelist.vero.com.mk/"


class _Reader2(_Reader):
    chain_code = "ramstore"
    chain_name = "Рамстор"
    website = "https://ramstore.com.mk/"


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
    }
    defaults.update(kwargs)
    return RawPriceRow(name=name, **defaults)


async def _seed(session, reader_class, ref: StoreRef, rows: list[RawPriceRow]) -> None:
    chain = await ingest.ensure_chain(session, reader_class)
    categories = await ingest.ensure_categories(session, GROUPS, SUBCATEGORIES)
    store = await ingest.ensure_store(session, chain, ref)
    run = await ingest.start_run(session, chain, store, run_date=RUN_DATE)
    result = ReaderResult(
        store=ref,
        rows=rows,
        source_url=ref.source_url or "https://example.invalid/",
        content_hash=None,
    )
    await ingest.save_result(
        session, run, store, result, grouper=default_grouper(), categories=categories
    )
    await session.flush()


SKOPJE_STORE = StoreRef(
    external_id="89", name="ВЕРО 1", city="Аеродром", source_url="https://x/89_1.html"
)
TETOVO_STORE = StoreRef(
    external_id="94", name="ВЕРО 3", city="Тетово", source_url="https://x/94_1.html"
)
RAMSTORE_SKOPJE = StoreRef(
    external_id="ramstore-vardar", name="РАМСТОРЕ ВАРДАР", city="Скопје"
)


@pytest.fixture
async def seeded(db_session):
    await _seed(
        db_session,
        _Reader,
        SKOPJE_STORE,
        [
            _row("НЕСКАФЕ КЛАСИК 100ГР", discount_pct=Decimal("20")),
            _row(
                "МАСЛО МАСЛИНОВО 0.75Л",
                description="МАСЛА ЗА ЈАДЕЊЕ - МАСЛИНОВО",
                regular_price=Decimal("700"),
                discount_price=Decimal("450"),
                discount_pct=Decimal("35"),
            ),
            _row(
                "ШАМПОН ЗА КОСА 400МЛ",
                description="КОЗМЕТИКА ЗА ГЛАВА - ШАМПОНИ",
                discount_price=Decimal("120"),
                discount_pct=Decimal("10"),
                promo_type_raw="ЛОЈАЛНОСТ",
            ),
            # Ред без попуст - не смее да влезе во приказот.
            _row("ЧАЈ ЛИПА", discount_price=None, discount_pct=None),
        ],
    )
    await _seed(
        db_session,
        _Reader2,
        RAMSTORE_SKOPJE,
        [
            _row(
                "ТАЈМ АУТ ЛЕШНИК 50 ГР",
                description="АПЕТИСАНИ",
                regular_price=Decimal("99"),
                discount_price=Decimal("40"),
                discount_pct=Decimal("59"),
                valid_from=RUN_DATE,
                valid_to=RUN_DATE,
            )
        ],
    )
    await _seed(
        db_session,
        _Reader,
        TETOVO_STORE,
        [_row("НЕСКАФЕ ГОЛД 200ГР", discount_pct=Decimal("15"))],
    )
    return db_session


# ==========================================================================
# Што се прикажува
# ==========================================================================
async def test_only_discounts_are_listed(seeded) -> None:
    rows = await list_discounts(seeded, DiscountFilter(run_date=RUN_DATE))
    names = {row.product_name for row in rows}
    assert "ЧАЈ ЛИПА" not in names
    assert len(rows) == 5


async def test_count_matches_list(seeded) -> None:
    filters = DiscountFilter(run_date=RUN_DATE)
    assert await count_discounts(seeded, filters) == 5


async def test_other_day_shows_nothing(seeded) -> None:
    rows = await list_discounts(seeded, DiscountFilter(run_date=date(2026, 9, 1)))
    assert rows == []


async def test_product_name_is_shown_exactly_as_in_pricelist(seeded) -> None:
    rows = await list_discounts(seeded, DiscountFilter(run_date=RUN_DATE))
    assert "НЕСКАФЕ КЛАСИК 100ГР" in {row.product_name for row in rows}


# ==========================================================================
# Филтри
# ==========================================================================
async def test_filter_by_city(seeded) -> None:
    rows = await list_discounts(
        seeded, DiscountFilter(run_date=RUN_DATE, city_slug="tetovo")
    )
    assert [row.product_name for row in rows] == ["НЕСКАФЕ ГОЛД 200ГР"]


async def test_skopje_covers_both_chains(seeded) -> None:
    """Веро пишува општина, Рамстор пишува Скопје - мора да се спојат."""
    rows = await list_discounts(
        seeded, DiscountFilter(run_date=RUN_DATE, city_slug="skopje")
    )
    chains = {row.chain_name for row in rows}
    assert chains == {"Веро", "Рамстор"}


async def test_filter_by_group(seeded) -> None:
    rows = await list_discounts(
        seeded, DiscountFilter(run_date=RUN_DATE, group_slug="kozmetika")
    )
    assert [row.product_name for row in rows] == ["ШАМПОН ЗА КОСА 400МЛ"]


async def test_loyalty_can_be_excluded(seeded) -> None:
    rows = await list_discounts(
        seeded, DiscountFilter(run_date=RUN_DATE, include_loyalty=False)
    )
    assert "ШАМПОН ЗА КОСА 400МЛ" not in {row.product_name for row in rows}


async def test_loyalty_is_flagged_for_display(seeded) -> None:
    rows = await list_discounts(seeded, DiscountFilter(run_date=RUN_DATE))
    shampoo = next(r for r in rows if r.product_name == "ШАМПОН ЗА КОСА 400МЛ")
    assert shampoo.is_loyalty_only is True


async def test_single_day_filter(seeded) -> None:
    rows = await list_discounts(
        seeded, DiscountFilter(run_date=RUN_DATE, only_single_day=True)
    )
    assert [row.product_name for row in rows] == ["ТАЈМ АУТ ЛЕШНИК 50 ГР"]


async def test_filter_by_store(seeded) -> None:
    stores = await available_stores(seeded, RUN_DATE)
    tetovo = next(sid for sid, _, name, _ in stores if name == "ВЕРО 3")
    rows = await list_discounts(
        seeded, DiscountFilter(run_date=RUN_DATE, store_ids=[tetovo])
    )
    assert len(rows) == 1


# ==========================================================================
# Подредување
# ==========================================================================
async def test_sort_by_discount_percent(seeded) -> None:
    rows = await list_discounts(
        seeded, DiscountFilter(run_date=RUN_DATE, sort_by=SortBy.DISCOUNT_PCT)
    )
    assert rows[0].product_name == "ТАЈМ АУТ ЛЕШНИК 50 ГР"  # 59%


async def test_sort_by_price(seeded) -> None:
    rows = await list_discounts(
        seeded, DiscountFilter(run_date=RUN_DATE, sort_by=SortBy.PRICE_ASC)
    )
    assert rows[0].discount_price == Decimal("40.00")


async def test_sort_by_unit_price_answers_where_is_it_cheapest(seeded) -> None:
    """Споредба по кг/л, за производи со различна грамажа.

    Подредувањето е по единица, па по цена - во рамки на иста единица
    редоследот мора да расте.
    """
    rows = await list_discounts(
        seeded, DiscountFilter(run_date=RUN_DATE, sort_by=SortBy.UNIT_PRICE)
    )
    per_unit: dict[str, list] = {}
    for row in rows:
        if row.unit_price_base is not None and row.base_unit:
            per_unit.setdefault(row.base_unit, []).append(row.unit_price_base)
    assert per_unit
    for unit, prices in per_unit.items():
        assert prices == sorted(prices), unit

    # Маслиново: 450 ден за 0.75 л = 600 ден/л
    olive = next(r for r in rows if r.product_name == "МАСЛО МАСЛИНОВО 0.75Л")
    assert olive.unit_price_base == Decimal("600")
    assert olive.unit_price_label == "600 ден/л"


async def test_rows_without_unit_price_go_last_not_first(seeded) -> None:
    rows = await list_discounts(
        seeded, DiscountFilter(run_date=RUN_DATE, sort_by=SortBy.UNIT_PRICE)
    )
    missing = [i for i, row in enumerate(rows) if row.unit_price_base is None]
    present = [i for i, row in enumerate(rows) if row.unit_price_base is not None]
    assert not present or not missing or min(missing) > max(present)


# ==========================================================================
# Групирање и менија
# ==========================================================================
async def test_group_discounts_returns_sections(seeded) -> None:
    groups = await group_discounts(seeded, DiscountFilter(run_date=RUN_DATE))
    names = {group.slug: group.count for group in groups}
    assert names["kozmetika"] == 1
    assert names["hrana"] >= 1


async def test_groups_come_in_display_order(seeded) -> None:
    groups = await group_discounts(seeded, DiscountFilter(run_date=RUN_DATE))
    slugs = [group.slug for group in groups]
    # Храна е прва по sort_order.
    assert slugs[0] == "hrana"


async def test_counts_by_group(seeded) -> None:
    rows = await counts_by_group(seeded, DiscountFilter(run_date=RUN_DATE))
    counts = {slug: count for slug, _, count in rows}
    assert counts["kozmetika"] == 1


async def test_available_cities(seeded) -> None:
    cities = {slug: count for slug, _, count in await available_cities(seeded, RUN_DATE)}
    assert cities["skopje"] == 2  # Веро 1 и Рамсторе Вардар
    assert cities["tetovo"] == 1


async def test_available_stores(seeded) -> None:
    stores = await available_stores(seeded, RUN_DATE)
    assert len(stores) == 3
    assert all(count > 0 for _, _, _, count in stores)


async def test_available_stores_filtered_by_city(seeded) -> None:
    stores = await available_stores(seeded, RUN_DATE, city_slug="tetovo")
    assert len(stores) == 1


async def test_latest_run_date(seeded) -> None:
    assert await latest_run_date(seeded) == RUN_DATE


async def test_unit_price_sort_does_not_mix_units(seeded) -> None:
    """6 ден/м и 800 ден/кг не се споредливи.

    Регресија: без групирање по единица, хартијата за печење (ден/м)
    испаѓаше пред храната (ден/кг) во „најевтино по кг/л".
    """
    rows = await list_discounts(
        seeded, DiscountFilter(run_date=RUN_DATE, sort_by=SortBy.UNIT_PRICE)
    )
    units = [row.base_unit for row in rows if row.base_unit]
    # Истата единица мора да биде во еден непрекинат блок.
    assert units == sorted(units)


# ==========================================================================
# Спојување на ист производ низ продавници
# ==========================================================================
async def test_same_product_same_price_is_one_row(db_session) -> None:
    """Ист попуст во повеќе продавници се прикажува еднаш.

    Без ова списокот е преполн со повторување: попуст на Рамстор важи во
    сите 36 продавници, па корисникот ја гледа истата картичка 36 пати.
    """
    for external_id, name in (("1", "ВЕРО 1"), ("2", "ВЕРО 2"), ("3", "ВЕРО 3")):
        await _seed(
            db_session,
            _Reader,
            StoreRef(external_id=external_id, name=name, city="Скопје"),
            [_row("НЕСКАФЕ КЛАСИК 100ГР", discount_price=Decimal("189"))],
        )

    rows = await list_discounts(db_session, DiscountFilter(run_date=RUN_DATE))
    assert len(rows) == 1
    assert rows[0].store_count == 3
    assert "3 продавници" in rows[0].where_label


async def test_same_product_different_price_stays_separate(db_session) -> None:
    """Различна цена НЕ се спојува - инаку би измислиле цена што ја нема."""
    await _seed(
        db_session,
        _Reader,
        StoreRef(external_id="1", name="ВЕРО 1", city="Скопје"),
        [_row("НЕСКАФЕ КЛАСИК 100ГР", discount_price=Decimal("189"))],
    )
    await _seed(
        db_session,
        _Reader,
        StoreRef(external_id="2", name="ВЕРО 2", city="Тетово"),
        [_row("НЕСКАФЕ КЛАСИК 100ГР", discount_price=Decimal("199"))],
    )

    rows = await list_discounts(db_session, DiscountFilter(run_date=RUN_DATE))
    assert len(rows) == 2
    assert {row.discount_price for row in rows} == {Decimal("189"), Decimal("199")}
    assert all(row.store_count == 1 for row in rows)


async def test_count_matches_merged_rows(db_session) -> None:
    """Бројот во заглавието мора да е ист како бројот на картички."""
    for external_id in ("1", "2", "3", "4"):
        await _seed(
            db_session,
            _Reader,
            StoreRef(external_id=external_id, name=f"ВЕРО {external_id}", city="Скопје"),
            [_row("НЕСКАФЕ КЛАСИК 100ГР", discount_price=Decimal("189"))],
        )

    filters = DiscountFilter(run_date=RUN_DATE)
    assert await count_discounts(db_session, filters) == 1
    assert len(await list_discounts(db_session, filters)) == 1


async def test_single_store_shows_its_name(db_session) -> None:
    await _seed(
        db_session,
        _Reader,
        StoreRef(external_id="1", name="ВЕРО 1", city="Скопје"),
        [_row("НЕСКАФЕ КЛАСИК 100ГР")],
    )
    row = (await list_discounts(db_session, DiscountFilter(run_date=RUN_DATE)))[0]
    assert row.where_label == "Веро · ВЕРО 1"
    assert row.city_name == "Скопје"
