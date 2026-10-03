"""Изборот на корисникот: кои категории ги следи.

Изборот живее на две места:

- **во URL-то** (`?izbor=kafe&izbor=masla`), за да линкот може да се подели
  и страницата да се освежи без да се изгуби изборот;
- **во колаче**, за да следното отворање го памети.

URL-то има предност. Колачето е само памтење: кога барањето носи избор,
колачето се пишува; кога носи празен избор, се брише. Разликата меѓу
„барањето не се изјасни" (нема `izbor` воопшто) и „барањето рече ништо"
(`izbor=` празно) е суштинска - без неа „види ги сите" не може да се
направи, зашто колачето веднаш би го вратило стариот избор.

Нема најава: колачето носи само слугови од каталогот, ниту едно лично
податоче, и нема потреба од согласност за следење.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.catalog.groups import (
    GROUPS,
    PARENT_OF,
    SUBCATEGORIES,
    category_names,
    category_slugs,
)

COOKIE_NAME = "izbor"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # една година

# Разделникот во колачето е ТОЧКА, не запирка: запирката е резервирана во
# заглавието `Set-Cookie`, па Starlette го наводничи и го бега целото
# („masla,kafe"). Прелистувачот го враќа така наводничено, изборот не се
# препознава и памтењето тивко откажува. Точката ја нема ниту еден слуг.
SEPARATOR = "."
_SPLIT = re.compile(r"[.,]")

# Горна граница: URL-то и колачето не смеат да растат без крај. 66 е сè што
# постои (12 групи + 54 под-категории), па ова никого не стеснува - служи
# против рачно натрупан URL.
MAX_SELECTED = 66

_KNOWN: frozenset[str] = frozenset(category_slugs())
_NAMES: dict[str, str] = category_names()

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


def normalise(values: Iterable[str]) -> list[str]:
    """Чист, подреден избор без излишни членови.

    Три работи:

    - непознатите слугови паѓаат (URL-то може да дојде рачно напишано);
    - под-категорија чиј родител е избран се вади - „Пијалоци, Кафе" е
      истото што и „Пијалоци", а краткиот запис е и појасен на екран;
    - редоследот е од каталогот, не од барањето.
    """
    chosen = {value.strip().lower() for value in values}
    chosen &= _KNOWN

    chosen -= {slug for slug in chosen if PARENT_OF.get(slug) in chosen}

    ordered = sorted(chosen, key=lambda slug: _ORDER.get(slug, (999, 999)))
    return ordered[:MAX_SELECTED]


def from_query(values: list[str] | None) -> list[str] | None:
    """Изборот од URL-то. `None` значи дека барањето не се изјаснило.

    `?izbor=` (празна вредност) НЕ е исто со отсутен `izbor`: првото значи
    „сите производи", второто „важи она што се памети".
    """
    if values is None:
        return None
    return normalise(values)


def from_cookie(raw: str | None) -> list[str]:
    """Изборот од колачето. Расипано колаче е празен избор, не грешка.

    Се прима и запирка: така изгледаа колачињата пред да се види дека
    запирката се бега.
    """
    if not raw:
        return []
    return normalise(_SPLIT.split(raw.strip('"')))


def to_cookie(slugs: Iterable[str]) -> str:
    return SEPARATOR.join(slugs)


def resolve(
    query_values: list[str] | None, cookie_raw: str | None
) -> tuple[list[str], bool]:
    """(избор, дали барањето го одреди).

    Второто кажува дали колачето треба да се препише.
    """
    from_url = from_query(query_values)
    if from_url is not None:
        return from_url, True
    return from_cookie(cookie_raw), False


def labels(slugs: Iterable[str]) -> list[str]:
    """Имињата за приказ, за да празната страница каже што било проверено."""
    return [_NAMES[slug] for slug in slugs if slug in _NAMES]


__all__ = [
    "COOKIE_MAX_AGE",
    "COOKIE_NAME",
    "MAX_SELECTED",
    "SEPARATOR",
    "from_cookie",
    "from_query",
    "labels",
    "normalise",
    "resolve",
    "to_cookie",
]
