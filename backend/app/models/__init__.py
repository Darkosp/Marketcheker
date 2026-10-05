"""Сите модели се увезуваат тука, за да ги види Alembic при autogenerate."""

from app.db.base import Base
from app.models.catalog import CategoryKeyword, Product, ProductCategory
from app.models.chain import Chain, Store
from app.models.enums import (
    BaseUnit,
    CategoryStatus,
    PromoType,
    RunStatus,
)
from app.models.geo import City
from app.models.prices import CurrentPrice, DailyStoreStats, PriceChange
from app.models.pricing import PricelistRun, PriceRow
from app.models.user import User, UserPick, UserStore

__all__ = [
    "Base",
    "BaseUnit",
    "CategoryKeyword",
    "CategoryStatus",
    "Chain",
    "City",
    "CurrentPrice",
    "DailyStoreStats",
    "PriceChange",
    "PriceRow",
    "PricelistRun",
    "Product",
    "ProductCategory",
    "PromoType",
    "RunStatus",
    "Store",
    "User",
    "UserPick",
    "UserStore",
]
