"""Проверка на здравје - користи го и Docker healthcheck-от."""

from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import __version__
from app.api.deps import SessionDep
from app.core.logging import get_logger

router = APIRouter(tags=["health"])
log = get_logger(__name__)


@router.get("/health", summary="Дали апликацијата работи")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/health/db", summary="Дали базата е достапна")
async def health_db(session: SessionDep) -> JSONResponse:
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:  # носно: не криј детали во логот
        log.error("Проверката на базата падна: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "error", "database": "unreachable"},
        )
    return JSONResponse(content={"status": "ok", "database": "ok"})
