"""VM theo hệ thống (PDF / Excel) and the ISO 27001 owner review e-mail drafts (owners/inventory.py, owners/mail_drafts.py)."""
import email
import io
import json
import zipfile
from email import policy

from openpyxl import load_workbook

from app.database import SessionLocal
from app.models import AuditLog
from app.owners import mail_drafts
from app.owners.models import OwnerMailTemplate
from test_owners import _sys, vms  # noqa: F401  (fixtures)
from test_spend_opex import _login, admin, app_  # noqa: F401  (fixtures)


def _setup(admin, vms):  # noqa: F811
    a = _sys(admin, deputyEmail="bk.cb@corp.example", deputyName="Lê C", supportEmails="dev1@corp.example").json()
    b = _sys(admin, name="Website", ownerEmail="lead.cb@corp.example", criticality=3).json()   # same owner, 2 systems
    c = _sys(admin, name="Kho <b>dữ liệu</b>", ownerEmail="").json()                          # no owner
    admin.post(f"/api/systems/{a['id']}/members", json={"ciIds": [vms["own-app-01"], vms["own-app-02"]]})
    admin.post(f"/api/systems/{b['id']}/members", json={"ciIds": [vms["own-web-01"]]})
    return a, b, c


def test_inventory_report_pdf_and_excel(app_, admin, vms):  # noqa: F811
    a, b, c = _setup(admin, vms)
    from app.owners import inventory
    s = SessionLocal()
    d = inventory.build(s)
    s.close()
    t = d["totals"]
    assert (t["systems"], t["vms"], t["noOwner"], t["noVm"], t["unowned"]) == (3, 3, 1, 1, 1)   # own-db-01 is unowned
    cb = next(x for x in d["systems"] if x["code"] == a["code"])
    assert [v["vm"] for v in cb["vms"]] == ["own-app-01", "own-app-02"] and cb["backupEmail"] == "bk.cb@corp.example"
    assert cb["supportEmails"] == ["dev1@corp.example"] and d["unowned"][0]["vm"] == "own-db-01"

    r = admin.get("/api/systems/report.xlsx")
    assert r.status_code == 200 and "vm-theo-he-thong-" in r.headers["content-disposition"]
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Tổng hợp", "VM theo hệ thống", "VM chưa thuộc hệ thống"]
    rows = [[c.value for c in row] for row in wb["VM theo hệ thống"].iter_rows()]
    assert [a["code"], "Core Banking", "lead.cb@corp.example", "own-app-01"] == rows[-3][:4]
    total = [[c.value for c in row] for row in wb["Tổng hợp"].iter_rows()][-1]
    assert total[0] == "Tổng" and total[12] == 3

    # owner filter: only that system, no "unowned" sheet
    r = admin.get(f"/api/systems/report.xlsx?systems={b['code']}")
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Tổng hợp", "VM theo hệ thống"]
    assert [row[3].value for row in wb["VM theo hệ thống"].iter_rows()][-1] == "own-web-01"

    from app import report_systems
    s = SessionLocal()
    html = report_systems.render_html(inventory.build(s), "admin")
    s.close()
    assert "Kho &lt;b&gt;dữ liệu&lt;/b&gt;" in html and "<b>dữ liệu</b>" not in html        # escaped
    assert "3. VM chưa thuộc hệ thống (1)" in html and "A.5.9" in html

    viewer = _login(app_, "viewer1")
    assert viewer.get("/api/systems/report.xlsx").status_code == 200                   # read: any signed-in user
    assert viewer.get("/api/systems/mail-template").status_code == 403                 # drafts: owner admin only
    assert viewer.get("/api/systems/mail-drafts").status_code == 403
    assert any(e["id"] == "systems-vm-pdf" for e in admin.get("/api/exports").json()["items"])


def test_mail_template_validation_save_reset(app_, admin, vms):  # noqa: F811
    t = admin.get("/api/systems/mail-template").json()
    assert t["custom"] is False and "{vm_table}" in t["body"] and "A.5.18" in t["body"]
    r = admin.put("/api/systems/mail-template", json={"subject": "Rà soát {period}", "body": "Chào {owner_nam}"})
    assert r.status_code == 400 and "{owner_nam}" in r.json()["detail"]
    assert admin.put("/api/systems/mail-template", json={"subject": "{vm_table}", "body": "x"}).status_code == 400
    assert admin.put("/api/systems/mail-template", json={"subject": "", "body": "x"}).status_code == 400
    r = admin.put("/api/systems/mail-template", json={"subject": "Rà soát {period}\r\nBcc: x@evil.example",
                                                       "body": "Chào {owner_name}, {{literal}} {obj.__class__}\n{vm_table}"})
    assert r.status_code == 400                                                        # {literal} is not a known field
    r = admin.put("/api/systems/mail-template", json={"subject": "Rà soát {period}\r\nBcc: x@evil.example",
                                                       "body": "Chào {owner_name}\n{vm_table}\nHạn {due_date}"})
    assert r.status_code == 200 and r.json()["custom"] is True
    assert r.json()["subject"] == "Rà soát {period} Bcc: x@evil.example"               # no CR/LF kept
    assert admin.delete("/api/systems/mail-template").json()["custom"] is False
    s = SessionLocal()
    assert s.get(OwnerMailTemplate, "review") is None
    assert s.query(AuditLog).filter(AuditLog.action == "owners.mail_template.save").count() >= 1
    s.close()


def test_mail_drafts_group_by_owner_preview_and_eml(app_, admin, vms):  # noqa: F811
    a, b, c = _setup(admin, vms)
    r = admin.get("/api/systems/mail-drafts?period=2027-H1&due=2027-01-26").json()
    assert r["period"] == "2027-H1" and r["due"] == "2027-01-26"
    assert [o["email"] for o in r["owners"]] == ["lead.cb@corp.example"]
    o = r["owners"][0]
    assert o["vmCount"] == 3 and o["cc"] == ["bk.cb@corp.example"] and len(o["systems"]) == 2
    assert [x["code"] for x in r["noOwner"]] == [c["code"]]
    assert admin.get("/api/systems/mail-drafts?due=26/01/2027").status_code == 400
    assert admin.get("/api/systems/mail-drafts?period=a%0Ab").status_code == 400

    p = admin.get("/api/systems/mail-drafts/preview?owner=Lead.CB@corp.example&period=2027-H1&due=2027-01-26").json()
    assert p["subject"] == "[ICO] Rà soát tài nguyên định kỳ 2027-H1 — 2 hệ thống, 3 VM — hạn 26/01/2027"
    assert "Kính gửi Trần B," in p["text"] and "own-web-01" in p["text"] and "A.5.11" in p["text"]
    assert "<table" in p["html"] and "own-app-02" in p["html"]
    # unsaved template preview (POST) — values are escaped in HTML
    p = admin.post("/api/systems/mail-drafts/preview?owner=lead.cb@corp.example",
                   json={"subject": "Hi {owner_name}", "body": "Hệ thống: {system_list}\n\n{vm_table}"}).json()
    assert p["subject"] == "Hi Trần B" and "<table" in p["html"]
    assert admin.get("/api/systems/mail-drafts/preview?owner=nobody@corp.example").status_code == 404

    r = admin.get("/api/systems/mail-drafts/download?owner=lead.cb@corp.example&period=2027-H1&due=2027-01-26")
    assert r.status_code == 200 and r.headers["content-type"].startswith("message/rfc822")
    msg = email.message_from_bytes(r.content, policy=policy.default)
    assert msg["X-Unsent"] == "1" and msg["To"] == "lead.cb@corp.example" and msg["Cc"] == "bk.cb@corp.example"
    assert msg["Subject"].startswith("[ICO] Rà soát tài nguyên định kỳ 2027-H1")
    parts = {p.get_content_type(): p for p in msg.walk()}
    assert "text/html" in parts and "text/plain" in parts
    att = [p for p in msg.iter_attachments()]
    assert len(att) == 1 and att[0].get_filename().endswith(".xlsx")
    wb = load_workbook(io.BytesIO(att[0].get_content()))
    assert [row[3].value for row in wb["VM theo hệ thống"].iter_rows()][-3:] == ["own-app-01", "own-app-02", "own-web-01"]

    r = admin.get("/api/systems/mail-drafts/download?attach=false&period=2027-H1")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert z.namelist() == ["ra-soat-2027-h1-lead-cb.eml"]
    m2 = email.message_from_bytes(z.read(z.namelist()[0]), policy=policy.default)
    assert not list(m2.iter_attachments())
    s = SessionLocal()
    logs = [json.loads(x.detail) for x in s.query(AuditLog).filter(AuditLog.action == "owners.mail_drafts.download")]
    s.close()
    assert {"owners": 1, "period": "2027-H1"} in logs


def test_render_escapes_values_and_unknown_braces():
    o = {"email": "o@corp.example", "name": "<script>x</script>", "cc": [],
         "systems": [{"code": "SYS-0001", "name": "A&B", "backupEmail": None, "backupName": None,
                      "vms": [{"vm": "<img src=x>", "vcenterKey": "vc", "powerOn": True, "vcpu": 2, "ramGB": 4.5, "diskGB": 100,
                               "os": None, "backupLabel": "—", "backup": None}]}],
         "totals": {"vms": 1, "on": 1, "off": 0, "vcpu": 2, "ramGB": 4.5, "diskGB": 100, "unprotected": 0, "failing": 0}}
    import datetime as dt
    tpl = {"subject": "{owner_name}", "body": "Chào {owner_name} — {system_list} — {ram_gb} GB {{x}}\n{vm_table}"}
    subject, text, html = mail_drafts.render(tpl, o, "2027-H1", dt.date(2027, 1, 26), "Hạ tầng", False, today=dt.date(2026, 11, 20))
    assert "<script>" not in html and "&lt;script&gt;" in html and "&lt;img src=x&gt;" in html and "A&amp;B" in html
    assert "4,5 GB {{x}}" in text and "Chào <script>x</script>" in text                  # text part is plain text
