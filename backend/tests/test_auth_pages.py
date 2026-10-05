"""Отворање сметка и влез, преку страниците.

Нема лозинки. Отворањето бара корисничко име и адреса, и се потврдува со
линк - **еднаш**. Потоа секоја најава бара корисничко име и адреса, без
пошта. Најавата не е услов за ништо: без сметка страницата и натаму ги
покажува сите денешни попусти.

Поштата не е наместена во тестовите, па страницата „Провери ја поштата" го
прикажува линкот. Истото важи и на машина за развој; на production празен
SMTP воопшто не дозволува стартување.
"""

from __future__ import annotations

import re

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.db

IME = "testko"
POSTA = "testko@primer.mk"

# Линкот е апсолутен (носи PUBLIC_URL), а тест клиентот бара патека.
TOKEN = re.compile(r'href="[^"]*/vlez\?t=([^"&]+)"')


async def _register(client: AsyncClient, ime: str = IME, posta: str = POSTA):
    return await client.post("/registracija", data={"ime": ime, "posta": posta})


def _token(html: str) -> str:
    found = TOKEN.search(html)
    assert found, "страницата не го понуди линкот"
    return found.group(1)


async def _enter(client: AsyncClient, html: str):
    return await client.get(f"/vlez?t={_token(html)}", follow_redirects=False)


async def _sign_up(client: AsyncClient, ime: str = IME, posta: str = POSTA):
    """Цел пат: отворање сметка и влегување преку линкот."""
    return await _enter(client, (await _register(client, ime, posta)).text)


# ==========================================================================
# Формуларите
# ==========================================================================
async def test_the_forms_open(db_client: AsyncClient) -> None:
    for path in ("/registracija", "/najava"):
        assert (await db_client.get(path)).status_code == 200, path


async def test_the_header_offers_entry_when_nobody_is_in(
    db_client: AsyncClient,
) -> None:
    assert 'href="/najava"' in (await db_client.get("/")).text


async def test_no_password_is_ever_asked_for(db_client: AsyncClient) -> None:
    for path in ("/registracija", "/najava"):
        assert 'type="password"' not in (await db_client.get(path)).text, path


# ==========================================================================
# Отворање сметка
# ==========================================================================
async def test_registration_sends_a_link_instead_of_logging_in(
    db_client: AsyncClient,
) -> None:
    """Сметката не е активна додека адресата не е потврдена."""
    response = await _register(db_client)
    assert response.status_code == 200
    assert POSTA in response.text
    # Уште не е внатре.
    assert IME not in (await db_client.get("/")).text


async def test_a_letter_that_did_not_go_is_not_claimed_as_sent(
    db_client: AsyncClient,
) -> None:
    """Поштата е исклучена во тестовите, како и кога серверот нема да
    одговори. „Провери ја поштата" тогаш е лага - човекот чека нешто што
    нема да дојде.
    """
    response = await _register(db_client)
    assert "Писмото не тргна" in response.text
    assert "Провери ја поштата" not in response.text


async def test_the_link_from_the_letter_lets_the_person_in(
    db_client: AsyncClient,
) -> None:
    await _sign_up(db_client)
    html = (await db_client.get("/")).text
    assert IME in html
    assert "одјави се" in html


async def test_a_confirmed_account_goes_to_the_picker(
    db_client: AsyncClient,
) -> None:
    """Нова сметка нема ништо во листата, па списокот со сите попусти не е
    следниот чекор - изборот е.
    """
    response = await _sign_up(db_client)
    assert response.status_code == 303
    assert response.headers["location"] == "/izbor"


async def test_a_bad_address_comes_back_with_the_reason(
    db_client: AsyncClient,
) -> None:
    response = await _register(db_client, posta="ne-e-adresa")
    assert "адреса" in response.text
    # Напишаното не исчезнува - инаку се пишува сè одново.
    assert "ne-e-adresa" in response.text
    assert IME in response.text


async def test_a_bad_username_comes_back_with_the_reason(
    db_client: AsyncClient,
) -> None:
    response = await _register(db_client, ime="дарко")
    assert "Корисничкото име" in response.text
    assert "дарко" in response.text


async def test_a_taken_name_says_so(db_client: AsyncClient) -> None:
    await _register(db_client)
    response = await _register(db_client, posta="drugo@primer.mk")
    assert "зафатено" in response.text


async def test_a_taken_address_points_at_entering_instead(
    db_client: AsyncClient,
) -> None:
    await _register(db_client)
    response = await _register(db_client, ime="drugo-ime")
    assert "веќе има сметка" in response.text


# ==========================================================================
# Влез
# ==========================================================================
async def test_entering_with_the_name_and_the_address(
    db_client: AsyncClient,
) -> None:
    """По првата потврда, најавата не бара пошта."""
    await _sign_up(db_client)
    await db_client.post("/odjava", follow_redirects=False)
    assert IME not in (await db_client.get("/")).text

    response = await db_client.post(
        "/najava", data={"ime": IME, "posta": POSTA}, follow_redirects=False
    )
    assert response.status_code == 303
    assert IME in (await db_client.get("/")).text


async def test_the_name_alone_is_not_enough(db_client: AsyncClient) -> None:
    """Инаку секој што ќе напише туѓо име влегува во туѓа сметка."""
    await _sign_up(db_client)
    await db_client.post("/odjava", follow_redirects=False)

    response = await db_client.post(
        "/najava", data={"ime": IME, "posta": ""}, follow_redirects=False
    )
    assert response.status_code == 200
    assert IME not in (await db_client.get("/")).text


async def test_the_address_alone_is_not_enough(db_client: AsyncClient) -> None:
    await _sign_up(db_client)
    await db_client.post("/odjava", follow_redirects=False)

    await db_client.post(
        "/najava", data={"ime": "", "posta": POSTA}, follow_redirects=False
    )
    assert IME not in (await db_client.get("/")).text


async def test_a_name_with_someone_elses_address_does_not_enter(
    db_client: AsyncClient,
) -> None:
    """Обете мора да се од ИСТА сметка."""
    await _sign_up(db_client)
    await db_client.post("/odjava", follow_redirects=False)
    await _sign_up(db_client, "drugiot", "drugiot@primer.mk")
    await db_client.post("/odjava", follow_redirects=False)

    response = await db_client.post(
        "/najava", data={"ime": IME, "posta": "drugiot@primer.mk"}
    )
    assert "Нема сметка" in response.text
    assert IME not in (await db_client.get("/")).text


async def test_an_unknown_account_says_so(db_client: AsyncClient) -> None:
    response = await db_client.post(
        "/najava", data={"ime": "nikogas", "posta": "nikogas@primer.mk"}
    )
    assert response.status_code == 200
    assert "Нема сметка" in response.text


async def test_an_unconfirmed_account_gets_the_link_again(
    db_client: AsyncClient,
) -> None:
    """Потврдата на адресата е единственото нешто што оди по пошта - и не
    смее да се прескокне со најава.
    """
    await _register(db_client)  # без отворање на линкот
    response = await db_client.post(
        "/najava", data={"ime": IME, "posta": POSTA}, follow_redirects=False
    )
    assert response.status_code == 200
    assert "/vlez?t=" in response.text
    assert IME not in (await db_client.get("/")).text


async def test_a_used_link_does_not_work_twice(db_client: AsyncClient) -> None:
    registered = await _register(db_client)
    await _enter(db_client, registered.text)
    await db_client.post("/odjava", follow_redirects=False)

    again = await _enter(db_client, registered.text)
    assert again.status_code == 200
    assert "Линкот не важи" in again.text


@pytest.mark.parametrize("rubbish", ["", "ne-e-token", "a.b.c"])
async def test_a_broken_link_says_so_instead_of_failing(
    db_client: AsyncClient, rubbish: str
) -> None:
    response = await db_client.get(f"/vlez?t={rubbish}")
    assert response.status_code == 200
    assert "Линкот не важи" in response.text


async def test_the_entry_forms_redirect_when_already_in(
    db_client: AsyncClient,
) -> None:
    await _sign_up(db_client)
    for path in ("/najava", "/registracija"):
        response = await db_client.get(path, follow_redirects=False)
        assert response.status_code == 303, path


async def test_logout_needs_a_post(db_client: AsyncClient) -> None:
    """Врска што одјавува може да ја активира туѓа страница, или самиот
    прелистувач при предвчитување.
    """
    assert (await db_client.get("/odjava")).status_code == 405


# ==========================================================================
# Листата оди на човекот
# ==========================================================================
async def test_a_choice_made_before_registering_is_kept(
    db_client: AsyncClient,
) -> None:
    """Некој пробал без сметка, одбрал неколку работи, па отворил сметка."""
    await db_client.get("/?izbor=kafe~нескафе")
    await _sign_up(db_client)
    assert "Кафе · нескафе" in (await db_client.get("/")).text


async def test_a_choice_made_before_does_not_overwrite_an_existing_list(
    db_client: AsyncClient,
) -> None:
    await _sign_up(db_client)
    await db_client.get("/?izbor=masla")
    await db_client.post("/odjava", follow_redirects=False)

    # Друг избор на истиот уред, потоа повторен влез.
    await db_client.get("/?izbor=pelenki")
    await db_client.post(
        "/najava", data={"ime": IME, "posta": POSTA}, follow_redirects=False
    )

    html = (await db_client.get("/")).text
    assert "Масла и масти" in html
    assert "Пелени" not in html


async def test_the_list_follows_the_person_not_the_device(
    db_client: AsyncClient,
) -> None:
    await _sign_up(db_client)
    await db_client.get("/?izbor=kafe")
    await db_client.post("/odjava", follow_redirects=False)

    # По одјава изборот не смее да остане на уредот - следниот човек на
    # истиот компјутер не смее да ја наследи туѓата листа.
    assert "Следиш:" not in (await db_client.get("/")).text


async def test_two_people_on_one_browser_keep_separate_lists(
    db_client: AsyncClient,
) -> None:
    """Токму ова колачето не можеше да го направи."""
    await _sign_up(db_client, "prviot", "prviot@primer.mk")
    await db_client.get("/?izbor=kafe")
    await db_client.post("/odjava", follow_redirects=False)

    await _sign_up(db_client, "vteriot", "vteriot@primer.mk")
    await db_client.get("/?izbor=pelenki")
    html = (await db_client.get("/")).text
    assert "Пелени и марамици" in html
    assert "Кафе ·" not in html


async def test_an_empty_list_is_invited_to_be_filled(
    db_client: AsyncClient,
) -> None:
    """Ситна врска не беше доволна - првиот корисник не ја забележа."""
    await _sign_up(db_client)
    html = (await db_client.get("/")).text
    assert "Направи си листа" in html
    assert IME in html


async def test_the_invitation_goes_away_once_there_is_a_list(
    db_client: AsyncClient,
) -> None:
    await _sign_up(db_client)
    assert "Направи си листа" not in (await db_client.get("/?izbor=kafe")).text


async def test_a_visitor_without_an_account_is_not_invited(
    db_client: AsyncClient,
) -> None:
    """Понудата има смисла само кога има каде да се зачува."""
    assert "Направи си листа" not in (await db_client.get("/")).text
