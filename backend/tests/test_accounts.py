"""Сметки: регистрација, најава, и листата врзана за човекот.

Изборот досега живееше во колаче - по уред. Двајца на ист компјутер делеа
една листа; еден човек со телефон и со компјутер имаше две.
"""

from __future__ import annotations

import pytest

from app.catalog.picks import Pick
from app.core.security import verify_password
from app.models import City
from app.services import accounts

pytestmark = pytest.mark.db


async def _user(session, username: str = "darko", password: str = "lozinka123"):
    return await accounts.register(session, username, password)


# ==========================================================================
# Корисничко име
# ==========================================================================
@pytest.mark.parametrize("name", ["darko", "d.sp", "marko_1", "ab-cd", "a" * 32])
def test_good_usernames_pass(name: str) -> None:
    assert accounts.check_username(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "ab",  # прекратко
        "a" * 33,  # предолго
        "со празно",
        "дарко",  # кирилица - „а" и „a" би биле два невидливо различни
        "darko@primer.mk",
        "",
    ],
)
def test_bad_usernames_are_refused(name: str) -> None:
    with pytest.raises(accounts.AccountError):
        accounts.check_username(name)


def test_case_does_not_make_a_second_person() -> None:
    assert accounts.normalise_username("  DaRkO ") == "darko"


# ==========================================================================
# Лозинка
# ==========================================================================
def test_short_password_is_refused() -> None:
    with pytest.raises(accounts.AccountError, match="најмалку"):
        accounts.check_password("kratka")


# ==========================================================================
# Регистрација
# ==========================================================================
async def test_registration_stores_a_hash_not_the_password(db_session) -> None:
    user = await _user(db_session)
    assert user.password_hash.startswith("$argon2id$")
    assert "lozinka123" not in user.password_hash
    assert verify_password("lozinka123", user.password_hash)


async def test_the_same_name_cannot_be_taken_twice(db_session) -> None:
    await _user(db_session)
    with pytest.raises(accounts.AccountError, match="зафатено"):
        await _user(db_session)


async def test_the_same_name_in_capitals_is_the_same_person(db_session) -> None:
    await _user(db_session, "Darko")
    with pytest.raises(accounts.AccountError, match="зафатено"):
        await _user(db_session, "DARKO")


# ==========================================================================
# Најава
# ==========================================================================
async def test_right_password_logs_in(db_session) -> None:
    await _user(db_session)
    assert await accounts.authenticate(db_session, "darko", "lozinka123")


async def test_capitals_in_the_name_still_log_in(db_session) -> None:
    await _user(db_session)
    assert await accounts.authenticate(db_session, "DARKO", "lozinka123")


async def test_wrong_password_does_not(db_session) -> None:
    await _user(db_session)
    assert await accounts.authenticate(db_session, "darko", "pogresna1") is None


async def test_an_unknown_name_answers_the_same_way(db_session) -> None:
    """Разлика меѓу „го нема тоа име" и „погрешна лозинка" кажува кои
    имиња постојат.
    """
    assert await accounts.authenticate(db_session, "nepostoechki", "bilokakva") is None


async def test_a_closed_account_cannot_log_in(db_session) -> None:
    user = await _user(db_session)
    user.is_active = False
    await db_session.flush()
    assert await accounts.authenticate(db_session, "darko", "lozinka123") is None


async def test_login_is_written_down(db_session) -> None:
    user = await _user(db_session)
    assert user.last_login_at is None
    await accounts.authenticate(db_session, "darko", "lozinka123")
    assert user.last_login_at is not None


# ==========================================================================
# Листата на корисникот
# ==========================================================================
async def test_an_empty_account_follows_nothing(db_session) -> None:
    user = await _user(db_session)
    assert await accounts.load_picks(db_session, user) == []


async def test_picks_come_back_in_the_same_order(db_session) -> None:
    user = await _user(db_session)
    picks = [Pick("masla"), Pick("kafe", ("нескафе",)), Pick(terms=("компир",))]
    await accounts.save_picks(db_session, user, picks)
    assert await accounts.load_picks(db_session, user) == [
        "masla",
        "kafe~нескафе",
        "~компир",
    ]


async def test_saving_replaces_instead_of_adding(db_session) -> None:
    """Кога корисникот отстранил нешто, тоа мора да исчезне."""
    user = await _user(db_session)
    await accounts.save_picks(db_session, user, [Pick("kafe"), Pick("masla")])
    await accounts.save_picks(db_session, user, [Pick("masla")])
    assert await accounts.load_picks(db_session, user) == ["masla"]


async def test_an_empty_list_can_be_saved(db_session) -> None:
    user = await _user(db_session)
    await accounts.save_picks(db_session, user, [Pick("kafe")])
    await accounts.save_picks(db_session, user, [])
    assert await accounts.load_picks(db_session, user) == []


async def test_two_people_keep_separate_lists(db_session) -> None:
    """Токму ова колачето не можеше да го направи на ист компјутер."""
    one = await _user(db_session, "darko")
    two = await _user(db_session, "ana")
    await accounts.save_picks(db_session, one, [Pick("kafe", ("нескафе",))])
    await accounts.save_picks(db_session, two, [Pick("pelenki")])

    assert await accounts.load_picks(db_session, one) == ["kafe~нескафе"]
    assert await accounts.load_picks(db_session, two) == ["pelenki"]


async def test_cyrillic_brands_survive_the_database(db_session) -> None:
    user = await _user(db_session)
    await accounts.save_picks(db_session, user, [Pick("kafe", ("нескафе", "200г"))])
    assert await accounts.load_picks(db_session, user) == ["kafe~нескафе~200г"]


# ==========================================================================
# Градот
# ==========================================================================
async def test_the_city_is_kept(db_session) -> None:
    db_session.add(City(slug="skopje", name="Скопје"))
    await db_session.flush()

    user = await _user(db_session)
    await accounts.set_city(db_session, user, "skopje")
    assert await accounts.city_of(db_session, user) == "skopje"


async def test_all_cities_clears_it(db_session) -> None:
    db_session.add(City(slug="skopje", name="Скопје"))
    await db_session.flush()
    user = await _user(db_session)
    await accounts.set_city(db_session, user, "skopje")
    await accounts.set_city(db_session, user, "")
    assert await accounts.city_of(db_session, user) == ""


async def test_an_unknown_city_does_not_wipe_the_kept_one(db_session) -> None:
    """URL-то може да дојде рачно напишано; тивко бришење е полошо."""
    db_session.add(City(slug="skopje", name="Скопје"))
    await db_session.flush()
    user = await _user(db_session)
    await accounts.set_city(db_session, user, "skopje")
    await accounts.set_city(db_session, user, "nepostoechki-grad")
    assert await accounts.city_of(db_session, user) == "skopje"
