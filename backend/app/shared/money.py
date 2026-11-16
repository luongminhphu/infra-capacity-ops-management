"""Money as integers (audit DAT-10, decision Q6 16/11/2026).

Amounts are stored in the currency's minor unit: VND has none (1 = 1 đồng), USD / EUR have cents.
AI costs are fractions of a cent, so they are stored in micro-dollars (1 USD = 1_000_000).
Rounding is ROUND_HALF_UP on the decimal value, never on a float product.

Two-release migration: release 1 added the integer columns, writes both, and backfills. Release 2
(this one) reads only the integer columns — every sum is an integer sum, converted to a decimal
value once for display — but still writes the Float too, because pods of release 1 may run side by
side during the rollout. A later release stops writing the Float and drops it.
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


def from_minor(minor: Optional[int], currency: Optional[str] = "VND") -> Optional[float]:
    """123450 USD-cents -> 1234.5; 1_234_568 VND -> 1234568.0. For display / JSON only."""
    if minor is None:
        return None
    return float(Decimal(int(minor)) / (Decimal(10) ** exponent(currency)))


def micros_to_usd(micros: Optional[int]) -> float:
    """37038 micro-USD -> 0.037038 (display only)."""
    return float(Decimal(int(micros or 0)) / Decimal(1_000_000))


def mul_minor(minor: int, qty: Any, divisor: Any = 1) -> int:
    """minor × qty / divisor rounded HALF_UP to a whole minor unit, computed in Decimal (qty and
    divisor may be fractional: pack quantities, cores per pack)."""
    d = Decimal(int(minor)) * Decimal(str(qty or 0)) / Decimal(str(divisor or 1))
    return int(d.quantize(Decimal(1), ROUND_HALF_UP))
