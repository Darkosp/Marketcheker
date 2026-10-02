"""Регистарот што го поврзува Chain.code со неговиот модул."""

from __future__ import annotations

import pytest

from app.readers import PricelistReader, RamstoreReader, VeroReader
from app.readers.registry import READERS, available_chains, get_reader_class


def test_vero_and_ramstore_are_registered() -> None:
    assert get_reader_class("vero") is VeroReader
    assert get_reader_class("ramstore") is RamstoreReader


def test_unknown_chain_raises_with_helpful_message() -> None:
    with pytest.raises(LookupError, match="tinex"):
        get_reader_class("tinex")


def test_error_lists_known_chains() -> None:
    with pytest.raises(LookupError, match="ramstore, vero"):
        get_reader_class("nepoznat")


def test_all_readers_implement_the_interface() -> None:
    for code, cls in READERS.items():
        assert issubclass(cls, PricelistReader), code
        assert cls.chain_code == code
        assert cls.chain_name
        assert cls.website.startswith("https://")


def test_available_chains_is_ready_for_seed() -> None:
    chains = {code: (name, site) for code, name, site in available_chains()}
    assert chains["vero"][0] == "Веро"
    assert chains["ramstore"][0] == "Рамстор"


def test_chain_codes_are_unique() -> None:
    codes = [cls.chain_code for cls in READERS.values()]
    assert len(codes) == len(set(codes))
