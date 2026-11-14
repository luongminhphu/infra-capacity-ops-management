"""Contract payment instalments and their document checklist."""
import datetime as dt

import pytest

from app.spend.fiscal import today_vn

from test_spend_opex import _login, _opex, admin, app_, editor  # noqa: F401  (fixtures)


@pytest.fixture()
def contract(editor):
    o = editor.post("/api/spend/opex", json=_opex(assets=[], amountNet=1_000_000_000)).json()   # 1,1 tỷ sau VAT
    return editor.post("/api/spend/contracts", json={"title": "HĐ bảo hành", "opexCode": o["code"]}).json()


def _url(c, *parts):
    return "/".join([f"/api/spend/contracts/{c['code']}/payments", *map(str, parts)])


def test_instalments_amounts_and_default_checklist(editor, contract):
    r = editor.post(_url(contract), json={"title": "Đợt 1 — tạm ứng", "percent": 30, "condition": "Khi ký hợp đồng"})
    assert r.status_code == 200, r.text
    d = r.json()
    p1 = d["items"][0]
    assert d["contractValue"] == 1_100_000_000 and p1["amount"] == 330_000_000 and p1["seq"] == 1
    assert [x["name"] for x in p1["documents"]][:3] == ["Đề nghị thanh toán", "Hóa đơn GTGT", "Biên bản nghiệm thu / bàn giao"]
    assert p1["docsTotal"] == 5 and len(p1["requiredMissing"]) == 3
    due = (today_vn() + dt.timedelta(days=10)).isoformat()
    d = editor.post(_url(contract), json={"title": "Đợt 2 — nghiệm thu", "amount": 770_000_000, "dueDate": due,
                                          "documents": [{"name": "Hóa đơn GTGT"}, {"name": "Biên bản nghiệm thu", "required": False}]}).json()
    assert d["plannedTotal"] == 1_100_000_000 and d["differenceFromValue"] == 0 and d["remaining"] == 1_100_000_000
    assert d["items"][1]["tone"] == "warn" and d["items"][1]["docsTotal"] == 2
    assert editor.post(_url(contract), json={"title": "x"}).status_code == 400                       # no amount / %
    assert editor.post(_url(contract), json={"title": "x", "percent": 120}).status_code == 400


def test_submit_requires_required_documents_and_paid_needs_approver(app_, admin, editor, contract):
    approver = _login(app_, "approver1")
    p = editor.post(_url(contract), json={"title": "Đợt 1", "amount": 500_000_000}).json()["items"][0]
    r = editor.post(_url(contract, p["id"], "submit"))
    assert r.status_code == 409 and "Hóa đơn GTGT" in r.json()["detail"]
    for doc in p["documents"]:
        if doc["required"]:
            t = editor.put(_url(contract, p["id"], "documents", doc["id"]), json={"done": True})
            assert t.status_code == 200
    after = t.json()["items"][0]
    assert after["requiredMissing"] == [] and all(x["doneBy"] == "editor1" for x in after["documents"] if x["required"])
    assert editor.post(_url(contract, p["id"], "submit")).json()["items"][0]["status"] == "submitted"
    # Checklist is locked once submitted.
    assert editor.put(_url(contract, p["id"], "documents", p["documents"][0]["id"]), json={"done": False}).status_code == 409
    assert editor.post(_url(contract, p["id"], "paid"), json={}).status_code == 403
    d = approver.post(_url(contract, p["id"], "paid"), json={"paidAt": "2026-11-20", "paidAmount": 499_000_000,
                                                            "paymentRef": "UNC-0123"}).json()
    item = d["items"][0]
    assert (item["status"], item["paidAt"], item["paidAmount"], item["tone"]) == ("paid", "2026-11-20", 499_000_000, "ok")
    assert d["paidTotal"] == 499_000_000
    assert editor.delete(_url(contract, p["id"])).status_code == 409
    assert admin.delete(f"/api/spend/contracts/{contract['code']}").status_code == 409             # paid instalment exists
    assert approver.post(_url(contract, p["id"], "unpay")).status_code == 403
    assert admin.post(_url(contract, p["id"], "unpay")).json()["items"][0]["status"] == "submitted"
    assert approver.post(_url(contract, p["id"], "reopen")).json()["items"][0]["status"] == "pending"


def test_overdue_and_cross_contract_access(editor, contract):
    past = (today_vn() - dt.timedelta(days=3)).isoformat()
    p = editor.post(_url(contract), json={"title": "Đợt quá hạn", "amount": 1, "dueDate": past}).json()
    assert p["items"][0]["tone"] == "crit" and p["overdue"] == 1
    o2 = editor.post("/api/spend/opex", json=_opex(assets=[])).json()
    other = editor.post("/api/spend/contracts", json={"title": "HĐ khác", "opexCode": o2["code"]}).json()
    pid, did = p["items"][0]["id"], p["items"][0]["documents"][0]["id"]
    assert editor.put(_url(other, pid, "documents", did), json={"done": True}).status_code == 404
    assert editor.post(_url(other, pid, "submit")).status_code == 404
    assert editor.delete(_url(other, pid)).status_code == 404


def test_checklist_edit_and_attachment(app_, editor, contract):
    p = editor.post(_url(contract), json={"title": "Đợt 1", "amount": 100, "documents": []}).json()["items"][0]
    d = editor.post(_url(contract, p["id"], "documents"), json={"name": "Bảo lãnh bảo hành", "required": True}).json()
    doc = d["items"][0]["documents"][0]
    up = editor.post(_url(contract, p["id"], "documents", doc["id"], "file"), files={"file": ("bao-lanh.pdf", b"%PDF-1.7 x")})
    assert up.status_code == 200 and up.json()["items"][0]["documents"][0]["file"]["name"] == "bao-lanh.pdf"
    viewer = _login(app_, "viewer1")
    assert viewer.get(_url(contract)).status_code == 200
    assert viewer.put(_url(contract, p["id"], "documents", doc["id"]), json={"done": True}).status_code == 403
    assert editor.delete(_url(contract, p["id"], "documents", doc["id"])).json()["items"][0]["documents"] == []


def test_default_checklist_admin(admin, editor):
    body = {"items": [{"name": "Hóa đơn GTGT", "required": True}, {"name": "Biên bản nghiệm thu", "required": True}]}
    assert editor.put("/api/spend/admin/payment-docs", json=body).status_code == 403
    assert [x["name"] for x in admin.put("/api/spend/admin/payment-docs", json=body).json()] == ["Hóa đơn GTGT", "Biên bản nghiệm thu"]
    assert admin.put("/api/spend/admin/payment-docs", json={"items": [{"name": "A"}, {"name": "a"}]}).status_code == 400
    from app.spend.service import DEFAULT_PAYMENT_DOCS   # restore for other tests
    admin.put("/api/spend/admin/payment-docs", json={"items": [{"name": n, "required": r} for n, r in DEFAULT_PAYMENT_DOCS]})
