"""Запишување на прочитан ценовник во базата.

Читачот враќа ReaderResult и не знае за базата; тука тоа се претвора во
редови. Секое читање оставa PricelistRun - и кога успее и кога падне, за да
може утре да се види што се случило.

Што се зачувува: **само редовите со попуст.** Еден ценовник на Рамстор има
16.700 реда, Веро 10.100; од тие околу 1.200-1.750 се попусти. Апликацијата
прикажува само попусти, па чувањето на целиот асортиман би значело околу
1,4 милиона реда дневно без употреба. Вкупниот број прочитани редови се
запишува во PricelistRun.rows_total, за да се знае од колку се избрани.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import Grouper, default_grouper, parse_quantity, unit_price
from app.catalog.geo import normalize_city
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models import (
    Chain,
    City,
    PricelistRun,
    PriceRow,
    Product,
    ProductCategory,
    Store,
)
from app.models.enums import CategoryStatus, RunStatus
from app.readers.base import (
    EmptyPricelist,
    PricelistReader,
    RawPriceRow,
    ReaderError,
    ReaderResult,
    StoreRef,
    StructureChanged,
)
from app.readers.parsing import fingerprint
from app.readers.promo import map_promo_type

log = get_logger(__name__)


def today_local() -> date:
    """Денешниот датум во Europe/Skopje - клуч за „денешни попусти"."""
    return datetime.now(get_settings().tz).date()


# --------------------------------------------------------------------------
# Основни записи
# --------------------------------------------------------------------------
async def ensure_chain(session: AsyncSession, reader: type[PricelistReader]) -> Chain:
    chain = await session.scalar(select(Chain).where(Chain.code == reader.chain_code))
    if chain is None:
        chain = Chain(
            code=reader.chain_code,
            name=reader.chain_name,
            website=reader.website,
        )
        session.add(chain)
        await session.flush()
    return chain


async def ensure_city(session: AsyncSession, raw_city: str | None) -> City | None:
    normalized = normalize_city(raw_city)
    if normalized is None:
        return None
    slug, display = normalized
    city = await session.scalar(select(City).where(City.slug == slug))
    if city is None:
        city = City(slug=slug, name=display)
        session.add(city)
        await session.flush()
    return city


async def ensure_store(session: AsyncSession, chain: Chain, ref: StoreRef) -> Store:
    """Го наоѓа или создава записот за продавница, и го освежува виденото."""
    store = await session.scalar(
        select(Store).where(
            Store.chain_id == chain.id, Store.external_id == ref.external_id
        )
    )
    city = await ensure_city(session, ref.city)
    now = datetime.now(UTC)

    if store is None:
        store = Store(
            chain_id=chain.id,
            city_id=city.id if city else None,
            external_id=ref.external_id,
            name=ref.name,
            address=ref.address,
            source_url=ref.source_url,
        )
        session.add(store)
        await session.flush()
        return store

    # Продавниците се преименуваат и преселуваат; записот се освежува.
    store.name = ref.name
    store.address = ref.address
    store.source_url = ref.source_url
    store.is_active = True
    store.last_seen_at = now
    if city is not None:
        store.city_id = city.id
    return store


async def ensure_categories(
    session: AsyncSession, groups, subcategories
) -> dict[str, ProductCategory]:
    """Ги создава двете нивоа категории и ги поврзува.

    Групите се без parent; под-категориите се нивни деца. Истата табела
    држи и двете нивоа, па додавање ново ниво не бара миграција.
    """
    existing = {
        row.slug: row for row in (await session.scalars(select(ProductCategory))).all()
    }

    for slug, name, order in groups:
        row = existing.get(slug)
        if row is None:
            row = ProductCategory(slug=slug, name=name, sort_order=order)
            session.add(row)
            existing[slug] = row
        else:
            row.name = name
            row.sort_order = order
            row.parent_id = None
    await session.flush()

    for slug, name, parent_slug, order in subcategories:
        parent = existing.get(parent_slug)
        if parent is None:
            log.warning("Под-категоријата %s бара непозната група %s", slug, parent_slug)
            continue
        row = existing.get(slug)
        if row is None:
            row = ProductCategory(
                slug=slug, name=name, sort_order=order, parent_id=parent.id
            )
            session.add(row)
            existing[slug] = row
        else:
            row.name = name
            row.sort_order = order
            row.parent_id = parent.id
    await session.flush()
    return existing


# --------------------------------------------------------------------------
# Производи
# --------------------------------------------------------------------------
async def _load_products(
    session: AsyncSession, chain_id: int, fingerprints: set[str]
) -> dict[str, Product]:
    """Ги вчитува постоечките производи на еден синџир по fingerprint.

    Еден упит за цел ценовник наместо упит по ред - инаку дневното читање
    прави десетки илјади мали упити.
    """
    if not fingerprints:
        return {}
    rows = await session.scalars(
        select(Product).where(
            Product.chain_id == chain_id,
            Product.fingerprint.in_(fingerprints),
        )
    )
    return {row.fingerprint: row for row in rows}


def _build_product(
    chain_id: int,
    row: RawPriceRow,
    key: str,
    grouper: Grouper,
    categories: dict[str, ProductCategory],
) -> Product:
    match = grouper.group_of(row.name, row.description)
    quantity = parse_quantity(row.name)
    # Се зачувува НАЈКОНКРЕТНАТА категорија; групата се чита преку parent.
    category = categories.get(match.category_slug) or categories.get(match.group_slug)

    return Product(
        chain_id=chain_id,
        raw_name=row.name,
        raw_description=row.description,
        fingerprint=key,
        package_value=quantity.value if quantity else None,
        package_unit=quantity.unit if quantity else None,
        base_quantity=quantity.base_quantity if quantity else None,
        base_unit=quantity.base_unit if quantity else None,
        category_id=category.id if category else None,
        category_status=(
            CategoryStatus.AUTO if match.matched else CategoryStatus.UNKNOWN
        ),
        category_matched_keyword=match.matched_keyword,
    )


# --------------------------------------------------------------------------
# Главниот влез
# --------------------------------------------------------------------------
async def start_run(
    session: AsyncSession,
    chain: Chain,
    store: Store | None,
    *,
    run_date: date,
    source_url: str | None = None,
) -> PricelistRun:
    run = PricelistRun(
        chain_id=chain.id,
        store_id=store.id if store else None,
        run_date=run_date,
        source_url=source_url,
        status=RunStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    session.add(run)
    await session.flush()
    return run


async def fail_run(
    session: AsyncSession, run: PricelistRun, error: BaseException
) -> PricelistRun:
    """Запишува неуспешно читање. Никогаш тивок празен резултат."""
    # Празен ценовник НЕ е дефект кај нас: изворот одговорил, само нема
    # што да објави (Кипер Штип враќа recordsTotal=0). Ако се води како
    # FAILED, состојбата секој ден изгледа алармантно без причина.
    match error:
        case StructureChanged():
            run.status = RunStatus.STRUCTURE_CHANGED
        case EmptyPricelist():
            run.status = RunStatus.EMPTY
        case _:
            run.status = RunStatus.FAILED
    run.finished_at = datetime.now(UTC)
    run.error_type = type(error).__name__
    run.error_message = str(error)[:4000]
    log_at = log.info if run.status is RunStatus.EMPTY else log.error
    log_at(
        "Читањето на %s заврши со %s (%s): %s",
        run.source_url or run.store_id,
        run.status.value,
        run.error_type,
        run.error_message,
    )
    return run


async def save_result(
    session: AsyncSession,
    run: PricelistRun,
    store: Store,
    result: ReaderResult,
    *,
    grouper: Grouper | None = None,
    categories: dict[str, ProductCategory] | None = None,
) -> int:
    """Ги запишува редовите со попуст од едно читање. Враќа колку запишал."""
    grouper = grouper or default_grouper()
    if categories is None:
        from app.catalog.groups import GROUPS, SUBCATEGORIES

        categories = await ensure_categories(session, GROUPS, SUBCATEGORIES)

    discount_rows = result.discount_rows

    # Иста содржина како претходниот успешен run -> нема што ново да се запише.
    if result.content_hash and await _unchanged(session, run, result.content_hash):
        run.status = RunStatus.UNCHANGED
        run.finished_at = datetime.now(UTC)
        run.rows_total = result.rows_total
        run.rows_discount = len(discount_rows)
        run.content_hash = result.content_hash
        log.info("%s: ценовникот е непроменет, прескокнувам", result.source_url)
        return 0

    keyed = [(fingerprint(row.name, row.description), row) for row in discount_rows]
    products = await _load_products(session, store.chain_id, {key for key, _ in keyed})

    now = datetime.now(UTC)
    new_products: dict[str, Product] = {}
    written = 0

    for key, row in keyed:
        product = products.get(key) or new_products.get(key)
        if product is None:
            product = _build_product(store.chain_id, row, key, grouper, categories)
            session.add(product)
            new_products[key] = product
        else:
            product.last_seen_at = now

        session.add(_build_price_row(run, store, product, row, run_date=run.run_date))
        written += 1

    # Производите мораат да добијат id пред редовите со цени да се запишат.
    await session.flush()

    run.status = RunStatus.SUCCESS
    run.finished_at = now
    run.rows_total = result.rows_total
    run.rows_discount = len(discount_rows)
    run.rows_skipped = result.rows_skipped
    run.pricelist_date = result.pricelist_date
    run.content_hash = result.content_hash
    run.source_url = result.source_url

    if result.warnings:
        log.warning(
            "%s: %d предупредувања, прво: %s",
            result.source_url,
            len(result.warnings),
            result.warnings[0],
        )

    return written


def _build_price_row(
    run: PricelistRun,
    store: Store,
    product: Product,
    row: RawPriceRow,
    *,
    run_date: date,
) -> PriceRow:
    promo_type = map_promo_type(row.promo_type_raw, has_discount=row.is_discount)
    quantity = parse_quantity(row.name)

    return PriceRow(
        run=run,
        store_id=store.id,
        product=product,
        run_date=run_date,
        sale_price=row.sale_price,
        regular_price=row.regular_price,
        discount_price=row.discount_price,
        discount_pct=row.discount_pct or _derive_pct(row),
        unit_price_raw=row.unit_price,
        unit_price_base=unit_price(row.discount_price, quantity),
        promo_type=promo_type,
        promo_type_raw=row.promo_type_raw,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        is_single_day=row.is_single_day,
        is_discount=row.is_discount,
        availability=row.availability,
    )


def _derive_pct(row: RawPriceRow) -> Decimal | None:
    """Процентот кога изворот не го дава, а има двете цени.

    Веро го дава, Рамстор го има вграден во цената. Ова е за извори што не го
    даваат воопшто - се пресметува, не се погодува.
    """
    if row.regular_price and row.discount_price and row.regular_price > 0:
        diff = row.regular_price - row.discount_price
        if diff <= 0:
            return None
        return (diff / row.regular_price * 100).quantize(Decimal("0.01"))
    return None


async def _unchanged(session: AsyncSession, run: PricelistRun, content_hash: str) -> bool:
    previous = await session.scalar(
        select(PricelistRun.content_hash)
        .where(
            PricelistRun.store_id == run.store_id,
            PricelistRun.status.in_((RunStatus.SUCCESS, RunStatus.UNCHANGED)),
            PricelistRun.id != run.id,
        )
        .order_by(PricelistRun.run_date.desc(), PricelistRun.id.desc())
        .limit(1)
    )
    return previous == content_hash


# --------------------------------------------------------------------------
async def ingest_store(
    session: AsyncSession,
    reader: PricelistReader,
    chain: Chain,
    ref: StoreRef,
    *,
    run_date: date,
    grouper: Grouper,
    categories: dict[str, ProductCategory],
) -> PricelistRun:
    """Чита и запишува една продавница. Грешките се запишуваат, не се фрлаат."""
    store = await ensure_store(session, chain, ref)
    run = await start_run(
        session, chain, store, run_date=run_date, source_url=ref.source_url
    )
    try:
        result = await reader.read_store(ref)
    except ReaderError as error:
        await fail_run(session, run, error)
        return run

    try:
        written = await save_result(
            session, run, store, result, grouper=grouper, categories=categories
        )
    except Exception as error:
        await fail_run(session, run, error)
        return run

    log.info(
        "%s / %s: %d попусти запишани (%d прочитани редови)",
        chain.code,
        store.name,
        written,
        result.rows_total,
    )
    return run
