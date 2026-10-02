"""HTML страници (Jinja2 + HTMX). Содржината доаѓа во чекор 5."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.web.templates_env import templates

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse, summary="Почетна страница")
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {"title": "Marketchecker"})
