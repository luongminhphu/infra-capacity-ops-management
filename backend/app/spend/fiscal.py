"""Fiscal-year and date arithmetic (pure functions, tested in tests/test_spend_fiscal.py).

A fiscal year is named after the calendar year it STARTS in: with start month 4,
01/04/2026 – 31/03/2027 is fiscal year 2026. Start month 1 = calendar year.
"""
import calendar
import datetime as dt
from typing import Tuple

from ..timeutil import VN_TZ


def fiscal_year(d: dt.date, start_month: int = 1) -> int:
    if not 1 <= start_month <= 12:
        raise ValueError("start_month must be 1..12")
    return d.year if d.month >= start_month else d.year - 1


def fiscal_range(year: int, start_month: int = 1) -> Tuple[dt.date, dt.date]:
    """First and last day of fiscal year `year`."""
    start = dt.date(year, start_month, 1)
    return start, add_years(start, 1) - dt.timedelta(days=1)


def add_years(d: dt.date, years: int) -> dt.date:
    """Same day N years later; 29/02 lands on 28/02 when the target year is not a leap year."""
    y = d.year + years
    if d.month == 2 and d.day == 29 and not calendar.isleap(y):
        return dt.date(y, 2, 28)
    return d.replace(year=y)


def term_end(start: dt.date, years: int) -> dt.date:
    """Last day of a term of `years` years starting on `start` (inclusive). A term starting on
    29/02 runs until 28/02 when the anniversary year is not a leap year (the anniversary is
    then 01/03): 29/02/2028 + 1 year -> 28/02/2029; 29/02/2028 + 4 years -> 28/02/2032."""
    y = start.year + years
    if start.month == 2 and start.day == 29 and not calendar.isleap(y):
        anniversary = dt.date(y, 3, 1)
    else:
        anniversary = start.replace(year=y)
    return anniversary - dt.timedelta(days=1)


def today_vn(now_utc: dt.datetime = None) -> dt.date:
    """Business "today" is the Vietnam calendar day (the server clock runs in UTC)."""
    now_utc = now_utc or dt.datetime.utcnow()
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=dt.timezone.utc)
    return now_utc.astimezone(VN_TZ).date()
