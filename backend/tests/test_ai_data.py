"""Super app SA5: AI on variable data — backup failure analysis (A3), import column mapping (A4,
import_mappings), OS classification suggestions (A5), VM change summary (A6). The AI call is faked;
the tests check what is sent (masked, minimal), what is kept from the answer (validated against the
facts) and the policy (switches, roles, features)."""
import json

import pytest

from app import ai_data, ai_platform
from app.database import SessionLocal
from app.models import AiCache, AiRun, AppSetting, ImportMapping
from app.spend import ai as transport
from app.spend.models import SpendAiConfig

from test_ai_platform import KEY, _fake
from test_spend_opex import _login, admin, app_, editor  # noqa: F401  (fixtures)

HOSTS = [{"host": "esx01.corp.local", "cluster": "PRD", "cores": 32, "ramGB": 512, "vcenterKey": "vc1"}]
VMS = [
    {"vm": "app-legacy-07", "cluster": "PRD", "host": "esx01.corp.local", "vcpu": 4, "ramGB": 16, "isPoweredOn": True,
     "vcenterKey": "vc1", "instanceUuid": "u-7", "guestId": "otherGuest64", "guestFullName": "Other (64-bit)",
     "toolsGuestFullName": None, "toolsGuestFamily": None, "toolsStatus": "guestToolsNotRunning"},
    {"vm": "db-ora-02", "cluster": "PRD", "host": "esx01.corp.local", "vcpu": 8, "ramGB": 64, "isPoweredOn": False,
     "vcenterKey": "vc1", "instanceUuid": "u-2", "guestId": "otherGuest", "guestFullName": "Other (32-bit)"},
    {"vm": "web-01", "cluster": "PRD", "host": "esx01.corp.local", "vcpu": 2, "ramGB": 8, "isPoweredOn": True,
     "vcenterKey": "vc1", "instanceUuid": "u-1", "guestFullName": "Microsoft Windows Server 2019 (64-bit)",
     "toolsGuestFullName": "Microsoft Windows Server 2019 Standard", "toolsGuestFamily": "windowsGuest", "toolsStatus": "guestToolsRunning"},
]

REPORT = {
    "period": {"days": 7}, "kpis": {"jobs": 120, "failed": 9, "partial": 2, "successRate": 90.8, "clients": 30, "clientsCrit": 3},
    "failureGroups": [{"label": "Mạng / kết nối", "count": 6, "clientCount": 2}, {"label": "Khác", "count": 3, "clientCount": 1}],
    "topCodes": [{"code": 58, "desc": "Không kết nối được tới client", "group": "Mạng / kết nối", "count": 6, "clientCount": 2},
                 {"code": 9999, "desc": "Mã lỗi 9999", "group": "Khác", "count": 3, "clientCount": 1}],
    "failedJobs": [{"status": 58, "statusText": "Không kết nối được tới client", "policy": "PRD-FS", "jobType": "Backup",
                    "schedule": "Full", "client": "app-legacy-07"} for _ in range(4)]
                  + [{"status": 58, "statusText": "Không kết nối được tới client", "policy": "PRD-FS", "jobType": "Backup",
                      "schedule": "Full", "client": "db-ora-02"} for _ in range(2)]
                  + [{"status": 9999, "statusText": "Mã lỗi 9999", "policy": "PRD-SQL", "jobType": "Backup",
                      "schedule": "Incr", "client": "db-ora-02"} for _ in range(3)],
}


@pytest.fixture(autouse=True)
def _clean(app_, monkeypatch):
    db = SessionLocal()
    for m in (AiRun, AiCache, SpendAiConfig, ImportMapping):
        db.query(m).delete()
    db.query(AppSetting).filter(AppSetting.key.like("feature.%")).delete(synchronize_session=False)
    db.commit()
    db.close()
    from app import data_access
    ai_platform._names_cache.update(names=None)
    monkeypatch.setattr(data_access, "get_latest_combined", lambda db, keys=None, include_import=True: (HOSTS, VMS, []))
    from app.backup import service as backup_service
    monkeypatch.setattr(backup_service, "build_summary", lambda db, server, days, check_feature=False: REPORT)
    yield


def _enable(admin, **kw):
    r = admin.put("/api/admin/ai", json={"provider": "anthropic", "apiKey": KEY, "allowExternal": True, **kw})
    assert r.status_code == 200, r.text


def _facts(seen):
    return json.loads(seen[-1]["text"].split("\n", 1)[1])


def test_tasks_registered_and_switchable(app_, admin, editor, monkeypatch):
    for k in ("backup_analysis", "column_mapping", "os_classify", "vm_changes"):
        assert k in ai_platform.TASKS
    seen = []
    monkeypatch.setattr(transport, "JSON_CALL", _fake({"headline": "x", "causes": [], "unknownCodes": [], "priorities": []}, seen))
    assert editor.post("/api/ai/backup-analysis").status_code == 409                    # AI off
    _enable(admin, tasks={"backup_analysis": False})
    r = editor.post("/api/ai/backup-analysis")
    assert r.status_code == 409 and seen == []
    st = editor.get("/api/ai/status").json()["tasks"]
    assert st["backup_analysis"] is False and st["os_classify"] is True


def test_backup_analysis_aggregates_masks_and_validates(app_, admin, editor, monkeypatch):
    _enable(admin)
    seen = []
    answer = {"headline": "9 job lỗi, 6 do mạng tới client app-legacy-07",
              "causes": [{"level": "bad", "title": "Mạng tới client", "codes": [58, 12345, True], "explanation": "e", "action": "Kiểm tra firewall"},
                         {"level": "nope", "title": "Mã lạ", "codes": [9999], "explanation": "e2", "action": "a2"}],
              "unknownCodes": [{"code": 9999, "meaning": "chưa chắc chắn", "action": "Xem log bpbrm"}, {"code": 77, "meaning": "x", "action": "y"}],
              "priorities": ["Mở port 1556", ""]}
    monkeypatch.setattr(transport, "JSON_CALL", _fake(answer, seen))
    r = editor.post("/api/ai/backup-analysis?days=7")
    assert r.status_code == 200, r.text
    f = _facts(seen)
    # 9 failed jobs → 2 aggregated combos (policy × code × job type), no per-job dump
    assert [(c["ma"], c["policy"], c["so_job"], c["so_client"]) for c in f["theo_policy"]] == [(58, "PRD-FS", 6, 2), (9999, "PRD-SQL", 3, 1)]
    assert f["ma_chua_ro"] == [9999]
    assert "app-legacy-07" not in seen[-1]["text"] and "db-ora-02" not in seen[-1]["text"] and "<host:" in seen[-1]["text"]
    out = r.json()
    assert out["causes"][0]["codes"] == [58]                                             # unknown / bool codes dropped
    assert out["causes"][1]["level"] == "info"
    assert [u["code"] for u in out["unknownCodes"]] == [9999]                            # 77 not in the facts
    assert out["priorities"] == ["Mở port 1556"]
    assert "app-legacy-07" in out["headline"]                                            # unmasked back
    assert editor.post("/api/ai/backup-analysis?days=7").json()["cached"] is True


def test_backup_analysis_respects_feature(app_, admin, editor):
    _enable(admin)
    db = SessionLocal()
    db.add(AppSetting(key="feature.backup", value="false"))
    db.commit()
    db.close()
    assert editor.post("/api/ai/backup-analysis").status_code == 404


def test_shape_and_signature():
    assert ai_data.shape("esx01.corp.local") == "aaa99.aaaa.aaaaa"
    assert ai_data.shape("10.1.2.3") == "99.9.9.9"
    assert ai_data.shape("Powered On") == "Powered On" and ai_data.shape("512 GB") == "512 GB" and ai_data.shape(32) == 32
    assert ai_data.shape("4237-ab12") == "9999-aa99"
    assert ai_data.shape("0912345678") == "9999999999"                                   # long digit IDs shaped
    assert ai_data.signature(["Tên máy", "Số lõi"]) == ai_data.signature([" số LÕI", "tên máy"])
    assert ai_data.signature(["A"]) != ai_data.signature(["B"])


def test_column_mapping_suggest_save_reuse(app_, admin, editor, monkeypatch):
    _enable(admin)
    headers = ["Máy chủ", "Nhóm", "Lõi CPU", "Bộ nhớ (GiB)", "Ghi chú"]
    samples = [{"Máy chủ": "esx01.corp.local", "Nhóm": "PRD", "Lõi CPU": "32", "Bộ nhớ (GiB)": "512", "Ghi chú": "rack A3 – 10.1.2.3"}]
    seen = []
    answer = {"mapping": [{"field": "name", "column": 0, "confidence": "high"},
                          {"field": "cluster", "column": 1, "confidence": "medium"},
                          {"field": "cores", "column": 2, "confidence": "high"},
                          {"field": "ramGB", "column": 3, "confidence": "high"},
                          {"field": "ramMB", "column": 4, "confidence": "low"},          # dropped: ramGB wins
                          {"field": "password", "column": 4, "confidence": "low"},       # unknown field
                          {"field": "status", "column": 99, "confidence": "low"}],       # no such column
              "notes": "Cột Nhóm có thể là cluster."}
    monkeypatch.setattr(transport, "JSON_CALL", _fake(answer, seen))
    lk = editor.post("/api/import-mappings/lookup", json={"target": "hosts", "headers": headers}).json()
    assert lk["saved"] is None and [f["key"] for f in lk["fields"] if f["required"]] == ["name", "cores"]
    r = editor.post("/api/ai/column-mapping", json={"target": "hosts", "headers": headers, "samples": samples * 9})
    assert r.status_code == 200, r.text
    assert r.json()["mapping"] == {"name": "Máy chủ", "cluster": "Nhóm", "cores": "Lõi CPU", "ramGB": "Bộ nhớ (GiB)"}
    sent = seen[-1]["text"]
    assert "esx01" not in sent and "10.1.2.3" not in sent and "corp" not in sent       # sample values shaped
    assert len(_facts(seen)["dong_mau_theo_thu_tu_cot"]) == 5                          # ≤ 5 sample rows
    # a file without a header row: data used as header labels is shaped too
    editor.post("/api/ai/column-mapping", json={"target": "hosts", "headers": ["esx09.corp.local", "PRD", "32", "10.9.9.9"], "samples": []})
    hd = [c["tieu_de"] for c in _facts(seen)["cot"]]
    assert hd[:3] == ["aaa99.aaaa.aaaaa", "PRD", "99"] and "10.9.9.9" not in seen[-1]["text"]
    # save requires the required fields, then lookup finds it for the same layout in another order
    bad = editor.put("/api/import-mappings", json={"target": "hosts", "headers": headers, "mapping": {"name": "Máy chủ"}})
    assert bad.status_code == 400 and "Số core" in bad.json()["detail"]
    ok = editor.put("/api/import-mappings", json={"target": "hosts", "headers": headers, "source": "ai", "mapping": r.json()["mapping"]})
    assert ok.status_code == 200 and ok.json()["source"] == "ai"
    again = editor.post("/api/import-mappings/lookup", json={"target": "hosts", "headers": list(reversed(headers))}).json()
    assert again["saved"]["mapping"]["cores"] == "Lõi CPU"
    # another user cannot overwrite it; only admin lists / deletes
    other = _login(app_, "approver1")
    assert other.put("/api/import-mappings", json={"target": "hosts", "headers": headers, "mapping": r.json()["mapping"]}).status_code == 403
    assert editor.get("/api/import-mappings").status_code == 403
    assert admin.get("/api/import-mappings").json()[0]["uses"] == 1
    assert editor.delete(f"/api/import-mappings/{again['saved']['id']}").status_code == 403
    assert admin.delete(f"/api/import-mappings/{again['saved']['id']}").status_code == 200
    assert editor.post("/api/import-mappings/lookup", json={"target": "vms", "headers": headers}).json()["saved"] is None
    assert editor.post("/api/import-mappings/lookup", json={"target": "x", "headers": headers}).status_code == 400
    assert editor.post("/api/import-mappings/lookup", json={"target": "hosts", "headers": "abc"}).status_code == 400


def test_os_classify_suggests_only_unknown(app_, admin, editor, monkeypatch):
    _enable(admin)
    seen = []
    answer = {"items": [{"i": 0, "category": "linux", "osVersion": "RHEL 7", "osEdition": "", "confidence": "low", "reason": "Tên app-legacy"},
                        {"i": 1, "category": "windows_server", "osVersion": "2012 R2", "osEdition": "Standard", "confidence": "medium", "reason": "r"},
                        {"i": 1, "category": "linux", "osVersion": "", "osEdition": "", "confidence": "high", "reason": "dup"},
                        {"i": 5, "category": "linux", "osVersion": "", "osEdition": "", "confidence": "high", "reason": "out of range"}]}
    monkeypatch.setattr(transport, "JSON_CALL", _fake(answer, seen))
    r = editor.post("/api/ai/os-classify")
    assert r.status_code == 200, r.text
    f = _facts(seen)
    assert [v["guestFullName"] for v in f["vm"]] == ["Other (64-bit)", "Other (32-bit)"]   # web-01 (Windows) not sent; powered-on first
    assert "app-legacy-07" not in seen[-1]["text"]
    out = r.json()
    assert out["total"] == 2 and [(i["vm"], i["category"], i["osVersion"]) for i in out["items"]] == [
        ("app-legacy-07", "linux", "RHEL 7"), ("db-ora-02", "windows_server", "2012 R2")]
    assert out["items"][0]["instanceUuid"] == "u-7" and out["items"][0]["osEdition"] is None
    # once confirmed via the existing overrides API, the VM is no longer a candidate
    body = {"vcenterKey": "vc1", "instanceUuid": "u-7", "vm": "app-legacy-07",
            "category": "linux", "osVersion": "RHEL 7", "note": "AI đề xuất"}
    assert editor.put("/api/licensing/overrides", json=body).status_code == 403     # decision 16/11: admins only
    assert admin.put("/api/licensing/overrides", json=body).status_code == 200
    seen.clear()
    editor.post("/api/ai/os-classify")
    assert [v["guestFullName"] for v in _facts(seen)["vm"]] == ["Other (32-bit)"]
    db = SessionLocal()
    from app.models import VmOsOverride
    db.query(VmOsOverride).delete()
    db.commit()
    db.close()


def test_vm_changes_summary(app_, admin, editor, monkeypatch):
    _enable(admin)
    from app import history
    ch = {"available": True, "months": 3, "since": "2026-08-01T00:00:00Z",
          "addedTotals": {"count": 2, "vcpu": 12, "ramGB": 80, "poweredOn": 2}, "removedTotals": {"count": 1, "vcpu": 2, "ramGB": 8, "poweredOn": 0},
          "resizedTotals": {"count": 1, "deltaVcpu": 4, "deltaRamGB": 16, "up": 1, "down": 0}, "movedTotals": {}, "renamedTotals": {},
          "clusterSummary": [{"cluster": "PRD", "vcenterLabel": "vc1", "baselineVmCount": 10, "currentVmCount": 11, "netVm": 1, "added": 2,
                              "removed": 1, "addedVcpu": 12, "addedRamGB": 80}],
          "added": [{"vm": "db-ora-02", "cluster": "PRD", "vcpu": 8, "ramGB": 64, "isPoweredOn": True},
                    {"vm": "web-01", "cluster": "PRD", "vcpu": 4, "ramGB": 16, "isPoweredOn": True}],
          "removed": [{"vm": "app-legacy-07", "cluster": "PRD", "vcpu": 2, "ramGB": 8, "isPoweredOn": False}],
          "resized": [{"vm": "web-01", "cluster": "PRD", "vcpuBefore": 2, "vcpuAfter": 6, "ramBefore": 8, "ramAfter": 24, "deltaVcpu": 4}]}
    monkeypatch.setattr(history, "get_vm_changes", lambda db, keys, months, **kw: ch)
    seen = []
    monkeypatch.setattr(transport, "JSON_CALL", _fake({"headline": "PRD thêm 2 VM (12 vCPU), lớn nhất db-ora-02",
                                                        "points": [{"level": "warn", "text": "t"}], "decisions": []}, seen))
    r = editor.post("/api/ai/vm-changes?months=3")
    assert r.status_code == 200, r.text
    f = _facts(seen)
    assert f["tong"]["them"]["vcpu"] == 12 and [v["vcpu"] for v in f["them_lon_nhat"]] == [8, 4]
    assert f["doi_cau_hinh_lon_nhat"][0]["vcpu"] == "2 → 6"
    assert "db-ora-02" not in seen[-1]["text"] and r.json()["headline"].endswith("db-ora-02")
    monkeypatch.setattr(history, "get_vm_changes", lambda db, keys, months, **kw: {"available": False})
    assert editor.post("/api/ai/vm-changes").json()["empty"] is True


def test_endpoints_require_login(app_):
    from fastapi.testclient import TestClient
    c = TestClient(app_)
    for path in ("/api/ai/backup-analysis", "/api/ai/os-classify", "/api/ai/vm-changes", "/api/ai/column-mapping", "/api/import-mappings/lookup"):
        assert c.post(path, json={}).status_code == 401
