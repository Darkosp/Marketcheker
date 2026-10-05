"""Читач за Веро - https://pricelist.vero.com.mk/

Структура на изворот:
- index.html дава листа продавници, секоја со линк "{id}_1.html".
  Во листата има и ЏАМБО продавници - истиот ценовник, друг бренд.
- Страницата на продавница има три табели; трета (индекс 2) е ценовникот.
  Пагинација: "Страна N" со линк кон "{id}_{N+1}.html", 500 реда по страница.
- Заглавие: "Последно ажурирање: 2/10/2026 7:02" -> датум на ценовникот.
- Ценовникот е цел асортиман (~10.100 реда за една продавница, ~21 страница),
  од кои ~1.200 со попуст. Попустите се групирани на првите страници, но
  читачот не се потпира на тоа - враќа сè, а што е попуст се одлучува по
  RawPriceRow.is_discount.

Внимание: index.html има расипан HTML кај дел од записите
("<a ...><H1>ЏАМБО 2</a></H1>" - затворачките тагови се наопаку), што
создава дупли <a> јазли. Затоа линковите се деduplицираат по href.
"""

from __future__ import annotations

import re
from datetime import date
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
from app.readers.http import PoliteClient, content_hash
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

BASE_URL = "https://pricelist.vero.com.mk/"

# Заглавија како што ги пишува Веро -> канонски колони.
HEADER_SPEC = {
    "НАЗИВ НА СТОКА": Column.NAME,
    "ПРОДАЖНА ЦЕНА": Column.SALE_PRICE,
    "ЕДИНЕЧНА ЦЕНА": Column.UNIT_PRICE,
    "ДОСТАПНОСТ ВО ПРОДАЖЕН ОБЈЕКТ": Column.AVAILABILITY,
    "ОПИС НА СТОКА": Column.DESCRIPTION,
    "РЕДОВНА ЦЕНА": Column.REGULAR_PRICE,
    "ЦЕНА СО ПОПУСТ": Column.DISCOUNT_PRICE,
    "ПОПУСТ": Column.DISCOUNT_PCT,
    "ВИД НА ПРОДАЖНО ПОТИКНУВАЊЕ": Column.PROMO_TYPE,
    "ВРЕМЕТРАЕЊЕ НА ПРОМОЦИЈА ИЛИ ПОПУСТ": Column.DURATION,
}

REQUIRED_COLUMNS = (
    Column.NAME,
    Column.SALE_PRICE,
    Column.DESCRIPTION,
    Column.REGULAR_PRICE,
    Column.DISCOUNT_PRICE,
)

# Колку страници најмногу следиме по продавница. Заштита од циклус во
# пагинацијата; при 500 реда по страница тоа е 100.000 реда.
MAX_PAGES = 200

# Над овој дел нечитливи редови сметаме дека форматот се сменил.
MAX_SKIPPED_RATIO = 0.1
MIN_SKIPPED_FOR_ALARM = 10

# "Страна 2" - бројот може да е по стрелката за назад.
_PAGE_LABEL = re.compile(r"Страна\s*(\d+)")


class VeroReader(PricelistReader):
    chain_code: ClassVar[str] = "vero"
    chain_name: ClassVar[str] = "Веро"
    website: ClassVar[str] = BASE_URL

    def __init__(self, client: PoliteClient | None = None) -> None:
        self._client = client or PoliteClient()

    # ------------------------------------------------------------------
    async def discover_stores(self) -> list[StoreRef]:
        html, _ = await self._client.get_text(BASE_URL)
        stores = parse_store_index(html, base_url=BASE_URL)
        if not stores:
            raise StructureChanged(
                f"{BASE_URL}: не најдов ниту една продавница во index.html"
            )
        log.info("Веро: најдени %d продавници", len(stores))
        return stores

    async def read_store(self, store: StoreRef) -> ReaderResult:
        first_url = store.source_url or urljoin(BASE_URL, f"{store.external_id}_1.html")

        rows: list[RawPriceRow] = []
        skipped = 0
        warnings: list[str] = []
        pricelist_date: date | None = None
        hashes: list[str] = []

        url: str | None = first_url
        seen_urls: set[str] = set()
        page_number = 0

        while url and page_number < MAX_PAGES:
            if url in seen_urls:
                warnings.append(f"пагинацијата се врти во круг кај {url}")
                break
            seen_urls.add(url)
            page_number += 1

            html, page_hash = await self._client.get_text(url)
            hashes.append(page_hash)

            # current_page се изведува од самото URL во parse_pricelist_page -
            # page_number тука е само бројач на преземени страници.
            page = parse_pricelist_page(html, source=url)
            rows.extend(page.rows)
            skipped += page.skipped
            warnings.extend(page.warnings)
            if pricelist_date is None:
                pricelist_date = page.pricelist_date

            url = urljoin(url, page.next_href) if page.next_href else None

        if page_number >= MAX_PAGES and url:
            warnings.append(f"застанав на {MAX_PAGES} страници; изворот има уште")

        if not rows:
            raise EmptyPricelist(f"{first_url}: ценовникот нема ниту еден ред")

        _guard_skipped(skipped, len(rows), source=first_url)

        return ReaderResult(
            store=store,
            rows=rows,
            source_url=first_url,
            pricelist_date=pricelist_date,
            # Хеш од хешевите на сите страници: се менува ако било која смени.
            content_hash=content_hash("".join(hashes)),
            rows_skipped=skipped,
            warnings=warnings,
        )


# --------------------------------------------------------------------------
# Парсирање - чисти функции, тестирани врз зачувани примероци
# --------------------------------------------------------------------------
def parse_store_index(html: str, *, base_url: str = BASE_URL) -> list[StoreRef]:
    """Ја чита листата продавници од index.html.

    Записите се "<a href='89_1.html'><H1>ВЕРО 1</H1></a><br><H3>адреса</H3>".
    Кај расипаните записи истиот href се појавува двапати, еден со празен
    текст - затоа dedupлицираме и го задржуваме записот со име.
    """
    tree = HTMLParser(html)
    found: dict[str, StoreRef] = {}

    for anchor in tree.css("a"):
        href = (anchor.attributes.get("href") or "").strip()
        if not href.endswith("_1.html"):
            continue
        external_id = href.split("_", 1)[0]
        if not external_id.isdigit():
            continue

        name = normalize_space(anchor.text())
        address = _address_near(anchor)

        existing = found.get(external_id)
        if existing is not None and not name:
            # Расипаниот дупликат без име - задржи го постоечкиот.
            continue
        if existing is not None and existing.name and not address:
            continue

        found[external_id] = StoreRef(
            external_id=external_id,
            name=name or f"Веро {external_id}",
            city=_city_from_address(address),
            address=address or None,
            source_url=urljoin(base_url, href),
        )

    return list(found.values())


def _address_near(anchor: Node) -> str:
    """Ја бара адресата (<H3>) во истата ќелија со линкот."""
    parent = anchor.parent
    for _ in range(4):
        if parent is None:
            return ""
        heading = parent.css_first("h3")
        if heading is not None:
            return normalize_space(heading.text())
        parent = parent.parent
    return ""


def _city_from_address(address: str) -> str | None:
    """Градот/општината е по цртичката: "Бул. ... бр.111 – Аеродром".

    Веро пишува општина за скопските продавници (Аеродром, Карпош, Центар),
    а град за останатите (Тетово, Битола). Сведувањето на општините во
    "Скопје" се прави при зачувување, не тука - читачот го враќа текстот
    како што го дава изворот.
    """
    if not address:
        return None
    for dash in ("–", "—", " - ", "-"):
        if dash in address:
            tail = address.rsplit(dash, 1)[1]
            return normalize_space(tail) or None
    return None


class _ParsedPage:
    """Исход од парсирање на една страница од ценовник."""

    __slots__ = ("next_href", "pricelist_date", "rows", "skipped", "warnings")

    def __init__(
        self,
        rows: list[RawPriceRow],
        *,
        pricelist_date: date | None,
        next_href: str | None,
        skipped: int,
        warnings: list[str],
    ) -> None:
        self.rows = rows
        self.pricelist_date = pricelist_date
        self.next_href = next_href
        self.skipped = skipped
        self.warnings = warnings


def parse_pricelist_page(
    html: str, *, source: str, current_page: int | None = None
) -> _ParsedPage:
    """Чита една страница "{id}_{n}.html".

    current_page го одредува тоа кој линк е "следен". Ако не е дадено, се
    чита од URL-то во source - тоа е посигурно од текстот на страницата.
    """
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

    if current_page is None:
        current_page = _page_number_from_href(source) or _current_page_number(tree)

    return _ParsedPage(
        parsed,
        pricelist_date=_parse_last_update(tree),
        next_href=_find_next_page(tree, current_page),
        skipped=skipped,
        warnings=warnings,
    )


def _find_pricelist_table(tree: HTMLParser) -> Node | None:
    """Ценовникот е табелата со <th> заглавие и најмногу редови."""
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
    """Чита "Последно ажурирање: 1/11/2025 8:34"."""
    for node in tree.css("td"):
        text = normalize_space(node.text())
        if text.startswith("Последно ажурирање"):
            try:
                return parse_date(text)
            except ValueParseError as exc:
                log.warning("Веро: не го прочитав датумот на ажурирање: %s", exc)
                return None
    return None


def _find_next_page(tree: HTMLParser, current_page: int) -> str | None:
    """Линкот кон следната страница е "{id}_{n}.html" со поголем број.

    Страниците од втората нанапред имаат и стрелка назад и стрелка напред;
    на последната нема напред. Затоа гледаме само линкови со број поголем од
    тековниот, и го земаме најмалиот од тие.
    """
    best: tuple[int, str] | None = None

    for anchor in tree.css("a"):
        href = (anchor.attributes.get("href") or "").strip()
        page = _page_number_from_href(href)
        if page is None or page <= current_page:
            continue
        if best is None or page < best[0]:
            best = (page, href)

    return best[1] if best else None


def _current_page_number(tree: HTMLParser) -> int:
    """Резервно читање на тековната страница од текстот "Страна N".

    Бројот се бара каде и да е во ќелијата: од втората страница нанапред
    ќелијата почнува со стрелката за назад ("🠈 Страна 2 🠊"), а не со
    зборот "Страна".
    """
    for node in tree.css("td"):
        match = _PAGE_LABEL.search(normalize_space(node.text()))
        if match:
            return int(match.group(1))
    return 1


def _page_number_from_href(href: str) -> int | None:
    """Бројот на страницата од "89_3.html" или цела таква URL адреса."""
    name = href.rsplit("/", 1)[-1]
    if not name.endswith(".html") or "_" not in name:
        return None
    tail = name.removesuffix(".html").rsplit("_", 1)[1]
    return int(tail) if tail.isdigit() else None


def _row_from_cells(cells_: list[str], mapping: dict[Column, int]) -> RawPriceRow:
    name = cell(cells_, mapping, Column.NAME)
    if not name:
        raise ValueParseError("редот е без назив на стока")

    unit_price, unit_label = parse_unit_price(cell(cells_, mapping, Column.UNIT_PRICE))
    valid_from, valid_to = parse_date_range(cell(cells_, mapping, Column.DURATION))

    return RawPriceRow(
        name=name,
        description=cell(cells_, mapping, Column.DESCRIPTION) or None,
        sale_price=parse_decimal(cell(cells_, mapping, Column.SALE_PRICE)),
        regular_price=parse_decimal(cell(cells_, mapping, Column.REGULAR_PRICE)),
        discount_price=parse_decimal(cell(cells_, mapping, Column.DISCOUNT_PRICE)),
        discount_pct=parse_percent(cell(cells_, mapping, Column.DISCOUNT_PCT)),
        unit_price=unit_price,
        unit_price_label=unit_label,
        promo_type_raw=cell(cells_, mapping, Column.PROMO_TYPE) or None,
        valid_from=valid_from,
        valid_to=valid_to,
        availability=cell(cells_, mapping, Column.AVAILABILITY) or None,
    )


def _guard_skipped(skipped: int, kept: int, *, source: str) -> None:
    """Неколку чудни реда се толерираат; многу значи сменет формат."""
    total = skipped + kept
    if skipped < MIN_SKIPPED_FOR_ALARM or total == 0:
        return
    if skipped / total > MAX_SKIPPED_RATIO:
        raise StructureChanged(
            f"{source}: {skipped} од {total} редови се нечитливи - "
            "изворот веројатно сменил формат"
        )
