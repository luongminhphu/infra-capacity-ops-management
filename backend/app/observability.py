"""The app's own logs and performance numbers, kept so an admin's AI client can read them
over MCP (see mcp_server.py) — no external log stack needed.

* Logs: every record from the root logger goes into an in-memory ring (INFO+, since start);
  WARNING+ is also persisted to `app_logs` (survives restarts, kept OBS_RETENTION_DAYS).
* Requests: per route template, 5-minute buckets with count, 4xx/5xx, latency histogram,
  time spent in the database and number of queries → `perf_buckets`.
* Slowest recent requests and SQL statements (normalised, never with parameters) in memory.

Secrets are redacted before anything is stored or returned: values of every *PASSWORD*,
*SECRET*, *TOKEN*, *API_KEY* environment variable, JWTs, bearer tokens, `password=` style
pairs and credentials inside URLs.
"""
import bisect
import collections
import contextvars
import datetime as dt
import hashlib
import json
import logging
import os
import re
import threading
import time
import traceback as tb_mod
from typing import Any, Dict, List, Optional, Tuple

from .config import get_settings

LATENCY_BUCKETS_MS = [5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000]  # + overflow
BUCKET_MINUTES = 5
STARTED_AT = dt.datetime.utcnow()
POD = (os.environ.get("HOSTNAME") or __import__("socket").gethostname() or "?")[:64]   # Kubernetes sets HOSTNAME = pod name
JOB_RUN_RETENTION_DAYS = 90
# Library loggers that write a line per font / glyph table on every PDF: kept at WARNING (or ERROR) so the in-memory
# ring and app_logs keep the app's own records. The HarfBuzz notice is logged once per process instead of per PDF.
QUIET_LOGGERS = {"fontTools": logging.WARNING, "fontTools.subset": logging.WARNING, "fontTools.ttLib": logging.WARNING}
_ONCE_PREFIXES = ("Using fontTools instead of HarfBuzz-Subset",)

_lock = threading.Lock()
_ring: "collections.deque[Dict[str, Any]]" = collections.deque(maxlen=5000)
_pending_logs: List[Dict[str, Any]] = []
MAX_PENDING_LOGS = 10000           # WARNING+ entries waiting for the next flush (bounded)
_buckets: Dict[Tuple[dt.datetime, str, str], Dict[str, Any]] = {}
_slow_requests: "collections.deque[Dict[str, Any]]" = collections.deque(maxlen=200)
_queries: Dict[str, Dict[str, Any]] = {}
_request_ctx: contextvars.ContextVar = contextvars.ContextVar("ico_request_ctx", default=None)
_internal: contextvars.ContextVar = contextvars.ContextVar("ico_obs_internal", default=False)


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------
_SECRET_ENV = re.compile(r"(PASSWORD|SECRET|TOKEN|API_KEY|APIKEY|PRIVATE_KEY)", re.I)
_PATTERNS = [
    (re.compile(r"eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}"), "<jwt>"),
    (re.compile(r"\bico_mcp_[\w-]+"), "<ico-token>"),
    (re.compile(r"(?i)(bearer\s+)[\w\-.~+/=]{8,}"), r"\1<redacted>"),
    (re.compile(r"(?i)((?:password|passwd|pwd|secret|api[_-]?key|token|client_secret)[\"']?\s*[:=]\s*[\"']?)[^\s\"',;&}]+"),
     r"\1<redacted>"),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^:/\s@]+:)[^@\s/]+@"), r"\1<redacted>@"),
]
_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_HOST = re.compile(r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.){2,}"
                   r"(?:com|net|org|vn|local|lan|internal|intra|corp|io|gov|edu|biz|info|cloud|site)\b", re.I)
_PUBLIC_HOSTS = {"login.microsoftonline.com", "graph.microsoft.com"}
_secret_values: Optional[List[str]] = None


def _secrets() -> List[str]:
    global _secret_values
    if _secret_values is None:
        s = get_settings()
        vals = {v for k, v in os.environ.items() if _SECRET_ENV.search(k) and v and len(v) >= 6}
        vals |= {v for v in (s.jwt_secret, s.admin_password, s.entra_client_secret) if v and len(v) >= 6}
        _secret_values = sorted(vals, key=len, reverse=True)
    return _secret_values


def register_secret(value: Optional[str]) -> None:
    """Secrets that are not environment variables (e.g. passwords saved in Settings)."""
    global _secret_values
    if value and len(value) >= 6 and value not in _secrets():
        with _lock:
            _secret_values = sorted(set(_secrets()) | {value}, key=len, reverse=True)


def redact(text: Optional[str]) -> str:
    if not text:
        return text or ""
    for v in _secrets():
        if v in text:
            text = text.replace(v, "<redacted>")
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


def _host_tag(m: "re.Match") -> str:
    h = m.group(0)
    if h.lower() in _PUBLIC_HOSTS:
        return h
    return "<host:" + hashlib.sha1(h.lower().encode()).hexdigest()[:6] + ">"


def mask_infra(text: Optional[str]) -> str:
    """For output to the AI: hide internal host names / IPs but keep them distinguishable."""
    from .mcp_config import current          # lazy: settings saved in the UI, environment otherwise
    if not text or not current().mask_hosts:
        return text or ""
    text = _IP.sub(lambda m: "<ip:" + hashlib.sha1(m.group(0).encode()).hexdigest()[:6] + ">", text)
    return _HOST.sub(_host_tag, text)


_NUM = re.compile(r"\b\d+(?:\.\d+)?\b")
_HEX = re.compile(r"\b[0-9a-f]{8,}\b|\b[0-9a-f]{8}-[0-9a-f-]{27}\b", re.I)
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")


def fingerprint(logger_name: str, exc_type: Optional[str], message: str) -> str:
    norm = _QUOTED.sub("'?'", _HEX.sub("#", _NUM.sub("#", message[:300])))
    return hashlib.sha1(f"{logger_name}|{exc_type or ''}|{norm}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Log capture
# ---------------------------------------------------------------------------
class _OnceFilter(logging.Filter):
    """Let a known repetitive library notice through once per process (it stays visible, it stops flooding)."""

    def __init__(self):
        super().__init__()
        self.seen: set = set()

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for p in _ONCE_PREFIXES:
            if msg.startswith(p):
                if p in self.seen:
                    return False
                self.seen.add(p)
        return True


def quiet_libraries() -> None:
    for name, level in QUIET_LOGGERS.items():
        logging.getLogger(name).setLevel(level)
    wp = logging.getLogger("weasyprint")
    if not any(isinstance(f, _OnceFilter) for f in wp.filters):
        wp.addFilter(_OnceFilter())


class CaptureHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("ico.obs"):
            return
        try:
            message = redact(record.getMessage())
            exc_type = trace = None
            if record.exc_info and record.exc_info[0]:
                exc_type = record.exc_info[0].__name__
                trace = redact("".join(tb_mod.format_exception(*record.exc_info))[-6000:])
            entry = {"at": dt.datetime.utcfromtimestamp(record.created), "level": record.levelname,
                     "logger": record.name, "message": message[:4000], "exc_type": exc_type, "traceback": trace,
                     "request_id": current_request_id(), "pod": POD}
            with _lock:
                _ring.append(entry)
                if record.levelno >= logging.WARNING:
                    entry["fingerprint"] = fingerprint(record.name, exc_type, message)
                    if len(_pending_logs) < MAX_PENDING_LOGS:
                        _pending_logs.append(entry)
        except Exception:  # noqa: BLE001 — logging must never break the app
            pass


def recent_logs(min_level: int, since: dt.datetime, logger_prefix: Optional[str], contains: Optional[str],
                limit: int, request_id: Optional[str] = None) -> List[Dict[str, Any]]:
    with _lock:
        items = list(_ring)
    out = []
    for e in reversed(items):
        if e["at"] < since or logging.getLevelName(e["level"]) < min_level:
            continue
        if request_id and e.get("request_id") != request_id:
            continue
        if logger_prefix and not e["logger"].startswith(logger_prefix):
            continue
        if contains and contains.lower() not in e["message"].lower():
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Request timing (pure ASGI middleware — sees the matched route template)
# ---------------------------------------------------------------------------
def _bucket_start(t: dt.datetime) -> dt.datetime:
    return t.replace(minute=t.minute - t.minute % BUCKET_MINUTES, second=0, microsecond=0)


def record_request(method: str, route: str, status: int, ms: float, db_ms: float, db_queries: int,
                   at: Optional[dt.datetime] = None) -> None:
    at = at or dt.datetime.utcnow()
    key = (_bucket_start(at), method, route)
    with _lock:
        b = _buckets.get(key)
        if b is None:
            b = _buckets[key] = {"count": 0, "errors_5xx": 0, "errors_4xx": 0, "total_ms": 0.0, "max_ms": 0.0,
                                 "db_ms": 0.0, "db_queries": 0, "hist": [0] * (len(LATENCY_BUCKETS_MS) + 1)}
        b["count"] += 1
        b["errors_5xx"] += status >= 500
        b["errors_4xx"] += 400 <= status < 500
        b["total_ms"] += ms
        b["max_ms"] = max(b["max_ms"], ms)
        b["db_ms"] += db_ms
        b["db_queries"] += db_queries
        b["hist"][bisect.bisect_left(LATENCY_BUCKETS_MS, ms)] += 1
        if ms >= get_settings().obs_slow_request_ms or db_queries >= 100:
            _slow_requests.append({"at": at, "method": method, "route": route, "status": status, "ms": round(ms, 1),
                                   "dbMs": round(db_ms, 1), "dbQueries": db_queries})


_RID_OK = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def request_id_from(scope) -> str:
    """Reuse a well-formed inbound X-Request-ID (proxy / App Proxy correlation), else a new one.
    Anything else is ignored so a client cannot inject text into the logs."""
    for k, v in scope.get("headers") or []:
        if k == b"x-request-id":
            try:
                cand = v.decode("ascii")
            except UnicodeDecodeError:
                break
            if _RID_OK.match(cand):
                return cand
            break
    return os.urandom(8).hex()


def current_request_id() -> Optional[str]:
    ctx = _request_ctx.get()
    return ctx.get("request_id") if ctx else None


class job_context:
    """Tags logs of a background job with "job-<name>-<id>" (audit OBS-02 / BEQ-11). The context
    is copied into asyncio.to_thread workers, so their log lines carry the same id."""

    def __init__(self, name: str):
        self.id = f"job-{name}-{os.urandom(3).hex()}"

    def __enter__(self):
        self._token = _request_ctx.set({"db_ms": 0.0, "db_queries": 0, "request_id": self.id})
        return self

    def __exit__(self, *exc):
        _request_ctx.reset(self._token)
        return False


class RequestIdLogFilter(logging.Filter):
    """Adds `request_id` to every log record ("-" outside a request) for the log format."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = current_request_id() or "-"
        return True


class TimingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not (scope["path"].startswith("/api/") or scope["path"].startswith("/mcp")):
            return await self.app(scope, receive, send)
        rid = request_id_from(scope)
        ctx = {"db_ms": 0.0, "db_queries": 0, "request_id": rid}
        token = _request_ctx.set(ctx)
        status_holder = {"status": 500}
        start = time.perf_counter()

        async def _send(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + [(b"x-request-id", rid.encode("ascii"))]
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            ms = (time.perf_counter() - start) * 1000
            route = scope.get("route")
            template = getattr(route, "path_format", None) or getattr(route, "path", None) or "(unmatched)"
            record_request(scope.get("method", "GET"), template, status_holder["status"], ms, ctx["db_ms"], ctx["db_queries"])
            _request_ctx.reset(token)


# ---------------------------------------------------------------------------
# SQL timing
# ---------------------------------------------------------------------------
_SQL_LITERAL = re.compile(r"'(?:[^']|'')*'")
_SQL_IN = re.compile(r"\(\s*(?:%\(\w+\)s|\?|:\w+)(?:\s*,\s*(?:%\(\w+\)s|\?|:\w+))+\s*\)")


def normalize_sql(sql: str) -> str:
    s = _SQL_IN.sub("(…)", _SQL_LITERAL.sub("'?'", " ".join(sql.split())))
    return s[:600]


def install_sql_timing(engine) -> None:
    from sqlalchemy import event

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        conn.info.setdefault("ico_t", []).append(time.perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        stack = conn.info.get("ico_t")
        if not stack:
            return
        ms = (time.perf_counter() - stack.pop()) * 1000
        if _internal.get():
            return
        ctx = _request_ctx.get()
        if ctx is not None:
            ctx["db_ms"] += ms
            ctx["db_queries"] += 1
        if ms < get_settings().obs_slow_query_ms:
            return
        key = normalize_sql(statement)
        now = dt.datetime.utcnow()
        with _lock:
            q = _queries.get(key)
            if q is None:
                if len(_queries) >= 500:
                    return
                q = _queries[key] = {"sql": key, "count": 0, "totalMs": 0.0, "maxMs": 0.0, "firstSeen": now}
            q["count"] += 1
            q["totalMs"] += ms
            q["maxMs"] = max(q["maxMs"], ms)
            q["lastSeen"] = now


def slow_queries(limit: int) -> List[Dict[str, Any]]:
    with _lock:
        items = [dict(q) for q in _queries.values()]
    items.sort(key=lambda q: q["totalMs"], reverse=True)
    for q in items:
        q["avgMs"] = round(q["totalMs"] / q["count"], 1)
        q["totalMs"], q["maxMs"] = round(q["totalMs"], 1), round(q["maxMs"], 1)
    return items[:limit]


def slow_requests(limit: int) -> List[Dict[str, Any]]:
    with _lock:
        items = list(_slow_requests)
    return sorted(items, key=lambda r: r["ms"], reverse=True)[:limit]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def flush(force_all_buckets: bool = False) -> None:
    """Write pending WARNING+ logs and finished 5-minute buckets to the database."""
    from .database import SessionLocal
    from .models import AppLog, PerfBucket

    s = get_settings()
    current = _bucket_start(dt.datetime.utcnow())
    with _lock:
        logs = _pending_logs[:]
        _pending_logs.clear()
        done = {k: v for k, v in _buckets.items() if force_all_buckets or k[0] < current}
        for k in done:
            del _buckets[k]
    if not logs and not done:
        return
    token = _internal.set(True)
    db = SessionLocal()
    try:
        for e in logs:
            db.add(AppLog(at=e["at"], level=e["level"], logger=e["logger"][:128], message=e["message"],
                          exc_type=(e["exc_type"] or None) and e["exc_type"][:128], traceback=e["traceback"],
                          fingerprint=e["fingerprint"], app_version=s.app_version,
                          request_id=(e.get("request_id") or None) and e["request_id"][:64], pod=e.get("pod")))
        for (start, method, route), b in done.items():
            # with_for_update: two pods merge into the same 5-minute row without losing counts
            row = (db.query(PerfBucket).filter_by(bucket_start=start, method=method, route=route[:255])
                   .with_for_update().first())
            if row is None:
                row = PerfBucket(bucket_start=start, method=method, route=route[:255], count=0, errors_5xx=0,
                                 errors_4xx=0, total_ms=0, max_ms=0, db_ms=0, db_queries=0,
                                 histogram=json.dumps([0] * (len(LATENCY_BUCKETS_MS) + 1)), app_version=s.app_version)
                db.add(row)
            hist = [a + c for a, c in zip(json.loads(row.histogram), b["hist"])]
            row.count += b["count"]
            row.errors_5xx += b["errors_5xx"]
            row.errors_4xx += b["errors_4xx"]
            row.total_ms += b["total_ms"]
            row.max_ms = max(row.max_ms, b["max_ms"])
            row.db_ms += b["db_ms"]
            row.db_queries += b["db_queries"]
            row.histogram = json.dumps(hist)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        # Put the batch back (oldest first, same 10 000 cap) so a DB blip does not lose exactly the
        # WARNING+ logs needed to investigate it; the next flush retries.
        with _lock:
            room = max(0, MAX_PENDING_LOGS - len(_pending_logs))
            dropped = max(0, len(logs) - room)
            _pending_logs[:0] = logs[dropped:]
            for k, b in done.items():
                _buckets.setdefault(k, b)
        logging.getLogger("ico.obs").exception("Could not persist logs / performance buckets; %d logs kept for retry%s",
                                               len(logs) - dropped, f", {dropped} dropped (queue full)" if dropped else "")
    finally:
        db.close()
        _internal.reset(token)


def purge_old() -> None:
    from .database import SessionLocal
    from .models import AppLog, AuditLog, JobRun, PerfBucket

    s = get_settings()
    cutoff = dt.datetime.utcnow() - dt.timedelta(days=s.obs_retention_days)
    token = _internal.set(True)
    db = SessionLocal()
    try:
        db.query(AppLog).filter(AppLog.at < cutoff).delete(synchronize_session=False)
        db.query(JobRun).filter(JobRun.started_at < dt.datetime.utcnow() - dt.timedelta(days=JOB_RUN_RETENTION_DAYS)) \
            .delete(synchronize_session=False)
        db.query(PerfBucket).filter(PerfBucket.bucket_start < cutoff).delete(synchronize_session=False)
        db.query(AuditLog).filter(AuditLog.at < dt.datetime.utcnow() - dt.timedelta(days=365)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
        _internal.reset(token)


async def flush_loop() -> None:
    import asyncio
    last_purge = 0.0
    while True:
        await asyncio.sleep(15)
        try:
            await asyncio.to_thread(flush)
            if time.time() - last_purge > 6 * 3600:
                await asyncio.to_thread(purge_old)
                last_purge = time.time()
        except Exception:  # noqa: BLE001
            logging.getLogger("ico.obs").exception("Observability flush failed")


# ---------------------------------------------------------------------------
# Reading performance back
# ---------------------------------------------------------------------------
def percentile(hist: List[int], q: float, max_ms: float) -> Optional[float]:
    """q-quantile, interpolated linearly inside the latency bucket that holds it (bounds: previous bucket edge →
    this edge, capped by the slowest request seen). The old "upper bound of the bucket" read 5 000 ms for anything
    between 2,5 and 5 s; the histogram itself is unchanged (pods of both versions keep merging the same rows)."""
    total = sum(hist)
    if not total:
        return None
    rank, acc = q * total, 0
    for i, c in enumerate(hist):
        if c and acc + c >= rank:
            lo = float(LATENCY_BUCKETS_MS[i - 1]) if i > 0 else 0.0
            hi = float(LATENCY_BUCKETS_MS[i]) if i < len(LATENCY_BUCKETS_MS) else float(max_ms)
            hi = max(lo, min(hi, float(max_ms)))
            return round(lo + (hi - lo) * (rank - acc) / c, 1)
        acc += c
    return round(float(max_ms), 1)


def buckets_since(db, since: dt.datetime) -> List[Dict[str, Any]]:
    """Persisted buckets + the ones still in memory, as plain dicts."""
    from .models import PerfBucket

    rows = [{"start": r.bucket_start, "method": r.method, "route": r.route, "count": r.count,
             "errors_5xx": r.errors_5xx, "errors_4xx": r.errors_4xx, "total_ms": r.total_ms, "max_ms": r.max_ms,
             "db_ms": r.db_ms, "db_queries": r.db_queries, "hist": json.loads(r.histogram), "version": r.app_version}
            for r in db.query(PerfBucket).filter(PerfBucket.bucket_start >= _bucket_start(since)).all()]
    version = get_settings().app_version
    with _lock:
        for (start, method, route), b in _buckets.items():
            if start >= _bucket_start(since):
                rows.append({"start": start, "method": method, "route": route, "count": b["count"],
                             "errors_5xx": b["errors_5xx"], "errors_4xx": b["errors_4xx"], "total_ms": b["total_ms"],
                             "max_ms": b["max_ms"], "db_ms": b["db_ms"], "db_queries": b["db_queries"],
                             "hist": list(b["hist"]), "version": version})
    return rows


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    count = sum(r["count"] for r in rows)
    hist = [0] * (len(LATENCY_BUCKETS_MS) + 1)
    for r in rows:
        hist = [a + b for a, b in zip(hist, r["hist"])]
    mx = max((r["max_ms"] for r in rows), default=0.0)
    e5 = sum(r["errors_5xx"] for r in rows)
    return {
        "requests": count,
        "errors5xx": e5, "errors4xx": sum(r["errors_4xx"] for r in rows),
        "errorRatePct": round(100 * e5 / count, 2) if count else 0.0,
        "avgMs": round(sum(r["total_ms"] for r in rows) / count, 1) if count else None,
        "p50Ms": percentile(hist, 0.5, mx), "p95Ms": percentile(hist, 0.95, mx), "p99Ms": percentile(hist, 0.99, mx),
        "maxMs": round(mx, 1),
        "avgDbMs": round(sum(r["db_ms"] for r in rows) / count, 1) if count else None,
        "avgDbQueries": round(sum(r["db_queries"] for r in rows) / count, 1) if count else None,
    }


def process_stats() -> Dict[str, Any]:
    import resource
    rss_kb = None
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
    except OSError:
        pass
    ru = resource.getrusage(resource.RUSAGE_SELF)
    return {"rssMb": round(rss_kb / 1024, 1) if rss_kb else None, "maxRssMb": round(ru.ru_maxrss / 1024, 1),
            "cpuUserS": round(ru.ru_utime, 1), "cpuSystemS": round(ru.ru_stime, 1),
            "threads": threading.active_count()}


def install(app_engine) -> None:
    root = logging.getLogger()
    if not any(isinstance(h, CaptureHandler) for h in root.handlers):
        h = CaptureHandler(level=logging.INFO)
        root.addHandler(h)
    install_sql_timing(app_engine)
