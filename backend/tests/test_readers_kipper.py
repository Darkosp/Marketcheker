"""Читачот за Кипер, врз зачувани примероци од 2026-10-02."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from app.readers.base import StructureChanged
from app.readers.kipper import _parse_rows as parse_rows
from app.readers.kipper import (
    _unit_label,
    find_post_id,
    parse_store_index,
)
from app.readers.kipper import _unpack as unpack
from tests.conftest import load_fixture


@pytest.fixture(scope="module")
def index_html() -> str:
    return load_fixture("kipper_marketet.html")


@pytest.fixture(scope="module")
def store_html() -> str:
    return load_fixture("kipper_store.html")


@pytest.fixture(scope="module")
def products() -> dict:
    return json.loads(load_fixture("kipper_products.json"))


# ---- листа продавници -----------------------------------------------------
def test_finds_stores(index_html: str) -> None:
    stores = parse_store_index(index_html)
    assert len(stores) == 19


def test_store_ids_are_unique(index_html: str) -> None:
    ids = [store.external_id for store in parse_store_index(index_html)]
    assert len(ids) == len(set(ids))


def test_store_has_slug_and_url(index_html: str) -> None:
    stores = {s.external_id: s for s in parse_store_index(index_html)}
    assert "kipper-134-kumanove" in stores
    assert stores["kipper-134-kumanove"].source_url == (
        "https://kipper.mk/mk/kipper-134-kumanove/"
    )


@pytest.mark.parametrize(
    ("slug", "city"),
    [
        ("kipper-134-kumanove", "Куманово"),
        ("kipper-167-shtip", "Штип"),
        ("kipper-163-tetove", "Тетово"),
        # Патеките се пишани албански; местото се преведува.
        ("kipper-147-shkup-maxhari", "Скопје"),
        ("kipper-166-kisela-voda", "Скопје"),
        ("kipper-158-diber", "Дебар"),
    ],
)
def test_city_from_slug(index_html: str, slug: str, city: str) -> None:
    stores = {s.external_id: s for s in parse_store_index(index_html)}
    assert stores[slug].city == city


def test_every_store_has_a_city(index_html: str) -> None:
    missing = [s.external_id for s in parse_store_index(index_html) if not s.city]
    assert missing == []


def test_empty_index_finds_nothing() -> None:
    assert parse_store_index("<html><body>нема линкови</body></html>") == []


# ---- post_id --------------------------------------------------------------
def test_finds_post_id_in_body_class(store_html: str) -> None:
    # AJAX-от бара post_id, а тој е во класата на <body>: „postid-23018".
    assert find_post_id(store_html) == "23018"


def test_missing_post_id_returns_none() -> None:
    assert find_post_id("<html><body class='page'>нема</body></html>") is None


# ---- JSON одговор ---------------------------------------------------------
def test_unpack_returns_rows_and_total(products: dict) -> None:
    rows, total = unpack(products, source="тест")
    assert len(rows) == 40
    assert total == 40


def test_unpack_rejects_non_object() -> None:
    with pytest.raises(StructureChanged, match="објект"):
        unpack([1, 2, 3], source="тест")


def test_unpack_rejects_missing_data_field() -> None:
    with pytest.raises(StructureChanged, match="data"):
        unpack({"draw": 1, "recordsTotal": 5}, source="тест")


def test_unpack_rejects_array_rows() -> None:
    """DataTables може да враќа и низи; Кипер враќа речници."""
    with pytest.raises(StructureChanged, match="објекти"):
        unpack({"data": [["а", "б"]]}, source="тест")


# ---- редови ---------------------------------------------------------------
def test_parses_all_rows(products: dict) -> None:
    rows, skipped, _ = parse_rows(products["data"])
    assert len(rows) == 40
    assert skipped == 0


def test_discount_rows_are_recognised(products: dict) -> None:
    rows, _, _ = parse_rows(products["data"])
    assert len([row for row in rows if row.is_discount]) == 20


def test_discount_row_fields(products: dict) -> None:
    rows, _, _ = parse_rows(products["data"])
    row = next(r for r in rows if r.is_discount)
    assert row.name
    assert row.regular_price is not None
    assert row.discount_price is not None
    # Кај Кипер продажната цена Е цената со попуст.
    assert row.discount_price == row.sale_price
    assert row.discount_pct is not None
    assert row.discount_pct > 0


def test_row_without_percent_is_not_discount(products: dict) -> None:
    rows, _, _ = parse_rows(products["data"])
    plain = next(r for r in rows if not r.is_discount)
    assert plain.discount_price is None
    assert plain.sale_price is not None


def test_description_comes_from_subgroup(products: dict) -> None:
    rows, _, _ = parse_rows(products["data"])
    assert all(row.description for row in rows)


def test_negative_percent_is_stored_positive(products: dict) -> None:
    # Изворот пишува „-6%"; попустот се чува како позитивен број.
    rows, _, _ = parse_rows(products["data"])
    assert all(row.discount_pct > 0 for row in rows if row.discount_pct is not None)


def test_row_without_name_is_skipped() -> None:
    rows, skipped, warnings = parse_rows([{"product_name": "", "product_price": "10"}])
    assert rows == []
    assert skipped == 1
    assert warnings


def test_iso_promotion_dates_are_read() -> None:
    rows, _, _ = parse_rows(
        [
            {
                "product_name": "НЕШТО",
                "product_price": "49",
                "product_price_normal": "52",
                "product_price_discount_percentage": "-6%",
                "promotion_datetime_from": "2026-10-01",
                "promotion_datetime_to": "2026-10-07",
            }
        ]
    )
    assert rows[0].valid_from == date(2026, 10, 1)
    assert rows[0].valid_to == date(2026, 10, 7)


def test_missing_promotion_dates_are_none() -> None:
    rows, _, _ = parse_rows(
        [
            {
                "product_name": "НЕШТО",
                "product_price": "49",
                "product_price_discount_percentage": "-6%",
                "promotion_datetime_from": None,
                "promotion_datetime_to": None,
            }
        ]
    )
    assert rows[0].valid_from is None
    assert rows[0].valid_to is None


# ---- единечна цена --------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1КГ=", "1кг"), ("1Л=", "1л"), ("1ПАР=", "1пар"), ("=1Л", "1л"), ("", None)],
)
def test_unit_label(raw: str, expected: str | None) -> None:
    assert _unit_label(raw) == expected


def test_unit_price_value_is_read(products: dict) -> None:
    rows, _, _ = parse_rows(products["data"])
    with_unit = [row for row in rows if row.unit_price is not None]
    assert with_unit
    assert all(isinstance(row.unit_price, Decimal) for row in with_unit)
