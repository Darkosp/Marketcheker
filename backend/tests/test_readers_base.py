"""Правилата од спецификацијата, кодирани во заедничкиот интерфејс."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.readers import RawPriceRow, ReaderResult, StoreRef


def _row(**kwargs) -> RawPriceRow:
    return RawPriceRow(name="Nescafe Classic 100 g", **kwargs)


def test_discount_is_row_with_discount_price() -> None:
    assert _row(discount_price=Decimal("189.00")).is_discount is True


def test_row_without_discount_price_is_not_discount() -> None:
    # Пополнета редовна цена сама по себе не е попуст.
    assert _row(regular_price=Decimal("239.00")).is_discount is False


def test_single_day_when_from_equals_to() -> None:
    day = date(2026, 10, 1)
    assert _row(valid_from=day, valid_to=day).is_single_day is True


def test_not_single_day_for_range() -> None:
    row = _row(valid_from=date(2026, 10, 1), valid_to=date(2026, 10, 7))
    assert row.is_single_day is False


def test_not_single_day_when_dates_missing() -> None:
    assert _row(valid_from=date(2026, 10, 1)).is_single_day is False


def test_result_counts_discount_rows_only() -> None:
    result = ReaderResult(
        store=StoreRef(external_id="1", name="Тест"),
        rows=[
            _row(discount_price=Decimal("189.00")),
            _row(regular_price=Decimal("239.00")),
            _row(discount_price=Decimal("99.50")),
        ],
        source_url="https://example.invalid/1_1.html",
    )
    assert result.rows_total == 3
    assert len(result.discount_rows) == 2
