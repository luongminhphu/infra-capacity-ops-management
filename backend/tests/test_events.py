"""ICO v2 G5: event store transitions, incident lifecycle, failed source kept open, flapping, MTTA / MTTR, API."""
import datetime as dt

import pytest

from app.cmdb.models import Ci
from app.database import SessionLocal
from app.events import service as ev
from app.events.models import OpsEvent, OpsEventOpen, OpsIncident
from app.models import AuditLog
from test_spend_opex import admin, app_  # noqa: F401  (fixtures)

T0 = dt.datetime(2026, 11, 2, 1, 0)


def _wipe(db):
    for m in (OpsEvent, OpsEventOpen, OpsIncident):
        db.query(m).delete()
    db.query(Ci).filter(Ci.source_key.like("g5vc:%")).delete(synchronize_session=False)
    db.commit()


@pytest.fixture()
def db(app_):
    s = SessionLocal()
    _wipe(s)
    yield s
    _wipe(s)
    s.close()


def _ci(db, name="esx-g5", typ="hypervisor_host", key="g5vc:esx-g5"):
    c = Ci(type=typ, source_kind="vcenter", source_key=key, name=name, status="active", attrs='{"vcenterKey": "g5vc"}')
    db.add(c)
    db.commit()
    return c.id


def _round(db, level, ci_id=None, minutes=0, source="health", key="hh|vc1|esx-g5", unknown=()):
    cur = {source: ({key: (level, ci_id, "Host esx-g5: phần cứng", "PSU 1")} if level != "ok" else {})}
    return ev.record_transitions(db, cur, unknown, now=T0 + dt.timedelta(minutes=minutes))


def test_ok_bad_crit_bad_ok_gives_four_events_and_one_resolved_incident(db):
    cid = _ci(db)
    for i, lv in enumerate(("ok", "bad", "crit", "bad", "ok")):
        _round(db, lv, cid, minutes=10 * i)
    assert [e.transition for e in db.query(OpsEvent).order_by(OpsEvent.id)] == ["open", "worse", "better", "clear"]
    incs = db.query(OpsIncident).all()
    assert len(incs) == 1
    i = incs[0]
    assert (i.status, i.resolved_by, i.peak_level, i.ci_id, i.event_count) == ("resolved", "system", "crit", cid, 1)
    assert i.resolved_at - i.opened_at == dt.timedelta(minutes=30)
    assert db.query(OpsEventOpen).count() == 0


def test_same_round_twice_writes_nothing_new(db):
    _round(db, "bad")
    _round(db, "bad", minutes=5)
    assert db.query(OpsEvent).count() == 1


def test_events_of_one_ci_join_one_incident(db):
    cid = _ci(db)
    _round(db, "bad", cid)
    _round(db, "crit", cid, source="alarm", key="alarm|vc1|a1")
    _round(db, "bad", cid, minutes=1)               # health still open (alarm source untouched: not in this round)
    assert db.query(OpsIncident).count() == 1
    assert db.query(OpsIncident).one().event_count == 2
    assert db.query(OpsIncident).one().peak_level == "crit"


def test_failed_source_keeps_its_events_open(db):
    _round(db, "bad")
    _round(db, "ok", minutes=5, unknown=("health",))
    assert db.query(OpsEventOpen).count() == 1
    assert db.query(OpsIncident).one().status == "open"
    assert [e.transition for e in db.query(OpsEvent)] == ["open"]


def test_flapping_after_four_opens_in_six_hours_alerts_once(db, monkeypatch):
    for i in range(4):
        _round(db, "bad", minutes=60 * i)
        _round(db, "ok", minutes=60 * i + 30)
    flapping = db.query(OpsIncident).filter(OpsIncident.flapping.is_(True)).all()
    assert len(flapping) == 1 and db.query(OpsIncident).count() == 4
    from app import ops_alerts, secrets_box
    from types import SimpleNamespace as NS
    monkeypatch.setattr(ops_alerts, "_channels", lambda _db: [NS(id=1, webhook_enc=secrets_box.encrypt("https://discord.com/api/webhooks/1/abc"))])
    sent = []
    assert ev.notify_flapping(db, post=lambda url, payload: sent.append(payload)) == 1
    assert ev.notify_flapping(db, post=lambda url, payload: sent.append(payload)) == 0
    assert len(sent) == 1 and "chập chờn" in sent[0]["content"]


def test_five_opens_outside_the_window_do_not_flap(db):
    for i in range(5):
        _round(db, "bad", minutes=200 * i)
        _round(db, "ok", minutes=200 * i + 30)
    assert db.query(OpsIncident).filter(OpsIncident.flapping.is_(True)).count() == 0


def test_mtta_mttr_per_month_and_source(db):
    db.add_all([
        OpsIncident(group_key="a", source="health", title="A", status="resolved", peak_level="crit", opened_at=T0,
                    acked_at=T0 + dt.timedelta(minutes=10), resolved_at=T0 + dt.timedelta(minutes=60), event_count=1),
        OpsIncident(group_key="b", source="health", title="B", status="resolved", peak_level="bad", opened_at=T0,
                    acked_at=T0 + dt.timedelta(minutes=20), resolved_at=T0 + dt.timedelta(minutes=120), event_count=1),
        OpsIncident(group_key="c", source="datastore", title="C", status="open", peak_level="warn", opened_at=T0, event_count=1)])
    db.commit()
    s = ev.stats(db, months=1, now=T0 + dt.timedelta(days=1))
    month = s["byMonth"][0]
    assert (month["key"], month["incidents"], month["crit"], month["open"], month["mttaMin"], month["mttrMin"]) == \
        ("2026-11", 3, 1, 1, 15.0, 90.0)
    by_src = {r["key"]: r for r in s["bySource"]}
    assert by_src["health"]["mttrMin"] == 90.0 and by_src["datastore"]["mttrMin"] is None


def test_items_resolve_to_ci_and_api_flow(db, admin):  # noqa: F811
    cid = _ci(db)
    items = [{"source": "health", "level": "bad", "alertKey": "hh|vc1|esx-g5", "title": "Host esx-g5", "detail": "",
              "ci": {"type": "hypervisor_host", "key": "g5vc:esx-g5"}},
             {"source": "alarm", "level": "crit", "alertKey": "alarm|vc1|x", "title": "Alarm", "detail": "",
              "ci": {"type": "hypervisor_host", "name": "ESX-G5", "vc": "g5vc"}},
             {"source": "spend", "level": "crit", "alertKey": "sp", "title": "not watched"}]
    counts = ev.run(db, items, errors=[])
    assert counts["open"] == 2
    inc = db.query(OpsIncident).one()
    assert inc.ci_id == cid and inc.event_count == 2

    r = admin.get("/api/incidents?status=active").json()
    assert [i["id"] for i in r["items"]] == [inc.id] and r["items"][0]["ci"]["name"] == "esx-g5"
    d = admin.get(f"/api/incidents/{inc.id}").json()
    assert {e["transition"] for e in d["events"]} == {"open"}
    assert admin.post(f"/api/incidents/{inc.id}/ack").json()["status"] == "acked"
    assert admin.post(f"/api/incidents/{inc.id}/ack").status_code == 409
    assert admin.post(f"/api/incidents/{inc.id}/assign", json={"assignee": "noc"}).json()["assignee"] == "noc"
    assert admin.post(f"/api/incidents/{inc.id}/resolve", json={"note": "Đã thay PSU"}).json()["status"] == "resolved"
    assert admin.post(f"/api/incidents/{inc.id}/delete").status_code == 422
    assert db.query(AuditLog).filter(AuditLog.action.like("incident.%")).count() >= 3
    assert len(admin.get(f"/api/ci/{cid}/timeline").json()["items"]) == 2
    assert admin.get("/api/incidents/999999").status_code == 404
    assert admin.get("/api/incidents/stats?months=2").status_code == 200


def test_ensure_schema_adds_ai_runs_cost_column(app_):  # noqa: F811
    """Audit: a duplicate "ai_runs" key in the additive dict dropped cost_usd_micros on upgraded databases."""
    from sqlalchemy import inspect, text
    from app.database import engine, ensure_schema
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE ai_runs DROP COLUMN cost_usd_micros"))
    ensure_schema()
    assert "cost_usd_micros" in {c["name"] for c in inspect(engine).get_columns("ai_runs")}
