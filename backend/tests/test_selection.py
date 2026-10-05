"""Изборот на корисникот: читање од URL, и градот од колаче.

Следењето бара сметка, па за листата нема колаче - таа доаѓа од сметката.
Најважната разлика што се тестира е меѓу „барањето не се изјасни" (нема
`izbor`) и „барањето рече ништо" (`izbor=`). Без неа копчето „види ги сите
попусти" не може да работи: сметката веднаш би го вратила стариот избор.
"""

from __future__ import annotations

import pytest

from app.catalog.groups import GROUPS, SUBCATEGORIES
from app.catalog.picks import Pick
from app.web import selection


def keys(values) -> list[str]:
    """Записите на изборите - пократко за читање во тврдењата."""
    return [pick.key for pick in values]


# ==========================================================================
# Чистење на изборот
# ==========================================================================
def test_known_slugs_pass_through() -> None:
    assert keys(selection.normalise(["kafe", "masla"])) == ["masla", "kafe"]


def test_unknown_slugs_are_dropped() -> None:
    """URL-то може да дојде рачно напишано.

    Згрешен слуг НЕ станува барање по назив: тогаш „kafe" напишано како
    „kaffe" тивко би филтрирало по тој текст и страницата би изгледала
    празна без причина.
    """
    assert keys(selection.normalise(["kafe", "nepostoecko", ""])) == ["kafe"]


def test_whitespace_and_case_are_forgiven() -> None:
    assert keys(selection.normalise([" KAFE ", "Masla"])) == ["masla", "kafe"]


def test_duplicates_collapse() -> None:
    assert keys(selection.normalise(["kafe", "kafe"])) == ["kafe"]


def test_group_swallows_its_own_subcategories() -> None:
    """„Пијалоци, Кафе" е истото што и „Пијалоци" - и пократкото е појасно."""
    assert keys(selection.normalise(["pijaloci", "kafe", "sok"])) == ["pijaloci"]


def test_subcategory_of_another_group_survives() -> None:
    assert keys(selection.normalise(["pijaloci", "masla"])) == ["masla", "pijaloci"]


def test_order_comes_from_the_catalog_not_the_request() -> None:
    """Ист избор секогаш дава ист запис - инаку колачето и линкот се менуваат
    без причина.
    """
    one = keys(selection.normalise(["kafe", "slatki", "pelenki"]))
    two = keys(selection.normalise(["pelenki", "kafe", "slatki"]))
    assert one == two == ["slatki", "kafe", "pelenki"]


def test_a_group_sorts_before_its_subcategories() -> None:
    assert keys(selection.normalise(["kafe", "hrana"])) == ["hrana", "kafe"]


def test_selection_is_capped() -> None:
    everything = [slug for slug, _, _ in GROUPS] + [
        slug for slug, _, _, _ in SUBCATEGORIES
    ]
    assert len(selection.normalise(everything)) <= selection.MAX_SELECTED


def test_every_catalog_slug_is_selectable() -> None:
    """Секое ниво е избирливо само по себе - и групите, и под-категориите."""
    for slug, _, _ in GROUPS:
        assert keys(selection.normalise([slug])) == [slug]
    for slug, _, _, _ in SUBCATEGORIES:
        assert keys(selection.normalise([slug])) == [slug]


# ==========================================================================
# URL наспроти колаче
# ==========================================================================
def test_missing_parameter_means_undecided() -> None:
    assert selection.from_query(None) is None


def test_empty_parameter_means_everything() -> None:
    """`?izbor=` е изјаснет празен избор, не отсутен избор."""
    assert selection.from_query([""]) == []


def test_what_is_remembered_applies_when_the_request_is_silent() -> None:
    chosen, from_url = selection.choose(None, ["kafe", "masla"])
    assert keys(chosen) == ["masla", "kafe"]
    assert from_url is False


def test_the_url_wins_over_what_is_remembered() -> None:
    chosen, from_url = selection.choose(["pelenki"], ["kafe"])
    assert keys(chosen) == ["pelenki"]
    assert from_url is True


def test_an_empty_url_clears_what_was_remembered() -> None:
    chosen, from_url = selection.choose([""], ["kafe", "masla"])
    assert chosen == []
    assert from_url is True


def test_labels_are_human_names() -> None:
    chosen = selection.normalise(["kafe", "pelenki"])
    assert selection.labels(chosen) == ["Кафе", "Пелени и марамици"]


def test_a_brand_is_read_next_to_its_category() -> None:
    chosen = selection.normalise(["kafe~нескафе"])
    assert selection.labels(chosen) == ["Кафе · нескафе"]


# ==========================================================================
# Трето ниво: вид, бренд и грамажа - истиот механизам
# ==========================================================================
def test_a_term_narrows_inside_a_category() -> None:
    chosen = selection.normalise(["kafe~нескафе"])
    assert chosen == [Pick(category="kafe", terms=("нескафе",))]


def test_several_terms_stack_up() -> None:
    """„кафе инстант нескафе" е еден избор со два збора, не три избори."""
    chosen = selection.normalise(["kafe~инстант~нескафе"])
    assert chosen == [Pick(category="kafe", terms=("инстант", "нескафе"))]


def test_a_brand_can_stand_without_a_category() -> None:
    """Напишано само „нескафе" - бара низ сè."""
    assert selection.normalise(["~нескафе"]) == [Pick(terms=("нескафе",))]


def test_the_whole_category_swallows_its_brands() -> None:
    """Кој го следи сето кафе, ништо не добива од „кафе · нескафе"."""
    assert selection.normalise(["kafe", "kafe~нескафе"]) == [Pick(category="kafe")]


def test_a_group_swallows_brands_deeper_down() -> None:
    assert selection.normalise(["pijaloci", "kafe~нескафе"]) == [
        Pick(category="pijaloci")
    ]


def test_a_free_brand_is_not_swallowed() -> None:
    """Без категорија, зборот бара насекаде - категоријата не го покрива."""
    chosen = selection.normalise(["pijaloci", "~нескафе"])
    assert len(chosen) == 2


def test_two_brands_stay_two_choices() -> None:
    """Нескафе ИЛИ Јакобс - два избора, не еден со два збора."""
    chosen = selection.normalise(["kafe~нескафе", "kafe~јакобс"])
    assert len(chosen) == 2


def test_the_wider_choice_is_listed_first() -> None:
    chosen = selection.normalise(["kafe~нескафе", "sok"])
    assert keys(chosen) == ["kafe~нескафе", "sok"]


def test_too_short_a_word_is_not_a_filter() -> None:
    """Еден знак би фатил сè - тоа не е стеснување."""
    assert selection.normalise(["kafe~а"]) == [Pick(category="kafe")]


def test_a_glued_whole_name_is_refused() -> None:
    assert selection.normalise(["kafe~" + "х" * 80]) == [Pick(category="kafe")]


def test_like_characters_do_not_act_as_wildcards() -> None:
    """Напишано „100%" смее да значи само „100%", не „сè"."""
    from app.catalog.picks import like_pattern

    assert like_pattern("100%") == r"%100\%%"


# ==========================================================================
# Градот се памети како и производите
# ==========================================================================
GRADOVI = ("skopje", "bitola", "tetovo")


def test_a_city_from_the_url_decides() -> None:
    assert selection.resolve_city("bitola", None, GRADOVI) == ("bitola", True)


def test_an_empty_city_in_the_url_means_all_cities() -> None:
    """Паѓачкото мени секогаш праќа вредност, па празното е изјаснето."""
    assert selection.resolve_city("", "skopje", GRADOVI) == ("", True)


def test_without_a_city_in_the_url_the_remembered_one_applies() -> None:
    assert selection.resolve_city(None, "skopje", GRADOVI) == ("skopje", False)


def test_a_remembered_city_without_discounts_today_is_dropped() -> None:
    """Списокот нуди само градови со попусти ДЕНЕС.

    Ако запаметениот го нема, менито би покажувало „сите градови" додека
    филтерот тивко би филтрирал по него - празна страница без објаснување.
    """
    assert selection.resolve_city(None, "kocani", GRADOVI) == ("", False)


def test_a_city_written_by_hand_is_honoured_even_if_it_gives_nothing() -> None:
    """Во URL-то е изречно барање, за разлика од запаметеното."""
    assert selection.resolve_city("kocani", None, GRADOVI) == ("kocani", True)


def test_rubbish_in_the_city_cookie_is_ignored() -> None:
    for junk in ("../etc", "СКОПЈЕ", "a" * 80, "sko pje"):
        assert selection.resolve_city(None, junk, GRADOVI) == ("", False)


def test_rubbish_in_the_city_url_does_not_become_a_filter() -> None:
    assert selection.resolve_city("../etc", None, GRADOVI) == ("", True)


# ==========================================================================
# Броевите на македонски
# ==========================================================================
@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (1, "1 продавница"),
        (2, "2 продавници"),
        (11, "11 продавници"),  # единаесет е исклучок
        (21, "21 продавница"),
        (341, "341 продавница"),
        (1234, "1.234 продавници"),
        (0, "0 продавници"),
    ],
)
def test_counted_nouns_follow_the_last_digit(count: int, expected: str) -> None:
    from app.web.templates_env import brojka

    assert brojka(count, "продавница", "продавници") == expected
