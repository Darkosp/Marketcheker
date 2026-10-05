"""Кратко памтење во меморија, за упити што се менуваат еднаш дневно.

Цените се читаат еднаш на ден. Сепак, секое отворање на почетната страница
бараше десет упити низ `price_row` за истите десет производи - две секунди
по посетител, за одговор што ќе биде ист до утре во 11:00.

Затоа: не сложен кеш, туку речник со рок. Кога рокот ќе истече, следниот
што ќе побара го пресметува одново. Нема бришење однадвор, нема кеш-сервер,
нема што да се расипе.

Што НЕ смее да влезе тука: сè што зависи од корисникот, и сè што се менува
во текот на денот. Листата на еден човек, или денешните попусти по филтер,
мора да бидат свежи.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Hashable
from typing import Any

# Сите кешови во процесот, за да може да се испразнат на едно место: по
# дневното читање (кога сè се менува) и меѓу тестови (каде податоците се
# менуваат во секој тест, а рокот од десет минути би лажел).
_ALL: list[Cache] = []


def clear_all() -> None:
    """Ги празни сите кешови."""
    for cache in _ALL:
        cache.clear()


class Cache:
    """Речник со рок, за еден процес."""

    __slots__ = ("_entries", "_lock", "_max", "_ttl")

    def __init__(self, seconds: float, max_entries: int = 500) -> None:
        self._ttl = seconds
        # Клучот може да носи и избор на корисник, па бројот записи мора да
        # има граница - инаку речникот расте колку што има различни листи.
        self._max = max_entries
        self._entries: dict[Hashable, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()
        _ALL.append(self)

    async def get(
        self, key: Hashable, make: Callable[[], Awaitable[Any]]
    ) -> Any:
        """Запаметеното, или пресметано одново ако рокот истекол.

        Клучот се држи под брава за да десет истовремени посетители не го
        направат истиот упит десет пати.
        """
        now = time.monotonic()
        entry = self._entries.get(key)
        if entry is not None and entry[0] > now:
            return entry[1]

        async with self._lock:
            # Друг можеби го пресметал додека се чекаше.
            entry = self._entries.get(key)
            if entry is not None and entry[0] > time.monotonic():
                return entry[1]

            value = await make()
            if len(self._entries) >= self._max:
                # Наједноставното чистење што работи: најстарите по ред на
                # внесување. Не е LRU, но ова е кеш со рок од минути.
                for old in list(self._entries)[: self._max // 4]:
                    del self._entries[old]
            self._entries[key] = (time.monotonic() + self._ttl, value)
            return value

    def clear(self) -> None:
        """За тестови, и за по дневното читање."""
        self._entries.clear()


__all__ = ["Cache", "clear_all"]
