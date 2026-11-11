"""Backup module: NetBackup payload parsing, analysis criteria, demo sync and API."""
import datetime as dt

from app.backup import analysis, client, status_codes

NOW = dt.datetime(2026, 11, 11, 3, 0, 0)  # 10:00 VN


# ---------------------------------------------------------------------------
# Parsers (shapes of the NetBackup REST API)
# ---------------------------------------------------------------------------
def test_parse_job_payload():
    j = client.parse_job({"type": "job", "id": "101", "attributes": {
        "jobId": 101, "parentJobId": 101, "jobType": "BACKUP", "policyName": "P1", "policyType": "VMware",
        "scheduleType": "FULL", "clientName": "App01.corp.local", "state": "DONE", "status": 1,
        "startTime": "2026-11-10T15:00:00.000Z", "endTime": "2026-11-10T16:30:00.000Z", "elapsedTime": "PT1H30M",
        "kilobytesTransferred": 1048576}})
    assert j["jobId"] == 101 and j["parentJobId"] is None  # parentJobId == jobId means "no parent"
    assert j["category"] == "backup" and j["elapsedSec"] == 5400
    assert j["startTime"] == dt.datetime(2026, 11, 10, 15, 0) and j["status"] == 1
    running = client.parse_job({"attributes": {"jobId": 5, "jobType": "BACKUP", "state": "ACTIVE", "status": 0,
                                               "startTime": "2026-11-10T15:00:00Z", "endTime": "1970-01-01T00:00:00Z"}})
    assert running["status"] is None and running["endTime"] is None
    assert client.parse_job({"attributes": {"jobId": 6, "jobType": "DUPLICATION"}})["category"] == "copy"


def test_parse_policy_and_pool():
    p = client.parse_policy({"type": "policy", "id": "WIN-FS", "attributes": {"policy": {
        "policyName": "WIN-FS", "policyType": "MS-Windows", "policyAttributes": {"active": False, "storage": "stu1"},
        "clients": [{"hostName": "fs01"}, {"hostName": "fs02"}],
        "schedules": [{"scheduleName": "Full", "scheduleType": "FULL", "frequencySeconds": 604800,
                       "backupCopies": {"copies": [{"retentionPeriod": {"value": 4, "unit": "WEEKS"}}]}}]}}})
    assert p["active"] is False and p["clients"] == ["fs01", "fs02"] and p["schedules"][0]["retention"] == "4 weeks"
    pool = client.parse_disk_pool({"id": "dp1", "attributes": {"name": "dp1", "sType": "PureDisk", "usableSizeBytes": 1000,
                                                               "availableSpaceBytes": 250, "highWaterMark": 98}})
    assert pool["usedBytes"] == 750 and pool["state"] == "UP"
    vols = client.parse_disk_pool({"id": "dp2", "attributes": {"diskVolumes": [
        {"totalCapacityBytes": 600, "freeCapacityBytes": 100, "state": "UP"}, {"totalCapacityBytes": 400, "freeCapacityBytes": 100, "state": "DOWN"}]}})
    assert vols["totalBytes"] == 1000 and vols["usedBytes"] == 800 and vols["state"] == "DOWN"


def test_client_key_and_codes():
    assert client.client_key("SRV01.Corp.Local") == "srv01" and client.client_key("10.1.2.3") == "10.1.2.3"
    assert status_codes.outcome(0) == "success" and status_codes.outcome(1) == "partial" and status_codes.outcome(58) == "failed"
    assert status_codes.group_of(58) == "client" and status_codes.group_of(129) == "storage" and status_codes.group_of(99999) == "other"


# ---------------------------------------------------------------------------
# Analysis criteria
# ---------------------------------------------------------------------------
def _job(jid, client_name, status, hours_ago, parent=None, jtype="BACKUP", policy="P1", ptype="Standard"):
    st = NOW - dt.timedelta(hours=hours_ago)
    return {"nbuKey": "n1", "jobId": jid, "parentJobId": parent, "jobType": jtype, "category": client.job_category(jtype),
            "policyName": policy, "policyType": ptype, "scheduleType": "FULL", "clientName": client_name,
            "clientKey": client.client_key(client_name), "state": "DONE" if status is not None else "ACTIVE",
            "status": status, "startTime": st, "endTime": st + dt.timedelta(hours=1) if status is not None else None,
            "elapsedSec": 3600, "kbytes": 1024 ** 2}


def test_analysis_rates_rpo_and_coverage():
    jobs = [
        # VMware run: parent 1 → snapshot 2 → backup 3 (only the leaf job 3 is counted)
        _job(1, "nbu-master", 0, 10, ptype="VMware"), _job(2, "vm-ok", 0, 10, parent=1, jtype="SNAPSHOT", ptype="VMware"),
        _job(3, "vm-ok", 0, 10, parent=2, ptype="VMware"),
        # fs03: two failures in a row after an old success → consecutive failure (critical)
        _job(10, "fs03", 0, 80), _job(11, "fs03", 58, 50), _job(12, "fs03", 58, 26),
        # db01: last success 30 h ago → over RPO (warning)
        _job(20, "db01.corp.local", 0, 31),
        # app02: partial success only
        _job(30, "app02", 1, 5),
    ]
    policies = [{"nbuKey": "n1", "name": "P1", "policyType": "Standard", "active": True, "clients": ["ghost01"], "schedules": []}]
    vms = [{"vm": "vm-ok", "isPoweredOn": True, "vcenterKey": "vc", "cluster": "C1"},
           {"vm": "VM-NEW", "isPoweredOn": True, "vcenterKey": "vc", "cluster": "C1", "guestHostname": "db01.corp.local"},
           {"vm": "orphan", "isPoweredOn": True, "vcenterKey": "vc", "cluster": "C1"},
           {"vm": "vCLS-1", "isPoweredOn": True, "vcenterKey": "vc", "cluster": "C1"},
           {"vm": "off", "isPoweredOn": False, "vcenterKey": "vc", "cluster": "C1"}]
    r = analysis.analyze(jobs, policies, [], [], vms, analysis.BackupThresholds(), NOW, days=7)
    k = r["kpis"]
    assert k["jobs"] == 6 and k["failed"] == 2 and k["partial"] == 1  # parent + snapshot not counted
    assert k["successRate"] == round(4 * 100 / 6, 1)
    by = {c["key"]: c for c in r["clients"]}
    assert by["fs03"]["status"] == "crit" and by["fs03"]["consecutiveFail"] == 2
    assert by["db01"]["status"] == "warn" and by["db01"]["statusLabel"] == "Quá RPO"
    assert by["app02"]["status"] == "warn" and by["vm-ok"]["status"] == "ok"
    assert by["ghost01"]["status"] == "crit" and by["ghost01"]["statusLabel"] == "Không có job"
    assert "nbu-master" not in by  # parent discovery job is not a client
    cov = r["coverage"]
    assert cov["total"] == 3 and cov["protected"] == 2 and cov["unprotected"] == 1  # db01 matched via guest hostname
    assert cov["excluded"] == 1 and cov["poweredOff"] == 1
    assert r["readiness"] == "crit" and r["failureGroups"][0]["group"] == "client"


def test_storage_forecast_and_thresholds():
    hist = [{"nbuKey": "n1", "name": "dp", "totalBytes": 100.0, "usedBytes": 70.0 + d, "capturedAt": NOW - dt.timedelta(days=10 - d)}
            for d in range(11)]
    latest = [{**hist[-1], "storageType": "PureDisk", "highWaterPct": 98.0, "state": "UP", "freeBytes": 20.0}]
    r = analysis.analyze([], [], latest, hist, None, analysis.BackupThresholds(), NOW, days=7)
    s = r["storage"][0]
    assert s["usedPct"] == 80.0 and s["daysToFull"] == 18 and s["status"] == "warn"
    th = analysis.thresholds_from({"backup_rpo_hours": "48", "backup_consecutive_fail": "3", "backup_coverage_exclude": "tmp"})
    assert th.rpo_hours == 48.0 and th.consecutive_fail == 3 and th.coverage_exclude == "tmp"


# ---------------------------------------------------------------------------
# Demo sync + API
# ---------------------------------------------------------------------------
def test_demo_sync_upsert_and_api():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import SessionLocal, ensure_schema
    from app.models import BackupJob
    ensure_schema()
    with TestClient(app) as c:
        assert c.post("/api/auth/login", json={"username": "admin", "password": "Admin12345!"}).status_code == 200
        first = c.post("/api/backup/servers/nbu-test/sync").json()
        assert first["ok"] and first["jobs"] > 100 and first["pools"] == 3
        db = SessionLocal()
        n1 = db.query(BackupJob).count()
        c.post("/api/backup/sync-all")
        assert db.query(BackupJob).count() == n1  # re-sync updates, never duplicates
        db.close()
        s = c.get("/api/backup/summary?days=7").json()
        assert s["kpis"]["jobs"] > 0 and s["storage"] and s["policies"] and s["findings"]
        assert c.get("/api/backup/jobs?days=7&outcome=failed").json()["total"] > 0
        th = c.put("/api/backup/thresholds", json={"rpo_hours": 30}).json()
        assert th["values"]["rpo_hours"] == 30
        pdf = c.get("/api/backup/report/pdf?days=7")
        assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
        xlsx = c.get("/api/backup/report/xlsx?days=7")
        assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"
        assert c.get("/api/health").json()["app"] == "Infra Capacity Ops Management"
