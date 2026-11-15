"""Nhập Opex / Capex từ file Excel đang quản lý (docs/design/spend-import.md).

Two steps, both stateless — the file is sent again on commit so what is written is exactly what
was checked:

1. `preview(kind, data)` reads the workbook (shared.xlsx_io: zip-bomb guard, bounded), finds the
   header row by column aliases (Vietnamese / English, accents and case ignored), turns every row
   into the payload the normal create paths take, and reports per row: create / skip (duplicate)
   / error, with warnings.
2. `commit(...)` re-runs the preview and writes the valid rows in ONE transaction through the same
   service functions as the screens (service.create for Opex; the Capex router helpers + reconcile
   for projects), so every rule, code sequence and audit entry is the usual one.

History: an Opex row followed in the file by the next term of the same service (same title and
vendor, starting within 31 days after it ends) is imported as "Đã gia hạn" and linked to its
successor (renewed_from), so renewal chains, the budget plan and plan-vs-actual work on old data.
"""
import datetime as dt
from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, Request
from sqlalchemy import update
from sqlalchemy.orm import Session

from ..models import User
from ..shared.text import norm_text, parse_number, to_int
from ..shared.xlsx_io import XlsxError, read_rows
from . import fiscal, service
from .models import CapexProject, OpexItem, SpendCategory, SpendCostCenter, SpendSequence, SpendVendor
from .settings import SpendSettings

MAX_ROWS = 2000
HEADER_SCAN = 20
CHAIN_GAP_DAYS = 31

# field -> aliases (compared after norm_text). The first alias is the template header.
OPEX_COLUMNS: Dict[str, List[str]] = {
    "code": ["Mã Opex", "ma", "ma opex", "code"],
    "category": ["Loại chi phí", "loai", "loai chi phi", "category", "nhom chi phi"],
    "title": ["Nội dung dịch vụ", "noi dung", "dich vu", "ten dich vu", "title", "service", "description of service"],
    "vendor": ["Nhà cung cấp", "ncc", "nha cung cap", "vendor", "supplier", "doi tac"],
    "contractRef": ["Số hợp đồng", "so hd", "ma hop dong", "hop dong", "contract", "contract no"],
    "poRef": ["Số PO", "po", "ma po", "po number"],
    "startDate": ["Ngày bắt đầu", "bat dau", "tu ngay", "start", "start date", "ngay hieu luc"],
    "endDate": ["Ngày kết thúc", "ket thuc", "den ngay", "het han", "ngay het han", "end", "end date", "expiry"],
    "months": ["Thời hạn (tháng)", "thoi han", "so thang", "term months", "months"],
    "cycle": ["Chu kỳ", "chu ky", "chu ky thanh toan", "billing cycle", "cycle"],
    "amountNet": ["Số tiền trước VAT", "truoc vat", "gia tri truoc vat", "so tien", "thanh tien", "amount", "net"],
    "vatPct": ["VAT (%)", "vat", "thue", "thue suat", "vat rate"],
    "amountGross": ["Số tiền sau VAT", "sau vat", "gia tri sau vat", "tong tien", "gross", "total"],
    "fiscalYear": ["Năm tài chính", "nam tai chinh", "ntc", "nam", "fiscal year", "fy"],
    "costCenter": ["Cost center", "cost center", "trung tam chi phi", "phong ban"],
    "owner": ["Người phụ trách", "phu trach", "nguoi phu trach", "owner"],
    "ownerEmail": ["Email phụ trách", "email", "email phu trach"],
    "status": ["Trạng thái", "trang thai", "status"],
    "stopReason": ["Lý do ngừng", "ly do ngung", "ly do"],
    "serials": ["Serial thiết bị", "serial", "serials", "so serial"],
    "description": ["Ghi chú", "ghi chu", "mo ta", "note", "notes"],
}
CAPEX_COLUMNS: Dict[str, List[str]] = {
    "project": ["Tên dự án", "du an", "ten du an", "project", "project name"],
    "fiscalYear": ["Năm tài chính", "nam tai chinh", "ntc", "nam", "fiscal year", "fy"],
    "goLive": ["Ngày go-live", "go live", "ngay go live", "ngay dua vao su dung", "go live date"],
    "projectStatus": ["Trạng thái dự án", "trang thai du an", "trang thai", "status"],
    "owner": ["Người phụ trách", "phu trach", "nguoi phu trach", "owner"],
    "costCenter": ["Cost center", "cost center", "trung tam chi phi", "phong ban"],
    "category": ["Loại chi phí", "loai", "loai chi phi", "category"],
    "item": ["Hạng mục", "hang muc", "mo ta hang muc", "item", "description", "thiet bi"],
    "vendor": ["Nhà cung cấp", "ncc", "nha cung cap", "vendor", "supplier"],
    "quantity": ["Số lượng", "so luong", "sl", "qty", "quantity"],
    "amountNet": ["Số tiền trước VAT", "truoc vat", "gia tri truoc vat", "thanh tien", "so tien", "amount", "net"],
    "vatPct": ["VAT (%)", "vat", "thue", "thue suat"],
    "amountGross": ["Số tiền sau VAT", "sau vat", "gia tri sau vat", "tong tien", "gross", "total"],
    "startDate": ["Ngày bắt đầu bảo hành", "bat dau bao hanh", "ngay bat dau", "warranty start", "start date"],
    "coverageYears": ["Số năm bảo hành / license", "so nam bao hanh", "nam bao hanh", "bao hanh nam", "coverage years", "warranty years"],
    "renewalPct": ["% gia hạn", "phan tram gia han", "ty le gia han", "renewal pct", "renewal %"],
    "renewalAmount": ["Số tiền gia hạn", "tien gia han", "renewal amount"],
}
REQUIRED = {"opex": ("category", "title", "vendor", "startDate", "amountNet"), "capex": ("project", "category", "item", "amountNet")}

CYCLES = {"yearly": ["hang nam", "nam", "yearly", "annual", "annually", "12 thang"], "monthly": ["hang thang", "thang", "monthly"],
          "quarterly": ["hang quy", "quy", "quarterly"], "one_time": ["mot lan", "1 lan", "one time", "once", "onetime"]}
OPEX_STATUSES = {"planned": ["du kien", "planned", "ke hoach"], "active": ["dang hieu luc", "hieu luc", "active", "dang dung", "con han"],
                 "renewed": ["da gia han", "renewed", "het han da gia han"], "stopped": ["ngung", "da ngung", "stopped", "huy", "khong gia han"]}
CAPEX_STATUSES = {"draft": ["nhap", "draft", "de xuat"], "approved": ["da duyet", "duyet", "approved"],
                  "in_progress": ["dang trien khai", "trien khai", "in progress"], "closed": ["da dong", "dong", "hoan thanh", "closed", "done"]}


class ImportError_(ValueError):
    pass


# ---------------------------------------------------------------- reading
def _match(value: Any, table: Dict[str, List[str]]) -> Optional[str]:
    v = norm_text(str(value or ""))
    if not v:
        return None
    for key, aliases in table.items():
        if v == key.replace("_", " ") or v in aliases:
            return key
    return None


def _find_header(rows: List[List[Any]], columns: Dict[str, List[str]]) -> Tuple[int, Dict[str, int]]:
    """Index of the header row and field -> column. Exact alias first, then 'header contains alias'."""
    best = (-1, {})
    norm_alias = {f: [norm_text(a) for a in al] for f, al in columns.items()}
    for i, row in enumerate(rows[:HEADER_SCAN]):
        cells = [norm_text(str(c)) if c is not None else "" for c in row]
        found: Dict[str, int] = {}
        for f, al in norm_alias.items():
            for j, c in enumerate(cells):
                if c and c in al and j not in found.values():
                    found[f] = j
                    break
        for f, al in norm_alias.items():
            if f in found:
                continue
            for j, c in enumerate(cells):
                if c and j not in found.values() and any(len(a) >= 4 and a in c for a in al):
                    found[f] = j
                    break
        if len(found) > len(best[1]):
            best = (i, found)
    return best


def _date(v: Any) -> Optional[dt.date]:
    if v in (None, ""):
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    s = str(v).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"ngày “{s[:20]}” không đọc được (dùng dd/mm/yyyy)")


def _money(v: Any, label: str) -> Optional[int]:
    if v in (None, ""):
        return None
    d = parse_number(v)
    if d is None or d < 0:
        raise ValueError(f"{label} “{str(v)[:20]}” không phải số tiền")
    return to_int(d)


def _pct(v: Any, label: str) -> Optional[Decimal]:
    if v in (None, ""):
        return None
    d = parse_number(str(v).replace("%", "")) if not isinstance(v, (int, float)) else Decimal(str(v))
    if d is None:
        raise ValueError(f"{label} “{str(v)[:20]}” không hợp lệ")
    if Decimal(0) < d < 1 and isinstance(v, float):
        d = d * 100                       # Excel percent cell: 0.1 = 10 %
    if not Decimal(0) <= d <= Decimal(100):
        raise ValueError(f"{label} phải từ 0 đến 100")
    return d.quantize(Decimal("0.01"))


def _text(v: Any, n: int) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()[:n]


class _Refs:
    """Categories / cost centres / vendors resolved once (by name, code or alias)."""

    def __init__(self, db: Session):
        self.db = db
        self.cats: Dict[str, SpendCategory] = {}
        for c in db.query(SpendCategory).all():
            self.cats[norm_text(c.name)] = c
            self.cats[norm_text(c.code)] = c
        self.ccs: Dict[str, SpendCostCenter] = {}
        for c in db.query(SpendCostCenter).all():
            self.ccs[norm_text(c.code)] = c
            self.ccs[norm_text(c.name)] = c
        self._vendors: Dict[str, Optional[SpendVendor]] = {}

    def category(self, v: Any) -> Optional[SpendCategory]:
        return self.cats.get(norm_text(str(v or "")))

    def cost_center(self, v: Any) -> Optional[SpendCostCenter]:
        return self.ccs.get(norm_text(str(v or "")))

    def vendor(self, name: str) -> Optional[SpendVendor]:
        key = norm_text(name)
        if key not in self._vendors:
            self._vendors[key] = service.find_vendor(self.db, name)
        return self._vendors[key]


def _sheet(data: bytes, kind: str) -> Tuple[str, int, Dict[str, int], List[List[Any]]]:
    columns = OPEX_COLUMNS if kind == "opex" else CAPEX_COLUMNS
    try:
        sheets = read_rows(data, max_rows=MAX_ROWS + HEADER_SCAN, max_cols=40)
    except XlsxError as exc:
        raise ImportError_(str(exc)) from None
    best = None
    for title, rows in sheets:
        h, cols = _find_header(rows, columns)
        if h >= 0 and (best is None or len(cols) > len(best[2])):
            best = (title, h, cols, rows)
    need = REQUIRED[kind]
    if best is None or sum(1 for f in need if f in best[2]) < len(need) - 1:
        labels = ", ".join(columns[f][0] for f in need)
        raise ImportError_(f"Không tìm thấy dòng tiêu đề. File cần các cột: {labels} (tải file mẫu để xem).")
    missing = [columns[f][0] for f in need if f not in best[2]]
    if missing:
        raise ImportError_(f"Thiếu cột bắt buộc: {', '.join(missing)}.")
    return best


# ---------------------------------------------------------------- Opex
def _opex_rows(db: Session, data: bytes, cfg: SpendSettings, user: User, today: dt.date) -> Dict[str, Any]:
    title, h, cols, rows = _sheet(data, "opex")
    refs = _Refs(db)
    out: List[Dict[str, Any]] = []
    get = lambda r, f: (r[cols[f]] if f in cols and cols[f] < len(r) else None)  # noqa: E731
    for i, r in enumerate(rows[h + 1:], start=h + 2):
        if not any(c not in (None, "") for c in r):
            continue
        if len(out) >= MAX_ROWS:
            raise ImportError_(f"Tối đa {MAX_ROWS} dòng mỗi lần nhập.")
        row = {"row": i, "errors": [], "warnings": [], "action": "create"}
        try:
            cat = refs.category(get(r, "category"))
            if not cat:
                row["errors"].append(f"Loại chi phí “{_text(get(r, 'category'), 40)}” không có trong danh mục")
            t = _text(get(r, "title"), 255)
            if not t:
                row["errors"].append("Thiếu nội dung dịch vụ")
            vname = _text(get(r, "vendor"), 255)
            if not vname:
                row["errors"].append("Thiếu nhà cung cấp")
            start = _date(get(r, "startDate"))
            end = _date(get(r, "endDate"))
            months = to_int(parse_number(get(r, "months"))) if get(r, "months") not in (None, "") else None
            if start is None:
                row["errors"].append("Thiếu ngày bắt đầu")
            elif end is None and months:
                from .budget_plan import add_months
                end = add_months(start, months) - dt.timedelta(days=1)
            elif end is None:
                end = fiscal.term_end(start, 1)
                row["warnings"].append("Không có ngày kết thúc — tính 1 năm")
            if start and end and end < start:
                row["errors"].append("Ngày kết thúc trước ngày bắt đầu")
            cycle = _match(get(r, "cycle"), CYCLES) if get(r, "cycle") not in (None, "") else "yearly"
            if cycle is None:
                row["errors"].append(f"Chu kỳ “{_text(get(r, 'cycle'), 20)}” không rõ (Hằng năm / Hằng quý / Hằng tháng / Một lần)")
            net = _money(get(r, "amountNet"), "Số tiền trước VAT")
            vat = _pct(get(r, "vatPct"), "VAT")
            gross = _money(get(r, "amountGross"), "Số tiền sau VAT")
            if net is None and gross is not None:
                v = vat if vat is not None else Decimal(10)
                net = to_int(Decimal(gross) * 100 / (100 + v))
                row["warnings"].append("Tính số tiền trước VAT từ sau VAT")
            if net is None:
                row["errors"].append("Thiếu số tiền")
            vat = vat if vat is not None else Decimal(10)
            if net is not None and gross is not None and gross < net:
                row["errors"].append("Số tiền sau VAT nhỏ hơn trước VAT")
            fy = to_int(parse_number(get(r, "fiscalYear"))) if get(r, "fiscalYear") not in (None, "") else None
            cc_raw = get(r, "costCenter")
            cc = refs.cost_center(cc_raw) if cc_raw not in (None, "") else None
            if cc_raw not in (None, "") and cc is None:
                row["warnings"].append(f"Cost center “{_text(cc_raw, 30)}” không có — bỏ trống")
            status_raw = get(r, "status")
            status = _match(status_raw, OPEX_STATUSES) if status_raw not in (None, "") else None
            if status_raw not in (None, "") and status is None:
                row["errors"].append(f"Trạng thái “{_text(status_raw, 20)}” không rõ (Dự kiến / Đang hiệu lực / Đã gia hạn / Ngừng)")
            vendor = refs.vendor(vname) if vname else None
            if vname and vendor is None:
                row["warnings"].append(f"Nhà cung cấp mới “{vname}” sẽ được tạo")
            email = _text(get(r, "ownerEmail"), 255)
            if email and not service.EMAIL_RE.match(email):
                row["warnings"].append("Email phụ trách không hợp lệ — bỏ trống")
                email = ""
            row.update({
                "code": _text(get(r, "code"), 20), "categoryId": cat.id if cat else None, "categoryName": cat.name if cat else None,
                "title": t, "vendorName": vname, "vendorId": vendor.id if vendor else None,
                "contractRef": _text(get(r, "contractRef"), 64) or None, "poRef": _text(get(r, "poRef"), 64) or None,
                "startDate": start, "endDate": end, "billingCycle": cycle, "amountNet": net, "vatPct": vat,
                "amountGross": gross, "fiscalYear": fy, "costCenterId": cc.id if cc else None,
                "ownerName": _text(get(r, "owner"), 128) or (user.display_name or user.username), "ownerEmail": email or None,
                "statusIn": status, "stopReason": _text(get(r, "stopReason"), 500) or None,
                "serials": [s.strip()[:128] for s in str(get(r, "serials") or "").replace(";", ",").replace("\n", ",").split(",") if s.strip()][:50],
                "description": _text(get(r, "description"), 5000) or None,
            })
        except ValueError as exc:
            row["errors"].append(str(exc))
        if row["errors"]:
            row["action"] = "error"
        out.append(row)

    # Duplicates: an existing Opex (same code, or same title + vendor + start), or twice in the file.
    ok = [r for r in out if r["action"] == "create"]
    existing_codes = {c for (c,) in db.query(OpexItem.code)}
    existing = {(norm_text(o.title), o.vendor_id, o.start_date): o.code
                for o in db.query(OpexItem).filter(OpexItem.start_date.in_({r["startDate"] for r in ok} or {today}))}
    seen = {}
    for r in ok:
        key = (norm_text(r["title"]), norm_text(r["vendorName"]), r["startDate"])
        if r["code"] and r["code"] in existing_codes:
            r["action"], r["skipReason"] = "skip", f"Đã có {r['code']}"
        elif r["vendorId"] and (norm_text(r["title"]), r["vendorId"], r["startDate"]) in existing:
            r["action"], r["skipReason"] = "skip", f"Trùng {existing[(norm_text(r['title']), r['vendorId'], r['startDate'])]}"
        elif key in seen:
            r["action"], r["skipReason"] = "skip", f"Trùng dòng {seen[key]} trong file"
        else:
            seen[key] = r["row"]

    # Renewal chains inside the file; status of each row.
    chains = defaultdict(list)
    for r in out:
        if r["action"] == "create":
            chains[(norm_text(r["title"]), norm_text(r["vendorName"]))].append(r)
    for group in chains.values():
        group.sort(key=lambda r: r["startDate"])
        for prev, nxt in zip(group, group[1:]):
            gap = (nxt["startDate"] - prev["endDate"]).days
            if 0 < gap <= CHAIN_GAP_DAYS + 1:
                prev["successorRow"], nxt["predecessorRow"] = nxt["row"], prev["row"]
    for r in out:
        if r["action"] != "create":
            continue
        st = r["statusIn"]
        if st is None:
            if r.get("successorRow"):
                st = "renewed"
            elif r["startDate"] > today:
                st = "planned"
            else:
                st = "active"
                if r["endDate"] < today:
                    r["warnings"].append("Đã hết hạn, không có kỳ tiếp theo trong file — nhập là Đang hiệu lực (hiện Quá hạn để quyết định)")
        if st == "stopped" and not r["stopReason"]:
            r["stopReason"] = "Nhập từ file: đã ngừng"
        if st == "renewed" and not r.get("successorRow"):
            r["warnings"].append("Đánh dấu Đã gia hạn nhưng không thấy kỳ tiếp theo trong file")
        r["status"] = st
    return {"sheet": title, "headerRow": h + 1, "columns": {f: OPEX_COLUMNS[f][0] for f in cols}, "rows": out}


def _opex_commit(db: Session, rows: List[Dict[str, Any]], cfg: SpendSettings, user: User, request: Optional[Request]) -> List[str]:
    created, by_row = [], {}
    for r in sorted(rows, key=lambda r: r["startDate"]):
        payload = {"categoryId": r["categoryId"], "title": r["title"], "description": r["description"],
                   "vendorName": r["vendorName"], "vendorId": r["vendorId"], "contractRef": r["contractRef"], "poRef": r["poRef"],
                   "startDate": r["startDate"].isoformat(), "endDate": r["endDate"].isoformat(), "billingCycle": r["billingCycle"],
                   "amountNet": r["amountNet"], "vatPct": r["vatPct"], "amountGross": r["amountGross"], "fiscalYear": r["fiscalYear"],
                   "costCenterId": r["costCenterId"], "ownerName": r["ownerName"], "ownerEmail": r["ownerEmail"],
                   "status": "planned" if r["status"] == "planned" else "active",
                   "assets": [{"serial": s} for s in r["serials"]]}
        o = service.create(db, payload, cfg, user, request, origin="manual")
        if r["status"] in ("renewed", "stopped"):
            o.status = r["status"]
            o.stop_reason = r["stopReason"] if r["status"] == "stopped" else None
        pred = by_row.get(r.get("predecessorRow"))
        if pred is not None:
            o.renewed_from_id = pred.id
            if r["status"] != "stopped":
                o.origin = "renewal"
        by_row[r["row"]] = o
        created.append(o.code)
        db.flush()
    return created


# ---------------------------------------------------------------- Capex
def _capex_rows(db: Session, data: bytes, cfg: SpendSettings, user: User, today: dt.date) -> Dict[str, Any]:
    title, h, cols, rows = _sheet(data, "capex")
    refs = _Refs(db)
    get = lambda r, f: (r[cols[f]] if f in cols and cols[f] < len(r) else None)  # noqa: E731
    items: List[Dict[str, Any]] = []
    last_project, last_fy = None, None
    for i, r in enumerate(rows[h + 1:], start=h + 2):
        if not any(c not in (None, "") for c in r):
            continue
        if len(items) >= MAX_ROWS:
            raise ImportError_(f"Tối đa {MAX_ROWS} dòng mỗi lần nhập.")
        row = {"row": i, "errors": [], "warnings": []}
        try:
            own = _text(get(r, "project"), 255)
            pname = own or last_project                               # merged cells: blank = same project as above
            if not pname:
                row["errors"].append("Thiếu tên dự án")
            last_project = pname
            fy_cell = get(r, "fiscalYear")
            fy = to_int(parse_number(fy_cell)) if fy_cell not in (None, "") else (None if own else last_fy)
            last_fy = fy
            cat = refs.category(get(r, "category"))
            if not cat:
                row["errors"].append(f"Loại chi phí “{_text(get(r, 'category'), 40)}” không có trong danh mục")
            desc = _text(get(r, "item"), 255)
            if not desc:
                row["errors"].append("Thiếu hạng mục")
            vname = _text(get(r, "vendor"), 255)
            net = _money(get(r, "amountNet"), "Số tiền trước VAT")
            vat = _pct(get(r, "vatPct"), "VAT")
            gross = _money(get(r, "amountGross"), "Số tiền sau VAT")
            if net is None and gross is not None:
                net = to_int(Decimal(gross) * 100 / (100 + (vat if vat is not None else Decimal(10))))
            if net is None:
                row["errors"].append("Thiếu số tiền")
            qty = to_int(parse_number(get(r, "quantity"))) if get(r, "quantity") not in (None, "") else 1
            years = to_int(parse_number(get(r, "coverageYears"))) if get(r, "coverageYears") not in (None, "") else 0
            if not 0 <= (years or 0) <= 10:
                row["errors"].append("Số năm bảo hành / license từ 0 đến 10")
            if years and not vname:
                row["errors"].append("Có thời hạn bảo hành / license nên cần nhà cung cấp")
            row.update({
                "project": pname, "fiscalYear": fy,
                "goLive": _date(get(r, "goLive")), "projectStatusRaw": get(r, "projectStatus"),
                "owner": _text(get(r, "owner"), 128) or None, "costCenterRaw": get(r, "costCenter"),
                "categoryId": cat.id if cat else None, "categoryName": cat.name if cat else None, "description": desc,
                "vendorName": vname or None, "quantity": qty or 1, "amountNet": net, "vatPct": vat if vat is not None else Decimal(10),
                "amountGross": gross, "startDate": _date(get(r, "startDate")), "coverageYears": years or 0,
                "renewalPct": _pct(get(r, "renewalPct"), "% gia hạn"), "renewalAmount": _money(get(r, "renewalAmount"), "Số tiền gia hạn"),
            })
            if vname and refs.vendor(vname) is None:
                row["warnings"].append(f"Nhà cung cấp mới “{vname}” sẽ được tạo")
        except ValueError as exc:
            row["errors"].append(str(exc))
        items.append(row)

    # Group into projects (name + fiscal year); project fields come from the first row that has them.
    projects: Dict[Tuple[str, Any], Dict[str, Any]] = {}
    for r in items:
        if not r.get("project"):
            continue
        key = (norm_text(r["project"]), r.get("fiscalYear"))
        p = projects.setdefault(key, {"name": r["project"], "fiscalYear": r.get("fiscalYear"), "goLive": None, "status": None,
                                      "owner": None, "costCenterId": None, "rows": [], "errors": [], "warnings": []})
        p["rows"].append(r)
        for f, src in (("goLive", "goLive"), ("owner", "owner")):
            if p[f] is None and r.get(src):
                p[f] = r[src]
        if p["status"] is None and r.get("projectStatusRaw") not in (None, ""):
            p["status"] = _match(r["projectStatusRaw"], CAPEX_STATUSES)
            if p["status"] is None:
                p["errors"].append(f"Trạng thái dự án “{_text(r['projectStatusRaw'], 20)}” không rõ (Nháp / Đã duyệt / Đang triển khai / Đã đóng)")
        if p["costCenterId"] is None and r.get("costCenterRaw") not in (None, ""):
            cc = refs.cost_center(r["costCenterRaw"])
            if cc:
                p["costCenterId"] = cc.id
            else:
                p["warnings"].append(f"Cost center “{_text(r['costCenterRaw'], 30)}” không có — bỏ trống")
    existing = {(norm_text(n), fy) for n, fy in db.query(CapexProject.name, CapexProject.fiscal_year)}
    out = []
    for p in projects.values():
        p["status"] = p["status"] or "draft"
        fy = p["fiscalYear"] or (fiscal.fiscal_year(p["goLive"], cfg.fiscal_start_month) if p["goLive"] else None)
        if fy is None:
            p["errors"].append("Thiếu năm tài chính hoặc ngày go-live")
        p["fiscalYear"] = fy
        if len(p["rows"]) > 200:
            p["errors"].append("Tối đa 200 hạng mục cho một dự án")
        bad_rows = [r for r in p["rows"] if r["errors"]]
        if bad_rows:
            p["errors"].append(f"{len(bad_rows)} hạng mục lỗi")
        if fy and (norm_text(p["name"]), fy) in existing:
            p["action"], p["skipReason"] = "skip", f"Đã có dự án cùng tên trong FY {fy}"
        else:
            p["action"] = "error" if p["errors"] else "create"
        if p["status"] != "draft" and any(r.get("coverageYears") for r in p["rows"]):
            p["warnings"].append("Dự án không ở trạng thái Nháp: hạng mục có bảo hành / license sẽ sinh Opex gia hạn dự kiến"
                                 " — kiểm tra trùng với Opex đã có")
        p["amountNet"] = sum(r.get("amountNet") or 0 for r in p["rows"])
        out.append(p)
    orphans = [r for r in items if not r.get("project")]
    return {"sheet": title, "headerRow": h + 1, "columns": {f: CAPEX_COLUMNS[f][0] for f in cols}, "projects": out,
            "orphans": orphans}


def _capex_commit(db: Session, projects: List[Dict[str, Any]], cfg: SpendSettings, user: User,
                  request: Optional[Request]) -> List[str]:
    from ..routers.spend_capex import _apply_items     # same validation as the Capex screen
    from . import capex, codes
    created = []
    for p in projects:
        proj = CapexProject(status=p["status"], created_by=user.username, row_version=1, name=p["name"][:255],
                            fiscal_year=p["fiscalYear"], go_live_date=p["goLive"], owner_name=p["owner"],
                            cost_center_id=p["costCenterId"])
        proj.code = codes.next_code(db, "capex", proj.fiscal_year)
        db.add(proj)
        db.flush()
        _apply_items(db, proj, [{
            "categoryId": r["categoryId"], "description": r["description"], "vendorName": r["vendorName"],
            "quantity": r["quantity"], "amountNet": r["amountNet"], "vatPct": r["vatPct"], "amountGross": r["amountGross"],
            "startDate": r["startDate"].isoformat() if r["startDate"] else None, "coverageYears": r["coverageYears"],
            "renewalPct": r["renewalPct"], "renewalAmount": r["renewalAmount"]} for r in p["rows"]], user, request)
        db.flush()
        if proj.status in capex.GENERATING_STATUSES:
            capex.reconcile(db, proj, cfg, user, request)
        service.audit(db, user, request, "spend.capex.create", code=proj.code, name=proj.name, source="import")
        created.append(proj.code)
    return created


# ---------------------------------------------------------------- entry points
def preview(db: Session, kind: str, data: bytes, cfg: SpendSettings, user: User, today: Optional[dt.date] = None) -> Dict[str, Any]:
    today = today or fiscal.today_vn()
    if kind == "opex":
        r = _opex_rows(db, data, cfg, user, today)
        rows = r["rows"]
        r["summary"] = {"total": len(rows), "create": sum(1 for x in rows if x["action"] == "create"),
                        "skip": sum(1 for x in rows if x["action"] == "skip"), "error": sum(1 for x in rows if x["action"] == "error"),
                        "renewedLinks": sum(1 for x in rows if x["action"] == "create" and x.get("predecessorRow")),
                        "amountGross": sum(service.gross_of(x["amountNet"], x["vatPct"]) if x.get("amountGross") is None else x["amountGross"]
                                           for x in rows if x["action"] == "create")}
        return r
    if kind == "capex":
        r = _capex_rows(db, data, cfg, user, today)
        ps = r["projects"]
        r["summary"] = {"total": len(ps), "create": sum(1 for p in ps if p["action"] == "create"),
                        "skip": sum(1 for p in ps if p["action"] == "skip"), "error": sum(1 for p in ps if p["action"] == "error"),
                        "items": sum(len(p["rows"]) for p in ps if p["action"] == "create"), "orphans": len(r["orphans"])}
        return r
    raise ImportError_("Loại nhập phải là opex hoặc capex.")


IMPORT_LOCK_KEYS = {"opex": "IMP-OPX", "capex": "IMP-CPX"}


def _claim_import_lock(db: Session, kind: str) -> None:
    """Serialize commits of the same kind on a lock row in spend_sequences (year 0 is never a
    code year). The UPDATE takes a row lock on PostgreSQL (SQLite locks the whole database), so
    a second concurrent "Ghi" waits here until the first commits, and its duplicate check below
    then sees the rows the first one wrote. First use creates the row; if two first uses race,
    the loser's INSERT hits the primary key and the router returns 409 with nothing written."""
    key = IMPORT_LOCK_KEYS.get(kind)
    if key is None:
        return          # preview() rejects unknown kinds
    res = db.execute(update(SpendSequence).where(SpendSequence.kind == key, SpendSequence.year == 0)
                     .values(last_no=SpendSequence.last_no + 1))
    if res.rowcount == 0:
        db.add(SpendSequence(kind=key, year=0, last_no=1))
        db.flush()


def commit(db: Session, kind: str, data: bytes, cfg: SpendSettings, user: User, request: Optional[Request],
           skip_errors: bool) -> Dict[str, Any]:
    _claim_import_lock(db, kind)      # before the duplicate check, so it runs on committed data
    pv = preview(db, kind, data, cfg, user)
    if pv["summary"]["error"] and not skip_errors:
        raise HTTPException(400, f"Còn {pv['summary']['error']} dòng lỗi — sửa file hoặc chọn “Chỉ nhập dòng hợp lệ”.")
    if kind == "opex":
        good = [r for r in pv["rows"] if r["action"] == "create"]
        # A row whose predecessor is not imported keeps no link.
        rows_ok = {r["row"] for r in good}
        for r in good:
            if r.get("predecessorRow") not in rows_ok:
                r.pop("predecessorRow", None)
        created = _opex_commit(db, good, cfg, user, request)
    else:
        created = _capex_commit(db, [p for p in pv["projects"] if p["action"] == "create"], cfg, user, request)
    service.audit(db, user, request, f"spend.import.{kind}", created=len(created), codes=created[:200],
                  skipped=pv["summary"]["skip"], errors=pv["summary"]["error"])
    return {"created": created, "summary": pv["summary"]}


def public_preview(pv: Dict[str, Any], kind: str) -> Dict[str, Any]:
    """JSON-safe view (dates as ISO, Decimals as float) — bounded to what the screen shows."""
    def clean(v):
        if isinstance(v, (dt.date, dt.datetime)):
            return v.isoformat()
        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items() if k not in ("projectStatusRaw", "costCenterRaw")}
        if isinstance(v, list):
            return [clean(x) for x in v]
        return v
    return clean(pv)
