"""Читач за Кипер - https://kipper.mk/mk/marketet/

Структура на изворот:
- /mk/marketet/ дава линкови кон страници на продавници со патека
  „/mk/kipper-<број>-<место>/".
- Страницата на продавница има празна табела „#products"; редовите доаѓаат
  со POST на /wp-admin/admin-ajax.php (DataTables, server-side). post_id-то
  е во класата на <body>: „postid-23018".
- Одговорот е JSON со речници (не низи), со полиња:
      product_name, product_name_alt (албански/латиница),
      product_price (продажна), product_price_normal (редовна),
      product_price_discount_percentage ("-6%"),
      product_price_weight + product_type_alt ("1КГ=") - единечна цена,
      product_subgroup (опис), promotion_datetime_from/to

Сајтот е зад Cloudflare, но robots.txt го дозволува точно овој endpoint:
    Allow: /wp-admin/admin-ajax.php
Проверено од сервер на 2026-10-02: работи без JS challenge.

Попуст = има product_price_discount_percentage. Тогаш product_price е
цената со попуст, а product_price_normal е редовната.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, ClassVar
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

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
from app.readers.http import PoliteClient, content_hash
from app.readers.parsing import (
    ValueParseError,
    normalize_space,
    parse_date,
    parse_decimal,
    parse_percent,
)

log = get_logger(__name__)

BASE_URL = "https://kipper.mk/"
STORES_URL = urljoin(BASE_URL, "mk/marketet/")
AJAX_URL = urljoin(BASE_URL, "wp-admin/admin-ajax.php")

# Колку редови се бараат во едно барање. Најголемата продавница има ~2.100,
# па еден повик стига; сепак следиме recordsTotal и земаме уште ако треба.
PAGE_SIZE = 5000
MAX_REQUESTS = 10

# „/mk/kipper-134-kumanove/" - бројот е ознака на продавницата кај Кипер,
# но клучот што ни треба за AJAX е post_id од страницата.
_STORE_PATH = re.compile(r"^https://kipper\.mk/mk/(kipper-[^/]+)/?$", re.IGNORECASE)
_POST_ID = re.compile(r"\bpostid-(\d+)\b")

MAX_SKIPPED_RATIO = 0.1
MIN_SKIPPED_FOR_ALARM = 10


class KipperReader(PricelistReader):
    chain_code: ClassVar[str] = "kipper"
    chain_name: ClassVar[str] = "Кипер"
    website: ClassVar[str] = BASE_URL

    def __init__(self, client: PoliteClient | None = None) -> None:
        self._client = client or PoliteClient()

    # ------------------------------------------------------------------
    async def discover_stores(self) -> list[StoreRef]:
        html, _ = await self._client.get_text(STORES_URL)
        stores = parse_store_index(html)
        if not stores:
            raise StructureChanged(
                f"{STORES_URL}: не најдов ниту една продавница "
                "(очекував линкови /mk/kipper-<број>-<место>/)"
            )
        log.info("Кипер: најдени %d продавници", len(stores))
        return stores

    async def read_store(self, store: StoreRef) -> ReaderResult:
        url = store.source_url or urljoin(BASE_URL, f"mk/{store.external_id}/")

        post_id = store.extra.get("post_id")
        if not post_id:
            # post_id-то е во страницата; го бараме само ако не е веќе познато.
            page_html, _ = await self._client.get_text(url)
            post_id = find_post_id(page_html)
            if post_id is None:
                raise StructureChanged(f"{url}: не најдов postid- во страницата")

        rows: list[dict[str, Any]] = []
        total: int | None = None

        for request_number in range(MAX_REQUESTS):
            payload = await self._client.post_json(
                AJAX_URL,
                data={
                    "action": "get_products_data",
                    "post_id": str(post_id),
                    "draw": str(request_number + 1),
                    "start": str(len(rows)),
                    "length": str(PAGE_SIZE),
                },
                headers={"X-Requested-With": "XMLHttpRequest", "Referer": url},
            )
            batch, total = _unpack(payload, source=url)
            if not batch:
                break
            rows.extend(batch)
            if total is not None and len(rows) >= total:
                break

        if total is not None and len(rows) < total:
            log.warning(
                "Кипер %s: земени %d од %d редови", store.external_id, len(rows), total
            )

        if not rows:
            raise EmptyPricelist(f"{url}: ценовникот нема ниту еден ред")

        parsed, skipped, warnings = _parse_rows(rows)
        _guard_skipped(skipped, len(parsed), source=url)

        return ReaderResult(
            store=store,
            rows=parsed,
            source_url=url,
            pricelist_date=_pricelist_date(parsed),
            content_hash=content_hash(repr(rows)),
            rows_skipped=skipped,
            warnings=warnings,
        )


# --------------------------------------------------------------------------
# Парсирање
# --------------------------------------------------------------------------
def parse_store_index(html: str) -> list[StoreRef]:
    """Ги чита продавниците од /mk/marketet/."""
    tree = HTMLParser(html)
    found: dict[str, StoreRef] = {}

    for anchor in tree.css("a"):
        href = (anchor.attributes.get("href") or "").strip()
        match = _STORE_PATH.match(href)
        if match is None:
            continue
        slug = match.group(1)
        if slug in found:
            continue

        name = normalize_space(anchor.text()) or slug.replace("-", " ").upper()
        found[slug] = StoreRef(
            external_id=slug,
            name=name,
            city=_city_from_slug(slug),
            source_url=href,
        )

    return list(found.values())


def find_post_id(html: str) -> str | None:
    """post_id-то што го бара AJAX-от е во класата на <body>: „postid-23018"."""
    match = _POST_ID.search(html)
    return match.group(1) if match else None


# Местата во патеката се пишани албански; ова ги дава македонските имиња.
_PLACE_NAMES = {
    "shkup": "Скопје",
    "skopje": "Скопје",
    "kumanove": "Куманово",
    "strumice": "Струмица",
    "kocani": "Кочани",
    "gostivar": "Гостивар",
    "diber": "Дебар",
    "prilep": "Прилеп",
    "tetove": "Тетово",
    "shtip": "Штип",
    "zajas": "Зајас",
    "vinice": "Виница",
    "kallnik": "Калник",
    "porooj": "Порој",
    "studenican": "Студеничани",
    "nikushtak": "Никуштак",
    "kisela-voda": "Скопје",
    "maxhari": "Скопје",
    "butel": "Скопје",
    "zelezara": "Скопје",
    "sveti-nikole": "Свети Николе",
}


def _city_from_slug(slug: str) -> str | None:
    """„kipper-147-shkup-maxhari" -> „Скопје".

    Патеката е „kipper-<број>-<место>"; местото може да има повеќе делови.
    Непознато место останува непознато - не се погодува.
    """
    parts = slug.lower().split("-")[2:]
    if not parts:
        return None
    # Прво цела опашка („kisela-voda"), па првиот дел („shkup").
    for candidate in ("-".join(parts), parts[0]):
        name = _PLACE_NAMES.get(candidate)
        if name:
            return name
    return None


def _unpack(payload: Any, *, source: str) -> tuple[list[dict[str, Any]], int | None]:
    """Го вади списокот редови од DataTables одговорот."""
    if not isinstance(payload, dict):
        raise StructureChanged(f"{source}: AJAX одговорот не е објект")

    rows = payload.get("data")
    if rows is None:
        raise StructureChanged(
            f"{source}: нема поле 'data' во одговорот; клучеви: {sorted(payload)}"
        )
    if not isinstance(rows, list):
        raise StructureChanged(f"{source}: полето 'data' не е список")
    if rows and not isinstance(rows[0], dict):
        raise StructureChanged(
            f"{source}: редовите не се објекти туку {type(rows[0]).__name__}"
        )

    total = payload.get("recordsTotal")
    return rows, int(total) if isinstance(total, int | str) and str(
        total
    ).isdigit() else None


def _parse_rows(rows: list[dict[str, Any]]) -> tuple[list[RawPriceRow], int, list[str]]:
    parsed: list[RawPriceRow] = []
    skipped = 0
    warnings: list[str] = []

    for row in rows:
        try:
            parsed.append(_row_from_json(row))
        except ValueParseError as exc:
            skipped += 1
            if len(warnings) < 5:
                warnings.append(f"прескокнат ред: {exc}")

    return parsed, skipped, warnings


def _row_from_json(row: dict[str, Any]) -> RawPriceRow:
    name = normalize_space(row.get("product_name"))
    if not name:
        raise ValueParseError("редот е без назив на артикал")

    sale_price = parse_decimal(row.get("product_price"))
    regular_price = parse_decimal(row.get("product_price_normal"))
    pct = parse_percent(row.get("product_price_discount_percentage"))

    # Попуст има само кога изворот даде процент; тогаш продажната цена Е
    # цената со попуст.
    discount_price = sale_price if pct is not None else None

    return RawPriceRow(
        name=name,
        description=normalize_space(row.get("product_subgroup")) or None,
        sale_price=sale_price,
        regular_price=regular_price,
        discount_price=discount_price,
        discount_pct=pct,
        unit_price=parse_decimal(row.get("product_price_weight")),
        unit_price_label=_unit_label(row.get("product_type_alt")),
        promo_type_raw=None,  # Кипер не дава вид на поттикнување во JSON-от
        valid_from=_date(row.get("promotion_datetime_from")),
        valid_to=_date(row.get("promotion_datetime_to")),
        availability=None,
    )


def _unit_label(raw: Any) -> str | None:
    """„1КГ=" или „=1Л" -> „1кг" / „1л"."""
    text = normalize_space(str(raw) if raw is not None else "")
    text = text.replace("=", "").strip()
    return text.lower() or None


def _date(raw: Any) -> date | None:
    """Кипер дава ISO датум или null."""
    if not raw:
        return None
    text = normalize_space(str(raw))
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    try:
        return parse_date(text)
    except ValueParseError:
        return None


def _pricelist_date(rows: list[RawPriceRow]) -> date | None:
    """Кипер НЕ дава датум на ценовникот - затоа враќа None.

    Првата верзија земаше најдоцниот почеток на акција, но тоа дава датум
    во иднина (акции што почнуваат наредната недела) и состојбата
    покажуваше ценовник од утре. Подобро празно отколку погрешно.

    Ако изворот почне да дава „последно ажурирање", тука е местото.
    """
    _ = rows
    return None


def _guard_skipped(skipped: int, kept: int, *, source: str) -> None:
    total = skipped + kept
    if skipped < MIN_SKIPPED_FOR_ALARM or total == 0:
        return
    if skipped / total > MAX_SKIPPED_RATIO:
        raise StructureChanged(
            f"{source}: {skipped} од {total} редови се нечитливи - "
            "изворот веројатно сменил формат"
        )


__all__ = ["KipperReader", "SourceUnavailable", "find_post_id", "parse_store_index"]
