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
from .shared.money import micros_to_usd, usd_to_micros

logger = logging.getLogger("vco.ai")

# Task registry: key -> (label, description, on by default)
TASKS: Dict[str, Tuple[str, str, bool]] = {
    "proposal_draft": ("Soạn tờ trình", "Viết nội dung các mục của tờ trình Mẫu A / B theo dữ liệu Opex / Capex / so sánh báo giá.", True),
    "quote_extract": ("Đọc báo giá PDF scan", "Chép bảng báo giá trong file PDF (thường là scan) thành dòng hàng để kiểm tra.", True),
    "quote_compare": ("Tóm tắt khác biệt báo giá", "Chỉ ra khác biệt nội dung / cấu hình giữa các báo giá, rủi ro và câu hỏi cho NCC.", True),
    "home_brief": ("Bản tin trang chủ", "Tóm tắt việc cần làm hôm nay từ hộp việc và bản tin capacity.", True),
    "report_summary": ("Tổng kết báo cáo PDF", "Viết phần “Tổng kết — vấn đề & quyết định” của báo cáo PDF từ số liệu đã tính.", True),
    "backup_analysis": ("Phân tích lỗi backup", "Gom job lỗi theo nguyên nhân gốc, giải thích mã lỗi lạ, đề xuất thứ tự xử lý (mức độ vẫn theo luật).", True),
    "column_mapping": ("Ánh xạ cột file nhập", "Đề xuất cột nào là tên / CPU / RAM… cho file host / VM bố cục lạ (chỉ gửi tiêu đề cột và dạng dữ liệu mẫu).", True),
    "os_classify": ("Phân loại hệ điều hành", "Đề xuất họ / phiên bản HĐH cho VM “Other / Unknown”; người xác nhận mới ghi đè.", True),
    "vm_changes": ("Tóm tắt biến động VM", "Tóm tắt VM thêm / bỏ / đổi cấu hình trong kỳ và tác động tới capacity.", True),
    "sizing_narrative": ("Nhận định sizing", "Diễn giải phương án sizing, rủi ro và câu hỏi cần làm rõ với khách hàng (số liệu giữ nguyên).", True),
    "contract_diff": ("Tóm tắt thay đổi hợp đồng", "Tóm tắt điều khoản đổi giữa 2 version hợp đồng, đánh dấu điều khoản rủi ro.", True),
    "invoice_read": ("Đọc hoá đơn", "Đọc hoá đơn PDF trong hồ sơ thanh toán để hệ thống đối chiếu số tiền / MST / số hợp đồng.", True),
    "ask_ico": ("Hỏi ICO", "Trả lời câu hỏi tiếng Việt bằng công cụ chỉ đọc, theo đúng quyền của người hỏi.", True),
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
    prev = getattr(_usage, "data", None) or {"in": 0, "out": 0}       # a tool loop makes several calls: add them up
    _usage.data = {"model": getattr(response, "model", None), "in": prev["in"] + (getattr(u, "input_tokens", 0) or 0),
                   "out": prev["out"] + (getattr(u, "output_tokens", 0) or 0)}


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
    from .shared import ai_transport as transport
    if not transport.enabled(cfg):
        return False
    return task is None or task_states(cfg).get(task, False)


def _month_start(now: Optional[dt.datetime] = None) -> dt.datetime:
    now = now or dt.datetime.utcnow()
    return dt.datetime(now.year, now.month, 1)


def _micros_col():
    """DAT-10: cost is stored only as integer micro-USD; summed as integers."""
    return func.coalesce(AiRun.cost_usd_micros, 0)


def month_cost_micros(db: Session, now: Optional[dt.datetime] = None) -> int:
    return int(db.query(func.coalesce(func.sum(_micros_col()), 0)).filter(AiRun.created_at >= _month_start(now)).scalar() or 0)


def month_cost(db: Session, now: Optional[dt.datetime] = None) -> float:
    """USD for display; budget comparisons use month_cost_micros()."""
    return micros_to_usd(month_cost_micros(db, now))


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
    spent_micros = month_cost_micros(db) + _held_micros(cfg)
    spent = micros_to_usd(spent_micros)
    if spent_micros >= usd_to_micros(budget):
        raise AiBlocked(f"Đã dùng hết hạn mức AI tháng này ({spent:.2f} / {budget} USD) — AI tạm dừng tới đầu tháng sau.".replace(".", ",", 1))
    if user is not None:
        limit = cfg.rate_per_hour or 30
        n = (db.query(func.count(AiRun.id)).filter(AiRun.username == user.username, AiRun.status.in_(("ok", "error", "cached")),
                                                   AiRun.created_at >= dt.datetime.utcnow() - dt.timedelta(hours=1)).scalar() or 0)
        if n >= limit:
            raise AiBlocked(f"Đã gọi AI {n} lần trong 1 giờ (giới hạn {limit}) — thử lại sau.")
    return cfg


# ---------------------------------------------------------------- masking
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6 = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){3,7}[0-9A-Fa-f]{1,4}(?![\w:])")      # ≥ 3 colons: not a time 10:30:00
PERSON_KEYS = {"ownerName", "deputyName", "backupName", "supportName", "supportContact", "managerName"}
_EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,}")
_names_cache: Dict[str, Any] = {"at": 0.0, "names": None}
_names_lock = threading.Lock()
NAMES_TTL = 300


def _token_key() -> bytes:
    from .config import get_settings
    st = get_settings()
    return ((st.secrets_key or "") + "|" + (st.jwt_secret or "")).encode()


class Masker:
    """Replace known names (hosts, VMs, backup clients — exact words, case-insensitive) and IPv4
    addresses by tokens `<host:…>` / `<ip:…>` before sending, and put them back in the answer.
    Tokens are an HMAC with a server secret (not reversible by the receiver); a collision gets a
    longer token, so unmasking never returns the wrong name. Lookup is per word in a set: cost is
    linear in the facts, whatever the inventory size."""

    def __init__(self, names: Iterable[str], key: Optional[bytes] = None):
        import hmac
        self._key = key if key is not None else _token_key()
        self._hmac = lambda v: hmac.new(self._key, v.encode(), hashlib.sha256).hexdigest()
        self.names = {n.lower() for n in names if n and len(n) >= 3}
        # Names a word match cannot see ("SQL Server 01", "web(prod)"): exact substrings, longest first.
        phrases = sorted((n for n in self.names if _WORD.fullmatch(n) is None), key=len, reverse=True)
        self._phrases = re.compile("|".join(re.escape(p) for p in phrases), re.I) if phrases else None
        self.back: Dict[str, str] = {}
        self._fwd: Dict[str, str] = {}

    def _token(self, kind: str, value: str) -> str:
        key = value.lower()
        if key in self._fwd:
            return self._fwd[key]
        digest = self._hmac(kind + key)
        n = 12
        tok = f"<{kind}:{digest[:n]}>"
        while tok in self.back and self.back[tok].lower() != key:
            n += 4
            tok = f"<{kind}:{digest[:n]}>"
        self._fwd[key] = tok
        self.back[tok] = value
        return tok

    def _mask_str(self, s: str) -> str:
        s = _EMAIL.sub(lambda m: self._token("email", m.group(0)), s)        # owners / support contacts: personal data
        s = _IPV4.sub(lambda m: self._token("ip", m.group(0)), s)
        s = _IPV6.sub(lambda m: self._token("ip", m.group(0)), s)
        if self._phrases is not None:
            s = self._phrases.sub(lambda m: self._token("host", m.group(0)), s)

        def word(m: "re.Match") -> str:
            w = m.group(0)
            core = w.rstrip(".-_")
            if core.lower() in self.names:
                return self._token("host", core) + w[len(core):]
            short = core.split(".")[0]
            if short != core and short.lower() in self.names:
                return self._token("host", core) + w[len(core):]
            return w
        return _WORD.sub(word, s)

    def mask(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self._mask_str(obj)
        if isinstance(obj, list):
            return [self.mask(x) for x in obj]
        if isinstance(obj, dict):        # people's names (owner, deputy, support) are personal data like their e-mails
            return {k: self._token("person", v) if k in PERSON_KEYS and isinstance(v, str) and v.strip() else self.mask(v)
                    for k, v in obj.items()}
        return obj

    def unmask(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return re.sub(r"<(?:host|ip|email|person):[0-9a-f]{12,64}>", lambda m: self.back.get(m.group(0), m.group(0)), obj)
        if isinstance(obj, list):
            return [self.unmask(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self.unmask(v) for k, v in obj.items()}
        return obj


def _known_names(db: Session) -> set:
    """Every host / VM / guest hostname / backup client name (and short host names), cached 5 min."""
    now = time.monotonic()
    with _names_lock:
        if _names_cache["names"] is not None and now - _names_cache["at"] < NAMES_TTL:
            return _names_cache["names"]
    from . import data_access
    from .models import BackupJob
    hosts, vms, _ = data_access.get_latest_combined(db, None)
    names = {h.get("host") for h in hosts} | {v.get("vm") for v in vms} | {v.get("guestHostname") for v in vms}
    names |= {c for (c,) in db.query(BackupJob.client_name).distinct() if c}
    names |= _source_names(db)
    names = {n for n in names if n}
    names |= {n.split(".")[0] for n in names if "." in n}
    out = {n.lower() for n in names if len(n) >= 3}
    with _names_lock:
        _names_cache.update(at=time.monotonic(), names=out)
    return out


def _source_names(db: Session) -> set:
    """Names of the other sources an AI fact can carry: datastores, storage arrays / volumes, Dell OME devices, IPAM
    hostnames (review C1). Each table is optional (module never synced → empty)."""
    from .hardware.models import HwDeviceState
    from .ipam.models import IpamHost
    from .models import DatastoreState, StorageArrayState, StorageVolumeState
    out: set = set()
    for col in (DatastoreState.name, StorageArrayState.name, StorageVolumeState.name, HwDeviceState.name, IpamHost.hostname):
        try:
            out |= {n for (n,) in db.query(col).distinct() if n}
        except Exception:  # noqa: BLE001 — table missing on an old install: mask what we know
            db.rollback()
    return out


def masker_for(db: Session) -> Masker:
    return Masker(_known_names(db))


# ---------------------------------------------------------------- running a task
PROMPT_VERSION = "2026-11-sa6"
# Per task, when a task's prompt changes on its own (bump it with the prompt; the cache and ai_runs follow).
PROMPT_VERSIONS: Dict[str, str] = {"ask_ico": "2026-11-c2"}
_last = threading.local()


def last_run_id() -> Optional[int]:
    """Id of the ai_runs row written last by this thread (Hỏi ICO returns it for 👍 / 👎)."""
    return getattr(_last, "run_id", None)
RUN_RETENTION_DAYS = 400
# Budget reserved by calls in flight: worst-case cost of one call, held in spend_ai_config.reserved_micros so every pod
# sees it (review C4; it was a per-process number, so N pods could overshoot by N reservations).
RESERVE_USD = 0.5
RESERVE_STALE = dt.timedelta(minutes=15)                 # longer than any call (transport deadline): leftover of a crash


def _held_micros(cfg) -> int:
    at = getattr(cfg, "reserved_at", None)
    if not at or dt.datetime.utcnow() - at > RESERVE_STALE:
        return 0
    return int(getattr(cfg, "reserved_micros", 0) or 0)


def _reserve(db: Session, task: str, micros: int) -> None:
    """Hold `micros` of the month budget in ONE conditional UPDATE (no read-then-write race between pods).
    Raises AiBlocked when the budget cannot hold it."""
    from sqlalchemy import case, update
    from .spend.models import SpendAiConfig
    from .database import SessionLocal
    cfg = config(db)
    budget = usd_to_micros(cfg.budget_usd if cfg.budget_usd is not None else 100)
    spent = month_cost_micros(db)
    now = dt.datetime.utcnow()
    held = case((SpendAiConfig.reserved_at < now - RESERVE_STALE, 0), else_=func.coalesce(SpendAiConfig.reserved_micros, 0))
    with SessionLocal() as own:                          # own transaction: never commits / rolls back the caller's work
        res = own.execute(update(SpendAiConfig).where(SpendAiConfig.id == 1, held + micros + spent <= budget)
                          # keep the start of a busy stretch: a leak (pod killed mid-call) then expires RESERVE_STALE after it,
                          # instead of every new call pushing its expiry forward all day
                          .values(reserved_micros=held + micros, reserved_at=case((held > 0, SpendAiConfig.reserved_at), else_=now))
                          .execution_options(synchronize_session=False))
        own.commit()
    if res.rowcount != 1:
        raise AiBlocked(f"Hạn mức AI tháng này không đủ cho lượt gọi “{TASKS[task][0]}” "
                        f"({micros_to_usd(spent):.2f} / {micros_to_usd(budget):.0f} USD, kể cả lượt đang chạy) — thử lại sau.".replace(".", ",", 1))


def _release(micros: int) -> None:
    """Give back a reservation, in its own session (the caller's may be rolled back)."""
    from sqlalchemy import case, update
    from .spend.models import SpendAiConfig
    from .database import SessionLocal
    try:
        cur = func.coalesce(SpendAiConfig.reserved_micros, 0)
        with SessionLocal() as own:
            own.execute(update(SpendAiConfig).where(SpendAiConfig.id == 1)
                        .values(reserved_micros=case((cur > micros, cur - micros), else_=0))
                        .execution_options(synchronize_session=False))
            own.commit()
    except Exception:  # noqa: BLE001 — a lost release expires after RESERVE_STALE
        logger.warning("Could not release the AI budget reservation", exc_info=True)


def _hash(task: str, facts: Any, cfg=None) -> str:
    extra = f"{getattr(cfg, 'model', '')}|{getattr(cfg, 'effort', '')}|{PROMPT_VERSIONS.get(task, PROMPT_VERSION)}"
    return hashlib.sha256((task + "|" + extra + "|" + json.dumps(facts, ensure_ascii=False, sort_keys=True, default=str)).encode()).hexdigest()


def record(db: Session, task: str, user: Optional[User], status: str, h: Optional[str], started: float,
            usage: Optional[Dict[str, Any]] = None, message: str = "") -> None:
    usage = usage or {"model": None, "in": 0, "out": 0}
    if status == "blocked" and db.query(AiRun.id).filter(
            AiRun.task == task, AiRun.status == "blocked", AiRun.username == (user.username if user else None),
            AiRun.created_at >= dt.datetime.utcnow() - dt.timedelta(minutes=1)).first():
        return                                             # at most one "blocked" row per user / task / minute
    cost = price(usage.get("model"), usage.get("in"), usage.get("out")) if status in ("ok", "error") else 0.0
    from .observability import current_request_id
    run = AiRun(task=task, username=user.username if user else None, model=usage.get("model"), status=status,
                input_tokens=usage.get("in") or None, output_tokens=usage.get("out") or None,
                cost_usd_micros=usd_to_micros(cost),                     # DAT-10: integer micro-USD only
                input_hash=h, latency_ms=int((time.monotonic() - started) * 1000), message=message[:300] or None,
                request_id=(current_request_id() or None) and current_request_id()[:64],
                prompt_version=PROMPT_VERSIONS.get(task, PROMPT_VERSION))
    db.add(run)
    db.commit()
    _last.run_id = run.id




def run(db: Session, user: Optional[User], task: str, call: Callable[[], Any], facts: Any = None,
        cache: bool = False, reserve: float = RESERVE_USD) -> Tuple[Any, bool]:
    """Policy → cache → call → accounting. `call()` performs the request (already masked) and
    returns the parsed result. Returns (result, from_cache). Raises AiBlocked or the transport's
    AiError (both have safe messages)."""
    from .shared.ai_transport import AiError
    started = time.monotonic()
    micros = usd_to_micros(reserve)
    try:
        cfg = check(db, task, user)
        _reserve(db, task, micros)                         # atomic on every pod
    except AiBlocked as exc:
        record(db, task, user, "blocked", None, started, message=str(exc))
        raise
    h = _hash(task, facts, cfg) if facts is not None else None
    try:
        return _run_reserved(db, user, task, call, h, cache, started, AiError)
    finally:
        _release(micros)


def _run_reserved(db, user, task, call, h, cache, started, AiError):
    if cache and h:
        row = db.get(AiCache, h)
        if row and row.created_at >= dt.datetime.utcnow() - dt.timedelta(hours=CACHE_HOURS):
            record(db, task, user, "cached", h, started)
            return json.loads(row.output), True
    _take_usage()
    try:
        result = call()
    except Exception as exc:
        safe = str(exc) if isinstance(exc, (AiError, AiBlocked)) else f"Lỗi không mong đợi ({type(exc).__name__})."
        record(db, task, user, "error", h, started, _take_usage(), message=safe)
        raise
    record(db, task, user, "ok", h, started, _take_usage())
    if cache and h:
        db.query(AiCache).filter(AiCache.created_at < dt.datetime.utcnow() - dt.timedelta(hours=CACHE_HOURS)).delete(synchronize_session=False)
        db.merge(AiCache(key=h, task=task, output=json.dumps(result, ensure_ascii=False), created_at=dt.datetime.utcnow()))
        db.commit()
    alert_if_needed(db)
    return result, False


def alert_if_needed(db: Session, post=None) -> bool:
    """Once per month, when the month's cost reaches the alert threshold (default 50 USD), tell
    the admins: Discord ops channels (the hộp việc shows it too, see inbox.system_items)."""
    from sqlalchemy import or_, update
    from .spend.models import SpendAiConfig
    cfg = config(db)
    if cfg is None:
        return False
    now = dt.datetime.utcnow()
    month = now.strftime("%Y-%m")
    db.query(AiRun).filter(AiRun.created_at < now - dt.timedelta(days=RUN_RETENTION_DAYS)).delete(synchronize_session=False)
    db.commit()
    threshold = cfg.alert_usd if cfg.alert_usd is not None else 50
    spent_micros = month_cost_micros(db, now)
    spent = micros_to_usd(spent_micros)
    if spent_micros < usd_to_micros(threshold) or cfg.alert_month == month:
        return False
    # Atomic claim: only the request that flips alert_month sends the alert.
    res = db.execute(update(SpendAiConfig).where(SpendAiConfig.id == 1, or_(SpendAiConfig.alert_month.is_(None),
                                                                           SpendAiConfig.alert_month != month))
                     .values(alert_month=month))
    db.commit()
    if res.rowcount != 1:
        return False
    try:
        from . import secrets_box
        from .spend.models import SpendNotifyChannel
        from .shared.webhook import WebhookError as NotifyError, post_json as _post_json, validate_discord_url
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
    task_micros: Dict[str, int] = {}
    for t, st, n, micros in (db.query(AiRun.task, AiRun.status, func.count(AiRun.id), func.coalesce(func.sum(_micros_col()), 0))
                             .filter(AiRun.created_at >= start).group_by(AiRun.task, AiRun.status)):
        row = by_task.setdefault(t, {"label": t, "calls": 0, "cached": 0, "blocked": 0, "errors": 0, "costUsd": 0.0})
        key = {"ok": "calls", "cached": "cached", "blocked": "blocked", "error": "errors"}[st]
        row[key] += n
        task_micros[t] = task_micros.get(t, 0) + int(micros or 0)          # integer sum; rounded once for display
    for t, micros in task_micros.items():
        by_task[t]["costUsd"] = round(micros_to_usd(micros), 4)
    recent = db.query(AiRun).order_by(AiRun.id.desc()).limit(30).all()
    return {
        "month": start.strftime("%Y-%m"), "costUsd": round(micros_to_usd(month_cost_micros(db)), 4),
        "budgetUsd": cfg.budget_usd if cfg and cfg.budget_usd is not None else 100,
        "alertUsd": cfg.alert_usd if cfg and cfg.alert_usd is not None else 50,
        "byTask": by_task,
        "recent": [{"at": r.created_at.isoformat() + "Z", "task": r.task, "user": r.username, "model": r.model, "status": r.status,
                    "inputTokens": r.input_tokens, "outputTokens": r.output_tokens,
                    "costUsd": micros_to_usd(r.cost_usd_micros or 0),
                    "latencyMs": r.latency_ms, "message": r.message} for r in recent],
    }
