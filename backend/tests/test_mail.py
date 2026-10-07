"""Писмата: адреси и заглавија.

Првите пораки кон Gmail и Outlook беа ПРИФАТЕНИ од серверот и никогаш не
стигнаа до сандачето. Причината не беше ни блокада ни лозинка: писмата
немаа `Date` и `Message-ID`. Обете се задолжителни по стандардот и
филтрите такво писмо го третираат како сомнително.
"""

from __future__ import annotations

from email.message import EmailMessage
from email.utils import parsedate_to_datetime

import pytest

from app.services import mail


def _message() -> EmailMessage:
    """Писмото онака како што го гради `send`, без да се испрати."""
    import asyncio

    captured: dict[str, EmailMessage] = {}

    async def fake_send(message, **kwargs):
        captured["m"] = message

    original = mail.aiosmtplib.send
    mail.aiosmtplib.send = fake_send
    try:
        from app.core.config import get_settings

        get_settings.cache_clear()
        import os

        os.environ["SMTP_HOST"] = "posta.primer.mk"
        asyncio.run(
            mail.send(to="nekoj@primer.mk", subject="Проба", text="т", html="<p>х</p>")
        )
    finally:
        mail.aiosmtplib.send = original
        os.environ["SMTP_HOST"] = ""
        get_settings.cache_clear()
    return captured["m"]


# ==========================================================================
# Заглавија без кои писмото паѓа во спам
# ==========================================================================
def test_the_letter_carries_a_date() -> None:
    assert parsedate_to_datetime(_message()["Date"]) is not None


def test_the_letter_carries_a_message_id() -> None:
    assert _message()["Message-ID"].startswith("<")


def test_the_message_id_carries_our_own_domain() -> None:
    """Туѓ или измислен домен е уште еден знак дека писмото не е од онаму
    од каде што тврди.
    """
    assert _message()["Message-ID"].endswith("@darbo.mk>")


def test_the_letter_says_it_is_automatic() -> None:
    """За да не добие автоматски одговор, и да нема јамка."""
    assert _message()["Auto-Submitted"] == "auto-generated"


def test_both_forms_are_sent() -> None:
    """Порака само со HTML почесто завршува во спам."""
    kinds = {part.get_content_type() for part in _message().walk()}
    assert "text/plain" in kinds
    assert "text/html" in kinds


def test_the_sender_domain_is_read_from_the_address() -> None:
    assert mail._sender_domain("DARBOX <info@darbo.mk>") == "darbo.mk"
    assert mail._sender_domain("info@darbo.mk") == "darbo.mk"
    assert mail._sender_domain("без-домен") == "localhost"


# ==========================================================================
# Адреси
# ==========================================================================
@pytest.mark.parametrize(
    "address", ["a@b.mk", "  A.B+c@Primer.COM  ", "x@sub.domen.com.mk"]
)
def test_good_addresses_are_accepted(address: str) -> None:
    assert mail.clean_email(address)


@pytest.mark.parametrize(
    "address", ["", "   ", "a", "a@", "@b.mk", "a b@c.mk", "a@b", "a@b.c"]
)
def test_bad_addresses_are_refused(address: str) -> None:
    assert mail.clean_email(address) is None


def test_the_address_is_lowercased_and_trimmed() -> None:
    assert mail.clean_email("  Darko@Primer.MK ") == "darko@primer.mk"


def test_an_absurdly_long_address_is_refused() -> None:
    assert mail.clean_email("a" * 250 + "@primer.mk") is None
