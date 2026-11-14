"""Vendor quotations: read the lines of a quote, check its arithmetic, compare vendors, score them.

Everything here is pure / deterministic (no AI): numbers are parsed and checked by code.
The AI is only used upstream to turn a scanned PDF into lines (spend/ai.py extract_quotation);
its output goes through the same checks and must be verified by a person.
"""
import difflib
import io
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------- numbers
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
            return Decimal(str(value))
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
        return Decimal(s)
    except InvalidOperation:
        return None


def to_int(d: Optional[Decimal]) -> Optional[int]:
    return None if d is None else int(d.quantize(Decimal(1), ROUND_HALF_UP))


def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "").replace("đ", "d").replace("Đ", "D")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# ---------------------------------------------------------------- reading tables
HEADER_KEYS = {
    "seq": ("stt", "#", "no", "tt"),
    "description": ("hang muc", "mo ta", "noi dung", "ten hang", "ten thiet bi", "dien giai", "components", "description", "item", "san pham"),
    "model": ("model", "ma hang", "part number", "p n", "ma sp", "thong so", "specification"),
    "unit": ("dvt", "don vi", "unit", "uom"),
    "qty": ("sl", "so luong", "qty", "quantity", "khoi luong"),
    "unit_price": ("don gia", "unit price", "price", "gia"),
    "amount": ("thanh tien", "amount", "total", "tong tien", "gia tri"),
}
SUMMARY_KEYS = [
    ("vat", ("vat", "thue gtgt", "thue")),
    ("discount", ("chiet khau", "giam gia", "discount")),
    ("total", ("tong cong", "grand total", "tong gia tri", "total amount", "tong thanh toan", "tong")),
    ("subtotal", ("cong", "sub total", "subtotal", "cong truoc thue", "truoc thue")),
]


@dataclass
class Line:
    line_type: str
    description: str
    model: Optional[str] = None
    unit: Optional[str] = None
    qty: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    amount: Optional[Decimal] = None


@dataclass
class Extracted:
    lines: List[Line] = field(default_factory=list)
    vat_pct: Optional[Decimal] = None
    subtotal: Optional[Decimal] = None
    discount: Optional[Decimal] = None
    vat: Optional[Decimal] = None
    total: Optional[Decimal] = None
    message: str = ""


def _header_map(row: Sequence[Any]) -> Dict[str, int]:
    found: Dict[str, int] = {}
    for idx, cell in enumerate(row):
        t = norm_text(str(cell)) if cell is not None else ""
        if not t:
            continue
        for key, words in HEADER_KEYS.items():
            if key in found:
                continue
            if any(t == w or t.startswith(w + " ") or (len(w) > 3 and w in t) for w in words):
                # "unit price" contains "price": keep the first match per column only
                if key == "amount" and "don gia" in t:
                    continue
                if key == "unit_price" and ("thanh tien" in t or t in ("total", "amount")):
                    continue
                found[key] = idx
                break
    return found


def _summary_type(text: str) -> Optional[str]:
    t = norm_text(text)
    if not t:
        return None
    for kind, words in SUMMARY_KEYS:
        if any(t == w or t.startswith(w + " ") or t.startswith(w + ":") for w in words):
            return kind
    return None


def lines_from_rows(rows: Sequence[Sequence[Any]]) -> Extracted:
    """Find the header row of a quotation table and read its lines (shared by Excel and Word)."""
    best, best_i = {}, -1
    for i, row in enumerate(rows[:60]):
        hm = _header_map(row)
        if "description" in hm and ("amount" in hm or "unit_price" in hm) and len(hm) > len(best):
            best, best_i = hm, i
    out = Extracted()
    if best_i < 0:
        out.message = "Không tìm thấy dòng tiêu đề bảng (Hạng mục / SL / Đơn giá / Thành tiền)."
        return out

    def cell(row, key):
        i = best.get(key)
        return row[i] if i is not None and i < len(row) else None

    for row in rows[best_i + 1:]:
        desc = cell(row, "description")
        desc_s = str(desc).strip() if desc is not None else ""
        label = " ".join(str(c) for c in row[: (best.get("amount") or len(row))] if c not in (None, "")).strip()
        amount = parse_number(cell(row, "amount"))
        qty, price = parse_number(cell(row, "qty")), parse_number(cell(row, "unit_price"))
        # A summary row (Cộng / VAT / Tổng / Chiết khấu) has no quantity and no unit price: an item
        # named "Tổng đài IP" with a quantity stays an item.
        kind = (_summary_type(desc_s) or _summary_type(label)) if qty is None and price is None else None
        if kind:
            if kind == "vat":
                m = re.search(r"(\d{1,2}(?:[.,]\d+)?)\s*%", label)
                if m:
                    out.vat_pct = parse_number(m.group(1))
            if amount is not None:
                setattr(out, kind, amount)
            out.lines.append(Line(kind, desc_s or label, amount=amount))
            continue
        if not desc_s:
            continue
        if amount is None and qty is None and price is None:
            out.lines.append(Line("note", desc_s))             # group heading ("I. Phần cứng")
            continue
        model = cell(row, "model")
        unit = cell(row, "unit")
        out.lines.append(Line("item", desc_s[:2000], model=str(model).strip()[:255] if model not in (None, "") else None,
                              unit=str(unit).strip()[:32] if unit not in (None, "") else None,
                              qty=qty, unit_price=price, amount=amount))
    if not any(l.line_type == "item" for l in out.lines):
        out.message = "Đã thấy tiêu đề bảng nhưng không đọc được dòng hàng nào."
    return out


def read_xlsx(data: bytes) -> Extracted:
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    best: Optional[Extracted] = None
    for ws in wb.worksheets[:10]:
        rows = [list(r) for r in ws.iter_rows(max_row=2000, max_col=40, values_only=True)]
        ex = lines_from_rows(rows)
        if best is None or sum(l.line_type == "item" for l in ex.lines) > sum(l.line_type == "item" for l in best.lines):
            best = ex
    return best or Extracted(message="File không có sheet nào.")


def read_docx(data: bytes) -> Extracted:
    import docx
    d = docx.Document(io.BytesIO(data))
    best: Optional[Extracted] = None
    for t in d.tables:
        rows = [[c.text for c in r.cells] for r in t.rows]
        ex = lines_from_rows(rows)
        if best is None or sum(l.line_type == "item" for l in ex.lines) > sum(l.line_type == "item" for l in best.lines):
            best = ex
    return best or Extracted(message="File Word không có bảng nào.")


# ---------------------------------------------------------------- checks
@dataclass
class Finding:
    code: str
    severity: str
    message: str
    line_index: Optional[int] = None
    expected: Optional[Decimal] = None
    actual: Optional[Decimal] = None


def vnd(v: Optional[Decimal]) -> str:
    if v is None:
        return "—"
    q = v.quantize(Decimal(1), ROUND_HALF_UP)
    return f"{int(q):,}".replace(",", ".")


def check(lines: Sequence[Line], subtotal: Optional[Decimal], discount: Optional[Decimal], vat: Optional[Decimal],
          total: Optional[Decimal], vat_pct: Optional[Decimal], currency: str = "VND",
          fx_rate: Optional[Decimal] = None) -> Tuple[List[Finding], Dict[str, Optional[Decimal]]]:
    """Arithmetic and completeness checks of one quotation. Returns findings and computed totals."""
    f: List[Finding] = []
    items = [(i, l) for i, l in enumerate(lines) if l.line_type == "item"]
    seen: Dict[str, int] = {}
    computed_sum = Decimal(0)
    for i, l in items:
        if l.qty is None or l.unit_price is None or l.amount is None:
            missing = [n for n, v in (("số lượng", l.qty), ("đơn giá", l.unit_price), ("thành tiền", l.amount)) if v is None]
            f.append(Finding("MISSING", "warn", f"Dòng “{l.description[:80]}” thiếu {', '.join(missing)}.", i))
        if l.amount is not None and l.amount == 0:
            f.append(Finding("ZERO", "warn", f"Dòng “{l.description[:80]}” có thành tiền bằng 0.", i))
        if l.qty is not None and l.unit_price is not None:
            expect = l.qty * l.unit_price
            if l.amount is not None and abs(expect - l.amount) > max(Decimal(1), abs(l.qty)):
                f.append(Finding("LINE", "crit", f"Dòng “{l.description[:80]}”: {vnd(l.qty)} × {vnd(l.unit_price)} = {vnd(expect)}, "
                                                 f"báo giá ghi {vnd(l.amount)} (lệch {vnd(l.amount - expect)}).", i, expect, l.amount))
        amount = l.amount if l.amount is not None else (l.qty * l.unit_price if l.qty is not None and l.unit_price is not None else None)
        if amount is not None:
            computed_sum += amount
        key = norm_text(f"{l.description} {l.model or ''}")
        if key and key in seen:
            f.append(Finding("DUP", "warn", f"Dòng “{l.description[:80]}” trùng với dòng {seen[key] + 1}.", i))
        seen.setdefault(key, i)
    tol = Decimal(1000)
    comp: Dict[str, Optional[Decimal]] = {"subtotal": computed_sum if items else None}
    if subtotal is not None and items and abs(subtotal - computed_sum) > tol:
        f.append(Finding("SUBTOTAL", "crit", f"Cộng các dòng = {vnd(computed_sum)}, báo giá ghi {vnd(subtotal)} (lệch {vnd(subtotal - computed_sum)}).",
                         None, computed_sum, subtotal))
    base = (subtotal if subtotal is not None else computed_sum) - (discount or 0)
    comp["afterDiscount"] = base if items or subtotal is not None else None
    if vat is not None and vat_pct is None and base:
        guess = (vat * 100 / base).quantize(Decimal("0.01"))
        for rate in (Decimal(0), Decimal(5), Decimal(8), Decimal(10)):
            if abs(guess - rate) <= Decimal("0.05"):
                vat_pct = rate
                break
    if vat_pct is not None:
        exp_vat = (base * vat_pct / 100).quantize(Decimal(1), ROUND_HALF_UP)
        comp["vat"] = exp_vat
        if vat is not None and abs(vat - exp_vat) > tol:
            f.append(Finding("VAT", "crit", f"VAT {vnd(vat_pct)}% của {vnd(base)} = {vnd(exp_vat)}, báo giá ghi {vnd(vat)}.", None, exp_vat, vat))
    else:
        comp["vat"] = vat
    exp_total = base + (vat if vat is not None else (comp["vat"] or 0))
    comp["total"] = exp_total
    if total is not None and abs(total - exp_total) > tol:
        f.append(Finding("TOTAL", "crit", f"Cộng – chiết khấu + VAT = {vnd(exp_total)}, báo giá ghi tổng {vnd(total)} (lệch {vnd(total - exp_total)}).",
                         None, exp_total, total))
    if not items:
        f.append(Finding("EMPTY", "crit", "Báo giá chưa có dòng hàng nào."))
    if currency != "VND" and not fx_rate:
        f.append(Finding("FX", "crit", f"Báo giá bằng {currency} nhưng chưa nhập tỷ giá quy đổi VND."))
    comp["vatPct"] = vat_pct
    return f, comp


# ---------------------------------------------------------------- comparison
def match_key(description: str, model: Optional[str]) -> str:
    m = norm_text(model or "")
    return f"m:{m}" if m else f"d:{norm_text(description)}"


def align(quotes: Sequence[Tuple[int, Sequence[Dict[str, Any]]]], threshold: float = 0.82) -> List[Dict[str, Any]]:
    """Group item lines of several quotations into comparison rows.
    quotes: [(quotation_id, [{id, description, model, unit, qty, unitPrice, amount, matchKey}])].
    Lines are grouped by manual matchKey, then model, then by description similarity (one line per
    quotation per row)."""
    rows: List[Dict[str, Any]] = []
    for qid, lines in quotes:
        for l in lines:
            key = l.get("matchKey") or match_key(l["description"], l.get("model"))
            target = next((r for r in rows if r["key"] == key and qid not in r["cells"]), None)
            if target is None and not l.get("matchKey"):
                desc = norm_text(l["description"])
                scored = [(difflib.SequenceMatcher(None, desc, r["norm"]).ratio(), r) for r in rows
                          if qid not in r["cells"] and not r["manual"]]
                scored = [s for s in scored if s[0] >= threshold]
                if scored:
                    target = max(scored, key=lambda s: s[0])[1]
            if target is None:
                target = {"key": key, "norm": norm_text(l["description"]), "description": l["description"], "unit": l.get("unit"),
                          "cells": {}, "manual": bool(l.get("matchKey"))}
                rows.append(target)
            target["cells"][qid] = l
    for r in rows:
        amounts = {q: c.get("amountVnd") for q, c in r["cells"].items() if c.get("amountVnd") is not None}
        r["minQuotation"] = min(amounts, key=amounts.get) if amounts else None
        lo = min(amounts.values()) if amounts else None
        r["spreadPct"] = (round(float((max(amounts.values()) - lo) * 100 / lo), 1) if amounts and lo else None)
        r.pop("norm", None)
    return rows


# ---------------------------------------------------------------- evaluation form
STANDARD_CRITERIA = [
    ("Chất lượng sản phẩm/dịch vụ", Decimal("0.15")), ("Giá thành", Decimal("0.15")), ("Mức độ uy tín NCC", Decimal("0.10")),
    ("Kinh nghiệm NCC", Decimal("0.10")), ("Dịch vụ trước/sau bán hàng", Decimal("0.10")),
    ("Tuân thủ các quy định về bảo mật và an toàn thông tin", Decimal("0.10")),
]
EXTENDED_DEFAULTS = {
    "cabling": ["Mức độ hiểu biết về hạ tầng cáp mạng của HSC", "Linh hoạt xử lý các vấn đề phát sinh do thay đổi thiết kế",
                "Đáp ứng yêu cầu tiến độ dự án"],
    "network": ["Hiểu biết về mô hình kết nối mạng ở các dự án cải tạo trước đó",
                "Hỗ trợ cho mượn thiết bị tương đương để vận hành trước", "Đáp ứng yêu cầu tiến độ dự án"],
    "vc": ["Có kinh nghiệm tư vấn và triển khai các giải pháp VC", "Hỗ trợ cho mượn thiết bị", "Đáp ứng yêu cầu tiến độ dự án"],
    "other": ["Đáp ứng yêu cầu tiến độ dự án", "Linh hoạt xử lý các vấn đề phát sinh"],
}
WORKSTREAM_LABELS = {"cabling": "Cabling (gồm Hub)", "network": "Network", "vc": "Video Conference", "other": "Khác"}


def classify(total: Optional[Decimal]) -> str:
    """Same thresholds as the IFS formula of the EVALUATION FORM: >= 8 A, >= 7 B, >= 6 C."""
    if total is None:
        return ""
    if total >= 8:
        return "A"
    if total >= 7:
        return "B"
    if total >= 6:
        return "C"
    return "Không đạt"


def validate_criteria(criteria: Sequence[Tuple[str, str, Decimal]]) -> Optional[str]:
    """(section, name, weight) -> error message or None. A = 70 %, B = 30 % with 2–4 criteria."""
    a = [w for s, _, w in criteria if s == "A"]
    b = [w for s, _, w in criteria if s == "B"]
    if not 2 <= len(b) <= 4:
        return "Khung đánh giá mở rộng (B) cần tối thiểu 02, tối đa 04 tiêu chí."
    if sum(a) != Decimal("0.7"):
        return f"Tổng tỷ trọng khung tiêu chuẩn (A) phải là 70% (đang {sum(a) * 100:.0f}%)."
    if sum(b) != Decimal("0.3"):
        return f"Tổng tỷ trọng khung mở rộng (B) phải là 30% (đang {sum(b) * 100:.0f}%)."
    if any(not n.strip() for _, n, _ in criteria):
        return "Tên tiêu chí không được để trống."
    return None


def weighted_total(weights_scores: Iterable[Tuple[Decimal, Optional[Decimal]]]) -> Optional[Decimal]:
    pairs = list(weights_scores)
    if not pairs or any(s is None for _, s in pairs):
        return None
    return sum((w * s for w, s in pairs), Decimal(0)).quantize(Decimal("0.01"))


def suggest_price_score(totals: Dict[int, Optional[Decimal]]) -> Dict[int, Decimal]:
    """Cheapest = 10; others lose 1 point per 5 % above the cheapest (min 1)."""
    valid = {k: v for k, v in totals.items() if v}
    if not valid:
        return {}
    lo = min(valid.values())
    out = {}
    for k, v in valid.items():
        pct = (v - lo) * 100 / lo
        out[k] = max(Decimal(1), (Decimal(10) - pct / 5).quantize(Decimal("0.5"), ROUND_HALF_UP))
    return out
