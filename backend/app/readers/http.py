"""Учтив HTTP клиент за читачите.

Правила: пауза меѓу барања, повторни обиди со растечка пауза, јасен
User-Agent со линк до проектот. Секоја трајна грешка излегува како
SourceUnavailable - читачот никогаш не враќа тивок празен резултат.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from types import TracebackType
from typing import Any, Self

import httpx

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.readers.base import SourceUnavailable

log = get_logger(__name__)

# Статуси кај кои има смисла да се обидеме повторно.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

# Најдолга пауза која ја почитуваме од Retry-After, за да не виси читањето.
MAX_RETRY_AFTER_SECONDS = 60.0


class PoliteClient:
    """Обвивка над httpx.AsyncClient со пауза, повторни обиди и логирање."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            headers={
                "User-Agent": self._settings.scraper_user_agent,
                "Accept-Language": "mk,en;q=0.8",
            },
            timeout=self._settings.scraper_timeout_seconds,
            follow_redirects=True,
        )
        # Кога беше последното барање - за паузата меѓу барања. Клучот го
        # штити од напоредни задачи: без него сите читаат иста вредност,
        # спијат исто време и пукаат заедно - паузата станува привид.
        self._last_request_at: float | None = None
        self._turn = asyncio.Lock()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------
    async def _wait_turn(self) -> None:
        """Држи најмалку SCRAPER_DELAY_SECONDS меѓу две барања.

        Редот се чува со клуч, за да важи и кога повеќе задачи го делат
        истиот клиент. Секој клиент е свој ред: ако синџирот чита повеќе
        продавници напоредно, секоја има свој клиент и своја пауза.
        """
        delay = self._settings.scraper_delay_seconds
        async with self._turn:
            if delay > 0 and self._last_request_at is not None:
                elapsed = time.monotonic() - self._last_request_at
                if elapsed < delay:
                    await asyncio.sleep(delay - elapsed)
            self._last_request_at = time.monotonic()

    def _retry_after(self, response: httpx.Response, attempt: int) -> float:
        """Паузата пред следниот обид: Retry-After ако го има, инаку растечка."""
        header = response.headers.get("retry-after")
        if header:
            try:
                return min(float(header), MAX_RETRY_AFTER_SECONDS)
            except ValueError:
                pass
        return min(2.0**attempt, MAX_RETRY_AFTER_SECONDS)

    async def request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Испраќа барање со повторни обиди. Фрла SourceUnavailable."""
        attempts = self._settings.scraper_max_retries + 1
        last_error: str = "непознато"

        for attempt in range(attempts):
            await self._wait_turn()
            try:
                response = await self._client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                log.warning(
                    "Барањето кон %s падна (обид %d/%d): %s",
                    url,
                    attempt + 1,
                    attempts,
                    last_error,
                )
                if attempt + 1 < attempts:
                    await asyncio.sleep(min(2.0**attempt, MAX_RETRY_AFTER_SECONDS))
                continue

            if response.status_code in RETRY_STATUSES:
                last_error = f"HTTP {response.status_code}"
                if attempt + 1 < attempts:
                    pause = self._retry_after(response, attempt)
                    log.warning(
                        "%s врати %s, пауза %.1fs (обид %d/%d)",
                        url,
                        response.status_code,
                        pause,
                        attempt + 1,
                        attempts,
                    )
                    await asyncio.sleep(pause)
                    continue
                raise SourceUnavailable(
                    f"{url} врати {response.status_code} по {attempts} обиди"
                )

            if response.status_code >= 400:
                # 403/404 нема смисла да се повторуваат.
                raise SourceUnavailable(f"{url} врати {response.status_code}")

            return response

        raise SourceUnavailable(f"{url} е недостапен по {attempts} обиди: {last_error}")

    async def get_text(self, url: str, **kwargs: Any) -> tuple[str, str]:
        """Враќа (текст, хеш на содржината)."""
        response = await self.request("GET", url, **kwargs)
        return response.text, content_hash(response.content)

    async def get_bytes(self, url: str, **kwargs: Any) -> tuple[bytes, str]:
        """Враќа (бајти, хеш на содржината) - за PDF ценовници."""
        response = await self.request("GET", url, **kwargs)
        return response.content, content_hash(response.content)

    async def post_json(self, url: str, **kwargs: Any) -> Any:
        """POST што очекува JSON одговор (Кипер, КАМ)."""
        response = await self.request("POST", url, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            raise SourceUnavailable(f"{url} не врати валиден JSON: {exc}") from exc


def content_hash(payload: bytes | str) -> str:
    """SHA-256 на преземената содржина, за откривање непроменет ценовник."""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
