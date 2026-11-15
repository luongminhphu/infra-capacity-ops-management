"""Contracts: single branch of versions, comparison with attribution, Final / Signed, purge."""
import datetime as dt
import io
import zipfile
from xml.sax.saxutils import escape

import pytest

from app.database import SessionLocal
from app.models import AuditLog
from app.spend import contracts, files
from app.spend.models import Contract, ContractVersion, SpendFile

from test_spend_opex import _login, _opex, admin, app_, editor  # noqa: F401  (fixtures)

NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def docx(*paragraphs: str, doctype: bool = False) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{escape(p)}</w:t></w:r></w:p>" for p in paragraphs)
    xml = (('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]>' if doctype else '<?xml version="1.0"?>')
           + f'<w:document xmlns:w="{NS}"><w:body>{body}</w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


BASE = ["HỢP ĐỒNG DỊCH VỤ", "Điều 1. Phạm vi: bảo hành 4 máy chủ.", "Điều 2. Giá trị: 100.000.000 đồng.",
        "Điều 3. Thanh toán trong 30 ngày.", "Điều 4. Hiệu lực 12 tháng."]


@pytest.fixture()
def contract(editor):
    o = editor.post("/api/spend/opex", json=_opex(assets=[])).json()
    r = editor.post("/api/spend/contracts", json={"title": "HĐ bảo hành Dell 2026", "opexCode": o["code"]})
    assert r.status_code == 200, r.text
    return r.json()


def _up(client, code, data, party="legal", base=0, note=None, name="hd.docx"):
    form = {"editorParty": party, "baseVersion": str(base)}
    if note:
        form["note"] = note
    return client.post(f"/api/spend/contracts/{code}/versions", files={"file": (name, data)}, data=form)


def test_link_rules(editor):
    o = editor.post("/api/spend/opex", json=_opex(assets=[])).json()
    assert editor.post("/api/spend/contracts", json={"title": "x"}).status_code == 400
    assert editor.post("/api/spend/contracts", json={"title": "x", "opexCode": o["code"], "capexCode": "CPX-2026-0001"}).status_code == 400
    assert editor.post("/api/spend/contracts", json={"title": "x", "opexCode": "OPX-1999-0001"}).status_code == 400
    c = editor.post("/api/spend/contracts", json={"title": "x", "opexCode": o["code"]}).json()
    assert c["code"] == "HD-2026-0001" and c["linkCode"] == o["code"] and c["vendorName"] == "Dell Việt Nam"
    db = SessionLocal()                     # the database refuses a contract with both links
    row = db.query(Contract).filter_by(code=c["code"]).one()
    row.capex_project_id = row.opex_id
    with pytest.raises(Exception):
        db.commit()
    db.rollback()
    db.close()
    assert editor.get(f"/api/spend/opex/{o['code']}").json()["contracts"][0]["code"] == c["code"]


def test_single_branch_optimistic_lock(contract, editor):
    code = contract["code"]
    r = _up(editor, code, docx(*BASE), "legal", 0, "Bản đầu")
    assert r.status_code == 200 and r.json()["currentVersion"] == 1 and r.json()["status"] == "draft"
    r = _up(editor, code, docx(*BASE[:2], "Điều 2. Giá trị: 95.000.000 đồng.", *BASE[3:]), "partner", 1)
    assert r.status_code == 200 and r.json()["currentVersion"] == 2 and r.json()["status"] == "negotiating"
    # Someone still on v1 uploads: refused, and nothing is stored.
    stale = _up(editor, code, docx(*BASE), "finance", 1)
    assert stale.status_code == 409 and "v2" in stale.json()["detail"]
    db = SessionLocal()
    assert db.query(ContractVersion).count() == 2
    c = db.query(Contract).filter_by(code=code).one()
    tmp = files.files_dir() / "tmp"
    assert not tmp.exists() or not any(tmp.iterdir())
    db.close()
    # Only .docx, valid content, no DOCTYPE.
    assert _up(editor, code, b"%PDF-1.4 x", "legal", 2, name="a.pdf").status_code == 415
    assert _up(editor, code, docx("x", doctype=True), "legal", 2).status_code == 415
    assert _up(editor, code, docx("x"), "boss", 2).status_code == 400
    v = editor.get(f"/api/spend/contracts/{code}").json()["versions"]
    assert [(x["versionNo"], x["party"]) for x in v] == [(2, "partner"), (1, "legal")] and v[1]["note"] == "Bản đầu"
    assert c.current_version_no == 2


def test_compare_attributes_changes_to_the_version_that_made_them(contract, editor):
    code = contract["code"]
    v1 = BASE
    v2 = [BASE[0], BASE[1], "Điều 2. Giá trị: 95.000.000 đồng.", BASE[3], BASE[4]]               # partner: price
    v3 = [BASE[0], BASE[1], "Điều 2. Giá trị: 95.000.000 đồng.", "Điều 3. Thanh toán trong 45 ngày.",   # finance: payment
          BASE[4], "Điều 5. Phạt chậm thanh toán 0,05%/ngày."]                                       # finance: new article
    v4 = [p for p in v3 if not p.startswith("Điều 4")]                                              # legal: removes art. 4
    for i, (paras, party) in enumerate(((v1, "legal"), (v2, "partner"), (v3, "finance"), (v4, "legal"))):
        assert _up(editor, code, docx(*paras), party, i).status_code == 200
    d = editor.get(f"/api/spend/contracts/{code}/diff?from=1&to=4").json()
    changes = [(b["op"], b["party"], b.get("text") or "".join(w["text"] for w in b["words"] if w["op"] != "delete"))
               for b in d["blocks"] if b["op"] != "equal"]
    assert ("change", "partner", "Điều 2. Giá trị: 95.000.000 đồng.") in changes
    assert ("change", "finance", "Điều 3. Thanh toán trong 45 ngày.") in changes
    assert ("insert", "finance", "Điều 5. Phạt chậm thanh toán 0,05%/ngày.") in changes
    assert ("delete", "legal", "Điều 4. Hiệu lực 12 tháng.") in changes
    assert d["changesByParty"] == {"legal": 1, "finance": 2, "partner": 1}
    price = next(b for b in d["blocks"] if b["op"] == "change" and b["party"] == "partner")
    assert {"op": "delete", "text": "100.000.000"} in price["words"] and {"op": "insert", "text": "95.000.000"} in price["words"]
    assert editor.get(f"/api/spend/contracts/{code}/diff?from=3&to=2").status_code == 400


def test_finalize_sign_and_purge(app_, admin, contract, editor):
    approver = _login(app_, "approver1")
    code = contract["code"]
    for i, party in enumerate(("legal", "partner", "finance")):
        _up(editor, code, docx(*BASE, f"Sửa lần {i}"), party, i)
    assert editor.post(f"/api/spend/contracts/{code}/finalize", json={}).status_code == 403
    r = approver.post(f"/api/spend/contracts/{code}/finalize", json={"versionNo": 2})
    assert r.status_code == 200 and r.json()["status"] == "final" and r.json()["finalVersion"] == 2
    assert _up(editor, code, docx("x"), "legal", 3).status_code == 409             # no versions after Final
    assert editor.put(f"/api/spend/contracts/{code}", json={"title": "Đổi tên"}).status_code == 409
    # Signed scan: PDF only.
    assert approver.post(f"/api/spend/contracts/{code}/sign", files={"file": ("ky.docx", docx("x"))}).status_code == 415
    s = approver.post(f"/api/spend/contracts/{code}/sign", files={"file": ("ban-ky.pdf", b"%PDF-1.7 signed")})
    assert s.status_code == 200 and s.json()["status"] == "signed" and s.json()["signedFile"]["name"] == "ban-ky.pdf"
    # Before the waiting time: nothing purged.
    db = SessionLocal()
    assert contracts.purge_due(db) == []
    c = db.query(Contract).filter_by(code=code).one()
    due = c.purge_due_at
    assert (due - c.finalized_at).days == 30
    purged = contracts.purge_due(db, due + dt.timedelta(minutes=1))
    assert purged == [code]
    vs = {v.version_no: v for v in db.query(ContractVersion).filter_by(contract_id=c.id)}
    fl = {f.id: f for f in db.query(SpendFile)}
    assert fl[vs[1].file_id].purged_at and fl[vs[3].file_id].purged_at and fl[vs[2].file_id].purged_at is None
    assert not files.path_of(fl[vs[1].file_id]).exists() and files.path_of(fl[vs[2].file_id]).exists()
    assert vs[1].text_content is None and vs[2].text_content is not None
    assert fl[c.signed_file_id].purged_at is None                                     # signed scan kept
    assert db.query(AuditLog).filter(AuditLog.action == "spend.contract.purge", AuditLog.detail.like(f"%{code}%")).count() == 1
    assert contracts.purge_due(db, due + dt.timedelta(days=1)) == []                   # idempotent
    db.close()
    # Metadata stays; the file answers 410; comparing a purged version is refused.
    d = editor.get(f"/api/spend/contracts/{code}").json()
    v1 = next(v for v in d["versions"] if v["versionNo"] == 1)
    assert v1["file"]["purged"] is True and v1["hasText"] is False
    assert editor.get(f"/api/spend/files/{v1['file']['id']}").status_code == 410
    assert editor.get(f"/api/spend/contracts/{code}/diff?from=1&to=2").status_code == 410
    assert admin.delete(f"/api/spend/contracts/{code}").status_code == 409


def test_admin_can_cancel_purge(app_, admin, contract, editor):
    approver = _login(app_, "approver1")
    code = contract["code"]
    _up(editor, code, docx(*BASE), "legal", 0)
    _up(editor, code, docx(*BASE, "x"), "partner", 1)
    approver.post(f"/api/spend/contracts/{code}/finalize", json={})
    assert approver.post(f"/api/spend/contracts/{code}/purge/cancel").status_code == 403
    r = admin.post(f"/api/spend/contracts/{code}/purge/cancel")
    assert r.status_code == 200 and r.json()["purgeCancelledBy"] == "admin"
    db = SessionLocal()
    assert contracts.purge_due(db, dt.datetime.utcnow() + dt.timedelta(days=60)) == []
    db.close()
    assert admin.post(f"/api/spend/contracts/{code}/purge/resume").status_code == 200
    db = SessionLocal()
    assert contracts.purge_due(db, dt.datetime.utcnow() + dt.timedelta(days=60)) == [code]
    db.close()


def test_opex_with_contract_cannot_be_deleted(admin, contract):
    assert admin.delete(f"/api/spend/opex/{contract['linkCode']}").status_code == 409


def test_draft_contract_delete_removes_files(admin, contract, editor):
    code = contract["code"]
    _up(editor, code, docx(*BASE), "legal", 0)
    db = SessionLocal()
    f = db.query(SpendFile).order_by(SpendFile.id.desc()).first()
    path = files.path_of(f)
    db.close()
    assert path.exists()
    assert editor.delete(f"/api/spend/contracts/{code}").status_code == 403
    assert admin.delete(f"/api/spend/contracts/{code}").status_code == 200
    assert not path.exists()


def test_docx_paragraph_extraction():
    assert contracts.docx_paragraphs(docx("A", "  ", "B & C")) == ["A", "B & C"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x><x/>'.encode("utf-16"))
    with pytest.raises(contracts.DocxTextError):
        contracts.docx_paragraphs(buf.getvalue())
    with pytest.raises(contracts.DocxTextError):
        contracts.docx_paragraphs(docx("A", doctype=True))
