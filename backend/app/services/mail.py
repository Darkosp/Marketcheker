"""Испраќање пошта: потврда на адреса и најава со линк.

Нема лозинки. Сметката се отвора со корисничко име и адреса, а влегувањето
оди преку линк што стигнува на таа адреса - истиот механизам и за првата
потврда и за секоја следна најава.

Тоа значи дека поштата НЕ е украс: ако не работи, никој не може да влезе.
Затоа:

- кога SMTP не е наместен, линкот се запишува во дневникот наместо да се
  испрати, за да локалното тестирање не зависи од сервис за пошта;
- на production празен SMTP ја запира апликацијата (`app.core.config`),
  наместо луѓето да останат заклучени надвор;
- постои и рачен пат (`python -m app.cli vlez`), за кога поштата падне.
"""

from __future__ import annotations

import re
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

import aiosmtplib

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

# Намерно едноставна: целта е да фати очигледни грешки при пишување, не да
# суди што е валидна адреса. Вистинската проверка е дали писмото стигнало -
# адреса што не постои нема да биде потврдена.
EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$")

MAX_EMAIL_LENGTH = 254  # колку дозволува стандардот


def clean_email(value: str) -> str | None:
    """Адресата нормализирана, или `None` ако очигледно не чини."""
    address = value.strip().lower()
    if not address or len(address) > MAX_EMAIL_LENGTH or not EMAIL.match(address):
        return None
    return address


def _sender_domain(sender: str) -> str:
    """Доменот од адресата на испраќачот, за `Message-ID`.

    Идентификаторот мора да носи домен што ни припаѓа; туѓ или измислен е
    уште еден знак дека писмото не е од онаму од каде што тврди.
    """
    _, address = parseaddr(sender)
    _, _, domain = address.partition("@")
    return domain or "localhost"


async def send(to: str, subject: str, text: str, html: str) -> bool:
    """Испраќа порака. `False` ако не поминала.

    Двата облика одат заедно: некои читачи на пошта не прикажуваат HTML, а
    порака без текстуален облик почесто завршува во спам.
    """
    settings = get_settings()

    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject

    # Без овие двете писмото е неисправно по стандардот и филтрите го
    # третираат како сомнително: првите пораки кон Gmail и Outlook беа
    # прифатени од серверот и никогаш не стигнаа до сандачето.
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=_sender_domain(settings.smtp_from))

    # Ова не е одговор на човек и не треба да добие автоматски одговор
    # („надвор сум од канцеларија") - така се избегнува и јамка.
    message["Auto-Submitted"] = "auto-generated"

    message.set_content(text)
    message.add_alternative(html, subtype="html")

    if not settings.mail_enabled:
        # Нема сервер за пошта: пораката оди во дневникот за да може да се
        # тестира. Цел текст, не само линкот - за да се види што ќе прочита
        # корисникот.
        log.warning(
            "ПОШТАТА НЕ Е НАМЕСТЕНА - писмото НЕ е испратено.\n"
            "До: %s\nНаслов: %s\n%s",
            to,
            subject,
            text,
        )
        return False

    context = ssl.create_default_context()
    if not settings.smtp_verify:
        # Споделен хостинг често нуди сертификат на името на серверот, не на
        # доменот. Врската останува шифрирана; само потврдата на идентитетот
        # отпаѓа.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    try:
        await aiosmtplib.send(
            message,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user or None,
            password=settings.smtp_password or None,
            use_tls=settings.smtp_ssl,
            start_tls=not settings.smtp_ssl,
            tls_context=context,
            timeout=settings.smtp_timeout,
        )
    except Exception as problem:
        # Не се крие: човекот чека писмо што нема да дојде, и мора да се
        # знае зошто.
        log.error("Поштата кон %s падна: %s", to, problem)
        return False

    log.info("Писмо испратено до %s (%s)", to, subject)
    return True


__all__ = ["EMAIL", "MAX_EMAIL_LENGTH", "clean_email", "send"]
