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
        # Изборот на производи - празен, непознат, повторен, натрупан.
        "?izbor=",
        "?izbor=nepostoecko",
        "?izbor=kafe&izbor=kafe",
        "?izbor=pijaloci&izbor=kafe",
        "?izbor=" + "&izbor=".join(["kafe"] * 200),
    ],
)
async def test_page_never_returns_validation_error(
    client: AsyncClient, query: str
) -> None:
    response = await client.get(f"/{query}")
    assert response.status_code == 200, response.text


async def test_status_page_loads(client: AsyncClient) -> None:
    assert (await client.get("/sostojba")).status_code == 200


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?izbor=",
        "?izbor=kafe",
        "?izbor=nepostoecko",
        "?grad=skopje",
        "?otvori=nepostoecko",
        "?otvori=kafe~нескафе",
        "?izbor=kafe~нескафе",
        "?dodaj=",
        "?dodaj=нескафе&otvori=nepostoecko",
    ],
)
async def test_selection_page_never_returns_validation_error(
    client: AsyncClient, query: str
) -> None:
    response = await client.get(f"/izbor{query}", follow_redirects=True)
    assert response.status_code == 200, response.text


# ==========================================================================
# Изборот на производи
# ==========================================================================
async def test_the_first_step_offers_the_groups(client: AsyncClient) -> None:
    html = (await client.get("/izbor")).text
    assert "Пијалоци и напитоци" in html
    assert "otvori=pijaloci" in html


async def test_opening_a_group_shows_its_subcategories(client: AsyncClient) -> None:
    html = (await client.get("/izbor?otvori=pijaloci")).text
    assert "Кафе" in html
    assert "otvori=kafe" in html


async def test_every_level_can_be_taken_whole(client: AsyncClient) -> None:
    """Секое ниво може да биде последно - и групата, и под-категоријата."""
    group = (await client.get("/izbor?otvori=pijaloci")).text
    assert "izbor=pijaloci" in group

    category = (await client.get("/izbor?otvori=kafe")).text
    assert "izbor=kafe" in category


async def test_the_deepest_level_comes_from_the_real_names(
    client: AsyncClient,
) -> None:
    """Третото ниво НЕ е измислено: зборовите се вадат од називите во
    ценовниците, па „Нескафе" е таму затоа што постои, не затоа што некој
    се сетил на него.
    """
    html = (await client.get("/izbor?otvori=kafe")).text
    assert "НЕСКАФЕ" in html
    assert "izbor=kafe~" in html


async def test_a_brand_can_be_typed_in(client: AsyncClient) -> None:
    html = (await client.get("/izbor?otvori=kafe")).text
    assert 'name="dodaj"' in html


async def test_typing_a_brand_adds_it_and_cleans_the_url(
    client: AsyncClient,
) -> None:
    """По додавањето се оди на чисто URL, за да освежување не го додаде
    истото двапати.
    """
    response = await client.get(
        "/izbor?otvori=kafe&izbor=&dodaj=нескафе", follow_redirects=False
    )
    assert response.status_code == 303
    # `~` не се кодира - станува „izbor=kafe~<бренд>".
    assert "izbor=kafe~" in response.headers["location"]
    assert "dodaj" not in response.headers["location"]


async def test_a_useless_word_is_not_added(client: AsyncClient) -> None:
    """Еден знак би фатил сè - тоа не е стеснување."""
    response = await client.get(
        "/izbor?otvori=kafe&izbor=&dodaj=а", follow_redirects=False
    )
    assert response.status_code == 303
    assert "kafe~" not in response.headers["location"]


async def test_the_walk_keeps_what_is_already_chosen(client: AsyncClient) -> None:
    """Слегувањето ниво подолу не смее да го изгуби веќе избраното."""
    html = (await client.get("/izbor?izbor=masla&otvori=pijaloci")).text
    assert "izbor=masla" in html
    assert "Масла и масти" in html


async def test_a_chosen_thing_can_be_dropped(client: AsyncClient) -> None:
    html = (await client.get("/izbor?izbor=masla&izbor=kafe")).text
    # Врската за отстранување ја носи листата БЕЗ тој избор.
    assert "/izbor?izbor=kafe" in html


async def test_the_city_survives_the_walk(client: AsyncClient) -> None:
    html = (await client.get("/izbor?grad=skopje&otvori=pijaloci")).text
    assert "grad=skopje" in html


async def test_choice_is_remembered_in_a_cookie(client: AsyncClient) -> None:
    response = await client.get("/?izbor=kafe&izbor=masla")
    # Редоследот е од каталогот, не од URL-то. Разделникот е знак на
    # викање: запирка во колаче се бега („masla,kafe"), а точката ја
    # има во брендови како „dr.oetker".
    assert response.cookies.get("izbor") == "masla!kafe"


async def test_remembered_choice_applies_without_the_parameter(
    client: AsyncClient,
) -> None:
    await client.get("/?izbor=kafe")
    html = (await client.get("/")).text
    assert "Следиш:" in html
    assert "Кафе" in html


async def test_a_choice_of_several_survives_the_cookie(client: AsyncClient) -> None:
    """Регресија: со запирка како разделник, прелистувачот го враќаше
    наводничено колачето и изборот од повеќе категории се губеше цел.
    """
    await client.get("/?izbor=kafe&izbor=masla&izbor=pelenki")
    html = (await client.get("/")).text
    for name in ("Кафе", "Масла и масти", "Пелени и марамици"):
        assert name in html


async def test_an_empty_choice_forgets_the_cookie(client: AsyncClient) -> None:
    await client.get("/?izbor=kafe")
    assert client.cookies.get("izbor") == "kafe"
    await client.get("/?izbor=")
    assert not client.cookies.get("izbor")


async def test_the_city_is_remembered_too(client: AsyncClient) -> None:
    """Инаку секое отворање се враќа на „сите градови" - пропуст што се
    гледаше веднаш штом семејството почна да ја отвора од телефони.
    """
    response = await client.get("/?grad=skopje")
    assert response.cookies.get("grad") == "skopje"

    html = (await client.get("/")).text
    assert 'value="skopje" selected' in html.replace(" >", ">")


async def test_choosing_all_cities_forgets_the_city(client: AsyncClient) -> None:
    await client.get("/?grad=skopje")
    assert client.cookies.get("grad") == "skopje"
    await client.get("/?grad=")
    assert not client.cookies.get("grad")


async def test_the_remembered_city_reaches_the_picker(client: AsyncClient) -> None:
    """„Прикажи попусти" од /izbor мора да го врати во истиот град."""
    await client.get("/?grad=skopje")
    html = (await client.get("/izbor")).text
    assert "grad=skopje" in html


async def test_choice_travels_in_hidden_fields(client: AsyncClient) -> None:
    """Инаку страничењето и подредувањето го губат изборот - истата грешка
    што веќе се случи со категоријата.
    """
    html = (await client.get("/?izbor=kafe")).text
    assert '<input type="hidden" name="izbor" value="kafe">' in html
    assert '<input type="hidden" name="izbor" value="">' in html


# ==========================================================================
# Празната страница кога нема попуст на избраното
# ==========================================================================
async def test_empty_selection_says_what_was_checked(client: AsyncClient) -> None:
    """Непостоечкиот град гарантира нула резултати, без зависност од тоа
    што има во базата денес.
    """
    html = (await client.get("/?izbor=kafe&grad=nepostoecki-grad")).text
    assert "Денес нема попуст на ниту еден од избраните производи." in html
    assert "Проверени:" in html
    assert "Кафе" in html


async def test_empty_selection_offers_a_way_out(client: AsyncClient) -> None:
    html = (await client.get("/?izbor=kafe&grad=nepostoecki-grad")).text
    assert 'href="/izbor' in html
    assert 'href="/?izbor="' in html


async def test_empty_selection_hides_the_category_buttons(
    client: AsyncClient,
) -> None:
    """„Празна страница" значи празна: копчиња со нули не се прикажуваат.

    Садот останува, за да HTMX има што да замени при следното барање.
    """
    html = (await client.get("/?izbor=kafe&grad=nepostoecki-grad")).text
    assert 'id="kategorii"' in html
    assert 'class="chips"' not in html


async def test_no_selection_keeps_the_old_empty_message(client: AsyncClient) -> None:
    """Без избор, празниот резултат е вина на филтрите - и тоа го кажува."""
    html = (await client.get("/?grad=nepostoecki-grad")).text
    assert "Денес нема попуст на ниту еден од избраните производи." not in html
    assert "Нема попусти што одговараат на избраното." in html


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


async def test_cards_show_money_not_percent(client: AsyncClient) -> None:
    """Трите броја се редовна, со попуст и заштеда - без процент."""
    html = (await client.get("/")).text
    for label in ("редовна", "со попуст", "заштеда"):
        assert label in html
    assert "pct-high" not in html


async def test_savings_is_the_default_order(client: AsyncClient) -> None:
    html = (await client.get("/")).text
    assert '<option value="zasteda" selected>' in html.replace(" >", ">")


async def test_old_percent_links_still_work(client: AsyncClient) -> None:
    """Линк со стариот начин на подредување не смее да падне."""
    assert (await client.get("/?sortiraj=popust")).status_code == 200


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


# ==========================================================================
# Списокот маркети следи по избраниот град
# ==========================================================================
async def test_market_field_comes_back_with_htmx(client: AsyncClient) -> None:
    """Регресија: списокот маркети се пресметуваше по град на серверот, но
    HTMX менуваше само резултатите - страничната лента остануваше со
    маркетите од претходниот град.
    """
    partial = (await client.get("/?grad=skopje", headers={"HX-Request": "true"})).text
    assert 'id="market-field"' in partial
    assert "hx-swap-oob" in partial


async def test_market_field_is_in_the_full_page_too(client: AsyncClient) -> None:
    html = (await client.get("/")).text
    assert 'id="market-field"' in html
    # Во полна страница нема out-of-band ознака на тоа поле.
    assert 'id="market-field" hx-swap-oob' not in html


async def test_city_name_is_shown_next_to_the_market_label(
    client: AsyncClient,
) -> None:
    html = (await client.get("/?grad=skopje")).text
    assert "field-hint" in html


async def test_no_city_means_no_hint(client: AsyncClient) -> None:
    assert "field-hint" not in (await client.get("/")).text


# ==========================================================================
# Страницата за пишување
# ==========================================================================
@pytest.mark.parametrize(
    "query",
    ["", "?q=", "?q=кафе", "?q=нема-вакво", "?q=%25", "?q=кафе&izbor=masla", "?q=a"],
)
async def test_the_search_page_never_returns_validation_error(
    client: AsyncClient, query: str
) -> None:
    assert (await client.get(f"/najdi{query}")).status_code == 200


async def test_a_wide_word_offers_a_way_to_narrow(client: AsyncClient) -> None:
    html = (await client.get("/najdi?q=кафе")).text
    assert "Стесни уште" in html
    assert "НЕСКАФЕ" in html


async def test_a_wide_word_also_offers_the_category(client: AsyncClient) -> None:
    html = (await client.get("/najdi?q=кафе")).text
    assert "Следи ја целата" in html
    assert "otvori=kafe" in html


async def test_a_precise_phrase_is_offered_as_a_finished_choice(
    client: AsyncClient,
) -> None:
    """Тоа што го бараше корисникот: „зејтин брилијант" веднаш е готово."""
    html = (await client.get("/najdi?q=зејтин+брилијант")).text
    assert "Следи ги" in html
    assert "Стесни уште" not in html


async def test_narrowing_keeps_the_previous_words(client: AsyncClient) -> None:
    html = (await client.get("/najdi?q=кафе")).text
    assert "q=%D0%BA%D0%B0%D1%84%D0%B5+" in html or "q=кафе+" in html


async def test_the_search_keeps_the_list(client: AsyncClient) -> None:
    html = (await client.get("/najdi?q=кафе&izbor=masla")).text
    assert "izbor=masla" in html


async def test_nothing_found_explains_why(client: AsyncClient) -> None:
    html = (await client.get("/najdi?q=нештоштонепостои")).text
    assert "Нема ништо" in html


async def test_the_main_page_offers_the_search(client: AsyncClient) -> None:
    assert 'href="/najdi' in (await client.get("/")).text


async def test_the_picker_offers_the_search_too(client: AsyncClient) -> None:
    assert 'href="/najdi' in (await client.get("/izbor")).text
