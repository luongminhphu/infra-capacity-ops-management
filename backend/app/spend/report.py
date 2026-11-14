"""Budget plan report: ONE function builds every number shown on the web dashboard, in the
PDF and in the Excel workbook (build_budget_report), so the three can never disagree.

Rows are unified across the two sources:
* Opex item  -> one row (fiscal year of the item);
* Capex item -> one row (fiscal year of its project; cost centre / status of the project).

Filters (all optional): fiscalYears, kind (opex|capex|all), categoryIds, vendorIds,
costCenterIds, opexStatuses, capexStatuses, expiryFrom/expiryTo (Opex end date; Capex rows are
left out when an expiry range is set), amountMin/amountMax (amount after VAT of the row).
"""
import datetime as dt
from collections import defaultdict
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from . import fiscal, service
from .models import (
    OPEX_STATUSES, CapexItem, CapexProject, OpexItem, SpendCategory, SpendCostCenter, SpendVendor,
)
from .settings import SpendSettings

CAPEX_STATUSES = ("draft", "approved", "in_progress", "closed")
DEFAULT_OPEX_STATUSES = ["planned", "active", "renewed"]          # "stopped" is not spent
DEFAULT_CAPEX_STATUSES = ["approved", "in_progress", "closed"]    # drafts are proposals, not budget
CAPEX_LABELS = {"draft": "Nháp", "approved": "Đã duyệt", "in_progress": "Đang triển khai", "closed": "Đã đóng"}
JSON_ROW_LIMIT = 500
EXPIRY_HORIZON_DAYS = 365


class FilterError(ValueError):
    pass


def _ints(v: Any, label: str) -> List[int]:
    if v in (None, "", []):
        return []
    if not isinstance(v, list):
        v = [v]
    try:
        out = sorted({int(x) for x in v})
    except (TypeError, ValueError) as exc:
        raise FilterError(f"{label}: phải là danh sách số.") from exc
    if len(out) > 200:
        raise FilterError(f"{label}: tối đa 200 giá trị.")
    return out


def _date(v: Any, label: str) -> Optional[dt.date]:
    if v in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(str(v))
    except ValueError as exc:
        raise FilterError(f"{label} không hợp lệ (YYYY-MM-DD).") from exc


def _money(v: Any, label: str) -> Optional[int]:
    if v in (None, ""):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError) as exc:
        raise FilterError(f"{label} phải là số nguyên đồng.") from exc
    if n < 0:
        raise FilterError(f"{label} không được âm.")
    return n


def parse_filters(data: Optional[Dict[str, Any]], cfg: SpendSettings, today: dt.date) -> Dict[str, Any]:
    """Validated, normalised filters. Default: current and next fiscal year, spent statuses."""
    data = data or {}
    cur = fiscal.fiscal_year(today, cfg.fiscal_start_month)
    f = {
        "fiscalYears": _ints(data.get("fiscalYears"), "Năm tài chính") or [cur, cur + 1],
        "kind": data.get("kind") or "all",
        "categoryIds": _ints(data.get("categoryIds"), "Loại chi phí"),
        "vendorIds": _ints(data.get("vendorIds"), "Nhà cung cấp"),
        "costCenterIds": _ints(data.get("costCenterIds"), "Cost center"),
        "opexStatuses": list(data.get("opexStatuses") or DEFAULT_OPEX_STATUSES),
        "capexStatuses": list(data.get("capexStatuses") or DEFAULT_CAPEX_STATUSES),
        "expiryFrom": _date(data.get("expiryFrom"), "Hết hạn từ ngày"),
        "expiryTo": _date(data.get("expiryTo"), "Hết hạn đến ngày"),
        "amountMin": _money(data.get("amountMin"), "Số tiền từ"),
        "amountMax": _money(data.get("amountMax"), "Số tiền đến"),
    }
    if f["kind"] not in ("all", "opex", "capex"):
        raise FilterError("Loại báo cáo phải là all, opex hoặc capex.")
    if any(s not in OPEX_STATUSES for s in f["opexStatuses"]) or any(s not in CAPEX_STATUSES for s in f["capexStatuses"]):
        raise FilterError("Trạng thái lọc không hợp lệ.")
    if any(not 2000 <= y <= 2100 for y in f["fiscalYears"]) or len(f["fiscalYears"]) > 10:
        raise FilterError("Năm tài chính: tối đa 10 năm, từ 2000 đến 2100.")
    if f["expiryFrom"] and f["expiryTo"] and f["expiryTo"] < f["expiryFrom"]:
        raise FilterError("Khoảng ngày hết hạn không hợp lệ.")
    if f["amountMin"] is not None and f["amountMax"] is not None and f["amountMax"] < f["amountMin"]:
        raise FilterError("Khoảng số tiền không hợp lệ.")
    return f


def public_filters(f: Dict[str, Any]) -> Dict[str, Any]:
    return {k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in f.items()}


def fiscal_quarter(d: dt.date, start_month: int) -> Dict[str, Any]:
    fy = fiscal.fiscal_year(d, start_month)
    q = ((d.month - start_month) % 12) // 3 + 1
    return {"key": f"{fy}-Q{q}", "label": f"Q{q}/NTC {fy}", "fiscalYear": fy, "quarter": q}


# ---------------------------------------------------------------- rows
def _rows(db: Session, f: Dict[str, Any], lk: service.Lookups) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    def keep(amount: int) -> bool:
        return ((f["amountMin"] is None or amount >= f["amountMin"]) and
                (f["amountMax"] is None or amount <= f["amountMax"]))

    def names(cat_id, ven_id, cc_id):
        cat, ven, cc = lk.categories.get(cat_id), lk.vendors.get(ven_id), lk.cost_centers.get(cc_id)
        return {"categoryId": cat_id, "category": cat.name if cat else "—", "categoryGroup": cat.grp if cat else "other",
                "vendorId": ven_id, "vendor": ven.name if ven else "—",
                "costCenterId": cc_id, "costCenter": f"{cc.code} — {cc.name}" if cc else "—"}

    if f["kind"] in ("all", "opex"):
        q = db.query(OpexItem).filter(OpexItem.fiscal_year.in_(f["fiscalYears"]), OpexItem.status.in_(f["opexStatuses"]))
        if f["categoryIds"]:
            q = q.filter(OpexItem.category_id.in_(f["categoryIds"]))
        if f["vendorIds"]:
            q = q.filter(OpexItem.vendor_id.in_(f["vendorIds"]))
        if f["costCenterIds"]:
            q = q.filter(OpexItem.cost_center_id.in_(f["costCenterIds"]))
        if f["expiryFrom"]:
            q = q.filter(OpexItem.end_date >= f["expiryFrom"])
        if f["expiryTo"]:
            q = q.filter(OpexItem.end_date <= f["expiryTo"])
        items = q.all()
        cpx_ids = {o.capex_project_id for o in items if o.capex_project_id}
        cpx_codes = dict(db.query(CapexProject.id, CapexProject.code).filter(CapexProject.id.in_(cpx_ids))) if cpx_ids else {}
        for o in items:
            if not keep(o.amount_gross):
                continue
            rows.append({"kind": "opex", "code": o.code, "title": o.title, "fiscalYear": o.fiscal_year,
                         **names(o.category_id, o.vendor_id, o.cost_center_id),
                         "startDate": o.start_date, "endDate": o.end_date, "status": o.status,
                         "statusLabel": service.STATUS_LABELS[o.status], "origin": o.origin,
                         "originLabel": service.ORIGIN_LABELS[o.origin], "capexCode": cpx_codes.get(o.capex_project_id),
                         "owner": o.owner_name, "amountNet": o.amount_net, "amountGross": o.amount_gross})

    if f["kind"] in ("all", "capex") and not (f["expiryFrom"] or f["expiryTo"]):
        q = (db.query(CapexItem, CapexProject).join(CapexProject, CapexProject.id == CapexItem.project_id)
             .filter(CapexProject.fiscal_year.in_(f["fiscalYears"]), CapexProject.status.in_(f["capexStatuses"])))
        if f["categoryIds"]:
            q = q.filter(CapexItem.category_id.in_(f["categoryIds"]))
        if f["vendorIds"]:
            q = q.filter(CapexItem.vendor_id.in_(f["vendorIds"]))
        if f["costCenterIds"]:
            q = q.filter(CapexProject.cost_center_id.in_(f["costCenterIds"]))
        for it, p in q.all():
            if not keep(it.amount_gross):
                continue
            rows.append({"kind": "capex", "code": p.code, "title": f"{p.name} — {it.description}", "fiscalYear": p.fiscal_year,
                         **names(it.category_id, it.vendor_id, p.cost_center_id),
                         "startDate": it.start_date or p.go_live_date, "endDate": None, "status": p.status,
                         "statusLabel": CAPEX_LABELS[p.status], "origin": "capex", "originLabel": "Đầu tư",
                         "capexCode": p.code, "owner": p.owner_name or "", "amountNet": it.amount_net,
                         "amountGross": it.amount_gross})
    rows.sort(key=lambda r: (r["fiscalYear"], r["kind"], r["category"], r["code"]))
    return rows


# ---------------------------------------------------------------- aggregates
def _pct_change(a: int, b: int) -> Optional[float]:
    return None if not a else round((b - a) * 100.0 / a, 1)


def build_budget_report(db: Session, filters: Dict[str, Any], cfg: SpendSettings,
                        today: Optional[dt.date] = None) -> Dict[str, Any]:
    today = today or fiscal.today_vn()
    lk = service.Lookups(db)
    rows = _rows(db, filters, lk)
    cur = fiscal.fiscal_year(today, cfg.fiscal_start_month)

    by_year: Dict[int, Dict[str, int]] = {y: {"opex": 0, "capex": 0} for y in filters["fiscalYears"]}
    by_cat: Dict[str, Dict[str, Any]] = {}
    vendors: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        by_year.setdefault(r["fiscalYear"], {"opex": 0, "capex": 0})[r["kind"]] += r["amountGross"]
        c = by_cat.setdefault(r["category"], {"category": r["category"], "opex": 0, "capex": 0, "count": 0})
        c[r["kind"]] += r["amountGross"]
        c["count"] += 1
        if r["vendorId"] is not None:
            v = vendors.setdefault(r["vendor"], {"vendor": r["vendor"], "total": 0, "count": 0, "opex": 0, "capex": 0})
            v["total"] += r["amountGross"]
            v[r["kind"]] += r["amountGross"]
            v["count"] += 1

    total = sum(r["amountGross"] for r in rows)
    years = [{"fiscalYear": y, "opex": v["opex"], "capex": v["capex"], "total": v["opex"] + v["capex"]}
             for y, v in sorted(by_year.items())]
    cats = sorted(({**c, "total": c["opex"] + c["capex"]} for c in by_cat.values()), key=lambda c: -c["total"])
    for c in cats:
        c["sharePct"] = round(c["total"] * 100.0 / total, 1) if total else 0.0
    top = sorted(vendors.values(), key=lambda v: (-v["total"], v["vendor"]))[:10]
    for v in top:
        v["sharePct"] = round(v["total"] * 100.0 / total, 1) if total else 0.0

    year_of = {y["fiscalYear"]: y for y in years}
    zero = {"opex": 0, "capex": 0, "total": 0}
    cur_y, next_y = {**zero, **year_of.get(cur, {}), "fiscalYear": cur}, {**zero, **year_of.get(cur + 1, {}), "fiscalYear": cur + 1}
    compare = {"current": cur_y, "next": next_y, "deltaPct": _pct_change(cur_y["total"], next_y["total"]),
               "inFilter": cur in filters["fiscalYears"] and cur + 1 in filters["fiscalYears"]}

    # Expiring Opex (still to decide: planned / active), grouped by fiscal quarter of the end date.
    horizon = today + dt.timedelta(days=EXPIRY_HORIZON_DAYS)
    quarters: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if r["kind"] != "opex" or r["status"] not in ("planned", "active") or r["endDate"] is None:
            continue
        if not (filters["expiryFrom"] or filters["expiryTo"]) and not (today - dt.timedelta(days=90) <= r["endDate"] <= horizon):
            continue
        fq = fiscal_quarter(r["endDate"], cfg.fiscal_start_month)
        g = quarters.setdefault(fq["key"], {**fq, "count": 0, "total": 0, "items": []})
        g["count"] += 1
        g["total"] += r["amountGross"]
        g["items"].append({"code": r["code"], "title": r["title"], "vendor": r["vendor"], "endDate": r["endDate"].isoformat(),
                           "daysLeft": (r["endDate"] - today).days, "amountGross": r["amountGross"], "owner": r["owner"]})
    expiring = [quarters[k] for k in sorted(quarters)]
    for g in expiring:
        g["items"].sort(key=lambda i: i["endDate"])

    from_capex = [r for r in rows if r["kind"] == "opex" and r["origin"] == "capex"]
    capex_by_year = defaultdict(int)
    for r in from_capex:
        capex_by_year[r["fiscalYear"]] += r["amountGross"]

    planned_next = [r for r in rows if r["kind"] == "opex" and r["status"] == "planned" and r["fiscalYear"] == cur + 1]
    soon = [i for g in expiring for i in g["items"] if 0 <= i["daysLeft"] <= max(cfg.reminder_days)]
    overdue = [i for g in expiring for i in g["items"] if i["daysLeft"] < 0]
    decisions = []
    if overdue:
        decisions.append({"cls": "crit", "title": f"{len(overdue)} khoản đã quá hạn nhưng chưa gia hạn / ngừng",
                          "decision": "Quyết định gia hạn hoặc ngừng ngay", "amount": sum(i["amountGross"] for i in overdue),
                          "timeframe": "Ngay"})
    if soon:
        decisions.append({"cls": "bad", "title": f"{len(soon)} khoản hết hạn trong {max(cfg.reminder_days)} ngày",
                          "decision": "Duyệt gia hạn (hoặc ngừng) trước ngày hết hạn", "amount": sum(i["amountGross"] for i in soon),
                          "timeframe": f"Trong {max(cfg.reminder_days)} ngày"})
    if planned_next:
        decisions.append({"cls": "warn", "title": f"{len(planned_next)} khoản Opex dự kiến của NTC {cur + 1}",
                          "decision": f"Duyệt đưa vào ngân sách NTC {cur + 1}", "amount": sum(r["amountGross"] for r in planned_next),
                          "timeframe": f"Trước NTC {cur + 1}"})

    opex_total = sum(r["amountGross"] for r in rows if r["kind"] == "opex")
    capex_total = total - opex_total
    if compare["inFilter"] and cur_y["total"]:
        change = compare["deltaPct"]
        headline = (f"Ngân sách NTC {cur + 1} {'tăng' if change >= 0 else 'giảm'} {abs(change):.1f}% so với NTC {cur} "
                    f"({service.money_vn(next_y['total'])} so với {service.money_vn(cur_y['total'])}).")
    else:
        headline = f"Tổng ngân sách theo bộ lọc: {service.money_vn(total)} ({len(rows)} dòng)."

    return {
        "generatedFor": today.isoformat(), "currentFiscalYear": cur, "filters": public_filters(filters),
        "headline": headline,
        "totals": {"total": total, "opex": opex_total, "capex": capex_total, "net": sum(r["amountNet"] for r in rows),
                   "count": len(rows), "vendorCount": len(vendors)},
        "byYear": years, "byCategory": cats, "compare": compare, "expiringByQuarter": expiring,
        "fromCapex": {"total": sum(r["amountGross"] for r in from_capex), "count": len(from_capex),
                      "byYear": [{"fiscalYear": y, "total": v} for y, v in sorted(capex_by_year.items())],
                      "items": [{k: r[k] for k in ("code", "title", "fiscalYear", "vendor", "capexCode", "status",
                                                   "statusLabel", "amountGross")} for r in from_capex]},
        "topVendors": top, "decisions": decisions, "rows": rows,
    }


def to_json(report: Dict[str, Any]) -> Dict[str, Any]:
    """API shape: dates as ISO strings, detail rows capped (Excel carries every row)."""
    rows = report["rows"]
    out = {k: v for k, v in report.items() if k != "rows"}
    out["rowsTotal"] = len(rows)
    out["rows"] = [{k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in r.items()} for r in rows[:JSON_ROW_LIMIT]]
    return out


def filter_summary(db: Session, f: Dict[str, Any]) -> str:
    """One line describing the filters, for report headers (values are escaped by the caller)."""
    parts = ["NTC " + ", ".join(str(y) for y in f["fiscalYears"])]
    parts.append({"all": "Opex + Capex", "opex": "chỉ Opex", "capex": "chỉ Capex"}[f["kind"]])
    if f["categoryIds"]:
        parts.append("loại: " + ", ".join(c.name for c in db.query(SpendCategory).filter(SpendCategory.id.in_(f["categoryIds"]))))
    if f["vendorIds"]:
        parts.append("NCC: " + ", ".join(v.name for v in db.query(SpendVendor).filter(SpendVendor.id.in_(f["vendorIds"]))))
    if f["costCenterIds"]:
        parts.append("cost center: " + ", ".join(c.code for c in db.query(SpendCostCenter).filter(SpendCostCenter.id.in_(f["costCenterIds"]))))
    if f["expiryFrom"] or f["expiryTo"]:
        parts.append(f"hết hạn {service.date_vn(f['expiryFrom']) if f['expiryFrom'] else '…'} – "
                     f"{service.date_vn(f['expiryTo']) if f['expiryTo'] else '…'}")
    if f["amountMin"] is not None or f["amountMax"] is not None:
        parts.append(f"số tiền {service.money_vn(f['amountMin']) if f['amountMin'] is not None else '0'} – "
                     f"{service.money_vn(f['amountMax']) if f['amountMax'] is not None else '…'}")
    return " · ".join(parts)
