"""Clean-up: secrets re-encrypted with SECRETS_KEY without retyping; data of sources no longer configured."""
import datetime as dt

import pytest

from app import orphans, secrets_box, sources
from app.config import get_settings
from app.database import SessionLocal
from app.models import (
    AuditLog, BackupJob, BackupSyncRun, DataSource, DatastoreState, HostSnapshot, MetricPoint, SnapshotRun,
    StorageArrayState, VmSnapshot,
)

from test_sources import ADMIN, SECRET, _vc, client  # noqa: F401  (fixture)


# ---------------------------------------------------------------- SECRETS_KEY rotation
def test_secrets_saved_before_secrets_key_stay_readable_and_are_reencrypted(client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "secrets_key", "")                       # saved with the JWT_SECRET-derived key
    sid = client.post("/api/admin/sources", json=_vc()).json()["id"]
    db = SessionLocal()
    legacy = db.get(DataSource, sid).password_enc
    db.close()
    st = client.get("/api/admin/sources").json()
    assert st["secretsKeyConfigured"] is False and st["secrets"] == {"current": 1, "previous": 0, "unreadable": 0}

    monkeypatch.setattr(s, "secrets_key", "new-dedicated-key-0123456789")   # admin sets SECRETS_KEY, pod restarts
    sources.invalidate_cache()
    assert secrets_box.decrypt(legacy) == SECRET                    # still readable before re-encryption
    assert client.get("/api/admin/sources").json()["secrets"] == {"current": 0, "previous": 1, "unreadable": 0}
    secrets_box.rotate_at_startup()
    db = SessionLocal()
    token = db.get(DataSource, sid).password_enc
    db.close()
    assert token != legacy and SECRET not in token
    assert client.get("/api/admin/sources").json()["secrets"] == {"current": 1, "previous": 0, "unreadable": 0}
    # the old key is no longer needed: JWT_SECRET may change now
    monkeypatch.setattr(s, "jwt_secret", "a-completely-different-jwt-secret")
    assert secrets_box.decrypt(token) == SECRET
    assert next(c for c in s.vcenters() if c.key == "vc-ui-01").password == SECRET


def test_replacing_secrets_key_with_previous_list(client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "secrets_key", "key-one")
    sid = client.post("/api/admin/sources", json=_vc()).json()["id"]
    monkeypatch.setattr(s, "secrets_key", "key-two")
    assert client.get("/api/admin/sources").json()["secrets"]["unreadable"] == 1      # key-one forgotten
    monkeypatch.setattr(s, "secrets_key_previous", "key-zero, key-one")
    r = client.post("/api/admin/sources/rotate-secrets").json()
    assert r == {"rotated": 1, "current": 0, "unreadable": 0}
    monkeypatch.setattr(s, "secrets_key_previous", "")
    db = SessionLocal()
    assert secrets_box.decrypt(db.get(DataSource, sid).password_enc) == SECRET
    assert db.query(AuditLog).filter_by(action="secrets.rotate").count() >= 1
    db.close()
    monkeypatch.setattr(s, "secrets_key", "")
    assert client.post("/api/admin/sources/rotate-secrets").status_code == 409


# ---------------------------------------------------------------- orphan data
@pytest.fixture()
def seeded():
    now = dt.datetime.utcnow()
    db = SessionLocal()
    for key in ("vc-old", "vc-ui-01"):
        run = SnapshotRun(vcenter_key=key, source="live", status="ok", started_at=now - dt.timedelta(days=2), finished_at=now)
        db.add(run)
        db.flush()
        db.add_all([HostSnapshot(run_id=run.id, vcenter_key=key, host=f"esx-{key}", captured_at=now),
                    VmSnapshot(run_id=run.id, vcenter_key=key, vm=f"vm-{key}", captured_at=now),
                    VmSnapshot(run_id=run.id, vcenter_key=key, vm=f"vm2-{key}", captured_at=now),
                    DatastoreState(vcenter_key=key, ds_key="ds1", name="DS1", captured_at=now)])
        db.add(MetricPoint(kind="datastore", entity=f"{key}:ds1", metric="usedGB", at=now, value=1.0))
    imp = SnapshotRun(vcenter_key=None, source="import", status="ok", started_at=now)  # imported data: never orphan
    db.add(imp)
    db.flush()
    db.add(VmSnapshot(run_id=imp.id, vcenter_key=None, vm="vm-import", captured_at=now))
    imp_id = imp.id
    brun = BackupSyncRun(nbu_key="nbu-gone", status="ok", started_at=now)
    db.add(brun)
    db.flush()
    db.add(BackupJob(nbu_key="nbu-gone", job_id=1, run_id=brun.id) if hasattr(BackupJob, "run_id") else BackupJob(nbu_key="nbu-gone", job_id=1))
    db.add(StorageArrayState(source_key="pure-gone", vendor="pure", name="FA", captured_at=now))
    db.add(MetricPoint(kind="array", entity="pure-gone", metric="iops", at=now, value=5.0))
    db.add(MetricPoint(kind="datastore", entity="vc_old:ds1", metric="usedGB", at=now, value=1.0))   # "_" is not a wildcard
    db.commit()
    db.close()
    yield
    db = SessionLocal()                     # only what this fixture added: other tests' history stays
    keys = ("vc-old", "vc-ui-01")
    for m in (VmSnapshot, HostSnapshot, DatastoreState):
        db.query(m).filter(m.vcenter_key.in_(keys)).delete(synchronize_session=False)
    db.query(VmSnapshot).filter_by(vm="vm-import").delete(synchronize_session=False)
    db.query(SnapshotRun).filter(SnapshotRun.vcenter_key.in_(keys)).delete(synchronize_session=False)
    db.query(SnapshotRun).filter(SnapshotRun.id == imp_id).delete(synchronize_session=False)
    db.query(BackupJob).filter_by(nbu_key="nbu-gone").delete(synchronize_session=False)
    db.query(BackupSyncRun).filter_by(nbu_key="nbu-gone").delete(synchronize_session=False)
    db.query(StorageArrayState).filter_by(source_key="pure-gone").delete(synchronize_session=False)
    db.query(MetricPoint).filter(MetricPoint.entity.in_(("vc-old:ds1", "vc-ui-01:ds1", "pure-gone", "vc_old:ds1"))).delete(synchronize_session=False)
    db.commit()
    db.close()


def test_scan_lists_keys_without_a_source_and_purge_removes_only_them(client, seeded):
    client.post("/api/admin/sources", json=_vc())                  # vc-ui-01 configured
    mine = {("vcenter", "vc-old"), ("vcenter", "vc_old"), ("netbackup", "nbu-gone"), ("storage", "pure-gone"), ("vcenter", "vc-ui-01")}
    items = {(o["family"], o["key"]): o for o in client.get("/api/admin/sources/orphans").json()["items"]}
    assert mine - set(items) == {("vcenter", "vc-ui-01")}               # configured source is not listed
    old = items[("vcenter", "vc-old")]
    assert old["rows"] == 6 and {t["label"]: t["rows"] for t in old["tables"]} == {
        "VM (lịch sử)": 2, "Host (lịch sử)": 1, "Lần đồng bộ": 1, "Datastore": 1, "Chuỗi số liệu": 1}
    assert old["lastSeen"] is not None
    # confirmation and configured keys are enforced
    assert client.post("/api/admin/sources/orphans/purge", json={"family": "vcenter", "key": "vc-old", "confirm": "x"}).status_code == 400
    assert client.post("/api/admin/sources/orphans/purge",
                       json={"family": "vcenter", "key": "vc-ui-01", "confirm": "vc-ui-01"}).status_code == 409
    r = client.post("/api/admin/sources/orphans/purge", json={"family": "vcenter", "key": "vc-old", "confirm": "vc-old"}).json()
    assert r["rows"] == 6
    db = SessionLocal()
    assert db.query(VmSnapshot).filter_by(vcenter_key="vc-old").count() == 0
    assert db.query(VmSnapshot).filter_by(vcenter_key="vc-ui-01").count() == 2        # configured source untouched
    assert db.query(VmSnapshot).filter(VmSnapshot.vcenter_key.is_(None)).count() == 1  # imported data untouched
    assert db.query(MetricPoint).filter_by(entity="vc_old:ds1").count() == 1           # LIKE escapes "_"
    assert db.query(AuditLog).filter_by(action="sources.orphan_purge").count() >= 1
    db.close()
    assert client.post("/api/admin/sources/orphans/purge",
                       json={"family": "storage", "key": "pure-gone", "confirm": "pure-gone"}).json()["rows"] == 2
    assert client.post("/api/admin/sources/orphans/purge",
                       json={"family": "netbackup", "key": "nbu-gone", "confirm": "nbu-gone"}).json()["rows"] == 2
    left = {(o["family"], o["key"]) for o in client.get("/api/admin/sources/orphans").json()["items"]}
    assert left & mine == {("vcenter", "vc_old")}


def test_orphan_endpoints_are_admin_only(client):
    from fastapi.testclient import TestClient
    from app.main import app
    from test_spend_opex import _user, PW
    _user("viewer1", "viewer")
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "viewer1", "password": PW}).status_code == 200
    assert c.get("/api/admin/sources/orphans").status_code == 403
    assert c.post("/api/admin/sources/orphans/purge", json={"family": "vcenter", "key": "x", "confirm": "x"}).status_code == 403
    assert c.post("/api/admin/sources/rotate-secrets").status_code == 403
