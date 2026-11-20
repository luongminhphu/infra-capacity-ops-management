"""Registry of background work runs (`job_runs`): every source sync of every family, the CI build, owner rules,
the review round, alerts, retention, metrics roll-up and the poll round itself — one row each, with timing,
status, numeric counters and the public (already redacted) error.

Before this, only vCenter and NetBackup runs were stored (snapshot_runs / backup_sync_runs); everything else was a
line of INFO text in the memory of one pod. MCP reads this table (mcp_server sync_history), so an AI client sees the
whole background flow on any pod.

Writing is best effort: a failure here is logged and never stops or slows the job (one INSERT per round step)."""
import datetime as dt
import json
import logging
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from . import observability

logger = logging.getLogger("vco.jobruns")

MAX_COUNTERS = 30


def _trigger_and_run_id() -> Tuple[str, Optional[str]]:
    rid = observability.current_request_id()
    return ("schedule" if not rid or rid.startswith("job-") else "manual"), (rid[:64] if rid else None)


def counters_of(result: Any) -> Optional[Dict[str, Any]]:
    """Numbers of a result dict (one level, plus one nested level as a.b), at most MAX_COUNTERS."""
    if not isinstance(result, dict):
        return None
    out: Dict[str, Any] = {}

    def num(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    for k, v in result.items():
        if k in ("ok", "runId"):
            continue
        if num(v):
            out[str(k)[:40]] = round(v, 3) if isinstance(v, float) else v
        elif isinstance(v, dict):
            for k2, v2 in v.items():
                if num(v2):
                    out[f"{k}.{k2}"[:40]] = round(v2, 3) if isinstance(v2, float) else v2
        if len(out) >= MAX_COUNTERS:
            break
    return out or None


def _write(rows: Iterable[Dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        return
    from .database import SessionLocal
    from .models import JobRun
    token = observability._internal.set(True)          # not counted as the request's SQL
    try:
        with SessionLocal() as db:
            db.add_all([JobRun(**r) for r in rows])
            db.commit()
    except Exception:  # noqa: BLE001 — the registry must never break the job it describes
        logger.warning("Could not record %d job run(s)", len(rows), exc_info=True)
    finally:
        observability._internal.reset(token)


def _row(job: str, started: dt.datetime, ms: Optional[float], status: str, counters: Optional[Dict[str, Any]] = None,
         error: Optional[str] = None, family: Optional[str] = None, key: Optional[str] = None) -> Dict[str, Any]:
    trigger, rid = _trigger_and_run_id()
    return {"job": job, "family": family, "source_key": (key or None) and str(key)[:64], "trigger": trigger, "run_id": rid,
            "pod": observability.POD, "started_at": started, "duration_ms": None if ms is None else max(0, int(ms)),
            "status": status, "counters": json.dumps(counters, ensure_ascii=False) if counters else None,
            "error": (observability.redact(str(error))[:500] if error else None)}


def _families() -> Dict[Tuple[str, str], str]:
    """(result key field, source key) → family, from the configured sources (source_status.configured)."""
    from .source_status import configured
    out: Dict[Tuple[str, str], str] = {}
    try:
        for fam, _kind, cfg in configured():
            field = "vcenterKey" if fam == "virtualization" else "key"
            out.setdefault((field, cfg.key), fam)
    except Exception:  # noqa: BLE001
        logger.debug("source families unavailable", exc_info=True)
    return out


def record_syncs(results: List[Optional[Dict[str, Any]]], starts: Dict[int, Tuple[float, dt.datetime]],
                 ends: Dict[int, float]) -> None:
    """One row per source of a run_syncs() call (scheduler round, Đồng bộ ngay, per-source sync)."""
    fams = _families()
    rows = []
    for i, r in enumerate(results):
        if r is None or i not in starts:
            continue
        field = "vcenterKey" if "vcenterKey" in r else "key"
        key = r.get(field)
        mono, wall = starts[i]
        ms = (ends[i] - mono) * 1000 if i in ends else (time.monotonic() - mono) * 1000
        status = "ok" if r.get("ok") else ("timeout" if r.get("timedOut") else "skipped" if r.get("skipped") else "error")
        rows.append(_row("sync", wall, ms, status, counters_of(r), None if r.get("ok") else r.get("error"),
                         fams.get((field, key)) or ("virtualization" if field == "vcenterKey" else None), key))
    _write(rows)


def timed(job: str, fn: Callable[..., Any], *args, none_is: str = "error") -> Any:
    """Run fn(*args), record it, return its result. The scheduler's step helpers return None on failure (they log
    the exception themselves); `none_is` says what None means for this step ("error" or "skipped")."""
    wall, mono = dt.datetime.utcnow(), time.monotonic()
    try:
        res = fn(*args)
    except Exception as exc:
        _write([_row(job, wall, (time.monotonic() - mono) * 1000, "error", None, f"{type(exc).__name__} — xem log")])
        raise
    ms = (time.monotonic() - mono) * 1000
    if res is None:
        _write([_row(job, wall, ms, none_is, None, "Lỗi — xem log của lần chạy này (run_id)." if none_is == "error" else None)])
    else:
        _write([_row(job, wall, ms, "ok", counters_of(res))])
    return res


def skipped(job: str, reason: str) -> None:
    _write([_row(job, dt.datetime.utcnow(), 0, "skipped", None, reason)])


def record_round(results: List[Dict[str, Any]], seconds: float) -> None:
    ok = sum(1 for r in results if r.get("ok"))
    skipped_n = sum(1 for r in results if r.get("skipped"))
    failed = len(results) - ok - skipped_n
    started = dt.datetime.utcnow() - dt.timedelta(seconds=seconds)
    _write([_row("poll", started, seconds * 1000, "error" if failed else "ok",
                 {"sources": len(results), "ok": ok, "failed": failed, "skipped": skipped_n},
                 f"{failed} nguồn lỗi" if failed else None)])


def out(r) -> Dict[str, Any]:
    """A row for MCP / API (error masked for the AI)."""
    return {"job": r.job, "family": r.family, "key": r.source_key, "trigger": r.trigger, "runId": r.run_id, "pod": r.pod,
            "startedAt": r.started_at.replace(microsecond=0).isoformat() + "Z", "durationMs": r.duration_ms,
            "status": r.status, "counters": json.loads(r.counters) if r.counters else None,
            "error": observability.mask_infra(r.error) if r.error else None}
