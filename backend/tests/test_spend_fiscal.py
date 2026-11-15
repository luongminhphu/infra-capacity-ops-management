"""Fiscal-year arithmetic and reminder threshold choice (pure functions)."""
import datetime as dt
from decimal import Decimal

import pytest

from app.spend.fiscal import add_years, fiscal_range, fiscal_year, today_vn
from app.spend.reminders import pick_threshold
from app.spend.service import gross_of

D = dt.date


@pytest.mark.parametrize("day,start_month,expected", [
    (D(2026, 1, 1), 1, 2026), (D(2026, 12, 31), 1, 2026),
    (D(2026, 3, 31), 4, 2025), (D(2026, 4, 1), 4, 2026), (D(2027, 3, 31), 4, 2026),
    (D(2026, 6, 30), 7, 2025), (D(2026, 7, 1), 7, 2026),
])
def test_fiscal_year_boundaries(day, start_month, expected):
    assert fiscal_year(day, start_month) == expected


def test_fiscal_range():
    assert fiscal_range(2026, 1) == (D(2026, 1, 1), D(2026, 12, 31))
    assert fiscal_range(2026, 4) == (D(2026, 4, 1), D(2027, 3, 31))
    with pytest.raises(ValueError):
        fiscal_year(D(2026, 1, 1), 13)


@pytest.mark.parametrize("start,years,expected", [
    (D(2026, 6, 15), 1, D(2027, 6, 15)), (D(2026, 6, 15), 3, D(2029, 6, 15)),
    (D(2028, 2, 29), 1, D(2029, 2, 28)), (D(2028, 2, 29), 4, D(2032, 2, 29)),
])
def test_add_years_leap_day(start, years, expected):
    assert add_years(start, years) == expected


@pytest.mark.parametrize("start,years,expected", [
    (D(2026, 1, 1), 1, D(2026, 12, 31)), (D(2026, 6, 15), 3, D(2029, 6, 14)),
    (D(2028, 2, 29), 1, D(2029, 2, 28)), (D(2028, 2, 29), 4, D(2032, 2, 28)), (D(2032, 2, 29), 1, D(2033, 2, 28)),
])
def test_term_end(start, years, expected):
    from app.spend.fiscal import term_end
    assert term_end(start, years) == expected


def test_today_is_vietnam_calendar_day():
    # 18:30 UTC on 31/12 is already 01/01 01:30 in Vietnam (UTC+7).
    assert today_vn(dt.datetime(2026, 12, 31, 18, 30)) == D(2027, 1, 1)
    assert today_vn(dt.datetime(2026, 12, 31, 16, 59)) == D(2026, 12, 31)


@pytest.mark.parametrize("days_left,target,skipped", [
    (95, None, []), (90, 90, []), (61, 90, []), (60, 60, [90]), (58, 60, [90]),
    (30, 30, [60, 90]), (0, 30, [60, 90]), (-1, None, []),
])
def test_pick_threshold_sends_smallest_reached(days_left, target, skipped):
    assert pick_threshold(days_left, [90, 60, 30]) == (target, skipped)


def test_gross_rounds_half_up_to_dong():
    assert gross_of(100_000_000, Decimal("10")) == 110_000_000
    assert gross_of(1_234_567, Decimal("8")) == 1_333_332      # 1,333,332.36
    assert gross_of(15, Decimal("10")) == 17                   # 16.5 -> 17
