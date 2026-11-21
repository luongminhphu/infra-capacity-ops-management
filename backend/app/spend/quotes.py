"""Vendor quotations: read the lines of a quote, check its arithmetic, compare vendors, score them.

Everything here is pure / deterministic (no AI): numbers are parsed and checked by code.
The AI is only used upstream to turn a scanned PDF into lines (spend/ai.py extract_quotation);
its output goes through the same checks and must be verified by a person.
"""
import difflib
import io
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------- numbers
from ..shared.text import MAX_ABS, _bounded, norm_text, parse_number, to_int  # noqa: E402,F401  (shared; old names kept)


# ---------------------------------------------------------------- reading tables
HEADER_KEYS = {
    "seq": ("stt", "#", "no", "tt"),
    "description": ("hang muc", "mo ta", "noi dung", "ten hang", "ten thiet bi", "dien giai", "components", "description", "item", "san pham"),
    "model": ("model", "ma hang", "part number", "p n", "ma sp", "thong so", "specification"),
    "unit": ("dvt", "don vi", "unit", "uom"),
    "qty": ("sl", "so luong", "qty", "quantity", "khoi luong"),
    "unit_price": ("don gia", "unit price", "price", "gia"),
    "amount": ("thanh tien", "amount", "total", "tong tien", "gia tri"),
    "vat": ("thue gtgt", "vat", "thue"),
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
    vat_pct: Optional[Decimal] = None          # per-line rate as printed (0 = KCT / "-"); None = quotation rate
    vat_amount: Optional[Decimal] = None       # per-line tax as printed
    sets: Optional[Decimal] = None             # bundle multiplier (None = 1)


def base_amount(l: "Line") -> Optional[Decimal]:
    """Thành tiền of one printed line (amount, else qty × unit price)."""
    if l.amount is not None:
        return l.amount
    if l.qty is not None and l.unit_price is not None:
        return l.qty * l.unit_price
    return None


def line_total(l: "Line") -> Optional[Decimal]:
    """What the line adds to the quotation: thành tiền × số bộ."""
    a = base_amount(l)
    return None if a is None else a * (l.sets if l.sets is not None else 1)


VAT_RATES = (Decimal(0), Decimal(5), Decimal(8), Decimal(10))


def line_vat_pct(l: "Line") -> Optional[Decimal]:
    """Rate printed on the line, or read back from the printed tax amount (8.148.800 of 101.860.000 = 8 %)."""
    if l.vat_pct is not None:
        return l.vat_pct
    a = base_amount(l)
    if l.vat_amount is not None and a:
        guess = l.vat_amount * 100 / a
        for r in VAT_RATES:
            if abs(guess - r) <= Decimal("0.05"):
                return r
    if l.vat_amount is not None and l.vat_amount == 0:
        return Decimal(0)
    return None


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
                # "Đơn giá trước thuế GTGT" / "Thành tiền sau thuế GTGT" are not the tax column
                if key == "vat" and (t.startswith("thanh tien") or t.startswith("don gia") or "sau thue" in t or "bao gom" in t):
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
        vat_cell = cell(row, "vat")
        vat_amt = parse_number(vat_cell)
        if vat_amt is None and isinstance(vat_cell, str) and (vat_cell.strip() in ("-", "–", "—")
                                                             or norm_text(vat_cell) in ("kct", "khong chiu thue")):
            vat_amt = Decimal(0)
        out.lines.append(Line("item", desc_s[:2000], model=str(model).strip()[:255] if model not in (None, "") else None,
                              unit=str(unit).strip()[:32] if unit not in (None, "") else None,
                              qty=qty, unit_price=price, amount=amount, vat_amount=vat_amt))
    if not any(l.line_type == "item" for l in out.lines):
        out.message = "Đã thấy tiêu đề bảng nhưng không đọc được dòng hàng nào."
    return out


from ..shared.xlsx_io import MAX_PART, MAX_TOTAL, guard_zip as _guard_zip  # noqa: E402,F401  (shared guard; old name kept)


def read_xlsx(data: bytes) -> Extracted:
    try:
        return _read_xlsx(data)
    except Exception as exc:              # malformed file: report, never 500
        return Extracted(message=f"Không đọc được file Excel ({type(exc).__name__}) — nhập dòng bằng tay.")


def read_docx(data: bytes) -> Extracted:
    try:
        return _read_docx(data)
    except Exception as exc:
        return Extracted(message=f"Không đọc được file Word ({type(exc).__name__}) — nhập dòng bằng tay.")


def _read_xlsx(data: bytes) -> Extracted:
    from ..shared import xlsx_io
    best: Optional[Extracted] = None
    for _title, rows in xlsx_io.read_rows(data, max_sheets=10, max_rows=2000, max_cols=40):
        ex = lines_from_rows(rows)
        if best is None or sum(l.line_type == "item" for l in ex.lines) > sum(l.line_type == "item" for l in best.lines):
            best = ex
    return best or Extracted(message="File không có sheet nào.")


def _read_docx(data: bytes) -> Extracted:
    import docx
    _guard_zip(data)
    d = docx.Document(io.BytesIO(data))
    best: Optional[Extracted] = None
    for t in d.tables:
        rows = [[c.text for c in r.cells] for r in t.rows]
        ex = lines_from_rows(rows)
        if best is None or sum(l.line_type == "item" for l in ex.lines) > sum(l.line_type == "item" for l in best.lines):
            best = ex
    return best or Extracted(message="File Word không có bảng nào.")


# ---------------------------------------------------------------- column limits
# What the columns hold (PostgreSQL rejects more with "numeric field overflow"; SQLite does not check).
LIMITS = {"qty": Decimal("1e12"), "unit_price": Decimal("1e15"), "amount": Decimal("1e15"), "vat_amount": Decimal("1e15"),
          "vat_pct": Decimal(100), "sets": Decimal(10_000)}


def fit(v: Optional[Decimal], key: str) -> Optional[Decimal]:
    """A number read from a file / the AI that cannot be right for its column (a tax amount read as a rate, a
    misaligned scan cell read as a quantity) becomes empty: the person checking the quotation fills it in."""
    if v is None:
        return None
    if key in ("vat_pct", "sets") and v < 0:
        return None
    return v if abs(v) < LIMITS[key] else None


def fit_line(l: "Line") -> int:
    """Fit every number of a line in place; returns how many were dropped."""
    dropped = 0
    for key in ("qty", "unit_price", "amount", "vat_amount", "vat_pct", "sets"):
        v = getattr(l, key)
        nv = fit(v, key)
        if v is not None and nv is None:
            dropped += 1
        setattr(l, key, nv)
    return dropped


# ---------------------------------------------------------------- bundles ("Phòng họp × 2" + per-room breakdown)
SUMMARY_TYPES = ("subtotal", "total", "vat", "discount")


def _close(a: Decimal, b: Decimal, tol: Decimal = Decimal(1000)) -> bool:
    return abs(a - b) <= tol


def items_sum(lines: Sequence[Line]) -> Decimal:
    return sum((line_total(l) or Decimal(0) for l in lines if l.line_type == "item"), Decimal(0))


def _bundle_like(l: Line) -> bool:
    """The next bundle of a multi-bundle quotation ("Phòng loại A × 2", then "Phòng loại B × 3")."""
    return (l.line_type in ("item", "note") and l.qty is not None and l.qty >= 2 and l.qty == l.qty.to_integral_value()
            and (l.unit_price is not None or l.amount is not None))


def normalize_bundles(lines: List[Line], stated_subtotal: Optional[Decimal]) -> List[str]:
    """Vendors quote a room / kit N times in three ways:
      A. a bundle line "Phòng họp — SL 2 — đơn giá X — thành tiền 2X" then the breakdown of ONE room (sum X);
      B. the same bundle as a heading (no amount) and the breakdown of one room;
      C. the breakdown already multiplied (SL 2 on every line) — nothing to do.
    A and B are detected only when the breakdown that follows adds up exactly to the bundle's unit price; the
    bundle becomes a heading ("note") and its breakdown counts × N (Line.sets). Applied only when it makes the
    lines agree with the subtotal the vendor printed (or no subtotal is printed). Changes `lines` in place and
    returns one sentence per bundle for the person who checks the quotation."""
    if stated_subtotal is not None and _close(items_sum(lines), stated_subtotal):
        return []
    plan = []                                   # (bundle index, [breakdown indexes], n)
    for i, b in enumerate(lines):
        if (b.line_type not in ("item", "note") or b.qty is None or b.qty < 2 or b.qty >= LIMITS["sets"]
                or b.qty != b.qty.to_integral_value()):
            continue
        unit = b.unit_price if b.unit_price is not None else (b.amount / b.qty if b.amount is not None else None)
        if not unit:
            continue
        seg, run = [], Decimal(0)
        for j in range(i + 1, len(lines)):
            l = lines[j]
            if l.line_type in SUMMARY_TYPES:
                break
            if l.line_type != "item":
                continue
            a = base_amount(l)
            if a is None:
                continue
            seg.append(j)
            run += a
            if len(seg) >= 2 and _close(run, unit):
                nxt = next((lines[k] for k in range(j + 1, len(lines)) if lines[k].line_type != "note"), None)
                if nxt is None or nxt.line_type in SUMMARY_TYPES or _bundle_like(nxt):
                    plan.append((i, list(seg), b.qty))
                break
            if run > unit + 1000:
                break
    if not plan:
        return []
    saved = [(l.line_type, l.sets) for l in lines]
    notes = []
    for i, seg, n in plan:
        b = lines[i]
        b.line_type = "note"
        for j in seg:
            lines[j].sets = (lines[j].sets or Decimal(1)) * n
        notes.append(f"“{b.description[:60]}” là gói {vnd(n)} × {vnd(b.unit_price or (b.amount / n))}; "
                     f"{len(seg)} dòng chi tiết bên dưới là cho 1 gói nên được nhân {vnd(n)}.")
    if stated_subtotal is not None and not _close(items_sum(lines), stated_subtotal):
        for l, (t, st) in zip(lines, saved):             # does not explain the vendor's subtotal: undo
            l.line_type, l.sets = t, st
        return []
    return notes


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
          fx_rate: Optional[Decimal] = None) -> Tuple[List[Finding], Dict[str, Any]]:
    """Arithmetic and completeness checks of one quotation. Returns findings and computed totals.
    A line counts thành tiền × số bộ; VAT is computed line by line when the quotation prints per-line tax
    (hardware 8 %, software KCT), otherwise at the quotation rate."""
    discount = abs(discount) if discount is not None else None      # Excel quotes print it "-5.000.000" or "5.000.000"
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
        base = base_amount(l)
        if l.vat_pct is not None and l.vat_amount is not None and base is not None:
            exp_line_vat = (base * l.vat_pct / 100).quantize(Decimal(1), ROUND_HALF_UP)
            if abs(exp_line_vat - l.vat_amount) > Decimal(1000):
                f.append(Finding("LINEVAT", "warn", f"Dòng “{l.description[:80]}”: VAT {vnd(l.vat_pct)}% của {vnd(base)} = {vnd(exp_line_vat)}, "
                                                    f"báo giá ghi {vnd(l.vat_amount)}.", i, exp_line_vat, l.vat_amount))
        amount = line_total(l)
        if amount is not None:
            computed_sum += amount
        key = norm_text(f"{l.description} {l.model or ''}")
        if key and key in seen:
            f.append(Finding("DUP", "warn", f"Dòng “{l.description[:80]}” trùng với dòng {seen[key] + 1}.", i))
        seen.setdefault(key, i)
    tol = Decimal(1000)
    comp: Dict[str, Any] = {"subtotal": computed_sum if items else None}
    if subtotal is not None and items and abs(subtotal - computed_sum) > tol:
        f.append(Finding("SUBTOTAL", "crit", f"Cộng các dòng = {vnd(computed_sum)}, báo giá ghi {vnd(subtotal)} (lệch {vnd(subtotal - computed_sum)})."
                                             + _explain_subtotal(lines, computed_sum, subtotal), None, computed_sum, subtotal))
    base = (subtotal if subtotal is not None else computed_sum) - (discount or 0)
    comp["afterDiscount"] = base if items or subtotal is not None else None
    # Per-line VAT: the quotation prints tax per line (or marks lines KCT / "-").
    rates = {i: line_vat_pct(l) for i, l in items}
    per_line = any(r is not None for r in rates.values())
    if per_line:
        default = vat_pct if vat_pct is not None else _common_rate(rates.values())
        exp_vat = Decimal(0)
        for i, l in items:
            amt = line_total(l) or Decimal(0)
            r = rates[i] if rates[i] is not None else default
            if r is None:
                continue
            exp_vat += amt * r / 100
        if discount and computed_sum:
            exp_vat = exp_vat * (computed_sum - discount) / computed_sum
        exp_vat = exp_vat.quantize(Decimal(1), ROUND_HALF_UP)
        used = sorted({r for r in rates.values() if r is not None} | ({default} if default is not None and None in rates.values() else set()))
        comp["vat"], comp["vatRates"] = exp_vat, [float(r) for r in used]
        vat_pct = vat_pct if vat_pct is not None else default
        if vat is not None and abs(vat - exp_vat) > tol:
            f.append(Finding("VAT", "crit", f"VAT theo từng dòng ({_rates_text(used)}) = {vnd(exp_vat)}, báo giá ghi {vnd(vat)}.", None, exp_vat, vat))
    else:
        if vat is not None and vat_pct is None and base:
            guess = (vat * 100 / base).quantize(Decimal("0.01"))
            for rate in VAT_RATES:
                if abs(guess - rate) <= Decimal("0.05"):
                    vat_pct = rate
                    break
        if vat_pct is not None:
            exp_vat = (base * vat_pct / 100).quantize(Decimal(1), ROUND_HALF_UP)
            comp["vat"] = exp_vat
            comp["vatRates"] = [float(vat_pct)]
            if vat is not None and abs(vat - exp_vat) > tol:
                f.append(Finding("VAT", "crit", f"VAT {vnd(vat_pct)}% của {vnd(base)} = {vnd(exp_vat)}, báo giá ghi {vnd(vat)}."
                                                + _explain_vat(lines, base, vat, vat_pct), None, exp_vat, vat))
        else:
            comp["vat"] = vat
            comp["vatRates"] = []
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


def _common_rate(rates: Iterable[Optional[Decimal]]) -> Optional[Decimal]:
    """The rate most lines carry (hardware 8 %): applied to lines that print no rate."""
    counts: Dict[Decimal, int] = {}
    for r in rates:
        if r is not None and r > 0:
            counts[r] = counts.get(r, 0) + 1
    return max(counts, key=counts.get) if counts else None


def _rates_text(rates: Sequence[Decimal]) -> str:
    return " / ".join("KCT" if r == 0 else f"{vnd(r)}%" for r in rates) or "—"


SOFTWARE_WORDS = ("license", "licence", "ban quyen", "phan mem", "software", "subscription", "thue bao", "cloud")


def _explain_subtotal(lines: Sequence[Line], computed: Decimal, stated: Decimal) -> str:
    """One hint for the person checking: the usual reasons a line sum differs from the vendor's subtotal."""
    items = [l for l in lines if l.line_type == "item" and line_total(l) is not None]
    diff = computed - stated
    if diff > 0:
        for l in items:
            if _close(line_total(l), stated) and len(items) > 2:
                return (f" Dòng “{l.description[:60]}” đã bằng tổng báo giá: đây là dòng gói, các dòng còn lại là chi tiết của nó — "
                        f"đổi loại dòng gói thành “Tiêu đề nhóm” (và đặt Số bộ cho các dòng chi tiết nếu gói có SL > 1).")
        for l in items:
            if _close(line_total(l), diff):
                return (f" Phần chênh bằng đúng dòng “{l.description[:60]}”: đây có thể là dòng gói / dòng cộng nhóm "
                        f"bị tính trùng với các dòng chi tiết — đổi loại dòng thành “Tiêu đề nhóm”.")
    if computed and stated:
        for k in range(2, 51):
            if _close(computed * k, stated):
                return (f" Báo giá ghi gấp {k} lần cộng các dòng: có thể các dòng là chi tiết cho 1 phòng / bộ và báo giá tính {k} bộ — "
                        f"đặt Số bộ = {k} cho các dòng chi tiết.")
            if _close(stated * k, computed):
                return f" Cộng các dòng gấp {k} lần báo giá ghi: có thể đã nhân số bộ hai lần."
    return ""


def _explain_vat(lines: Sequence[Line], base: Decimal, vat: Decimal, rate: Decimal) -> str:
    """VAT lower than rate × base: some lines are not taxed (software KCT). Name them when they explain it."""
    if not rate or vat >= base * rate / 100:
        return ""
    exempt = base - vat * 100 / rate
    tol = max(Decimal(1000), Decimal(1000) * 100 / rate)
    items = [l for l in lines if l.line_type == "item" and line_total(l) is not None]
    soft = [l for l in items if any(w in norm_text(f"{l.description} {l.model or ''}") for w in SOFTWARE_WORDS)]
    if soft and _close(sum(line_total(l) for l in soft), exempt, tol):
        names = ", ".join(f"“{l.description[:40]}”" for l in soft[:4])
        return (f" Các dòng {names} (cộng {vnd(sum(line_total(l) for l in soft))}) có thể không chịu VAT (KCT): khi đó VAT khớp báo giá — "
                f"đặt VAT % = 0 cho các dòng này.")
    return f" Có khoảng {vnd(exempt)} không chịu VAT {vnd(rate)}% (phần mềm / dịch vụ KCT?) — kiểm cột thuế từng dòng."


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
    if not 1 <= len(a) <= 10:
        return "Khung đánh giá tiêu chuẩn (A) có từ 01 đến 10 tiêu chí."
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


PRICE_WORDS = ("gia thanh", "gia ca", "chi phi", "price", "cost")


def is_price_criterion(name: str) -> bool:
    t = norm_text(name)
    return t == "gia" or any(w in t for w in PRICE_WORDS)


def suggest_evaluation(criteria: Sequence[Tuple[int, str]], totals: Dict[int, Optional[Decimal]],
                       labels: Dict[int, str]) -> Dict[str, Any]:
    """Starting scores for the EVALUATION FORM, price first: the price criterion ("Giá thành") scores by price
    (suggest_price_score: cheapest 10, −1 per 5 % above), every other criterion 10; the person edits them.
    The conclusion proposes the cheapest vendor — with the others at 10 it also has the best weighted total."""
    price = suggest_price_score(totals)
    scores: Dict[int, Dict[int, Decimal]] = {}
    price_ids = []
    for cid, name in criteria:
        if is_price_criterion(name):
            price_ids.append(cid)
            scores[cid] = {q: price[q] for q in totals if q in price}
        else:
            scores[cid] = {q: Decimal(10) for q in totals}
    valid = {q: v for q, v in totals.items() if v}
    if not valid:
        return {"scores": scores, "priceCriteria": price_ids, "pick": None, "conclusion": None}
    pick = min(valid, key=lambda q: (valid[q], q))
    lo = valid[pick]
    others = sorted((q for q in valid if q != pick), key=lambda q: valid[q])
    pct = lambda q: f"{(valid[q] - lo) * 100 / lo:.1f}".replace(".", ",")      # vi-VN decimal comma
    diff = ", ".join(f"{labels.get(q, q)} +{pct(q)}%" for q in others)
    text = f"Đề xuất chọn {labels.get(pick, pick)} — giá thấp nhất {vnd(lo)} đ" + (f" ({diff})" if diff else "")
    return {"scores": scores, "priceCriteria": price_ids, "pick": pick, "conclusion": text[:255]}
