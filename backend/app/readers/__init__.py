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

__all__ = [
    "EmptyPricelist",
    "PricelistReader",
    "RawPriceRow",
    "ReaderError",
    "ReaderResult",
    "SourceUnavailable",
    "StoreRef",
    "StructureChanged",
]
