"""Учтивиот HTTP клиент: User-Agent, пауза, повторни обиди, грешки.

Сите тестови одат преку httpx.MockTransport - ниеден не допира мрежа.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.readers.base import SourceUnavailable
from app.readers.http import PoliteClient, content_hash


def _settings(**overrides) -> Settings:
    base = {
        "secret_key": "test-secret-key-dolga-najmalku-16",
        "postgres_password": "test",
        "scraper_user_agent": "Marketchecker/test (+https://example.invalid)",
        "scraper_delay_seconds": 0,
        "scraper_max_retries": 2,
        "scraper_timeout_seconds": 5,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture(autouse=True)
def _instant_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Паузите меѓу обидите не смеат да ги забавуваат тестовите.

    Тестовите што проверуваат колку се чека го преснимуваат ова со свој
    fake_sleep што ги собира повиците.
    """

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.readers.http.asyncio.sleep", no_sleep)


def _client(handler, **overrides) -> PoliteClient:
    settings = _settings(**overrides)
    inner = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": settings.scraper_user_agent},
        follow_redirects=True,
    )
    return PoliteClient(settings, client=inner)


# ---- успешно читање -------------------------------------------------------
async def test_get_text_returns_body_and_hash() -> None:
    async with _client(lambda r: httpx.Response(200, text="здраво")) as client:
        text, digest = await client.get_text("https://example.invalid/")
    assert text == "здраво"
    assert digest == content_hash("здраво".encode())


async def test_sends_project_user_agent() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, text="ok")

    async with _client(handler) as client:
        await client.get_text("https://example.invalid/")

    # Јасен User-Agent со линк до проектот - дел од учтивото читање.
    assert "Marketchecker" in seen[0]
    assert "example.invalid" in seen[0]


async def test_get_bytes_for_pdf() -> None:
    payload = b"%PDF-1.4 ..."
    async with _client(lambda r: httpx.Response(200, content=payload)) as client:
        data, digest = await client.get_bytes("https://example.invalid/cenovnik.pdf")
    assert data == payload
    assert digest == content_hash(payload)


async def test_post_json() -> None:
    async with _client(lambda r: httpx.Response(200, json={"shops": [1, 2]})) as client:
        data = await client.post_json("https://example.invalid/api")
    assert data == {"shops": [1, 2]}


async def test_post_json_rejects_non_json() -> None:
    async with _client(lambda r: httpx.Response(200, text="<html>")) as client:
        with pytest.raises(SourceUnavailable, match="JSON"):
            await client.post_json("https://example.invalid/api")


# ---- повторни обиди -------------------------------------------------------
async def test_retries_on_503_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, text="конечно")

    async with _client(handler) as client:
        text, _ = await client.get_text("https://example.invalid/")

    assert text == "конечно"
    assert calls["n"] == 3


async def test_gives_up_after_max_retries() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503)

    async with _client(handler, scraper_max_retries=1) as client:
        with pytest.raises(SourceUnavailable, match="503"):
            await client.get_text("https://example.invalid/")

    # max_retries=1 значи 2 обиди вкупно.
    assert calls["n"] == 2


async def test_retries_on_network_error() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("мрежата падна", request=request)
        return httpx.Response(200, text="ok")

    async with _client(handler) as client:
        text, _ = await client.get_text("https://example.invalid/")

    assert text == "ok"
    assert calls["n"] == 2


async def test_network_error_becomes_source_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("нема одговор", request=request)

    async with _client(handler, scraper_max_retries=0) as client:
        with pytest.raises(SourceUnavailable):
            await client.get_text("https://example.invalid/")


# ---- грешки што не се повторуваат ----------------------------------------
async def test_404_fails_immediately_without_retry() -> None:
    # Страниците на Веро враќаат 404; повторувањето нема да помогне.
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    async with _client(handler) as client:
        with pytest.raises(SourceUnavailable, match="404"):
            await client.get_text("https://example.invalid/89_1.html")

    assert calls["n"] == 1


async def test_403_fails_immediately() -> None:
    # Кипер е зад Cloudflare - 403 значи блокада, не привремена грешка.
    async with _client(lambda r: httpx.Response(403)) as client:
        with pytest.raises(SourceUnavailable, match="403"):
            await client.get_text("https://example.invalid/")


# ---- пауза меѓу барања ----------------------------------------------------
async def test_waits_between_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Паузата се почитува: второто барање чека SCRAPER_DELAY_SECONDS."""
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("app.readers.http.asyncio.sleep", fake_sleep)

    async with _client(
        lambda r: httpx.Response(200, text="ok"), scraper_delay_seconds=1.5
    ) as client:
        await client.get_text("https://example.invalid/1")
        await client.get_text("https://example.invalid/2")

    # Првото барање не чека, второто чека.
    assert len(slept) == 1
    assert 0 < slept[0] <= 1.5


async def test_no_wait_when_delay_is_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("app.readers.http.asyncio.sleep", fake_sleep)

    async with _client(lambda r: httpx.Response(200, text="ok")) as client:
        await client.get_text("https://example.invalid/1")
        await client.get_text("https://example.invalid/2")

    assert slept == []


async def test_honours_retry_after_header(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("app.readers.http.asyncio.sleep", fake_sleep)

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, text="ok")

    async with _client(handler) as client:
        await client.get_text("https://example.invalid/")

    assert 7.0 in slept


async def test_caps_absurd_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry-After од еден час не смее да го закочи дневното читање."""
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("app.readers.http.asyncio.sleep", fake_sleep)

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "3600"})
        return httpx.Response(200, text="ok")

    async with _client(handler) as client:
        await client.get_text("https://example.invalid/")

    assert max(slept) <= 60.0


# ---- заштита на поставките ------------------------------------------------
def test_non_ascii_user_agent_is_rejected_at_startup() -> None:
    """Кирилица во User-Agent би крашнала секое барање - фати го порано."""
    with pytest.raises(ValueError, match="ASCII"):
        _settings(scraper_user_agent="Marketchecker/тест")
