"""Сметки: отворање, влез со линк, и што следи секој корисник.

Изборот досега живееше во колаче - по уред, не по човек. Двајца на ист
компјутер делеа една листа, а еден човек со телефон и со компјутер имаше
две. Сметката го врзува изборот за човекот.

**Нема лозинки.** Сметката се отвора со корисничко име и адреса, и се
потврдува со линк што стигнува на таа адреса - **еднаш**. Потоа секоја
најава бара корисничко име и адреса, без пошта.

Тоа значи дека најавата НЕ Е ТАЈНА: обете се знаат или се погодуваат, па
кој ги има, влегува. Одлуката е свесна и одговара на тоа што се чува -
листа категории и град, ништо лично. **Ако во сметката некогаш влезе нешто
повеќе од листа - порака, плаќање, историја на купување - ова мора да се
смени во лозинка или во линк при секоја најава.**

Поштата останува потребна за првата потврда. Затоа `app.core.config` не
дозволува production без наместен SMTP, а `python -m app.cli vlez` дава
рачен пат кога поштата ќе падне.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.picks import Pick
from app.core.config import get_settings
from app.models import City, User, UserPick
from app.services.mail import clean_email

# Букви, бројки, точка, долна црта и цртичка. Без празни места и без
# кирилица: корисничкото име се пишува често и на телефон, а мешањето
# „а" (кирилично) и „a" (латинично) создава два невидливо различни корисника.
USERNAME = re.compile(r"^[a-z0-9._-]{3,32}$")


class AccountError(ValueError):
    """Нешто во барањето не чини. Пораката оди право на екран."""


def normalise_username(value: str) -> str:
    """Малите букви се единствениот запис: „Darko" и „darko" се ист човек."""
    return value.strip().lower()


def check_username(value: str) -> str:
    name = normalise_username(value)
    if not USERNAME.match(name):
        raise AccountError(
            "Корисничкото име може да содржи латинични букви, бројки, точка, "
            "цртичка и долна црта, и да биде долго од 3 до 32 знаци."
        )
    return name


def check_email(value: str) -> str:
    address = clean_email(value)
    if address is None:
        raise AccountError("Тоа не личи на адреса за е-пошта. Провери уште еднаш.")
    return address


async def register(session: AsyncSession, username: str, email: str) -> User:
    """Нова сметка, непотврдена. Фрла `AccountError` со порака за екран."""
    name = check_username(username)
    address = check_email(email)

    taken = await session.scalar(select(User.id).where(User.username == name))
    if taken is not None:
        raise AccountError("Тоа корисничко име е зафатено. Пробај друго.")

    used = await session.scalar(select(User.id).where(User.email == address))
    if used is not None:
        raise AccountError(
            "На таа адреса веќе има сметка. Побарај линк за влез наместо нова."
        )

    user = User(username=name, email=address)
    session.add(user)
    await session.flush()
    return user


async def find_by_login(session: AsyncSession, text: str) -> User | None:
    """Сметката по адреса или по корисничко име.

    Служи за рачниот влез од терминал и за повторно праќање на потврдата.
    Најавата бара обете - види `authenticate`.
    """
    typed = text.strip().lower()
    if not typed:
        return None
    return await session.scalar(
        select(User).where(or_(User.email == typed, User.username == typed))
    )


async def authenticate(
    session: AsyncSession, username: str, email: str
) -> User | None:
    """Сметката кога корисничкото име И адресата се од неа, инаку `None`.

    Обете мора да се поклопат: само корисничко име би значело дека секој
    што ќе напише туѓо име влегува. Ниту ова не е тајна - но бара да се
    знаат две работи наместо една.
    """
    name = normalise_username(username)
    address = (email or "").strip().lower()
    if not name or not address:
        return None

    user = await session.scalar(
        select(User).where(User.username == name, User.email == address)
    )
    if user is None or not user.is_active:
        return None
    return user


# ==========================================================================
# Влез со линк
# ==========================================================================
def _signer() -> URLSafeTimedSerializer:
    # Солта го врзува потписот за оваа намена: потпис од сесија не смее да
    # важи како линк за влез.
    return URLSafeTimedSerializer(get_settings().secret_key, salt="vlez")


async def issue_login_code(session: AsyncSession, user: User) -> str:
    """Нов еднократен линк за таа сметка.

    Кодот се чува кај корисникот, а потписот го носи. Со тоа линкот важи
    еднаш, а нов линк го поништува претходниот - старото писмо во сандачето
    престанува да отвора врата.
    """
    user.login_code = secrets.token_urlsafe(16)
    await session.flush()
    return _signer().dumps({"id": user.id, "code": user.login_code})


async def redeem_login_code(session: AsyncSession, token: str) -> User | None:
    """Корисникот ако линкот чини, инаку `None`.

    Истиот линк ја потврдува адресата и влегува: ако писмото стигнало и
    некој кликнал, адресата постои - друга проверка не ни треба.
    """
    minutes = get_settings().mail_link_minutes
    try:
        payload = _signer().loads(token, max_age=minutes * 60)
    except (SignatureExpired, BadSignature):
        return None
    if not isinstance(payload, dict):
        return None

    user = await session.get(User, payload.get("id"))
    if user is None or not user.is_active or not user.login_code:
        return None
    # Константно споредување: кодот е тајна како и лозинка.
    if not secrets.compare_digest(user.login_code, str(payload.get("code", ""))):
        return None

    user.login_code = None
    if user.email_confirmed_at is None:
        user.email_confirmed_at = datetime.now(UTC)
    user.last_login_at = datetime.now(UTC)
    await session.flush()
    return user


async def get_user(session: AsyncSession, user_id: int | None) -> User | None:
    if user_id is None:
        return None
    user = await session.get(User, user_id)
    return user if user is not None and user.is_active else None


# ==========================================================================
# Што следи корисникот
# ==========================================================================
async def load_picks(session: AsyncSession, user: User) -> list[str]:
    """Записите на изборите, во редоследот во кој се зачувани.

    Се враќаат како записи, не како `Pick`: чистењето и подредувањето ги
    прави `app.web.selection`, исто како за URL-то и за колачето. Едно
    место каде се одлучува што е валидно.
    """
    rows = await session.execute(
        select(UserPick.pick_key)
        .where(UserPick.user_id == user.id)
        .order_by(UserPick.position, UserPick.id)
    )
    return [key for (key,) in rows]


async def save_picks(
    session: AsyncSession, user: User, picks: list[Pick]
) -> None:
    """Ја заменува целата листа.

    Замена, не спојување: кога корисникот отстранил нешто, тоа мора да
    исчезне. Листата е мала (до 30), па нема што да се штеди.
    """
    await session.execute(delete(UserPick).where(UserPick.user_id == user.id))
    session.add_all(
        UserPick(user_id=user.id, pick_key=pick.key, position=index)
        for index, pick in enumerate(picks)
    )
    await session.flush()


async def set_city(session: AsyncSession, user: User, city_slug: str) -> None:
    """Градот на корисникот. Празно значи „сите градови"."""
    if not city_slug:
        user.city_id = None
        return
    city_id = await session.scalar(select(City.id).where(City.slug == city_slug))
    # Непознат град не го менува запаметениот: URL-то може да дојде рачно
    # напишано, а тивко бришење на изборот е полошо од игнорирање.
    if city_id is not None:
        user.city_id = city_id


async def city_of(session: AsyncSession, user: User) -> str:
    if user.city_id is None:
        return ""
    return await session.scalar(select(City.slug).where(City.id == user.city_id)) or ""


__all__ = [
    "AccountError",
    "authenticate",
    "check_email",
    "check_username",
    "city_of",
    "find_by_login",
    "get_user",
    "issue_login_code",
    "load_picks",
    "normalise_username",
    "redeem_login_code",
    "register",
    "save_picks",
    "set_city",
]
