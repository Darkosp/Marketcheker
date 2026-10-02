"""Извештај: како се распределуваат попустите по групи на употреба.

Работи врз зачуван JSON од читачите, не врз жив сајт:
    PYTHONPATH=/app python scripts/report_groups.py /tmp/opisi.json

Најважниот дел е на крајот: описите што останале несортирани. Од таму се
додаваат нови клучни зборови во app/catalog/groups.py.
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

from app.catalog import default_grouper, group_names, parse_quantity
from app.catalog.groups import FALLBACK_SLUG, GROUPS

TOP_UNSORTED = 30


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    grouper = default_grouper()
    names = group_names()

    per_group: collections.Counter[str] = collections.Counter()
    unsorted_labels: collections.Counter[str] = collections.Counter()
    unsorted_example: dict[str, str] = {}
    matched_in: collections.Counter[str] = collections.Counter()

    discounts = 0
    no_quantity = 0

    for chain, rows in data.items():
        chain_discounts = 0
        for name, description, is_discount in rows:
            if not is_discount:
                continue
            discounts += 1
            chain_discounts += 1

            result = grouper.group_of(name, description)
            per_group[result.group_slug] += 1
            if result.matched:
                matched_in[result.matched_in or "?"] += 1
            else:
                key = description or "(без опис)"
                unsorted_labels[key] += 1
                unsorted_example.setdefault(key, name)

            if parse_quantity(name) is None:
                no_quantity += 1

        print(f"{chain}: {chain_discounts} попусти")

    if not discounts:
        print("Нема попусти во податоците.")
        return 1

    unsorted_count = per_group.get(FALLBACK_SLUG, 0)
    sorted_count = discounts - unsorted_count

    print()
    print("=" * 68)
    print(f"ПОПУСТИ ВКУПНО: {discounts}")
    print(f"сортирани:      {sorted_count} ({100 * sorted_count // discounts}%)")
    print(f"несортирани:    {unsorted_count} ({100 * unsorted_count // discounts}%)")
    print(f"без грамажа:    {no_quantity} ({100 * no_quantity // discounts}%)")
    print(
        f"фатено по опис: {matched_in.get('description', 0)}, "
        f"по назив: {matched_in.get('name', 0)}"
    )
    print("=" * 68)

    print()
    print("ПО ГРУПА:")
    for slug, name, _ in sorted(GROUPS, key=lambda row: row[2]):
        count = per_group.get(slug, 0)
        bar = "#" * (count * 40 // discounts)
        print(f"  {count:>5}  {name:<32} {bar}")

    print()
    print(f"НЕСОРТИРАНИ ОПИСИ (првите {TOP_UNSORTED}) - од тука расте речникот:")
    for label, count in unsorted_labels.most_common(TOP_UNSORTED):
        example = unsorted_example[label][:42]
        print(f"  {count:>4}  {label[:36]:<38} пр. {example}")
    if not unsorted_labels:
        print("  (нема - сите попусти се сортирани)")

    _ = names  # имињата се користат погоре преку GROUPS
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
