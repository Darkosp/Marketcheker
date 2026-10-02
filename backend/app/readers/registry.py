"""Регистар на читачи: chain_code -> класа.

Chain.code во базата го најдува својот модул преку овој регистар. Нов синџир
се додава со една линија тука и еден модул.
"""

from __future__ import annotations

from app.readers.base import PricelistReader
from app.readers.ramstore import RamstoreReader
from app.readers.vero import VeroReader

READERS: dict[str, type[PricelistReader]] = {
    VeroReader.chain_code: VeroReader,
    RamstoreReader.chain_code: RamstoreReader,
    # Кипер, КАМ и Тинекс доаѓаат во чекор 6.
}


def get_reader_class(chain_code: str) -> type[PricelistReader]:
    """Ја враќа класата на читачот за даден синџир."""
    try:
        return READERS[chain_code]
    except KeyError:
        known = ", ".join(sorted(READERS))
        raise LookupError(
            f"нема читач за синџир {chain_code!r}; познати: {known}"
        ) from None


def available_chains() -> list[tuple[str, str, str]]:
    """(code, name, website) за сите регистрирани читачи - за seed на базата."""
    return [(cls.chain_code, cls.chain_name, cls.website) for cls in READERS.values()]
