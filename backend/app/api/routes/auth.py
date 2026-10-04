"""Регистрација, најава, одјава.

Најавата НЕ е услов за ништо. Без сметка страницата и натаму ги покажува
сите денешни попусти; сметката носи едно нешто - листата да биде на
човекот, а не на уредот.

Затоа тука нема пренасочување кон најава од други страници, нема заштитени
патеки, и нема порака „мора да се најавиш".
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.deps import SESSION_KEY, CurrentUser, SessionDep
from app.core.logging import get_logger
from app.models import User
from app.services import accounts
from app.web import selection
from app.web.templates_env import templates

router = APIRouter(tags=["auth"])
log = get_logger(__name__)

# Каде се оди по успешна најава.
HOME = "/"


def _form(request: Request, template: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, template, {"request": request, **context})


async def _start_session(
    request: Request, session: SessionDep, user: User
) -> None:
    """Го памети корисникот и го презема изборот од колачето, ако треба.

    Некој прво пробал без сметка, одбрал неколку производи, па се
    регистрирал. Тој избор не смее да исчезне - но ниту смее да прегази
    листа што веќе постои на сметката.
    """
    request.session.clear()  # нов идентитет, стара сесија не се надградува
    request.session[SESSION_KEY] = user.id

    if await accounts.load_picks(session, user):
        return
    from_cookie = selection.from_cookie(request.cookies.get(selection.COOKIE_NAME))
    if from_cookie:
        await accounts.save_picks(session, user, from_cookie)


def _forget_cookies(response: Response) -> None:
    """Колачињата повеќе не одлучуваат ништо - листата е на сметката.

    Ако останеа, по одјава би се вратил туѓ избор на истиот уред.
    """
    response.delete_cookie(selection.COOKIE_NAME, path="/")
    response.delete_cookie(selection.CITY_COOKIE, path="/")


# ==========================================================================
# Регистрација
# ==========================================================================
@router.get("/registracija", response_class=HTMLResponse, summary="Нова сметка")
async def register_form(request: Request, user: CurrentUser) -> Response:
    if user is not None:
        return RedirectResponse(HOME, status_code=303)
    return _form(request, "registracija.html", title="Направи сметка")


@router.post("/registracija", response_class=HTMLResponse, summary="Нова сметка")
async def register(
    request: Request,
    session: SessionDep,
    ime: str = Form(default=""),
    lozinka: str = Form(default=""),
    lozinka_pak: str = Form(default=""),
) -> Response:
    if lozinka != lozinka_pak:
        return _form(
            request,
            "registracija.html",
            title="Направи сметка",
            error="Двете лозинки не се исти.",
            ime=ime,
        )

    try:
        user = await accounts.register(session, ime, lozinka)
    except accounts.AccountError as problem:
        return _form(
            request,
            "registracija.html",
            title="Направи сметка",
            error=str(problem),
            ime=ime,
        )

    await _start_session(request, session, user)
    await session.commit()
    log.info("Нова сметка: %s", user.username)

    response = RedirectResponse(HOME, status_code=303)
    _forget_cookies(response)
    return response


# ==========================================================================
# Најава
# ==========================================================================
@router.get("/najava", response_class=HTMLResponse, summary="Најава")
async def login_form(request: Request, user: CurrentUser) -> Response:
    if user is not None:
        return RedirectResponse(HOME, status_code=303)
    return _form(request, "najava.html", title="Најава")


@router.post("/najava", response_class=HTMLResponse, summary="Најава")
async def login(
    request: Request,
    session: SessionDep,
    ime: str = Form(default=""),
    lozinka: str = Form(default=""),
) -> Response:
    user = await accounts.authenticate(session, ime, lozinka)
    if user is None:
        # Една порака за двата случаја: разликата кажува кои имиња постојат.
        return _form(
            request,
            "najava.html",
            title="Најава",
            error="Погрешно корисничко име или лозинка.",
            ime=ime,
        )

    await _start_session(request, session, user)
    await session.commit()

    response = RedirectResponse(HOME, status_code=303)
    _forget_cookies(response)
    return response


# ==========================================================================
# Одјава
# ==========================================================================
@router.post("/odjava", summary="Одјава")
async def logout(request: Request) -> Response:
    """Само POST: врска што одјавува со клик може да ја активира и туѓа
    страница, или прелистувачот сам при предвчитување.
    """
    request.session.clear()
    response = RedirectResponse(HOME, status_code=303)
    _forget_cookies(response)
    return response
