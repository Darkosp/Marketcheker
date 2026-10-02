"""Совпаѓање на производ со група, преку клучни зборови.

Генеричен механизам: земи называт и описот од ценовникот, спореди со речник
од клучни зборови, врати најдобриот. Речникот живее во groups.py.

Две работи го прават употребливо врз вистински ценовници:

1. **Негативните зборови се проверуваат прво.** Ценовниците се полни со
   производи што содржат збор од друга група: „МАСЛО ЗА КОСА" не е храна,
   „ФИЛТЕР ЗА КАФЕ" не е пијалок, „САД ЗА МАСЛО" е дом.

2. **Описот има предност над називот.** Описот е полицата на која стои
   производот („КОЗМЕТИКА ЗА ГЛАВА", „МАСЛА ЗА ЈАДЕЊЕ") и за груби групи е
   посигурен од називот. Називот служи кога описот не кажува ништо.
   (За ситни категории би било обратно - затоа изворот се избира со `prefer`.)
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.readers.parsing import normalize_for_match

Source = Literal["name", "description"]


@dataclass(frozen=True, slots=True)
class Keyword:
    """Еден клучен збор од речникот.

    priority решава кога повеќе зборови фаќаат; поголем победува. При ист
    приоритет победува подолгиот збор, па „масло за коса" победува „масло".

    Совпаѓањето СЕКОГАШ почнува на граница на збор. Без тоа „ВИРЏИН" содржи
    „џин" и маслиновото масло станува алкохол - вистинска грешка фатена со
    тест. Крајот не е врзан, за да стебло како „играчк" фати „ИГРАЧКИ".

    whole_word го врзува и крајот, за кратки зборови каде продолжението
    значи нешто друго: „чај" не смее да фати „ЧАЈНИК".
    """

    group_slug: str
    text: str
    priority: int = 1
    is_negative: bool = False
    whole_word: bool = False

    @property
    def normalized(self) -> str:
        return normalize_for_match(self.text)

    def build_pattern(self) -> re.Pattern[str]:
        """Шаблонот се компајлира еднаш, при изградба на Grouper."""
        body = re.escape(self.normalized)
        tail = r"\b" if self.whole_word else ""
        return re.compile(rf"\b{body}{tail}")


@dataclass(frozen=True, slots=True)
class Match:
    """Исходот од совпаѓањето на еден производ."""

    group_slug: str
    # Дали групата е најдена со речникот, или е резервната „Друго".
    matched: bool
    matched_keyword: str | None = None
    matched_in: Source | None = None
    # Негативниот збор што го одвел производот подалеку од неговата група.
    excluded_by: str | None = None


@dataclass
class Grouper:
    """Речникот натоварен во меморија, подготвен за брзо совпаѓање.

    Се прави еднаш по читање и се користи за сите редови.
    """

    keywords: Sequence[Keyword]
    # Групата во која паѓа сè што речникот не го познава. Производот СЕ
    # ПРИКАЖУВА - само е означен како несортиран.
    fallback_slug: str = "drugo"
    # Кој извор победува при ист приоритет.
    prefer: Source = "description"

    # Клучен збор со предкомпајлиран шаблон - се компајлира еднаш, а потоа
    # се вртат десетки илјадници редови низ истите неколку стотини шаблони.
    _positive: list[tuple[Keyword, re.Pattern[str]]] = field(
        default_factory=list, init=False, repr=False
    )
    _negative: list[tuple[Keyword, re.Pattern[str]]] = field(
        default_factory=list, init=False, repr=False
    )

    def __post_init__(self) -> None:
        for keyword in self.keywords:
            if not keyword.normalized:
                continue
            target = self._negative if keyword.is_negative else self._positive
            target.append((keyword, keyword.build_pattern()))
        self._positive.sort(
            key=lambda item: (-item[0].priority, -len(item[0].normalized))
        )
        # Подолгите негативни прво: поконкретниот исклучок е поважен.
        self._negative.sort(key=lambda item: -len(item[0].normalized))

    @property
    def _weights(self) -> dict[Source, int]:
        return (
            {"description": 1, "name": 0}
            if self.prefer == "description"
            else {"name": 1, "description": 0}
        )

    def group_of(self, name: str | None, description: str | None = None) -> Match:
        """Ја одредува групата по употреба на еден ред од ценовник."""
        name_text = normalize_for_match(name)
        desc_text = normalize_for_match(description)
        if not name_text and not desc_text:
            return Match(self.fallback_slug, matched=False)

        haystack = f"{desc_text} | {name_text}"
        weights = self._weights

        # 1. Кои групи се исклучени. Негативен збор не го фрла производот во
        #    „Друго" - само вели дека НЕ е од таа група, па следната најдобра
        #    добива шанса. „МАСЛО ЗА КОСА" не е храна, но е козметика.
        excluded: dict[str, str] = {}
        for negative, pattern in self._negative:
            if pattern.search(haystack):
                excluded.setdefault(negative.group_slug, negative.text)

        # 2. Најдобриот позитивен збор од група што не е исклучена.
        best: tuple[int, int, int] | None = None
        best_match: Match | None = None

        for keyword, pattern in self._positive:
            if keyword.group_slug in excluded:
                continue
            for source in ("description", "name"):
                text = desc_text if source == "description" else name_text
                if not text or not pattern.search(text):
                    continue
                score = (keyword.priority, weights[source], len(keyword.normalized))
                if best is None or score > best:
                    best = score
                    best_match = Match(
                        group_slug=keyword.group_slug,
                        matched=True,
                        matched_keyword=keyword.text,
                        matched_in=source,  # type: ignore[arg-type]
                    )

        if best_match is not None:
            return best_match

        # 3. Ништо не фати. Производот сè уште се прикажува, во „Друго".
        return Match(
            self.fallback_slug,
            matched=False,
            excluded_by=next(iter(excluded.values()), None),
        )

    def group_many(self, rows: Iterable[tuple[str | None, str | None]]) -> list[Match]:
        return [self.group_of(name, description) for name, description in rows]


def build_grouper(
    keywords: Iterable[Keyword],
    *,
    fallback_slug: str = "drugo",
    prefer: Source = "description",
) -> Grouper:
    return Grouper(tuple(keywords), fallback_slug=fallback_slug, prefer=prefer)
