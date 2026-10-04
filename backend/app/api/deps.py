"""Заеднички FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.models import User
from app.services import accounts

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

SESSION_KEY = "user_id"


async def current_user(request: Request, session: SessionDep) -> User | None:
    """Најавениот корисник, или `None`.

    Нема страница што БАРА најава: без сметка сè уште се гледаат сите
    попусти. Затоа ова враќа `None` наместо да фрла.

    Сесијата носи само број. Ако сметката е избришана или затворена во
    меѓувреме, бројот води никаде и корисникот е како ненајавен.
    """
    return await accounts.get_user(session, request.session.get(SESSION_KEY))


CurrentUser = Annotated["User | None", Depends(current_user)]
