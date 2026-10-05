"""Групирање по употреба.

Сите примери тука се ВИСТИНСКИ називи и описи од ценовниците на Веро и
Рамстор на 2026-10-02.
"""

from __future__ import annotations

import pytest

from app.catalog import Keyword, build_grouper, default_grouper
from app.catalog.groups import (
    FALLBACK_SLUG,
    GROUPS,
    KEYWORDS,
    category_slugs,
    group_slugs,
)


@pytest.fixture(scope="module")
def grouper():
    return default_grouper()


# ==========================================================================
# Маслата - причината поради која ситните категории беа отфрлени.
# Ист збор „масло", различна намена; описот (полицата) го решава.
# ==========================================================================
@pytest.mark.parametrize(
    ("name", "description", "expected"),
    [
        # Веро: описот е „ГРУПА - ПОДГРУПА", групата кажува сè
        (
            "МАСЛО МАСЛИНОВО АЛЕКСАНДРОС ПОМАС 0.75Л",
            "МАСЛА ЗА ЈАДЕЊЕ - МАСЛО ЗА ЈАДЕЊЕ МАСЛИНОВО",
            "hrana",
        ),
        (
            "МАСЛО ПЧЕНКАРНО ФЕРАРА 1Л",
            "МАСЛА ЗА ЈАДЕЊЕ - МАСЛО ЗА ЈАДЕЊЕ СОНЧОГЛЕДОВО",
            "hrana",
        ),
        (
            "БОЈА ЗА КОСА ПАЛЕТЕ - W5 - НУГАТ",
            "КОЗМЕТИКА ЗА ГЛАВА - БОИ ЗА КОСА ЗА ЖЕНИ",
            "kozmetika",
        ),
        # Рамстор: една етикета, но пишува намената
        ("АФРОДИТА ОРЕОВО МАСЛО ЗА ТЕМНА КОСА 50МЛ", "МАСЛО ЗА КОСА", "kozmetika"),
        ("МАСЛО ЗА КОСА БОНЕС 60ГР.ОРЕОВО", "МАСЛО ЗА КОСА", "kozmetika"),
        ("НИВЕА МАСЛО ЗА СОНЧАЊЕ СПРЕЈ Ф6 200 МЛ", "МАСЛО ЗА СОНЧАЊЕ", "kozmetika"),
        ("МОНИНИ МАСЛИНОВО МАСЛО ЕКСТРА ВИРЏИН 0.5Л", "МАСЛИНОВО МАСЛО", "hrana"),
        ("БРИЛИЈАНТ СОНЧОГЛЕДОВО МАСЛО 1Л", "СОНЧОГЛЕДОВО МАСЛО", "hrana"),
        # Садови за масло се дом, не храна
        ("ХЕЛИОС САД ЗА МАСЛО 25 ЦЛ", "САДОВИ ЗА МАСЛО И ОЦЕТ", "dom"),
    ],
)
def test_oils_go_to_the_right_group(
    grouper, name: str, description: str, expected: str
) -> None:
    assert grouper.group_of(name, description).group_slug == expected


# ==========================================================================
# Кафето - истиот проблем во друга боја
# ==========================================================================
@pytest.mark.parametrize(
    ("name", "description", "expected"),
    [
        ("КАФЕ НЕСКАФЕ 3 ВО 1 КРЕМИ ЛАТЕ 10Х15ГР", "КАФЕ - ИНСТАНТ КАФЕ", "pijaloci"),
        ("ФИЛТЕР ЗА КАФЕ БР. 2 80КОМ 1/20", "ФИЛТЕР ЗА КАФЕ", "dom"),
        ("СЕТ ШОЛЈИ ЗА КАФЕ 1/6 80МЛ HS-19029", "ШОЛЈА ЗА КАФЕ", "dom"),
        ("ЗОТТ САХНЕ МЛЕКО ЗА КАФЕ 10%ММ 10X10Г", "МЛЕКО ЗА КАФЕ", "hrana"),
    ],
)
def test_coffee_related_items(
    grouper, name: str, description: str, expected: str
) -> None:
    assert grouper.group_of(name, description).group_slug == expected


# ==========================================================================
# Групи од вистински описи
# ==========================================================================
@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("КОНДИТОРСКИ ПРОИЗВОДИ - ЧОКОЛАДИ", "hrana"),
        ("МЛЕКО И МЛЕЧНИ ПРОИЗВОДИ - МЛЕКО СТЕРИЛИЗИРАНО", "hrana"),
        ("СУВОМЕСНАТИ ПРОИЗВОДИ - САЛАМИ", "hrana"),
        ("БЕЗАЛКОХОЛНИ ПИЈАЛОЦИ - ЏУС", "pijaloci"),
        ("АЛКОХОЛНИ ПИЈАЛОЦИ - ВИСКИ", "alkohol-tutun"),
        ("ЦИГАРИ И ОПРЕМА ЗА ПУШАЧИ - ЦИГАРИ", "alkohol-tutun"),
        ("КОЗМЕТИКА ЗА УСТА - ПАСТИ ЗА ЗАБИ", "kozmetika"),
        ("ДЕТЕРГЕНТИ - ДЕТЕРГЕНТ ЗА МАШИНСКО ПЕРЕЊЕ", "higiena-dom"),
        ("ХРАНА ЗА БЕБИЊА - КАШИ", "bebe-deca"),
        ("ХРАНА И ОПРЕМА ЗА ДОМ.МИЛЕНИЦИ - ХРАНА ЗА МАЧКИ", "milenici"),
        ("БАТЕРИИ И ЛАМПИ - БАТЕРИИ", "tehnika"),
        ("ДОЛНА ОБЛЕКА - ДОЛНА ОБЛЕКА ЗА ЖЕНИ", "obleka"),
        ("ШКОЛСКИ И КАНЦ.ПРИБОР - ОПРЕМА ЗА ШКОЛО", "kancelarija"),
        # Рамстор, една етикета
        ("ХРАНА ЗА МАЧЕ", "milenici"),
        ("ДЕО СПРЕЈ", "kozmetika"),
        ("ОМЕКНУВАЧ ЗА АЛИШТА", "higiena-dom"),
        ("КАПСУЛИ ЗА МАШИНСКО ПЕРЕЊЕ АЛИШТА", "higiena-dom"),
        ("ПАСТА ЗА ЗАБИ", "kozmetika"),
        ("ЕДИНЕЧЕН СЛАДОЛЕД", "hrana"),
        ("ТРАЈНО МЛЕКО", "hrana"),
    ],
)
def test_real_descriptions(grouper, description: str, expected: str) -> None:
    assert grouper.group_of("нешто", description).group_slug == expected


# ==========================================================================
# Резервната група - производот СЕ ПРИКАЖУВА, само е несортиран
# ==========================================================================
def test_unknown_goes_to_fallback_not_dropped(grouper) -> None:
    result = grouper.group_of("НЕКОЈ ЧУДЕН ПРОИЗВОД XYZ", "НЕПОЗНАТА ЕТИКЕТА")
    assert result.group_slug == FALLBACK_SLUG
    assert result.matched is False


def test_empty_input_goes_to_fallback(grouper) -> None:
    assert grouper.group_of(None, None).group_slug == FALLBACK_SLUG


def test_fallback_group_exists_in_groups() -> None:
    assert FALLBACK_SLUG in group_slugs()


# ==========================================================================
# Механизам
# ==========================================================================
def test_description_beats_name_at_equal_priority() -> None:
    """За груби групи полицата е посигурна од називот."""
    grouper = build_grouper(
        [Keyword("hrana", "масло"), Keyword("kozmetika", "крема")],
        prefer="description",
    )
    # Називот вика „крема", описот вика „масло" - описот победува.
    assert grouper.group_of("КРЕМА НЕШТО", "МАСЛО").group_slug == "hrana"


def test_prefer_name_inverts_the_rule() -> None:
    grouper = build_grouper(
        [Keyword("hrana", "масло"), Keyword("kozmetika", "крема")],
        prefer="name",
    )
    assert grouper.group_of("КРЕМА НЕШТО", "МАСЛО").group_slug == "kozmetika"


def test_higher_priority_wins() -> None:
    grouper = build_grouper(
        [
            Keyword("hrana", "масло", priority=1),
            Keyword("kozmetika", "за коса", priority=30),
        ]
    )
    assert grouper.group_of("МАСЛО ЗА КОСА", "").group_slug == "kozmetika"


def test_longer_keyword_wins_at_equal_priority() -> None:
    grouper = build_grouper([Keyword("hrana", "сок"), Keyword("pijaloci", "газиран сок")])
    assert grouper.group_of("", "ГАЗИРАН СОК").group_slug == "pijaloci"


def test_negative_keyword_lets_next_group_win_not_fallback() -> None:
    """Негативен збор вели „не е од ТАА група", не „фрли го во Друго"."""
    grouper = build_grouper(
        [
            Keyword("hrana", "масло"),
            Keyword("kozmetika", "коса"),
            Keyword("hrana", "масло за коса", is_negative=True),
        ]
    )
    result = grouper.group_of("МАСЛО ЗА КОСА", "")
    assert result.group_slug == "kozmetika"


def test_negative_keyword_falls_back_when_nothing_else_matches() -> None:
    grouper = build_grouper(
        [
            Keyword("hrana", "масло"),
            Keyword("hrana", "моторно масло", is_negative=True),
        ]
    )
    result = grouper.group_of("МОТОРНО МАСЛО 5W30", "")
    assert result.group_slug == FALLBACK_SLUG
    assert result.excluded_by == "моторно масло"


def test_match_reports_where_it_matched(grouper) -> None:
    result = grouper.group_of("НЕШТО", "КОНДИТОРСКИ ПРОИЗВОДИ - ЧОКОЛАДИ")
    assert result.matched is True
    assert result.matched_in == "description"
    assert result.matched_keyword


# ==========================================================================
# Исправност на самиот речник
# ==========================================================================
def test_every_keyword_points_to_a_real_category() -> None:
    """Клучен збор смее да води кон група или под-категорија, не кон ништо."""
    unknown = sorted({k.category_slug for k in KEYWORDS} - category_slugs())
    assert unknown == [], f"клучни зборови со непозната категорија: {unknown}"


def test_every_subcategory_has_a_real_parent() -> None:
    from app.catalog.groups import PARENT_OF

    orphans = sorted(set(PARENT_OF.values()) - group_slugs())
    assert orphans == [], f"под-категории со непозната група: {orphans}"


def test_group_slugs_are_unique() -> None:
    slugs = [slug for slug, _, _ in GROUPS]
    assert len(slugs) == len(set(slugs))


def test_group_sort_orders_are_unique() -> None:
    orders = [order for _, _, order in GROUPS]
    assert len(orders) == len(set(orders))


def test_keywords_are_ascii_free_of_stray_characters() -> None:
    """Печатна грешка со латиница во кирилична фраза не фаќа ништо.

    Регресија: во првата верзија стоеше „ексtra вирџин" со латинично „tra".
    """
    for keyword in KEYWORDS:
        text = keyword.normalized
        has_cyrillic = any("Ѐ" <= ch <= "ӿ" for ch in text)
        has_latin = any("a" <= ch <= "z" for ch in text)
        assert not (has_cyrillic and has_latin), f"мешана азбука: {keyword.text!r}"


def test_baking_paper_is_not_food(grouper) -> None:
    """Регресија: „за печење" фаќаше и хартија и плехови за печење."""
    assert (
        grouper.group_of("АЛФОЛ ХАРТИЈА ЗА ПЕЧЕЊЕ 8М", "ХАРТИЈА ЗА ПЕЧЕЊЕ").group_slug
        == "higiena-dom"
    )


def test_flour_for_baking_is_still_food(grouper) -> None:
    assert grouper.group_of("БРАШНО ЗА ПЕЧЕЊЕ 1КГ", "БРАШНО").group_slug == "hrana"
