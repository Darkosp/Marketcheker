"""Набројувања. Се чуваат како VARCHAR + CHECK (native_enum=False), не како
PostgreSQL ENUM тип - така додавањето нова вредност е обична миграција.
"""

from __future__ import annotations

from enum import StrEnum


class PromoType(StrEnum):
    """Вид на продажно поттикнување како што го објавува маркетот."""

    DISCOUNT = "discount"  # обична акциска продажба
    LOYALTY = "loyalty"  # само со картичка за лојалност (Рамстор „ЛОЈАЛНОСТ")
    MULTIBUY = "multibuy"  # 1+1, 2+1, количински попуст
    OTHER = "other"  # непознат вид - се чува и во promo_type_raw
    NONE = "none"  # редот не е попуст


class RunStatus(StrEnum):
    """Исход од едно читање на ценовник."""

    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"  # мрежа/HTTP/парсирање падна
    EMPTY = "empty"  # изворот одговори, но не објави ниту еден производ
    STRUCTURE_CHANGED = "structure_changed"  # изворот смени формат - бара човек
    UNCHANGED = "unchanged"  # ист content_hash како претходно
    SKIPPED = "skipped"


class CategoryStatus(StrEnum):
    """Како производот дојде до својата општа категорија."""

    UNKNOWN = "unknown"  # ниеден клучен збор не фати - оди во листа за рачна потврда
    AUTO = "auto"  # автоматски, преку речник со клучни зборови
    CONFIRMED = "confirmed"  # човек потврди
    REJECTED = "rejected"  # човек рече „не е ова" - не прикажувај


class BaseUnit(StrEnum):
    """Единица за помошната цена, за да може „каде е најевтино"."""

    KG = "kg"
    LITER = "l"
    PIECE = "kom"
    METER = "m"
    SQUARE_METER = "m2"
    WASH = "pranje"  # детергенти се објавуваат и по број на перења
