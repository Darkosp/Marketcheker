"""Што си одбрал корисникот: кои производи ги следи и во кој град.

Изборот живее на две места:

- **во URL-то** (`?izbor=kafe&izbor=masla`), за да линкот може да се подели
  и страницата да се освежи без да се изгуби изборот;
- **во колаче**, за да следното отворање го памети.

URL-то има предност. Колачето е само памтење: кога барањето носи избор,
колачето се пишува; кога носи празен избор, се брише. Разликата меѓу
„барањето не се изјасни" (нема `izbor` воопшто) и „барањето рече ништо"
(`izbor=` празно) е суштинска - без неа „види ги сите" не може да се
направи, зашто колачето веднаш би го вратило стариот избор.

Нема најава: колачето носи само тоа што корисникот одбрал да следи -
категории од каталогот и зборови што сам ги напишал. Ниту едно лично
податоче, и нема потреба од согласност за следење.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import quote, unquote

from app.catalog.groups import GROUPS, SUBCATEGORIES, category_slugs
from app.catalog.picks import Pick, parse_pick

COOKIE_NAME = "izbor"
CITY_COOKIE = "grad"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # една година

# Слуг на град: букви, бројки и цртички. Служи само како заштита од ѓубре
# во колачето - кои градови навистина постојат се знае дури во упитот.
_CITY = re.compile(r"^[a-z0-9-]{1,64}$")

# Разделникот во колачето е ЗНАК НА ВИКАЊЕ. Запирката е резервирана во
# заглавието `Set-Cookie`: Starlette го наводничува и го бега целото
# („masla,kafe"), прелистувачот го враќа така, и памтењето тивко
# откажува. Точката изгледаше како решение, но `quote` никогаш не ја бега,
# па бренд како „dr.oetker" би го скршил записот на два. Знакот на викање го
# бега, значи никогаш не се појавува внатре во еден избор.
SEPARATOR = "!"
_SPLIT = re.compile(r"!")

# Горна граница на бројот избори. Повеќе од ова не стеснува ништо - само го
# натрупува URL-то и колачето.
MAX_SELECTED = 30

# Колачето има тврда граница околу 4 KB во прелистувачите, а кирилицата по
# процентно кодирање зафаќа шест бајти по буква. Подолгото се отсекува, за
# да превеликиот избор изгуби дел наместо целото колаче да биде одбиено.
MAX_COOKIE_BYTES = 3500

_KNOWN: frozenset[str] = frozenset(category_slugs())

# Редоследот е оној од каталогот, не оној од URL-то: ист избор секогаш дава
# ист запис, па колачето и линкот не се менуваат без причина. Групата оди
# пред своите под-категории, зашто второто место е 0.
_ORDER: dict[str, tuple[int, int]] = {slug: (order, 0) for slug, _, order in GROUPS}
_ORDER.update(
    {
        slug: (_ORDER[parent][0], order)
        for slug, _, parent, order in SUBCATEGORIES
        if parent in _ORDER
    }
)


def normalise(values: Iterable[str]) -> list[Pick]:
    """Чиста, подредена листа без излишни членови.

    Три работи:

    - непрепознатливото паѓа (URL-то може да дојде рачно напишано);
    - избор што друг веќе го покрива се вади - „Пијалоци" и „Кафе · нескафе"
      заедно значат само „Пијалоци", а краткиот запис е и појасен на екран;
    - редоследот е од каталогот, не од барањето, па ист избор секогаш дава
      ист запис и колачето не се менува без причина.
    """
    picks: list[Pick] = []
    for value in values:
        pick = parse_pick(value, _KNOWN)
        if pick is not None and pick not in picks:
            picks.append(pick)

    picks = [
        pick for pick in picks if not any(other.covers(pick) for other in picks)
    ]
    picks.sort(key=_rank)
    return picks[:MAX_SELECTED]


def _rank(pick: Pick) -> tuple[int, int, int, str]:
    """Редослед: по каталогот, а во иста категорија прво поширокото."""
    group, inside = _ORDER.get(pick.category or "", (999, 999))
    return (group, inside, len(pick.terms), pick.key)


def from_query(values: list[str] | None) -> list[Pick] | None:
    """Изборот од URL-то. `None` значи дека барањето не се изјаснило.

    `?izbor=` (празна вредност) НЕ е исто со отсутен `izbor`: првото значи
    „сите производи", второто „важи она што се памети".
    """
    if values is None:
        return None
    return normalise(values)


def from_cookie(raw: str | None) -> list[Pick]:
    """Изборот од колачето. Расипано колаче е празен избор, не грешка."""
    if not raw:
        return []
    return normalise(unquote(part) for part in _SPLIT.split(raw.strip('"')))


def to_cookie(picks: Iterable[Pick]) -> str:
    """Записот за колачето.

    Секој избор се кодира одделно, зашто брендовите се на кирилица а
    колачето прима само ASCII. Долгиот избор се отсекува на граница на цел
    избор - подобро отколку прелистувачот да го одбие целото колаче.
    """
    written: list[str] = []
    length = 0
    for pick in picks:
        piece = quote(pick.key, safe="")
        length += len(piece) + 1
        if length > MAX_COOKIE_BYTES:
            break
        written.append(piece)
    return SEPARATOR.join(written)


def choose(
    query_values: list[str] | None, remembered: Iterable[str]
) -> tuple[list[Pick], bool]:
    """(избор, дали барањето го одреди).

    Запаметеното доаѓа или од сметката, или од колачето - на ова место не е
    важно од каде. Второто кажува дали треба да се запише.
    """
    from_url = from_query(query_values)
    if from_url is not None:
        return from_url, True
    return normalise(remembered), False


def resolve(
    query_values: list[str] | None, cookie_raw: str | None
) -> tuple[list[Pick], bool]:
    """Како `choose`, но запаметеното доаѓа од колаче."""
    from_url = from_query(query_values)
    if from_url is not None:
        return from_url, True
    return from_cookie(cookie_raw), False


def resolve_city(
    requested: str | None, cookie_raw: str | None, known: Iterable[str]
) -> tuple[str, bool]:
    """(град, дали барањето го одреди) - истото правило како кај производите.

    Со една разлика: град од URL-то се применува каков што е, а град од
    колачето само ако денес навистина постои во списокот.

    Зошто разликата: списокот нуди само градови што имаат попусти ДЕНЕС. Ако
    запаметениот град денес го нема, паѓачкото мени би покажувало „сите
    градови" додека филтерот тивко би филтрирал по него - страница без
    резултати и без објаснување. Напишан рачно во URL-то, пак, е изречно
    барање и се почитува, па дури и да не даде ништо.
    """
    if requested is not None:
        return (requested if _CITY.match(requested) else ""), True

    remembered = cookie_raw or ""
    if remembered and _CITY.match(remembered) and remembered in set(known):
        return remembered, False
    return "", False


def labels(picks: Iterable[Pick]) -> list[str]:
    """Имињата за приказ, за да празната страница каже што било проверено."""
    return [pick.label for pick in picks]


__all__ = [
    "CITY_COOKIE",
    "COOKIE_MAX_AGE",
    "COOKIE_NAME",
    "MAX_COOKIE_BYTES",
    "MAX_SELECTED",
    "SEPARATOR",
    "choose",
    "from_cookie",
    "from_query",
    "labels",
    "normalise",
    "resolve",
    "resolve_city",
    "to_cookie",
]
