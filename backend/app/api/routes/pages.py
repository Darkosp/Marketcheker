"""HTML страници (Jinja2 + HTMX).

Нема најава и изборот не е обврзен: се отвора страницата и се гледаат сите
денешни попусти. Кој ќе си одбере што следи (`/izbor`), го гледа само тоа.

Сè што корисникот ќе избере живее во URL-то, за да може линк да се подели
и страницата да се освежи без да се изгуби изборот. Изборот на производи
оди и во колаче, за да следното отворање го памети - `app.web.selection`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import HTMLResponse

from app.api.deps import SessionDep
from app.catalog.groups import PARENT_OF
from app.services.catalog import catalog_tree
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
    read_coverage,
    read_quality,
    run_summary,
)
from app.services.ingest import today_local
from app.services.stats import (
    chain_stats,
    latest_stats_date,
    price_movement,
    top_stores,
)
from app.web import selection
from app.web.templates_env import templates

router = APIRouter(tags=["pages"])

# Колку попусти по страница.
PAGE_SIZES: tuple[int, ...] = (24, 48, 96, 200)
DEFAULT_PAGE_SIZE = PAGE_SIZES[1]

# Процентот не се нуди: 50% на производ од 100 денари е 50 денари, а 28%
# на кафе од 700 е 200. Старите линкови со `sortiraj=popust` и натаму
# работат - само не се предлага.
SORT_OPTIONS: tuple[tuple[str, str], ...] = (
    (SortBy.SAVINGS.value, "најголема заштеда"),
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


def _store_ids(raw: list[str] | None) -> list[int]:
    """Ги чита ид-овата на продавници од URL-то.

    Примаат се СТРИНГОВИ намерно: опцијата „сите маркети" праќа празна
    вредност, а со list[int] FastAPI враќаше 422 на СЕКОЕ барање од
    формуларот - паѓаа и филтрите, и подредувањето, и страниците.
    URL-то може да дојде и рачно напишано, па нечитливото се игнорира.
    """
    ids: list[int] = []
    for value in raw or []:
        text = value.strip()
        if text.isdigit():
            ids.append(int(text))
    return ids


def _remember_selection(response: Response, slugs: list[str]) -> None:
    """Колачето го памети изборот; празен избор го брише.

    httponly: изборот го чита серверот, не JavaScript. Нема лични
    податоци внатре - само слугови од каталогот.
    """
    if slugs:
        response.set_cookie(
            selection.COOKIE_NAME,
            selection.to_cookie(slugs),
            max_age=selection.COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            path="/",
        )
    else:
        response.delete_cookie(selection.COOKIE_NAME, path="/")


@router.get("/", response_class=HTMLResponse, summary="Денешни попусти")
async def index(
    request: Request,
    session: SessionDep,
    datum: date | None = None,
    grad: str | None = None,
    grupa: str | None = None,
    izbor: list[str] | None = Query(default=None),
    market: list[str] | None = Query(default=None),
    sortiraj: str = SortBy.SAVINGS.value,
    lojalnost: bool = True,
    ednodnevni: bool = False,
    strana: int = 1,
    po_strana: int = DEFAULT_PAGE_SIZE,
) -> HTMLResponse:
    run_date, is_stale = await _resolve_date(session, datum)

    try:
        sort_by = SortBy(sortiraj)
    except ValueError:
        sort_by = SortBy.SAVINGS

    page_size = _clamp_page_size(po_strana)
    stores = _store_ids(market)
    chosen, chosen_in_url = selection.resolve(
        izbor, request.cookies.get(selection.COOKIE_NAME)
    )

    def build(group_slug: str | None, *, limit: int, offset: int) -> DiscountFilter:
        return DiscountFilter(
            run_date=run_date,
            city_slug=grad or None,
            group_slug=group_slug,
            selection=chosen,
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

    cities = await available_cities(session, run_date)
    city_names = {slug: name for slug, name, _ in cities}

    context = {
        "title": "Денешни попусти",
        "chosen": chosen,
        "chosen_labels": selection.labels(chosen),
        "run_date": run_date,
        "today": today_local(),
        "is_stale": is_stale,
        "rows": rows,
        "pagination": pagination,
        "page_sizes": PAGE_SIZES,
        "group_counts": await counts_by_group(session, menu_filters),
        "subcategories": subcategories,
        "selected_group": selected_group,
        "cities": cities,
        "stores": await available_stores(session, run_date, grad),
        "selected": {
            "grad": grad or "",
            "grad_name": city_names.get(grad or "", ""),
            "grupa": grupa or "",
            "grupa_root": selected_group,
            "izbor": chosen,
            "market": set(stores),
            "sortiraj": sort_by.value,
            "lojalnost": lojalnost,
            "ednodnevni": ednodnevni,
            "po_strana": page_size,
        },
        "sort_options": SORT_OPTIONS,
    }

    # Празната страница се појавува само кога ИЗБОРОТ останал без попусти.
    # Бројот на проверени продавници оди со неа: „нема попуст" без него
    # изгледа како дефект, а со него е тврдење.
    context["empty_selection"] = bool(chosen) and not total
    if context["empty_selection"]:
        chains, store_count = await read_coverage(
            session, run_date, grad or None, stores
        )
        context["coverage"] = {"chains": chains, "stores": store_count}

    # HTMX бара само резултатите; копчињата се враќаат одделно
    # (out-of-band), за да се освежи означеното иако се менува само списокот.
    is_htmx = bool(request.headers.get("hx-request"))
    context["oob"] = is_htmx
    template = "partials/results.html" if is_htmx else "index.html"

    response = templates.TemplateResponse(request, template, context)
    # Колачето се пишува само кога барањето се изјаснило за изборот -
    # инаку секое прелистување би го препишувало со истото.
    if chosen_in_url:
        _remember_selection(response, chosen)
    return response


@router.get("/izbor", response_class=HTMLResponse, summary="Избор на производи")
async def selection_page(
    request: Request,
    session: SessionDep,
    izbor: list[str] | None = Query(default=None),
    grad: str | None = None,
) -> HTMLResponse:
    """Што следи корисникот.

    Секое ниво е избирливо само по себе: може да се земе цела „Пијалоци и
    напитоци", или само „Кафе" во неа. Бројките се од целиот каталог, не од
    денешните попусти - изборот е трајна намера, а „Кафе 0" би изгледало
    како причина кафето да не се избере.

    Формуларот оди со GET на „/": изборот влегува во URL-то, а страницата
    со попусти го запишува во колачето.
    """
    chosen, _ = selection.resolve(izbor, request.cookies.get(selection.COOKIE_NAME))
    run_date, _ = await _resolve_date(session, None)

    context = {
        "title": "Што следиш",
        "tree": await catalog_tree(session),
        "cities": await available_cities(session, run_date),
        "chosen": set(chosen),
        "chosen_count": len(chosen),
        "selected": {"grad": grad or ""},
    }
    return templates.TemplateResponse(request, "izbor.html", context)


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
        "quality": await read_quality(session, run_date),
        "total": await count_discounts(session, DiscountFilter(run_date=run_date)),
    }
    return templates.TemplateResponse(request, "status.html", context)


@router.get(
    "/statistika", response_class=HTMLResponse, summary="Кој маркет попушта најмногу"
)
async def stats_page(
    request: Request, session: SessionDep, datum: date | None = None
) -> HTMLResponse:
    """Споредба на маркетите.

    Главната мерка е УДЕЛОТ на попусти во асортиманот, не бројот: маркет
    со 20.000 производи и 1.000 попусти не е подарежлив од маркет со
    2.000 производи и 300 попусти. Тоа може да се пресмета само затоа што
    се чува целиот асортиман, не само попустите.
    """
    run_date = datum or await latest_stats_date(session) or today_local()
    chains = await chain_stats(session, run_date=run_date)

    up, down, changes = await price_movement(session, days=30)

    context = {
        "title": "Статистика",
        "run_date": run_date,
        "today": today_local(),
        "chains": chains,
        "stores": await top_stores(session, run_date=run_date, limit=12),
        "movement": {"up": up, "down": down, "total": changes},
        "totals": {
            "products": sum(row.products_total for row in chains),
            "discounts": sum(row.discounts_total for row in chains),
            "savings": sum((row.total_savings or 0) for row in chains),
        },
    }
    return templates.TemplateResponse(request, "stats.html", context)
