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
from app.readers.kam import KamReader
from app.readers.kipper import KipperReader
from app.readers.promo import map_promo_type
from app.readers.ramstore import RamstoreReader
from app.readers.registry import (
    READERS,
    SKIPPED_BY_DEFAULT,
    available_chains,
    default_chain_codes,
    get_reader_class,
)
from app.readers.tinex import TinexReader
from app.readers.vero import VeroReader

__all__ = [
    "READERS",
    "SKIPPED_BY_DEFAULT",
    "EmptyPricelist",
    "KamReader",
    "KipperReader",
    "PoliteClient",
    "PricelistReader",
    "RamstoreReader",
    "RawPriceRow",
    "ReaderError",
    "ReaderResult",
    "SourceUnavailable",
    "StoreRef",
    "StructureChanged",
    "TinexReader",
    "VeroReader",
    "available_chains",
    "content_hash",
    "default_chain_codes",
    "get_reader_class",
    "map_promo_type",
]
