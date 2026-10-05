"""Регистар на читачи: chain_code -> класа.

Chain.code во базата го најдува својот модул преку овој регистар. Нов синџир
се додава со една линија тука и еден модул.
"""

from __future__ import annotations

from app.readers.base import PricelistReader
from app.readers.kam import KamReader
from app.readers.kipper import KipperReader
from app.readers.proverkanaceni import (
    StokomakReader,
    TamaroReader,
    ZitoReader,
)
from app.readers.ramstore import RamstoreReader
from app.readers.tinex import TinexReader
from app.readers.vero import VeroReader

READERS: dict[str, type[PricelistReader]] = {
    VeroReader.chain_code: VeroReader,
    RamstoreReader.chain_code: RamstoreReader,
    KipperReader.chain_code: KipperReader,
    KamReader.chain_code: KamReader,
    ZitoReader.chain_code: ZitoReader,
    StokomakReader.chain_code: StokomakReader,
    TamaroReader.chain_code: TamaroReader,
    TinexReader.chain_code: TinexReader,
}

# Синџири што дневното читање ги прескокнува, зашто изворот не работи.
# Се читаат само кога се побараат изрично (пр. `citaj --tinex`), што е
# начин да се провери дали сајтот се вратил.
SKIPPED_BY_DEFAULT: frozenset[str] = frozenset({TinexReader.chain_code})


def get_reader_class(chain_code: str) -> type[PricelistReader]:
    """Ја враќа класата на читачот за даден синџир."""
    try:
        return READERS[chain_code]
    except KeyError:
        known = ", ".join(sorted(READERS))
        raise LookupError(
            f"нема читач за синџир {chain_code!r}; познати: {known}"
        ) from None


def default_chain_codes() -> list[str]:
    """Синџирите што влегуваат во дневното читање."""
    return [code for code in READERS if code not in SKIPPED_BY_DEFAULT]


def available_chains() -> list[tuple[str, str, str]]:
    """(code, name, website) за сите регистрирани читачи - за seed на базата."""
    return [(cls.chain_code, cls.chain_name, cls.website) for cls in READERS.values()]
