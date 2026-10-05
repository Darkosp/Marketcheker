"""Читач за КАМ - https://kam.mk/ceni-vo-marketi.nspx

Структура на изворот:
- POST на /ShopsWeb/LoadShopList враќа JSON со 86 продавници:
  Id, Name, Address, City, Municipality, ShopFiles[0].RelativePath.
- Ценовникот е текстуален PDF на https://kam.mk/{RelativePath}, при што
  патеката го носи датумот: „2026/10/02/73.pdf". Околу 157 страници,
  460 KB, 1.700 реда - Crystal Reports, без потреба од OCR.
- Првиот ред од PDF-от е „Датум и време на последно ажурирање на цените:
  02.10.2026 5:49:36AM" - тоа е датумот на ценовникот и се проверува.

Табелата има 9 колони, со заглавие преку два реда (спојни ќелии):
    назив | продажна цена | единична цена | опис | достапност |
    редовна цена | цена со попуст + Попуст(%) | вид | времетраење

Внимание: цената со попуст и процентот се во ИСТА ќелија:
    „110ден.\\nПопуст: 15%"

Парсирањето на PDF е синхроно и трае околу 7 секунди по продавница, затоа
се врти во нишка - инаку ја блокира asyncio јамката за сите читачи.
"""

from __future__ import annotations

import asyncio
import io
import re
from datetime import date
from decimal import Decimal
from typing import Any, ClassVar
from urllib.parse import urljoin

import pdfplumber

from app.core.logging import get_logger
from app.readers.base import (
    EmptyPricelist,
    PricelistReader,
    RawPriceRow,
    ReaderResult,
    SourceUnavailable,
    StoreRef,
    StructureChanged,
)
from app.readers.http import PoliteClient
from app.readers.parsing import (
    ValueParseError,
    normalize_space,
    parse_date,
    parse_date_range,
    parse_decimal,
    parse_percent,
    parse_unit_price,
)

log = get_logger(__name__)

BASE_URL = "https://kam.mk/"
PAGE_URL = urljoin(BASE_URL, "ceni-vo-marketi.nspx")
SHOPS_URL = urljoin(BASE_URL, "ShopsWeb/LoadShopList")

# Редоследот на колоните во PDF-от. Се проверува преку заглавието.
COL_NAME = 0
COL_SALE = 1
COL_UNIT = 2
COL_DESCRIPTION = 3
COL_AVAILABILITY = 4
COL_REGULAR = 5
COL_DISCOUNT = 6
COL_PROMO_TYPE = 7
COL_DURATION = 8
COLUMN_COUNT = 9

# Зборови што мора да ги има во заглавието; ако ги нема, форматот се сменил.
HEADER_MARKERS = ("назив", "продажна", "единична", "редовна")

_UPDATED = re.compile(
    r"последно\s+ажурирање\s+на\s+цените\s*:?\s*(?P<rest>.{0,40})", re.IGNORECASE
)
# „110ден.\nПопуст: 15%" - цена и процент во иста ќелија.
_DISCOUNT_CELL = re.compile(r"Попуст\s*:?\s*(?P<pct>[\d.,]+)\s*%", re.IGNORECASE)

MAX_SKIPPED_RATIO = 0.1
MIN_SKIPPED_FOR_ALARM = 10


class KamReader(PricelistReader):
    chain_code: ClassVar[str] = "kam"
    chain_name: ClassVar[str] = "КАМ"
    website: ClassVar[str] = BASE_URL

    def __init__(self, client: PoliteClient | None = None) -> None:
        self._client = client or PoliteClient()

    # ------------------------------------------------------------------
    async def discover_stores(self) -> list[StoreRef]:
        payload = await self._client.post_json(
            SHOPS_URL, json={}, headers={"Referer": PAGE_URL}
        )
        stores = parse_shop_list(payload, base_url=BASE_URL)
        if not stores:
            raise StructureChanged(f"{SHOPS_URL}: не најдов ниту една продавница")
        log.info("КАМ: најдени %d продавници", len(stores))
        return stores

    async def read_store(self, store: StoreRef) -> ReaderResult:
        if not store.source_url:
            raise StructureChanged(
                f"КАМ {store.external_id}: продавницата нема ценовник (ShopFiles)"
            )

        payload, digest = await self._client.get_bytes(store.source_url)
        if not payload.startswith(b"%PDF"):
            raise StructureChanged(
                f"{store.source_url}: одговорот не е PDF (почнува со {payload[:8]!r})"
            )

        # pdfplumber е синхрон и троши процесор; во нишка е, за да не ја
        # блокира јамката додека другите синџири читаат.
        rows, pricelist_date, skipped, warnings = await asyncio.to_thread(
            parse_pdf, payload, source=store.source_url
        )

        if not rows:
            raise EmptyPricelist(f"{store.source_url}: ценовникот нема ниту еден ред")

        _guard_skipped(skipped, len(rows), source=store.source_url)

        return ReaderResult(
            store=store,
            rows=rows,
            source_url=store.source_url,
            pricelist_date=pricelist_date,
            content_hash=digest,
            rows_skipped=skipped,
            warnings=warnings,
        )


# --------------------------------------------------------------------------
# Продавници
# --------------------------------------------------------------------------
def parse_shop_list(payload: Any, *, base_url: str = BASE_URL) -> list[StoreRef]:
    """Ја чита листата продавници од LoadShopList."""
    if isinstance(payload, dict):
        # Ако некогаш го завиткаат во објект, најди го списокот внатре.
        payload = next(
            (value for value in payload.values() if isinstance(value, list)), None
        )
    if not isinstance(payload, list):
        raise StructureChanged(f"{SHOPS_URL}: одговорот не е список продавници")

    stores: list[StoreRef] = []
    for shop in payload:
        if not isinstance(shop, dict):
            continue
        shop_id = shop.get("Id")
        if shop_id is None:
            continue

        relative = _first_file(shop)
        stores.append(
            StoreRef(
                external_id=str(shop_id),
                name=normalize_space(shop.get("Name")) or f"КАМ {shop_id}",
                city=normalize_space(shop.get("City"))
                or normalize_space(shop.get("Municipality"))
                or None,
                address=normalize_space(shop.get("Address")) or None,
                source_url=urljoin(base_url, relative) if relative else None,
                extra={"relative_path": relative} if relative else {},
            )
        )
    return stores


def _first_file(shop: dict[str, Any]) -> str | None:
    files = shop.get("ShopFiles")
    if not isinstance(files, list) or not files:
        return None
    first = files[0]
    if not isinstance(first, dict):
        return None
    relative = normalize_space(first.get("RelativePath"))
    return relative.lstrip("/") or None


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------
def parse_pdf(
    payload: bytes, *, source: str
) -> tuple[list[RawPriceRow], date | None, int, list[str]]:
    """Ги вади редовите од целиот PDF. Синхроно - викај го во нишка."""
    rows: list[RawPriceRow] = []
    skipped = 0
    warnings: list[str] = []
    pricelist_date: date | None = None
    header_seen = False

    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        if not pdf.pages:
            raise StructureChanged(f"{source}: PDF-от нема страници")

        first_text = pdf.pages[0].extract_text() or ""
        pricelist_date = _parse_updated(first_text)

        for page in pdf.pages:
            for table in page.extract_tables():
                for raw_row in table:
                    cells = [normalize_space(cell) for cell in raw_row]
                    if _is_header(cells):
                        header_seen = True
                        continue
                    if not cells or not cells[COL_NAME]:
                        continue
                    try:
                        rows.append(_row_from_cells(cells))
                    except ValueParseError as exc:
                        skipped += 1
                        if len(warnings) < 5:
                            warnings.append(f"прескокнат ред: {exc}")

    if not header_seen:
        raise StructureChanged(
            f"{source}: не го најдов заглавието на табелата "
            f"(очекував {list(HEADER_MARKERS)})"
        )

    return rows, pricelist_date, skipped, warnings


def _is_header(cells: list[str]) -> bool:
    """Заглавието се протега преку два реда со спојни ќелии."""
    joined = " ".join(cells).lower()
    return sum(marker in joined for marker in HEADER_MARKERS) >= 3


def _parse_updated(text: str) -> date | None:
    """„Датум и време на последно ажурирање на цените: 02.10.2026 5:49:36AM"."""
    match = _UPDATED.search(normalize_space(text))
    if match is None:
        return None
    try:
        return parse_date(match.group("rest"))
    except ValueParseError as exc:
        log.warning("КАМ: не го прочитав датумот на ценовникот: %s", exc)
        return None


def split_discount_cell(raw: str) -> tuple[Decimal | None, Decimal | None]:
    """„110ден.\\nПопуст: 15%" -> (110, 15).

    Ако нема процент, целата ќелија е цена.
    """
    text = normalize_space(raw)
    if not text:
        return None, None

    match = _DISCOUNT_CELL.search(text)
    if match is None:
        return parse_decimal(text), None

    price_text = text[: match.start()].strip()
    return parse_decimal(price_text) if price_text else None, parse_percent(
        match.group("pct")
    )


def _row_from_cells(cells: list[str]) -> RawPriceRow:
    if len(cells) < COLUMN_COUNT:
        raise ValueParseError(f"редот има {len(cells)} наместо {COLUMN_COUNT} ќелии")

    name = cells[COL_NAME]
    if not name:
        raise ValueParseError("редот е без назив на стока")

    discount_price, discount_pct = split_discount_cell(cells[COL_DISCOUNT])
    unit_price, unit_label = parse_unit_price(cells[COL_UNIT])
    valid_from, valid_to = parse_date_range(cells[COL_DURATION])

    return RawPriceRow(
        name=name,
        description=cells[COL_DESCRIPTION] or None,
        sale_price=parse_decimal(cells[COL_SALE]),
        regular_price=parse_decimal(cells[COL_REGULAR]),
        discount_price=discount_price,
        discount_pct=discount_pct,
        unit_price=unit_price,
        unit_price_label=unit_label,
        promo_type_raw=cells[COL_PROMO_TYPE] or None,
        valid_from=valid_from,
        valid_to=valid_to,
        availability=cells[COL_AVAILABILITY] or None,
    )


def _guard_skipped(skipped: int, kept: int, *, source: str) -> None:
    total = skipped + kept
    if skipped < MIN_SKIPPED_FOR_ALARM or total == 0:
        return
    if skipped / total > MAX_SKIPPED_RATIO:
        raise StructureChanged(
            f"{source}: {skipped} од {total} редови се нечитливи - "
            "изворот веројатно сменил формат"
        )


__all__ = [
    "KamReader",
    "SourceUnavailable",
    "parse_pdf",
    "parse_shop_list",
    "split_discount_cell",
]
