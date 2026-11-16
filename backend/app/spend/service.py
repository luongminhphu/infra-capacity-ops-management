"""Opex business rules: validation, money, derived status, state commands, audit.

Status is changed only through commands (confirm / renew / stop / reactivate), never by the
generic update — see docs/design/spend-module.md §1.2.
"""
import datetime as dt
import json
import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Iterable, List, Optional

from fastapi import HTTPException, Request
from sqlalchemy import update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from ..models import AuditLog, User
from ..timeutil import to_iso_utc
from . import codes, fiscal
from .models import (
    BILLING_CYCLES, OpexAsset, OpexAttachment, OpexItem, SpendCategory, SpendCostCenter, SpendFile, SpendVendor,
)
from .settings import SpendSettings

STATUS_LABELS = {"planned": "Dự kiến", "active": "Đang hiệu lực", "expiring": "Sắp hết hạn", "overdue": "Quá hạn",
                 "renewed": "Đã gia hạn", "stopped": "Ngừng"}
CYCLE_LABELS = {"one_time": "Một lần", "monthly": "Hằng tháng", "quarterly": "Hằng quý", "yearly": "Hằng năm"}
ORIGIN_LABELS = {"manual": "Nhập tay", "capex": "Sinh từ Capex", "renewal": "Gia hạn"}
STALE_MESSAGE = "Dữ liệu đã được người khác sửa, tải lại rồi thử lại."
EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")

DEFAULT_CATEGORIES = [
    ("HW_SERVER", "Bảo hành máy chủ", "hw_warranty", 15),
    ("HW_STORAGE", "Bảo hành storage", "hw_warranty", 15),
    ("HW_NETWORK", "Bảo hành thiết bị mạng", "hw_warranty", 15),
    ("LIC_FIREWALL", "License firewall", "license", 20),
    ("LIC_VMWARE", "License VMware", "license", 20),
    ("LIC_BACKUP", "License backup", "license", 20),
    ("DC_MAINT", "Bảo trì DC", "dc_maintenance", None),
    ("OUTSOURCED", "Dịch vụ thuê ngoài", "outsourced", None),
    ("OTHER", "Khác", "other", None),
]


DEFAULT_PAYMENT_DOCS = [
    ("Đề nghị thanh toán", True), ("Hóa đơn GTGT", True), ("Biên bản nghiệm thu / bàn giao", True),
    ("Bảo lãnh tạm ứng / thực hiện hợp đồng", False), ("Phiếu xuất kho, CO / CQ", False),
]


def ensure_defaults(db: Session) -> None:
    """Seed the category list, the payment document checklist and the renovation cost groups once (empty tables only —
    admins edit them afterwards)."""
    from .models import PaymentDocDefault
    if db.query(SpendCategory.id).first() is None:
        for i, (code, name, grp, pct) in enumerate(DEFAULT_CATEGORIES):
            db.add(SpendCategory(code=code, name=name, grp=grp, renewal_pct=pct, sort_order=i))
        db.commit()
    if db.query(PaymentDocDefault.id).first() is None:
        for i, (name, required) in enumerate(DEFAULT_PAYMENT_DOCS):
            db.add(PaymentDocDefault(name=name, required=required, sort_order=i))
        db.commit()
    from .models import RenovationCostGroup
    from .renovation import DEFAULT_GROUPS
    if db.query(RenovationCostGroup.id).first() is None:
        for ws, groups in DEFAULT_GROUPS.items():
            for i, (name, aliases) in enumerate(groups):
                db.add(RenovationCostGroup(workstream=ws, name=name, aliases="\n".join(aliases), seq=i))
        db.commit()


# ---------------------------------------------------------------- helpers
def audit(db: Session, user: User, request: Optional[Request], action: str, **detail) -> None:
    """Written in the caller's transaction: the audit row commits with the change or not at all."""
    db.add(AuditLog(username=user.username, action=action,
                    client_ip=request.client.host if request is not None and request.client else None,
                    detail=json.dumps(detail, default=str, ensure_ascii=False)))


def norm_name(name: str) -> str:
    s = unicodedata.normalize("NFD", name or "").replace("đ", "d").replace("Đ", "D")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"\s+", " ", s).strip()


def gross_of(net: int, vat_pct: Decimal) -> int:
    return int((Decimal(net) * (Decimal(100) + Decimal(vat_pct)) / Decimal(100)).quantize(Decimal(1), ROUND_HALF_UP))


def money_vn(v: Optional[int]) -> str:
    return "—" if v is None else f"{v:,}".replace(",", ".") + " đ"


def date_vn(d: Optional[dt.date]) -> str:
    return d.strftime("%d/%m/%Y") if d else "—"


def derived_status(item: OpexItem, today: dt.date, cfg: SpendSettings) -> Dict[str, Any]:
    """Display status (5 labels) and the HSC tone. "Sắp hết hạn" / "Quá hạn" are computed,
    never stored: a stored copy would be wrong the first day the job does not run."""
    days_left = (item.end_date - today).days
    status = item.status
    if item.status == "active":
        if days_left < 0:
            status = "overdue"
        elif days_left <= max(cfg.reminder_days):
            status = "expiring"
    if item.status != "active":
        tone = {"planned": "na", "renewed": "ok", "stopped": "na"}[item.status]
    elif days_left < 0:
        tone = "crit"                        # VƯỢT NGƯỠNG
    elif days_left <= min(cfg.reminder_days):
        tone = "bad"                         # CẢNH BÁO
    elif days_left <= max(cfg.reminder_days):
        tone = "warn"                        # THEO DÕI
    else:
        tone = "ok"                          # AN TOÀN
    return {"displayStatus": status, "displayLabel": STATUS_LABELS[status], "tone": tone, "daysLeft": days_left}


class Lookups:
    """Small reference tables loaded once per request (no per-row queries)."""

    def __init__(self, db: Session):
        self.categories = {c.id: c for c in db.query(SpendCategory).all()}
        self.vendors = {v.id: v for v in db.query(SpendVendor).all()}
        self.cost_centers = {c.id: c for c in db.query(SpendCostCenter).all()}


def serialize(item: OpexItem, lk: Lookups, today: dt.date, cfg: SpendSettings, codes_by_id: Dict[int, str] = None,
              successor: Optional[str] = None) -> Dict[str, Any]:
    cat, ven, cc = lk.categories.get(item.category_id), lk.vendors.get(item.vendor_id), lk.cost_centers.get(item.cost_center_id)
    out = {
        "id": item.id, "code": item.code, "fiscalYear": item.fiscal_year,
        "categoryId": item.category_id, "categoryName": cat.name if cat else None,
        "title": item.title, "description": item.description,
        "vendorId": item.vendor_id, "vendorName": ven.name if ven else None,
        "contractRef": item.contract_ref, "poRef": item.po_ref,
        "startDate": item.start_date.isoformat(), "endDate": item.end_date.isoformat(),
        "billingCycle": item.billing_cycle, "billingCycleLabel": CYCLE_LABELS.get(item.billing_cycle),
        "amountNet": item.amount_net, "vatPct": float(item.vat_pct), "amountGross": item.amount_gross,
        "costCenterId": item.cost_center_id, "costCenterName": f"{cc.code} — {cc.name}" if cc else None,
        "ownerName": item.owner_name, "ownerEmail": item.owner_email,
        "status": item.status, "stopReason": item.stop_reason,
        "origin": item.origin, "originLabel": ORIGIN_LABELS.get(item.origin),
        "capexProjectId": item.capex_project_id, "confirmedAt": to_iso_utc(item.confirmed_at), "confirmedBy": item.confirmed_by,
        "renewedFromCode": (codes_by_id or {}).get(item.renewed_from_id) if item.renewed_from_id else None,
        "renewedToCode": successor,
        "rowVersion": item.row_version,
        "createdAt": to_iso_utc(item.created_at), "createdBy": item.created_by,
        "updatedAt": to_iso_utc(item.updated_at), "updatedBy": item.updated_by,
    }
    out.update(derived_status(item, today, cfg))
    return out


# ---------------------------------------------------------------- input validation
def _bad(msg: str):
    raise HTTPException(400, msg)


def _date(v: Any, label: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(v))
    except (TypeError, ValueError):
        _bad(f"{label} không hợp lệ (định dạng YYYY-MM-DD).")


def _money(v: Any, label: str) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        _bad(f"{label} phải là số tiền (đồng).")
    try:
        d = Decimal(str(v))
    except ArithmeticError:
        _bad(f"{label} phải là số tiền (đồng).")
    if d != d.to_integral_value() or d < 0 or d > Decimal("999999999999999"):
        _bad(f"{label} phải là số nguyên đồng, không âm.")
    return int(d)


def _text(v: Any, label: str, max_len: int, required: bool = False) -> Optional[str]:
    s = str(v).strip() if v is not None else ""
    if required and not s:
        _bad(f"Thiếu {label}.")
    if len(s) > max_len:
        _bad(f"{label} dài tối đa {max_len} ký tự.")
    return s or None


def vendor_label(v: Optional[SpendVendor]) -> str:
    """Name used in comparison / evaluation documents and tờ trình: the short name when set."""
    if v is None:
        return "—"
    return (v.short_name or "").strip() or v.name


def find_vendor(db: Session, name: str) -> Optional[SpendVendor]:
    """Vendor whose name, short name or one of its aliases matches `name` (case / accents / spaces ignored)."""
    key = norm_name(name)
    if not key:
        return None
    v = db.query(SpendVendor).filter_by(name_norm=key).first()
    if v:
        return v
    for cand in db.query(SpendVendor).filter(SpendVendor.short_name.isnot(None) | SpendVendor.aliases.isnot(None)):
        if cand.short_name and norm_name(cand.short_name) == key:
            return cand
    for cand in db.query(SpendVendor).filter(SpendVendor.aliases.isnot(None)):
        if key in {norm_name(a) for a in re.split(r"[\n;]+", cand.aliases or "") if a.strip()}:
            return cand
    return None


def resolve_vendor(db: Session, data: Dict[str, Any], user: User, request: Optional[Request]) -> int:
    """vendorId, or vendorName (an unknown name creates the vendor — same normalised name reuses it)."""
    if data.get("vendorId"):
        v = db.get(SpendVendor, int(data["vendorId"]))
        if not v:
            _bad("Nhà cung cấp không tồn tại.")
        return v.id
    name = _text(data.get("vendorName"), "nhà cung cấp", 255, required=True)
    key = norm_name(name)
    v = find_vendor(db, name)
    if not v:
        v = SpendVendor(name=name, name_norm=key)
        db.add(v)
        db.flush()
        audit(db, user, request, "spend.vendor.create", vendor=name)
    return v.id


def apply_fields(db: Session, item: OpexItem, data: Dict[str, Any], cfg: SpendSettings, user: User,
                 request: Optional[Request], creating: bool) -> None:
    cat = db.get(SpendCategory, int(data.get("categoryId") or 0))
    if not cat:
        _bad("Chọn loại chi phí.")
    item.category_id = cat.id
    item.title = _text(data.get("title"), "nội dung dịch vụ", 255, required=True)
    item.description = _text(data.get("description"), "mô tả", 5000)
    item.vendor_id = resolve_vendor(db, data, user, request)
    item.contract_ref = _text(data.get("contractRef"), "mã hợp đồng", 64)
    item.po_ref = _text(data.get("poRef"), "mã PO", 64)
    item.start_date = _date(data.get("startDate"), "Ngày bắt đầu")
    item.end_date = _date(data.get("endDate"), "Ngày kết thúc")
    if item.end_date < item.start_date:
        _bad("Ngày kết thúc phải sau ngày bắt đầu.")
    cycle = data.get("billingCycle") or "yearly"
    if cycle not in BILLING_CYCLES:
        _bad("Chu kỳ không hợp lệ.")
    item.billing_cycle = cycle
    fy = data.get("fiscalYear")
    item.fiscal_year = int(fy) if fy not in (None, "") else fiscal.fiscal_year(item.start_date, cfg.fiscal_start_month)
    if not 2000 <= item.fiscal_year <= 2100:
        _bad("Năm tài chính không hợp lệ.")
    item.amount_net = _money(data.get("amountNet"), "Số tiền trước VAT")
    try:
        vat = Decimal(str(data.get("vatPct", 10)))
    except ArithmeticError:
        _bad("VAT không hợp lệ.")
    if not Decimal(0) <= vat <= Decimal(100):
        _bad("VAT phải từ 0 đến 100%.")
    item.vat_pct = vat.quantize(Decimal("0.01"))
    gross = data.get("amountGross")
    item.amount_gross = _money(gross, "Số tiền sau VAT") if gross not in (None, "") else gross_of(item.amount_net, item.vat_pct)
    if item.amount_gross < item.amount_net:
        _bad("Số tiền sau VAT không được nhỏ hơn trước VAT.")
    cc = data.get("costCenterId")
    if cc:
        if not db.get(SpendCostCenter, int(cc)):
            _bad("Cost center không tồn tại.")
        item.cost_center_id = int(cc)
    else:
        item.cost_center_id = None
    item.owner_name = _text(data.get("ownerName"), "người phụ trách", 128, required=True)
    email = _text(data.get("ownerEmail"), "email người phụ trách", 255)
    if email and not EMAIL_RE.match(email):
        _bad("Email người phụ trách không hợp lệ.")
    item.owner_email = email
    if creating:
        status = data.get("status") or "active"
        if status not in ("planned", "active"):
            _bad("Khoản mới chỉ có thể là Dự kiến hoặc Đang hiệu lực.")
        item.status = status


def replace_assets(db: Session, item: OpexItem, assets: Optional[Iterable[Dict[str, Any]]]) -> None:
    if assets is None:
        return
    assets = list(assets)
    if len(assets) > 500:
        _bad("Tối đa 500 thiết bị cho một khoản.")
    db.query(OpexAsset).filter(OpexAsset.opex_id == item.id).delete(synchronize_session=False)
    for a in assets:
        serial = _text(a.get("serial"), "serial", 128, required=True)
        db.add(OpexAsset(opex_id=item.id, serial=serial, description=_text(a.get("description"), "mô tả thiết bị", 255),
                         vcenter_key=_text(a.get("vcenterKey"), "vCenter", 64), host_name=_text(a.get("hostName"), "host", 255)))


def snapshot(item: OpexItem) -> Dict[str, Any]:
    """Fields compared in the audit trail (money, dates, status, links)."""
    return {"fiscalYear": item.fiscal_year, "amountNet": item.amount_net, "vatPct": str(item.vat_pct),
            "amountGross": item.amount_gross, "startDate": item.start_date, "endDate": item.end_date,
            "status": item.status, "vendorId": item.vendor_id, "categoryId": item.category_id,
            "title": item.title, "ownerName": item.owner_name, "costCenterId": item.cost_center_id}


def diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, List[Any]]:
    return {k: [before.get(k), after.get(k)] for k in after if before.get(k) != after.get(k)}


def create(db: Session, data: Dict[str, Any], cfg: SpendSettings, user: User, request: Optional[Request],
           origin: str = "manual") -> OpexItem:
    item = OpexItem(origin=origin, created_by=user.username, row_version=1)
    apply_fields(db, item, data, cfg, user, request, creating=True)
    item.code = codes.next_code(db, "opex", item.fiscal_year)
    db.add(item)
    db.flush()
    replace_assets(db, item, data.get("assets"))
    audit(db, user, request, "spend.opex.create", code=item.code, after=snapshot(item))
    return item


def renew(db: Session, item: OpexItem, data: Dict[str, Any], cfg: SpendSettings, user: User,
          request: Optional[Request]) -> OpexItem:
    """Mark `item` renewed and create its successor. Reminders stop because only active
    items are reminded; renewing twice hits the UNIQUE renewed_from_id."""
    if item.status != "active":
        raise HTTPException(409, "Chỉ khoản đang hiệu lực mới gia hạn được.")
    if db.query(OpexItem.id).filter(OpexItem.renewed_from_id == item.id).first():
        raise HTTPException(409, "Khoản này đã được gia hạn.")
    start = _date(data["startDate"], "Ngày bắt đầu") if data.get("startDate") else item.end_date + dt.timedelta(days=1)
    if data.get("endDate"):
        end = _date(data["endDate"], "Ngày kết thúc")
    else:
        end = start + (item.end_date - item.start_date)
    base = {
        "categoryId": item.category_id, "title": item.title, "description": item.description, "vendorId": item.vendor_id,
        "contractRef": item.contract_ref, "poRef": item.po_ref, "billingCycle": item.billing_cycle,
        "amountNet": item.amount_net, "vatPct": item.vat_pct, "costCenterId": item.cost_center_id,
        "ownerName": item.owner_name, "ownerEmail": item.owner_email,
    }
    fields = {k: data[k] for k in ("title", "contractRef", "poRef", "amountNet", "vatPct", "amountGross", "fiscalYear", "ownerName")
              if data.get(k) not in (None, "")}
    payload = {**base, **fields, "startDate": start.isoformat(), "endDate": end.isoformat(),
               "status": data.get("status") or "active"}
    if "amountNet" in fields and "amountGross" not in fields:
        payload.pop("amountGross", None)
    new = create(db, payload, cfg, user, request, origin="renewal")
    new.renewed_from_id = item.id
    db.add_all([OpexAsset(opex_id=new.id, serial=a.serial, description=a.description, vcenter_key=a.vcenter_key,
                          host_name=a.host_name) for a in db.query(OpexAsset).filter_by(opex_id=item.id)])
    before = item.status
    bump_version(db, item)                     # SCH-01: conditional UPDATE, not `+= 1`
    item.status = "renewed"
    item.updated_at, item.updated_by = dt.datetime.utcnow(), user.username
    audit(db, user, request, "spend.opex.renew", code=item.code, newCode=new.code, status=[before, "renewed"])
    return new


TRANSITIONS = {
    "confirm": ({"planned"}, "active"),
    "stop": ({"planned", "active"}, "stopped"),
    "reactivate": ({"stopped"}, "active"),
}


def bump_version(db: Session, obj: Any, expected: Any = None, message: str = STALE_MESSAGE) -> None:
    """Optimistic lock enforced by the database, not by a Python comparison:
    UPDATE <table> SET row_version = expected + 1 WHERE id = :id AND row_version = :expected.
    0 rows -> someone else changed the row since `expected` was read -> 409 (the caller's
    transaction is rolled back with the session). On PostgreSQL the UPDATE also takes the row
    lock, so a concurrent writer waits for this transaction and then sees 0 rows.

    `expected` is the client's rowVersion; None means "the version this request loaded".
    Callers must not also do `obj.row_version += 1`."""
    try:
        exp = obj.row_version if expected is None else int(expected)
    except (TypeError, ValueError):
        raise HTTPException(400, "rowVersion không hợp lệ.")
    model = type(obj)
    res = db.execute(update(model).where(model.id == obj.id, model.row_version == exp)
                     .values(row_version=exp + 1).execution_options(synchronize_session=False))
    if res.rowcount != 1:
        raise HTTPException(409, message)
    set_committed_value(obj, "row_version", exp + 1)


def transition(db: Session, item: OpexItem, command: str, user: User, request: Optional[Request],
               reason: Optional[str] = None) -> None:
    allowed, target = TRANSITIONS[command]
    if item.status not in allowed:
        raise HTTPException(409, f"Không thể thực hiện thao tác này khi khoản đang ở trạng thái “{STATUS_LABELS[item.status]}”.")
    before = item.status
    item.status = target
    if command == "confirm":
        item.confirmed_at, item.confirmed_by = dt.datetime.utcnow(), user.username
    if command == "stop":
        item.stop_reason = _text(reason, "lý do ngừng", 500, required=True)
    if command == "reactivate":
        item.stop_reason = None
    bump_version(db, item)
    item.updated_at, item.updated_by = dt.datetime.utcnow(), user.username
    audit(db, user, request, f"spend.opex.{command}", code=item.code, status=[before, target], reason=reason)


def attachments(db: Session, item: OpexItem) -> List[Dict[str, Any]]:
    rows = (db.query(SpendFile).join(OpexAttachment, OpexAttachment.file_id == SpendFile.id)
            .filter(OpexAttachment.opex_id == item.id).order_by(SpendFile.uploaded_at).all())
    return [file_out(f) for f in rows]


def file_out(f: SpendFile) -> Dict[str, Any]:
    return {"id": f.id, "name": f.original_name, "sizeBytes": f.size_bytes, "mime": f.mime,
            "uploadedBy": f.uploaded_by, "uploadedAt": to_iso_utc(f.uploaded_at), "purged": f.purged_at is not None}
