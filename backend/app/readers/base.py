"""Заеднички интерфејс за сите читачи на ценовници.

Читачот е чиста функција од извор во податоци: не пишува во базата и не знае
за SQLAlchemy. Тоа го прави тестирањето врз зачувани примероци (fixtures)
можно без база и без жив сајт.

Правило: НИКОГАШ тивок празен резултат. Ако структурата не е онаа што ја
очекуваме - StructureChanged. Ако изворот не е достапен - SourceUnavailable.
Ако ценовникот е валиден но празен - EmptyPricelist. Повикувачот ги запишува
како PricelistRun со соодветен status.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, ClassVar


# --------------------------------------------------------------------------
# Грешки
# --------------------------------------------------------------------------
class ReaderError(Exception):
    """Основа за сите грешки на читачите."""


class SourceUnavailable(ReaderError):
    """Сајтот не одговара, врати HTTP грешка, или е блокиран (пр. Cloudflare)."""


class StructureChanged(ReaderError):
    """Ценовникот повеќе не изгледа како што очекуваме.

    Фрлај ова кога колона исчезнала, заглавието не се совпаѓа, или форматот
    на датум/цена е непознат. Ова е знак да се поправи читачот - не е
    нормална состојба и не смее да се претвори во празен резултат.
    """


class EmptyPricelist(ReaderError):
    """Структурата е во ред, но нема ниту еден ред со производ."""


# --------------------------------------------------------------------------
# Податочни типови
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class StoreRef:
    """Продавница откриена на изворниот сајт."""

    external_id: str
    name: str
    city: str | None = None
    address: str | None = None
    # Страницата/PDF-от од кој се чита ценовникот на оваа продавница.
    source_url: str | None = None
    # Сè што е специфично за изворот (пр. Кипер post_id, КАМ RelativePath).
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RawPriceRow:
    """Еден ред од ценовник, уште непреработен.

    Цените се Decimal или None - никогаш float, за да не се загуби точност
    на денари. Текстот се задржува како што е (name, description), затоа што
    корисникот го гледа точно така.
    """

    name: str
    description: str | None = None
    sale_price: Decimal | None = None  # продажна цена
    regular_price: Decimal | None = None  # редовна цена
    discount_price: Decimal | None = None  # цена со попуст
    discount_pct: Decimal | None = None  # попуст %
    unit_price: Decimal | None = None  # единечна цена како во ценовникот
    unit_price_label: str | None = None  # „ден/кг", „ден/л", ...
    promo_type_raw: str | None = None  # вид на продажно поттикнување
    valid_from: date | None = None
    valid_to: date | None = None
    availability: str | None = None
    # Ред од кој лист/страница дојде - помага при дебагирање на PDF-и.
    source_page: int | None = None

    @property
    def is_discount(self) -> bool:
        """Попуст = ред со пополнета цена со попуст.

        Нула НЕ е пополнета цена. Кипер праќа product_price „0" со попуст
        „-100%" за производи без внесена цена; прикажано како попуст тоа би
        го пратило купувачот во маркет по нешто што не постои за 0 денари.
        """
        return self.discount_price is not None and self.discount_price > 0

    @property
    def is_single_day(self) -> bool:
        """Еднодневен попуст: датум од == датум до."""
        return (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_from == self.valid_to
        )


@dataclass(slots=True)
class ReaderResult:
    """Исходот од читање на еден ценовник."""

    store: StoreRef
    rows: list[RawPriceRow]
    source_url: str
    # Датумот од заглавието на ценовникот, ако изворот го пишува.
    pricelist_date: date | None = None
    # Хеш од преземената содржина - ако е ист како претходно, ценовникот е
    # непроменет.
    content_hash: str | None = None
    # Редови што читачот ги видел но не можел да ги разбере. Броиме, не
    # премолчуваме: голем број тука значи дека нешто се сменило.
    rows_skipped: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def rows_total(self) -> int:
        return len(self.rows)

    @property
    def discount_rows(self) -> list[RawPriceRow]:
        return [row for row in self.rows if row.is_discount]


# --------------------------------------------------------------------------
# Интерфејс
# --------------------------------------------------------------------------
class PricelistReader(ABC):
    """Еден читач по синџир.

    chain_code мора да се совпаѓа со Chain.code во базата - така редот во
    базата го најдува својот модул.
    """

    chain_code: ClassVar[str]
    chain_name: ClassVar[str]
    website: ClassVar[str]

    @abstractmethod
    async def discover_stores(self) -> list[StoreRef]:
        """Ја враќа тековната листа продавници од изворниот сајт.

        Фрла SourceUnavailable или StructureChanged; празна листа е грешка,
        не резултат.
        """

    @abstractmethod
    async def read_store(self, store: StoreRef) -> ReaderResult:
        """Го чита ценовникот на една продавница."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.chain_code}>"
