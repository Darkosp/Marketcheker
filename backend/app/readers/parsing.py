"""Споделени помошни функции за парсирање на ценовници.

Тука живее сè што е заедничко меѓу изворите: нормализација на текст,
читање на цени и датуми, и мапирање на колони по ИМЕ (не по позиција -
Веро и Рамстор ги имаат истите колони во различен ред).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from app.readers.base import StructureChanged


class ValueParseError(ValueError):
    """Една ќелија не може да се прочита.

    Не ја прекинува целата страница: повикувачот го брои редот како
    прескокнат. Ако премногу редови паднат, читачот фрла StructureChanged.
    """


class Column(StrEnum):
    """Канонски колони, независно од тоа како ги вика изворот."""

    NAME = "name"
    SALE_PRICE = "sale_price"
    UNIT_PRICE = "unit_price"
    AVAILABILITY = "availability"
    DESCRIPTION = "description"
    REGULAR_PRICE = "regular_price"
    DISCOUNT_PRICE = "discount_price"
    DISCOUNT_PCT = "discount_pct"
    POINTS = "points"
    PROMO_TYPE = "promo_type"
    DURATION = "duration"


# --------------------------------------------------------------------------
# Текст
# --------------------------------------------------------------------------
_WHITESPACE = re.compile(r"\s+")
_PARENS = re.compile(r"\([^)]*\)")


def normalize_space(value: str | None) -> str:
    """Собира сите видови празни места во едно и крати од краевите.

    Ги чисти и NBSP и BOM, кои ги има во изворните HTML-и.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFC", value)
    text = text.replace(" ", " ").replace("﻿", "")
    return _WHITESPACE.sub(" ", text).strip()


def normalize_header(value: str | None) -> str:
    """Клуч за препознавање на заглавие на колона.

    Веро пишува "Продажна цена(со ДДВ)", Рамстор "ПРОДАЖНА ЦЕНА"; по
    отстранување на заградите и великите букви, двете се исти.
    """
    text = normalize_space(value).upper()
    text = _PARENS.sub(" ", text)
    text = text.replace(":", " ")
    return _WHITESPACE.sub(" ", text).strip()


def header_key(value: str | None) -> str:
    """Клуч за совпаѓање на заглавие: како normalize_header, но без празни места.

    Изворите ставаат <br> во заглавијата ("Достапност во<br>продажен објект"),
    а parserот не вметнува празно место на негово место - излегува
    "ВОПРОДАЖЕН". Споредбата без празни места го прави мапирањето неосетливо
    на тоа каде изворот ќе прекине ред.
    """
    return normalize_header(value).replace(" ", "")


def normalize_for_match(value: str | None) -> str:
    """Нормализација за споредба на текст (клучни зборови, fingerprint)."""
    return normalize_space(value).lower()


def fingerprint(name: str, description: str | None = None) -> str:
    """Стабилен клуч за производ во рамки на еден синџир.

    Истиот назив и опис секогаш даваат ист клуч, па дневните читања не
    создаваат дупликати. Грамажата е дел од називот, значи и од клучот -
    Nescafe 100 g и 200 g даваат различни клучеви.
    """
    base = f"{normalize_for_match(name)}|{normalize_for_match(description)}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]


# --------------------------------------------------------------------------
# Броеви
# --------------------------------------------------------------------------
# Дозволуваме: 59 | 45.00 | 10150.00 | 1 019 | 1.234,56 (европски запис)
_NUMBER = re.compile(r"\d[\d\s.,]*")


def parse_decimal(raw: str | None) -> Decimal | None:
    """Чита број од ќелија со цена. Празна ќелија -> None.

    Фрла ValueParseError кога има текст што личи на број но не е читлив -
    подобро прескокнат ред со запис во лог, отколку тивко погрешна цена.
    """
    text = normalize_space(raw)
    if not text:
        return None

    match = _NUMBER.search(text)
    if match is None:
        raise ValueParseError(f"нема број во {raw!r}")

    number = match.group(0).strip().rstrip(".,").replace(" ", "")
    has_dot = "." in number
    has_comma = "," in number

    if has_dot and has_comma:
        # Европски запис: последниот раздвојувач е децималниот.
        if number.rindex(",") > number.rindex("."):
            number = number.replace(".", "").replace(",", ".")
        else:
            number = number.replace(",", "")
    elif has_comma:
        # Само запирка: децимална ако зад неа има 1-2 цифри (45,5 / 45,50),
        # инаку е раздвојувач на илјадарки (1,234).
        tail = number.rsplit(",", 1)[1]
        number = number.replace(",", "." if len(tail) <= 2 else "")
    elif has_dot and number.count(".") > 1:
        # Повеќе точки значи илјадарки: 1.234.567
        number = number.replace(".", "")

    try:
        return Decimal(number)
    except InvalidOperation as exc:
        raise ValueParseError(f"не можам да прочитам број од {raw!r}") from exc


def parse_percent(raw: str | None) -> Decimal | None:
    """Чита процент: "22 %", "23.73%", "-23,73 %" -> Decimal.

    Знакот се игнорира: попустот се чува како позитивен број.
    """
    value = parse_decimal(raw)
    if value is None:
        return None
    return abs(value)


# --------------------------------------------------------------------------
# Датуми
# --------------------------------------------------------------------------
# Сите извори пишуваат ден-прво.
_DATE_FORMATS = ("%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%y", "%d.%m.%y")
_DATE_IN_TEXT = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b")


def _date_from_parts(day: str | int, month: str | int, year: str | int, raw: str) -> date:
    year_int = int(year)
    if year_int < 100:
        year_int += 2000
    try:
        return date(year_int, int(month), int(day))
    except ValueError as exc:
        raise ValueParseError(f"невозможен датум во {raw!r}") from exc


def parse_date(raw: str | None) -> date | None:
    """Чита датум од ќелија. Празна ќелија -> None."""
    text = normalize_space(raw)
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # Датум вграден во подолг текст ("Последно ажурирање: 1/11/2025 8:34")
    match = _DATE_IN_TEXT.search(text)
    if match:
        return _date_from_parts(*match.groups(), raw=text)
    raise ValueParseError(f"непознат формат на датум: {raw!r}")


def parse_date_range(raw: str | None) -> tuple[date | None, date | None]:
    """Чита времетраење: "27/10/2025 - 03/11/2025" или "01.10.2026 - 21.10.2026".

    Ако има само еден датум, се враќа како почеток и крај - тоа е
    еднодневен попуст.
    """
    text = normalize_space(raw)
    if not text:
        return None, None

    found = _DATE_IN_TEXT.findall(text)
    if not found:
        raise ValueParseError(f"нема датум во времетраење {raw!r}")

    dates = [_date_from_parts(*parts, raw=text) for parts in found[:2]]
    if len(dates) == 1:
        return dates[0], dates[0]
    return dates[0], dates[1]


# --------------------------------------------------------------------------
# Единечна цена
# --------------------------------------------------------------------------
# Веро:    "219 ден/кг", "1099 ден/пакување"
# Рамстор: "100 ГР: =16.11ДЕН", "1 ПАРЧЕ =2.25ДЕН"
_UNIT_LABEL_SLASH = re.compile(r"ден\s*/\s*([^\s,;]+)", re.IGNORECASE)
_UNIT_LABEL_PREFIX = re.compile(r"^\s*([\d.,]*\s*[^\s:=]+)\s*:?\s*=")


def parse_unit_price(raw: str | None) -> tuple[Decimal | None, str | None]:
    """Враќа (цена, ознака на единица) од колоната "единечна цена".

    Ознаката се чува како текст на изворот; сведувањето во kg/l се прави
    подоцна, кога се знае грамажата на производот.
    """
    text = normalize_space(raw)
    if not text:
        return None, None

    slash = _UNIT_LABEL_SLASH.search(text)
    if slash:
        # "219 ден/кг" -> цената е пред "ден", ознаката по "/"
        return parse_decimal(text[: slash.start()]), slash.group(1).strip().lower()

    prefix = _UNIT_LABEL_PREFIX.match(text)
    if prefix:
        # "100 ГР: =16.11ДЕН" -> ознака пред "=", цена по "="
        label = normalize_space(prefix.group(1)).lower()
        return parse_decimal(text[prefix.end() :]), label

    # Само број, без ознака.
    return parse_decimal(text), None


# --------------------------------------------------------------------------
# Колони
# --------------------------------------------------------------------------
def build_column_map(
    headers: Sequence[str],
    spec: Mapping[str, Column],
    required: Iterable[Column],
    *,
    source: str,
) -> dict[Column, int]:
    """Мапира канонска колона -> индекс, по ИМЕ на заглавието.

    Фрла StructureChanged ако задолжителна колона исчезне - тоа е знак
    дека изворот сменил формат и читачот треба да се поправи.
    """
    # Клучевите на spec-от се пишуваат читливо, со празни места; споредбата
    # се прави без нив (види header_key).
    spec_by_key = {header_key(name): column for name, column in spec.items()}

    mapping: dict[Column, int] = {}
    seen: list[str] = []

    for index, raw_header in enumerate(headers):
        seen.append(normalize_header(raw_header))
        column = spec_by_key.get(header_key(raw_header))
        if column is not None and column not in mapping:
            mapping[column] = index

    missing = [column.value for column in required if column not in mapping]
    if missing:
        raise StructureChanged(
            f"{source}: ги нема задолжителните колони {missing}; заглавието е {seen}"
        )
    return mapping


def cell(row: Sequence[str], mapping: Mapping[Column, int], column: Column) -> str:
    """Безопасно чита ќелија: колона што ја нема или краток ред -> празно."""
    index = mapping.get(column)
    if index is None or index >= len(row):
        return ""
    return normalize_space(row[index])
