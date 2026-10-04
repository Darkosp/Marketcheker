"""Регистрација, најава и одјава преку страниците.

Најавата не е услов за ништо: без сметка страницата и натаму ги покажува
сите денешни попусти. Сметката носи едно нешто - листата да биде на
човекот, а не на уредот.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.db

IME = "testko"
LOZINKA = "lozinka123"


async def _register(client: AsyncClient, ime: str = IME, lozinka: str = LOZINKA):
    return await client.post(
        "/registracija",
        data={"ime": ime, "lozinka": lozinka, "lozinka_pak": lozinka},
        follow_redirects=False,
    )


# ==========================================================================
# Формуларите се отвораат
# ==========================================================================
async def test_the_forms_open(db_client: AsyncClient) -> None:
    for path in ("/registracija", "/najava"):
        response = await db_client.get(path)
        assert response.status_code == 200, path


async def test_the_header_offers_login_when_nobody_is_in(
    db_client: AsyncClient,
) -> None:
    assert 'href="/najava"' in (await db_client.get("/")).text


# ==========================================================================
# Регистрација
# ==========================================================================
async def test_registration_logs_the_person_in(db_client: AsyncClient) -> None:
    response = await _register(db_client)
    assert response.status_code == 303
    assert response.headers["location"] == "/"

    html = (await db_client.get("/")).text
    assert IME in html
    assert "одјави се" in html


async def test_mismatched_passwords_are_caught(db_client: AsyncClient) -> None:
    response = await db_client.post(
        "/registracija",
        data={"ime": IME, "lozinka": LOZINKA, "lozinka_pak": "nesto-drugo"},
    )
    assert response.status_code == 200
    assert "не се исти" in response.text


async def test_a_bad_username_comes_back_with_the_reason(
    db_client: AsyncClient,
) -> None:
    response = await db_client.post(
        "/registracija",
        data={"ime": "дарко", "lozinka": LOZINKA, "lozinka_pak": LOZINKA},
    )
    assert response.status_code == 200
    assert "Корисничкото име" in response.text
    # Напишаното не исчезнува - инаку се пишува сè одново.
    assert "дарко" in response.text


async def test_a_taken_name_says_so(db_client: AsyncClient) -> None:
    await _register(db_client)
    await db_client.post("/odjava", follow_redirects=False)
    response = await _register(db_client)
    assert "зафатено" in response.text


# ==========================================================================
# Најава и одјава
# ==========================================================================
async def test_login_and_logout(db_client: AsyncClient) -> None:
    await _register(db_client)
    await db_client.post("/odjava", follow_redirects=False)
    assert IME not in (await db_client.get("/")).text

    response = await db_client.post(
        "/najava", data={"ime": IME, "lozinka": LOZINKA}, follow_redirects=False
    )
    assert response.status_code == 303
    assert IME in (await db_client.get("/")).text


async def test_a_wrong_password_says_nothing_about_the_name(
    db_client: AsyncClient,
) -> None:
    await _register(db_client)
    await db_client.post("/odjava", follow_redirects=False)

    wrong_password = await db_client.post(
        "/najava", data={"ime": IME, "lozinka": "pogresna1"}
    )
    unknown_name = await db_client.post(
        "/najava", data={"ime": "nikogas-postoel", "lozinka": "pogresna1"}
    )
    assert "Погрешно корисничко име или лозинка." in wrong_password.text
    assert "Погрешно корисничко име или лозинка." in unknown_name.text


async def test_logout_needs_a_post(db_client: AsyncClient) -> None:
    """Врска што одјавува може да ја активира туѓа страница, или самиот
    прелистувач при предвчитување.
    """
    assert (await db_client.get("/odjava")).status_code == 405


async def test_the_forms_redirect_when_already_logged_in(
    db_client: AsyncClient,
) -> None:
    await _register(db_client)
    for path in ("/najava", "/registracija"):
        response = await db_client.get(path, follow_redirects=False)
        assert response.status_code == 303, path


# ==========================================================================
# Листата оди на човекот
# ==========================================================================
async def test_a_choice_made_before_registering_is_kept(
    db_client: AsyncClient,
) -> None:
    """Некој пробал без сметка, одбрал неколку работи, па се регистрирал.
    Тој избор не смее да исчезне.
    """
    await db_client.get("/?izbor=kafe~нескафе")
    await _register(db_client)

    html = (await db_client.get("/")).text
    assert "Кафе · нескафе" in html


async def test_a_choice_made_before_does_not_overwrite_an_existing_list(
    db_client: AsyncClient,
) -> None:
    await _register(db_client)
    await db_client.get("/?izbor=masla")
    await db_client.post("/odjava", follow_redirects=False)

    # Друг избор на истиот уред, потоа повторна најава.
    await db_client.get("/?izbor=pelenki")
    await db_client.post(
        "/najava", data={"ime": IME, "lozinka": LOZINKA}, follow_redirects=False
    )
    html = (await db_client.get("/")).text
    assert "Масла и масти" in html
    assert "Пелени" not in html


async def test_the_list_follows_the_person_not_the_cookie(
    db_client: AsyncClient,
) -> None:
    await _register(db_client)
    await db_client.get("/?izbor=kafe")
    await db_client.post("/odjava", follow_redirects=False)

    # По одјава изборот не смее да остане на уредот - следниот човек на
    # истиот компјутер не смее да ја наследи туѓата листа.
    assert "Следиш:" not in (await db_client.get("/")).text

    await db_client.post(
        "/najava", data={"ime": IME, "lozinka": LOZINKA}, follow_redirects=False
    )
    assert "Кафе" in (await db_client.get("/")).text


async def test_two_people_on_one_browser_keep_separate_lists(
    db_client: AsyncClient,
) -> None:
    """Токму ова колачето не можеше да го направи."""
    await _register(db_client, "prviot")
    await db_client.get("/?izbor=kafe")
    await db_client.post("/odjava", follow_redirects=False)

    await _register(db_client, "vteriot")
    await db_client.get("/?izbor=pelenki")
    assert "Пелени и марамици" in (await db_client.get("/")).text
    assert "Кафе ·" not in (await db_client.get("/")).text

    await db_client.post("/odjava", follow_redirects=False)
    await db_client.post(
        "/najava", data={"ime": "prviot", "lozinka": LOZINKA}, follow_redirects=False
    )
    html = (await db_client.get("/")).text
    assert "Пелени" not in html
