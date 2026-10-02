"""Нормализација на градови.

Изворите не се согласуваат што е град. Веро за скопските продавници пишува
општина („Центар", „Карпош", „Аеродром"), Рамстор пишува „Скопје". Без
сведување, корисник што ќе избере Скопје би ги пропуштил сите 10 продавници
на Веро во Скопје.

Затоа секое име поминува низ:
    "Бул. ... – Карпош"  -> ("skopje", "Скопје")
    "Тетово"             -> ("tetovo", "Тетово")

Непознато име останува свој град - читачот не го погодува, само го
нормализира записот.
"""

from __future__ import annotations

import re

from app.readers.parsing import normalize_for_match, normalize_space

# Македонска кирилица во латиница, за slug.
_TRANSLITERATION = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "ѓ": "gj",
    "е": "e",
    "ж": "zh",
    "з": "z",
    "ѕ": "dz",
    "и": "i",
    "ј": "j",
    "к": "k",
    "л": "l",
    "љ": "lj",
    "м": "m",
    "н": "n",
    "њ": "nj",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "ќ": "kj",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "c",
    "ч": "ch",
    "џ": "dj",
    "ш": "sh",
}

# Општини и делови на Скопје како што ги пишуваат изворите.
SKOPJE_PARTS = frozenset(
    {
        # Градски општини
        "аеродром",
        "бутел",
        "гази баба",
        "ѓорче петров",
        "карпош",
        "кисела вода",
        "центар",
        "чаир",
        "шуто оризари",
        "сарај",
        # Делови што се појавуваат наместо општина
        "автокоманда",
        "топанско поле",
        "север",
        "козле",
        "тафталиџе",
        "капиштец",
        "дебар маало",
        "водно",
        "горно лисиче",
        "мичурин",
        "ист гејт",
        "џевахир",
        "скопје",
    }
)

SKOPJE = ("skopje", "Скопје")

# Градови каде изворот пишува име со правописна разлика.
_ALIASES: dict[str, tuple[str, str]] = {
    "богородица": ("gevgelija", "Гевгелија"),
}


def slugify(value: str) -> str:
    """Латинична ознака од македонско име: „Кисела Вода" -> „kisela-voda"."""
    text = normalize_for_match(value)
    letters = [_TRANSLITERATION.get(ch, ch) for ch in text]
    slug = re.sub(r"[^a-z0-9]+", "-", "".join(letters))
    return slug.strip("-")


def normalize_city(raw: str | None) -> tuple[str, str] | None:
    """Враќа (slug, име за приказ). Празно име -> None.

    Сите скопски општини и делови даваат ("skopje", "Скопје").
    """
    name = normalize_space(raw)
    if not name:
        return None

    key = normalize_for_match(name)
    if key in SKOPJE_PARTS:
        return SKOPJE

    alias = _ALIASES.get(key)
    if alias is not None:
        return alias

    slug = slugify(name)
    if not slug:
        return None
    # Името за приказ го пишуваме со прва голема буква по збор.
    display = " ".join(word.capitalize() for word in name.split())
    return slug, display
