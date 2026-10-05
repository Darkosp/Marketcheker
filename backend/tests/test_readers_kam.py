"""Читачот за КАМ, врз зачувани примероци од 2026-10-02.

PDF примерокот е првите 3 страници од вистински ценовник (157 страници).
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from app.readers.base import StructureChanged
from app.readers.kam import parse_pdf, parse_shop_list, split_discount_cell
from tests.conftest import load_fixture, load_fixture_bytes


@pytest.fixture(scope="module")
def shops() -> list:
    return json.loads(load_fixture("kam_shops.json"))


@pytest.fixture(scope="module")
def pdf_bytes() -> bytes:
    return load_fixture_bytes("kam_pricelist.pdf")


@pytest.fixture(scope="module")
def parsed(pdf_bytes: bytes):
    return parse_pdf(pdf_bytes, source="тест")


# ---- листа продавници -----------------------------------------------------
def test_reads_shops(shops: list) -> None:
    stores = parse_shop_list(shops)
    assert len(stores) == 8


def test_shop_has_id_name_city_address(shops: list) -> None:
    store = parse_shop_list(shops)[0]
    assert store.external_id == "73"
    assert store.name == "Радовиш"
    assert store.city == "Радовиш"
    assert store.address


def test_pricelist_url_is_built_from_relative_path(shops: list) -> None:
    # „2026/10/02/73.pdf" -> https://kam.mk/2026/10/02/73.pdf
    store = parse_shop_list(shops)[0]
    assert store.source_url == "https://kam.mk/2026/10/02/73.pdf"
    assert store.extra["relative_path"] == "2026/10/02/73.pdf"


def test_shop_without_files_has_no_url() -> None:
    stores = parse_shop_list([{"Id": 1, "Name": "Тест", "ShopFiles": []}])
    assert stores[0].source_url is None


def test_city_falls_back_to_municipality() -> None:
    stores = parse_shop_list(
        [{"Id": 2, "Name": "Тест", "City": None, "Municipality": "Гази Баба"}]
    )
    assert stores[0].city == "Гази Баба"


def test_non_list_payload_is_structure_changed() -> None:
    with pytest.raises(StructureChanged, match="список"):
        parse_shop_list("не е список")


def test_wrapped_payload_is_unwrapped() -> None:
    # Ако некогаш го завиткаат во објект, списокот се наоѓа внатре.
    stores = parse_shop_list({"Data": [{"Id": 5, "Name": "Тест"}]})
    assert stores[0].external_id == "5"


# ---- ќелија со попуст -----------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "price", "pct"),
    [
        ("110ден.\nПопуст: 15%", Decimal("110"), Decimal("15")),
        ("59ден.\nПопуст: 49%", Decimal("59"), Decimal("49")),
        ("107ден.\nПопуст: 14%", Decimal("107"), Decimal("14")),
        # Без процент - целата ќелија е цена.
        ("110ден.", Decimal("110"), None),
        ("", None, None),
    ],
)
def test_split_discount_cell(raw: str, price, pct) -> None:
    """Цената и процентот се во ИСТА ќелија кај КАМ."""
    assert split_discount_cell(raw) == (price, pct)


# ---- PDF ------------------------------------------------------------------
def test_reads_rows_from_pdf(parsed) -> None:
    rows, _, skipped, _ = parsed
    assert len(rows) > 20
    assert skipped == 0


def test_reads_pricelist_date_from_header(parsed) -> None:
    # „Датум и време на последно ажурирање на цените: 02.10.2026 5:49:36AM"
    _, pricelist_date, _, _ = parsed
    assert pricelist_date == date(2026, 10, 2)


def test_prices_have_den_suffix_stripped(parsed) -> None:
    # Изворот пишува „23ден."
    rows, _, _, _ = parsed
    priced = [row for row in rows if row.sale_price is not None]
    assert priced
    assert all(isinstance(row.sale_price, Decimal) for row in priced)


def test_discount_rows_have_price_and_percent(parsed) -> None:
    rows, _, _, _ = parsed
    discounts = [row for row in rows if row.is_discount]
    assert discounts
    for row in discounts:
        assert row.discount_price is not None
        assert row.discount_pct is not None


def test_discount_row_has_duration(parsed) -> None:
    # „28.09.2026 до 04.10.2026"
    rows, _, _, _ = parsed
    row = next(r for r in rows if r.is_discount and r.valid_from)
    assert row.valid_to is not None
    assert row.valid_from <= row.valid_to


def test_unit_price_is_parsed(parsed) -> None:
    # „100 гр = 9.20 ден."
    rows, _, _, _ = parsed
    with_unit = [row for row in rows if row.unit_price is not None]
    assert with_unit
    assert any(row.unit_price_label for row in with_unit)


def test_description_and_availability_are_read(parsed) -> None:
    rows, _, _, _ = parsed
    assert any(row.description for row in rows)
    assert any(row.availability for row in rows)


def test_header_rows_are_not_products(parsed) -> None:
    rows, _, _, _ = parsed
    names = {row.name for row in rows}
    assert not any("Назив на" in name for name in names)


def test_pdf_without_expected_header_raises() -> None:
    # Празен PDF без табела - форматот се сменил или датотеката е друга.
    import io

    import pdfplumber  # noqa: F401  (се користи од parse_pdf)

    minimal = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
        b"trailer<</Root 1 0 R>>"
    )
    with pytest.raises((StructureChanged, Exception)):
        parse_pdf(io.BytesIO(minimal).getvalue(), source="тест")
