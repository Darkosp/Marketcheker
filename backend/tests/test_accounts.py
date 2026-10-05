"""Сметки без лозинка: отворање, влез со линк, и листата на човекот.

Изборот порано живееше во колаче - по уред. Двајца на ист компјутер делеа
една листа; еден човек со телефон и со компјутер имаше две.

Лозинки нема воопшто: сметката се отвора со корисничко име и адреса, а
влегувањето оди преку линк што стигнува на таа адреса.
"""

from __future__ import annotations

import pytest

from app.catalog.picks import Pick
from app.models import City
from app.services import accounts

pytestmark = pytest.mark.db


async def _user(session, username: str = "darko", email: str = "darko@primer.mk"):
    return await accounts.register(session, username, email)


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
# Адреса
# ==========================================================================
@pytest.mark.parametrize(
    "address",
    ["darko@primer.mk", "a.b+c@sub.primer.com", "  DARKO@Primer.MK  "],
)
def test_good_addresses_pass(address: str) -> None:
    assert "@" in accounts.check_email(address)


def test_the_address_is_lowercased() -> None:
    """Инаку „Darko@" и „darko@" би биле две сметки на исто сандаче."""
    assert accounts.check_email(" Darko@Primer.MK ") == "darko@primer.mk"


@pytest.mark.parametrize(
    "address", ["", "darko", "darko@", "@primer.mk", "darko primer.mk", "a@b"]
)
def test_bad_addresses_are_refused(address: str) -> None:
    with pytest.raises(accounts.AccountError):
        accounts.check_email(address)


# ==========================================================================
# Отворање сметка
# ==========================================================================
async def test_a_new_account_is_not_confirmed_yet(db_session) -> None:
    user = await _user(db_session)
    assert user.email == "darko@primer.mk"
    assert user.is_confirmed is False


async def test_no_password_is_stored(db_session) -> None:
    """Нема лозинка воопшто - нема што да се украде од базата."""
    user = await _user(db_session)
    assert user.password_hash is None


async def test_the_same_name_cannot_be_taken_twice(db_session) -> None:
    await _user(db_session)
    with pytest.raises(accounts.AccountError, match="зафатено"):
        await _user(db_session, "darko", "drugo@primer.mk")


async def test_the_same_address_cannot_be_taken_twice(db_session) -> None:
    """Едно сандаче, една сметка - инаку линкот за влез не знае каде води."""
    await _user(db_session)
    with pytest.raises(accounts.AccountError, match="веќе има сметка"):
        await _user(db_session, "drugo-ime", "darko@primer.mk")


async def test_the_same_name_in_capitals_is_the_same_person(db_session) -> None:
    await _user(db_session, "Darko")
    with pytest.raises(accounts.AccountError, match="зафатено"):
        await _user(db_session, "DARKO", "drugo@primer.mk")


# ==========================================================================
# Наоѓање сметка
# ==========================================================================
async def test_an_account_is_found_by_address(db_session) -> None:
    await _user(db_session)
    found = await accounts.find_by_login(db_session, "DARKO@primer.mk")
    assert found is not None


async def test_an_account_is_found_by_username_too(db_session) -> None:
    """Човекот пишува што памети."""
    await _user(db_session)
    assert await accounts.find_by_login(db_session, "darko") is not None


async def test_an_unknown_login_finds_nothing(db_session) -> None:
    assert await accounts.find_by_login(db_session, "nikogas@primer.mk") is None
    assert await accounts.find_by_login(db_session, "") is None


# ==========================================================================
# Влез со линк
# ==========================================================================
async def test_the_link_lets_the_person_in(db_session) -> None:
    user = await _user(db_session)
    token = await accounts.issue_login_code(db_session, user)
    assert await accounts.redeem_login_code(db_session, token) is user


async def test_the_link_confirms_the_address(db_session) -> None:
    """Истиот линк потврдува и внесува: ако писмото стигнало и некој
    кликнал, адресата постои - друга проверка не ни треба.
    """
    user = await _user(db_session)
    token = await accounts.issue_login_code(db_session, user)
    await accounts.redeem_login_code(db_session, token)
    assert user.is_confirmed is True


async def test_the_link_works_only_once(db_session) -> None:
    """Старо писмо во сандачето не смее да отвора врата засекогаш."""
    user = await _user(db_session)
    token = await accounts.issue_login_code(db_session, user)
    assert await accounts.redeem_login_code(db_session, token) is not None
    assert await accounts.redeem_login_code(db_session, token) is None


async def test_a_new_link_kills_the_previous_one(db_session) -> None:
    user = await _user(db_session)
    first = await accounts.issue_login_code(db_session, user)
    await accounts.issue_login_code(db_session, user)
    assert await accounts.redeem_login_code(db_session, first) is None


async def test_a_tampered_link_is_refused(db_session) -> None:
    user = await _user(db_session)
    token = await accounts.issue_login_code(db_session, user)
    assert await accounts.redeem_login_code(db_session, token[:-3] + "xyz") is None


@pytest.mark.parametrize("rubbish", ["", "ne-e-token", "a.b.c"])
async def test_rubbish_is_refused_not_fatal(db_session, rubbish: str) -> None:
    assert await accounts.redeem_login_code(db_session, rubbish) is None


async def test_a_closed_account_cannot_enter(db_session) -> None:
    user = await _user(db_session)
    token = await accounts.issue_login_code(db_session, user)
    user.is_active = False
    await db_session.flush()
    assert await accounts.redeem_login_code(db_session, token) is None


async def test_entering_is_written_down(db_session) -> None:
    user = await _user(db_session)
    assert user.last_login_at is None
    await accounts.redeem_login_code(
        db_session, await accounts.issue_login_code(db_session, user)
    )
    assert user.last_login_at is not None


async def test_the_second_entry_keeps_the_first_confirmation(db_session) -> None:
    """Датумот на потврда е кога адресата е проверена, не последниот влез."""
    user = await _user(db_session)
    await accounts.redeem_login_code(
        db_session, await accounts.issue_login_code(db_session, user)
    )
    first = user.email_confirmed_at
    await accounts.redeem_login_code(
        db_session, await accounts.issue_login_code(db_session, user)
    )
    assert user.email_confirmed_at == first


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
    one = await _user(db_session, "darko", "darko@primer.mk")
    two = await _user(db_session, "ana", "ana@primer.mk")
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
