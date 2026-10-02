"""Страницата со попусти: филтри, подредување, страничење.

Трите грешки што се фатени рачно во прелистувач, а не со тест:

1. Празниот избор „сите маркети" праќаше `market=`, а параметарот беше
   list[int] - секое барање од формуларот враќаше 422 и ништо не работеше.
2. Избраната категорија живееше во името на копчето, па страничењето ја
   губеше.
3. Неоштиклирано поле прелистувачот не го праќа, па серверот ја земаше
   стандардната вредност и исклучувањето немаше ефект.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.api.routes.pages import PAGE_SIZES, Pagination


# ==========================================================================
# Параметри од URL - не смее ништо да врати 422
# ==========================================================================
@pytest.mark.parametrize(
    "query",
    [
        "",
        # Регресија: „сите маркети" праќа празна вредност.
        "?market=",
        "?market=&grad=&grupa=",
        "?market=abc",
        "?market=1&market=",
        # URL може да дојде и рачно напишан.
        "?strana=0",
        "?strana=-5",
        "?strana=99999",
        "?po_strana=7",
        "?po_strana=0",
        "?sortiraj=nepostoecko",
        "?grupa=nepostoecka-grupa",
        "?grad=nepostoecki-grad",
        "?lojalnost=false&lojalnost=true",
        "?ednodnevni=false",
        "?datum=2020-01-01",
    ],
)
async def test_page_never_returns_validation_error(
    client: AsyncClient, query: str
) -> None:
    response = await client.get(f"/{query}")
    assert response.status_code == 200, response.text


async def test_status_page_loads(client: AsyncClient) -> None:
    assert (await client.get("/sostojba")).status_code == 200


# ==========================================================================
# Приказот носи сè што му треба на формуларот
# ==========================================================================
async def test_group_lives_in_a_hidden_field(client: AsyncClient) -> None:
    """Инаку копчето „следна" ја губи избраната категорија.

    Копчињата за категории немаат name; изборот го носи скриено поле, што
    секое праќање го вклучува.
    """
    html = (await client.get("/?grupa=hrana")).text
    assert 'name="grupa"' in html
    assert 'id="grupa"' in html
    assert 'value="hrana"' in html


async def test_checkboxes_send_a_value_when_unchecked(client: AsyncClient) -> None:
    """Скриено „false" пред секое поле, за да исклучувањето стигне."""
    html = (await client.get("/")).text
    assert '<input type="hidden" name="lojalnost" value="false">' in html
    assert '<input type="hidden" name="ednodnevni" value="false">' in html


async def test_page_size_options_are_offered(client: AsyncClient) -> None:
    html = (await client.get("/")).text
    for size in PAGE_SIZES:
        assert f'value="{size}"' in html


async def test_htmx_request_returns_only_results(client: AsyncClient) -> None:
    full = (await client.get("/")).text
    partial = (await client.get("/", headers={"HX-Request": "true"})).text
    assert "<html" in full
    assert "<html" not in partial
    # Копчињата се враќаат одделно, за да се освежи означеното.
    assert "hx-swap-oob" in partial


# ==========================================================================
# Пресметката на страници
# ==========================================================================
def test_pagination_counts_pages() -> None:
    assert Pagination(page=1, page_size=48, total=100).pages == 3


def test_pagination_offset() -> None:
    assert Pagination(page=3, page_size=24, total=100).offset == 48


def test_pagination_shows_range() -> None:
    page = Pagination(page=2, page_size=24, total=100)
    assert (page.first_shown, page.last_shown) == (25, 48)


def test_last_page_range_is_clipped() -> None:
    page = Pagination(page=5, page_size=24, total=100)
    assert page.last_shown == 100


def test_empty_result_has_one_page() -> None:
    page = Pagination(page=1, page_size=48, total=0)
    assert page.pages == 1
    assert page.first_shown == 0
    assert not page.has_next


def test_window_shows_all_pages_when_few() -> None:
    assert Pagination(page=1, page_size=10, total=50).window == [1, 2, 3, 4, 5]


def test_window_is_trimmed_when_many() -> None:
    """Со 97 страници не се прикажуваат сите копчиња."""
    window = Pagination(page=50, page_size=48, total=4649).window
    assert window == [1, 49, 50, 51, 97]


def test_window_at_first_page() -> None:
    assert Pagination(page=1, page_size=48, total=4649).window == [1, 2, 97]


def test_has_previous_and_next() -> None:
    middle = Pagination(page=2, page_size=10, total=100)
    assert middle.has_previous and middle.has_next
    first = Pagination(page=1, page_size=10, total=100)
    assert not first.has_previous
    last = Pagination(page=10, page_size=10, total=100)
    assert not last.has_next
