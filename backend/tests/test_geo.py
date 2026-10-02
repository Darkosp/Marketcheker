"""Нормализација на градови.

Сите имиња тука доаѓаат од вистинските продавници на Веро и Рамстор.
"""

from __future__ import annotations

import pytest

from app.catalog.geo import normalize_city, slugify


@pytest.mark.parametrize(
    "raw",
    [
        # Веро пишува општина за скопските продавници...
        "Аеродром",
        "Карпош",
        "Центар",
        "Чаир",
        "Кисела Вода",
        # ...а Рамстор пишува град.
        "Скопје",
        # Големите букви и празните места не смеат да прават нов град.
        "СКОПЈЕ",
        "  скопје  ",
    ],
)
def test_skopje_parts_become_skopje(raw: str) -> None:
    """Без ова корисник што избира Скопје би ги пропуштил сите Веро продавници."""
    assert normalize_city(raw) == ("skopje", "Скопје")


@pytest.mark.parametrize(
    ("raw", "slug", "name"),
    [
        ("Тетово", "tetovo", "Тетово"),
        ("Битола", "bitola", "Битола"),
        ("Куманово", "kumanovo", "Куманово"),
        ("Гевгелија", "gevgelija", "Гевгелија"),
        ("Кавадарци", "kavadarci", "Кавадарци"),
        ("Штип", "shtip", "Штип"),
        ("Струмица", "strumica", "Струмица"),
        ("Кичево", "kichevo", "Кичево"),
        ("Охрид", "ohrid", "Охрид"),
        ("Струга", "struga", "Струга"),
        ("Велес", "veles", "Велес"),
    ],
)
def test_other_cities_keep_themselves(raw: str, slug: str, name: str) -> None:
    assert normalize_city(raw) == (slug, name)


def test_same_city_written_differently_gives_one_slug() -> None:
    assert normalize_city("ТЕТОВО") == normalize_city("тетово") == ("tetovo", "Тетово")


def test_empty_city() -> None:
    assert normalize_city(None) is None
    assert normalize_city("") is None
    assert normalize_city("   ") is None


def test_unknown_city_becomes_its_own() -> None:
    # Нов град не се погодува и не се губи - добива свој запис.
    assert normalize_city("Демир Капија") == ("demir-kapija", "Демир Капија")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Кисела Вода", "kisela-voda"),
        ("Ѓорче Петров", "gjorche-petrov"),
        ("Љубљанска", "ljubljanska"),
        ("Џепчиште", "djepchishte"),
        ("Ќосевци", "kjosevci"),
        ("Жабени", "zhabeni"),
    ],
)
def test_slugify_macedonian_letters(raw: str, expected: str) -> None:
    assert slugify(raw) == expected
