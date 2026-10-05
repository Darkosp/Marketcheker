"""Влезна точка на FastAPI апликацијата."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.api.routes import auth, health, pages
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.db.session import dispose_engine
from app.web.templates_env import STATIC_DIR

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level)
    log.info(
        "DARBOX Marketchecker %s стартува (околина=%s, зона=%s)",
        __version__,
        settings.app_env,
        settings.scheduler_timezone,
    )
    # Табелите ги создава Alembic (`alembic upgrade head`), не апликацијата.
    yield
    await dispose_engine()
    log.info("DARBOX Marketchecker запре")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="DARBOX Marketchecker",
        description="Дневни попусти од ценовниците на маркетите во Македонија.",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
    )

    # Сесијата го држи најавениот корисник.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="mc_session",
        https_only=settings.is_production,
        same_site="lax",
        # Долго намерно: без лозинка, секоја повторна најава значи уште една
        # посета на поштата. Три месеци прави тоа да биде редок настан.
        max_age=settings.session_days * 24 * 60 * 60,
    )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/sw.js", include_in_schema=False)
    async def service_worker() -> FileResponse:
        """Service worker-от се служи од КОРЕНОТ, не од /static/.

        Опсегот на service worker е ограничен на патеката од која е
        преземен: од /static/sw.js би покривал само /static/, значи ништо
        корисно.
        """
        return FileResponse(
            STATIC_DIR / "sw.js",
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
        )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(pages.router)

    return app


app = create_app()
