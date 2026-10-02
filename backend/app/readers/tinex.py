"""Читач за Тинекс - НЕДОВРШЕН, изворот е недостапен.

Состојба на 2026-10-02:

- Сопствената страница „Ценовник" на tinex.com.mk
  (/тинекс-листа-на-артикли) води кон http://ceni.tinex.mk/
- ceni.tinex.mk постои во DNS (194.61.58.3), но одбива врски на 80, 443 и
  8080 - „Connection refused". Проверено и од сервер и од прелистувач,
  значи не е блокада по User-Agent, туку услугата не работи.
- Спецификацијата спомнува параметри org, search и perPage, но без жив
  сајт форматот на одговорот не може да се види.

Затоа тука НЕМА парсер. Да се напише парсер врз претпоставка би значело
код што никој не го проверил врз вистински податоци - токму она што
проектот го избегнува.

Читачот е регистриран за да се знае дека изворот постои и зошто го нема во
дневното читање. Chain.is_active за Тинекс се поставува на False, па
`python -m app.cli citaj` го прескокнува; се вика само изрично:

    docker compose exec api python -m app.cli citaj --tinex

Тоа ќе фрли SourceUnavailable со јасна порака - начин да се провери дали
сајтот се вратил, без да се менува код.

Кога ceni.tinex.mk ќе проработи:
1. Види каков одговор дава (HTML табела? JSON?) и зачувај примерок во
   tests/fixtures/.
2. Напиши parse_store_index и parse_pricelist како кај другите читачи.
3. Исфрли го SKIP_BY_DEFAULT и додај тестови врз примерокот.
"""

from __future__ import annotations

from typing import ClassVar

from app.core.logging import get_logger
from app.readers.base import (
    PricelistReader,
    ReaderResult,
    SourceUnavailable,
    StoreRef,
)
from app.readers.http import PoliteClient

log = get_logger(__name__)

BASE_URL = "http://ceni.tinex.mk/"
INFO_URL = "https://www.tinex.com.mk/тинекс-листа-на-артикли"

# Изворот не работи; дневното читање го прескокнува додека не се врати.
SKIP_BY_DEFAULT = True

_UNAVAILABLE = (
    f"Ценовникот на Тинекс ({BASE_URL}) не е достапен: услугата одбива врски. "
    f"Сопствената страница на Тинекс ({INFO_URL}) води кон истата адреса. "
    "Кога ќе проработи, читачот треба да се напише врз вистински примерок - "
    "види го коментарот во app/readers/tinex.py."
)


class TinexReader(PricelistReader):
    chain_code: ClassVar[str] = "tinex"
    chain_name: ClassVar[str] = "Тинекс"
    website: ClassVar[str] = INFO_URL

    def __init__(self, client: PoliteClient | None = None) -> None:
        self._client = client or PoliteClient()

    async def discover_stores(self) -> list[StoreRef]:
        """Проверува дали изворот се вратил. Ако не - јасна грешка.

        Намерно НЕ враќа празен список: празен резултат би изгледал како
        „нема продавници", а вистината е „изворот не работи".
        """
        try:
            await self._client.get_text(BASE_URL)
        except SourceUnavailable as exc:
            raise SourceUnavailable(_UNAVAILABLE) from exc

        # Ако стигнеме дотука, сајтот се вратил - а парсер сè уште нема.
        log.warning("Тинекс одговори! Време е да се напише парсерот.")
        raise SourceUnavailable(
            f"{BASE_URL} повторно одговара, но читачот сè уште нема парсер. "
            "Зачувај примерок во tests/fixtures/ и напиши го."
        )

    async def read_store(self, store: StoreRef) -> ReaderResult:
        raise SourceUnavailable(_UNAVAILABLE)


__all__ = ["SKIP_BY_DEFAULT", "TinexReader"]
