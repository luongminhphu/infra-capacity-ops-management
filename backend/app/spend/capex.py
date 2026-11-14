"""Capex projects and the planned Opex they generate (docs/design/spend-module.md §2.2).

`plan_item` is pure: given an item, its project and the settings it returns the renewal the
item will need, or the reason there is none. `reconcile` applies the plan to the database:

* a generated Opex is "managed" while it is planned, unconfirmed and untouched by hand
  (amount_mode = 'auto') — only managed rows follow Capex edits;
* confirmed or hand-edited rows are never overwritten; the detail view shows the difference;
* an item removed / without coverage loses its managed Opex; an unmanaged one is kept and
  unlinked from the item (still linked to the project).

Generation only happens once the project left 'draft': a draft is a proposal and must not
inflate next year's budget. Drafts still show the preview.
"""
import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional, Tuple

from fastapi import Request
from sqlalchemy.orm import Session

from ..models import User
from . import codes, fiscal, service
from .models import CapexItem, CapexProject, Contract, OpexAsset, OpexAttachment, OpexItem, SpendCategory, SpendReminderLog
from .settings import SpendSettings

GENERATING_STATUSES = ("approved", "in_progress", "closed")
PROJECT_STATUSES = ("draft", "approved", "in_progress", "closed")
PROJECT_LABELS = {"draft": "Nháp", "approved": "Đã duyệt", "in_progress": "Đang triển khai", "closed": "Đã đóng"}
# (from, to) -> minimum spend role
PROJECT_TRANSITIONS = {
    ("draft", "approved"): "approver",
    ("approved", "draft"): "approver",
    ("approved", "in_progress"): "editor",
    ("in_progress", "closed"): "editor",
    ("closed", "in_progress"): "approver",
}


@dataclass
class Planned:
    start_date: dt.date
    end_date: dt.date
    fiscal_year: int
    amount_net: int
    vat_pct: Decimal
    amount_gross: int
    pct: Optional[Decimal]          # None when the amount was typed on the item


def renewal_pct(item: CapexItem, category: Optional[SpendCategory]) -> Optional[Decimal]:
    if item.renewal_pct is not None:
        return Decimal(item.renewal_pct)
    if category is not None and category.renewal_pct is not None:
        return Decimal(category.renewal_pct)
    return None


def plan_item(item: CapexItem, project: CapexProject, category: Optional[SpendCategory],
              cfg: SpendSettings) -> Tuple[Optional[Planned], Optional[str]]:
    """(renewal, None) or (None, reason in Vietnamese)."""
    years = item.coverage_years or 0
    if years <= 0:
        return None, "Không có bảo hành / license / support đi kèm"
    base = item.start_date or project.go_live_date
    if base is None:
        return None, "Thiếu ngày bắt đầu (nhập ở hạng mục hoặc ngày go-live của dự án)"
    if item.renewal_amount is not None:
        net, pct = int(item.renewal_amount), None
    else:
        pct = renewal_pct(item, category)
        if pct is None:
            return None, "Loại chi phí không có % gia hạn — nhập % hoặc số tiền gia hạn cho hạng mục"
        net = int((Decimal(item.amount_net) * pct / Decimal(100)).quantize(Decimal(1), ROUND_HALF_UP))
    # Renewal starts the day after the included coverage ends; it covers one year.
    start = fiscal.term_end(base, years) + dt.timedelta(days=1)
    end = fiscal.term_end(start, 1)
    vat = Decimal(item.vat_pct if item.vat_pct is not None else 10)
    return Planned(start_date=start, end_date=end, fiscal_year=fiscal.fiscal_year(start, cfg.fiscal_start_month),
                   amount_net=net, vat_pct=vat, amount_gross=service.gross_of(net, vat), pct=pct), None


def is_managed(o: OpexItem) -> bool:
    return o.origin == "capex" and o.status == "planned" and o.confirmed_at is None and o.amount_mode == "auto"


def _title(item: CapexItem) -> str:
    return f"Gia hạn: {item.description}"[:255]


def _apply_plan(o: OpexItem, item: CapexItem, project: CapexProject, p: Planned) -> None:
    o.title = _title(item)
    o.category_id, o.vendor_id = item.category_id, item.vendor_id
    o.start_date, o.end_date, o.fiscal_year = p.start_date, p.end_date, p.fiscal_year
    o.amount_net, o.vat_pct, o.amount_gross = p.amount_net, p.vat_pct, p.amount_gross
    o.cost_center_id = project.cost_center_id
    o.owner_name = project.owner_name or o.owner_name or project.created_by


def _plan_fields(o: OpexItem) -> Dict[str, object]:
    return {"startDate": o.start_date, "endDate": o.end_date, "fiscalYear": o.fiscal_year,
            "amountNet": o.amount_net, "amountGross": o.amount_gross}


def _delete_opex(db: Session, o: OpexItem) -> None:
    db.query(OpexAttachment).filter(OpexAttachment.opex_id == o.id).delete(synchronize_session=False)
    db.query(OpexAsset).filter(OpexAsset.opex_id == o.id).delete(synchronize_session=False)
    db.query(SpendReminderLog).filter(SpendReminderLog.opex_id == o.id).delete(synchronize_session=False)
    db.delete(o)


def reconcile(db: Session, project: CapexProject, cfg: SpendSettings, user: User,
              request: Optional[Request]) -> Dict[str, List[str]]:
    """Bring the generated Opex of `project` in line with its items. Runs in the caller's
    transaction and is idempotent: a second call with no change does nothing."""
    out = {"created": [], "updated": [], "removed": [], "kept": []}
    items = db.query(CapexItem).filter(CapexItem.project_id == project.id).all()
    cats = {c.id: c for c in db.query(SpendCategory).all()}
    existing = db.query(OpexItem).filter(OpexItem.capex_project_id == project.id, OpexItem.origin == "capex").all()
    by_item = {o.capex_item_id: o for o in existing if o.capex_item_id is not None}
    generating = project.status in GENERATING_STATUSES
    item_ids = set()
    for item in items:
        item_ids.add(item.id)
        plan, _reason = plan_item(item, project, cats.get(item.category_id), cfg) if generating else (None, "draft")
        o = by_item.get(item.id)
        if plan is None or item.vendor_id is None:
            if o is not None:
                _drop(db, o, out, user, request, project)
            continue
        if o is None:
            o = OpexItem(origin="capex", status="planned", amount_mode="auto", billing_cycle="yearly",
                         capex_project_id=project.id, capex_item_id=item.id, created_by=user.username, row_version=1)
            _apply_plan(o, item, project, plan)
            o.code = codes.next_code(db, "opex", plan.fiscal_year)
            db.add(o)
            db.flush()
            out["created"].append(o.code)
            service.audit(db, user, request, "spend.opex.create", code=o.code, capex=project.code, after=service.snapshot(o))
        elif is_managed(o):
            before = _plan_fields(o)
            snap = service.snapshot(o)
            _apply_plan(o, item, project, plan)
            if _plan_fields(o) != before or service.snapshot(o) != snap:
                o.row_version += 1
                o.updated_at, o.updated_by = dt.datetime.utcnow(), user.username
                out["updated"].append(o.code)
                service.audit(db, user, request, "spend.opex.update", code=o.code, capex=project.code,
                              changes=service.diff(snap, service.snapshot(o)))
        else:
            out["kept"].append(o.code)
    for o in existing:
        if o.capex_item_id is not None and o.capex_item_id not in item_ids:
            _drop(db, o, out, user, request, project)
    return out


def _drop(db: Session, o: OpexItem, out, user: User, request, project: CapexProject) -> None:
    from .models import ProposalOpex
    has_contract = (db.query(Contract.id).filter(Contract.opex_id == o.id).first() is not None
                    or db.query(ProposalOpex.opex_id).filter(ProposalOpex.opex_id == o.id).first() is not None)
    if is_managed(o) and not has_contract:
        out["removed"].append(o.code)
        service.audit(db, user, request, "spend.opex.delete", code=o.code, capex=project.code, before=service.snapshot(o),
                      reason="Hạng mục Capex bị xoá hoặc không còn sinh gia hạn")
        _delete_opex(db, o)
    else:
        o.capex_item_id = None           # keep the confirmed / edited item, still tied to the project
        out["kept"].append(o.code)


def item_view(item: CapexItem, project: CapexProject, cats: Dict[int, SpendCategory], cfg: SpendSettings,
              opex: Optional[OpexItem], vendors: Dict[int, object]) -> Dict[str, object]:
    plan, reason = plan_item(item, project, cats.get(item.category_id), cfg)
    if plan is not None and item.vendor_id is None:
        plan, reason = None, "Thiếu nhà cung cấp"
    cat = cats.get(item.category_id)
    ven = vendors.get(item.vendor_id)
    view = {
        "id": item.id, "categoryId": item.category_id, "categoryName": cat.name if cat else None,
        "description": item.description, "vendorId": item.vendor_id, "vendorName": getattr(ven, "name", None),
        "quantity": item.quantity, "amountNet": item.amount_net, "vatPct": float(item.vat_pct), "amountGross": item.amount_gross,
        "startDate": item.start_date.isoformat() if item.start_date else None, "coverageYears": item.coverage_years,
        "renewalPct": float(item.renewal_pct) if item.renewal_pct is not None else None,
        "renewalAmount": item.renewal_amount, "sortOrder": item.sort_order,
        "effectivePct": float(plan.pct) if plan and plan.pct is not None else None,
        "plan": None if plan is None else {
            "startDate": plan.start_date.isoformat(), "endDate": plan.end_date.isoformat(), "fiscalYear": plan.fiscal_year,
            "amountNet": plan.amount_net, "amountGross": plan.amount_gross},
        "noPlanReason": reason, "opex": None,
    }
    if opex is not None:
        state = "managed" if is_managed(opex) else ("confirmed" if opex.confirmed_at else "edited")
        differs = plan is not None and (opex.start_date != plan.start_date or opex.amount_net != plan.amount_net
                                        or opex.fiscal_year != plan.fiscal_year)
        view["opex"] = {"code": opex.code, "status": opex.status, "state": state, "fiscalYear": opex.fiscal_year,
                        "startDate": opex.start_date.isoformat(), "amountNet": opex.amount_net,
                        "amountGross": opex.amount_gross, "differsFromPlan": bool(differs and state != "managed")}
    return view
