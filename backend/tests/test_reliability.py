"""Background job reliability (audit 2026-11-15, B6): vCenter timeouts and per-source deadline,
per-source single-flight sync lock, readiness probe, ops alerts that never report a false
"Đã hết", NetBackup page cap warning, log flush that survives a DB failure."""
import datetime as dt
import socket
import threading
import time
from types import SimpleNamespace as NS

import pytest
from sqlalchemy.exc import OperationalError

from app import inbox, observability, ops_alerts, secrets_box
from app.backup import client as nbu_client
from app.backup.collector import _insert_new_jobs
from app.collectors import scheduler
from app.collectors.locks import BUSY_MESSAGE, SourceBusy, source_lock
from app.config import NetBackupConfig, VCenterConfig, get_settings
from app.database import SessionLocal
from app.models import BackupJob, OpsAlertState
from app.spend.models import SpendNotifyChannel
from app.vcenter import client as vc

from test_spend_opex import admin, app_  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True, scope="module")
def _schema():
    from app.database import ensure_schema
    ensure_schema()


# ---------------------------------------------------------------------------
# REL-01 / REL-05: vCenter timeouts
# ---------------------------------------------------------------------------
def _silent_server():
    """A TCP server that accepts connections and never answers (a hung vCenter)."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    conns = []
    stop = threading.Event()

    def accept():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                conns.append(srv.accept()[0])
            except OSError:
                pass
    threading.Thread(target=accept, daemon=True).start()

    def close():
        stop.set()
        for c in conns:
            c.close()
        srv.close()
    return srv.getsockname()[1], close


def test_connect_to_hung_vcenter_times_out_with_vietnamese_message():
    port, close = _silent_server()
    cfg = VCenterConfig("vc-hung", "VC treo", "127.0.0.1", port, "u", "S3cretPw!", False)
    try:
        t0 = time.monotonic()
        with pytest.raises(vc.VCenterConnectionError) as ei:
            vc._connect(cfg, timeout=1)    # hangs forever without the version-discovery timeout
        assert time.monotonic() - t0 < 10
        assert "VC treo" in str(ei.value) and "không phản hồi trong 1 giây" in str(ei.value)
        assert "S3cretPw!" not in str(ei.value)
    finally:
        close()


def test_timeout_is_configurable_and_passed_to_smartconnect(monkeypatch):
    seen = []
    monkeypatch.setattr(vc, "SmartConnect", lambda **kw: seen.append(kw["httpConnectionTimeout"]) or NS())
    vc._connect(VCenterConfig("a", "A", "h", 443, "u", "p", False))
    vc._connect(VCenterConfig("b", "B", "h", 443, "u", "p", False, timeout=45))
    assert seen == [get_settings().vcenter_timeout_seconds, 45] and seen[0] == 120

    def boom(**kw):
        raise socket.timeout("timed out")
    monkeypatch.setattr(vc, "SmartConnect", boom)
    with pytest.raises(vc.VCenterConnectionError, match="không phản hồi trong 45 giây"):
        vc._connect(VCenterConfig("b", "B", "h", 443, "u", "p", False, timeout=45))


def test_network_error_does_not_fall_back_to_per_object_reads(monkeypatch):
    class TimingOut:
        def RetrievePropertiesEx(self, specs, options):
            raise socket.timeout("timed out")

    from test_sync import _content
    lazy_calls = []
    monkeypatch.setattr(vc, "_retrieve_lazy", lambda *a: lazy_calls.append(a) or {})
    monkeypatch.setattr(vc, "_connect", lambda cfg: NS(RetrieveContent=lambda: _content(TimingOut())))
    monkeypatch.setattr(vc, "Disconnect", lambda si: None)
    with pytest.raises(vc.VCenterConnectionError, match="không phản hồi"):
        vc.collect_inventory(VCenterConfig("vc-t", "VC T", "h", 443, "u", "p", False, timeout=30))
    assert lazy_calls == []


def test_hung_vcenter_does_not_block_other_sources(monkeypatch):
    s = get_settings()
    release = threading.Event()
    good = VCenterConfig("vc-rel-good", "VC good", "", 443, "", "", False, demo=True)
    hung = VCenterConfig("vc-rel-hung", "VC hung", "hung.invalid", 443, "u", "p", False)

    def hang(cfg, timeout=None):
        release.wait(30)
        raise vc.VCenterConnectionError("released")
    monkeypatch.setattr(vc, "_connect", hang)
    monkeypatch.setattr(type(s), "vcenters", lambda self: [hung, good])
    monkeypatch.setattr(type(s), "netbackups", lambda self: [])
    monkeypatch.setattr(scheduler, "deadline_seconds", lambda settings=None: 1.0)
    try:
        t0 = time.monotonic()
        results = scheduler.sync_everything(s)
        assert time.monotonic() - t0 < 8
        by_key = {r["vcenterKey"]: r for r in results}
        assert by_key["vc-rel-good"]["ok"] and by_key["vc-rel-good"]["vmCount"] > 0
        assert by_key["vc-rel-hung"]["ok"] is False and by_key["vc-rel-hung"]["timedOut"]
        assert "Quá hạn" in by_key["vc-rel-hung"]["error"]
        # The abandoned sync still holds its source: the next round skips it instead of piling up.
        again = {r["vcenterKey"]: r for r in scheduler.sync_vcenters(s)}
        assert again["vc-rel-hung"]["skipped"] and again["vc-rel-hung"]["error"] == BUSY_MESSAGE
        assert again["vc-rel-good"]["ok"]
    finally:
        release.set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:            # the abandoned thread ends and frees the source
        try:
            with source_lock("vcenter", "vc-rel-hung"):
                break
        except SourceBusy:
            time.sleep(0.05)
    else:
        pytest.fail("abandoned sync never released its source lock")


def test_run_syncs_keeps_order_and_isolates_errors():
    def fail():
        raise RuntimeError("x")
    out = scheduler.run_syncs([lambda: {"key": "a", "ok": True}, fail, lambda: {"key": "c", "ok": True}],
                              [{"key": "a"}, {"key": "b"}, {"key": "c"}], workers=1, deadline=5)
    assert [r["key"] for r in out] == ["a", "b", "c"] and out[1] == {"key": "b", "ok": False, "error": "Đồng bộ thất bại. Xem log máy chủ để biết chi tiết."}   # internals stay in the log


# ---------------------------------------------------------------------------
# REL-02 / DAT-04: one sync per source; upsert survives a concurrent insert
# ---------------------------------------------------------------------------
def test_manual_sync_while_source_is_syncing_returns_409(admin):  # noqa: F811
    key = get_settings().netbackups()[0].key
    with source_lock("netbackup", key):
        r = admin.post(f"/api/backup/servers/{key}/sync")
        assert r.status_code == 409 and r.json()["detail"] == "Đang đồng bộ nguồn này, thử lại sau."
        res = scheduler.sync_backups(get_settings())
        assert res[0]["skipped"] and res[0]["ok"] is False
    r = admin.post(f"/api/backup/servers/{key}/sync")
    assert r.status_code == 200, r.text


def test_manual_vcenter_sync_while_running_returns_409(admin, monkeypatch):  # noqa: F811
    s = get_settings()
    cfg = VCenterConfig("vc-lock", "VC lock", "", 443, "", "", False, demo=True)
    monkeypatch.setattr(type(s), "vcenters", lambda self: [cfg])
    with source_lock("vcenter", "vc-lock"):
        assert admin.post("/api/vcenters/vc-lock/sync").status_code == 409
    assert admin.post("/api/vcenters/vc-lock/sync").status_code == 200


def test_insert_new_jobs_tolerates_rows_written_concurrently():
    t = dt.datetime(2026, 5, 1, 21)
    key = "nbu-race"
    other = SessionLocal()
    other.add(BackupJob(nbu_key=key, job_id=1, category="backup", status=None, state="ACTIVE", start_time=t,
                        updated_at=t))
    other.commit()
    other.close()
    db = SessionLocal()
    try:
        rows = [{"nbu_key": key, "job_id": 1, "updated_at": t, "category": "backup", "status": 0, "state": "DONE",
                 "start_time": t},
                {"nbu_key": key, "job_id": 2, "updated_at": t, "category": "backup", "status": 0, "state": "DONE",
                 "start_time": t}]
        _insert_new_jobs(db, key, rows)            # job 1 collides with uq_backup_job
        db.commit()
        got = {r.job_id: r for r in db.query(BackupJob).filter_by(nbu_key=key)}
        assert sorted(got) == [1, 2] and got[1].status == 0 and got[1].state == "DONE"
    finally:
        db.query(BackupJob).filter_by(nbu_key=key).delete()
        db.commit()
        db.close()


# ---------------------------------------------------------------------------
# OBS-01: readiness
# ---------------------------------------------------------------------------
def test_ready_200_when_db_ok_and_503_when_db_down(app_, monkeypatch):  # noqa: F811
    from fastapi.testclient import TestClient
    from app import main
    c = TestClient(app_)
    assert c.get("/api/health").status_code == 200
    r = c.get("/api/health/ready")
    assert r.status_code == 200 and r.json()["checks"]["database"]["ok"]

    def down():
        raise OperationalError("SELECT 1", {}, Exception("db-secret-host:5432 refused"))
    monkeypatch.setattr(main.engine, "connect", down)
    r = c.get("/api/health/ready")
    assert r.status_code == 503 and r.json()["checks"]["database"] == {"ok": False, "reason": "database_unavailable"}
    assert "db-secret-host" not in r.text
    assert c.get("/api/health").status_code == 200          # liveness unaffected


def test_ready_503_when_scheduler_heartbeat_is_stale(app_, monkeypatch):  # noqa: F811
    from fastapi.testclient import TestClient
    s = get_settings()
    monkeypatch.setattr(s, "enable_scheduler", True)
    monkeypatch.setattr(scheduler, "_heartbeat", {"at": time.monotonic(), "interval": 300, "rounds": 1})
    c = TestClient(app_)
    assert c.get("/api/health/ready").status_code == 200
    stale = time.monotonic() - (3 * 300 + 2 * scheduler.deadline_seconds(s) + 121)
    monkeypatch.setattr(scheduler, "_heartbeat", {"at": stale, "interval": 300, "rounds": 1})
    r = c.get("/api/health/ready")
    assert r.status_code == 503 and r.json()["checks"]["scheduler"]["reason"] == "scheduler_stale"
    monkeypatch.setattr(scheduler, "_heartbeat", {"at": None, "interval": None, "rounds": 0})
    assert c.get("/api/health/ready").json()["checks"]["scheduler"]["reason"] == "scheduler_not_started"


# ---------------------------------------------------------------------------
# REL-03 / REL-08: ops alerts
# ---------------------------------------------------------------------------
URL1 = "https://discord.com/api/webhooks/123456789012345678/abcDEF_-1"
URL2 = "https://discord.com/api/webhooks/223456789012345678/zzzDEF_-2"


@pytest.fixture()
def alert_db(app_):  # noqa: F811
    db = SessionLocal()
    db.query(OpsAlertState).delete()
    db.query(SpendNotifyChannel).delete()
    db.commit()
    inbox.invalidate()
    yield db
    db.rollback()
    db.query(OpsAlertState).delete()
    db.query(SpendNotifyChannel).delete()
    db.commit()
    db.close()
    inbox.invalidate()


def _channel(db, url, name):
    db.add(SpendNotifyChannel(kind="discord", name=name, enabled=True, ops_alerts=True, created_by="t",
                              webhook_enc=secrets_box.encrypt(url)))
    db.commit()


def test_failed_provider_does_not_resolve_its_alerts(alert_db, monkeypatch):
    db = alert_db
    _channel(db, URL1, "ops")
    state = {"broken": False}

    def backup_items(db):
        if state["broken"]:
            raise RuntimeError("DB chập chờn")
        return [inbox.item("backup", "bad", "bk|cov", "Độ phủ 50%", push=True)]
    monkeypatch.setattr(inbox, "PROVIDERS", [("backup", backup_items, False)])
    posts = []
    post = lambda url, payload: posts.append(payload["content"])  # noqa: E731
    assert ops_alerts.check_and_notify(db, post=post) == {"new": 1, "resolved": 0, "sent": 1, "failed": 0}
    state["broken"] = True
    assert ops_alerts.check_and_notify(db, post=post) == {"new": 0, "resolved": 0, "sent": 0, "failed": 0}
    assert len(posts) == 1 and db.get(OpsAlertState, "bk|cov").active      # no false "Đã hết"
    state["broken"] = False
    assert ops_alerts.check_and_notify(db, post=post)["new"] == 0 and len(posts) == 1   # no re-alert either


def test_failed_channel_gets_the_alert_later(alert_db, monkeypatch):
    db = alert_db
    _channel(db, URL1, "ops1")
    _channel(db, URL2, "ops2")
    monkeypatch.setattr(inbox, "PROVIDERS", [("backup", lambda db: [
        inbox.item("backup", "bad", "bk|cov", "Độ phủ 50%", push=True)], False)])
    from app.spend.notify import NotifyError
    posts = []

    def flaky(url, payload):
        if url == URL2:
            raise NotifyError("Discord trả mã HTTP 500.")
        posts.append((url, payload["content"]))
    assert ops_alerts.check_and_notify(db, post=flaky) == {"new": 1, "resolved": 0, "sent": 1, "failed": 1}

    def bug(url, payload):           # not a NotifyError: still isolated per channel, state committed
        if url == URL2:
            raise KeyError("level")
        posts.append((url, payload["content"]))
    assert ops_alerts.check_and_notify(db, post=bug) == {"new": 1, "resolved": 0, "sent": 0, "failed": 1}
    assert [u for u, _ in posts] == [URL1]                                 # channel 1 not told twice
    ok = lambda url, payload: posts.append((url, payload["content"]))  # noqa: E731
    assert ops_alerts.check_and_notify(db, post=ok) == {"new": 1, "resolved": 0, "sent": 1, "failed": 0}
    assert [u for u, _ in posts] == [URL1, URL2]
    assert ops_alerts.check_and_notify(db, post=ok)["sent"] == 0             # everyone has it
    # Cleared: only channels that got the alert get "Đã hết", once each.
    monkeypatch.setattr(inbox, "PROVIDERS", [("backup", lambda db: [], False)])
    assert ops_alerts.check_and_notify(db, post=ok) == {"new": 0, "resolved": 1, "sent": 2, "failed": 0}
    assert all("Đã hết" in t for _, t in posts[2:]) and len(posts) == 4
    assert ops_alerts.check_and_notify(db, post=ok)["sent"] == 0


# ---------------------------------------------------------------------------
# REL-04: NetBackup page cap is reported
# ---------------------------------------------------------------------------
def test_job_page_cap_becomes_a_warning(monkeypatch, caplog):
    cfg = NetBackupConfig("nbu-cap", "NBU cap", "nbu.invalid", 1556, "key", "", "", "unixpwd", "", False, "8.0")
    calls = []

    def get(self, path, params=None, headers=None):
        calls.append(path)
        if path == "/admin/jobs":
            off = params["page[offset]"]
            return {"data": [{"id": off + i, "attributes": {"jobId": off + i, "jobType": "BACKUP"}}
                             for i in range(nbu_client.PAGE_LIMIT)], "meta": {"pagination": {"next": 1}}}
        return {"data": []}
    monkeypatch.setattr(nbu_client.NetBackupClient, "get", get)
    monkeypatch.setattr(nbu_client.NetBackupClient, "login", lambda self: None)
    monkeypatch.setattr(nbu_client.NetBackupClient, "logout", lambda self: None)
    with caplog.at_level("WARNING", logger="vco.netbackup"):
        jobs, _, _, warnings = nbu_client.collect(cfg, dt.datetime(2026, 1, 1))
    assert calls.count("/admin/jobs") == nbu_client.MAX_JOB_PAGES == 300
    assert len(jobs) == 30000 and warnings[0].startswith("Job: chỉ đọc 30000 job mới nhất")
    assert any("page limit" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# OBS-03: log flush keeps the batch when the DB write fails
# ---------------------------------------------------------------------------
def test_flush_requeues_logs_when_db_write_fails(monkeypatch):
    import app.database as database

    class Broken:
        def add(self, *a):
            pass

        def query(self, *a):
            raise OperationalError("x", {}, Exception("down"))

        def commit(self):
            raise OperationalError("x", {}, Exception("down"))

        def rollback(self):
            pass

        def close(self):
            pass
    entry = {"at": dt.datetime.utcnow(), "level": "WARNING", "logger": "t", "message": "keep-me-rel",
             "exc_type": None, "traceback": None, "fingerprint": "f"}
    with observability._lock:
        observability._pending_logs.append(entry)
    monkeypatch.setattr(database, "SessionLocal", Broken)
    observability.flush()
    with observability._lock:
        kept = [e for e in observability._pending_logs if e["message"] == "keep-me-rel"]
        observability._pending_logs[:] = [e for e in observability._pending_logs if e["message"] != "keep-me-rel"]
    assert len(kept) == 1



# ---------------------------------------------------------------- Round 2 PR3: data correctness

def test_capacity_alert_survives_level_change(alert_db, monkeypatch):
    """BEQ-04: a cluster staying above 90 % that moves THEO DÕI -> VƯỢT NGƯỠNG keeps one alert
    (no false "Đã hết" + new alert); the inbox key still changes so a worsened item reappears."""
    db = alert_db
    _channel(db, URL1, "ops")
    level = {"v": "warn"}

    def cap_items(db):
        return [inbox.item("capacity", level["v"], inbox._key("cap", "C1", level["v"]), "Cluster C1", push=True,
                           alert_key=inbox._key("cap", "C1"))]
    monkeypatch.setattr(inbox, "PROVIDERS", [("capacity", cap_items, False)])
    posts = []
    post = lambda url, payload: posts.append(payload["content"])  # noqa: E731
    assert ops_alerts.check_and_notify(db, post=post)["new"] == 1
    level["v"] = "crit"
    out = ops_alerts.check_and_notify(db, post=post)
    assert out["new"] == 0 and out["resolved"] == 0 and len(posts) == 1
    assert db.get(OpsAlertState, "cap|C1").active


def test_failed_provider_does_not_poison_the_next_one(alert_db, monkeypatch):
    """BEQ-02: each provider runs in a SAVEPOINT; a SQL error in one leaves the session usable."""
    from sqlalchemy import text

    def broken(db):
        db.execute(text("SELECT * FROM no_such_table"))
        return []

    def fine(db):
        db.execute(text("SELECT 1")).scalar()
        return [inbox.item("backup", "bad", "bk|ok", "Vẫn chạy", push=False)]
    monkeypatch.setattr(inbox, "PROVIDERS", [("capacity", broken, False), ("backup", fine, False)])
    items, errors = inbox._compute(alert_db, dt.date.today())
    assert errors == ["capacity"] and [i["key"] for i in items] == ["bk|ok"]
    assert alert_db.execute(text("SELECT 1")).scalar() == 1


def test_alert_runs_are_serialised():
    """BEQ-03: the scheduler run and "Chạy ngay" share one lock."""
    assert ops_alerts._run_lock.acquire(blocking=False)
    ops_alerts._run_lock.release()
