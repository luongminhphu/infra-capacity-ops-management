"""Tiered time series: raw → hourly → daily, retention, series() tier choice, linear forecast."""
import datetime as dt

import pytest

from app import metrics
from app.config import get_settings
from app.database import SessionLocal, engine
from app.models import Base, MetricPoint, MetricRollup

NOW = dt.datetime(2026, 11, 17, 10, 30)


@pytest.fixture()
def db():
    Base.metadata.create_all(bind=engine)
    s = SessionLocal()
    s.query(MetricPoint).delete()
    s.query(MetricRollup).delete()
    s.commit()
    yield s
    s.query(MetricPoint).delete()
    s.query(MetricRollup).delete()
    s.commit()
    s.close()


def test_rollup_hour_and_day_from_raw(db):
    # 4 polls in 09:xx (10, 20, 30, 40 GB used), 2 in 10:xx (50, 60): hourly avg 25 / 55, daily avg 35
    for minute, used in ((0, 10), (15, 20), (30, 30), (45, 40)):
        metrics.record(db, "datastore", [("vc1:ds-1", "usedGB", used)], NOW.replace(hour=9, minute=minute))
    for minute, used in ((0, 50), (15, 60)):
        metrics.record(db, "datastore", [("vc1:ds-1", "usedGB", used)], NOW.replace(hour=10, minute=minute))
    metrics.record(db, "datastore", [("vc1:ds-1", "usedGB", float("nan")), ("vc1:ds-1", "usedGB", None)], NOW)   # skipped
    db.commit()
    out = metrics.rollup(db, NOW)
    db.commit()
    assert out["hour"] == 2 and out["day"] == 1
    h = {r.start.hour: r for r in db.query(MetricRollup).filter_by(period="hour")}
    assert (h[9].count, h[9].sum / h[9].count, h[9].min, h[9].max, h[9].last) == (4, 25.0, 10.0, 40.0, 40.0)
    assert (h[10].count, h[10].last) == (2, 60.0)
    d = db.query(MetricRollup).filter_by(period="day").one()
    assert (d.count, d.sum / d.count, d.last) == (6, 35.0, 60.0)
    # idempotent: a second run recomputes, never doubles
    metrics.rollup(db, NOW)
    db.commit()
    assert db.query(MetricRollup).filter_by(period="day").one().count == 6


def test_retention_per_tier(db):
    s = get_settings()
    old_raw = NOW - dt.timedelta(days=s.metrics_raw_days + 1)
    metrics.record(db, "datastore", [("e", "usedGB", 1)], old_raw)
    db.add(MetricRollup(kind="datastore", entity="e", metric="usedGB", period="hour", start=NOW - dt.timedelta(days=s.metrics_hourly_days + 1),
                        count=1, sum=1, min=1, max=1, last=1))
    db.add(MetricRollup(kind="datastore", entity="e", metric="usedGB", period="day", start=NOW - dt.timedelta(days=s.metrics_hourly_days + 1),
                        count=1, sum=1, min=1, max=1, last=1))           # daily kept (3 years)
    db.commit()
    out = metrics.rollup(db, NOW)
    db.commit()
    assert (out["purgedRaw"], out["purgedHour"], out["purgedDay"]) == (1, 1, 0)


def test_series_picks_tier(db):
    for day in range(10):        # one value per day for 10 days, raw kept 7 days
        at = NOW - dt.timedelta(days=9 - day)
        metrics.record(db, "datastore", [("ds", "usedGB", 100 + day * 10)], at)
        db.add(MetricRollup(kind="datastore", entity="ds", metric="usedGB", period="hour", start=at.replace(minute=0),
                            count=1, sum=100 + day * 10, min=0, max=0, last=0))
    db.commit()
    raw = metrics.series(db, "datastore", ["ds"], "usedGB", NOW - dt.timedelta(days=3), NOW)["ds"]
    assert [v for _, v in raw] == [160.0, 170.0, 180.0, 190.0]           # raw tier (window ≤ 7 days, start inclusive)
    hourly = metrics.series(db, "datastore", ["ds"], "usedGB", NOW - dt.timedelta(days=30), NOW)["ds"]
    assert [v for _, v in hourly] == [100.0 + 10 * i for i in range(10)]   # hourly tier, no duplicate raw tail


def test_linear_forecast_days_to_full():
    pts = [(NOW + dt.timedelta(days=d), 700 + 5 * d) for d in range(0, 31, 3)]   # +5 GB / day, 850 GB at day 30
    f = metrics.linear_forecast(pts, 1000)
    assert round(f["perDay"], 3) == 5.0 and round(f["daysToLimit"], 1) == 30.0     # (1000 − 850) / 5
    assert metrics.linear_forecast(pts[:2], 1000) is None                     # too few points
    assert metrics.linear_forecast([(NOW, 1), (NOW + dt.timedelta(hours=1), 2), (NOW + dt.timedelta(hours=2), 3)], 10) is None  # < 3 days
    flat = [(NOW + dt.timedelta(days=d), 500 - d) for d in range(10)]
    assert metrics.linear_forecast(flat, 1000)["daysToLimit"] is None         # shrinking: never full
