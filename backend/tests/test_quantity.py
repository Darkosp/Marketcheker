"""Читање на грамажа од називот.

Сите називи тука се вистински, од ценовниците на Веро и Рамстор.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.catalog import parse_quantity, unit_price
from app.models.enums import BaseUnit


@pytest.mark.parametrize(
    ("name", "value", "unit", "base_quantity", "base_unit"),
    [
        # Литри
        (
            "МАСЛО МАСЛИНОВО АЛЕКСАНДРОС ПОМАС 0.75Л",
            Decimal("0.75"),
            "Л",
            Decimal("0.75"),
            BaseUnit.LITER,
        ),
        (
            "БРИЛИЈАНТ СОНЧОГЛЕДОВО МАСЛО 5 Л",
            Decimal("5"),
            "Л",
            Decimal("5"),
            BaseUnit.LITER,
        ),
        # Милилитри -> литри
        (
            "Р СОНЧОГЛЕДОВО МАСЛО 905МЛ",
            Decimal("905"),
            "МЛ",
            Decimal("0.905"),
            BaseUnit.LITER,
        ),
        (
            "НИВЕА МАСЛО ЗА СОНЧАЊЕ СПРЕЈ Ф6 200 МЛ",
            Decimal("200"),
            "МЛ",
            Decimal("0.2"),
            BaseUnit.LITER,
        ),
        # Грамови -> килограми
        ("НЕСКАФЕ КЛАСИК 100ГР", Decimal("100"), "ГР", Decimal("0.1"), BaseUnit.KG),
        ("ЕЏЕ ЕСПАЊОЛА МАСЛИНКИ 500Г", Decimal("500"), "Г", Decimal("0.5"), BaseUnit.KG),
        (
            "КАРНЕКС ЈЕТРЕНА ПАШТЕТА 50 G",
            Decimal("50"),
            "G",
            Decimal("0.05"),
            BaseUnit.KG,
        ),
        # Килограми
        ("РИСО СКОТИ ОРИЗ ПАРБОИЛД 1 КГ", Decimal("1"), "КГ", Decimal("1"), BaseUnit.KG),
        # Запирка како децимална
        (
            "КИНДЕР КАНТРИ СО МЛЕКО И ЖИТАРИЦИ 23,5ГР",
            Decimal("23.5"),
            "ГР",
            Decimal("0.0235"),
            BaseUnit.KG,
        ),
        # Центилитри
        (
            "ХЕЛИОС САД ЗА МАСЛО 25 ЦЛ",
            Decimal("25"),
            "ЦЛ",
            Decimal("0.25"),
            BaseUnit.LITER,
        ),
    ],
)
def test_parse_quantity_real_names(
    name: str,
    value: Decimal,
    unit: str,
    base_quantity: Decimal,
    base_unit: BaseUnit,
) -> None:
    result = parse_quantity(name)
    assert result is not None
    assert result.value == value
    assert result.unit == unit
    assert result.base_quantity == base_quantity
    assert result.base_unit is base_unit


# ---- множители ------------------------------------------------------------
def test_multiplier_gives_total_quantity() -> None:
    """„10Х15ГР" значи 150 г - толку добива купувачот за цената."""
    result = parse_quantity("КАФЕ НЕСКАФЕ 3 ВО 1 КРЕМИ ЛАТЕ 10Х15ГР")
    assert result is not None
    assert result.value == Decimal("150")
    assert result.base_quantity == Decimal("0.15")
    assert result.pack_count == 10


def test_multiplier_with_latin_x() -> None:
    # Изворите мешаат кирилично Х и латинично X во истиот ценовник.
    result = parse_quantity("ЗОТТ САХНЕ МЛЕКО ЗА КАФЕ 10%ММ 10X10Г")
    assert result is not None
    assert result.value == Decimal("100")
    assert result.pack_count == 10


def test_multiplier_beats_last_simple_match() -> None:
    # Без проверка на множител, „5Х75Г" би дало 75 г наместо 375 г.
    result = parse_quantity("НУДЛИ ИНДОМИ ГОВЕДСКО 5Х75Г 8/1")
    assert result is not None
    assert result.value == Decimal("375")


# ---- што НЕ е грамажа -----------------------------------------------------
def test_no_quantity_returns_none() -> None:
    assert parse_quantity("БАТЕРИЈА ЕНЕРЏАЈЗЕР ЕВРИДЕЈ АЛК. АА 4+2") is None


def test_pack_notation_is_not_quantity() -> None:
    # „20/1" и „1/100" се начин на пакување, не грамажа.
    assert parse_quantity("ЧЕТКА ЗА ЗАБИ ЛАКАЛУТ АКТИВ 1/1") is None


def test_dimensions_are_not_quantity() -> None:
    # „33Х33" се сантиметри, нема единица по себе.
    assert parse_quantity("САЛФЕТИ КАМЕЛИЈА 1СЛ. 33Х33 1/100") is None


def test_empty_name() -> None:
    assert parse_quantity("") is None
    assert parse_quantity(None) is None


def test_zero_quantity_is_rejected() -> None:
    assert parse_quantity("НЕШТО 0ГР") is None


def test_technical_numbers_do_not_become_quantity() -> None:
    """Последното совпаѓање се зема, за да техничките броеви не победат."""
    # „75ВВ", „Е27", „60В" не се единици; „3/1" не е грамажа.
    assert parse_quantity("СИЈАЛИЦА ФИЛИПС ЛЕД 75ВВ Е27 60В А60 3/1 Е27 БЕЛА") is None


def test_last_match_wins() -> None:
    # Називот има два броја со единица; последниот е грамажата на пакувањето.
    result = parse_quantity("СОК 1Л ПАКУВАЊЕ 250МЛ")
    assert result is not None
    assert result.value == Decimal("250")


# ---- помошна цена за споредба --------------------------------------------
def test_unit_price_enables_comparison() -> None:
    """0.75 л за 450 ден наспроти 1 л за 560 ден - кое е поевтино?"""
    a = unit_price(Decimal("450"), parse_quantity("МАСЛИНОВО МАСЛО 0.75Л"))
    b = unit_price(Decimal("560"), parse_quantity("МАСЛИНОВО МАСЛО 1Л"))
    assert a is not None and b is not None
    assert a == Decimal("600")
    assert b == Decimal("560")
    assert b < a  # литарското е поевтино по литар


def test_unit_price_per_kilogram() -> None:
    assert unit_price(Decimal("189"), parse_quantity("НЕСКАФЕ 100ГР")) == Decimal("1890")


def test_unit_price_without_quantity_is_none() -> None:
    # Без грамажа нема споредба - не претпоставуваме.
    assert unit_price(Decimal("189"), None) is None


def test_unit_price_without_price_is_none() -> None:
    assert unit_price(None, parse_quantity("НЕСКАФЕ 100ГР")) is None
