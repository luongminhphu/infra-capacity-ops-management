"""Cài đặt → AI & MCP (admin) and the AI tasks reachable from the UI (super app SA4)."""
import datetime as dt
import json
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import ai_platform, ai_tasks, secrets_box
from ..database import get_db
from ..models import User
from ..security import get_current_user, is_admin, require_admin
from ..spend import ai as transport, service
from ..spend.models import SpendAiConfig

router = APIRouter(prefix="/api", tags=["ai"])


def _out(c) -> Dict[str, Any]:
    states = ai_platform.task_states(c)
    return {"provider": c.provider if c else "none", "model": (c.model if c else None) or transport.DEFAULT_MODEL,
            "effort": (c.effort if c else None) or "medium", "hasApiKey": bool(c and c.api_key_enc),
            "allowExternal": bool(c and c.allow_external), "enabled": ai_platform.enabled(c),
            "maskNames": True if c is None or c.mask_names is None else bool(c.mask_names),
            "budgetUsd": c.budget_usd if c and c.budget_usd is not None else 100,
            "alertUsd": c.alert_usd if c and c.alert_usd is not None else 50,
            "ratePerHour": c.rate_per_hour if c and c.rate_per_hour else 30,
            "tasks": [{"key": k, "label": lbl, "desc": desc, "on": states[k]} for k, (lbl, desc, _) in ai_platform.TASKS.items()],
            "secretsKeyConfigured": secrets_box.dedicated_key(),
            "updatedBy": c.updated_by if c else None}


@router.get("/ai/status")
def ai_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """What the UI may offer: which tasks are on (no key, no budget detail for non-admins)."""
    c = ai_platform.config(db)
    on = ai_platform.enabled(c)
    return {"enabled": on, "tasks": {k: on and v for k, v in ai_platform.task_states(c).items()}}


@router.get("/admin/ai")
def get_ai(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    return {**_out(ai_platform.config(db)), "usage": ai_platform.usage_report(db)}


def _int(v: Any, label: str, lo: int, hi: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(400, f"{label} phải là số nguyên.") from None
    if not lo <= n <= hi:
        raise HTTPException(400, f"{label} từ {lo} đến {hi}.")
    return n


@router.put("/admin/ai")
def put_ai(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_admin), db: Session = Depends(get_db)):
    c = db.get(SpendAiConfig, 1) or SpendAiConfig(id=1, updated_by=user.username)
    provider = data.get("provider", c.provider or "none")
    if provider not in ("none", "anthropic"):
        raise HTTPException(400, "Nhà cung cấp AI không hợp lệ.")
    model = str(data.get("model") or c.model or transport.DEFAULT_MODEL).strip()
    if not model.replace("-", "").replace(".", "").isalnum() or len(model) > 64:
        raise HTTPException(400, "Tên model không hợp lệ.")
    effort = data.get("effort", c.effort or "medium")
    if effort not in ("low", "medium", "high"):
        raise HTTPException(400, "Mức effort không hợp lệ.")
    c.provider, c.model, c.effort = provider, model, effort
    c.allow_external = bool(data.get("allowExternal", c.allow_external))
    if "maskNames" in data:
        c.mask_names = bool(data["maskNames"])
    c.budget_usd = _int(data.get("budgetUsd", c.budget_usd if c.budget_usd is not None else 100), "Hạn mức tháng (USD)", 1, 10000)
    c.alert_usd = _int(data.get("alertUsd", c.alert_usd if c.alert_usd is not None else 50), "Ngưỡng cảnh báo (USD)", 1, 10000)
    if c.alert_usd > c.budget_usd:
        raise HTTPException(400, "Ngưỡng cảnh báo phải nhỏ hơn hoặc bằng hạn mức tháng.")
    c.rate_per_hour = _int(data.get("ratePerHour", c.rate_per_hour or 30), "Số lượt / người / giờ", 1, 1000)
    tasks = data.get("tasks")
    if tasks is not None:
        if not isinstance(tasks, dict):
            raise HTTPException(400, "Danh sách tác vụ không hợp lệ.")
        states = ai_platform.task_states(c)
        states.update({k: bool(v) for k, v in tasks.items() if k in ai_platform.TASKS})
        c.tasks_json = json.dumps(states)
    changed = False
    if data.get("apiKey"):
        c.api_key_enc, changed = secrets_box.encrypt(str(data["apiKey"]).strip()), True
    elif data.get("clearApiKey"):
        c.api_key_enc, changed = None, True
    c.updated_at, c.updated_by = dt.datetime.utcnow(), user.username
    db.merge(c)
    service.audit(db, user, request, "ai.config.update", provider=provider, model=model, allowExternal=c.allow_external,
                  maskNames=c.mask_names, budgetUsd=c.budget_usd, alertUsd=c.alert_usd, keyChanged=changed,
                  tasks=ai_platform.task_states(c))
    db.commit()
    return {**_out(db.get(SpendAiConfig, 1)), "usage": ai_platform.usage_report(db)}


@router.post("/admin/ai/test")
def test_ai(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        return {"ok": True, "message": transport.test_connection(ai_platform.config(db))}
    except transport.AiError as exc:
        return {"ok": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"Lỗi: {type(exc).__name__}"}


@router.post("/ai/home-brief")
def home_brief(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """A1 — bản tin trang chủ from the user's own inbox (role-filtered) and the capacity brief."""
    facts = ai_tasks.home_facts(db, user)
    try:
        out = ai_tasks.summarize(db, user, "home_brief", "home", facts)
    except ai_platform.AiBlocked as exc:
        raise HTTPException(409, str(exc)) from exc
    except transport.AiError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {**out, "at": dt.datetime.utcnow().isoformat() + "Z"}


def pdf_ai_box(db: Session, user: User, audience: str, facts_fn) -> None:
    """Called by PDF endpoints when ?ai=1: computes the AI summary (or a one-line reason) and sets
    it for this request's HTML rendering (report_pdf.AI_BOX)."""
    from ..report_pdf import AI_BOX
    try:
        s = ai_tasks.summarize(db, user, "report_summary", audience, facts_fn())
        AI_BOX.set(ai_tasks.box_html(s))
    except (ai_platform.AiBlocked, transport.AiError) as exc:
        AI_BOX.set(ai_tasks.box_html(None, str(exc)))


__all__ = ["router", "pdf_ai_box", "is_admin"]
