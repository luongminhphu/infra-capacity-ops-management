"""MCP tools added by review phase B (docs/design/ico-ai-mcp-review.md §4.1): the state of the whole app for an
admin's AI client — sources, background runs, data freshness, the AI module, configuration, one request end to end.

Same rules as mcp_server: read only, aggregated numbers, no infrastructure inventory, no secrets, no user names
(AI runs show a short hash per user), host names / IPs masked (MCP_MASK_HOSTS)."""
import ctypes.util
import datetime as dt
import hashlib
from collections import defaultdict
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import observability as obs
from .config import get_settings
from .models import AiRun, AppLog, JobRun


class ToolInputError(ValueError):
    """Bad arguments: the message tells the AI client how to fix the call."""


def _iso(t: Optional[dt.datetime]) -> Optional[str]:
    return t.replace(microsecond=0).isoformat() + "Z" if t else None


def _pct(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 1)


def _hours(args: Dict[str, Any], default: int, maximum: int = 24 * 30) -> int:
    h = args.get("since_hours", default)
    if not isinstance(h, int) or isinstance(h, bool) or not 1 <= h <= maximum:
        raise ToolInputError(f"since_hours must be an integer from 1 to {maximum} (got {h!r}).")
    return h


# ---------------------------------------------------------------- source status
def tool_source_status(db: Session, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import source_status
    items = source_status.status_all(db)
    fam = args.get("family")
    if fam:
        items = [i for i in items if i["family"] == fam]
    out = [{"family": i["family"], "kind": i["kind"], "key": i["key"], "name": obs.mask_infra(i["name"]), "demo": i["demo"],
            "state": i["state"], "lastSyncAt": i["lastSyncAt"], "lastOkAt": i["lastOkAt"], "lastErrorAt": i["lastErrorAt"],
            "error": obs.mask_infra(obs.redact(i["error"]))[:600] if i["error"] else None} for i in items]
    return {"staleAfterMinutes": int(source_status.stale_after().total_seconds() // 60),
            "summary": source_status.summary(items), "sources": out,
            "hint": "state: ok · stale (no successful sync within staleAfterMinutes) · error (last attempt failed) · "
                    "never (never synced). For the runs behind a state use ico_sync_history(source=<family>, key=<key>)."}


# ---------------------------------------------------------------- job runs (aggregated)
def tool_job_runs(db: Session, args: Dict[str, Any]) -> Dict[str, Any]:
    hours = _hours(args, 24)
    since = dt.datetime.utcnow() - dt.timedelta(hours=hours)
    rows = (db.query(JobRun.job, JobRun.family, JobRun.source_key, JobRun.status, JobRun.duration_ms, JobRun.started_at,
                     JobRun.error, JobRun.trigger)
            .filter(JobRun.started_at >= since).order_by(JobRun.started_at).all())
    groups: Dict[tuple, Dict[str, Any]] = {}
    for job, fam, key, status, ms, at, err, trig in rows:
        g = groups.setdefault((job, fam, key), {"job": job, "family": fam, "key": key, "runs": 0, "ok": 0, "error": 0,
                                                "skipped": 0, "timeout": 0, "manual": 0, "_ms": []})
        g["runs"] += 1
        g[status] += 1
        g["manual"] += trig == "manual"
        if ms is not None:
            g["_ms"].append(ms)
        g["lastAt"], g["lastStatus"] = _iso(at), status
        if status != "ok" and err:
            g["lastError"] = obs.mask_infra(err)[:300]
    out = []
    for g in groups.values():
        ms = g.pop("_ms")
        out.append({**g, "p50Ms": _pct(ms, 0.5), "p95Ms": _pct(ms, 0.95), "maxMs": max(ms) if ms else None})
    out.sort(key=lambda g: (-(g["error"] + g["timeout"]), -(g["p95Ms"] or 0)))
    return {"sinceHours": hours, "groups": out,
            "hint": "One group per job (and per source for syncs). Failing or slow groups come first. "
                    "Use ico_sync_history for the individual runs and ico_trace_request(request_id=runId) for their logs."}


# ---------------------------------------------------------------- data freshness
def tool_data_freshness(db: Session, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import source_status
    from .cmdb.models import Ci
    now = dt.datetime.utcnow()
    items = source_status.status_all(db)

    def age(iso):
        if not iso:
            return None
        return round((now - dt.datetime.fromisoformat(iso.rstrip("Z"))).total_seconds() / 3600, 1)
    fams: Dict[str, Dict[str, Any]] = {}
    for i in items:
        f = fams.setdefault(i["family"], {"family": i["family"], "sources": 0, "oldestOkHours": None, "neverSynced": 0})
        f["sources"] += 1
        a = age(i["lastOkAt"])
        if a is None:
            f["neverSynced"] += 1
        elif f["oldestOkHours"] is None or a > f["oldestOkHours"]:
            f["oldestOkHours"] = a
    cis: Dict[str, Dict[str, int]] = defaultdict(lambda: {"active": 0, "retired": 0})
    for t, st, n in db.query(Ci.type, Ci.status, func.count(Ci.id)).group_by(Ci.type, Ci.status):
        cis[t][st if st in ("active", "retired") else "active"] += n
    last_cmdb = db.query(JobRun).filter(JobRun.job == "cmdb", JobRun.status == "ok").order_by(JobRun.started_at.desc()).first()
    from .owners import service as owners
    try:
        cov = owners.coverage(db, limit=1)
        coverage = {"vms": cov["total"], "withOwner": cov["owned"], "inSystemWithoutOwner": cov["inSystemNoOwner"],
                    "unassigned": cov["unassigned"], "ownedPct": cov["ownedPct"]}
    except Exception:  # noqa: BLE001 — module tables missing on a very old install
        coverage = None
    return {"at": _iso(now), "families": sorted(fams.values(), key=lambda f: f["family"]),
            "cmdb": {"lastBuildAt": _iso(last_cmdb.started_at) if last_cmdb else None,
                     "lastBuildHoursAgo": round((now - last_cmdb.started_at).total_seconds() / 3600, 1) if last_cmdb else None,
                     "cis": dict(sorted(cis.items()))},
            "ownerCoverage": coverage,
            "hint": "oldestOkHours = age of the least recent successful sync in the family. The CI registry is rebuilt after "
                    "every poll round on the leader pod; a lastBuildHoursAgo far above the poll interval means the round stalls."}


# ---------------------------------------------------------------- AI module
def _user_tag(name: Optional[str]) -> Optional[str]:
    return "user:" + hashlib.sha256(name.lower().encode()).hexdigest()[:8] if name else None


def tool_ai_usage(db: Session, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import ai_platform
    from .shared import ai_transport
    from .shared.money import micros_to_usd
    hours = _hours(args, 24 * 7)
    since = dt.datetime.utcnow() - dt.timedelta(hours=hours)
    cfg = ai_platform.config(db)
    states = ai_platform.task_states(cfg)
    by: Dict[str, Dict[str, Any]] = {}
    for task, status, ms, micros, tin, tout, model in (
            db.query(AiRun.task, AiRun.status, AiRun.latency_ms, AiRun.cost_usd_micros, AiRun.input_tokens, AiRun.output_tokens,
                     AiRun.model).filter(AiRun.created_at >= since)):
        t = by.setdefault(task, {"task": task, "label": ai_platform.TASKS.get(task, (task,))[0], "enabled": states.get(task),
                                 "ok": 0, "cached": 0, "blocked": 0, "error": 0, "costUsd": 0, "inputTokens": 0, "outputTokens": 0,
                                 "models": set(), "_ms": []})
        t[status] = t.get(status, 0) + 1
        t["costUsd"] += micros or 0
        t["inputTokens"] += tin or 0
        t["outputTokens"] += tout or 0
        if model:
            t["models"].add(model)
        if status == "ok" and ms is not None:
            t["_ms"].append(ms)
    tasks = []
    for t in by.values():
        ms = t.pop("_ms")
        tasks.append({**t, "costUsd": round(micros_to_usd(t["costUsd"]), 4), "models": sorted(t["models"]),
                      "p50Ms": _pct(ms, 0.5), "p95Ms": _pct(ms, 0.95)})
    tasks.sort(key=lambda t: (-(t["error"] + t["blocked"]), t["task"]))
    recent = (db.query(AiRun).filter(AiRun.created_at >= since, AiRun.status.in_(("error", "blocked")))
              .order_by(AiRun.id.desc()).limit(20).all())
    return {
        "sinceHours": hours,
        "config": {"provider": getattr(cfg, "provider", None), "model": getattr(cfg, "model", None),
                   "effort": getattr(cfg, "effort", None), "enabled": bool(ai_transport.enabled(cfg)) if cfg else False,
                   "allowExternal": bool(getattr(cfg, "allow_external", False)), "maskNames": getattr(cfg, "mask_names", True) is not False,
                   "budgetUsd": getattr(cfg, "budget_usd", None) if cfg else None, "alertUsd": getattr(cfg, "alert_usd", None) if cfg else None,
                   "ratePerHour": getattr(cfg, "rate_per_hour", None) if cfg else None,
                   "tasksOff": sorted(k for k, on in states.items() if not on)},
        "monthCostUsd": round(ai_platform.month_cost(db), 4),
        "tasks": tasks,
        "recentProblems": [{"at": _iso(r.created_at), "task": r.task, "status": r.status, "user": _user_tag(r.username),
                            "latencyMs": r.latency_ms, "message": obs.mask_infra(r.message or "")[:300]} for r in recent],
        "hint": "Only counts and safe messages are stored (never prompts or answers). blocked = policy refused the call "
                "(module off, task off, budget, per-user rate) — the message says which. Users are shown as stable hashes.",
    }


# ---------------------------------------------------------------- configuration check
def _check(name: str, ok: bool, detail: str, level_bad: str = "warn") -> Dict[str, Any]:
    return {"check": name, "status": "ok" if ok else level_bad, "detail": detail}


def tool_config_check(db: Session, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import leader, mcp_config
    s = get_settings()
    checks = []
    base = s.public_base()
    checks.append(_check("public_url", bool(base) and base.lower().startswith("https://"),
                         "PUBLIC_URL is https" if base.lower().startswith("https://") else
                         ("PUBLIC_URL is not https" if base else "PUBLIC_URL not set: e-mail links and OIDC callback cannot be built")))
    checks.append(_check("secure_cookie", s.secure_cookies(), "Session cookie has the Secure flag" if s.secure_cookies()
                         else "Session cookie without Secure (COOKIE_SECURE=false and PUBLIC_URL not https) — test only"))
    checks.append(_check("scheduler", bool(s.enable_scheduler), f"Polling every {s.poll_interval_minutes} min" if s.enable_scheduler
                         else "ENABLE_SCHEDULER=false: nothing is synced automatically", "bad"))
    ls = leader.status()
    checks.append(_check("leader", True, ("this pod is the leader" if ls["leader"] else "this pod is a standby (another pod polls)")
                         + ("" if ls["coordinated"] else " — single instance (SQLite / no advisory lock)")))
    hb = bool(ctypes.util.find_library("harfbuzz-subset"))
    checks.append(_check("pdf_harfbuzz", hb, "libharfbuzz-subset present: fast font subsetting" if hb
                         else "libharfbuzz-subset missing: PDFs subset fonts with fontTools (slower)"))
    try:
        from .spend.notify import smtp_settings
        smtp = smtp_settings(db) is not None
        checks.append(_check("smtp", smtp, "SMTP configured (owner reviews, reminders)" if smtp
                             else "SMTP not configured: owner review e-mails stay pending"))
    except Exception as exc:  # noqa: BLE001 — e.g. stored password unreadable
        checks.append(_check("smtp", False, f"SMTP settings unreadable: {type(exc).__name__}", "bad"))
    m = mcp_config.current(db)
    checks.append(_check("mcp_mask_hosts", m.mask_hosts, "host names / IPs masked in MCP output" if m.mask_hosts
                         else "MCP_MASK_HOSTS off: internal names reach the AI client"))
    from . import ai_platform
    from .shared import ai_transport
    cfg = ai_platform.config(db)
    ai_on = bool(cfg) and ai_transport.enabled(cfg)
    checks.append(_check("ai", True, "AI module on" if ai_on else "AI module off (features fall back to rule-based text)"))
    if ai_on and getattr(cfg, "mask_names", True) is False:
        checks.append(_check("ai_mask_names", False, "AI name masking is off: host / VM names are sent to the provider"))
    from .features import get_features
    feats = get_features(db)
    off = sorted(k for k, v in (feats.items() if isinstance(feats, dict) else []) if not v)
    worst = "bad" if any(c["status"] == "bad" for c in checks) else ("warn" if any(c["status"] == "warn" for c in checks) else "ok")
    return {"appVersion": s.app_version, "pod": obs.POD, "status": worst, "checks": checks, "featuresOff": off,
            "retention": {"logsDays": s.obs_retention_days, "jobRunsDays": obs.JOB_RUN_RETENTION_DAYS,
                          "snapshotsMonths": s.snapshot_retention_months},
            "hint": "Configuration flags only, never values of secrets. A 'warn' is a risk to review, not necessarily a fault."}


# ---------------------------------------------------------------- one request / job end to end
def tool_trace_request(db: Session, args: Dict[str, Any]) -> Dict[str, Any]:
    rid = str(args.get("request_id") or "").strip()
    if not 8 <= len(rid) <= 64:
        raise ToolInputError("request_id is required (8–64 characters): the X-Request-ID of a response, a requestId from "
                             "ico_search_logs, or a runId from ico_sync_history (job-poll-…).")
    obs.flush()
    events: List[Dict[str, Any]] = []
    for r in db.query(AppLog).filter(AppLog.request_id == rid).order_by(AppLog.at).limit(500):
        events.append({"at": r.at, "kind": "log", "level": r.level, "logger": r.logger, "pod": r.pod,
                       "message": obs.mask_infra(r.message)[:1500], "exception": r.exc_type})
    seen = {(e["at"], e["message"]) for e in events}
    for e in obs.recent_logs(0, dt.datetime.utcnow() - dt.timedelta(days=30), None, None, 1000, rid):
        msg = obs.mask_infra(e["message"])[:1500]
        if (e["at"], msg) not in seen:
            events.append({"at": e["at"], "kind": "log", "level": e["level"], "logger": e["logger"], "pod": e.get("pod"),
                           "message": msg, "exception": e.get("exc_type")})
    from . import jobruns
    for r in db.query(JobRun).filter(JobRun.run_id == rid).order_by(JobRun.started_at).limit(200):
        events.append({"at": r.started_at, "kind": "run", **{k: v for k, v in jobruns.out(r).items() if k != "runId"}})
    events.sort(key=lambda e: e["at"])
    for e in events:
        e["at"] = _iso(e["at"]) if isinstance(e["at"], dt.datetime) else e["at"]
    return {"requestId": rid, "count": len(events), "events": events,
            "note": f"WARNING+ logs of every pod and job runs come from the database; INFO logs only from the memory of pod "
                    f"{obs.POD}. An empty result: unknown id, or INFO-only records kept by another pod.",
            "hint": None if events else "Check the id, or search ico_search_logs(level='WARNING', since_minutes=…) first."}
