"""Time series in tiers: raw points (METRICS_RAW_DAYS, default 7) → hourly (90 days) → daily (3 years).

Collectors call `record()` once per poll; the leader's scheduler round calls `maybe_rollup()`, which at
most once per ROLLUP_EVERY recomputes the hourly buckets of the last few hours and the daily buckets of
today / yesterday from the raw points, then deletes what is past each tier's retention. Readers call
`series()`: it picks the finest tier that still covers the requested window and appends the raw points
newer than the last rollup, so a chart over 30 days is ~720 hourly values instead of ~2 900 raw ones.

Rollups are recomputed from raw (idempotent), so a missed or repeated run never double-counts.
"""
import datetime as dt
import logging
import threading
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from .config import get_settings
from .models import MetricPoint, MetricRollup

logger = logging.getLogger(__name__)

ROLLUP_EVERY = dt.timedelta(minutes=55)
HOURS_BACK = 3                      # hourly buckets recomputed each run (covers a missed run or two)
_last_run: Optional[dt.datetime] = None
_run_lock = threading.Lock()

Point = Tuple[str, str, float]      # (entity, metric, value)


def record(db: Session, kind: str, points: Iterable[Point], at: Optional[dt.datetime] = None) -> int:
    """Bulk insert one poll's values (non-finite values are skipped). Caller commits."""
    at = at or dt.datetime.utcnow()
    rows = [{"kind": kind, "entity": str(e)[:255], "metric": m, "at": at, "value": float(v)}
            for e, m, v in points if v is not None and v == v and abs(float(v)) != float("inf")]
    if rows:
        db.bulk_insert_mappings(MetricPoint, rows)
    return len(rows)


def _floor(t: dt.datetime, period: str) -> dt.datetime:
    return t.replace(minute=0, second=0, microsecond=0) if period == "hour" else t.replace(hour=0, minute=0, second=0, microsecond=0)


def _upsert(db: Session, period: str, buckets: Dict[Tuple[str, str, str, dt.datetime], List[Tuple[dt.datetime, float]]]) -> int:
    if not buckets:
        return 0
    starts = {k[3] for k in buckets}
    existing = {(r.kind, r.entity, r.metric, r.start): r for r in db.query(MetricRollup)
                .filter(MetricRollup.period == period, MetricRollup.start.in_(starts))}
    n = 0
    for key, vals in buckets.items():
        vals.sort()
        nums = [v for _, v in vals]
        row = existing.get(key)
        if row is None:
            row = MetricRollup(kind=key[0], entity=key[1], metric=key[2], period=period, start=key[3])
            db.add(row)
        row.count, row.sum, row.min, row.max, row.last = len(nums), sum(nums), min(nums), max(nums), nums[-1]
        n += 1
    return n


def rollup(db: Session, now: Optional[dt.datetime] = None) -> Dict[str, int]:
    """Recompute recent hourly / daily buckets from raw points and purge past retention. Caller commits."""
    s = get_settings()
    now = now or dt.datetime.utcnow()
    since = _floor(now, "day") - dt.timedelta(days=1)                 # yesterday 00:00: covers both tiers
    hour_from = _floor(now, "hour") - dt.timedelta(hours=HOURS_BACK)
    hourly: Dict[Tuple[str, str, str, dt.datetime], List[Tuple[dt.datetime, float]]] = defaultdict(list)
    daily: Dict[Tuple[str, str, str, dt.datetime], List[Tuple[dt.datetime, float]]] = defaultdict(list)
    q = (db.query(MetricPoint.kind, MetricPoint.entity, MetricPoint.metric, MetricPoint.at, MetricPoint.value)
         .filter(MetricPoint.at >= since).yield_per(5000))
    for kind, entity, metric, at, value in q:
        if at >= hour_from:
            hourly[(kind, entity, metric, _floor(at, "hour"))].append((at, value))
        daily[(kind, entity, metric, _floor(at, "day"))].append((at, value))
    out = {"hour": _upsert(db, "hour", hourly), "day": _upsert(db, "day", daily)}
    out["purgedRaw"] = db.query(MetricPoint).filter(MetricPoint.at < now - dt.timedelta(days=s.metrics_raw_days)).delete(synchronize_session=False)
    out["purgedHour"] = (db.query(MetricRollup).filter(MetricRollup.period == "hour",
                         MetricRollup.start < now - dt.timedelta(days=s.metrics_hourly_days)).delete(synchronize_session=False))
    out["purgedDay"] = (db.query(MetricRollup).filter(MetricRollup.period == "day",
                        MetricRollup.start < now - dt.timedelta(days=s.metrics_daily_days)).delete(synchronize_session=False))
    return out


def maybe_rollup(now: Optional[dt.datetime] = None) -> Optional[Dict[str, int]]:
    """At most once per ROLLUP_EVERY in this process (the leader's round); never raises."""
    global _last_run
    from .database import SessionLocal
    now = now or dt.datetime.utcnow()
    if not _run_lock.acquire(blocking=False):
        return None
    try:
        if _last_run is not None and now - _last_run < ROLLUP_EVERY:
            return None
        with SessionLocal() as db:
            out = rollup(db, now)
            db.commit()
        _last_run = now
        return out
    except Exception:  # noqa: BLE001 — never stops polling; retried next round
        logger.exception("Metric rollup failed; will retry next round")
        return None
    finally:
        _run_lock.release()


def series(db: Session, kind: str, entities: Sequence[str], metric: str, since: dt.datetime,
           now: Optional[dt.datetime] = None) -> Dict[str, List[Tuple[dt.datetime, float]]]:
    """{entity: [(time, value), …]} oldest first. Raw points when the window fits in raw retention,
    else hourly (window ≤ hourly retention) or daily averages, plus raw points newer than the last bucket."""
    s = get_settings()
    now = now or dt.datetime.utcnow()
    out: Dict[str, List[Tuple[dt.datetime, float]]] = {e: [] for e in entities}
    if not entities:
        return out
    span = now - since
    if span <= dt.timedelta(days=s.metrics_raw_days):
        rows = (db.query(MetricPoint.entity, MetricPoint.at, MetricPoint.value)
                .filter(MetricPoint.kind == kind, MetricPoint.metric == metric, MetricPoint.entity.in_(list(entities)),
                        MetricPoint.at >= since).order_by(MetricPoint.at))
        for e, at, v in rows:
            out[e].append((at, v))
        return out
    period = "hour" if span <= dt.timedelta(days=s.metrics_hourly_days) else "day"
    last_start: Dict[str, dt.datetime] = {}
    rows = (db.query(MetricRollup.entity, MetricRollup.start, MetricRollup.sum, MetricRollup.count)
            .filter(MetricRollup.kind == kind, MetricRollup.metric == metric, MetricRollup.period == period,
                    MetricRollup.entity.in_(list(entities)), MetricRollup.start >= _floor(since, period))
            .order_by(MetricRollup.start))
    step = dt.timedelta(hours=1) if period == "hour" else dt.timedelta(days=1)
    for e, start, total, count in rows:
        out[e].append((start, total / count if count else 0.0))
        last_start[e] = start
    tail_from = min((t + step for t in last_start.values()), default=since)
    raw = (db.query(MetricPoint.entity, MetricPoint.at, MetricPoint.value)
           .filter(MetricPoint.kind == kind, MetricPoint.metric == metric, MetricPoint.entity.in_(list(entities)),
                   MetricPoint.at >= tail_from).order_by(MetricPoint.at))
    for e, at, v in raw:
        if e not in last_start or at >= last_start[e] + step:
            out[e].append((at, v))
    return out


def linear_forecast(points: Sequence[Tuple[dt.datetime, float]], limit: float, min_days: float = 3.0) -> Optional[Dict[str, float]]:
    """Least-squares trend of a growing value; {"perDay", "daysToLimit"} or None when there is too little
    history (< min_days) or no growth. daysToLimit is measured from the last point."""
    if len(points) < 3:
        return None
    t0 = points[0][0]
    xs = [(t - t0).total_seconds() / 86400 for t, _ in points]
    if xs[-1] - xs[0] < min_days:
        return None
    ys = [v for _, v in points]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    if slope <= 0:
        return {"perDay": slope, "daysToLimit": None}
    last_fit = my + slope * (xs[-1] - mx)
    return {"perDay": slope, "daysToLimit": max(0.0, (limit - last_fit) / slope)}
