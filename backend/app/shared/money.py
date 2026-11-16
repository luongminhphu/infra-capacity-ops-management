"""Money as integers (audit DAT-10, decision Q6 16/11/2026).

Amounts are stored in the currency's minor unit: VND has none (1 = 1 đồng), USD / EUR have cents.
AI costs are fractions of a cent, so they are stored in micro-dollars (1 USD = 1_000_000).
Rounding is ROUND_HALF_UP on the decimal value, never on a float product.

Two-release migration: release 1 adds the integer columns, writes both, and backfills; reads still
use the old Float column. Release 2 switches reads to the integer column and stops writing the float.
"""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional

CURRENCY_EXPONENT = {"VND": 0, "JPY": 0, "KRW": 0, "USD": 2, "EUR": 2, "GBP": 2, "SGD": 2, "AUD": 2}


def exponent(currency: Optional[str]) -> int:
    return CURRENCY_EXPONENT.get((currency or "VND").upper(), 2)


def to_minor(value: Any, currency: Optional[str] = "VND") -> Optional[int]:
    """1234.5 USD -> 123450; 1_234_567.6 VND -> 1_234_568; None / blank -> None."""
    if value is None or value == "":
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return int((d * (Decimal(10) ** exponent(currency))).quantize(Decimal(1), ROUND_HALF_UP))


def usd_to_micros(value: Any) -> int:
    """0.0123456789 USD -> 12346 micro-USD."""
    try:
        d = Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        return 0
    return int((d * Decimal(1_000_000)).quantize(Decimal(1), ROUND_HALF_UP))
