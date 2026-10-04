"""Сметки: регистрација, најава, и што следи секој корисник.

Изборот досега живееше во колаче - по уред, не по човек. Тоа значеше дека
двајца на ист компјутер делат една листа, а еден човек со телефон и со
компјутер има две. Сметката го врзува изборот за човекот.

Што НЕ се бара: е-пошта, име, ништо. Корисничко име и лозинка. Е-поштата
бара услуга за испраќање пораки (потврда, заборавена лозинка) - уште една
зависност што се расипува, за корист што сега ја нема.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.picks import Pick
from app.core.security import (
    MIN_PASSWORD_LENGTH,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.models import City, User, UserPick

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


def check_password(value: str) -> str:
    if len(value) < MIN_PASSWORD_LENGTH:
        raise AccountError(
            f"Лозинката мора да има најмалку {MIN_PASSWORD_LENGTH} знаци."
        )
    return value


async def register(session: AsyncSession, username: str, password: str) -> User:
    """Нова сметка. Фрла `AccountError` со порака за екран."""
    name = check_username(username)
    check_password(password)

    taken = await session.scalar(select(User.id).where(User.username == name))
    if taken is not None:
        raise AccountError("Тоа корисничко име е зафатено. Пробај друго.")

    user = User(username=name, password_hash=hash_password(password))
    session.add(user)
    await session.flush()
    return user


async def authenticate(
    session: AsyncSession, username: str, password: str
) -> User | None:
    """Корисникот ако лозинката чини, инаку `None`.

    Нема разлика во одговорот меѓу „го нема тоа име" и „погрешна лозинка":
    таа разлика кажува кои имиња постојат.
    """
    name = normalise_username(username)
    user = await session.scalar(select(User).where(User.username == name))
    if user is None or not user.is_active:
        # Се троши исто време како при вистинска проверка, за да одговорот
        # не каже дали името постои.
        verify_password(password, hash_password("празно"))
        return None

    if not verify_password(password, user.password_hash):
        return None

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = datetime.now(UTC)
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
    "check_password",
    "check_username",
    "city_of",
    "get_user",
    "load_picks",
    "normalise_username",
    "register",
    "save_picks",
    "set_city",
]
