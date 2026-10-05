"""Регистарот што го поврзува Chain.code со неговиот модул."""

from __future__ import annotations

import pytest

from app.readers import PricelistReader, RamstoreReader, VeroReader
from app.readers.registry import READERS, available_chains, get_reader_class


def test_vero_and_ramstore_are_registered() -> None:
    assert get_reader_class("vero") is VeroReader
    assert get_reader_class("ramstore") is RamstoreReader


def test_all_chains_are_registered() -> None:
    assert set(READERS) == {
        "vero",
        "ramstore",
        "kipper",
        "kam",
        "zito",
        "stokomak",
        "tamaro",
        "tinex",
    }


def test_unknown_chain_raises_with_helpful_message() -> None:
    with pytest.raises(LookupError, match="nepostoecki"):
        get_reader_class("nepostoecki")


def test_error_lists_known_chains() -> None:
    with pytest.raises(LookupError, match="kam, kipper, ramstore"):
        get_reader_class("nepoznat")


def test_all_readers_implement_the_interface() -> None:
    for code, cls in READERS.items():
        assert issubclass(cls, PricelistReader), code
        assert cls.chain_code == code
        assert cls.chain_name
        assert cls.website.startswith("http")


def test_available_chains_is_ready_for_seed() -> None:
    chains = {code: (name, site) for code, name, site in available_chains()}
    assert chains["vero"][0] == "Веро"
    assert chains["ramstore"][0] == "Рамстор"
    assert chains["kipper"][0] == "Кипер"
    assert chains["kam"][0] == "КАМ"
    assert chains["tinex"][0] == "Тинекс"
    assert chains["zito"][0] == "Жито Лукс"
    assert chains["stokomak"][0] == "Стокомак"
    assert chains["tamaro"][0] == "Тамаро"


def test_chain_codes_are_unique() -> None:
    codes = [cls.chain_code for cls in READERS.values()]
    assert len(codes) == len(set(codes))
