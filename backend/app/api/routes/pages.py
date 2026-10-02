"""HTML страници (Jinja2 + HTMX).

Нема најава и нема обврзен избор: се отвора страницата и се гледаат сите
денешни попусти. Сè што корисникот ќе избере живее во URL-то, за да може
линк да се подели и страницата да се освежи без да се изгуби изборот.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.api.deps import SessionDep
from app.catalog.groups import PARENT_OF
from app.services.discounts import (
    DiscountFilter,
    SortBy,
    available_cities,
    available_stores,
    count_discounts,
    counts_by_group,
    counts_by_subcategory,
    latest_run_date,
    list_discounts,
    run_summary,
)
from app.services.ingest import today_local
from app.web.templates_env import templates

router = APIRouter(tags=["pages"])

# Колку попусти по страница.
PAGE_SIZES: tuple[int, ...] = (24, 48, 96, 200)
DEFAULT_PAGE_SIZE = PAGE_SIZES[1]

SORT_OPTIONS: tuple[tuple[str, str], ...] = (
    (SortBy.DISCOUNT_PCT.value, "најголем попуст"),
    (SortBy.PRICE_ASC.value, "најниска цена"),
    (SortBy.UNIT_PRICE.value, "најевтино по кг/л"),
    (SortBy.STORE.value, "по маркет"),
    (SortBy.NAME.value, "по назив"),
)


@dataclass(slots=True)
class Pagination:
    """Сè што му треба на приказот за да се движи низ страниците."""

    page: int
    page_size: int
    total: int

    @property
    def pages(self) -> int:
        return max(1, math.ceil(self.total / self.page_size))

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def first_shown(self) -> int:
        return 0 if not self.total else self.offset + 1

    @property
    def last_shown(self) -> int:
        return min(self.offset + self.page_size, self.total)

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def window(self) -> list[int]:
        """Броевите на страници околу тековната, за копчињата.

        Со стотици страници не се прикажуваат сите: само првата, последната
        и неколку околу тековната.
        """
        if self.pages <= 7:
            return list(range(1, self.pages + 1))

        around = {1, self.pages}
        around.update(
            page
            for page in range(self.page - 1, self.page + 2)
            if 1 <= page <= self.pages
        )
        return sorted(around)


async def _resolve_date(session, requested: date | None) -> tuple[date, bool]:
    """Кој ден да се прикаже.

    Ако за денес нема ниту еден попуст (читањето уште не поминало, или сите
    извори паднале), се прикажува последниот ден со податоци - но страницата
    го кажува тоа, не се преправа дека е денешно.
    """
    today = today_local()
    if requested is not None:
        return requested, requested != today

    if await count_discounts(session, DiscountFilter(run_date=today)):
        return today, False

    latest = await latest_run_date(session)
    if latest is None:
        return today, False
    return latest, latest != today


def _clamp_page_size(value: int) -> int:
    return value if value in PAGE_SIZES else DEFAULT_PAGE_SIZE


@router.get("/", response_class=HTMLResponse, summary="Денешни попусти")
async def index(
    request: Request,
    session: SessionDep,
    datum: date | None = None,
    grad: str | None = None,
    grupa: str | None = None,
    market: list[int] | None = Query(default=None),
    sortiraj: str = SortBy.DISCOUNT_PCT.value,
    lojalnost: bool = True,
    ednodnevni: bool = False,
    strana: int = 1,
    po_strana: int = DEFAULT_PAGE_SIZE,
) -> HTMLResponse:
    run_date, is_stale = await _resolve_date(session, datum)

    try:
        sort_by = SortBy(sortiraj)
    except ValueError:
        sort_by = SortBy.DISCOUNT_PCT

    page_size = _clamp_page_size(po_strana)
    stores = list(market or [])

    def build(group_slug: str | None, *, limit: int, offset: int) -> DiscountFilter:
        return DiscountFilter(
            run_date=run_date,
            city_slug=grad or None,
            group_slug=group_slug,
            store_ids=stores,
            sort_by=sort_by,
            include_loyalty=lojalnost,
            only_single_day=ednodnevni,
            limit=limit,
            offset=offset,
        )

    total = await count_discounts(session, build(grupa or None, limit=1, offset=0))

    # Страница надвор од опсегот се враќа на последната што постои.
    pagination = Pagination(page=max(1, strana), page_size=page_size, total=total)
    if pagination.page > pagination.pages:
        pagination = Pagination(page=pagination.pages, page_size=page_size, total=total)

    rows = await list_discounts(
        session, build(grupa or None, limit=page_size, offset=pagination.offset)
    )

    # Бројките во менито се сметаат БЕЗ филтерот по категорија, за да
    # покажуваат колку има насекаде, не само во избраното.
    menu_filters = build(None, limit=1, offset=0)
    selected_group = PARENT_OF.get(grupa or "", grupa or "")
    subcategories = (
        await counts_by_subcategory(session, menu_filters, selected_group)
        if selected_group and selected_group != "drugo"
        else []
    )

    context = {
        "title": "Денешни попусти",
        "run_date": run_date,
        "today": today_local(),
        "is_stale": is_stale,
        "rows": rows,
        "pagination": pagination,
        "page_sizes": PAGE_SIZES,
        "group_counts": await counts_by_group(session, menu_filters),
        "subcategories": subcategories,
        "selected_group": selected_group,
        "cities": await available_cities(session, run_date),
        "stores": await available_stores(session, run_date, grad),
        "selected": {
            "grad": grad or "",
            "grupa": grupa or "",
            "grupa_root": selected_group,
            "market": set(stores),
            "sortiraj": sort_by.value,
            "lojalnost": lojalnost,
            "ednodnevni": ednodnevni,
            "po_strana": page_size,
        },
        "sort_options": SORT_OPTIONS,
    }

    # HTMX бара само резултатите; копчињата се враќаат одделно
    # (out-of-band), за да се освежи означеното иако се менува само списокот.
    is_htmx = bool(request.headers.get("hx-request"))
    context["oob"] = is_htmx
    template = "partials/results.html" if is_htmx else "index.html"
    return templates.TemplateResponse(request, template, context)


@router.get("/sostojba", response_class=HTMLResponse, summary="Состојба на читањата")
async def status_page(
    request: Request, session: SessionDep, datum: date | None = None
) -> HTMLResponse:
    run_date, is_stale = await _resolve_date(session, datum)
    context = {
        "title": "Состојба на читањата",
        "run_date": run_date,
        "today": today_local(),
        "is_stale": is_stale,
        "runs": await run_summary(session, run_date),
        "total": await count_discounts(session, DiscountFilter(run_date=run_date)),
    }
    return templates.TemplateResponse(request, "status.html", context)
