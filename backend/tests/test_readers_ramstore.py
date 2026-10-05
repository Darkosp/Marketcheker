"""Читачот за Рамстор, врз зачувани примероци од 2026-10-02."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.enums import PromoType
from app.readers.base import StructureChanged
from app.readers.promo import map_promo_type
from app.readers.ramstore import (
    parse_pricelist,
    parse_store_index,
    split_discount_cell,
)
from tests.conftest import load_fixture


@pytest.fixture(scope="module")
def index_html() -> str:
    return load_fixture("ramstore_marketi.html")


@pytest.fixture(scope="module")
def store_html() -> str:
    return load_fixture("ramstore_store.html")


# ---- листа продавници -----------------------------------------------------
def test_finds_all_36_stores(index_html: str) -> None:
    assert len(parse_store_index(index_html)) == 36


def test_links_come_from_button_onclick(index_html: str) -> None:
    # Линковите НЕ се <a href>, туку location.href во onclick на <button>.
    stores = {s.external_id: s for s in parse_store_index(index_html)}
    assert "ramstore-vardar" in stores
    assert stores["ramstore-vardar"].source_url == (
        "https://ramstore.com.mk/marketi/ramstore-vardar/"
    )


def test_store_ids_are_unique(index_html: str) -> None:
    ids = [store.external_id for store in parse_store_index(index_html)]
    assert len(ids) == len(set(ids))


def test_store_has_name_and_address(index_html: str) -> None:
    stores = {s.external_id: s for s in parse_store_index(index_html)}
    vardar = stores["ramstore-vardar"]
    assert "ВАРДАР" in vardar.name.upper()
    assert vardar.address is not None
    assert "Кирил и Методиј" in vardar.address


def test_city_is_last_part_after_comma(index_html: str) -> None:
    stores = {s.external_id: s for s in parse_store_index(index_html)}
    assert stores["ramstore-vardar"].city == "Скопје"
    assert stores["ramstore-tetovo-3"].city == "Тетово"


def test_empty_index_finds_nothing() -> None:
    assert parse_store_index("<html><body>нема копчиња</body></html>") == []


# ---- ценовник -------------------------------------------------------------
def test_reads_rows(store_html: str) -> None:
    rows, _, skipped, _ = parse_pricelist(store_html, source="тест")
    assert len(rows) == 40
    assert skipped == 0


def test_reads_pricelist_date(store_html: str) -> None:
    # "Датум и време на последно ажурирање на цените: 02.10.2026 4:00AM"
    _, pricelist_date, _, _ = parse_pricelist(store_html, source="тест")
    assert pricelist_date == date(2026, 10, 2)


def test_pricelist_holds_whole_assortment_not_only_discounts(store_html: str) -> None:
    # За разлика од Веро, Рамстор го објавува целиот асортиман.
    rows, _, _, _ = parse_pricelist(store_html, source="тест")
    assert any(row.is_discount for row in rows)
    assert any(not row.is_discount for row in rows)


def test_row_without_discount_price_is_not_discount(store_html: str) -> None:
    rows, _, _, _ = parse_pricelist(store_html, source="тест")
    plain = next(row for row in rows if not row.is_discount)
    assert plain.discount_price is None
    assert plain.sale_price is not None


def test_discount_row_is_read_correctly(store_html: str) -> None:
    rows, _, _, _ = parse_pricelist(store_html, source="тест")
    row = next(row for row in rows if row.is_discount)
    assert row.sale_price is not None
    assert row.regular_price is not None
    assert row.discount_price is not None
    # Процентот е вграден во ќелијата со цена, не е своја колона.
    assert row.discount_pct is not None
    assert 0 < row.discount_pct < 100
    assert row.valid_from is not None
    assert row.valid_to is not None


def test_columns_are_mapped_by_name_despite_different_order(store_html: str) -> None:
    # Кај Рамстор "опис" е пред "достапност", обратно од Веро.
    rows, _, _, _ = parse_pricelist(store_html, source="тест")
    row = rows[0]
    assert row.description
    assert row.availability in {"ДА", "НЕ"}


# ---- ќелија "цена со попуст" ---------------------------------------------
def test_split_discount_cell_with_percent() -> None:
    assert split_discount_cell("45.00-23.73%") == (Decimal("45.00"), Decimal("23.73"))


def test_split_discount_cell_large_price() -> None:
    assert split_discount_cell("11350.00-18.92%") == (
        Decimal("11350.00"),
        Decimal("18.92"),
    )


def test_split_discount_cell_without_percent() -> None:
    assert split_discount_cell("45.00") == (Decimal("45.00"), None)


def test_split_discount_cell_empty() -> None:
    assert split_discount_cell("") == (None, None)


# ---- вид на акција --------------------------------------------------------
def test_akciska_prodazba_is_plain_discount() -> None:
    assert map_promo_type("АКЦИСКА ПРОДАЖБА", has_discount=True) is PromoType.DISCOUNT


def test_lojalnost_is_marked_separately() -> None:
    # Важи само со картичка - корисник без картичка не ја добива цената.
    assert map_promo_type("ЛОЈАЛНОСТ", has_discount=True) is PromoType.LOYALTY


def test_ponuda_is_discount() -> None:
    assert map_promo_type("ПОНУДА", has_discount=True) is PromoType.DISCOUNT


def test_row_without_discount_price_is_promo_none() -> None:
    # Правилото: попуст = ред со пополнета цена со попуст. Дел од редовите
    # имаат тип на акција без цена со попуст - тие не се попусти.
    assert map_promo_type("АКЦИСКА ПРОДАЖБА", has_discount=False) is PromoType.NONE


def test_unknown_promo_type_becomes_other_not_discount() -> None:
    assert map_promo_type("НЕШТО НОВО 2027", has_discount=True) is PromoType.OTHER


def test_empty_promo_type_with_discount_price_is_discount() -> None:
    assert map_promo_type("", has_discount=True) is PromoType.DISCOUNT


def test_loyalty_wins_over_discount_wording() -> None:
    assert (
        map_promo_type("АКЦИСКА ПРОДАЖБА СО КАРТИЧКА", has_discount=True)
        is PromoType.LOYALTY
    )


def test_multibuy_is_recognised() -> None:
    assert map_promo_type("1+1 ГРАТИС", has_discount=True) is PromoType.MULTIBUY


# ---- грешки ---------------------------------------------------------------
def test_missing_table_raises_structure_changed() -> None:
    with pytest.raises(StructureChanged, match="табела"):
        parse_pricelist("<html><body><p>нема табела</p></body></html>", source="т")


def test_renamed_column_raises_structure_changed() -> None:
    html = """
    <table><tr><th>НЕШТО ДРУГО</th><th>ПРОДАЖНА ЦЕНА</th></tr>
    <tr><td>НЕСКАФЕ</td><td>45.00</td></tr></table>
    """
    with pytest.raises(StructureChanged, match="name"):
        parse_pricelist(html, source="тест")
