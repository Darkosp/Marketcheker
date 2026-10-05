"""Што значи тоа што човекот го напишал.

Трите случаи што ги бараше корисникот, сите низ едно поле:

    „кафе"              широко  → понуди видови и брендови
    „кафе инстант"      потесно → пак понуди
    „зејтин брилијант"  точно   → тоа е изборот

Разликата не е во зборот, туку во тоа колку производи фаќа. Затоа тестовите
бараат база со вистински производи.
"""

from __future__ import annotations

import pytest

from app.catalog import default_grouper
from app.catalog.groups import GROUPS, SUBCATEGORIES
from app.readers.base import RawPriceRow, ReaderResult, StoreRef
from app.services import ingest
from app.services.search import WIDE, understand

pytestmark = pytest.mark.db

RUN_DATE = __import__("datetime").date(2026, 10, 2)


class _Reader:
    chain_code = "vero"
    chain_name = "Веро"
    website = "https://pricelist.vero.com.mk/"


STORE = StoreRef(external_id="1", name="ВЕРО 1", city="Скопје")


async def _seed(session, names: list[tuple[str, str]]) -> None:
    chain = await ingest.ensure_chain(session, _Reader)
    categories = await ingest.ensure_categories(session, GROUPS, SUBCATEGORIES)
    store = await ingest.ensure_store(session, chain, STORE)
    run = await ingest.start_run(session, chain, store, run_date=RUN_DATE)
    rows = [
        RawPriceRow(name=name, description=description, sale_price=None)
        for name, description in names
    ]
    await ingest.save_result(
        session,
        run,
        store,
        ReaderResult(
            store=STORE, rows=rows, source_url="https://x/", content_hash=None
        ),
        grouper=default_grouper(),
        categories=categories,
    )
    await session.flush()


KAFINJA = "КАФЕ - ИНСТАНТ КАФЕ"


@pytest.fixture
async def seeded(db_session):
    await _seed(
        db_session,
        [
            *[(f"КАФЕ НЕСКАФЕ КЛАСИК {n}Г", KAFINJA) for n in range(100, 100 + WIDE)],
            *[(f"КАФЕ ЈАКОБС {n}Г", KAFINJA) for n in range(10)],
            ("ЗЕЈТИН БРИЛИЈАНТ 1Л", "МАСЛА ЗА ЈАДЕЊЕ"),
            ("МАГДОНОС СВЕЖ", "ОВОШЈЕ И ЗЕЛЕНЧУК"),
        ],
    )
    return db_session


# ==========================================================================
# Широко наспроти точно
# ==========================================================================
async def test_a_wide_word_is_marked_as_wide(seeded) -> None:
    found = await understand(seeded, "кафе")
    assert found.products > WIDE
    assert found.is_wide is True


async def test_a_precise_phrase_is_not_wide(seeded) -> None:
    """Точно тоа што го бараше корисникот: „зејтин брилијант" е готов избор."""
    found = await understand(seeded, "зејтин брилијант")
    assert found.products == 1
    assert found.is_wide is False


async def test_one_precise_word_is_enough_too(seeded) -> None:
    found = await understand(seeded, "магдонос")
    assert found.products == 1
    assert found.is_wide is False


# ==========================================================================
# Стеснување со уште еден збор
# ==========================================================================
async def test_a_wide_word_offers_words_to_narrow_with(seeded) -> None:
    found = await understand(seeded, "кафе")
    assert "НЕСКАФЕ" in found.narrowing
    assert "ЈАКОБС" in found.narrowing


async def test_the_typed_word_is_not_offered_back(seeded) -> None:
    """„КАФЕ" е во секој погодок - не стеснува ништо."""
    found = await understand(seeded, "кафе")
    assert "КАФЕ" not in found.narrowing


async def test_narrowing_actually_narrows(seeded) -> None:
    wide = await understand(seeded, "кафе")
    narrow = await understand(seeded, "кафе јакобс")
    assert narrow.products < wide.products
    assert narrow.products == 10


async def test_words_are_joined_with_and_not_or(seeded) -> None:
    """„кафе јакобс" значи обата збора, не било кој од нив."""
    found = await understand(seeded, "кафе јакобс")
    assert all("ЈАКОБС" in name for name in found.examples)


async def test_the_next_search_keeps_the_previous_words(seeded) -> None:
    found = await understand(seeded, "кафе")
    assert found.plus("ЈАКОБС") == "кафе јакобс"


# ==========================================================================
# Категорија со такво име
# ==========================================================================
async def test_a_category_name_is_recognised(seeded) -> None:
    found = await understand(seeded, "кафе")
    assert [hit.slug for hit in found.categories] == ["kafe"]


async def test_a_brand_is_not_a_category(seeded) -> None:
    assert await understand(seeded, "нескафе") == (
        await understand(seeded, "нескафе")
    )
    assert (await understand(seeded, "нескафе")).categories == []


async def test_several_words_are_not_matched_against_categories(seeded) -> None:
    """„кафе јакобс" е барање по назив, не категорија."""
    assert (await understand(seeded, "кафе јакобс")).categories == []


# ==========================================================================
# Што се враќа за приказ
# ==========================================================================
async def test_examples_show_what_would_be_followed(seeded) -> None:
    found = await understand(seeded, "јакобс")
    assert found.examples
    assert all("ЈАКОБС" in name for name in found.examples)


async def test_the_whole_thing_becomes_one_pick(seeded) -> None:
    """Повеќе зборови се ЕДЕН избор со повеќе услови, не повеќе избори."""
    found = await understand(seeded, "кафе јакобс")
    assert found.as_pick.key == "~кафе~јакобс"


async def test_nothing_found_is_not_an_error(seeded) -> None:
    found = await understand(seeded, "нештоштонепостои")
    assert found.products == 0
    assert found.found_anything is False


@pytest.mark.parametrize("rubbish", ["", "   ", "a", "%", "~"])
async def test_rubbish_input_is_survivable(seeded, rubbish: str) -> None:
    found = await understand(seeded, rubbish)
    assert found.found_anything is False
