"""Платформата proverkanaceni.mk, врз зачувани примероци од 2026-10-02.

Еден читач покрива три синџира: Жито Лукс, Стокомак и Тамаро. Истата
платформа ја користеше и Тинекс - оттаму параметрите org/search/perPage.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.readers.proverkanaceni import (
    PAGE_SIZE,
    StokomakReader,
    TamaroReader,
    ZitoReader,
    parse_page,
    parse_store_select,
)
from tests.conftest import load_fixture

BASE = "https://zito.proverkanaceni.mk/"


@pytest.fixture(scope="module")
def stores_html() -> str:
    return load_fixture("proverkanaceni_stores.html")


@pytest.fixture(scope="module")
def page_html() -> str:
    return load_fixture("proverkanaceni_page.html")


# ---- листа продавници -----------------------------------------------------
def test_reads_stores_from_select(stores_html: str) -> None:
    stores = parse_store_select(stores_html, base_url=BASE)
    assert len(stores) == 94


def test_store_ids_are_unique(stores_html: str) -> None:
    ids = [store.external_id for store in parse_store_select(stores_html, base_url=BASE)]
    assert len(ids) == len(set(ids))


def test_store_url_carries_the_org_parameter(stores_html: str) -> None:
    store = parse_store_select(stores_html, base_url=BASE)[0]
    assert store.source_url == f"{BASE}?org={store.external_id}"


@pytest.mark.parametrize(
    ("name_part", "city"),
    [
        # Жито го пишува градот по цртичка...
        ("Трговски - Велес", "Велес"),
        ("Пазар - Кочани", "Кочани"),
        ("Бутел1 - Скопје", "Скопје"),
        # ...или како дел од името.
        ("Струмица 4", "Струмица"),
    ],
)
def test_city_is_found_in_the_store_name(
    stores_html: str, name_part: str, city: str
) -> None:
    stores = parse_store_select(stores_html, base_url=BASE)
    store = next(s for s in stores if name_part in s.name)
    assert store.city == city


def test_unknown_city_stays_empty(stores_html: str) -> None:
    """Име без град не се погодува."""
    stores = parse_store_select(stores_html, base_url=BASE)
    nameless = [s for s in stores if s.city is None]
    assert nameless  # „10 Жито Ване Кат" и слични
    assert all("-" not in s.name.rsplit(" ", 1)[-1] for s in nameless[:1])


def test_default_city_is_used_when_the_name_has_none(stores_html: str) -> None:
    """Тамаро работи само во охридско и не го пишува градот."""
    stores = parse_store_select(stores_html, base_url=BASE, default_city="Охрид")
    assert all(store.city for store in stores)


def test_empty_select_finds_nothing() -> None:
    assert parse_store_select("<html><body>нема</body></html>", base_url=BASE) == []


# ---- страница со цени -----------------------------------------------------
def test_reads_a_full_page(page_html: str) -> None:
    rows, skipped, _, had_header = parse_page(page_html, source="тест")
    assert len(rows) == PAGE_SIZE
    assert skipped == 0
    assert had_header is True


def test_rows_without_discount_have_fewer_cells(page_html: str) -> None:
    """Платформата не испишува празни ќелии за колоните со попуст.

    Регресија: читачот бараше точно 9 ќелии, па редовите без попуст - кои
    имаат само 6 - се прескокнуваа. Со 4.086 од 4.856 нечитливи редови,
    заштитата правилно фрлаше StructureChanged.
    """
    rows, skipped, _, _ = parse_page(page_html, source="тест")
    plain = [row for row in rows if not row.is_discount]
    assert plain
    assert skipped == 0
    assert all(row.sale_price is not None for row in plain)


def test_discount_row_is_read(page_html: str) -> None:
    rows, _, _, _ = parse_page(page_html, source="тест")
    discounts = [row for row in rows if row.is_discount]
    assert discounts

    row = discounts[0]
    assert row.regular_price is not None
    assert row.discount_price is not None
    assert row.discount_price < row.regular_price
    # Цената и процентот се во ИСТА ќелија: „39 ден.Попуст:29%"
    assert row.discount_pct is not None
    assert 0 < row.discount_pct < 100


def test_duration_is_read(page_html: str) -> None:
    # „30.09.2026 до13.10.2026" - без празно место околу „до".
    rows, _, _, _ = parse_page(page_html, source="тест")
    row = next(r for r in rows if r.is_discount and r.valid_from)
    assert isinstance(row.valid_from, date)
    assert row.valid_to is not None
    assert row.valid_from <= row.valid_to


def test_prices_have_den_suffix_stripped(page_html: str) -> None:
    rows, _, _, _ = parse_page(page_html, source="тест")
    priced = [row for row in rows if row.sale_price is not None]
    assert priced
    assert all(isinstance(row.sale_price, Decimal) for row in priced)


def test_description_is_read(page_html: str) -> None:
    rows, _, _, _ = parse_page(page_html, source="тест")
    assert any(row.description for row in rows)


def test_page_without_table_is_the_end_not_an_error(page_html: str) -> None:
    """Празна страница значи дека пагинацијата заврши."""
    rows, skipped, _, had_header = parse_page(
        "<html><body><p>нема табела</p></body></html>", source="тест"
    )
    assert rows == []
    assert skipped == 0
    assert had_header is False


# ---- синџирите ------------------------------------------------------------
def test_three_chains_share_one_reader() -> None:
    assert ZitoReader.subdomain == "zito"
    assert StokomakReader.subdomain == "stokomak"
    assert TamaroReader.subdomain == "tamaro"


def test_base_url_is_built_from_the_subdomain() -> None:
    assert ZitoReader().base_url == "https://zito.proverkanaceni.mk/"
    assert TamaroReader().base_url == "https://tamaro.proverkanaceni.mk/"


def test_only_tamaro_has_a_default_city() -> None:
    """Тамаро работи во еден град; другите два се низ цела Македонија."""
    assert TamaroReader.default_city == "Охрид"
    assert ZitoReader.default_city is None
    assert StokomakReader.default_city is None


def test_concurrency_stays_polite() -> None:
    """Трите синџира се на ИСТ домаќин.

    2 по синџир x 3 синџира = 6 истовремени врски кон еден сервер - онолку
    колку што отвора и обичен прелистувач.
    """
    for reader in (ZitoReader, StokomakReader, TamaroReader):
        assert reader.store_concurrency == 2


def test_no_data_marker_is_not_a_skipped_row() -> None:
    """Платформата пишува „Нема податоци за прикажување" во празна страница.

    Тоа е крај на пагинацијата, не нечитлив ред. Избројано како
    прескокнато, табелата за квалитет на читањето би лажела.
    """
    html = """
    <table>
      <tr><th>Назив на стока-производ</th><th>Продажна цена</th>
          <th>Единечна цена</th><th>Редовна цена</th></tr>
      <tr><td>Нема податоци за прикажување</td></tr>
    </table>
    """
    rows, skipped, warnings, _ = parse_page(html, source="тест")
    assert rows == []
    assert skipped == 0
    assert warnings == []


def test_a_store_may_genuinely_have_one_product() -> None:
    """Жито во „3 Струмица 4" објавува еден единствен производ.

    Читачот застанува бидејќи страницата е пократка од бараната, не
    поради грешка.
    """
    html = """
    <table>
      <tr><th>Назив на стока-производ</th><th>Продажна цена</th>
          <th>Единечна цена</th><th>Опис на стока</th>
          <th>Достапност во продажен објект</th><th>Редовна цена</th></tr>
      <tr><td>ФИТ ЦИГАРИ ВИОЛА</td><td>140 ден.</td>
          <td>1par = 140.00 ден.</td><td>Електронски цигари</td>
          <td>Да</td><td>140 ден.</td></tr>
    </table>
    """
    rows, skipped, _, had_header = parse_page(html, source="тест")
    assert len(rows) == 1
    assert skipped == 0
    assert had_header is True
