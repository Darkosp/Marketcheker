"""Изборот на корисникот: читање од URL и од колаче.

Најважната разлика што се тестира е меѓу „барањето не се изјасни" (нема
`izbor`) и „барањето рече ништо" (`izbor=`). Без неа копчето „види ги сите
попусти" не може да работи: колачето веднаш би го вратило стариот избор.
"""

from __future__ import annotations

import pytest

from app.catalog.groups import GROUPS, SUBCATEGORIES
from app.web import selection


# ==========================================================================
# Чистење на изборот
# ==========================================================================
def test_known_slugs_pass_through() -> None:
    assert selection.normalise(["kafe", "masla"]) == ["masla", "kafe"]


def test_unknown_slugs_are_dropped() -> None:
    """URL-то може да дојде рачно напишано."""
    assert selection.normalise(["kafe", "nepostoecko", ""]) == ["kafe"]


def test_whitespace_and_case_are_forgiven() -> None:
    assert selection.normalise([" KAFE ", "Masla"]) == ["masla", "kafe"]


def test_duplicates_collapse() -> None:
    assert selection.normalise(["kafe", "kafe"]) == ["kafe"]


def test_group_swallows_its_own_subcategories() -> None:
    """„Пијалоци, Кафе" е истото што и „Пијалоци" - и пократкото е појасно."""
    assert selection.normalise(["pijaloci", "kafe", "sok"]) == ["pijaloci"]


def test_subcategory_of_another_group_survives() -> None:
    chosen = selection.normalise(["pijaloci", "masla"])
    assert chosen == ["masla", "pijaloci"]


def test_order_comes_from_the_catalog_not_the_request() -> None:
    """Ист избор секогаш дава ист запис - инаку колачето и линкот се менуваат
    без причина.
    """
    one = selection.normalise(["kafe", "slatki", "pelenki"])
    two = selection.normalise(["pelenki", "kafe", "slatki"])
    assert one == two == ["slatki", "kafe", "pelenki"]


def test_a_group_sorts_before_its_subcategories() -> None:
    chosen = selection.normalise(["kafe", "hrana"])
    assert chosen == ["hrana", "kafe"]


def test_selection_is_capped() -> None:
    everything = [slug for slug, _, _ in GROUPS] + [
        slug for slug, _, _, _ in SUBCATEGORIES
    ]
    assert len(selection.normalise(everything)) <= selection.MAX_SELECTED


def test_every_catalog_slug_is_selectable() -> None:
    """Секое ниво е избирливо само по себе - и групите, и под-категориите."""
    for slug, _, _ in GROUPS:
        assert selection.normalise([slug]) == [slug]
    for slug, _, _, _ in SUBCATEGORIES:
        assert selection.normalise([slug]) == [slug]


# ==========================================================================
# URL наспроти колаче
# ==========================================================================
def test_missing_parameter_means_undecided() -> None:
    assert selection.from_query(None) is None


def test_empty_parameter_means_everything() -> None:
    """`?izbor=` е изјаснет празен избор, не отсутен избор."""
    assert selection.from_query([""]) == []


def test_undecided_request_falls_back_to_the_cookie() -> None:
    chosen, from_url = selection.resolve(None, "kafe,masla")
    assert chosen == ["masla", "kafe"]
    assert from_url is False


def test_url_wins_over_the_cookie() -> None:
    chosen, from_url = selection.resolve(["pelenki"], "kafe")
    assert chosen == ["pelenki"]
    assert from_url is True


def test_empty_url_clears_the_cookie_choice() -> None:
    chosen, from_url = selection.resolve([""], "kafe,masla")
    assert chosen == []
    assert from_url is True


def test_broken_cookie_is_ignored_not_fatal() -> None:
    assert selection.from_cookie("kafe,,nepostoecko,") == ["kafe"]
    assert selection.from_cookie("") == []
    assert selection.from_cookie(None) == []


def test_cookie_round_trip() -> None:
    chosen = selection.normalise(["kafe", "pelenki"])
    assert selection.from_cookie(selection.to_cookie(chosen)) == chosen


# ==========================================================================
# Имињата за празната страница
# ==========================================================================
def test_labels_are_human_names() -> None:
    assert selection.labels(["kafe", "pelenki"]) == ["Кафе", "Пелени и марамици"]


def test_labels_skip_what_it_does_not_know() -> None:
    assert selection.labels(["nepostoecko"]) == []


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
