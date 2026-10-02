"""Jinja2 околина - една инстанца за целата апликација."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


def asset_version() -> str:
    """Кратка ознака што се менува кога ќе се смени стилот.

    Без ова прелистувачот служи стара CSS по ажурирање - се случи при
    преработката на дизајнот: новите правила не се применуваа додека
    кешот не се исчисти рачно.
    """
    stylesheet = STATIC_DIR / "app.css"
    try:
        return str(int(stylesheet.stat().st_mtime))
    except OSError:
        return "0"


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["asset_version"] = asset_version
