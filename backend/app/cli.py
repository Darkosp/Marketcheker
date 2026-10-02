"""Команди од терминал.

docker compose exec api python -m app.cli citaj           # сите синџири
docker compose exec api python -m app.cli citaj --vero --limit 2
docker compose exec api python -m app.cli izvestaj        # состојба за денес
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.session import SessionLocal, dispose_engine
from app.readers.registry import READERS
from app.services.discounts import (
    DiscountFilter,
    count_discounts,
    counts_by_group,
    run_summary,
)
from app.services.ingest import today_local
from app.services.runner import run_all


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="app.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    read = sub.add_parser("citaj", help="прочитај ги ценовниците и запиши попустите")
    read.add_argument("--datum", type=date.fromisoformat, default=None)
    read.add_argument(
        "--limit",
        type=int,
        default=None,
        help="колку продавници по синџир (за проба)",
    )
    for code in READERS:
        read.add_argument(f"--{code}", action="store_true", help=f"само {code}")

    report = sub.add_parser("izvestaj", help="состојба за денот")
    report.add_argument("--datum", type=date.fromisoformat, default=None)

    return parser.parse_args(argv)


async def _read(args: argparse.Namespace) -> int:
    chosen = [code for code in READERS if getattr(args, code, False)]
    outcomes = await run_all(
        run_date=args.datum,
        chain_codes=chosen or None,
        store_limit=args.limit,
    )
    print()
    failed = 0
    for outcome in outcomes:
        mark = "OK " if outcome.ok else "ГРЕШКА"
        print(
            f"{mark} {outcome.chain_code}: {outcome.succeeded}/{outcome.stores} "
            f"продавници, {outcome.discounts} попусти, "
            f"{outcome.unchanged} непроменети, {outcome.empty} без цени, "
            f"{outcome.failed} паднати"
        )
        for error in outcome.errors:
            print(f"      {error}")
        failed += outcome.failed + len(outcome.errors)
    return 1 if failed else 0


async def _report(args: argparse.Namespace) -> int:
    run_date = args.datum or today_local()
    async with SessionLocal() as session:
        print(f"Состојба за {run_date}:")
        rows = await run_summary(session, run_date)
        if not rows:
            print("  нема читања за тој ден")
            return 1
        for chain, status, runs, discounts in rows:
            print(f"  {chain:<12} {status:<18} {runs:>3} читања, {discounts:>6} попусти")

        filters = DiscountFilter(run_date=run_date)
        total = await count_discounts(session, filters)
        print()
        print(f"Попусти за приказ: {total}")
        for slug, name, count in await counts_by_group(session, filters):
            print(f"  {count:>6}  {name}  [{slug}]")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv if argv is not None else sys.argv[1:])
    setup_logging(get_settings().log_level)

    async def run() -> int:
        try:
            if args.command == "citaj":
                return await _read(args)
            return await _report(args)
        finally:
            await dispose_engine()

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
