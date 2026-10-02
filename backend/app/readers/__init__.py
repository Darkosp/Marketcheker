"""Читачи на ценовници. Секој синџир е посебен модул со заеднички интерфејс."""

from app.readers.base import (
    EmptyPricelist,
    PricelistReader,
    RawPriceRow,
    ReaderError,
    ReaderResult,
    SourceUnavailable,
    StoreRef,
    StructureChanged,
)
from app.readers.http import PoliteClient, content_hash
from app.readers.promo import map_promo_type
from app.readers.ramstore import RamstoreReader
from app.readers.registry import READERS, available_chains, get_reader_class
from app.readers.vero import VeroReader

__all__ = [
    "READERS",
    "EmptyPricelist",
    "PoliteClient",
    "PricelistReader",
    "RamstoreReader",
    "RawPriceRow",
    "ReaderError",
    "ReaderResult",
    "SourceUnavailable",
    "StoreRef",
    "StructureChanged",
    "VeroReader",
    "available_chains",
    "content_hash",
    "get_reader_class",
    "map_promo_type",
]
