"""Nền tảng AI dùng chung (super app SA4, docs/design/super-app.md §4).

Every AI task of the app goes through `run()`:
  1. policy — provider configured + "cho phép gửi ra ngoài" + the task switched on + monthly
     budget (decision: 100 USD hard limit) + calls per user per hour;
  2. masking — hostnames / IPs / VM names replaced by stable tokens before sending (decision 3),
     restored in the answer;
  3. cache — same task + same facts ⇒ stored answer, no new call;
  4. the call itself (spend/ai.py transport, structured JSON output);
  5. accounting — `ai_runs` row (task, user, model, tokens, cost; never the content) and the
     50 USD alert to admins (Discord ops channels + hộp việc), once per month.

Numbers are always computed by code; the AI writes, summarises, classifies. When the AI is off,
blocked or failing, callers fall back to the rule-based text they already have."""
import datetime as dt
import hashlib
import json
import logging
import re
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import AiCache, AiRun, User

logger = logging.getLogger("vco.ai")

# Task registry: key -> (label, description, on by default)
TASKS: Dict[str, Tuple[str, str, bool]] = {
    "proposal_draft": ("Soạn tờ trình", "Viết nội dung các mục của tờ trình Mẫu A / B theo dữ liệu Opex / Capex / so sánh báo giá.", True),
    "quote_extract": ("Đọc báo giá PDF scan", "Chép bảng báo giá trong file PDF (thường là scan) thành dòng hàng để kiểm tra.", True),
    "quote_compare": ("Tóm tắt khác biệt báo giá", "Chỉ ra khác biệt nội dung / cấu hình giữa các báo giá, rủi ro và câu hỏi cho NCC.", True),
    "home_brief": ("Bản tin trang chủ", "Tóm tắt việc cần làm hôm nay từ hộp việc và bản tin capacity.", True),
    "report_summary": ("Tổng kết báo cáo PDF", "Viết phần “Tổng kết — vấn đề & quyết định” của báo cáo PDF từ số liệu đã tính.", True),
}

# USD per million tokens (input, output) — Claude API list prices. An unknown model is priced at
# the most expensive known one so the budget is never under-counted.
PRICES: Dict[str, Tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0), "claude-opus-5": (5.0, 25.0), "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50), "claude-fable-5-2": (8.0, 40.0), "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
}
WORST_PRICE = max(PRICES.values(), key=lambda p: p[1])
CACHE_HOURS = 6


class AiBlocked(Exception):
    """The platform refused the call (off, budget, rate). Message is safe to show."""


def price(model: Optional[str], input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICES.get((model or "").split("[")[0], WORST_PRICE)
    return round((input_tokens or 0) * pin / 1e6 + (output_tokens or 0) * pout / 1e6, 6)


# ---------------------------------------------------------------- usage reported by the transport
_usage = threading.local()


def note_usage(response: Any) -> None:
    """Called by the transport (spend/ai.py) after each Messages API response."""
    u = getattr(response, "usage", None)
    _usage.data = {"model": getattr(response, "model", None), "in": getattr(u, "input_tokens", 0) or 0,
                   "out": getattr(u, "output_tokens", 0) or 0}


def _take_usage() -> Dict[str, Any]:
    d = getattr(_usage, "data", None) or {"model": None, "in": 0, "out": 0}
    _usage.data = None
    return d


# ---------------------------------------------------------------- configuration
def config(db: Session):
    from .spend.models import SpendAiConfig
    return db.get(SpendAiConfig, 1)


def task_states(cfg) -> Dict[str, bool]:
    try:
        stored = json.loads(cfg.tasks_json) if cfg is not None and cfg.tasks_json else {}
    except ValueError:
        stored = {}
    return {k: bool(stored.get(k, default)) for k, (_, _, default) in TASKS.items()}


def enabled(cfg, task: Optional[str] = None) -> bool:
    from .spend import ai as transport
    if not transport.enabled(cfg):
        return False
    return task is None or task_states(cfg).get(task, False)


def _month_start(now: Optional[dt.datetime] = None) -> dt.datetime:
    now = now or dt.datetime.utcnow()
    return dt.datetime(now.year, now.month, 1)


def month_cost(db: Session, now: Optional[dt.datetime] = None) -> float:
    return float(db.query(func.coalesce(func.sum(AiRun.cost_usd), 0.0)).filter(AiRun.created_at >= _month_start(now)).scalar() or 0.0)


def check(db: Session, task: str, user: Optional[User]):
    """Raise AiBlocked unless the task may run now for this user. Returns the config."""
    cfg = config(db)
    if task not in TASKS:
        raise AiBlocked("Tác vụ AI không tồn tại.")
    if not enabled(cfg):
        raise AiBlocked("AI chưa bật hoặc chưa cho phép gửi dữ liệu ra ngoài (Cài đặt → AI & MCP).")
    if not task_states(cfg)[task]:
        raise AiBlocked(f"Tác vụ “{TASKS[task][0]}” đang tắt (Cài đặt → AI & MCP).")
    budget = cfg.budget_usd if cfg.budget_usd is not None else 100
    spent = month_cost(db)
    if spent >= budget:
        raise AiBlocked(f"Đã dùng hết hạn mức AI tháng này ({spent:.2f} / {budget} USD) — AI tạm dừng tới đầu tháng sau.".replace(".", ",", 1))
    if user is not None:
        limit = cfg.rate_per_hour or 30
        n = (db.query(func.count(AiRun.id)).filter(AiRun.username == user.username, AiRun.status.in_(("ok", "error")),
                                                   AiRun.created_at >= dt.datetime.utcnow() - dt.timedelta(hours=1)).scalar() or 0)
        if n >= limit:
            raise AiBlocked(f"Đã gọi AI {n} lần trong 1 giờ (giới hạn {limit}) — thử lại sau.")
    return cfg


# ---------------------------------------------------------------- masking
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


class Masker:
    """Replace known names (hosts, VMs, backup clients) and IPv4 addresses by stable tokens
    `<host:ab12cd>` before sending, and put them back in the answer."""

    def __init__(self, names: Iterable[str]):
        self.forward: Dict[str, str] = {}
        self.back: Dict[str, str] = {}
        uniq = sorted({n for n in names if n and len(n) >= 3}, key=len, reverse=True)
        for n in uniq:
            tok = f"<host:{hashlib.sha1(n.lower().encode()).hexdigest()[:6]}>"
            self.forward[n] = tok
            self.back[tok] = n
        self._re = re.compile("|".join(re.escape(n) for n in uniq), re.I) if uniq else None
        self._lower = {n.lower(): t for n, t in self.forward.items()}

    def _ip(self, m: "re.Match") -> str:
        ip = m.group(0)
        tok = f"<ip:{hashlib.sha1(ip.encode()).hexdigest()[:6]}>"
        self.back[tok] = ip
        return tok

    def mask(self, obj: Any) -> Any:
        if isinstance(obj, str):
            s = self._re.sub(lambda m: self._lower[m.group(0).lower()], obj) if self._re else obj
            return _IPV4.sub(self._ip, s)
        if isinstance(obj, list):
            return [self.mask(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self.mask(v) for k, v in obj.items()}
        return obj

    def unmask(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return re.sub(r"<(?:host|ip):[0-9a-f]{6}>", lambda m: self.back.get(m.group(0), m.group(0)), obj)
        if isinstance(obj, list):
            return [self.unmask(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self.unmask(v) for k, v in obj.items()}
        return obj


def masker_for(db: Session) -> Masker:
    """Names from the latest inventory snapshot and backup clients."""
    from . import data_access
    from .models import BackupJob
    hosts, vms, _ = data_access.get_latest_combined(db, None)
    names = [h.get("host") for h in hosts] + [v.get("vm") for v in vms] + [v.get("guestHostname") for v in vms]
    names += [c for (c,) in db.query(BackupJob.client_name).distinct().limit(20000) if c]
    short = [n.split(".")[0] for n in names if n and "." in n]
    return Masker([n for n in names + short if n])


# ---------------------------------------------------------------- running a task
def _hash(task: str, facts: Any) -> str:
    return hashlib.sha256((task + "|" + json.dumps(facts, ensure_ascii=False, sort_keys=True, default=str)).encode()).hexdigest()


def _record(db: Session, task: str, user: Optional[User], status: str, h: Optional[str], started: float,
            usage: Optional[Dict[str, Any]] = None, message: str = "") -> None:
    usage = usage or {"model": None, "in": 0, "out": 0}
    db.add(AiRun(task=task, username=user.username if user else None, model=usage.get("model"), status=status,
                 input_tokens=usage.get("in") or None, output_tokens=usage.get("out") or None,
                 cost_usd=price(usage.get("model"), usage.get("in"), usage.get("out")) if status in ("ok", "error") else 0.0,
                 input_hash=h, latency_ms=int((time.monotonic() - started) * 1000), message=message[:300] or None))
    db.commit()


def run(db: Session, user: Optional[User], task: str, call: Callable[[], Any], facts: Any = None,
        cache: bool = False) -> Tuple[Any, bool]:
    """Policy → cache → call → accounting. `call()` performs the request (already masked) and
    returns the parsed result. Returns (result, from_cache). Raises AiBlocked or the transport's
    AiError (both have safe messages)."""
    started = time.monotonic()
    h = _hash(task, facts) if facts is not None else None
    try:
        check(db, task, user)
    except AiBlocked as exc:
        _record(db, task, user, "blocked", h, started, message=str(exc))
        raise
    if cache and h:
        row = db.get(AiCache, h)
        if row and row.created_at >= dt.datetime.utcnow() - dt.timedelta(hours=CACHE_HOURS):
            _record(db, task, user, "cached", h, started)
            return json.loads(row.output), True
    _take_usage()
    try:
        result = call()
    except Exception as exc:
        _record(db, task, user, "error", h, started, _take_usage(), message=str(exc))
        raise
    _record(db, task, user, "ok", h, started, _take_usage())
    if cache and h:
        db.merge(AiCache(key=h, task=task, output=json.dumps(result, ensure_ascii=False), created_at=dt.datetime.utcnow()))
        db.commit()
    alert_if_needed(db)
    return result, False


def alert_if_needed(db: Session, post=None) -> bool:
    """Once per month, when the month's cost reaches the alert threshold (default 50 USD), tell
    the admins: Discord ops channels (the hộp việc shows it too, see inbox.system_items)."""
    cfg = config(db)
    if cfg is None:
        return False
    now = dt.datetime.utcnow()
    month = now.strftime("%Y-%m")
    threshold = cfg.alert_usd if cfg.alert_usd is not None else 50
    spent = month_cost(db, now)
    if spent < threshold or cfg.alert_month == month:
        return False
    cfg.alert_month = month
    db.commit()
    try:
        from . import secrets_box
        from .spend.models import SpendNotifyChannel
        from .spend.notify import NotifyError, _post_json, validate_discord_url
        post = post or _post_json
        text = (f"**ICO — chi phí AI tháng {now:%m/%Y}: {spent:.2f} USD** (ngưỡng cảnh báo {threshold} USD, "
                f"hạn mức {cfg.budget_usd or 100} USD). Xem chi tiết ở Cài đặt → AI & MCP.")
        for ch in db.query(SpendNotifyChannel).filter(SpendNotifyChannel.kind == "discord", SpendNotifyChannel.enabled.is_(True),
                                                      SpendNotifyChannel.ops_alerts.is_(True)):
            try:
                post(validate_discord_url(secrets_box.decrypt(ch.webhook_enc)),
                     {"content": text, "allowed_mentions": {"parse": []}, "username": "ICO cảnh báo"})
            except (NotifyError, secrets_box.SecretUnreadable, ValueError) as exc:
                logger.warning("AI cost alert to channel %s failed: %s", ch.id, exc)
    except Exception:  # noqa: BLE001 — the alert must never break the AI call
        logger.exception("AI cost alert failed")
    return True


def usage_report(db: Session) -> Dict[str, Any]:
    cfg = config(db)
    start = _month_start()
    by_task = {t: {"label": TASKS.get(t, (t,))[0], "calls": 0, "cached": 0, "blocked": 0, "errors": 0, "costUsd": 0.0}
               for t in TASKS}
    for t, st, n, cost in (db.query(AiRun.task, AiRun.status, func.count(AiRun.id), func.coalesce(func.sum(AiRun.cost_usd), 0.0))
                           .filter(AiRun.created_at >= start).group_by(AiRun.task, AiRun.status)):
        row = by_task.setdefault(t, {"label": t, "calls": 0, "cached": 0, "blocked": 0, "errors": 0, "costUsd": 0.0})
        key = {"ok": "calls", "cached": "cached", "blocked": "blocked", "error": "errors"}[st]
        row[key] += n
        row["costUsd"] = round(row["costUsd"] + float(cost), 4)
    recent = db.query(AiRun).order_by(AiRun.id.desc()).limit(30).all()
    return {
        "month": start.strftime("%Y-%m"), "costUsd": round(month_cost(db), 4),
        "budgetUsd": cfg.budget_usd if cfg and cfg.budget_usd is not None else 100,
        "alertUsd": cfg.alert_usd if cfg and cfg.alert_usd is not None else 50,
        "byTask": by_task,
        "recent": [{"at": r.created_at.isoformat() + "Z", "task": r.task, "user": r.username, "model": r.model, "status": r.status,
                    "inputTokens": r.input_tokens, "outputTokens": r.output_tokens, "costUsd": r.cost_usd,
                    "latencyMs": r.latency_ms, "message": r.message} for r in recent],
    }
