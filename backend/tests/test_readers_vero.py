"""Читачот за Веро, врз зачувани примероци од 2026-10-02."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.enums import PromoType
from app.readers.base import StructureChanged
from app.readers.promo import map_promo_type
from app.readers.vero import parse_pricelist_page, parse_store_index
from tests.conftest import load_fixture


@pytest.fixture(scope="module")
def index_html() -> str:
    return load_fixture("vero_index.html")


@pytest.fixture(scope="module")
def page1_html() -> str:
    return load_fixture("vero_store_page1.html")


@pytest.fixture(scope="module")
def page2_html() -> str:
    return load_fixture("vero_store_page2.html")


# ---- листа продавници -----------------------------------------------------
def test_finds_all_stores(index_html: str) -> None:
    # 14 ВЕРО + 3 ЏАМБО = 17, вклучувајќи ги двата записа со расипан HTML.
    assert len(parse_store_index(index_html)) == 17


def test_store_ids_are_unique(index_html: str) -> None:
    # Расипаниот HTML прави дупли <a> јазли за истиот href.
    ids = [store.external_id for store in parse_store_index(index_html)]
    assert len(ids) == len(set(ids))


def test_malformed_entries_get_their_name(index_html: str) -> None:
    # "<a href=...><H1>ЏАМБО 2</a></H1>" - затворачките тагови се наопаку.
    stores = {s.external_id: s for s in parse_store_index(index_html)}
    assert stores["119"].name == "ЏАМБО 2"
    assert stores["168"].name == "ЏАМБО 3"


def test_store_has_address_and_source_url(index_html: str) -> None:
    stores = {s.external_id: s for s in parse_store_index(index_html)}
    first = stores["89"]
    assert first.name == "ВЕРО 1"
    assert first.address == "Бул. Јане Сандански бр.111 – Аеродром"
    assert first.source_url == "https://pricelist.vero.com.mk/89_1.html"


def test_city_is_taken_from_address_tail(index_html: str) -> None:
    stores = {s.external_id: s for s in parse_store_index(index_html)}
    # Веро пишува општина за скопските продавници, град за останатите.
    assert stores["89"].city == "Аеродром"
    assert stores["94"].city == "Тетово"
    assert stores["97"].city == "Битола"
    assert stores["164"].city == "Куманово"


def test_empty_index_finds_nothing() -> None:
    assert parse_store_index("<html><body>нема ништо</body></html>") == []


# ---- страница со ценовник -------------------------------------------------
def test_reads_rows(page1_html: str) -> None:
    page = parse_pricelist_page(page1_html, source="https://x/89_1.html")
    assert len(page.rows) == 40
    assert page.skipped == 0


def test_reads_pricelist_date(page1_html: str) -> None:
    # "Последно ажурирање: 2/10/2026 7:02"
    page = parse_pricelist_page(page1_html, source="https://x/89_1.html")
    assert page.pricelist_date == date(2026, 10, 2)


def test_first_row_is_read_correctly(page1_html: str) -> None:
    row = parse_pricelist_page(page1_html, source="https://x/89_1.html").rows[0]
    assert row.name == "УЗО 12 1Л"
    assert row.description == "АЛКОХОЛНИ ПИЈАЛОЦИ - АПЕРИТИВИ"
    assert row.sale_price == Decimal("889")
    assert row.regular_price == Decimal("995")
    assert row.discount_price == Decimal("889")
    assert row.discount_pct == Decimal("10")
    assert row.unit_price == Decimal("889")
    assert row.unit_price_label == "парче"
    assert row.promo_type_raw == "Акциска цена"
    assert row.availability == "Да"
    assert row.valid_from == date(2026, 10, 1)
    assert row.valid_to == date(2026, 10, 7)


def test_promo_wording_change_is_still_recognised(page1_html: str) -> None:
    """Веро го смени текстот од "Промотивна цена" (2025) во "Акциска цена".

    Мапирањето е по делче од зборот, не по точно совпаѓање, затоа промената
    не го расипа читачот.
    """
    row = parse_pricelist_page(page1_html, source="https://x/89_1.html").rows[0]
    assert map_promo_type(row.promo_type_raw, has_discount=True) is PromoType.DISCOUNT


def test_description_is_group_and_subgroup(page1_html: str) -> None:
    # Колоната "опис" е "ГРУПА - ПОДГРУПА"; влез за категоризацијата.
    page = parse_pricelist_page(page1_html, source="https://x/89_1.html")
    assert all(" - " in (row.description or "") for row in page.rows)


def test_name_is_kept_exactly_as_in_source(page1_html: str) -> None:
    # Производот се прикажува точно како во ценовникот, со грамажа.
    page = parse_pricelist_page(page1_html, source="https://x/89_1.html")
    assert all(row.name == row.name.strip() for row in page.rows)
    assert any(any(ch.isdigit() for ch in row.name) for row in page.rows)


def test_reader_does_not_assume_only_discounts(page1_html: str) -> None:
    """Сите редови во овој примерок имаат попуст, но тоа не е правило.

    Во октомври 2026 Веро објавува цел асортиман: ~1.200 попусти од ~10.100
    редови, со попустите групирани на првите страници. Читачот ги враќа сите
    редови и дозволува и едните и другите - одлуката што е попуст се прави
    по RawPriceRow.is_discount, не по страница.
    """
    page = parse_pricelist_page(page1_html, source="https://x/89_1.html")
    assert all(row.is_discount for row in page.rows)
    # Ред без цена со попуст е валиден ред, само не е попуст.
    html = page1_html.replace(
        '<td style="text-align:right;color:red;">889</td>', "<td></td>", 1
    )
    changed = parse_pricelist_page(html, source="https://x/89_1.html")
    assert changed.skipped == 0
    assert not changed.rows[0].is_discount


# ---- пагинација -----------------------------------------------------------
def test_page1_points_to_page2(page1_html: str) -> None:
    page = parse_pricelist_page(page1_html, source="https://x/89_1.html")
    assert page.next_href == "89_2.html"


def test_middle_page_goes_forward_not_back(page2_html: str) -> None:
    """Страница 2 има и стрелка назад и напред - мора да оди напред.

    Регресија: ќелијата со пагинација почнува со стрелката за назад, а не
    со зборот "Страна", па читањето на тековната страница од текстот даваше
    1 и читачот се вртеше назад на 89_2 - 1.500 наместо 10.100 редови.
    """
    page = parse_pricelist_page(page2_html, source="https://x/89_2.html")
    assert page.next_href == "89_3.html"


def test_current_page_is_read_from_url(page2_html: str) -> None:
    # Истиот HTML, но кажуваме дека сме на 89_3 - нема линк понапред.
    page = parse_pricelist_page(page2_html, source="https://x/89_3.html")
    assert page.next_href is None


def test_current_page_falls_back_to_page_label(page2_html: str) -> None:
    # Без број во URL-то, бројот се чита од текстот "Страна 2".
    page = parse_pricelist_page(page2_html, source="без-број")
    assert page.next_href == "89_3.html"


def test_last_page_has_no_next(page2_html: str) -> None:
    last = page2_html.replace('href="89_3.html"', 'href="89_2.html"')
    page = parse_pricelist_page(last, source="https://x/89_9.html")
    assert page.next_href is None


# ---- грешки ---------------------------------------------------------------
def test_missing_table_raises_structure_changed() -> None:
    with pytest.raises(StructureChanged, match="табела"):
        parse_pricelist_page("<html><body><p>нема табела</p></body></html>", source="т")


def test_renamed_column_raises_structure_changed() -> None:
    # Ако Веро ја преименува колоната со назив, читачот мора да јави.
    html = """
    <table><tr><th>Нешто друго</th><th>Продажна цена</th></tr>
    <tr><td>Нескафе</td><td>59</td></tr></table>
    """
    with pytest.raises(StructureChanged, match="name"):
        parse_pricelist_page(html, source="тест")


def test_table_without_header_raises_structure_changed() -> None:
    html = "<table><tr><td>Нескафе</td><td>59</td></tr></table>"
    with pytest.raises(StructureChanged):
        parse_pricelist_page(html, source="тест")


def test_unreadable_row_is_skipped_not_silently_dropped() -> None:
    # Еден нечитлив ред се брои и се предупредува, но не ја руши страницата.
    html = """
    <table>
      <tr><th>Назив на стока</th><th>Продажна цена</th><th>Опис на стока</th>
          <th>Редовна цена</th><th>Цена со попуст</th>
          <th>Времетраење на промоција или попуст</th></tr>
      <tr><td>ДОБАР РЕД</td><td>59</td><td>А - Б</td><td>76</td><td>59</td>
          <td>01/10/2026 - 07/10/2026</td></tr>
      <tr><td>ЛОШ РЕД</td><td>59</td><td>А - Б</td><td>76</td><td>59</td>
          <td>некогаш наскоро</td></tr>
    </table>
    """
    page = parse_pricelist_page(html, source="тест")
    assert len(page.rows) == 1
    assert page.skipped == 1
    assert page.warnings
