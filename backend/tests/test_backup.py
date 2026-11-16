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
        # db01: last success 40 h ago → over the nightly RPO (fixed window 20:00→04:00: 24 + 8 + 1 = 33 h)
        _job(20, "db01.corp.local", 0, 41),
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


# ---------------------------------------------------------------------------
# TLS: NetBackup internal CA has no keyUsage extension (rejected by Python 3.13 strict mode)
# ---------------------------------------------------------------------------
def _nbu_like_pki(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    now = dt.datetime.now(dt.timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "NBU Internal CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(1).not_valid_before(now - dt.timedelta(hours=1)).not_valid_after(now + dt.timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))  # deliberately no KeyUsage, like the NetBackup CA
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(2)
            .not_valid_before(now - dt.timedelta(hours=1)).not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    pem = lambda c: c.public_bytes(serialization.Encoding.PEM)  # noqa: E731
    (tmp_path / "ca.pem").write_bytes(pem(ca))
    (tmp_path / "srv.pem").write_bytes(pem(leaf))
    (tmp_path / "srv.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                                         serialization.NoEncryption()))
    return tmp_path


def _https_server(pki):
    import json
    import ssl
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps({"data": []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(pki / "srv.pem"), str(pki / "srv.key"))
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _cfg(port, **kw):
    from app.config import NetBackupConfig
    return NetBackupConfig("t", "t", kw.get("host", "localhost"), port, "KEY", "", "", "unixpwd", "", kw.get("verify", True),
                           "8.0", timeout=5, ca_cert=kw.get("ca", ""))


def test_ssl_context_flags():
    import ssl
    off = client.nbu_ssl_context(_cfg(1, verify=False))
    assert off.verify_mode == ssl.CERT_NONE and not off.check_hostname
    on = client.nbu_ssl_context(_cfg(1))
    assert on.verify_mode == ssl.CERT_REQUIRED and on.check_hostname
    assert not on.verify_flags & getattr(ssl, "VERIFY_X509_STRICT", 0)
    try:
        client.nbu_ssl_context(_cfg(1, ca="/no/such/ca.pem"))
        raise AssertionError("missing CA file must fail")
    except client.NetBackupError as exc:
        assert "CA_CERT" in str(exc)


def test_ssl_accepts_netbackup_ca_without_key_usage(tmp_path):
    import ssl
    pki = _nbu_like_pki(tmp_path)
    srv = _https_server(pki)
    port = srv.server_address[1]
    since = dt.datetime(2026, 11, 1)
    try:
        assert client.NetBackupClient(_cfg(port, ca=str(pki / "ca.pem"))).fetch_jobs(since) == []
        if hasattr(ssl, "VERIFY_X509_STRICT"):  # the strict default is exactly what the shared context removes
            strict = client.NetBackupClient(_cfg(port, ca=str(pki / "ca.pem")))
            strict.ctx.verify_flags |= ssl.VERIFY_X509_STRICT
            try:
                strict.fetch_jobs(since)
                raise AssertionError("strict mode should reject the CA")
            except client.NetBackupError as exc:
                assert "key usage" in str(exc).lower()
        for bad in (_cfg(port), _cfg(port, ca=str(pki / "ca.pem"), host="127.0.0.1")):  # no CA / wrong host name
            try:
                client.NetBackupClient(bad).fetch_jobs(since)
                raise AssertionError("verification must fail")
            except client.NetBackupError as exc:
                assert "không được tin cậy" in str(exc)
        assert client.NetBackupClient(_cfg(port, verify=False)).fetch_jobs(since) == []
    finally:
        srv.shutdown()


def test_query_string_is_tomcat_safe(monkeypatch):
    """NetBackup's API runs on Tomcat, which answers 400 to a raw '[' or ']' in the URL."""
    import urllib.parse
    seen = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"data": []}'

    monkeypatch.setattr(client.urllib.request, "urlopen", lambda req, **kw: (seen.append(req.full_url), _Resp())[1])
    client.NetBackupClient(_cfg(1556, verify=False)).fetch_jobs(dt.datetime(2026, 11, 1))
    query = seen[0].split("?", 1)[1]
    assert "[" not in query and "]" not in query and " " not in query
    q = urllib.parse.parse_qs(query)
    assert q["page[limit]"] == ["100"] and q["page[offset]"] == ["0"]
    assert q["filter"] == ["startTime ge 2026-11-01T00:00:00.000Z"]


def test_error_detail_from_json_and_html():
    import io
    import urllib.error
    j = urllib.error.HTTPError("u", 400, "", {}, io.BytesIO(b'{"errorCode":9973,"errorMessage":"Invalid filter"}'))
    assert client._error_detail(j) == "Invalid filter (errorCode 9973)"
    h = urllib.error.HTTPError("u", 400, "", {}, io.BytesIO(b"<html><body><h1>HTTP Status 400 - Bad Request</h1></body></html>"))
    assert client._error_detail(h) == "HTTP Status 400 - Bad Request"


def test_backup_scope_rules_and_coverage():
    rules = [{"vcenterKey": "vc1", "cluster": "PROD-A"}, {"vcenterKey": "vc2"}, {"vcenterKey": "vc1", "cluster": "MIX", "host": "esx9"}]
    assert analysis.in_scope({"vcenterKey": "vc1", "cluster": "PROD-A", "host": "esx1"}, rules)
    assert analysis.in_scope({"vcenterKey": "vc2", "cluster": "ANY", "host": "x"}, rules)          # whole vCenter
    assert analysis.in_scope({"vcenterKey": "vc1", "cluster": "MIX", "host": "esx9"}, rules)      # single host
    assert not analysis.in_scope({"vcenterKey": "vc1", "cluster": "MIX", "host": "esx1"}, rules)
    assert not analysis.in_scope({"vcenterKey": "vc1", "cluster": "DEV", "host": "esx1"}, rules)
    assert analysis.in_scope({"vcenterKey": "vc1", "cluster": "DEV"}, [])                        # no rules = everything
    vms = [{"vm": "prod-app", "isPoweredOn": True, "vcenterKey": "vc1", "cluster": "PROD-A", "host": "esx1"},
           {"vm": "dev-app", "isPoweredOn": True, "vcenterKey": "vc1", "cluster": "DEV", "host": "esx5"},
           {"vm": "dr-db", "isPoweredOn": True, "vcenterKey": "vc2", "cluster": "DR", "host": "esx7"}]
    jobs = [_job(1, "prod-app", 0, 5)]
    r = analysis.analyze(jobs, [], [], [], vms, analysis.BackupThresholds(), NOW, days=7,
                         scope=[{"vcenterKey": "vc1", "cluster": "PROD-A"}, {"vcenterKey": "vc2", "cluster": "DR"}])
    cov = r["coverage"]
    assert cov["scopeActive"] and cov["outOfScope"] == 1 and cov["total"] == 2
    assert cov["protected"] == 1 and [v["vm"] for v in cov["unprotectedVms"]] == ["dr-db"]   # dev-app no longer flagged
    assert "vc1 / PROD-A" in cov["scopeLabel"] and "vc2 / DR" in cov["scopeLabel"]


def test_scope_api_roundtrip():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        assert c.post("/api/auth/login", json={"username": "admin", "password": "Admin12345!"}).status_code == 200
        rules = [{"vcenterKey": "vc1", "cluster": "PROD-A"}, {"vcenterKey": "vc2", "cluster": "PROD-B"}, {"vcenterKey": "vc2", "cluster": "PROD-B"}]
        out = c.put("/api/backup/scope", json={"rules": rules}).json()
        assert out["rules"] == [{"vcenterKey": "vc1", "cluster": "PROD-A", "host": None}, {"vcenterKey": "vc2", "cluster": "PROD-B", "host": None}]
        assert c.put("/api/backup/scope", json={"rules": [{"vcenterKey": "vc1", "host": "esx1"}]}).status_code == 400
        assert c.get("/api/backup/scope").json()["rules"] == out["rules"]
        assert c.put("/api/backup/scope", json={"rules": []}).json()["rules"] == []


def test_ensure_schema_retries_concurrent_create(monkeypatch):
    """Old and new pod starting together on an empty DB: the second CREATE TABLE fails once."""
    from sqlalchemy.exc import IntegrityError
    from app import database
    calls = {"n": 0}
    real = database.Base.metadata.create_all

    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("CREATE TABLE users", {}, Exception("duplicate key pg_type_typname_nsp_index"))
        return real(**kw)

    monkeypatch.setattr(database.Base.metadata, "create_all", flaky)
    monkeypatch.setattr("time.sleep", lambda s: None)
    database.ensure_schema()
    assert calls["n"] == 2


# ---------------------------------------------------------------------------
# Backup schedule (nightly window 20:00 → 04:00 next day)
# ---------------------------------------------------------------------------
def _vn_utc(y, m, d, hh, mm=0):
    """VN local time → naive UTC (how job times are stored)."""
    return dt.datetime(y, m, d, hh, mm) - dt.timedelta(hours=7)


def _sjob(jid, client_name, status, start, end, stype="DIFFERENTIAL_INCREMENTAL", policy="CORE-EQ-DB"):
    return {"nbuKey": "n1", "jobId": jid, "parentJobId": None, "jobType": "BACKUP", "category": "backup", "policyName": policy,
            "policyType": "Standard", "scheduleType": stype, "clientName": client_name, "clientKey": client.client_key(client_name),
            "state": "DONE" if status is not None else "ACTIVE", "status": status, "startTime": start, "endTime": end,
            "elapsedSec": 3600, "kbytes": 1024 ** 2}


def test_schedule_excel_import_and_rules():
    from app.backup import schedule as bs
    import io
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    def block(row, name, full, start, end, ret, incr=True):
        ws.cell(row=row, column=2, value=name)
        for i, d in enumerate(days):
            ws.cell(row=row + 2, column=2 + i, value=d)
            col = chr(66 + i)
            ws.cell(row=row + 3, column=2 + i, value=f'=IF({col}{row + 2}=$K${row + 2},"Full Backup","{"Incremental" if incr else ""}")')
            ws.cell(row=row + 4, column=2 + i, value=f'=IF({col}{row + 3}="","","Restore point")')
        for k, (label, val) in enumerate([("Full Backup", full), ("Full backup Retention (days)", ret), ("Date of creation", None),
                                          ("Start time", dt.time(*start)), ("End Time", dt.time(*end))]):
            ws.cell(row=row + 2 + k, column=10, value=label)
            ws.cell(row=row + 2 + k, column=11, value=val)
    block(2, "P-CORE-EQUITY", "Friday", (20, 0), (4, 0), 14)
    block(12, "OTBK-TRADE", "Thursday", (22, 0), (4, 0), 90, incr=False)
    buf = io.BytesIO()
    wb.save(buf)
    rules = {r["system"]: r for r in bs.parse_schedule_xlsx(buf.getvalue())}
    assert set(rules) == {"P-CORE-EQUITY", "OTBK-TRADE"}
    eq, ot = rules["P-CORE-EQUITY"], rules["OTBK-TRADE"]
    assert eq["fullDays"] == [4] and eq["incrDays"] == [0, 1, 2, 3, 5, 6] and eq["startTime"] == "20:00" and eq["endTime"] == "04:00"
    assert ot["fullDays"] == [3] and ot["incrDays"] == [] and ot["retentionDays"] == 90
    assert round(bs.rule_rpo_hours(eq, 60)) == 33 and round(bs.rule_rpo_hours(ot, 60)) == 175
    assert bs.retention_days("2 weeks") == 14 and bs.retention_days("90 days") == 90
    assert bs.policy_matches({"policyPatterns": "core-eq-*, VM-TRADE"}, "CORE-EQ-DB") and not bs.policy_matches({"policyPatterns": ""}, "X")


def test_schedule_nightly_compliance_across_midnight():
    from app.backup import schedule as bs
    rule = bs.validate_rule({"system": "P-CORE-EQUITY", "policyPatterns": "CORE-EQ-*", "fullDays": "Friday",
                             "incrDays": "Mon,Tue,Wed,Thu,Sat,Sun", "startTime": "20:00", "endTime": "04:00", "retentionDays": 14})
    # Nights of Mon 2026-11-02 .. Sun 11-08, evaluated on Mon 11-09 at 10:00 VN
    jobs = [
        _sjob(1, "db1", 0, _vn_utc(2026, 11, 2, 20), _vn_utc(2026, 11, 3, 1)),                 # Mon ok
        _sjob(2, "db1", 0, _vn_utc(2026, 11, 4, 0, 30), _vn_utc(2026, 11, 4, 3)),              # Tue night, started after midnight → still Tue
        _sjob(3, "db1", 0, _vn_utc(2026, 11, 4, 20), _vn_utc(2026, 11, 5, 6)),                 # Wed: ends 06:00 > 04:00 + 1h → late
        _sjob(4, "db1", 58, _vn_utc(2026, 11, 5, 20), _vn_utc(2026, 11, 5, 21)),               # Thu: fails …
        _sjob(5, "db1", 0, _vn_utc(2026, 11, 5, 22), _vn_utc(2026, 11, 6, 2)),                 # … retried OK same night → ok
        _sjob(6, "db1", 0, _vn_utc(2026, 11, 6, 20), _vn_utc(2026, 11, 7, 3)),                 # Fri: incremental only → wrong type
        _sjob(7, "db1", 13, _vn_utc(2026, 11, 7, 20), _vn_utc(2026, 11, 7, 22)),               # Sat: failed
        # Sun: nothing → missing
    ]
    now = _vn_utc(2026, 11, 9, 10)
    res = bs.evaluate([rule], jobs, [{"name": "CORE-EQ-DB", "schedules": [{"name": "Full", "type": "FULL", "retention": "1 weeks"}]}],
                      now, days=7, grace_minutes=60)
    r = res["rules"][0]
    by = {c["date"]: c["status"] for c in r["nights"]}
    assert by == {"2026-11-02": "ok", "2026-11-03": "ok", "2026-11-04": "late", "2026-11-05": "ok", "2026-11-06": "wrongtype",
                  "2026-11-07": "failed", "2026-11-08": "missing"}   # at 10:00 Monday the latest night is Sunday
    assert r["compliancePct"] == round(3 * 100 / 7, 1) and r["evaluated"] == 7
    assert r["retentionIssues"] == ["CORE-EQ-DB/Full: 1 weeks"]
    titles = " | ".join(f[2] for f in res["findings"])
    assert "2 đêm backup thiếu / lỗi" in titles and "đêm Full không chạy Full" in titles and "retention" in titles


def test_weekly_schedule_rpo_not_flagged():
    from app.backup import schedule as bs
    rule = bs.validate_rule({"system": "OTBK-TRADE", "policyPatterns": "OTBK-*", "fullDays": "Thursday", "incrDays": "",
                             "startTime": "22:00", "endTime": "04:00", "retentionDays": 90})
    jobs = [_sjob(1, "otbk1", 0, _vn_utc(2026, 11, 5, 22), _vn_utc(2026, 11, 6, 2), stype="FULL", policy="OTBK-TRADE")]
    now = _vn_utc(2026, 11, 10, 10)    # Tuesday, 104 h after the Thursday Full
    r = analysis.analyze(jobs, [{"nbuKey": "n1", "name": "OTBK-TRADE", "policyType": "Standard", "active": True, "clients": [],
                                  "schedules": []}], [], [], None, analysis.BackupThresholds(), now, days=7, schedules=[rule])
    c = next(c for c in r["clients"] if c["key"] == "otbk1")
    assert c["status"] == "ok" and c["rpoHours"] == 175.0
    assert not any(p["statusLabel"] == "Không chạy" for p in r["policies"])
    night = {n["date"]: n["status"] for n in r["schedule"]["rules"][0]["nights"]}
    assert night["2026-11-05"] == "ok" and r["kpis"]["scheduleCompliancePct"] == 100.0


def test_schedule_read_from_netbackup_policy():
    """Generic-schema policy payload: Full = Calendar Friday 20:00 +8h, Incremental = Frequency 1 day
    on the other nights; an Oracle policy whose window opens at 02:00 belongs to the previous night."""
    from app.backup import schedule as bs
    def win(days, start_h, hours=8):
        return [{"dayOfWeek": d, "startSeconds": start_h * 3600, "durationSeconds": hours * 3600 if d in days else 0} for d in range(1, 8)]
    pol = client.parse_policy({"type": "policy", "id": "CORE-EQ-DB", "attributes": {"policy": {
        "policyName": "CORE-EQ-DB", "policyType": "Standard", "policyAttributes": {"active": True},
        "schedules": [
            {"scheduleName": "Full", "backupType": "Full Backup", "scheduleType": "Calendar",
             "includeDates": {"recurringDaysOfWeek": ["1:6", "2:6", "3:6", "4:6", "5:6"]}, "startWindow": win([6], 20),
             "backupCopies": {"copies": [{"retentionPeriod": {"value": 2, "unit": "WEEKS"}}]}},
            {"scheduleName": "Incr", "backupType": "Differential Incremental Backup", "scheduleType": "Frequency",
             "frequencySeconds": 86400, "startWindow": win([1, 2, 3, 4, 5, 7], 20)},
        ]}}})
    assert pol["schedules"][0]["type"] == "FULL" and pol["schedules"][0]["calendarDays"] == [4]
    rule = bs.derive_rule(pol)
    assert rule["fullDays"] == [4] and rule["incrDays"] == [0, 1, 2, 3, 5, 6]
    assert rule["windows"][4] == {"start": "20:00", "end": "04:00"} and rule["retentionDays"] == 14
    ora = bs.derive_rule(client.parse_policy({"id": "ORA", "attributes": {"policy": {"policyName": "ORA", "schedules": [
        {"scheduleName": "Daily", "backupType": "Full Backup", "scheduleType": "Frequency", "frequencySeconds": 86400,
         "startWindow": win([3], 2, 6)}]}}}))   # Tuesday 02:00 → night of Monday
    assert ora["fullDays"] == [0]
    s, e = bs.window(ora, dt.date(2026, 11, 2))
    assert (s.day, s.hour, e.hour) == (3, 2, 8)
    weekly_any = bs.derive_rule(client.parse_policy({"id": "W", "attributes": {"policy": {"policyName": "W", "schedules": [
        {"scheduleName": "F", "backupType": "Full Backup", "scheduleType": "Frequency", "frequencySeconds": 604800,
         "startWindow": win([1, 2, 3, 4, 5, 6, 7], 20)},
        {"scheduleName": "I", "backupType": "Cumulative Incremental Backup", "scheduleType": "Frequency", "frequencySeconds": 86400,
         "startWindow": win([1, 2, 3, 4, 5, 6, 7], 20)}]}}}))
    assert weekly_any["fullEveryDays"] == 7 and weekly_any["fullDays"] == [] and len(weekly_any["incrDays"]) == 7
    # evaluate(auto=True) picks the policy up without any manual schedule
    jobs = [_sjob(1, "db1", 0, _vn_utc(2026, 11, 6, 20), _vn_utc(2026, 11, 7, 2), stype="FULL")]
    res = bs.evaluate([], jobs, [pol], _vn_utc(2026, 11, 7, 10), days=1, auto=True)
    assert res["rules"][0]["source"] == "netbackup" and res["rules"][0]["nights"][0]["status"] == "ok"


def test_fixed_window_mode():
    """Mode 'fixed': every active policy must succeed each night between 20:00 and 04:00 (+ grace)."""
    from app.backup import schedule as bs
    jobs = [_sjob(1, "a", 0, _vn_utc(2026, 11, 2, 21), _vn_utc(2026, 11, 3, 3), policy="P1"),
            _sjob(2, "a", 0, _vn_utc(2026, 11, 3, 21), _vn_utc(2026, 11, 4, 6), policy="P1"),      # 06:00 > 04:00 + 1 h
            _sjob(3, "a", 0, _vn_utc(2026, 11, 5, 1), _vn_utc(2026, 11, 5, 3), policy="P1")]       # 01:00 → night of 11-04
    pols = [{"name": "P1", "active": True, "schedules": [{"name": "F", "type": "FULL", "kind": "Calendar", "calendarDays": [4],
                                                          "windows": [{"day": 4, "startMin": 1200, "durationMin": 480}]}]},
            {"name": "OLD", "active": False, "schedules": []}]
    res = bs.evaluate([], jobs, pols, _vn_utc(2026, 11, 6, 10), days=4, auto=True, mode="fixed")
    assert [r["system"] for r in res["rules"]] == ["P1"] and res["rules"][0]["source"] == "fixed"
    assert [c["status"] for c in res["rules"][0]["nights"]] == ["ok", "late", "ok", "missing"]
    assert round(bs.rule_rpo_hours(res["rules"][0], 60)) == 33
    nb = bs.evaluate([], jobs, pols, _vn_utc(2026, 11, 6, 10), days=4, auto=True, mode="netbackup")
    assert nb["rules"][0]["source"] == "netbackup" and nb["rules"][0]["fullDays"] == [4]


def test_schedule_from_real_policy_payload():
    """Shape returned by the company's NetBackup: Calendar 'full-monthly' with no include dates (never runs
    on its own) + Frequency 'full-daily' with a 02:14 → 05:30 window every day."""
    from app.backup import schedule as bs
    win = lambda start, dur: [{"dayOfWeek": d, "startSeconds": start, "durationSeconds": dur} for d in range(1, 8)]  # noqa: E731
    empty = {"lastDayOfMonth": False, "recurringDaysOfMonth": [], "recurringDaysOfWeek": [], "specificDates": []}
    pol = client.parse_policy({"id": "DB1", "attributes": {"policy": {"policyName": "DB1", "schedules": [
        {"scheduleName": "full-monthly", "backupType": "Full Backup", "scheduleType": "Calendar", "frequencySeconds": 604800,
         "startWindow": win(8040, 11759), "includeDates": empty},
        {"scheduleName": "full-daily", "backupType": "Full Backup", "scheduleType": "Frequency", "frequencySeconds": 86400,
         "startWindow": win(8100, 11699), "includeDates": empty}]}}})
    rule = bs.derive_rule(pol)
    assert rule["fullDays"] == list(range(7)) and rule["incrDays"] == []
    assert rule["startTime"] == "02:15" and rule["endTime"] == "05:29"
    assert rule["scheduleNotes"] == ["full-monthly: lịch Calendar chưa chọn ngày chạy (không tính)"]
    s, e = bs.window(rule, dt.date(2026, 11, 2))            # night of Monday → Tuesday 02:15 – 05:29
    assert (s.day, s.hour, s.minute, e.hour, e.minute) == (3, 2, 15, 5, 29)
    # A real monthly calendar: Full on day 1 of each month at 02:14 → night of the last day of the previous month
    monthly = bs.derive_rule(client.parse_policy({"id": "M", "attributes": {"policy": {"policyName": "M", "schedules": [
        {"scheduleName": "full-monthly", "backupType": "Full Backup", "scheduleType": "Calendar", "startWindow": win(8040, 11759),
         "includeDates": {**empty, "recurringDaysOfMonth": [1]}},
        {"scheduleName": "incr", "backupType": "Differential Incremental Backup", "scheduleType": "Frequency",
         "frequencySeconds": 86400, "startWindow": win(8040, 11759), "includeDates": empty}]}}}))
    assert bs.expected_type(monthly, dt.date(2026, 10, 31)) == "full" and bs.expected_type(monthly, dt.date(2026, 11, 1)) == "incr"



def test_purge_keeps_six_months_and_latest_ok_run():
    """Decision Q5 (16/11): 6 months of jobs, sync runs and storage snapshots; the newest successful
    run survives whatever its age; batches do not change the result."""
    from app.backup import collector
    from app.config import get_settings
    from app.database import SessionLocal
    from app.database import ensure_schema
    from app.models import BackupJob, BackupPolicySnapshot, BackupStorageSnapshot, BackupSyncRun
    ensure_schema()
    assert get_settings().backup_retention_days == 183
    key = "nbu-purge-test"
    now = dt.datetime(2026, 11, 16)
    cfg = type("C", (), {"key": key})()
    db = SessionLocal()
    try:
        def run(days_ago, status="ok"):
            r = BackupSyncRun(nbu_key=key, source="live", status=status, started_at=now - dt.timedelta(days=days_ago))
            db.add(r)
            db.flush()
            db.add(BackupStorageSnapshot(run_id=r.id, nbu_key=key, name="pool", captured_at=r.started_at))
            db.add(BackupPolicySnapshot(run_id=r.id, nbu_key=key, name="P", captured_at=r.started_at))
            return r
        old_id, recent_id = run(400).id, run(10).id
        for i, days in enumerate((200, 184, 182, 1)):
            db.add(BackupJob(nbu_key=key, job_id=900000 + i, start_time=now - dt.timedelta(days=days)))
        db.commit()
        monkey = collector.PURGE_BATCH
        collector.PURGE_BATCH = 1                     # force several batches
        try:
            collector._purge(db, cfg, get_settings(), now)
        finally:
            collector.PURGE_BATCH = monkey
        db.commit()
        jobs = sorted((now - j.start_time).days for j in db.query(BackupJob).filter_by(nbu_key=key))
        assert jobs == [1, 182]
        runs = {r.id for r in db.query(BackupSyncRun).filter_by(nbu_key=key)}
        assert runs == {recent_id}
        assert db.query(BackupStorageSnapshot).filter_by(nbu_key=key, run_id=old_id).count() == 0
        assert db.query(BackupStorageSnapshot).filter_by(nbu_key=key, run_id=recent_id).count() == 1
        # alone and old: the latest ok run is kept with its snapshots
        db.query(BackupSyncRun).filter_by(id=recent_id).update({"started_at": now - dt.timedelta(days=500)})
        db.commit()
        collector._purge(db, cfg, get_settings(), now)
        db.commit()
        assert {r.id for r in db.query(BackupSyncRun).filter_by(nbu_key=key)} == {recent_id}
    finally:
        db.query(BackupJob).filter_by(nbu_key=key).delete()
        db.query(BackupStorageSnapshot).filter_by(nbu_key=key).delete()
        db.query(BackupPolicySnapshot).filter_by(nbu_key=key).delete()
        db.query(BackupSyncRun).filter_by(nbu_key=key).delete()
        db.commit()
        db.close()


def test_money_to_integer_minor_units():
    """DAT-10 step 1: integer twins of the Float money columns."""
    from app.shared.money import to_minor, usd_to_micros
    assert to_minor(1_234_567.5, "VND") == 1_234_568
    assert to_minor("1234.5", "usd") == 123450
    assert to_minor(0.1 + 0.2, "USD") == 30
    assert to_minor(None) is None and to_minor("", "VND") is None
    assert usd_to_micros(0.0123456789) == 12346
    assert usd_to_micros(None) == 0
