"""Chi phí & Hợp đồng — Opex API: roles, codes, validation, commands, attachments, settings."""
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import AppSetting, AuditLog, User
from app.security import hash_password
from app.spend import files
from app.spend.models import (
    EvaluationCriterion, EvaluationScore, Quotation, QuotationFinding, QuotationLine, QuoteRequest,
    RenovationCost, RenovationCostGroup, RenovationProject,
    CapexItem, CapexProject, Contract, ContractVersion, Proposal, ProposalContract, ProposalOpex, ProposalRecipient,
    ProposalTemplate, SpendAiConfig, SpendSavedFilter, ContractPayment, PaymentDocument,
    OpexAsset, OpexAttachment, OpexItem, SpendCategory, SpendCostCenter, SpendFile, SpendNotifyChannel,
    SpendReminderLog, SpendSequence, SpendSmtpConfig, SpendVendor, BudgetPlan, BudgetPlanLine,
)

ADMIN = {"username": "admin", "password": "Admin12345!"}
PW = "User12345!"


def _wipe():
    db = SessionLocal()
    for model in (BudgetPlanLine, BudgetPlan, EvaluationScore, EvaluationCriterion, QuotationFinding, QuotationLine, Quotation, QuoteRequest, RenovationCost, RenovationProject, RenovationCostGroup, PaymentDocument, ContractPayment, ProposalContract, ProposalOpex, Proposal, ProposalTemplate, ProposalRecipient, SpendAiConfig,
                  ContractVersion, Contract, SpendReminderLog, OpexAttachment, OpexAsset, SpendFile, SpendNotifyChannel, SpendSmtpConfig, SpendSavedFilter):
        db.query(model).delete()
    db.query(OpexItem).update({OpexItem.renewed_from_id: None})
    for model in (OpexItem, CapexItem, CapexProject, SpendVendor, SpendCostCenter, SpendSequence):
        db.query(model).delete()
    db.query(AppSetting).filter(AppSetting.key.like("spend.%")).delete(synchronize_session=False)
    db.query(AppSetting).filter(AppSetting.key == "feature.spend").delete()
    db.commit()
    db.close()


def _user(name: str, role: str) -> None:
    db = SessionLocal()
    u = db.query(User).filter_by(username=name).first() or User(username=name, password_hash=hash_password(PW))
    u.spend_role, u.spend_role_source = role, "ui"
    db.add(u)
    db.commit()
    db.close()


@pytest.fixture()
def app_():
    from app.main import app
    with TestClient(app):
        _wipe()
        for name, role in (("viewer1", "viewer"), ("editor1", "editor"), ("approver1", "approver"), ("nobody1", "none")):
            _user(name, role)
        yield app
        _wipe()


def _login(app, username, password=PW):
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200
    return c


@pytest.fixture()
def admin(app_):
    return _login(app_, ADMIN["username"], ADMIN["password"])


@pytest.fixture()
def editor(app_):
    return _login(app_, "editor1")


def _cat_id(code="HW_SERVER"):
    db = SessionLocal()
    cid = db.query(SpendCategory.id).filter_by(code=code).scalar()
    db.close()
    return cid


def _opex(**kw):
    return {"categoryId": _cat_id(), "title": "Gia hạn bảo hành Dell R750 (4 node)", "vendorName": "Dell Việt Nam",
            "startDate": "2026-01-01", "endDate": "2026-12-31", "billingCycle": "yearly",
            "amountNet": 120_000_000, "vatPct": 10, "ownerName": "Nguyễn Văn A", "ownerEmail": "a@corp.example",
            "assets": [{"serial": "SN-R750-01", "vcenterKey": "vc1", "hostName": "esx01.corp.local"}], **kw}


def test_roles_are_enforced_at_the_endpoint(app_, admin):
    nobody, viewer = _login(app_, "nobody1"), _login(app_, "viewer1")
    assert nobody.get("/api/spend/meta").status_code == 403
    assert viewer.get("/api/spend/meta").json()["role"] == "viewer"
    assert viewer.post("/api/spend/opex", json=_opex()).status_code == 403
    assert _login(app_, "editor1").post("/api/spend/opex", json=_opex()).status_code == 200
    assert viewer.put("/api/spend/settings", json={"fiscalStartMonth": 4}).status_code == 403
    assert _login(app_, "approver1").get("/api/spend/admin/channels").status_code == 403
    assert admin.get("/api/auth/me").json()["spendRole"] == "admin"
    assert viewer.get("/api/auth/me").json()["spendRole"] == "viewer"


def test_feature_off_hides_the_api(admin):
    assert admin.put("/api/settings/features", json={"features": {"spend": False}}).status_code == 200
    assert admin.get("/api/spend/meta").status_code == 404
    assert admin.put("/api/settings/features", json={"features": {"spend": True}}).status_code == 200
    assert admin.get("/api/spend/meta").status_code == 200


def test_create_codes_money_and_fiscal_year(admin, editor):
    a = editor.post("/api/spend/opex", json=_opex()).json()
    b = editor.post("/api/spend/opex", json=_opex(title="License firewall", vendorName="dell  viet nam")).json()
    assert (a["code"], b["code"]) == ("OPX-2026-0001", "OPX-2026-0002")
    assert a["amountGross"] == 132_000_000 and a["fiscalYear"] == 2026 and a["status"] == "active"
    assert a["vendorId"] == b["vendorId"], "same vendor after name normalisation"
    assert a["assets"][0]["hostName"] == "esx01.corp.local"
    # Fiscal year starting in April: a March start date belongs to the previous fiscal year.
    assert admin.put("/api/spend/settings", json={"fiscalStartMonth": 4}).status_code == 200
    c = editor.post("/api/spend/opex", json=_opex(startDate="2027-03-15", endDate="2028-03-14")).json()
    assert c["fiscalYear"] == 2026 and c["code"] == "OPX-2026-0003"
    # Hand-entered gross (invoice rounding) is kept.
    d = editor.post("/api/spend/opex", json=_opex(amountGross=132_000_005)).json()
    assert d["amountGross"] == 132_000_005


@pytest.mark.parametrize("patch,msg", [
    ({"endDate": "2025-12-31"}, "Ngày kết thúc"), ({"amountNet": -1}, "không âm"), ({"amountNet": 10.5}, "số nguyên"),
    ({"amountGross": 1}, "không được nhỏ hơn"), ({"vatPct": 120}, "VAT"), ({"title": ""}, "Thiếu nội dung"),
    ({"ownerEmail": "not-an-email"}, "Email"), ({"status": "renewed"}, "Dự kiến"), ({"categoryId": 999999}, "loại chi phí"),
])
def test_validation(editor, patch, msg):
    r = editor.post("/api/spend/opex", json=_opex(**patch))
    assert r.status_code == 400 and msg in r.json()["detail"], r.text


def test_update_optimistic_lock_and_audit(app_, editor):
    item = editor.post("/api/spend/opex", json=_opex()).json()
    body = {**_opex(amountNet=150_000_000), "rowVersion": item["rowVersion"]}
    r = editor.put(f"/api/spend/opex/{item['code']}", json=body)
    assert r.status_code == 200 and r.json()["amountGross"] == 165_000_000 and r.json()["rowVersion"] == 2
    # Second save with the stale version is refused.
    assert editor.put(f"/api/spend/opex/{item['code']}", json=body).status_code == 409
    # Status cannot be changed by a generic update (mass assignment).
    r = editor.put(f"/api/spend/opex/{item['code']}", json={**_opex(), "status": "stopped", "code": "X", "rowVersion": 2})
    assert r.status_code == 200 and r.json()["status"] == "active" and r.json()["code"] == item["code"]
    db = SessionLocal()
    row = (db.query(AuditLog).filter(AuditLog.action == "spend.opex.update", AuditLog.detail.like(f'%"{item["code"]}"%'))
           .order_by(AuditLog.id.desc()).offset(1).first())   # the first of the two updates
    assert json.loads(row.detail)["changes"]["amountNet"] == [120_000_000, 150_000_000]
    db.close()
    hist = editor.get(f"/api/spend/opex/{item['code']}/history").json()
    assert {h["action"] for h in hist} >= {"spend.opex.create", "spend.opex.update"}


def test_commands_renew_once_stop_confirm(app_, editor):
    approver = _login(app_, "approver1")
    item = editor.post("/api/spend/opex", json=_opex()).json()
    new = editor.post(f"/api/spend/opex/{item['code']}/renew", json={"amountNet": 130_000_000}).json()
    assert new["startDate"] == "2027-01-01" and new["endDate"] == "2027-12-31" and new["fiscalYear"] == 2027
    assert new["code"] == "OPX-2027-0001" and new["renewedFromCode"] == item["code"] and new["origin"] == "renewal"
    assert new["amountGross"] == 143_000_000 and new["assets"][0]["serial"] == "SN-R750-01"
    old = editor.get(f"/api/spend/opex/{item['code']}").json()
    assert old["status"] == "renewed" and old["renewedToCode"] == new["code"]
    assert editor.post(f"/api/spend/opex/{item['code']}/renew", json={}).status_code == 409
    # Planned -> confirm needs the approver role.
    planned = editor.post("/api/spend/opex", json=_opex(status="planned")).json()
    assert editor.post(f"/api/spend/opex/{planned['code']}/confirm").status_code == 403
    assert approver.post(f"/api/spend/opex/{planned['code']}/confirm").json()["status"] == "active"
    # Stop needs a reason; a stopped item cannot be edited.
    assert editor.post(f"/api/spend/opex/{planned['code']}/stop", json={}).status_code == 400
    assert editor.post(f"/api/spend/opex/{planned['code']}/stop", json={"reason": "Thanh lý thiết bị"}).json()["status"] == "stopped"
    assert editor.put(f"/api/spend/opex/{planned['code']}", json={**_opex(), "rowVersion": 99}).status_code == 409
    assert approver.post(f"/api/spend/opex/{planned['code']}/reactivate").json()["status"] == "active"


def test_list_filters_and_derived_status(app_, editor):
    import datetime as dt
    from app.spend.fiscal import today_vn
    t = today_vn()
    soon = editor.post("/api/spend/opex", json=_opex(startDate=(t - dt.timedelta(days=300)).isoformat(),
                                                      endDate=(t + dt.timedelta(days=20)).isoformat())).json()
    later = editor.post("/api/spend/opex", json=_opex(startDate=t.isoformat(),
                                                       endDate=(t + dt.timedelta(days=200)).isoformat(), vendorName="HPE")).json()
    past = editor.post("/api/spend/opex", json=_opex(startDate=(t - dt.timedelta(days=400)).isoformat(),
                                                      endDate=(t - dt.timedelta(days=5)).isoformat())).json()
    assert (soon["displayStatus"], soon["tone"]) == ("expiring", "bad")
    assert (later["displayStatus"], later["tone"]) == ("active", "ok")
    assert (past["displayStatus"], past["tone"]) == ("overdue", "crit")
    r = editor.get("/api/spend/opex?status=expiring").json()
    assert [i["code"] for i in r["items"]] == [soon["code"]]
    assert editor.get("/api/spend/opex?status=overdue").json()["total"] == 1
    r = editor.get("/api/spend/opex?q=hpe").json()
    assert r["total"] == 1 and r["sumGross"] == 132_000_000
    assert editor.get("/api/spend/opex?q=SN-R750").json()["total"] == 3
    assert editor.get("/api/spend/opex?status=bogus").status_code == 400


def _docx(extra=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def test_attachments_content_checks_and_download(app_, editor):
    item = editor.post("/api/spend/opex", json=_opex()).json()
    url = f"/api/spend/opex/{item['code']}/attachments"
    pdf = b"%PDF-1.7\n%test\n"
    r = editor.post(url, files={"file": ("bao gia <x>.pdf", pdf, "application/pdf")})
    assert r.status_code == 200 and r.json()[0]["name"] == "bao gia x.pdf"
    fid = r.json()[0]["id"]
    d = editor.get(f"/api/spend/files/{fid}")
    assert d.status_code == 200 and d.content == pdf and d.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in d.headers["content-disposition"]
    # Renamed executable, macro document, file named .pdf that is not a PDF: refused on content.
    assert editor.post(url, files={"file": ("x.pdf", b"MZ\x90\x00", "application/pdf")}).status_code == 415
    assert editor.post(url, files={"file": ("x.docx", _docx({"word/vbaProject.bin": "x"}), "application/x")}).status_code == 415
    assert editor.post(url, files={"file": ("x.docx", _docx({"../evil": "x"}), "application/x")}).status_code == 415
    ok = editor.post(url, files={"file": ("hop dong.docx", _docx(), "application/x")})
    assert ok.status_code == 200 and len(ok.json()) == 2
    # Stored under a random name, not the user's.
    db = SessionLocal()
    stored = db.get(SpendFile, fid)
    assert files.path_of(stored).is_file() and "bao" not in stored.storage_key
    db.close()
    # Viewer can download, cannot delete.
    viewer = _login(app_, "viewer1")
    assert viewer.get(f"/api/spend/files/{fid}").status_code == 200
    assert viewer.delete(f"{url}/{fid}").status_code == 403
    assert len(editor.delete(f"{url}/{fid}").json()) == 1
    assert not files.path_of(stored).exists()


def test_upload_size_limit(app_, editor, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "spend_max_upload_mb", 1)
    item = editor.post("/api/spend/opex", json=_opex()).json()
    big = b"%PDF-" + b"0" * (1024 * 1024 + 10)
    assert editor.post(f"/api/spend/opex/{item['code']}/attachments", files={"file": ("a.pdf", big)}).status_code == 413


def test_delete_is_admin_only_and_blocked_by_successor(admin, editor):
    a = editor.post("/api/spend/opex", json=_opex()).json()
    assert editor.delete(f"/api/spend/opex/{a['code']}").status_code == 403
    editor.post(f"/api/spend/opex/{a['code']}/renew", json={})
    assert admin.delete(f"/api/spend/opex/{a['code']}").status_code == 409
    b = editor.post("/api/spend/opex", json=_opex()).json()
    assert admin.delete(f"/api/spend/opex/{b['code']}").status_code == 200
    assert admin.get(f"/api/spend/opex/{b['code']}").status_code == 404


def test_settings_validation(admin):
    assert admin.put("/api/spend/settings", json={"reminderDays": [0]}).status_code == 400
    assert admin.put("/api/spend/settings", json={"appBaseUrl": "javascript:alert(1)"}).status_code == 400
    r = admin.put("/api/spend/settings", json={"reminderDays": [30, 90, 60, 60], "appBaseUrl": "https://ico.corp.example/"})
    assert r.json()["reminderDays"] == [90, 60, 30] and r.json()["appBaseUrl"] == "https://ico.corp.example"


def test_user_role_admin_and_entra_mapping(app_, admin):
    from app.config import get_settings
    from app.spend.roles import apply_entra_roles
    users = admin.get("/api/spend/admin/users").json()["items"]
    uid = next(u["id"] for u in users if u["username"] == "nobody1")
    assert admin.put(f"/api/spend/admin/users/{uid}/role", json={"role": "editor"}).json()["spendRole"] == "editor"
    assert admin.put(f"/api/spend/admin/users/{uid}/role", json={"role": "superuser"}).status_code == 400
    s = get_settings()
    u = User(username="x", password_hash="!", spend_role="viewer", spend_role_source="ui")
    apply_entra_roles(u, s, ["ICO.Spend.Approver", "ICO.Spend.Viewer"])
    assert (u.spend_role, u.spend_role_source) == ("approver", "entra")
    apply_entra_roles(u, s, [])                      # app role removed in Entra -> withdrawn
    assert u.spend_role == "none"
    u2 = User(username="y", password_hash="!", spend_role="editor", spend_role_source="ui")
    apply_entra_roles(u2, s, [])                     # admin-assigned role is kept
    assert u2.spend_role == "editor"
