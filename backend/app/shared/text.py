"""Vietnamese / English number and text helpers shared by every importer (báo giá, BOM, cải tạo…)."""
import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional

_NUM = re.compile(r"[-+]?\d[\d.,\s]*")


def parse_number(value: Any) -> Optional[Decimal]:
    """1.234.567 / 1,234,567 / 1.234,5 / 1,234.5 / "12.977 triệu" (unit words ignored) -> Decimal.
    A single separator followed by exactly 3 digits is a thousands separator (VN and EN usage)."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return _bounded(Decimal(str(value)))
        except InvalidOperation:
            return None
    m = _NUM.search(str(value).replace(" ", " "))
    if not m:
        return None
    s = m.group(0).replace(" ", "").strip(".,")
    if not s or s in "+-":
        return None
    dots, commas = s.count("."), s.count(",")
    if dots and commas:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        s = s.replace("," if dec == "." else ".", "").replace(dec, ".")
    elif dots or commas:
        sep = "." if dots else ","
        parts = s.split(sep)
        if len(parts) > 2 or (len(parts[-1]) == 3 and parts[0].lstrip("+-") not in ("", "0")):
            s = s.replace(sep, "")                       # thousands separators
        else:
            s = s.replace(sep, ".")                      # decimal separator
    try:
        return _bounded(Decimal(s))
    except InvalidOperation:
        return None


MAX_ABS = Decimal("1e15")          # larger than any quotation; keeps BIGINT / NUMERIC(18,2) safe


def _bounded(d: Decimal) -> Optional[Decimal]:
    return d if d.is_finite() and abs(d) < MAX_ABS else None


def to_int(d: Optional[Decimal]) -> Optional[int]:
    return None if d is None else int(d.quantize(Decimal(1), ROUND_HALF_UP))


def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "").replace("đ", "d").replace("Đ", "D")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


