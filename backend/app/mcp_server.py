"""Read-only MCP server (Model Context Protocol, Streamable HTTP transport, stateless JSON
responses) at /mcp — lets an admin's AI client (e.g. Claude Code) read the app's own logs and
performance numbers and propose fixes as pull requests. It cannot change anything.

Access: `Authorization: Bearer ico_mcp_…` — a personal token created by an admin in
Settings → AI / MCP. The owner must still be an admin when the token is used.
Scope: app logs, errors, request / SQL timings, collector run durations. No infrastructure
inventory; internal host names and IPs in log text are masked (MCP_MASK_HOSTS).
"""
import datetime as dt
import hashlib
import json
import logging
import platform
import time
from collections import defaultdict, deque
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from . import mcp_extra, observability as obs
from .config import get_settings
from .database import get_db
from .models import ApiToken, AppLog, AuditLog, BackupSyncRun, JobRun, SnapshotRun, User
from .security import is_admin, local_login_allowed

logger = logging.getLogger("vco.mcp")
router = APIRouter(tags=["mcp"])

SUPPORTED_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26"]
TOKEN_PREFIX = "ico_mcp_"
MAX_TEXT = 120_000
_rate: Dict[int, deque] = defaultdict(deque)

INSTRUCTIONS = (
    "Infra Capacity Ops (ICO) — read-only access to this web app's own logs and performance. "
    "Use it to diagnose errors and slowness and to propose code or configuration changes with evidence "
    "(tool output, timestamps, app version). Do not claim a change is deployed: fixes go through a pull "
    "request in the ICO repository, reviewed by a human, then a manual redeploy on Rancher. "
    "Times are UTC. Host names and IPs may be masked as <host:xxxxxx>/<ip:xxxxxx> (same value → same tag). "
    "Start with ico_app_overview, ico_config_check and ico_source_status; follow ids with ico_trace_request. "
    "Several pods may run: INFO logs, slow requests and slow SQL are per pod (field pod), the rest is shared."
)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
def _since(minutes: Any, default: int, maximum: int = 60 * 24 * 30) -> dt.datetime:
    try:
        m = int(minutes) if minutes is not None else default
    except (TypeError, ValueError):
        m = default
    return dt.datetime.utcnow() - dt.timedelta(minutes=max(1, min(m, maximum)))


def _limit(v: Any, default: int, maximum: int = 500) -> int:
    try:
        return max(1, min(int(v), maximum))
    except (TypeError, ValueError):
        return default


def _iso(t: Optional[dt.datetime]) -> Optional[str]:
    return t.replace(microsecond=0).isoformat() + "Z" if t else None


def _level(name: Any) -> int:
    lv = logging.getLevelName(str(name or "WARNING").upper())
    return lv if isinstance(lv, int) else logging.WARNING


def _like(text: str) -> str:
    """Literal substring for LIKE / ILIKE ('%' and '_' typed by the client are not wildcards)."""
    return "%" + str(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _log_out(e: Dict[str, Any], with_trace: bool) -> Dict[str, Any]:
    out = {"at": _iso(e["at"]), "level": e["level"], "logger": e["logger"], "message": obs.mask_infra(e["message"])}
    if e.get("request_id"):
        out["requestId"] = e["request_id"]
    if e.get("pod"):
        out["pod"] = e["pod"]
    if e.get("exc_type"):
        out["exception"] = e["exc_type"]
    if with_trace and e.get("traceback"):
        out["traceback"] = obs.mask_infra(e["traceback"])[-3000:]
    return out


def tool_app_overview(db: Session, args: Dict[str, Any]) -> Dict[str, Any]:
    s = get_settings()
    t0 = time.perf_counter()
    db.execute(text("SELECT 1"))
    db_ping = round((time.perf_counter() - t0) * 1000, 1)
    hour, day = _since(60, 60), _since(1440, 1440)
    obs.flush()
    counts = {lvl: n for lvl, n in db.query(AppLog.level, func.count(AppLog.id)).filter(AppLog.at >= day).group_by(AppLog.level)}
    counts_1h = {lvl: n for lvl, n in db.query(AppLog.level, func.count(AppLog.id)).filter(AppLog.at >= hour).group_by(AppLog.level)}
    pool = db.get_bind().pool
    last_vc = db.query(SnapshotRun).filter(SnapshotRun.source == "live").order_by(SnapshotRun.started_at.desc()).first()
    last_nb = db.query(BackupSyncRun).order_by(BackupSyncRun.started_at.desc()).first()
    return {
        "appVersion": s.app_version, "startedAt": _iso(obs.STARTED_AT),
        "uptimeHours": round((dt.datetime.utcnow() - obs.STARTED_AT).total_seconds() / 3600, 2),
        "python": platform.python_version(), "process": obs.process_stats(),
        "database": {"dialect": db.get_bind().dialect.name, "pingMs": db_ping,
                     "pool": pool.status() if hasattr(pool, "status") else None},
        "scheduler": {"enabled": s.enable_scheduler, "pollIntervalMinutes": s.poll_interval_minutes},
        "logs": {"last1h": counts_1h, "last24h": counts},
        "requestsLast1h": obs.summarize(obs.buckets_since(db, hour)),
        "requestsLast24h": obs.summarize(obs.buckets_since(db, day)),
        "pod": obs.POD,
        "lastVcenterSync": _run_out(last_vc, "vcenter") if last_vc else None,
        "lastBackupSync": _run_out(last_nb, "backup") if last_nb else None,
    }


def tool_search_logs(db: Session, args: Dict[str, Any]) -> Dict[str, Any]:
    level = _level(args.get("level"))
    since = _since(args.get("since_minutes"), 60)
    limit = _limit(args.get("limit"), 100)
    offset = int(args.get("offset") or 0)
    prefix, contains = args.get("logger"), args.get("contains")
    rid = str(args.get("request_id") or "").strip()[:64] or None
    with_trace = bool(args.get("include_traceback", True))
    if level >= logging.WARNING:
        obs.flush()
        q = db.query(AppLog).filter(AppLog.at >= since,
                                    AppLog.level.in_([n for n in ("WARNING", "ERROR", "CRITICAL") if _level(n) >= level]))
        if prefix:
            q = q.filter(AppLog.logger.like(str(prefix).replace("%", "").replace("_", "\\_") + "%", escape="\\"))
        if contains:
            q = q.filter(AppLog.message.ilike(_like(contains), escape="\\"))
        if rid:
            q = q.filter(AppLog.request_id == rid)
        rows = q.order_by(AppLog.at.desc(), AppLog.id.desc()).offset(offset).limit(limit + 1).all()
        page = _page(rows, offset, limit)
        rows = rows[:limit]
        items = [_log_out({"at": r.at, "level": r.level, "logger": r.logger, "message": r.message, "exc_type": r.exc_type,
                           "traceback": r.traceback, "request_id": r.request_id, "pod": r.pod}, with_trace) for r in rows]
        source = "database (WARNING+, kept %d days, every pod)" % get_settings().obs_retention_days
    else:
        mem = obs.recent_logs(level, since, prefix, contains, offset + limit + 1, rid)[offset:]
        page = _page(mem, offset, limit)
        items = [_log_out(e, with_trace) for e in mem[:limit]]
        source = f"memory of pod {obs.POD} (INFO+, since the last restart, last 5000 records)"
    return {"source": source, "pod": obs.POD, "count": len(items), "logs": items, **page}


def tool_error_summary(db: Session, args: Dict[str, Any]) -> Dict[str, Any]:
    obs.flush()
    since = _since(args.get("since_minutes"), 1440)
    limit = _limit(args.get("limit"), 30, 200)
    offset = int(args.get("offset") or 0)
    groups = (db.query(AppLog.fingerprint, func.count(AppLog.id), func.min(AppLog.at), func.max(AppLog.at),
                       func.max(AppLog.id))
              .filter(AppLog.at >= since).group_by(AppLog.fingerprint)
              .order_by(func.count(AppLog.id).desc(), func.max(AppLog.at).desc(), AppLog.fingerprint)
              .offset(offset).limit(limit + 1).all())          # ties: most recent first
    page = _page(groups, offset, limit)
    groups = groups[:limit]
    # 3 queries in all (was 2 per group): the sample rows, then versions and pods of every group at once
    fps = [g[0] for g in groups]
    samples = {r.id: r for r in db.query(AppLog).filter(AppLog.id.in_([g[4] for g in groups] or [-1]))}
    versions: Dict[str, set] = defaultdict(set)
    pods: Dict[str, set] = defaultdict(set)
    for fp, v, pod in (db.query(AppLog.fingerprint, AppLog.app_version, AppLog.pod)
                       .filter(AppLog.fingerprint.in_(fps or [""]), AppLog.at >= since).distinct()):
        versions[fp].add(v)
        if pod:
            pods[fp].add(pod)
    out = []
    for fp, n, first, last, sample_id in groups:
        r = samples[sample_id]
        out.append({"count": n, "firstSeen": _iso(first), "lastSeen": _iso(last), "level": r.level, "logger": r.logger,
                    "exception": r.exc_type, "sampleMessage": obs.mask_infra(r.message)[:1500],
                    "sampleTraceback": obs.mask_infra(r.traceback or "")[-2500:] or None,
                    "sampleRequestId": r.request_id, "appVersions": sorted(versions[fp], key=lambda x: x or ""),
                    "pods": sorted(pods[fp])})
    return {"since": _iso(since), "groups": out, **page}


def tool_endpoint_performance(db: Session, args: Dict[str, Any]) -> Dict[str, Any]:
    since = _since(args.get("since_minutes"), 60)
    sort = args.get("sort") if args.get("sort") in ("p95", "avg", "count", "errors", "max", "db") else "p95"
    rows = obs.buckets_since(db, since)
    by_route: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_route[f"{r['method']} {r['route']}"].append(r)
    items = [{"endpoint": k, **obs.summarize(v)} for k, v in by_route.items()]
    keyf: Dict[str, Callable] = {"p95": lambda x: x["p95Ms"] or 0, "avg": lambda x: x["avgMs"] or 0,
                                 "count": lambda x: x["requests"], "errors": lambda x: x["errors5xx"],
                                 "max": lambda x: x["maxMs"], "db": lambda x: x["avgDbMs"] or 0}
    items.sort(key=keyf[sort], reverse=True)
    return {"since": _iso(since), "overall": obs.summarize(rows),
            "note": "Percentiles are bucket upper bounds (5,10,25,50,100,250,500,1000,2500,5000,10000,30000 ms).",
            "endpoints": items[:_limit(args.get("limit"), 25, 200)]}


def tool_performance_trend(db: Session, args: Dict[str, Any]) -> Dict[str, Any]:
    hours = max(1, min(int(args.get("hours") or 24), 24 * 30))
    step = max(5, min(int(args.get("bucket_minutes") or 60), 1440))
    step -= step % 5
    endpoint = args.get("endpoint")
    rows = obs.buckets_since(db, dt.datetime.utcnow() - dt.timedelta(hours=hours))
    if endpoint:
        rows = [r for r in rows if f"{r['method']} {r['route']}" == endpoint or r["route"] == endpoint]
    series: Dict[dt.datetime, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        start = r["start"]
        key = start - dt.timedelta(minutes=(start.hour * 60 + start.minute) % step)
        series[key].append(r)
    points = []
    for k in sorted(series):
        v = series[k]
        sm = obs.summarize(v)
        points.append({"from": _iso(k), "requests": sm["requests"], "avgMs": sm["avgMs"], "p95Ms": sm["p95Ms"],
                       "errors5xx": sm["errors5xx"], "avgDbMs": sm["avgDbMs"],
                       "appVersions": sorted({r["version"] or "?" for r in v})})
    return {"endpoint": endpoint or "(all)", "bucketMinutes": step, "points": points}


def tool_slow_requests(db: Session, args: Dict[str, Any]) -> Dict[str, Any]:
    s = get_settings()
    items = [{**r, "at": _iso(r["at"])} for r in obs.slow_requests(_limit(args.get("limit"), 20, 200))]
    return {"thresholdMs": s.obs_slow_request_ms, "alsoIncluded": "requests with >= 100 SQL queries (possible N+1)",
            "sinceRestart": _iso(obs.STARTED_AT), "requests": items}


def tool_slow_queries(db: Session, args: Dict[str, Any]) -> Dict[str, Any]:
    s = get_settings()
    items = [{**q, "firstSeen": _iso(q["firstSeen"]), "lastSeen": _iso(q.get("lastSeen"))}
             for q in obs.slow_queries(_limit(args.get("limit"), 20, 200))]
    return {"thresholdMs": s.obs_slow_query_ms, "dialect": db.get_bind().dialect.name,
            "sinceRestart": _iso(obs.STARTED_AT), "note": "Statements are normalised; parameters are never recorded.",
            "queries": items}


def _run_out(r, kind: str) -> Dict[str, Any]:
    dur = (r.finished_at - r.started_at).total_seconds() if r.finished_at and r.started_at else None
    out = {"source": kind, "key": r.vcenter_key if kind == "vcenter" else r.nbu_key, "status": r.status,
           "startedAt": _iso(r.started_at), "durationS": round(dur, 1) if dur is not None else None,
           "error": obs.mask_infra(obs.redact(r.error_message))[:1500] if r.error_message else None}
    if kind == "backup":
        out.update({"jobsFetched": r.jobs_fetched, "policies": r.policy_count, "pools": r.pool_count})
    return out


FAMILIES = ("virtualization", "backup", "storage", "hardware", "ipam")
JOBS = ("sync", "cmdb", "owners", "reviews", "alerts", "retention", "metrics", "poll")


def tool_sync_history(db: Session, args: Dict[str, Any]) -> Dict[str, Any]:
    """Every background run from job_runs (all source families + the derived jobs). Before the first recorded run
    (an upgraded install), falls back to the legacy vCenter / NetBackup tables."""
    from . import jobruns
    src = args.get("source") or "all"
    limit = _limit(args.get("limit"), 20, 200)
    status = args.get("status")
    if db.query(JobRun.id).first() is not None:
        q = db.query(JobRun).filter(JobRun.started_at >= _since(args.get("since_minutes"), 1440))
        if src in FAMILIES:
            q = q.filter(JobRun.job == "sync", JobRun.family == src)
        elif src == "vcenter":                       # old name of the parameter
            q = q.filter(JobRun.job == "sync", JobRun.family == "virtualization")
        elif src in JOBS:
            q = q.filter(JobRun.job == src)
        if status in ("ok", "error", "skipped", "timeout"):
            q = q.filter(JobRun.status == status)
        if args.get("key"):
            q = q.filter(JobRun.source_key == str(args["key"])[:64])
        offset = int(args.get("offset") or 0)
        rows = q.order_by(JobRun.started_at.desc(), JobRun.id.desc()).offset(offset).limit(limit + 1).all()
        return {"source": "job_runs (every pod, kept %d days)" % obs.JOB_RUN_RETENTION_DAYS,
                "runs": [jobruns.out(r) for r in rows[:limit]], **_page(rows, offset, limit)}
    out: List[Dict[str, Any]] = []
    if src in ("all", "vcenter"):
        q = db.query(SnapshotRun).filter(SnapshotRun.source == "live")
        if status:
            q = q.filter(SnapshotRun.status == status)
        out += [_run_out(r, "vcenter") for r in q.order_by(SnapshotRun.started_at.desc()).limit(limit)]
    if src in ("all", "backup"):
        q = db.query(BackupSyncRun)
        if status:
            q = q.filter(BackupSyncRun.status == status)
        out += [_run_out(r, "backup") for r in q.order_by(BackupSyncRun.started_at.desc()).limit(limit)]
    out.sort(key=lambda r: r["startedAt"] or "", reverse=True)
    return {"source": "legacy snapshot_runs / backup_sync_runs", "runs": out[:limit], "offset": 0, "hasMore": False,
            "nextOffset": None}


_STR = {"type": "string"}


def _int(default: int, lo: int, hi: int, desc: str = "") -> Dict[str, Any]:
    return {"type": "integer", "default": default, "minimum": lo, "maximum": hi, **({"description": desc} if desc else {})}


def _obj(props: Dict[str, Any], required: tuple = ()) -> Dict[str, Any]:
    return {"type": "object", "properties": props, **({"required": list(required)} if required else {})}


_ARR = {"type": "array"}
_OBJ = {"type": "object"}
_PAGE_IN = {"offset": _int(0, 0, 10000, "Skip this many items (pagination: pass nextOffset of the previous page)")}
_PAGE_OUT = {"offset": {"type": "integer"}, "hasMore": {"type": "boolean"}, "nextOffset": {"type": ["integer", "null"]}}
_MINUTES = _int(60, 1, 43200, "Look back this many minutes (max 30 days)")

# name → fn, title, description, input schema, output schema. Every tool is read only, idempotent, closed world.
TOOLS: Dict[str, Dict[str, Any]] = {
    "ico_app_overview": {
        "fn": tool_app_overview, "title": "App health overview",
        "description": "Start here. Health snapshot of the pod that answers: app version, uptime, memory/CPU, DB ping and pool, "
                       "log counts by level, request volume / latency / error rate for the last 1h and 24h (all pods), last "
                       "vCenter and backup syncs. Then: ico_config_check, ico_source_status, ico_error_summary.",
        "schema": _obj({}),
        "output": _obj({"appVersion": _STR, "pod": _STR, "uptimeHours": {"type": "number"}, "database": _OBJ, "logs": _OBJ,
                        "requestsLast1h": _OBJ, "requestsLast24h": _OBJ}, ("appVersion", "pod", "logs")),
    },
    "ico_search_logs": {
        "fn": tool_search_logs, "title": "Search logs",
        "description": "Search the app's logs, newest first. WARNING/ERROR come from the database (every pod, kept "
                       "OBS_RETENTION_DAYS); INFO/DEBUG only from the memory of the pod that answers, since its restart. Each "
                       "record has requestId (X-Request-ID, or job-<name>-<id> for background work) and pod. Use request_id to "
                       "follow one request / scheduler round, or ico_trace_request for logs + job runs together.",
        "schema": _obj({
            "level": {"type": "string", "enum": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], "default": "WARNING"},
            "since_minutes": _MINUTES, "logger": {**_STR, "description": "Logger name prefix, e.g. vco.netbackup"},
            "contains": {**_STR, "description": "Case-insensitive literal text filter (% and _ are plain characters)"},
            "request_id": {**_STR, "description": "Only records of this request / job id"},
            "include_traceback": {"type": "boolean", "default": True}, "limit": _int(100, 1, 500), **_PAGE_IN}),
        "output": _obj({"source": _STR, "pod": _STR, "count": {"type": "integer"}, "logs": _ARR, **_PAGE_OUT},
                       ("source", "count", "logs", "hasMore")),
    },
    "ico_error_summary": {
        "fn": tool_error_summary, "title": "Recurring errors",
        "description": "WARNING+ records grouped by recurring problem (same logger, exception and message shape), most "
                       "frequent first: count, first/last seen, app versions and pods affected, a sample message, traceback "
                       "and sampleRequestId (pass it to ico_trace_request).",
        "schema": _obj({"since_minutes": _int(1440, 1, 43200, "Look back this many minutes (max 30 days)"),
                        "limit": _int(30, 1, 200), **_PAGE_IN}),
        "output": _obj({"since": _STR, "groups": _ARR, **_PAGE_OUT}, ("since", "groups", "hasMore")),
    },
    "ico_endpoint_performance": {
        "fn": tool_endpoint_performance, "title": "Endpoint performance",
        "description": "Per API endpoint (route template): requests, 4xx/5xx, avg / p50 / p95 / p99 / max latency (percentiles "
                       "interpolated inside histogram buckets), average DB time and SQL queries per request (a high count "
                       "suggests N+1).",
        "schema": _obj({"since_minutes": _MINUTES,
                        "sort": {"type": "string", "enum": ["p95", "avg", "count", "errors", "max", "db"], "default": "p95"},
                        "limit": _int(25, 1, 200)}),
        "output": _obj({"since": _STR, "overall": _OBJ, "endpoints": _ARR}, ("since", "overall", "endpoints")),
    },
    "ico_performance_trend": {
        "fn": tool_performance_trend, "title": "Performance over time",
        "description": "Latency / volume / errors per interval (optionally for one endpoint, e.g. 'GET /api/backup/summary') "
                       "with the app versions seen in each interval — spot regressions after a deploy.",
        "schema": _obj({"hours": _int(24, 1, 720), "bucket_minutes": _int(60, 5, 1440), "endpoint": _STR}),
        "output": _obj({"endpoint": _STR, "bucketMinutes": {"type": "integer"}, "points": _ARR}, ("points",)),
    },
    "ico_slow_requests": {
        "fn": tool_slow_requests, "title": "Slowest requests",
        "description": "Slowest recent requests of the pod that answers (over OBS_SLOW_REQUEST_MS, or with >= 100 SQL "
                       "queries) with DB time and query count, since its restart.",
        "schema": _obj({"limit": _int(20, 1, 200)}),
        "output": _obj({"thresholdMs": {"type": "number"}, "requests": _ARR}, ("requests",)),
    },
    "ico_slow_queries": {
        "fn": tool_slow_queries, "title": "Slowest SQL",
        "description": "Slow SQL statements of the pod that answers (over OBS_SLOW_QUERY_MS) since its restart, normalised "
                       "(never with parameters), by total time.",
        "schema": _obj({"limit": _int(20, 1, 200)}),
        "output": _obj({"thresholdMs": {"type": "number"}, "queries": _ARR}, ("queries",)),
    },
    "ico_sync_history": {
        "fn": tool_sync_history, "title": "Background runs",
        "description": "Individual background runs, newest first: source syncs of every family (virtualization = vCenter / "
                       "Proxmox / Nutanix, backup, storage, hardware = Dell OME, ipam) and the derived jobs (cmdb build, "
                       "owners rules, reviews, alerts, retention, metrics, poll = whole round): trigger, pod, duration, "
                       "status, counters, error. runId → ico_trace_request. For totals per job use ico_job_runs.",
        "schema": _obj({
            "source": {"type": "string", "enum": ["all", "virtualization", "backup", "storage", "hardware", "ipam", "vcenter",
                                                  "sync", "cmdb", "owners", "reviews", "alerts", "retention", "metrics", "poll"],
                       "default": "all"},
            "status": {"type": "string", "enum": ["ok", "error", "skipped", "timeout"]},
            "key": {**_STR, "description": "Source key, e.g. vc1"},
            "since_minutes": _int(1440, 1, 43200, "Look back this many minutes (max 30 days)"), "limit": _int(20, 1, 200), **_PAGE_IN}),
        "output": _obj({"source": _STR, "runs": _ARR, **_PAGE_OUT}, ("runs",)),
    },
    "ico_job_runs": {
        "fn": mcp_extra.tool_job_runs, "title": "Background runs — totals",
        "description": "Background runs aggregated per job (and per source for syncs): runs, ok / error / skipped / timeout, "
                       "manual runs, p50 / p95 / max duration, last status and error. Failing or slow groups first.",
        "schema": _obj({"since_hours": _int(24, 1, 720)}),
        "output": _obj({"sinceHours": {"type": "integer"}, "groups": _ARR}, ("groups",)),
    },
    "ico_source_status": {
        "fn": mcp_extra.tool_source_status, "title": "Data source status",
        "description": "Connection state of every configured data source (vCenter / Proxmox / Nutanix, NetBackup, Pure / NetApp, "
                       "Dell OME, GestióIP) from its real last syncs: ok / stale / error / never, last OK, last error.",
        "schema": _obj({"family": {"type": "string", "enum": ["virtualization", "backup", "storage", "hardware", "ipam"]}}),
        "output": _obj({"summary": _OBJ, "sources": _ARR, "staleAfterMinutes": {"type": "integer"}}, ("summary", "sources")),
    },
    "ico_data_freshness": {
        "fn": mcp_extra.tool_data_freshness, "title": "Data freshness",
        "description": "How old the data is: age of the least recent successful sync per source family, last CI registry "
                       "build, CI counts by type (active / retired), owner coverage of VMs.",
        "schema": _obj({}),
        "output": _obj({"families": _ARR, "cmdb": _OBJ, "ownerCoverage": {"type": ["object", "null"]}}, ("families", "cmdb")),
    },
    "ico_ai_usage": {
        "fn": mcp_extra.tool_ai_usage, "title": "AI module usage",
        "description": "The app's own AI module (Settings → AI & MCP): configuration flags (no key), month cost vs budget, per "
                       "task calls / cached / blocked / errors, latency p50 / p95, tokens, cost, and the recent errors / "
                       "blocks with their safe message. Never prompts or answers; users shown as hashes.",
        "schema": _obj({"since_hours": _int(168, 1, 720)}),
        "output": _obj({"config": _OBJ, "monthCostUsd": {"type": "number"}, "tasks": _ARR, "recentProblems": _ARR},
                       ("config", "tasks")),
    },
    "ico_config_check": {
        "fn": mcp_extra.tool_config_check, "title": "Configuration check",
        "description": "Configuration sanity (flags only, no secret values): PUBLIC_URL / HTTPS, Secure cookie, scheduler and "
                       "leader pod, PDF font library, SMTP, MCP host masking, AI module, features switched off, retention.",
        "schema": _obj({}),
        "output": _obj({"appVersion": _STR, "pod": _STR, "status": _STR, "checks": _ARR}, ("status", "checks")),
    },
    "ico_trace_request": {
        "fn": mcp_extra.tool_trace_request, "title": "Trace one request / run",
        "description": "Everything recorded for one request id or job id, in time order: logs (WARNING+ of every pod, INFO of "
                       "the pod that answers) and background runs. Ids come from a response's X-Request-ID, requestId in "
                       "logs, sampleRequestId of ico_error_summary or runId of ico_sync_history.",
        "schema": _obj({"request_id": {**_STR, "minLength": 8, "maxLength": 64}}, ("request_id",)),
        "output": _obj({"requestId": _STR, "count": {"type": "integer"}, "events": _ARR}, ("requestId", "events")),
    },
}
ANNOTATIONS = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
# Names before review phase B: still callable for one release (an AI client may have cached them), not listed.
ALIASES = {n[len("ico_"):]: n for n in TOOLS if n[len("ico_"):] in (
    "app_overview", "search_logs", "error_summary", "endpoint_performance", "performance_trend", "slow_requests",
    "slow_queries", "sync_history")}


def _check_args(name: str, schema: Dict[str, Any], args: Dict[str, Any]) -> None:
    """Validate against the tool's input schema; the message says how to fix the call (no silent clamping)."""
    props = schema.get("properties") or {}
    unknown = sorted(set(args) - set(props))
    if unknown:
        raise mcp_extra.ToolInputError(f"Unknown argument(s) {', '.join(unknown)} for {name}. "
                                       f"Allowed: {', '.join(sorted(props)) or '(none)'}.")
    for req in schema.get("required") or []:
        if args.get(req) in (None, ""):
            raise mcp_extra.ToolInputError(f"{req} is required for {name}.")
    for k, v in args.items():
        p = props[k]
        if v is None:
            continue
        t = p.get("type")
        if t == "integer":
            if not isinstance(v, int) or isinstance(v, bool):
                raise mcp_extra.ToolInputError(f"{k} must be an integer (got {v!r}).")
            if not p.get("minimum", v) <= v <= p.get("maximum", v):
                raise mcp_extra.ToolInputError(f"{k} must be from {p['minimum']} to {p['maximum']} (got {v}). "
                                               "Use a smaller window with offset paging for more.")
        elif t == "boolean" and not isinstance(v, bool):
            raise mcp_extra.ToolInputError(f"{k} must be true or false (got {v!r}).")
        elif t == "string":
            if not isinstance(v, str):
                raise mcp_extra.ToolInputError(f"{k} must be a string (got {v!r}).")
            if len(v) > p.get("maxLength", 500):
                raise mcp_extra.ToolInputError(f"{k} is too long (max {p.get('maxLength', 500)} characters).")
            if "enum" in p and v not in p["enum"]:
                raise mcp_extra.ToolInputError(f"{k} must be one of {', '.join(p['enum'])} (got {v!r}).")


def _page(items: List[Any], offset: int, limit: int) -> Dict[str, Any]:
    """Paging fields. `items` = the rows of this page fetched with limit + 1: the extra row only says there is more."""
    more = len(items) > limit
    return {"offset": offset, "hasMore": more, "nextOffset": offset + limit if more else None}


PROMPTS = {
    "health_review": {
        "description": "Review ICO health and performance and propose prioritised fixes with evidence.",
        "text": ("Review the health of Infra Capacity Ops with the ico tools: ico_app_overview, ico_config_check, "
                 "ico_source_status, ico_error_summary (24h), ico_job_runs (24h), ico_endpoint_performance (24h, sort p95), "
                 "ico_slow_requests and ico_slow_queries. Follow any interesting id with ico_trace_request. For each problem "
                 "give: symptom, evidence (tool output with timestamps, app version, pod), likely cause in the code (file and "
                 "function if you have the repository), proposed fix, risk. Do not change anything in production; if a code "
                 "change is warranted, prepare it as a pull request for human review."),
    },
    "error_triage": {
        "description": "Explain the most frequent recent errors and propose fixes.",
        "text": ("Use ico_error_summary for the last 24 hours, then ico_trace_request on the sampleRequestId of the top 3 "
                 "groups (or ico_search_logs around their first occurrence). Explain the root cause of each, whether it is a "
                 "bug, a configuration or an external system problem, and propose a fix (code change via pull request, or a "
                 "configuration step for the operator)."),
    },
    "sync_triage": {
        "description": "Find data sources that fail, are stale or slow, and say why and what to do.",
        "text": ("Use ico_source_status, ico_data_freshness and ico_job_runs (since_hours=48). For every source in error / "
                 "stale / never, or a job with errors, timeouts or a p95 far above its p50: list its last runs with "
                 "ico_sync_history(source=<family>, key=<key>), read the logs of the failing runId with ico_trace_request, "
                 "and classify the cause (credentials / network / remote API / our code / capacity of the run). Give the "
                 "operator's next step or the code change, with evidence. Sources are read only: never suggest a write call."),
    },
    "ai_module_review": {
        "description": "Review the app's AI module: errors, blocks, latency, cost, settings.",
        "text": ("Use ico_ai_usage (since_hours=168) and ico_config_check. Report per task: calls, errors and blocks with their "
                 "messages, p95 latency, cost and month cost against the budget. Flag tasks with an error or block rate above "
                 "10 %, slow tasks, masking switched off, and spend that will exceed the budget at the current pace. Propose "
                 "setting changes (which task, which limit) or code changes with evidence."),
    },
    "release_regression": {
        "description": "Compare errors and latency before and after the latest deploy.",
        "text": ("Use ico_app_overview for the current appVersion, ico_performance_trend (hours=72, bucket_minutes=60) overall "
                 "and for the 5 slowest endpoints of ico_endpoint_performance (since_minutes=4320), and ico_error_summary "
                 "(since_minutes=4320) with its appVersions. Say whether the current version is slower or fails more than the "
                 "previous one, on which endpoints / errors, with the numbers before and after."),
    },
}


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------
def _rpc_error(id_: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _tool_error(id_: Any, message: str) -> Dict[str, Any]:
    """A failed tool call is a result with isError (the model reads it and can retry), not a protocol error."""
    return {"jsonrpc": "2.0", "id": id_, "result": {"content": [{"type": "text", "text": message}], "isError": True}}


ENTRA_TOKEN_MAX_IDLE = dt.timedelta(days=14)


def _authenticate(request: Request, db: Session) -> Optional[tuple]:
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    raw = auth[7:].strip()
    if not raw.startswith(TOKEN_PREFIX):
        return None
    tok = db.query(ApiToken).filter(ApiToken.token_hash == hash_token(raw), ApiToken.scope == "mcp").first()
    now = dt.datetime.utcnow()
    if not tok or tok.revoked_at or tok.expires_at < now:
        return None
    user = db.get(User, tok.user_id)
    if not user or not is_admin(user) or (user.access_scope or "full") != "full" or \
            ((user.auth_source or "local") == "local" and not local_login_allowed(user)):
        return None
    # Entra rights (admin role, account enabled) are only re-read at sign-in: a token of someone who has not signed in
    # for ENTRA_TOKEN_MAX_IDLE may belong to a disabled account or a removed ICO.Admin role → refuse it.
    if user.auth_source == "entra" and (not user.last_login_at or now - user.last_login_at > ENTRA_TOKEN_MAX_IDLE):
        return None
    if not tok.last_used_at or (now - tok.last_used_at).total_seconds() > 60:
        tok.last_used_at = now
        db.commit()
    return tok, user


MAX_BATCH = 20


def _client_ip(request: Request) -> str:
    """Peer IP; X-Forwarded-For only when TRUST_FORWARDED_FOR says a trusted proxy sets it (SEC2-09)."""
    fwd = request.headers.get("x-forwarded-for") if get_settings().trust_forwarded_for else None
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else ""))[:64]


def _handle(msg: Dict[str, Any], db: Session, user: User, tok: ApiToken, request: Request) -> Optional[Dict[str, Any]]:
    id_, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if "id" not in msg:                # notification (e.g. notifications/initialized): no response
        return None
    if method == "initialize":
        requested = params.get("protocolVersion")
        return {"jsonrpc": "2.0", "id": id_, "result": {
            "protocolVersion": requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}, "prompts": {"listChanged": False}},
            "serverInfo": {"name": "infra-capacity-ops", "title": "Infra Capacity Ops (read-only)",
                           "version": get_settings().app_version},
            "instructions": INSTRUCTIONS}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": id_, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": id_, "result": {"tools": [
            {"name": n, "title": t["title"], "description": t["description"], "inputSchema": t["schema"],
             "outputSchema": t["output"], "annotations": {**ANNOTATIONS, "title": t["title"]}} for n, t in TOOLS.items()]}}
    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": id_, "result": {"prompts": [
            {"name": n, "description": p["description"]} for n, p in PROMPTS.items()]}}
    if method == "prompts/get":
        p = PROMPTS.get(params.get("name"))
        if not p:
            return _rpc_error(id_, -32602, "Unknown prompt")
        return {"jsonrpc": "2.0", "id": id_, "result": {"description": p["description"], "messages": [
            {"role": "user", "content": {"type": "text", "text": p["text"]}}]}}
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        name = ALIASES.get(name, name)
        tool = TOOLS.get(name)
        if not tool:
            return _tool_error(id_, f"Unknown tool {str(name)[:60]!r}. Available tools: {', '.join(TOOLS)}. "
                                    "Start with ico_app_overview.")
        if not isinstance(args, dict):
            return _tool_error(id_, "arguments must be a JSON object.")
        detail = json.dumps({"tool": name, "args": args, "token": tok.prefix}, default=str)
        if len(detail) > 2000:   # keep the audit row valid JSON (slicing the text cut it mid-string, SCH-10)
            detail = json.dumps({"tool": name, "argsTruncated": json.dumps(args, default=str)[:1500], "token": tok.prefix})
        db.add(AuditLog(username=user.username, action="mcp.tool", client_ip=_client_ip(request), detail=detail))
        db.commit()
        try:
            _check_args(name, tool["schema"], args)
            data = tool["fn"](db, args)
        except mcp_extra.ToolInputError as exc:
            db.rollback()
            return _tool_error(id_, f"Invalid arguments: {exc}")
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.exception("MCP tool %s failed", name)
            return _tool_error(id_, f"{name} failed ({type(exc).__name__}). Details are in the server log under request id "
                                    f"{obs.current_request_id()} — read them with ico_trace_request. Retry with a smaller "
                                    "window or limit if the data set is large.")
        body = json.dumps(data, ensure_ascii=False, default=str)
        if len(body) > MAX_TEXT:
            return _tool_error(id_, f"Result too large ({len(body):,} characters, max {MAX_TEXT:,}). Use a smaller limit or "
                                    "time window, or page with offset / nextOffset.")
        return {"jsonrpc": "2.0", "id": id_, "result": {"content": [{"type": "text", "text": body}],
                                                         "structuredContent": json.loads(body), "isError": False}}
    return _rpc_error(id_, -32601, f"Method not found: {method}")


@router.api_route("/mcp", methods=["GET", "DELETE"], include_in_schema=False)
@router.api_route("/mcp/", methods=["GET", "DELETE"], include_in_schema=False)
def mcp_no_stream():
    # Stateless server: no server-initiated SSE stream and no sessions to delete.
    return Response(status_code=405, headers={"Allow": "POST"})


@router.post("/mcp", include_in_schema=False)
@router.post("/mcp/", include_in_schema=False)
async def mcp_endpoint(request: Request, db: Session = Depends(get_db)):
    from starlette.concurrency import run_in_threadpool
    from . import mcp_config
    cfg = await run_in_threadpool(mcp_config.current, db)      # Settings → AI, environment as default
    if not cfg.enabled:
        return JSONResponse({"error": "MCP server is disabled (Settings → AI / MCP_ENABLED)"}, status_code=404)
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in cfg.allowed_origins:      # DNS-rebinding / browser cross-site protection
        return JSONResponse({"error": "Origin not allowed"}, status_code=403)
    auth = await run_in_threadpool(_authenticate, request, db)
    if not auth:
        return JSONResponse({"error": "invalid_token", "error_description": "Missing, invalid, expired or revoked MCP token"},
                            status_code=401, headers={"WWW-Authenticate": 'Bearer realm="ico-mcp"'})
    tok, user = auth
    window = _rate[tok.id]
    now = time.time()
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= cfg.rate_per_minute:
        return JSONResponse({"error": "rate_limited"}, status_code=429, headers={"Retry-After": "30"})
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse(_rpc_error(None, -32700, "Parse error"), status_code=400)
    messages = payload if isinstance(payload, list) else [payload]
    if not messages or not all(isinstance(m, dict) for m in messages):
        return JSONResponse(_rpc_error(None, -32600, "Invalid Request"), status_code=400)
    # the limit counts JSON-RPC messages, not HTTP requests: a batch cannot multiply it (SEC2-09)
    if len(messages) > MAX_BATCH or len(window) + len(messages) > cfg.rate_per_minute:
        return JSONResponse({"error": "rate_limited", "maxBatch": MAX_BATCH}, status_code=429, headers={"Retry-After": "30"})
    window.extend([now] * len(messages))
    replies = await run_in_threadpool(
        lambda: [r for r in (_handle(m, db, user, tok, request) for m in messages) if r is not None])
    if not replies:
        return Response(status_code=202)
    return JSONResponse(replies if isinstance(payload, list) else replies[0])
