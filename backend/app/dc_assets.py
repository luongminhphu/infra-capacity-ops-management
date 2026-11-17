"""Data Center devices (Tài sản → Thiết bị Data Center): import from the Excel asset list, age against the
Green policy (≥ 5 years in use) and correlation by serial number with the Opex items that cover the device
(HW warranty, license / firmware renewal — spend `opex_assets.serial`).

Excel layout (one sheet per device group, e.g. DC-Server / DC-Storage / DC-Network): a header row with
SN plus some of No. / BUSINESS FUNCTION / QTY / Type / Env / ID / BRAND / MODEL / ASSET TAG / ACQUISITION DATE /
LOCATION; merged "business function" cells (empty below the first row) carry down; a "REMOVED" business
function marks the device and the rows under it as removed. Uptime columns are recomputed from the
acquisition date, never read.
"""
import datetime as dt
import re
from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from .models import DcAsset
from .shared.text import norm_text
from .shared.xlsx_io import XlsxError, read_rows

CATEGORIES = ("server", "storage", "network", "other")
CATEGORY_LABELS = {"server": "Máy chủ", "storage": "Lưu trữ", "network": "Mạng", "other": "Khác"}
LEVEL_TEXT = {"crit": "VƯỢT NGƯỠNG", "bad": "CẢNH BÁO", "warn": "THEO DÕI", "ok": "AN TOÀN"}
LEVEL_RANK = {"crit": 0, "bad": 1, "warn": 2, "ok": 3}
MAX_SHEETS = 20
MAX_ROWS = 5000                 # per sheet
MAX_TOTAL = 10000
HEADER_SCAN = 15

# Normalised header text (shared.text.norm_text) → field. Exact match only: a header like "UPTIME (YEAR)"
# or the "Green Policy" summary block beside the table must not be taken for a field.
ALIASES: Dict[str, Tuple[str, ...]] = {
    "business": ("business function", "business", "chuc nang", "nhom chuc nang", "he thong", "nghiep vu"),
    "qty": ("qty", "so luong", "quantity"),
    "bizType": ("type", "loai", "phan loai"),
    "env": ("env", "environment", "moi truong"),
    "deviceId": ("id", "hostname", "host name", "device", "device name", "ten thiet bi", "ten may", "name"),
    "brand": ("brand", "hang", "hang san xuat", "vendor", "manufacturer"),
    "model": ("model", "model name", "dong may"),
    "serial": ("sn", "s n", "serial", "serial number", "serial no", "so serial", "service tag"),
    "assetTag": ("asset tag", "ma tai san", "asset code", "asset", "tag"),
    "acquired": ("acquisition date", "purchase date", "ngay mua", "ngay dua vao su dung", "install date", "ngay lap dat", "ngay nhap"),
    "location": ("location", "vi tri", "dia diem", "site"),
    "notes": ("note", "notes", "ghi chu", "remark", "remarks"),
    "category": ("category", "nhom thiet bi", "loai thiet bi"),
}
_FIELD_OF = {a: f for f, aliases in ALIASES.items() for a in aliases}
_REMOVED = {"removed", "da thanh ly", "thanh ly", "decommissioned", "retired", "da go"}
_CARRY = ("business", "bizType", "env")          # merged cells: empty below the first row of the group
_LIMITS = {"business": 128, "bizType": 32, "env": 16, "deviceId": 255, "brand": 64, "model": 128, "serial": 128,
           "assetTag": 64, "location": 128, "notes": 500}


class ImportError_(ValueError):
    pass


# ---------------------------------------------------------------- normalisation
def serial_key(s: Any) -> str:
    """Identity of a serial number: upper case, letters and digits only ("71FHKZ3 " = "71fhkz3")."""
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())[:128]


def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)                                    # numeric serials (NetApp 952248001227) read as numbers
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%d/%m/%Y")
    return re.sub(r"\s+", " ", str(v)).strip()


def _date(v: Any) -> Tuple[Optional[dt.date], bool]:
    """(date, ok). Excel dates, serial day numbers, dd/mm/yyyy, yyyy-mm-dd. Empty → (None, True)."""
    if v is None or v == "":
        return None, True
    if isinstance(v, dt.datetime):
        return v.date(), True
    if isinstance(v, dt.date):
        return v, True
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 20000 <= v <= 80000:                       # 1954 … 2119 as Excel day numbers
            return dt.date(1899, 12, 30) + dt.timedelta(days=int(v)), True
        return None, False
    s = str(v).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%m/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date(), True
        except ValueError:
            continue
    return None, False


def _qty(v: Any) -> int:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return 1
    return min(max(n, 1), 100000)


def category_of(sheet: str, value: str = "") -> str:
    for text in (value, sheet):
        n = norm_text(text)
        if not n:
            continue
        if any(w in n for w in ("server", "may chu", "compute", "host")):
            return "server"
        if any(w in n for w in ("storage", "luu tru", "san", "backup", "tape")):
            return "storage"
        if any(w in n for w in ("network", "mang", "switch", "router", "firewall", "wifi")):
            return "network"
    return "other"


def asset_key(category: str, serial: str, tag: str, device: str) -> str:
    if tag:
        return ("tag:" + re.sub(r"\s+", "", tag.upper()))[:255]
    return f"sn:{category}:{serial_key(serial)}:{norm_text(device)}"[:255]


# ---------------------------------------------------------------- Excel
def _header(rows: List[List[Any]]) -> Tuple[int, Dict[str, int]]:
    """(row index, field → column) of the first row within HEADER_SCAN that names SN and two more fields."""
    for i, row in enumerate(rows[:HEADER_SCAN]):
        cols: Dict[str, int] = {}
        gap = False
        for j, cell in enumerate(row):
            n = norm_text(_text(cell))
            if not n:
                gap = gap or len(cols) >= 3           # a blank header after the table: summary block follows
                continue
            if gap:
                break
            f = _FIELD_OF.get(n)
            if f and f not in cols:
                cols[f] = j
        if "serial" in cols and len(cols) >= 3:
            return i, cols
    return -1, {}


def parse(data: bytes) -> Dict[str, Any]:
    """Rows of every sheet that has an asset table. Raises ImportError_ for an unreadable / empty file."""
    try:
        sheets = read_rows(data, max_sheets=MAX_SHEETS, max_rows=MAX_ROWS, max_cols=40)
    except XlsxError as exc:
        raise ImportError_(str(exc))
    out_rows: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    used: List[Dict[str, Any]] = []
    for title, rows in sheets:
        h, cols = _header(rows)
        if h < 0:
            continue
        carry = {k: "" for k in _CARRY}
        n = 0
        for i in range(h + 1, len(rows)):
            row = rows[i]
            get = lambda f: row[cols[f]] if f in cols and cols[f] < len(row) else None  # noqa: E731
            vals = {f: _text(get(f)) for f in cols if f not in ("acquired", "qty")}
            if not any(vals.get(f) for f in ("serial", "assetTag", "deviceId", "model")):
                continue                              # blank / total line
            for k in _CARRY:
                if vals.get(k):
                    carry[k] = vals[k]
                else:
                    vals[k] = carry[k]
            removed = norm_text(vals.get("business", "")) in _REMOVED
            acquired, ok = _date(get("acquired"))
            rec = {"sheet": title[:64], "row": i + 1, "removed": removed, "acquiredOn": acquired, "qty": _qty(get("qty")) if "qty" in cols else 1}
            for f, lim in _LIMITS.items():
                rec[f] = (vals.get(f) or "")[:lim]
            rec["category"] = category_of(title, vals.get("category", ""))
            rec["serialKey"] = serial_key(rec["serial"])
            problems = []
            if not ok:
                problems.append(f"Ngày “{_text(get('acquired'))[:40]}” không đọc được (dd/mm/yyyy)")
            if not rec["serialKey"] and not rec["assetTag"]:
                problems.append("Thiếu SN và asset tag")
            rec["key"] = asset_key(rec["category"], rec["serial"], rec["assetTag"], rec["deviceId"])
            rec["errors"] = problems
            out_rows.append(rec)
            n += 1
            if len(out_rows) >= MAX_TOTAL:
                break
        used.append({"name": title, "category": category_of(title), "rows": n})
        if len(out_rows) >= MAX_TOTAL:
            break
    if not used:
        raise ImportError_("Không tìm thấy bảng thiết bị: cần một dòng tiêu đề có cột SN (Serial) và các cột như ID, BRAND, MODEL, ASSET TAG.")
    # An asset tag typed on two devices (copy-paste in the list): those rows fall back to SN + ID as identity
    # so neither device is lost, and the tag is reported. Same identity still twice: the later row is refused.
    # Same serial under different tags: warning.
    tag_rows: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in out_rows:
        if r["assetTag"] and not r["errors"]:
            tag_rows[r["key"]].append(r)
    dup_tags = []
    for rs in tag_rows.values():
        if len(rs) > 1 and len({x["serialKey"] for x in rs}) == len(rs) and all(x["serialKey"] for x in rs):
            for x in rs:
                x["key"] = asset_key(x["category"], x["serial"], "", x["deviceId"])
            dup_tags.append({"assetTag": rs[0]["assetTag"], "rows": [f"{x['sheet']} dòng {x['row']} ({x['deviceId'] or x['serial']})" for x in rs]})
    first: Dict[str, Dict[str, Any]] = {}
    by_serial: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in out_rows:
        if r["errors"] and "Thiếu SN và asset tag" in r["errors"]:
            continue
        if r["key"] in first:
            f = first[r["key"]]
            r["errors"].append(f"Trùng {'asset tag' if r['assetTag'] else 'SN + ID'} với {f['sheet']} dòng {f['row']}")
            continue
        first[r["key"]] = r
        if r["serialKey"]:
            by_serial[r["serialKey"]].append(r)
    for r in out_rows:
        if r["errors"]:
            errors.append({"sheet": r["sheet"], "row": r["row"], "deviceId": r["deviceId"], "serial": r["serial"], "errors": r["errors"]})
    warnings = [{"serial": rs[0]["serial"], "rows": [f"{x['sheet']} dòng {x['row']} ({x['deviceId'] or x['assetTag']})" for x in rs]}
                for rs in by_serial.values() if len(rs) > 1]
    return {"rows": [r for r in out_rows if not r["errors"]], "errors": errors, "duplicateSerials": warnings,
            "duplicateTags": dup_tags, "sheets": used}


# ---------------------------------------------------------------- write
_COLS = {"category": "category", "business": "business_function", "bizType": "biz_type", "env": "env", "deviceId": "device_id",
         "brand": "brand", "model": "model", "serial": "serial", "serialKey": "serial_key", "assetTag": "asset_tag",
         "acquiredOn": "acquired_on", "location": "location", "qty": "qty", "removed": "removed", "notes": "notes", "sheet": "sheet"}


def _values(r: Dict[str, Any]) -> Dict[str, Any]:
    return {col: (r[f] if r[f] != "" else None) if isinstance(r[f], str) else r[f] for f, col in _COLS.items()}


def plan(db: Session, parsed: Dict[str, Any], mark_missing: bool) -> Dict[str, Any]:
    existing = {a.asset_key: a for a in db.query(DcAsset)}
    new, upd, same = [], [], []
    for r in parsed["rows"]:
        a = existing.get(r["key"])
        vals = _values(r)
        if a is None:
            new.append(r)
        elif any(getattr(a, c) != v for c, v in vals.items()):
            upd.append(r)
        else:
            same.append(r)
    keys = {r["key"] for r in parsed["rows"]}
    cats = {s["category"] for s in parsed["sheets"]} | {r["category"] for r in parsed["rows"]}
    missing = [a for k, a in existing.items() if k not in keys and not a.removed and a.category in cats and a.sheet]
    return {"new": new, "update": upd, "same": same, "missing": missing, "markMissing": mark_missing, "existing": existing}


def public_plan(parsed: Dict[str, Any], p: Dict[str, Any]) -> Dict[str, Any]:
    action = {id(r): "new" for r in p["new"]} | {id(r): "update" for r in p["update"]}
    sample = [{"sheet": r["sheet"], "row": r["row"], "category": r["category"], "deviceId": r["deviceId"], "brand": r["brand"],
               "model": r["model"], "serial": r["serial"], "assetTag": r["assetTag"],
               "acquiredOn": r["acquiredOn"].isoformat() if r["acquiredOn"] else None, "removed": r["removed"],
               "action": action.get(id(r), "same")} for r in parsed["rows"]][:300]
    return {"sheets": parsed["sheets"], "counts": {"rows": len(parsed["rows"]), "new": len(p["new"]), "update": len(p["update"]),
            "same": len(p["same"]), "errors": len(parsed["errors"]), "missing": len(p["missing"])},
            "rows": sample, "errors": parsed["errors"][:200], "duplicateSerials": parsed["duplicateSerials"][:100],
            "duplicateTags": parsed["duplicateTags"][:100],
            "missing": [{"deviceId": a.device_id, "serial": a.serial, "assetTag": a.asset_tag} for a in p["missing"][:100]]}


def apply(db: Session, p: Dict[str, Any], username: str) -> Dict[str, int]:
    now = dt.datetime.utcnow()
    for r in p["new"]:
        db.add(DcAsset(asset_key=r["key"], imported_at=now, updated_by=username, **_values(r)))
    for r in p["update"]:
        a = p["existing"][r["key"]]
        for c, v in _values(r).items():
            setattr(a, c, v)
        a.imported_at, a.updated_by = now, username
    removed = 0
    if p["markMissing"]:
        for a in p["missing"]:
            a.removed, a.imported_at, a.updated_by = True, now, username
            removed += 1
    return {"new": len(p["new"]), "update": len(p["update"]), "same": len(p["same"]), "removed": removed}


# ---------------------------------------------------------------- age + coverage
def months_between(start: dt.date, today: dt.date) -> int:
    m = (today.year - start.year) * 12 + today.month - start.month
    if today.day < start.day:
        m -= 1
    return max(m, 0)


def age_text(months: Optional[int]) -> str:
    if months is None:
        return "—"
    y, m = divmod(months, 12)
    return f"{y} năm {m} tháng" if y and m else (f"{y} năm" if y else f"{m} tháng")


def age_bucket(months: Optional[int], limit: int) -> str:
    if months is None:
        return "unknown"
    if months <= 36:
        return "le3"
    return "ge5" if months >= limit else "3to5"


def _add_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    last = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return dt.date(y, m, min(d.day, last))


def opex_links(db: Session, keys: Iterable[str]) -> Dict[str, List[Dict[str, Any]]]:
    """serial key → Opex items whose device list has that serial (any spelling: spaces, case)."""
    from .spend.models import OpexAsset, OpexItem, SpendCategory, SpendVendor
    wanted = set(k for k in keys if k)
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    if not wanted:
        return out
    q = (db.query(OpexAsset.serial, OpexItem, SpendCategory.grp, SpendCategory.name, SpendVendor.name)
         .join(OpexItem, OpexItem.id == OpexAsset.opex_id)
         .join(SpendCategory, SpendCategory.id == OpexItem.category_id)
         .join(SpendVendor, SpendVendor.id == OpexItem.vendor_id))
    for serial, o, grp, cat_name, vendor in q:
        k = serial_key(serial)
        if k in wanted and not any(x["id"] == o.id for x in out[k]):
            out[k].append({"id": o.id, "code": o.code, "title": o.title, "grp": grp, "category": cat_name, "vendor": vendor,
                           "start": o.start_date, "end": o.end_date, "status": o.status, "contractRef": o.contract_ref,
                           "fiscalYear": o.fiscal_year})
    return out


def coverage(items: List[Dict[str, Any]], today: dt.date) -> Dict[str, Any]:
    """State of a group of Opex items for one device: active (covers today) / planned / expired / none."""
    live = [o for o in items if o["status"] != "stopped"]
    act = [o for o in live if o["start"] <= today <= o["end"]]
    if act:
        best = max(act, key=lambda o: o["end"])
        return {"state": "active", "end": best["end"], "item": best}
    fut = [o for o in live if o["start"] > today]
    if fut:
        best = min(fut, key=lambda o: o["start"])
        return {"state": "planned", "end": best["end"], "item": best}
    if items:
        best = max(items, key=lambda o: o["end"])
        return {"state": "expired", "end": best["end"], "item": best}
    return {"state": "none", "end": None, "item": None}


def evaluate(a: DcAsset, links: List[Dict[str, Any]], today: dt.date, limit: int, warn: int) -> Dict[str, Any]:
    months = months_between(a.acquired_on, today) if a.acquired_on else None
    hw = coverage([o for o in links if o["grp"] == "hw_warranty"], today)
    lic = coverage([o for o in links if o["grp"] == "license"], today)
    level, reasons = "ok", []

    def worse(lv: str) -> None:
        nonlocal level
        if LEVEL_RANK[lv] < LEVEL_RANK[level]:
            level = lv

    if a.removed:
        return {"months": months, "level": "ok", "reasons": [], "hw": hw, "lic": lic}
    if months is None:
        reasons.append("Thiếu ngày đưa vào sử dụng")
    elif months >= limit:
        kept = extended_until(a, today)
        # Kept in use on purpose (over budget, still good …) and still under HW warranty: THEO DÕI until review.
        worse("warn" if kept is not None and hw["state"] == "active" else "bad")
        reasons.append(f"Đã dùng {age_text(months)} (≥ {age_text(limit)})")
        if kept is not None:
            reasons.append("Dùng tiếp" + (f" đến {kept.strftime('%m/%Y')}" if kept != dt.date.max else "")
                           + (f" — {DECISION_REASONS[a.decision_reason].lower()}" if a.decision_reason in DECISION_REASONS else ""))
        elif a.decision == "replace":
            reasons.append("Đã quyết định thay thế")
        if hw["state"] != "active":
            worse("crit")
            reasons.append("Không còn bảo hành phần cứng" if hw["state"] == "expired" else
                           "Chưa ghép hợp đồng bảo hành phần cứng" if hw["state"] == "none" else "Bảo hành mới chưa bắt đầu")
    elif months >= warn:
        worse("warn")
        reasons.append(f"Đủ {age_text(limit)} vào {_add_months(a.acquired_on, limit).strftime('%m/%Y')}")
    if hw["state"] == "expired" and months is not None and months < limit:
        worse("warn")
        reasons.append(f"Hết bảo hành phần cứng từ {hw['end'].strftime('%d/%m/%Y')}")
    if lic["state"] == "expired":
        worse("warn")
        reasons.append(f"Hết hạn license / firmware từ {lic['end'].strftime('%d/%m/%Y')}")
    return {"months": months, "level": level, "reasons": reasons, "hw": hw, "lic": lic}


def _host_names(db: Session) -> Dict[str, str]:
    """short host name (lower) → host name as collected, for a link to the host card."""
    try:
        from .services.entities import _latest
        hosts, _ = _latest(db)
    except Exception:  # noqa: BLE001 — inventory unavailable: no links, the list still works
        return {}
    return {str(h.get("host") or "").split(".")[0].lower(): h.get("host") for h in hosts if h.get("host")}


def _cov_public(c: Dict[str, Any], detail: bool) -> Dict[str, Any]:
    it = c["item"]
    out = {"state": c["state"], "endDate": c["end"].isoformat() if c["end"] else None}
    if detail and it:
        out.update(opexId=it["id"], code=it["code"], title=it["title"], vendor=it["vendor"], contractRef=it["contractRef"])
    return out


def listing(db: Session, today: dt.date, limit: int, warn: int, opex_detail: bool) -> Dict[str, Any]:
    assets = db.query(DcAsset).order_by(DcAsset.category, DcAsset.business_function, DcAsset.device_id).all()
    links = opex_links(db, (a.serial_key for a in assets))
    hosts = _host_names(db)
    items = []
    summary = {"total": 0, "removed": 0, "ge5": 0, "3to5": 0, "le3": 0, "unknown": 0, "crit": 0, "bad": 0, "warn": 0,
               "hwActive": 0, "hwNone": 0, "hwExpired": 0, "licActive": 0, "linked": 0}
    for a in assets:
        lk = links.get(a.serial_key or "", [])
        ev = evaluate(a, lk, today, limit, warn)
        bucket = age_bucket(ev["months"], limit)
        if a.removed:
            summary["removed"] += 1
        else:
            summary["total"] += a.qty or 1
            summary[bucket] += a.qty or 1
            if ev["level"] != "ok":
                summary[ev["level"]] += 1
            summary["hwActive" if ev["hw"]["state"] == "active" else "hwExpired" if ev["hw"]["state"] == "expired" else "hwNone"] += 1
            summary["licActive"] += ev["lic"]["state"] == "active"
            summary["linked"] += bool(lk)
        items.append({
            "id": a.id, "category": a.category, "categoryLabel": CATEGORY_LABELS[a.category], "business": a.business_function,
            "bizType": a.biz_type, "env": a.env, "deviceId": a.device_id, "brand": a.brand, "model": a.model, "serial": a.serial,
            "assetTag": a.asset_tag, "acquiredOn": a.acquired_on.isoformat() if a.acquired_on else None, "location": a.location,
            "qty": a.qty, "removed": a.removed, "notes": a.notes, "sheet": a.sheet, "manual": not a.sheet,
            "updatedBy": a.updated_by, "decision": decision_public(a),
            "ageMonths": ev["months"], "ageText": age_text(ev["months"]), "ageBucket": bucket,
            "limitDate": _add_months(a.acquired_on, limit).isoformat() if a.acquired_on else None,
            "level": ev["level"], "levelText": "Đã thanh lý" if a.removed else LEVEL_TEXT[ev["level"]], "reasons": ev["reasons"],
            "hw": _cov_public(ev["hw"], opex_detail), "license": _cov_public(ev["lic"], opex_detail),
            "opex": [{"id": o["id"], "code": o["code"], "title": o["title"], "grp": o["grp"], "category": o["category"],
                      "vendor": o["vendor"], "endDate": o["end"].isoformat(), "status": o["status"], "contractRef": o["contractRef"]}
                     for o in sorted(lk, key=lambda o: o["end"], reverse=True)] if opex_detail else [],
            "opexCount": len(lk),
            "host": hosts.get((a.device_id or "").split(".")[0].lower()) if a.category == "server" else None,
        })
    items.sort(key=lambda x: (x["removed"], LEVEL_RANK[x["level"]], -(x["ageMonths"] or -1)))
    return {"items": items, "summary": summary, "limitMonths": limit, "warnMonths": warn, "opexDetail": opex_detail,
            "today": today.isoformat()}


# ---------------------------------------------------------------- lifecycle decision (thay thế / dùng tiếp)
DECISIONS = {"replace": "Thay thế", "extend": "Dùng tiếp, gia hạn Opex"}
DECISION_REASONS = {"budget": "Vượt ngân sách", "good": "Thiết bị còn tốt", "support": "Hãng còn hỗ trợ",
                    "project": "Chờ dự án / chuyển đổi khác", "other": "Lý do khác"}


def extended_until(a: DcAsset, on: dt.date) -> Optional[dt.date]:
    """The review date when the device is kept in use (decision "extend") on day `on`, else None.
    No review date = kept until changed: date.max."""
    if a.decision != "extend":
        return None
    until = a.decision_until or dt.date.max
    return until if until >= on else None


def decision_public(a: DcAsset) -> Optional[Dict[str, Any]]:
    if not a.decision:
        return None
    return {"decision": a.decision, "label": DECISIONS[a.decision], "reason": a.decision_reason,
            "reasonLabel": DECISION_REASONS.get(a.decision_reason or "", ""), "note": a.decision_note,
            "until": a.decision_until.isoformat() if a.decision_until else None, "by": a.decision_by,
            "at": a.decision_at.isoformat() if a.decision_at else None}


def set_decision(db: Session, assets: List[DcAsset], d: Dict[str, Any], username: str) -> int:
    """Record the decision for the devices (None / "" clears it). "extend" needs a reason."""
    decision = d.get("decision") or None
    if decision not in (None, *DECISIONS):
        raise ImportError_("Quyết định không hợp lệ.")
    reason = d.get("reason") or None
    if decision == "extend" and reason not in DECISION_REASONS:
        raise ImportError_("Chọn lý do dùng tiếp (vượt ngân sách, còn tốt …).")
    until, ok = _date(d.get("until"))
    if not ok:
        raise ImportError_("Ngày xem lại không hợp lệ.")
    note = _text(d.get("note"))[:500] or None
    now = dt.datetime.utcnow()
    for a in assets:
        a.decision = decision
        a.decision_reason = reason if decision else None
        a.decision_note = note if decision else None
        a.decision_until = until if decision == "extend" else None
        a.decision_by, a.decision_at = (username, now) if decision else (None, None)
    return len(assets)


def group_of(db: Session, asset_id: int, year_end: dt.date, limit: Optional[int] = None) -> List[DcAsset]:
    """Devices of the plan line whose sourceId is `asset_id`: same category + business function, in use for
    `limit` months by `year_end`, not removed."""
    from .config import get_settings
    limit = limit or get_settings().asset_age_limit_months
    a = db.get(DcAsset, asset_id)
    if a is None:
        return []
    biz = (a.business_function or "").strip()
    return [x for x in db.query(DcAsset).filter(DcAsset.category == a.category, DcAsset.removed.is_(False),
                                               DcAsset.acquired_on.isnot(None))
            if (x.business_function or "").strip() == biz and _add_months(x.acquired_on, limit) <= year_end]


# ---------------------------------------------------------------- budget plan (Kế hoạch năm sau)
CATEGORY_CODES = {"server": "HW_SERVER", "storage": "HW_STORAGE", "network": "HW_NETWORK"}
_DETAIL_MAX = 1000


def _clip(s: str) -> str:
    return s if len(s) <= _DETAIL_MAX else s[:_DETAIL_MAX - 1] + "…"


def _models(assets: List[DcAsset]) -> Dict[str, int]:
    models: Dict[str, int] = defaultdict(int)
    for a in assets:
        models[f"{a.brand or ''} {a.model or ''}".strip() or "—"] += a.qty or 1
    return dict(sorted(models.items(), key=lambda x: (-x[1], x[0])))


def plan_suggestions(db: Session, year_start: dt.date, year_end: dt.date,
                     limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Suggestions of Kế hoạch năm sau from the Data Center devices in use for at least `limit` months by the end
    of the fiscal year, one line per (category, business function); sourceId = smallest device id of the group.

    * replace (Capex) — devices to replace. Priority must when one is already past the limit at the start of
      the year without an active HW warranty. Start = when the first device reaches the limit (or its review
      date when it was kept in use until inside the year).
    * extend (Opex) — devices kept in use the whole year ("Dùng tiếp": over budget, still good …) that have no
      HW warranty Opex to renew; those with one get the usual Gia hạn line from that Opex.
    No price is known: the amount is typed in the plan."""
    from .config import get_settings
    from .spend.fiscal import today_vn
    from .spend.models import SpendCategory
    limit = limit or get_settings().asset_age_limit_months
    rows = [a for a in db.query(DcAsset).filter(DcAsset.removed.is_(False), DcAsset.acquired_on.isnot(None))
            if _add_months(a.acquired_on, limit) <= year_end]
    if not rows:
        return []
    cats = [c for c in db.query(SpendCategory).filter(SpendCategory.active.is_(True)).order_by(SpendCategory.sort_order, SpendCategory.id)]
    if not cats:
        return []
    by_code = {c.code: c.id for c in cats}
    fallback = by_code.get("OTHER", cats[0].id)
    today = today_vn()
    links = opex_links(db, (a.serial_key for a in rows))
    hw_items = lambda a: [o for o in links.get(a.serial_key or "", []) if o["grp"] == "hw_warranty"]  # noqa: E731
    replace: Dict[Tuple[str, str], List[Tuple[DcAsset, dt.date]]] = defaultdict(list)
    extend: Dict[Tuple[str, str], List[DcAsset]] = defaultdict(list)
    for a in rows:
        key = (a.category, (a.business_function or "").strip())
        due = _add_months(a.acquired_on, limit)
        kept = extended_until(a, year_start)
        if kept is not None and kept >= year_end:
            if not any(o["status"] != "stopped" for o in hw_items(a)):
                extend[key].append(a)                # kept all year, nothing to renew: plan a new warranty Opex
            continue
        if kept is not None:                         # kept until a date inside the year: replace after it
            due = max(due, kept + dt.timedelta(days=1))
        replace[key].append((a, due))
    out = []
    for (cat, business), pairs in replace.items():
        pairs.sort(key=lambda p: (p[1], p[0].device_id or ""))
        assets = [a for a, _ in pairs]
        n = sum(a.qty or 1 for a in assets)
        must = any(d <= year_start and coverage(hw_items(a), today)["state"] != "active" for a, d in pairs)
        parts = []
        for a, d in pairs:
            when = f"đến hạn {d.strftime('%m/%Y')}" if d > year_start else age_text(months_between(a.acquired_on, year_start))
            parts.append(f"{a.device_id or a.asset_tag or a.serial} (SN {a.serial or '—'}, {when})")
        models = _models(assets)
        detail = ", ".join(f"{q} × {m}" for m, q in models.items()) + f". Dùng từ {age_text(limit)}: " + "; ".join(parts)
        what = f"{n} × {next(iter(models))}" if len(models) == 1 else f"{n} thiết bị {CATEGORY_LABELS[cat].lower()}"
        out.append({"kind": "capex", "source": "replace", "sourceId": min(a.id for a in assets), "sourceCode": f"{n} thiết bị",
                    "categoryId": by_code.get(CATEGORY_CODES.get(cat, ""), fallback), "vendorId": None, "costCenterId": None,
                    "title": (f"Thay thế {what}" + (f" — {business}" if business else ""))[:255],
                    "startDate": min(max(pairs[0][1], year_start), year_end), "terms": 1, "baseNet": 0,
                    "vatPct": Decimal(10), "detail": _clip(detail), "priority": "must" if must else "should"})
    for (cat, business), assets in extend.items():
        assets.sort(key=lambda a: (a.acquired_on, a.device_id or ""))
        n = sum(a.qty or 1 for a in assets)
        reasons = sorted({DECISION_REASONS.get(a.decision_reason or "", "") for a in assets} - {""})
        parts = [f"{a.device_id or a.asset_tag or a.serial} (SN {a.serial or '—'}, "
                 f"{age_text(months_between(a.acquired_on, year_start))})" for a in assets]
        detail = (", ".join(f"{q} × {m}" for m, q in _models(assets).items()) + ". Không thay"
                  + (f" ({', '.join(reasons).lower()})" if reasons else "") + ", chưa có Opex bảo hành: " + "; ".join(parts))
        out.append({"kind": "opex", "source": "extend", "sourceId": min(a.id for a in assets), "sourceCode": f"{n} thiết bị",
                    "categoryId": by_code.get(CATEGORY_CODES.get(cat, ""), fallback), "vendorId": None, "costCenterId": None,
                    "title": (f"Gia hạn bảo hành {n} thiết bị {CATEGORY_LABELS[cat].lower()} dùng tiếp"
                              + (f" — {business}" if business else ""))[:255],
                    "startDate": year_start, "terms": 1, "baseNet": 0, "vatPct": Decimal(10), "detail": _clip(detail),
                    "priority": "must"})
    out.sort(key=lambda s: (s["source"], s["priority"] != "must", s["startDate"], s["title"]))
    return out


def extend_serials(db: Session, asset_id: int, year_start: dt.date, year_end: dt.date) -> List[str]:
    """Serials of an "extend" plan line (for the Opex created when the plan is applied)."""
    return [a.serial for a in group_of(db, asset_id, year_end)
            if a.serial and (extended_until(a, year_start) or dt.date.min) >= year_end][:50]


# ---------------------------------------------------------------- manual entry (one device at a time)
def save_manual(db: Session, a: Optional[DcAsset], d: Dict[str, Any], username: str) -> DcAsset:
    """Create (a is None) or edit one device. Same rules as an imported row; the identity follows the asset
    tag (or SN + ID) and must stay unique. A device typed by hand has no sheet: an import with "mark missing"
    never removes it."""
    def txt(f: str, required: bool = False) -> str:
        v = _text(d.get(f))[:_LIMITS.get(f, 255)]
        if required and not v:
            raise ImportError_(f"Thiếu {_LABELS[f]}.")
        return v
    cat = str(d.get("category") or "")
    if cat not in CATEGORIES:
        raise ImportError_("Chọn nhóm thiết bị.")
    acquired, ok = _date(d.get("acquiredOn"))
    if not ok:
        raise ImportError_("Ngày đưa vào dùng không hợp lệ (dd/mm/yyyy).")
    if acquired and acquired > dt.date.today() + dt.timedelta(days=366):
        raise ImportError_("Ngày đưa vào dùng ở quá xa trong tương lai.")
    rec = {f: txt(f) for f in _LIMITS}
    rec["deviceId"] = txt("deviceId", required=True)
    if not serial_key(rec["serial"]) and not rec["assetTag"]:
        raise ImportError_("Nhập SN hoặc asset tag.")
    rec.update(category=cat, acquiredOn=acquired, qty=_qty(d.get("qty") or 1), removed=bool(d.get("removed")),
               serialKey=serial_key(rec["serial"]), sheet=a.sheet if a else None)
    key = asset_key(cat, rec["serial"], rec["assetTag"], rec["deviceId"])
    clash = db.query(DcAsset.id).filter(DcAsset.asset_key == key, DcAsset.id != (a.id if a else -1)).first()
    if clash:
        raise ImportError_("Đã có thiết bị cùng " + ("asset tag." if rec["assetTag"] else "SN và tên."))
    vals = _values(rec)
    if a is None:
        a = DcAsset(asset_key=key, imported_at=dt.datetime.utcnow(), updated_by=username, **vals)
        db.add(a)
    else:
        for c, v in vals.items():
            setattr(a, c, v)
        a.asset_key, a.imported_at, a.updated_by = key, dt.datetime.utcnow(), username
    return a


_LABELS = {"deviceId": "tên thiết bị (ID)", "serial": "SN", "assetTag": "asset tag"}
