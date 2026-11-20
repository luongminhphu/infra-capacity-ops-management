"""Connection status of every data source (top bar + Settings): state from the last real sync, per-source and
all-source sync now (admin), Phần cứng page tells a never-synced OME source apart from an empty one."""
import datetime as dt

import pytest

from app import source_status
from app.database import SessionLocal
from app.hardware.models import HwComponentHealth, HwDeviceState, HwSourceState
from app.models import DataSource, StorageArrayState
from test_spend_opex import _login, admin, app_, editor  # noqa: F401  (fixtures)

NOW = dt.datetime(2026, 11, 20, 3, 0)
H3 = dt.timedelta(hours=3)


@pytest.mark.parametrize("ok,err,state", [
    (None, None, "never"),
    (NOW - dt.timedelta(minutes=30), None, "ok"),
    (NOW - dt.timedelta(hours=4), None, "stale"),
    (NOW - dt.timedelta(hours=2), NOW - dt.timedelta(minutes=5), "error"),     # failed after the last good sync
    (NOW - dt.timedelta(minutes=5), NOW - dt.timedelta(hours=2), "ok"),        # recovered
    (None, NOW - dt.timedelta(minutes=5), "error"),
])
def test_state_of(ok, err, state):
    assert source_status.state_of(ok, err, NOW, H3) == state


def test_summary_worst_state_per_family():
    items = [{"family": "virtualization", "state": "ok"}, {"family": "virtualization", "state": "error"},
             {"family": "hardware", "state": "never"}, {"family": "storage", "state": "stale"}]
    s = source_status.summary(items)
    assert (s["total"], s["ok"], s["state"], s["level"]) == (4, 1, "error", "crit")
    fams = {f["family"]: (f["ok"], f["total"], f["state"], f["level"]) for f in s["families"]}
    assert fams == {"virtualization": (1, 2, "error", "crit"), "storage": (0, 1, "stale", "warn"),
                    "hardware": (0, 1, "never", "na")}
    assert [f["family"] for f in s["families"]] == ["virtualization", "storage", "hardware"]   # fixed order, no empty family


@pytest.fixture()
def db(app_):
    s = SessionLocal()

    def wipe():
        for m in (HwComponentHealth, HwDeviceState, HwSourceState, StorageArrayState):
            s.query(m).delete()
        s.query(DataSource).filter(DataSource.kind.in_(("ome", "vcenter", "pure"))).delete(synchronize_session=False)
        s.commit()
        from app import sources
        sources.invalidate_cache()
    wipe()
    yield s
    s.rollback()
    wipe()
    s.close()


def _add(admin, **body):
    r = admin.post("/api/admin/sources", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_every_family_has_a_status_and_sync_now(db, admin, editor):
    _add(admin, kind="vcenter", key="vcs", name="VC status", host="", demo=True)
    ome = _add(admin, kind="ome", key="ome-s", name="OME status", host="", demo=True)
    _add(admin, kind="pure", key="pure-s", name="Pure status", host="fa.local", apiKey="tok-123456")
    from app import sources
    sources.invalidate_cache()
    db.add(StorageArrayState(source_key="pure-s", vendor="pure", name="Pure status", last_error="Không kết nối được fa.local:443",
                             last_error_at=dt.datetime.utcnow()))
    db.commit()
    st = editor.get("/api/sources/status")                      # any signed-in user of the full app
    assert st.status_code == 200
    by = {i["key"]: i for i in st.json()["items"]}
    assert by["ome-s"]["state"] == "never" and by["ome-s"]["family"] == "hardware" and by["ome-s"]["kindLabel"] == "Dell OME"
    assert by["pure-s"]["state"] == "error" and by["pure-s"]["error"] == "Không kết nối được fa.local:443"
    assert "host" not in by["pure-s"] and "tok-123456" not in st.text
    listing = admin.get("/api/admin/sources").json()["items"]
    row = next(i for i in listing if i["key"] == "ome-s")
    assert row["sync"]["state"] == "never" and row["sync"]["stateText"] == "Chưa đồng bộ"

    # sync one source now (admin only), whatever its kind
    assert editor.post(f"/api/admin/sources/{ome['id']}/sync").status_code == 403
    r = admin.post(f"/api/admin/sources/{ome['id']}/sync").json()
    assert r["ok"] is True and r["status"]["state"] == "ok" and r["status"]["lastSyncAt"]
    assert db.query(HwDeviceState).filter_by(source_key="ome-s").count() == 12         # 10 Dell hosts of the demo vCenter + 2 bare-metal

    # sync every source now: the demo vCenter + OME work, the unreachable array is reported, not raised
    assert editor.post("/api/sources/sync-all").status_code == 403
    from app.collectors import scheduler
    from app.storage import StorageError

    def unreachable(cfg, peers):
        raise StorageError("Không kết nối được fa.local:443 — timed out")
    import unittest.mock as um
    with um.patch.object(scheduler, "_sync_storage_task", unreachable):
        out = admin.post("/api/sources/sync-all").json()
    res = {(x.get("vcenterKey") or x.get("key")): x for x in out["results"]}
    assert res["vcs"]["ok"] and res["ome-s"]["ok"] and res["pure-s"]["ok"] is False and "timed out" in res["pure-s"]["error"]
    fams = {f["family"]: f for f in out["summary"]["families"]}
    assert fams["virtualization"]["state"] == "ok" and fams["hardware"]["ok"] == 1


def test_disabled_source_has_no_sync_state(db, admin):
    r = _add(admin, kind="ome", key="ome-off", name="OME off", host="", demo=True)
    assert admin.put(f"/api/admin/sources/{r['id']}", json={**r, "enabled": False}).status_code == 200
    row = next(i for i in admin.get("/api/admin/sources").json()["items"] if i["key"] == "ome-off")
    assert row["sync"] is None
    assert admin.post(f"/api/admin/sources/{r['id']}/sync").status_code == 409


def test_hardware_page_lists_a_never_synced_source(db, admin):
    _add(admin, kind="ome", key="ome-new", name="OME mới", host="", demo=True)
    from app import sources
    sources.invalidate_cache()
    d = admin.get("/api/hardware").json()
    assert d["configured"] is True and d["summary"]["servers"] == 0
    assert d["sources"] == [{"key": "ome-new", "name": "OME mới", "devices": None, "capturedAt": None, "never": True}]
