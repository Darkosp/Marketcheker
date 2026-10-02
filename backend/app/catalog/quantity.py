"""Читање на грамажа од називот на производот.

Грамажата е дел од идентитетот на производот: „НЕСКАФЕ 100ГР" и
„НЕСКАФЕ 200ГР" се два различни производи и така се прикажуваат. Но за
споредба „каде е најевтино" треба и сведена количина во основна единица
(кг/л/парче), за да може 0.75 л маслиново масло да се спореди со 1 л.

Шаблоните доаѓаат од вистински називи од Веро и Рамстор (26.854 назива):
    МАСЛО МАСЛИНОВО АЛЕКСАНДРОС ПОМАС 0.75Л   -> 0.75 л
    НЕСКАФЕ КЛАСИК 100ГР                      -> 100 г  -> 0.1 кг
    КАФЕ НЕСКАФЕ 3 ВО 1 ЛАТЕ 10Х15ГР          -> 150 г  (множител!)
    САЛФЕТИ ФРЕШ 3СЛ. 33Х33 20/1              -> нема грамажа
Околу 31% од називите воопшто немаат грамажа (батерии, сијалици, четки) -
тоа е нормално, не е грешка.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.models.enums import BaseUnit

# Единиците се пишуваат и на кирилица и на латиница, мешано во истиот ценовник.
# Вредноста е (основна единица, колку од основната е една таква единица).
_UNITS: dict[str, tuple[BaseUnit, Decimal]] = {
    "КГ": (BaseUnit.KG, Decimal(1)),
    "KG": (BaseUnit.KG, Decimal(1)),
    "ГР": (BaseUnit.KG, Decimal("0.001")),
    "Г": (BaseUnit.KG, Decimal("0.001")),
    "G": (BaseUnit.KG, Decimal("0.001")),
    "Л": (BaseUnit.LITER, Decimal(1)),
    "L": (BaseUnit.LITER, Decimal(1)),
    "МЛ": (BaseUnit.LITER, Decimal("0.001")),
    "ML": (BaseUnit.LITER, Decimal("0.001")),
    "ЦЛ": (BaseUnit.LITER, Decimal("0.01")),
    "CL": (BaseUnit.LITER, Decimal("0.01")),
    "КОМ": (BaseUnit.PIECE, Decimal(1)),
    "ПАР": (BaseUnit.PIECE, Decimal(1)),
    "ПАРЧЕ": (BaseUnit.PIECE, Decimal(1)),
    "М": (BaseUnit.METER, Decimal(1)),
    "M": (BaseUnit.METER, Decimal(1)),
}

# Најдолгите прво, за да „ПАРЧЕ" не се прочита како „ПАР".
_UNIT_PATTERN = "|".join(sorted(_UNITS, key=len, reverse=True))

_NUMBER = r"\d+(?:[.,]\d+)?"

# „10Х15ГР", „2Х75МЛ", „5X75Г" - кирилично Х и латинично X, и *.
_MULTIPLIER = re.compile(
    rf"(?<![\d.,])({_NUMBER})\s*[XХх*]\s*({_NUMBER})\s*({_UNIT_PATTERN})(?![А-ЯЁЀ-ЏA-Z])",
    re.IGNORECASE,
)

# „100ГР", „0.75Л", „5 Л"
_SIMPLE = re.compile(
    rf"(?<![\d.,])({_NUMBER})\s*({_UNIT_PATTERN})(?![А-ЯЁЀ-ЏA-Z])",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Quantity:
    """Грамажа прочитана од називот."""

    # Како пишува во називот: 100 + "ГР"
    value: Decimal
    unit: str
    # Сведено во основна единица: 0.1 + KG
    base_quantity: Decimal
    base_unit: BaseUnit
    # Колку парчиња во пакувањето, ако називот кажува („10Х15ГР" -> 10).
    pack_count: int | None = None


def parse_quantity(name: str | None) -> Quantity | None:
    """Ја чита грамажата од називот. Нема грамажа -> None.

    Кога називот има множител („10Х15ГР"), враќа вкупната количина (150 г),
    бидејќи тоа е количината што купувачот ја добива за цената.
    """
    if not name:
        return None

    multi = _last_match(_MULTIPLIER, name)
    if multi is not None:
        count = _to_decimal(multi.group(1))
        each = _to_decimal(multi.group(2))
        unit_text = multi.group(3).upper()
        if count is None or each is None or count <= 0 or each <= 0:
            return None
        total = count * each
        return _build(total, unit_text, pack_count=_as_int(count))

    simple = _last_match(_SIMPLE, name)
    if simple is None:
        return None
    value = _to_decimal(simple.group(1))
    if value is None or value <= 0:
        return None
    return _build(value, simple.group(2).upper(), pack_count=None)


def unit_price(price: Decimal | None, quantity: Quantity | None) -> Decimal | None:
    """Цена за една основна единица (ден/кг, ден/л, ден/парче).

    Ова е помошната цена за „каде е најевтино": дозволува 0.75 л да се
    спореди со 1 л. Без грамажа нема споредба - враќа None, не претпоставува.
    """
    if price is None or quantity is None or quantity.base_quantity <= 0:
        return None
    return price / quantity.base_quantity


# --------------------------------------------------------------------------
def _build(value: Decimal, unit_text: str, *, pack_count: int | None) -> Quantity | None:
    entry = _UNITS.get(unit_text)
    if entry is None:
        return None
    base_unit, factor = entry
    return Quantity(
        value=value,
        unit=unit_text,
        base_quantity=value * factor,
        base_unit=base_unit,
        pack_count=pack_count,
    )


def _last_match(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    """Последното совпаѓање: грамажата по правило стои на крајот од називот.

    „СИЈАЛИЦА ФИЛИПС ЛЕД 75ВВ Е27 60В А60 3/1" - броевите пред грамажата се
    технички податоци; земањето на последниот го намалува ризикот.
    """
    found = None
    for found in pattern.finditer(text):  # noqa: B007
        pass
    return found


def _to_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _as_int(value: Decimal) -> int | None:
    return int(value) if value == value.to_integral_value() else None
