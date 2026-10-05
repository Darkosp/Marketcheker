"""Отворање сметка, влез со линк, одјава.

Нема лозинки и нема корисничко име. Адресата е сè: со неа се отвора
сметката, се потврдува со линк - еднаш - и потоа се влегува, без пошта.

Најавата НЕ е услов за ништо: без сметка страницата и натаму ги покажува
сите денешни попусти. Сметката носи едно нешто - листата да биде на
човекот, а не на уредот.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.deps import SESSION_KEY, CurrentUser, SessionDep
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models import User
from app.services import accounts, mail
from app.web import selection
from app.web.templates_env import templates

router = APIRouter(tags=["auth"])
log = get_logger(__name__)

HOME = "/"
# Нова сметка нема ништо во листата, па списокот со сите попусти не е
# следниот чекор - изборот е.
AFTER_REGISTER = "/izbor"


def _page(request: Request, template: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, template, context)


async def _send_link(
    request: Request, session: SessionDep, user: User, *, first_time: bool
) -> tuple[str, bool]:
    """(линк, дали писмото тргна)."""
    settings = get_settings()
    token = await accounts.issue_login_code(session, user)
    link = f"{settings.public_url.rstrip('/')}/vlez?{urlencode({'t': token})}"

    context = {
        "display": user.display,
        "link": link,
        "minutes": settings.mail_link_minutes,
        "first_time": first_time,
    }
    sent = await mail.send(
        to=user.email or "",
        subject=(
            "Потврди ја адресата — DARBOX Marketchecker"
            if first_time
            else "Влез во DARBOX Marketchecker"
        ),
        text=templates.get_template("mail/vlez.txt").render(context),
        html=templates.get_template("mail/vlez.html").render(context),
    )
    return link, sent


def _sent_page(
    request: Request, user: User, link: str, sent: bool, *, first_time: bool
) -> HTMLResponse:
    """Страницата по барање линк.

    Ако писмото НЕ тргнало, тоа се кажува. „Провери ја поштата" кога ништо
    не е испратено е лага што го остава човекот да чека нешто што нема да
    дојде.

    Надвор од production, линкот се прикажува кога поштата не работи - инаку
    секое тестирање дома би барало исправен сервер за пошта.
    """
    settings = get_settings()
    return _page(
        request,
        "proveri-posta.html",
        title="Провери ја поштата" if sent else "Писмото не тргна",
        email=user.email,
        first_time=first_time,
        sent=sent,
        link=None if sent or settings.is_production else link,
    )


def _forget_cookies(response: Response) -> None:
    """Колачињата повеќе не одлучуваат ништо - листата е на сметката.

    Ако останеа, по одјава би се вратил туѓ избор на истиот уред.
    """
    response.delete_cookie(selection.COOKIE_NAME, path="/")
    response.delete_cookie(selection.CITY_COOKIE, path="/")


# ==========================================================================
# Отворање сметка
# ==========================================================================
@router.get("/registracija", response_class=HTMLResponse, summary="Нова сметка")
async def register_form(request: Request, user: CurrentUser) -> Response:
    if user is not None:
        return RedirectResponse(HOME, status_code=303)
    return _page(request, "registracija.html", title="Направи сметка")


@router.post("/registracija", response_class=HTMLResponse, summary="Нова сметка")
async def register(
    request: Request,
    session: SessionDep,
    posta: str = Form(default=""),
) -> Response:
    try:
        user = await accounts.register(session, posta)
    except accounts.AccountError as problem:
        return _page(
            request,
            "registracija.html",
            title="Направи сметка",
            error=str(problem),
            posta=posta,
        )

    link, sent = await _send_link(request, session, user, first_time=True)
    await session.commit()
    log.info("Нова сметка: %s", user.email)
    return _sent_page(request, user, link, sent, first_time=True)


# ==========================================================================
# Влез
# ==========================================================================
@router.get("/najava", response_class=HTMLResponse, summary="Влез")
async def login_form(request: Request, user: CurrentUser) -> Response:
    if user is not None:
        return RedirectResponse(HOME, status_code=303)
    return _page(request, "najava.html", title="Влез")


@router.post("/najava", response_class=HTMLResponse, summary="Влез")
async def login(
    request: Request,
    session: SessionDep,
    posta: str = Form(default=""),
) -> Response:
    """Влез со адреса.

    Непотврдената сметка не влегува - ѝ се праќа линкот повторно, зашто
    потврдата на адресата е единственото нешто што се проверува по пошта.
    """
    user = await accounts.authenticate(session, posta)
    if user is None:
        return _page(
            request,
            "najava.html",
            title="Влез",
            error="Нема сметка на таа адреса.",
            posta=posta,
        )

    if not user.is_confirmed:
        link, sent = await _send_link(request, session, user, first_time=True)
        await session.commit()
        return _sent_page(request, user, link, sent, first_time=True)

    await _start_session(request, session, user)
    await session.commit()

    response = RedirectResponse(HOME, status_code=303)
    _forget_cookies(response)
    return response


async def _start_session(
    request: Request, session: SessionDep, user: User
) -> bool:
    """Го памети корисникот и го презема изборот од колачето, ако треба.

    Враќа дали листата е сè уште празна - тогаш следниот чекор е изборот,
    не списокот со сите попусти.

    Некој пробал без сметка, одбрал неколку производи, па отворил сметка.
    Тој избор не смее да исчезне - но ниту смее да прегази листа што веќе
    постои на сметката.
    """
    empty = not await accounts.load_picks(session, user)
    request.session.clear()  # нов идентитет, стара сесија не се надградува
    request.session[SESSION_KEY] = user.id

    if empty:
        from_cookie = selection.from_cookie(request.cookies.get(selection.COOKIE_NAME))
        if from_cookie:
            await accounts.save_picks(session, user, from_cookie)
            empty = False
    return empty


@router.get("/vlez", summary="Потврда на адресата преку линкот од поштата")
async def enter(request: Request, session: SessionDep, t: str = "") -> Response:
    user = await accounts.redeem_login_code(session, t)
    if user is None:
        # Ништо не е запишано - неважечки линк само се чита.
        return _page(
            request,
            "link-ne-vazi.html",
            title="Линкот не важи",
        )

    empty = await _start_session(request, session, user)
    await session.commit()

    response = RedirectResponse(
        AFTER_REGISTER if empty else HOME, status_code=303
    )
    _forget_cookies(response)
    return response


@router.post("/odjava", summary="Одјава")
async def logout(request: Request) -> Response:
    """Само POST: врска што одјавува може да ја активира и туѓа страница,
    или прелистувачот сам при предвчитување.
    """
    request.session.clear()
    response = RedirectResponse(HOME, status_code=303)
    _forget_cookies(response)
    return response
