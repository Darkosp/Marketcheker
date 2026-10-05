"""Тинекс: изворот е недостапен, читачот мора да го каже тоа јасно.

Нема парсер зашто нема што да се парсира - ceni.tinex.mk одбива врски.
Тестовите тука го чуваат токму тоа: читачот не смее да претвори паднат
извор во празен резултат, и не смее да влезе во дневното читање.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.readers import SKIPPED_BY_DEFAULT, default_chain_codes
from app.readers.base import SourceUnavailable
from app.readers.http import PoliteClient
from app.readers.tinex import TinexReader


def _client(handler) -> PoliteClient:
    settings = Settings(
        secret_key="test-secret-key-dolga-najmalku-16",
        postgres_password="test",
        scraper_delay_seconds=0,
        scraper_max_retries=0,
    )
    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return PoliteClient(settings, client=inner)


def test_tinex_is_skipped_in_daily_run() -> None:
    """Паднатиот извор не го полни дневното читање со грешки."""
    assert "tinex" in SKIPPED_BY_DEFAULT
    assert "tinex" not in default_chain_codes()


def test_other_chains_are_not_skipped() -> None:
    codes = default_chain_codes()
    assert {"vero", "ramstore", "kipper", "kam"} <= set(codes)


async def test_unreachable_source_gives_clear_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=request)

    reader = TinexReader(_client(handler))
    with pytest.raises(SourceUnavailable) as error:
        await reader.discover_stores()

    message = str(error.value)
    assert "ceni.tinex.mk" in message
    assert "tinex.com.mk" in message  # каде е официјалната страница
    await reader._client.aclose()


async def test_does_not_return_empty_list_when_source_is_down() -> None:
    """Празен список би изгледал како „нема продавници"."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=request)

    reader = TinexReader(_client(handler))
    with pytest.raises(SourceUnavailable):
        await reader.discover_stores()
    await reader._client.aclose()


async def test_site_coming_back_says_parser_is_missing() -> None:
    """Ако сајтот се врати, пораката кажува што треба да се направи."""
    reader = TinexReader(_client(lambda r: httpx.Response(200, text="<html>цени</html>")))
    with pytest.raises(SourceUnavailable, match="парсер"):
        await reader.discover_stores()
    await reader._client.aclose()


async def test_read_store_also_fails_clearly() -> None:
    from app.readers.base import StoreRef

    reader = TinexReader(_client(lambda r: httpx.Response(200)))
    with pytest.raises(SourceUnavailable):
        await reader.read_store(StoreRef(external_id="1", name="Тест"))
    await reader._client.aclose()
