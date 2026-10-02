"""Мапирање на текстот за вид на поттикнување во PromoType.

Секој маркет го пишува со свои зборови. Оригиналниот текст секогаш се чува
во PriceRow.promo_type_raw, па ако мапирањето е погрешно, информацијата не е
изгубена. Непознат текст дава PromoType.OTHER и предупредување во логот -
никогаш тивко не се претвора во обичен попуст.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.models.enums import PromoType
from app.readers.parsing import normalize_for_match

log = get_logger(__name__)

# Точни совпаѓања, како што ги пишуваат изворите.
_EXACT: dict[str, PromoType] = {
    # Веро
    "промотивна цена": PromoType.DISCOUNT,
    # Рамстор
    "акциска продажба": PromoType.DISCOUNT,
    "лојалност": PromoType.LOYALTY,
    "понуда": PromoType.DISCOUNT,
}

# Делчиња што ги фаќаат варијациите (редоследот е важен: лојалност прво,
# за "акциска продажба со картичка" да не падне во обичен попуст).
_CONTAINS: tuple[tuple[str, PromoType], ...] = (
    ("лојал", PromoType.LOYALTY),
    ("картич", PromoType.LOYALTY),
    ("1+1", PromoType.MULTIBUY),
    ("2+1", PromoType.MULTIBUY),
    ("3+1", PromoType.MULTIBUY),
    ("количин", PromoType.MULTIBUY),
    ("акциј", PromoType.DISCOUNT),
    ("акциск", PromoType.DISCOUNT),
    ("промотивн", PromoType.DISCOUNT),
    ("промоциј", PromoType.DISCOUNT),
    ("попуст", PromoType.DISCOUNT),
    ("понуда", PromoType.DISCOUNT),
)


def map_promo_type(raw: str | None, *, has_discount: bool) -> PromoType:
    """Го сведува текстот на изворот во PromoType.

    has_discount доаѓа од правилото "попуст = ред со пополнета цена со
    попуст": ред без цена со попуст е NONE, без оглед што пишува во
    колоната за тип на акција.
    """
    if not has_discount:
        return PromoType.NONE

    text = normalize_for_match(raw)
    if not text:
        # Има цена со попуст, но изворот не кажал каков е - сметај го за попуст.
        return PromoType.DISCOUNT

    exact = _EXACT.get(text)
    if exact is not None:
        return exact

    for needle, promo_type in _CONTAINS:
        if needle in text:
            return promo_type

    log.warning("Непознат вид на поттикнување: %r - запишувам како OTHER", raw)
    return PromoType.OTHER


def is_loyalty_only(promo_type: PromoType) -> bool:
    """Дали цената важи само со картичка за лојалност.

    Таквите попусти се прикажуваат означено одделно - корисник без картичка
    нема да ја добие таа цена.
    """
    return promo_type is PromoType.LOYALTY
