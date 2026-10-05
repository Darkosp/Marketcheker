"""Прекатегоризација на веќе запишаните производи.

Кога речникот во app/catalog/groups.py ќе се дополни, веќе запишаните
производи ја чуваат старата категорија. Оваа скрипта ги поминува повторно
низ истиот групирач, без повторно читање на ценовниците.

    docker compose exec api python -m scripts.regroup           # пробно
    docker compose exec api python -m scripts.regroup --zapisi  # навистина

Пробното пуштање ништо не менува - само кажува колку би се сменило.
"""

from __future__ import annotations

import asyncio
import collections
import sys

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.catalog import default_grouper
from app.catalog.groups import GROUPS, SUBCATEGORIES
from app.core.logging import setup_logging
from app.db.session import SessionLocal, dispose_engine
from app.models import Product
from app.models.enums import CategoryStatus
from app.services.ingest import ensure_categories

BATCH = 2000


async def regroup(*, write: bool) -> int:
    grouper = default_grouper()

    async with SessionLocal() as session:
        categories = await ensure_categories(session, GROUPS, SUBCATEGORIES)
        by_id = {row.id: row for row in categories.values()}
        if write:
            await session.commit()

        moved: collections.Counter[str] = collections.Counter()
        total = 0
        changed = 0
        offset = 0

        while True:
            products = (
                await session.scalars(
                    select(Product)
                    .options(selectinload(Product.category))
                    .order_by(Product.id)
                    .offset(offset)
                    .limit(BATCH)
                )
            ).all()
            if not products:
                break
            offset += len(products)

            for product in products:
                total += 1
                match = grouper.group_of(product.raw_name, product.raw_description)
                target = categories.get(match.category_slug) or categories.get(
                    match.group_slug
                )
                if target is None or product.category_id == target.id:
                    continue

                old = by_id.get(product.category_id)
                moved[f"{old.slug if old else '—'} -> {target.slug}"] += 1
                changed += 1

                if write:
                    product.category_id = target.id
                    product.category_status = (
                        CategoryStatus.AUTO if match.matched else CategoryStatus.UNKNOWN
                    )
                    product.category_matched_keyword = match.matched_keyword

            if write:
                await session.commit()

        print(f"производи: {total}")
        print(f"сменети:   {changed} ({100 * changed // max(total, 1)}%)")
        print()
        print("НАЈЧЕСТИ ПРЕМЕСТУВАЊА:")
        for move, count in moved.most_common(25):
            print(f"  {count:>6}  {move}")

        if not write:
            print()
            print("Пробно пуштање - ништо не е сменето. Со --zapisi се запишува.")

    return 0


async def _run(write: bool) -> int:
    # Затворањето мора да биде во ИСТАТА јамка; инаку asyncpg фрла
    # „attached to a different loop" при гасење.
    try:
        return await regroup(write=write)
    finally:
        await dispose_engine()


def main(argv: list[str]) -> int:
    setup_logging("WARNING")
    return asyncio.run(_run("--zapisi" in argv))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
