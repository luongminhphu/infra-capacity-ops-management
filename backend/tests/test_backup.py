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
