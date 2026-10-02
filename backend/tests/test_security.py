"""Хеширање на лозинки."""

from __future__ import annotations

from app.core.security import hash_password, verify_password


def test_hash_is_argon2id() -> None:
    assert hash_password("тајна-лозинка").startswith("$argon2id$")


def test_same_password_gives_different_hash() -> None:
    # Различна сол секој пат.
    assert hash_password("ista") != hash_password("ista")


def test_verify_accepts_correct_password() -> None:
    assert verify_password("точна", hash_password("точна")) is True


def test_verify_rejects_wrong_password() -> None:
    assert verify_password("погрешна", hash_password("точна")) is False


def test_verify_rejects_garbage_hash() -> None:
    assert verify_password("нешто", "не-е-хеш") is False
