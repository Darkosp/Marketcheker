"""HTML страници (Jinja2 + HTMX).

Првата верзија не бара најава и не бара од корисникот да избира што следи:
ја отвора страницата и ги гледа сите денешни попусти по групи. Филтрите
(град, маркет, група, подредување) се необврзни и живеат во URL-то, за да
може линк да се подели.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.api.deps import SessionDep
from app.services.discounts import (
    DiscountFilter,
    SortBy,
    available_cities,
    available_stores,
    count_discounts,
    counts_by_group,
    counts_by_subcategory,
    group_discounts,
    latest_run_date,
    run_summary,
)
from app.services.ingest import today_local
from app.web.templates_env import templates

router = APIRouter(tags=["pages"])

PAGE_SIZE = 300


async def _resolve_date(session, requested: date | None) -> tuple[date, bool]:
    """Кој ден да се прикаже.

    Ако за денес нема ниту еден попуст (читањето уште не поминало, или
    сите извори паднале), се прикажува последниот ден со податоци - но
    страницата го кажува тоа, не се преправа дека е денешно.
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


def _filters(
    run_date: date,
    *,
    city: str | None,
    group: str | None,
    store: list[int] | None,
    sort: str,
    loyalty: bool,
    single_day: bool,
) -> DiscountFilter:
    try:
        sort_by = SortBy(sort)
    except ValueError:
        sort_by = SortBy.DISCOUNT_PCT

    return DiscountFilter(
        run_date=run_date,
        city_slug=city or None,
        group_slug=group or None,
        store_ids=list(store or []),
        sort_by=sort_by,
        include_loyalty=loyalty,
        only_single_day=single_day,
        limit=PAGE_SIZE,
    )


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
) -> HTMLResponse:
    run_date, is_stale = await _resolve_date(session, datum)
    filters = _filters(
        run_date,
        city=grad,
        group=grupa,
        store=market,
        sort=sortiraj,
        loyalty=lojalnost,
        single_day=ednodnevni,
    )

    total = await count_discounts(session, filters)
    groups = await group_discounts(session, filters)
    # Бројките по група се сметаат БЕЗ филтерот по група, за да менито
    # покажува колку има насекаде, не само во избраната.
    menu_filters = _filters(
        run_date,
        city=grad,
        group=None,
        store=market,
        sort=sortiraj,
        loyalty=lojalnost,
        single_day=ednodnevni,
    )

    # Второ ниво копчиња: само кога е избрана група од прво ниво.
    from app.catalog.groups import PARENT_OF

    selected_group = PARENT_OF.get(grupa or "", grupa or "")
    subcategories = (
        await counts_by_subcategory(session, menu_filters, selected_group)
        if selected_group
        else []
    )

    context = {
        "title": "Денешни попусти",
        "run_date": run_date,
        "today": today_local(),
        "is_stale": is_stale,
        "total": total,
        "shown": sum(group.count for group in groups),
        "page_size": PAGE_SIZE,
        "groups": groups,
        "group_counts": await counts_by_group(session, menu_filters),
        "subcategories": subcategories,
        "selected_group": selected_group,
        "cities": await available_cities(session, run_date),
        "stores": await available_stores(session, run_date, grad),
        "selected": {
            "grad": grad or "",
            "grupa": grupa or "",
            "grupa_root": selected_group,
            "market": set(market or []),
            "sortiraj": filters.sort_by.value,
            "lojalnost": lojalnost,
            "ednodnevni": ednodnevni,
        },
        "sort_options": [
            (SortBy.DISCOUNT_PCT.value, "најголем попуст"),
            (SortBy.PRICE_ASC.value, "најниска цена"),
            (SortBy.UNIT_PRICE.value, "најевтино по кг/л"),
            (SortBy.STORE.value, "по маркет"),
            (SortBy.NAME.value, "по назив"),
        ],
    }

    # HTMX бара само списокот, не целата страница. Копчињата за групи се
    # враќаат одделно (out-of-band), за да означеното копче се освежи.
    is_htmx = bool(request.headers.get("hx-request"))
    context["oob"] = is_htmx
    template = "partials/discount_list.html" if is_htmx else "index.html"
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
