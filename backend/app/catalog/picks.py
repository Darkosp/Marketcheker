"""Што следи корисникот - едно нешто во неговата листа.

Корисникот размислува во нивоа: кафе → инстант кафе → Нескафе → 200 г. Но
тоа НЕ се четири различни механизми. Второто ниво е категорија од
каталогот; третото и четвртото се збор што мора да го има во називот, онака
како што е напишан во ценовникот.

    Pick(category="kafe")                        сето кафе
    Pick(category="kafe", terms=("инстант",))    инстант кафе
    Pick(category="kafe", terms=("нескафе",))    само Нескафе
    Pick(category="kafe", terms=("нескафе", "200"))   Нескафе 200 г
    Pick(terms=("нескафе",))                     Нескафе каде и да е

Еден механизам наместо три нивоа значи дека „кафе инстант нескафе" и
пишувањето директно се истата работа, и дека ново ниво не бара ни миграција
ни повторно категоризирање на 80 илјади производи.

Зборовите се бараат ТОЧНО како што се напишани во ценовникот, на кирилица.
Називите не содржат ниту една латинична марка (проверено: 0 од 80.414 за
„NESCAFE", 201 за „НЕСКАФЕ"), па погодувањето латиница-кирилица би било
измислување - затоа страницата ги ПРЕДЛАГА зборовите од вистинските називи
наместо корисникот да погодува.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.catalog.groups import PARENT_OF, category_names

# Повеќе од ова не стеснува, само се натрупува URL.
MAX_TERMS = 4
MAX_TERM_LENGTH = 40
MIN_TERM_LENGTH = 2

# Раздвојување во записот: „kafe~нескафе~200".
TERM_MARK = "~"

# Знаците на `LIKE` во корисничкиот текст не смеат да значат нешто: напишано
# „100%" би фатило сè.
_LIKE_ESCAPES = str.maketrans({"\\": r"\\", "%": r"\%", "_": r"\_"})

_NAMES = category_names()


def clean_term(value: str) -> str | None:
    """Еден збор за барање, или `None` ако не чини за ништо.

    Премногу кратко е бескорисно („а" фаќа сè), премногу долго е веројатно
    залепен цел назив.
    """
    term = " ".join(value.split()).strip().lower()
    if not MIN_TERM_LENGTH <= len(term) <= MAX_TERM_LENGTH:
        return None
    return term


def like_pattern(term: str) -> str:
    """Терминот како образец за `ILIKE`.

    Знаците на `LIKE` во корисничкиот текст не смеат да значат нешто -
    напишано „100%" би фатило сè. Се бегаат тука, еднаш, наместо на секое
    место каде се гради упит.
    """
    return f"%{term.translate(_LIKE_ESCAPES)}%"


@dataclass(frozen=True, slots=True)
class Pick:
    """Едно нешто што корисникот го следи."""

    category: str | None = None
    terms: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        """Записот во URL-то и во колачето."""
        return TERM_MARK.join((self.category or "", *self.terms))

    @property
    def label(self) -> str:
        """Како се чита на екран: „Кафе · нескафе"."""
        parts = []
        if self.category:
            parts.append(_NAMES.get(self.category, self.category))
        parts.extend(self.terms)
        return " · ".join(parts)

    @property
    def is_whole_category(self) -> bool:
        """Цела категорија, без стеснување - таква може да проголта други."""
        return bool(self.category) and not self.terms

    def covers(self, other: Pick) -> bool:
        """Дали овој избор го прави другиот излишен.

        „Пијалоци" ги носи и „Кафе", и „Кафе · нескафе". Обратно не важи, и
        избор без категорија не го покрива ништо - тој бара низ сè.
        """
        if self is other or not self.is_whole_category or not other.category:
            return False
        return self.category in (other.category, PARENT_OF.get(other.category))


def parse_pick(raw: str, known: frozenset[str]) -> Pick | None:
    """„kafe~нескафе" -> Pick. Непознатото паѓа, не руши.

    Категоријата мора да е позната; збор без ознака `~` НЕ се претвора во
    барање - инаку згрешен слуг тивко би станал филтер по назив и
    страницата би изгледала празна без причина.
    """
    category_part, mark, term_part = raw.partition(TERM_MARK)
    category = category_part.strip().lower() or None
    if category and category not in known:
        return None

    terms: list[str] = []
    if mark:
        for piece in term_part.split(TERM_MARK):
            term = clean_term(piece)
            if term and term not in terms:
                terms.append(term)

    if not category and not terms:
        return None
    return Pick(category=category, terms=tuple(terms[:MAX_TERMS]))


__all__ = [
    "MAX_TERMS",
    "MAX_TERM_LENGTH",
    "MIN_TERM_LENGTH",
    "TERM_MARK",
    "Pick",
    "clean_term",
    "like_pattern",
    "parse_pick",
]
