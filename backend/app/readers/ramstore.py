"""Читач за Рамстор - https://ramstore.com.mk/marketi/

Структура на изворот:
- /marketi/ дава 36 продавници. Линковите НЕ се <a href>, туку
  "<button onclick="location.href='.../marketi/<slug>/';">" - затоа се
  вадат од onclick, не од href.
- Секоја продавница има една страница со целата табела, без пагинација.
  Табелата е голема (~16.700 реда, ~7-8 MB) - цел асортиман, не само попусти.
- Заглавие: "Датум и време на последно ажурирање на цените: 02.10.2026 4:00AM".
- Колоната "ЦЕНА СО ПОПУСТ" содржи и цена и процент: "45.00-23.73%".
- Типови акција: АКЦИСКА ПРОДАЖБА, ЛОЈАЛНОСТ (само со картичка), ПОНУДА.

Редот се смета за попуст САМО ако има пополнета цена со попуст - дел од
редовите имаат тип на акција без цена со попуст и тие не се попусти.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import ClassVar
from urllib.parse import urljoin

# Lexbor наместо Modest: Modest е отстранет во selectolax 1.0.
# API-то е исто за css()/text()/attributes, па парсирањето не се менува -
# тоа го потврдуваат тестовите врз зачуваните ценовници.
from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import LexborNode as Node

from app.core.logging import get_logger
from app.readers.base import (
    EmptyPricelist,
    PricelistReader,
    RawPriceRow,
    ReaderResult,
    StoreRef,
    StructureChanged,
)
from app.readers.parsing import (
    Column,
    ValueParseError,
    build_column_map,
    cell,
    normalize_space,
    parse_date,
    parse_date_range,
    parse_decimal,
    parse_percent,
    parse_unit_price,
)

log = get_logger(__name__)

BASE_URL = "https://ramstore.com.mk/"
STORES_URL = urljoin(BASE_URL, "marketi/")

HEADER_SPEC = {
    "НАЗИВ НА ПРОИЗВОД": Column.NAME,
    "ПРОДАЖНА ЦЕНА": Column.SALE_PRICE,
    "ЕДИНЕЧНА ЦЕНА": Column.UNIT_PRICE,
    "ОПИС НА ПРОИЗВОД": Column.DESCRIPTION,
    "ДОСТАПНОСТ НА ПРОИЗВОД": Column.AVAILABILITY,
    "РЕДОВНА ЦЕНА": Column.REGULAR_PRICE,
    "ЦЕНА СО ПОПУСТ": Column.DISCOUNT_PRICE,
    "ПОЕНИ": Column.POINTS,
    "ТИП НА АКЦИЈА": Column.PROMO_TYPE,
    "ВРЕМЕТРАЕЊЕ НА АКЦИЈА": Column.DURATION,
}

REQUIRED_COLUMNS = (
    Column.NAME,
    Column.SALE_PRICE,
    Column.DESCRIPTION,
    Column.REGULAR_PRICE,
    Column.DISCOUNT_PRICE,
    Column.PROMO_TYPE,
)

MAX_SKIPPED_RATIO = 0.1
MIN_SKIPPED_FOR_ALARM = 10

# "<button onclick="location.href='https://ramstore.com.mk/marketi/ramstore-vardar/';">"
_STORE_ONCLICK = re.compile(
    r"location\.href\s*=\s*['\"](?P<url>[^'\"]*/marketi/(?P<slug>[^/'\"]+)/?)['\"]"
)

# "45.00-23.73%" -> цена и процент во една ќелија.
_PRICE_WITH_PCT = re.compile(r"^(?P<price>[\d\s.,]+?)\s*-\s*(?P<pct>[\d.,]+)\s*%$")

_LAST_UPDATE = re.compile(
    r"последно\s+ажурирање\s+на\s+цените\s*:?\s*(?P<rest>.{0,40})", re.IGNORECASE
)


class RamstoreReader(PricelistReader):
    chain_code: ClassVar[str] = "ramstore"
    chain_name: ClassVar[str] = "Рамстор"
    website: ClassVar[str] = BASE_URL

    def __init__(self, client=None) -> None:
        from app.readers.http import PoliteClient

        self._client = client or PoliteClient()

    async def discover_stores(self) -> list[StoreRef]:
        html, _ = await self._client.get_text(STORES_URL)
        stores = parse_store_index(html, base_url=STORES_URL)
        if not stores:
            raise StructureChanged(
                f"{STORES_URL}: не најдов ниту една продавница "
                "(линковите се во button onclick, не во href)"
            )
        log.info("Рамстор: најдени %d продавници", len(stores))
        return stores

    async def read_store(self, store: StoreRef) -> ReaderResult:
        url = store.source_url or urljoin(STORES_URL, f"{store.external_id}/")
        html, page_hash = await self._client.get_text(url)

        rows, pricelist_date, skipped, warnings = parse_pricelist(html, source=url)

        if not rows:
            raise EmptyPricelist(f"{url}: ценовникот нема ниту еден ред")

        total = skipped + len(rows)
        if skipped >= MIN_SKIPPED_FOR_ALARM and skipped / total > MAX_SKIPPED_RATIO:
            raise StructureChanged(
                f"{url}: {skipped} од {total} редови се нечитливи - "
                "изворот веројатно сменил формат"
            )

        return ReaderResult(
            store=store,
            rows=rows,
            source_url=url,
            pricelist_date=pricelist_date,
            content_hash=page_hash,
            rows_skipped=skipped,
            warnings=warnings,
        )


# --------------------------------------------------------------------------
# Парсирање
# --------------------------------------------------------------------------
def parse_store_index(html: str, *, base_url: str = STORES_URL) -> list[StoreRef]:
    """Ја чита листата продавници од /marketi/.

    Името, адресата и работното време се во истата картичка со копчето.
    """
    tree = HTMLParser(html)
    found: dict[str, StoreRef] = {}

    for node in tree.css("[onclick]"):
        match = _STORE_ONCLICK.search(node.attributes.get("onclick") or "")
        if match is None:
            continue
        slug = match.group("slug")
        if slug in found:
            continue

        name, address = _card_details(node)
        found[slug] = StoreRef(
            external_id=slug,
            name=name or slug.replace("-", " ").upper(),
            city=_city_from_address(address),
            address=address or None,
            source_url=urljoin(base_url, match.group("url")),
        )

    return list(found.values())


def _card_details(button: Node) -> tuple[str, str]:
    """Го бара името и адресата во картичката над копчето."""
    node = button.parent
    for _ in range(5):
        if node is None:
            break
        text = normalize_space(node.text())
        if "Адреса" in text:
            return _name_from_card(node), _address_from_text(text)
        node = node.parent
    return "", ""


def _name_from_card(card: Node) -> str:
    for tag in ("h1", "h2", "h3", "h4", "strong"):
        heading = card.css_first(tag)
        if heading is not None:
            name = normalize_space(heading.text())
            if name:
                return name
    return ""


def _address_from_text(text: str) -> str:
    """Од "... Адреса: Ул. X бр.1 Центар,Скопје Работно време: ..." вади адреса."""
    if "Адреса" not in text:
        return ""
    tail = text.split("Адреса", 1)[1].lstrip(": ")
    for stop in ("Работно време", "ВИДИ", "Види"):
        if stop in tail:
            tail = tail.split(stop, 1)[0]
    return normalize_space(tail)


def _city_from_address(address: str) -> str | None:
    """Градот е последниот дел по запирка: "... Центар,Скопје" -> "Скопје".

    Кај дел од продавниците нема општина: "Ул. Ташко Караџа бр.1А, Скопје".
    """
    if not address:
        return None
    if "," in address:
        return normalize_space(address.rsplit(",", 1)[1]) or None
    return None


def parse_pricelist(
    html: str, *, source: str
) -> tuple[list[RawPriceRow], date | None, int, list[str]]:
    """Ја чита табелата со ценовник од страницата на продавница."""
    tree = HTMLParser(html)

    table = _find_pricelist_table(tree)
    if table is None:
        raise StructureChanged(f"{source}: не најдов табела со ценовник")

    rows = table.css("tr")
    headers = [normalize_space(th.text()) for th in rows[0].css("th")] if rows else []
    if not headers:
        raise StructureChanged(f"{source}: табелата нема заглавие")

    mapping = build_column_map(headers, HEADER_SPEC, REQUIRED_COLUMNS, source=source)

    parsed: list[RawPriceRow] = []
    skipped = 0
    warnings: list[str] = []

    for tr in rows[1:]:
        cells = [normalize_space(td.text()) for td in tr.css("td")]
        if not cells or not any(cells):
            continue
        try:
            parsed.append(_row_from_cells(cells, mapping))
        except ValueParseError as exc:
            skipped += 1
            if len(warnings) < 5:
                warnings.append(f"прескокнат ред: {exc}")

    return parsed, _parse_last_update(tree), skipped, warnings


def _find_pricelist_table(tree: HTMLParser) -> Node | None:
    best: Node | None = None
    best_rows = 0
    for table in tree.css("table"):
        if not table.css("th"):
            continue
        count = len(table.css("tr"))
        if count > best_rows:
            best, best_rows = table, count
    return best


def _parse_last_update(tree: HTMLParser) -> date | None:
    """Чита "Датум и време на последно ажурирање на цените: 02.10.2026 4:00AM"."""
    body = tree.body or tree.root
    if body is None:
        return None
    match = _LAST_UPDATE.search(normalize_space(body.text()))
    if match is None:
        return None
    try:
        return parse_date(match.group("rest"))
    except ValueParseError as exc:
        log.warning("Рамстор: не го прочитав датумот на ажурирање: %s", exc)
        return None


def split_discount_cell(raw: str) -> tuple[Decimal | None, Decimal | None]:
    """Ја дели ќелијата "45.00-23.73%" на (цена, процент).

    Ако нема процент, целата ќелија е цена.
    """
    text = normalize_space(raw)
    if not text:
        return None, None
    match = _PRICE_WITH_PCT.match(text)
    if match:
        return parse_decimal(match.group("price")), parse_percent(match.group("pct"))
    return parse_decimal(text), None


def _row_from_cells(cells_: list[str], mapping: dict[Column, int]) -> RawPriceRow:
    name = cell(cells_, mapping, Column.NAME)
    if not name:
        raise ValueParseError("редот е без назив на производ")

    discount_price, discount_pct = split_discount_cell(
        cell(cells_, mapping, Column.DISCOUNT_PRICE)
    )
    unit_price, unit_label = parse_unit_price(cell(cells_, mapping, Column.UNIT_PRICE))
    valid_from, valid_to = parse_date_range(cell(cells_, mapping, Column.DURATION))

    return RawPriceRow(
        name=name,
        description=cell(cells_, mapping, Column.DESCRIPTION) or None,
        sale_price=parse_decimal(cell(cells_, mapping, Column.SALE_PRICE)),
        regular_price=parse_decimal(cell(cells_, mapping, Column.REGULAR_PRICE)),
        discount_price=discount_price,
        discount_pct=discount_pct,
        unit_price=unit_price,
        unit_price_label=unit_label,
        promo_type_raw=cell(cells_, mapping, Column.PROMO_TYPE) or None,
        valid_from=valid_from,
        valid_to=valid_to,
        availability=cell(cells_, mapping, Column.AVAILABILITY) or None,
    )
