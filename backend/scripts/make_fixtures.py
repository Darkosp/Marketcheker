"""Прави скратени fixtures од вистински страници на ценовници.

Зошто скратени: една страница на Рамстор е ~7,7 MB. Во репото чуваме
заглавие + мал но претставителен избор редови, со НЕПРОМЕНЕТ markup, за да
тестовите останат брзи а парсерот сè уште да го гледа вистинскиот формат.

Користење (внатре во контејнерот, со преземена страница на диск):
    python scripts/make_fixtures.py vero   /tmp/vero_89_1.html
    python scripts/make_fixtures.py ramstore /tmp/ramstore_store.html

Скриптата НЕ чита од мрежа - работи врз датотека што веќе ја имаш. Така
освежувањето на fixtures е свесен чин, а тестовите никогаш не одат на живи
сајтови.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# Колку редови од табелата да задржиме.
KEEP_ROWS = 40


def _split_rows(table_html: str) -> tuple[str, list[str], str]:
    """Го дели HTML-от на табела во (пред првиот <tr>, редови, по последниот)."""
    pattern = re.compile(r"<tr\b.*?(?=<tr\b|</table)", re.S)
    rows = [m.group(0) for m in pattern.finditer(table_html)]
    if not rows:
        raise SystemExit("не најдов <tr> во табелата")
    start = table_html.index(rows[0])
    end = table_html.index(rows[-1]) + len(rows[-1])
    return table_html[:start], rows, table_html[end:]


def _pick(rows: list[str], keep: int) -> list[str]:
    """Го задржува заглавието, па редови од целиот опсег (не само почетокот).

    Редовите на крајот од ценовникот често изгледаат поинаку од првите,
    затоа земаме рамномерно распределен избор.
    """
    if len(rows) <= keep + 1:
        return rows
    header, body = rows[0], rows[1:]
    step = max(1, len(body) // keep)
    chosen = body[::step][:keep]
    return [header, *chosen]


def trim(html: str, *, table_index: int) -> str:
    """Ја крати табелата со ценовник, а останатото го остава како што е."""
    tables = list(re.finditer(r"<table\b.*?</table>", html, re.S))
    if table_index >= len(tables):
        raise SystemExit(f"нема табела {table_index}; најдени {len(tables)}")
    target = tables[table_index]
    before, rows, after = _split_rows(target.group(0))
    trimmed = before + "\n".join(_pick(rows, KEEP_ROWS)) + after
    return html[: target.start()] + trimmed + html[target.end() :]


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2

    which, source = argv[1], Path(argv[2])
    html = source.read_text(encoding="utf-8")

    if which == "vero":
        # Веро: третата табела е ценовникот (0 = лого, 1 = заглавие).
        out = FIXTURES / "vero_store_page1.html"
        out.write_text(trim(html, table_index=2), encoding="utf-8")
    elif which == "ramstore":
        out = FIXTURES / "ramstore_store.html"
        out.write_text(trim(html, table_index=0), encoding="utf-8")
    else:
        print(f"непознат извор: {which}")
        return 2

    size_kb = out.stat().st_size / 1024
    print(f"{out.name}: {size_kb:.0f} KB (од {source.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
