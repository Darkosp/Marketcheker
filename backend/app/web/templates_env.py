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


def broj(value: int) -> str:
    """1234 -> „1.234". Точката е разделникот за илјади на македонски."""
    return f"{value:,}".replace(",", ".")


def brojka(value: int, one: str, many: str) -> str:
    """„1 маркет", „7 маркети", „341 продавница".

    На македонски броевите што завршуваат на 1 (освен 11) носат еднина:
    „341 продавница", не „341 продавници".
    """
    last = abs(value) % 100
    form = one if last % 10 == 1 and last != 11 else many
    return f"{broj(value)} {form}"


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Без ова секоја Jinja ознака остава по еден празен ред и целата своја
# вовлеченост во одговорот: страницата со 24 картички беше 79 KB, од што
# добар дел празно место.
templates.env.trim_blocks = True
templates.env.lstrip_blocks = True
templates.env.globals["asset_version"] = asset_version
templates.env.filters["broj"] = broj
templates.env.filters["brojka"] = brojka
