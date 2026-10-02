"""Читач за платформата proverkanaceni.mk.

Повеќе синџири ја користат истата услуга, секој на свој поддомен:

    https://zito.proverkanaceni.mk/       Жито Лукс   (94 продавници)
    https://stokomak.proverkanaceni.mk/   Стокомак    (81 продавница)
    https://tamaro.proverkanaceni.mk/     Тамаро      (13 продавници)

Затоа читачот е еден, а синџирите се само поставки врз него. Ако утре уште
еден маркет се појави на платформата, доволно е уште една класа од три реда.

Истата платформа ја користеше и Тинекс (`ceni.tinex.mk`) - оттаму
параметрите `org`, `search` и `perPage` во спецификацијата. Ако Тинекс се
врати, веројатно ќе биде на поддомен тука.

Структура:
- Почетната страница има `<select name="org">` со сите продавници.
- Цените се на истата адреса со параметри:
      ?org=<id>&page=<n>&perPage=100&search=
- Табелата има 10 заглавија но 9 ќелии по ред: „Промотивна или попустна
  цена" е групно заглавие над „Цена со попуст". Затоа колоните се читаат
  по позиција, со проверка на заглавието.
- Цената со попуст и процентот се во ИСТА ќелија: „39 ден.Попуст:29%",
  исто како кај КАМ.
"""

from __future__ import annotations

from typing import ClassVar

from selectolax.parser import HTMLParser, Node

from app.catalog.geo import find_city_in
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
from app.readers.kam import split_discount_cell
from app.readers.parsing import (
    ValueParseError,
    normalize_space,
    parse_date_range,
    parse_decimal,
    parse_unit_price,
)

log = get_logger(__name__)

PLATFORM = "proverkanaceni.mk"

# Најголемата вредност што ја нуди самата платформа.
PAGE_SIZE = 100
# Заштита од бесконечна пагинација. Најголемите продавници имаат околу 35
# страници по 100 реда.
MAX_PAGES = 200

# Редоследот на ќелиите во еден ред (9, не 10 - види го коментарот горе).
COL_NAME = 0
COL_SALE = 1
COL_UNIT = 2
COL_DESCRIPTION = 3
COL_AVAILABILITY = 4
COL_REGULAR = 5
COL_DISCOUNT = 6
COL_PROMO_TYPE = 7
COL_DURATION = 8
CELL_COUNT = 9
# Редовите БЕЗ попуст ги немаат последните три ќелии воопшто - платформата
# не испишува празни. Затоа е доволно да стигнат до редовната цена.
MIN_CELLS = COL_REGULAR + 1

# Зборови што мора да ги има заглавието; ако ги нема, форматот се сменил.
HEADER_MARKERS = ("назив", "продажна", "единечна", "редовна")

MAX_SKIPPED_RATIO = 0.1
MIN_SKIPPED_FOR_ALARM = 10


class ProverkaNaCeniReader(PricelistReader):
    """Основа за сите синџири на платформата.

    Подкласата поставува само chain_code, chain_name и subdomain.
    """

    subdomain: ClassVar[str]
    # Една продавница бара околу 35-50 барања: платформата враќа најмногу
    # 100 реда по страница, без оглед што се бара со perPage. Сериски, Жито
    # со своите 94 продавници би траело околу шест часа.
    #
    # Намерно 2, не повеќе: ТРИТЕ синџири се на ист домаќин
    # (*.proverkanaceni.mk), па вкупната напоредност кон тој сервер е 3 x 2
    # = 6 врски - онолку колку што отвора и обичен прелистувач. Мерено,
    # едно барање трае околу 3,3 секунди; со 2 напоредни и пауза од 1,5s,
    # целата платформа се чита за околу два и пол часа.
    store_concurrency: ClassVar[int] = 2
    # Синџири што работат во еден град, а не го пишуваат во името на
    # продавницата. Се зема од сопствениот опис на синџирот, не се погодува.
    default_city: ClassVar[str | None] = None

    def __init__(self, client: PoliteClient | None = None) -> None:
        self._client = client or PoliteClient()

    @property
    def base_url(self) -> str:
        return f"https://{self.subdomain}.{PLATFORM}/"

    # ------------------------------------------------------------------
    async def discover_stores(self) -> list[StoreRef]:
        html, _ = await self._client.get_text(self.base_url)
        stores = parse_store_select(
            html, base_url=self.base_url, default_city=self.default_city
        )
        if not stores:
            raise StructureChanged(
                f"{self.base_url}: не најдов <select name='org'> со продавници"
            )
        log.info("%s: најдени %d продавници", self.chain_name, len(stores))
        return stores

    async def read_store(self, store: StoreRef) -> ReaderResult:
        rows: list[RawPriceRow] = []
        skipped = 0
        warnings: list[str] = []
        hashes: list[str] = []
        header_seen = False

        for page in range(1, MAX_PAGES + 1):
            html, page_hash = await self._client.get_text(
                self.base_url,
                params={
                    "org": store.external_id,
                    "page": str(page),
                    "perPage": str(PAGE_SIZE),
                    "search": "",
                },
            )
            hashes.append(page_hash)

            parsed, page_skipped, page_warnings, had_header = parse_page(
                html, source=f"{self.base_url}?org={store.external_id}&page={page}"
            )
            header_seen = header_seen or had_header
            rows.extend(parsed)
            skipped += page_skipped
            warnings.extend(page_warnings)

            # Пократка страница од побараната значи дека е последна.
            if len(parsed) + page_skipped < PAGE_SIZE:
                break
        else:
            warnings.append(f"застанав на {MAX_PAGES} страници; изворот има уште")

        if not header_seen:
            raise StructureChanged(
                f"{self.base_url}?org={store.external_id}: не го најдов заглавието "
                f"(очекував {list(HEADER_MARKERS)})"
            )

        if not rows:
            raise EmptyPricelist(
                f"{self.base_url}?org={store.external_id}: ценовникот нема ред"
            )

        _guard_skipped(skipped, len(rows), source=store.external_id)

        return ReaderResult(
            store=store,
            rows=rows,
            source_url=f"{self.base_url}?org={store.external_id}",
            # Платформата не дава датум на ценовникот.
            pricelist_date=None,
            content_hash=content_hash("".join(hashes)),
            rows_skipped=skipped,
            warnings=warnings,
        )


# --------------------------------------------------------------------------
# Парсирање
# --------------------------------------------------------------------------
def parse_store_select(
    html: str, *, base_url: str, default_city: str | None = None
) -> list[StoreRef]:
    """Ги чита продавниците од `<select name="org">`.

    Имињата се различни по синџир: „2 Трговски - Велес" кај Жито,
    „КУМАНОВО 2" кај Стокомак, „ТАМАРО МАРКЕТ 10" кај Тамаро. Градот се
    бара во името; ако не се најде, се зема default_city ако синџирот има.
    """
    tree = HTMLParser(html)
    select = tree.css_first('select[name="org"]')
    if select is None:
        return []

    stores: list[StoreRef] = []
    seen: set[str] = set()

    for option in select.css("option"):
        value = (option.attributes.get("value") or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)

        name = normalize_space(option.text()) or f"Продавница {value}"
        found = find_city_in(name)
        city = found[1] if found else default_city

        stores.append(
            StoreRef(
                external_id=value,
                name=name,
                city=city,
                source_url=f"{base_url}?org={value}",
            )
        )

    return stores


def parse_page(
    html: str, *, source: str
) -> tuple[list[RawPriceRow], int, list[str], bool]:
    """Чита една страница со цени.

    Враќа (редови, прескокнати, предупредувања, дали имало заглавие).
    """
    tree = HTMLParser(html)
    table = _find_table(tree)
    if table is None:
        # Страница без табела е крај на пагинацијата, не грешка.
        return [], 0, [], False

    headers = [normalize_space(th.text()).lower() for th in table.css("th")]
    joined = " ".join(headers)
    had_header = sum(marker in joined for marker in HEADER_MARKERS) >= 3

    rows: list[RawPriceRow] = []
    skipped = 0
    warnings: list[str] = []

    for tr in table.css("tr"):
        cells = [normalize_space(td.text()) for td in tr.css("td")]
        if not cells or not any(cells):
            continue
        try:
            rows.append(_row_from_cells(cells))
        except ValueParseError as exc:
            skipped += 1
            if len(warnings) < 5:
                warnings.append(f"{source}: прескокнат ред: {exc}")

    return rows, skipped, warnings, had_header


def _find_table(tree: HTMLParser) -> Node | None:
    best: Node | None = None
    best_rows = 0
    for table in tree.css("table"):
        count = len(table.css("tr"))
        if count > best_rows:
            best, best_rows = table, count
    return best


def _row_from_cells(raw_cells: list[str]) -> RawPriceRow:
    if len(raw_cells) < MIN_CELLS:
        raise ValueParseError(
            f"редот има {len(raw_cells)}, а треба најмалку {MIN_CELLS} ќелии"
        )

    # Дополнето до полна должина: ред без попуст завршува кај редовната цена.
    cells = raw_cells + [""] * (CELL_COUNT - len(raw_cells))

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


# --------------------------------------------------------------------------
# Синџирите
# --------------------------------------------------------------------------
class ZitoReader(ProverkaNaCeniReader):
    chain_code: ClassVar[str] = "zito"
    chain_name: ClassVar[str] = "Жито Лукс"
    website: ClassVar[str] = "https://zitoluks.com.mk/"
    subdomain: ClassVar[str] = "zito"


class StokomakReader(ProverkaNaCeniReader):
    chain_code: ClassVar[str] = "stokomak"
    chain_name: ClassVar[str] = "Стокомак"
    website: ClassVar[str] = "https://stokomak.proverkanaceni.mk/"
    subdomain: ClassVar[str] = "stokomak"


class TamaroReader(ProverkaNaCeniReader):
    chain_code: ClassVar[str] = "tamaro"
    chain_name: ClassVar[str] = "Тамаро"
    website: ClassVar[str] = "https://tamaro.mk/"
    subdomain: ClassVar[str] = "tamaro"
    # Синџирот сам се опишува како „Тамаро маркет Охрид"; имињата на
    # продавниците не го носат градот.
    default_city: ClassVar[str | None] = "Охрид"


__all__ = [
    "ProverkaNaCeniReader",
    "StokomakReader",
    "TamaroReader",
    "ZitoReader",
    "parse_page",
    "parse_store_select",
]
