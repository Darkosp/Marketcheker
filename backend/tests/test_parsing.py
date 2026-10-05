"""Споделените парсери за вредности.

Форматите тука се вистински, препишани од ценовниците на Веро и Рамстор.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.readers.base import StructureChanged
from app.readers.parsing import (
    Column,
    ValueParseError,
    build_column_map,
    cell,
    fingerprint,
    normalize_header,
    normalize_space,
    parse_date,
    parse_date_range,
    parse_decimal,
    parse_percent,
    parse_unit_price,
)


# ---- текст ----------------------------------------------------------------
def test_normalize_space_collapses_and_strips() -> None:
    assert normalize_space("  Nescafe   Classic \n 100 g  ") == "Nescafe Classic 100 g"


def test_normalize_space_removes_nbsp_and_bom() -> None:
    assert normalize_space("﻿Нескафе 100") == "Нескафе 100"


def test_normalize_space_handles_none() -> None:
    assert normalize_space(None) == ""


def test_normalize_header_drops_parentheses() -> None:
    # Веро пишува "Продажна цена(со ДДВ)", Рамстор "ПРОДАЖНА ЦЕНА".
    assert normalize_header("Продажна цена(со ДДВ)") == normalize_header("ПРОДАЖНА ЦЕНА")


def test_normalize_header_drops_colons() -> None:
    assert normalize_header("Опис на стока:") == "ОПИС НА СТОКА"


# ---- fingerprint ----------------------------------------------------------
def test_fingerprint_is_stable() -> None:
    assert fingerprint("НЕСКАФЕ 100ГР", "КАФЕ") == fingerprint(" нескафе  100гр ", "кафе")


def test_fingerprint_separates_different_grammage() -> None:
    # Главното правило: 100 g и 200 g се различни производи.
    assert fingerprint("НЕСКАФЕ КЛАСИК 100ГР") != fingerprint("НЕСКАФЕ КЛАСИК 200ГР")


def test_fingerprint_separates_different_description() -> None:
    assert fingerprint("МАСЛО 1Л", "МАСЛИНОВО") != fingerprint("МАСЛО 1Л", "СОНЧОГЛЕД")


# ---- броеви ---------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("59", Decimal("59")),  # Веро: цели броеви
        ("45.00", Decimal("45.00")),  # Рамстор
        ("10150.00", Decimal("10150.00")),  # без раздвојувач за илјадарки
        ("1 019", Decimal("1019")),  # празно место како раздвојувач
        ("145,50", Decimal("145.50")),  # запирка како децимална
        ("1.234,56", Decimal("1234.56")),  # европски запис
        ("1,234", Decimal("1234")),  # запирка како илјадарки
        ("1.234.567", Decimal("1234567")),  # точки како илјадарки
        ("59 ден", Decimal("59")),
    ],
)
def test_parse_decimal_formats(raw: str, expected: Decimal) -> None:
    assert parse_decimal(raw) == expected


def test_parse_decimal_empty_is_none() -> None:
    assert parse_decimal("") is None
    assert parse_decimal("   ") is None
    assert parse_decimal(None) is None


def test_parse_decimal_rejects_text() -> None:
    # Подобро прескокнат ред отколку тивко погрешна цена.
    with pytest.raises(ValueParseError):
        parse_decimal("нема цена")


def test_parse_percent_strips_sign_and_symbol() -> None:
    assert parse_percent("22 %") == Decimal("22")
    assert parse_percent("23.73%") == Decimal("23.73")
    assert parse_percent("-23,73 %") == Decimal("23.73")


# ---- датуми ---------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("27/10/2025", date(2025, 10, 27)),  # Веро
        ("01.10.2026", date(2026, 10, 1)),  # Рамстор
        ("1/11/2025 8:34", date(2025, 11, 1)),  # Веро, со време
        ("Последно ажурирање: 1/11/2025 8:34", date(2025, 11, 1)),
        ("02.10.2026 4:00AM", date(2026, 10, 2)),  # Рамстор, со време
    ],
)
def test_parse_date_formats(raw: str, expected: date) -> None:
    assert parse_date(raw) == expected


def test_parse_date_empty_is_none() -> None:
    assert parse_date("") is None


def test_parse_date_rejects_unknown_format() -> None:
    with pytest.raises(ValueParseError):
        parse_date("октомври 2026")


def test_parse_date_rejects_impossible_date() -> None:
    with pytest.raises(ValueParseError):
        parse_date("32/13/2026")


def test_parse_date_range_vero_format() -> None:
    assert parse_date_range("27/10/2025 - 03/11/2025") == (
        date(2025, 10, 27),
        date(2025, 11, 3),
    )


def test_parse_date_range_ramstore_format_with_newlines() -> None:
    raw = "01.10.2026  \n        - \n        21.10.2026"
    assert parse_date_range(raw) == (date(2026, 10, 1), date(2026, 10, 21))


def test_parse_date_range_single_date_is_single_day() -> None:
    # Еднодневен попуст: датум од == датум до.
    assert parse_date_range("02.10.2026") == (date(2026, 10, 2), date(2026, 10, 2))


def test_parse_date_range_empty() -> None:
    assert parse_date_range("") == (None, None)


# ---- единечна цена --------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "value", "label"),
    [
        ("219 ден/кг", Decimal("219"), "кг"),  # Веро
        ("899 ден/л", Decimal("899"), "л"),
        ("1099 ден/пакување", Decimal("1099"), "пакување"),
        ("100 ГР: =16.11ДЕН", Decimal("16.11"), "100 гр"),  # Рамстор
        ("1 ПАРЧЕ =2.25ДЕН", Decimal("2.25"), "1 парче"),
        ("100 МЛ: =10844.83ДЕН", Decimal("10844.83"), "100 мл"),
    ],
)
def test_parse_unit_price(raw: str, value: Decimal, label: str) -> None:
    assert parse_unit_price(raw) == (value, label)


def test_parse_unit_price_empty() -> None:
    assert parse_unit_price("") == (None, None)


# ---- мапирање на колони ---------------------------------------------------
SPEC = {
    "НАЗИВ НА СТОКА": Column.NAME,
    "ПРОДАЖНА ЦЕНА": Column.SALE_PRICE,
    "ОПИС НА СТОКА": Column.DESCRIPTION,
}


def test_build_column_map_by_name_not_position() -> None:
    # Истите колони во обратен ред даваат точни индекси.
    headers = ["Опис на стока", "Назив на стока", "Продажна цена(со ДДВ)"]
    mapping = build_column_map(
        headers, SPEC, [Column.NAME, Column.SALE_PRICE], source="тест"
    )
    assert mapping[Column.NAME] == 1
    assert mapping[Column.DESCRIPTION] == 0
    assert mapping[Column.SALE_PRICE] == 2


def test_build_column_map_raises_when_required_column_missing() -> None:
    # Изворот сменил формат - читачот мора да јави, не да молчи.
    with pytest.raises(StructureChanged, match="name"):
        build_column_map(["Продажна цена"], SPEC, [Column.NAME], source="тест")


def test_build_column_map_error_names_the_source() -> None:
    with pytest.raises(StructureChanged, match="vero_89_1"):
        build_column_map([], SPEC, [Column.NAME], source="vero_89_1")


def test_cell_returns_empty_for_missing_column() -> None:
    mapping = {Column.NAME: 0}
    assert cell(["Нескафе"], mapping, Column.DESCRIPTION) == ""


def test_cell_returns_empty_for_short_row() -> None:
    mapping = {Column.NAME: 0, Column.DESCRIPTION: 5}
    assert cell(["Нескафе"], mapping, Column.DESCRIPTION) == ""
