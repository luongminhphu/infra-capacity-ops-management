"""Super app SA6: sizing narrative (A7), contract version summary (A8), invoice reading with checks
done by code (A9), "Hỏi ICO" tool loop with the asker's rights (A10), report archive (kho báo cáo).
AI calls are faked; tests check what is sent, what is kept, and who may see what."""
import json
from types import SimpleNamespace as NS

import pytest

from app import ai_advanced, ai_platform
from app.database import SessionLocal
from app.models import AiCache, AiRun, ReportArchive
from app.spend import ai as transport
from app.spend.models import Contract, SpendAiConfig, SpendVendor

from test_ai_platform import KEY, _fake
from test_spend_contracts import BASE, _up, docx
from test_spend_opex import _login, _opex, admin, app_, editor  # noqa: F401  (fixtures)

HOSTS = [{"host": "esx01.corp.local", "cluster": "PRD", "cores": 32, "ramGB": 512, "vcenterKey": "vc1"},
         {"host": "esx02.corp.local", "cluster": "DEV", "cores": 16, "ramGB": 256, "vcenterKey": "vc1"}]
VMS = [{"vm": "sql-prod-01", "cluster": "PRD", "host": "esx01.corp.local", "vcpu": 16, "ramGB": 470, "isPoweredOn": True,
        "ipAddress": "10.1.2.3", "vcenterKey": "vc1"},
       {"vm": "web-dev-01", "cluster": "DEV", "host": "esx02.corp.local", "vcpu": 2, "ramGB": 4, "isPoweredOn": True, "vcenterKey": "vc1"}]


@pytest.fixture(autouse=True)
def _clean(app_, monkeypatch):
    db = SessionLocal()
    for m in (AiRun, AiCache, SpendAiConfig, ReportArchive):
        db.query(m).delete()
    db.commit()
    db.close()
    from app import data_access, inbox
    inbox.invalidate()
    ai_platform._names_cache.update(names=None)
    monkeypatch.setattr(data_access, "get_latest_combined", lambda db, keys=None, include_import=True: (HOSTS, VMS, []))
    from app.services import entities
    entities._cache.clear()
    yield
    inbox.invalidate()


def _enable(admin, **kw):
    r = admin.put("/api/admin/ai", json={"provider": "anthropic", "apiKey": KEY, "allowExternal": True, **kw})
    assert r.status_code == 200, r.text


def _facts(seen):
    return json.loads(seen[-1]["text"].split("\n", 1)[1])


# ---------------------------------------------------------------- A7
SIZING = {"vms": [{"name": "app", "qty": 10, "vcpu": 8, "ramGB": 32, "diskGB": 200}],
          "profiles": [{"name": "2x32C/1TB", "sockets": 2, "coresPerSocket": 32, "ramGB": 1024, "unitPrice": 500_000_000},
                       {"name": "2x16C/512G", "sockets": 2, "coresPerSocket": 16, "ramGB": 512, "unitPrice": 300_000_000}]}


def test_sizing_narrative(app_, admin, editor, monkeypatch):
    seen = []
    answer = {"headline": "Đề xuất 2 host", "narrative": ["Đoạn 1", ""], "risks": [{"level": "boom", "text": "r"}],
              "questions": ["Tăng trưởng 3 năm?"], "customerSummary": "Kính gửi…"}
    monkeypatch.setattr(transport, "JSON_CALL", _fake(answer, seen))
    assert editor.post("/api/sizing/ai-narrative", json=SIZING).status_code == 409             # AI off
    _enable(admin)
    r = editor.post("/api/sizing/ai-narrative", json=SIZING)
    assert r.status_code == 200, r.text
    f = _facts(seen)
    assert f["nhu_cau"]["vmCount"] == 10 and f["nhu_cau"]["vcpu"] == 80
    assert [p["phuong_an"] for p in f["phuong_an"]] and sum(1 for p in f["phuong_an"] if p["de_xuat"]) == 1
    out = r.json()
    assert out["narrative"] == ["Đoạn 1"] and out["risks"][0]["level"] == "info" and out["questions"] == ["Tăng trưởng 3 năm?"]


# ---------------------------------------------------------------- A8
@pytest.fixture()
def contract(editor):
    o = editor.post("/api/spend/opex", json=_opex(assets=[])).json()
    return editor.post("/api/spend/contracts", json={"title": "HĐ bảo hành Dell 2026", "opexCode": o["code"]}).json()


def test_contract_diff_summary(app_, admin, editor, contract, monkeypatch):
    code = contract["code"]
    assert _up(editor, code, docx(*BASE)).status_code == 200
    v2 = [BASE[0], BASE[1], "Điều 2. Giá trị: 120.000.000 đồng.", "Điều 3. Thanh toán trong 10 ngày.", BASE[4], "Điều 5. Phạt chậm thanh toán 0,5%/ngày."]
    assert _up(editor, code, docx(*v2), party="partner", base=1).status_code == 200
    _enable(admin)
    seen = []
    answer = {"headline": "Đối tác tăng giá và rút ngắn hạn thanh toán",
              "changes": [{"ref": 1, "topic": "payment", "summary": "30 → 10 ngày", "risk": "high", "why": "Bên mua phải trả sớm"},
                          {"ref": 0, "topic": "price", "summary": "100 → 120 triệu", "risk": "medium", "why": "Tăng 20%"},
                          {"ref": 7, "topic": "sla", "summary": "không có", "risk": "high", "why": "x"},
                          {"ref": 1, "topic": "payment", "summary": "trùng", "risk": "low", "why": "x"}],
              "watch": ["Đàm phán lại hạn thanh toán"]}
    monkeypatch.setattr(transport, "JSON_CALL", _fake(answer, seen))
    viewer = _login(app_, "viewer1")
    r = viewer.post(f"/api/spend/contracts/{code}/diff/ai?from=1&to=2")
    assert r.status_code == 200, r.text
    ch = _facts(seen)["thay_doi"]
    assert len(ch) == 3 and ch[0]["cu"].endswith("100.000.000 đồng.") and ch[0]["moi"].endswith("120.000.000 đồng.")
    assert ch[2]["cu"] == "" and "Phạt" in ch[2]["moi"] and ch[2]["ben_sua"] == "partner"
    out = r.json()
    assert [(c["ref"], c["risk"], c["topicLabel"]) for c in out["changes"]] == [(1, "high", "Thanh toán"), (0, "medium", "Giá")]
    assert _login(app_, "nobody1").post(f"/api/spend/contracts/{code}/diff/ai?from=1&to=2").status_code == 403


# ---------------------------------------------------------------- A9
def test_money_parser():
    assert ai_advanced._money("1.234.567.890") == 1234567890
    assert ai_advanced._money("1,234,567") == 1234567 and ai_advanced._money("1.000.000,00 đ") == 1000000
    assert ai_advanced._money("abc") is None and ai_advanced._money("9" * 20) is None


def test_invoice_read_checks_by_code(app_, admin, editor, monkeypatch):
    o = editor.post("/api/spend/opex", json=_opex(assets=[], amountNet=1_000_000_000)).json()
    c = editor.post("/api/spend/contracts", json={"title": "HĐ bảo hành", "opexCode": o["code"]}).json()
    url = f"/api/spend/contracts/{c['code']}/payments"
    p = editor.post(url, json={"title": "Đợt 1", "percent": 30}).json()["items"][0]           # 330.000.000
    doc = next(d for d in p["documents"] if d["name"] == "Hóa đơn GTGT")
    base = f"{url}/{p['id']}/documents/{doc['id']}"
    assert editor.post(f"{base}/ai-read").status_code == 400                                      # no file yet
    assert editor.post(f"{base}/file", files={"file": ("hd.pdf", b"%PDF-1.7 hoa don")}).status_code == 200
    db = SessionLocal()
    v = db.get(SpendVendor, db.query(Contract).filter_by(code=c["code"]).one().vendor_id)
    v.tax_code = "0101234567"
    db.commit()
    db.close()
    _enable(admin)
    sent = []

    def fake_pdf(cfg, key, system, pdf, instruction, schema, max_tokens):
        sent.append(pdf)
        ai_platform.note_usage(NS(model="claude-opus-5-5", usage=NS(input_tokens=2000, output_tokens=300)))
        return json.dumps({"isInvoice": True, "invoiceNo": "0000123", "invoiceSerial": "1C26TAA", "invoiceDate": "2026-11-10",
                           "sellerName": "Dell VN", "sellerTaxCode": "0101234567", "buyerName": "Công ty", "buyerTaxCode": "0300000000",
                           "contractRef": c["code"], "amountBeforeTax": "300.000.000", "vatAmount": "30.000.000",
                           "totalAmount": "330.000.001", "currency": "VND"})
    monkeypatch.setattr(transport, "PDF_JSON_CALL", fake_pdf)
    assert _login(app_, "viewer1").post(f"{base}/ai-read").status_code == 403
    r = editor.post(f"{base}/ai-read")
    assert r.status_code == 200, r.text
    ck = {x["key"]: x for x in r.json()["checks"]}
    assert ck["amount"]["status"] == "bad" and ck["amount"]["invoice"] == 330_000_001 and "+1" in ck["amount"]["note"]
    assert ck["arith"]["status"] == "bad"                         # 300 + 30 ≠ 330.000.001
    assert ck["tax"]["status"] == "ok" and ck["contract"]["status"] == "ok" and r.json()["ok"] is False
    assert sent == [b"%PDF-1.7 hoa don"]
    assert editor.post(f"{base}/ai-read").json()["invoice"]["cached"] is True and len(sent) == 1   # same file: no new call


# ---------------------------------------------------------------- A10
def _tool_script(steps, seen):
    """Fake TOOLS_CALL: plays `steps` (list of block lists) in order, recording messages sent."""
    it = iter(steps)

    def call(cfg, key, system, messages, tools, max_tokens):
        seen.append(json.loads(json.dumps(messages, default=str)))
        ai_platform.note_usage(NS(model="claude-opus-5-5", usage=NS(input_tokens=1000, output_tokens=100)))
        blocks = next(it)
        return {"stop": "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn", "content": blocks}
    return call


def test_ask_ico_tool_loop_masks_and_respects_rights(app_, admin, editor, monkeypatch):
    _enable(admin)
    seen = []
    # 1st turn: the model asks capacity of PRD and the card of a VM given as a masked token
    m = ai_platform.masker_for(SessionLocal())
    tok = m.mask("sql-prod-01")
    steps = [[{"type": "tool_use", "id": "t1", "name": "capacity_cluster", "input": {"cluster": "PRD"}},
              {"type": "tool_use", "id": "t2", "name": "thong_tin_vm", "input": {"name": tok}},
              {"type": "tool_use", "id": "t3", "name": "xoa_vm", "input": {"name": "x"}}],
             [{"type": "text", "text": f"Cluster PRD có 1 VM lớn nhất là {tok} (16 vCPU)."}]]
    monkeypatch.setattr(transport, "TOOLS_CALL", _tool_script(steps, seen))
    r = editor.post("/api/ai/ask", json={"question": "sql-prod-01 có phải VM lớn nhất ở cluster PRD không, IP 10.1.2.3?"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert "sql-prod-01" in out["answer"] and out["tools"] == ["capacity_cluster", "thong_tin_vm", "xoa_vm"]
    assert out["links"] == [{"type": "vm", "name": "sql-prod-01", "vcenterKey": "vc1"}]
    sent = json.dumps(seen, ensure_ascii=False)
    assert "10.1.2.3" not in sent and "sql-prod-01" not in sent and "esx01" not in sent    # question + tool results masked
    results = {x["tool_use_id"]: x for x in seen[1][-1]["content"]}
    assert json.loads(results["t1"]["content"])[0]["cluster"] == "PRD" and results["t3"]["is_error"] is True
    assert json.loads(results["t2"]["content"])["config"]["vcpu"] == 16                       # token resolved before lookup
    db = SessionLocal()
    run = db.query(AiRun).filter_by(task="ask_ico").one()
    assert (run.input_tokens, run.output_tokens) == (2000, 200)                                # both turns accounted
    db.close()
    assert editor.post("/api/ai/ask", json={"question": "a"}).status_code == 400
    # a loop that keeps asking for tools stops at the token ceiling with a clear message
    def greedy(cfg, key, system, messages, tools, max_tokens):
        ai_platform.note_usage(NS(model="claude-opus-5-5", usage=NS(input_tokens=70_000, output_tokens=100)))
        return {"stop": "tool_use", "tokens": 70_100, "content": [{"type": "tool_use", "id": f"g{len(messages)}", "name": "capacity_cluster", "input": {}}]}
    monkeypatch.setattr(transport, "TOOLS_CALL", greedy)
    r = editor.post("/api/ai/ask", json={"question": "Liệt kê tất cả mọi thứ"})
    assert r.status_code == 422 and "hẹp hơn" in r.json()["detail"]


def test_ask_ico_inbox_tool_is_role_filtered(app_, admin, monkeypatch):
    _enable(admin)
    nobody = _login(app_, "nobody1")                                  # no spend role
    from app import inbox
    items = [{"source": "spend", "level": "warn", "title": "Opex OPX-1 sắp hết hạn", "detail": "", "due": None, "key": "s1", "role": "spend:viewer"},
             {"source": "capacity", "level": "crit", "title": "Cluster PRD vượt ngưỡng", "detail": "", "due": None, "key": "c1"}]
    monkeypatch.setattr(inbox, "collect", lambda db, today=None, use_cache=True: (items, []))
    seen = []
    monkeypatch.setattr(transport, "TOOLS_CALL", _tool_script([[{"type": "tool_use", "id": "a", "name": "viec_can_lam", "input": {}}],
                                                               [{"type": "text", "text": "ok"}]], seen))
    assert nobody.post("/api/ai/ask", json={"question": "Hôm nay có việc gì?"}).status_code == 200
    titles = [v["noi_dung"] for v in json.loads(seen[1][-1]["content"][0]["content"])["viec"]]
    assert titles == ["Cluster PRD vượt ngưỡng"]


# ---------------------------------------------------------------- archive
def test_report_archive_via_header(app_, admin, editor, monkeypatch):
    r = editor.get("/api/backup/report/pdf?days=7", headers={"X-ICO-Archive": "1"})
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    r2 = editor.post("/api/spend/reports/budget.pdf", json={}, headers={"X-ICO-Archive": "1"})
    editor.get("/api/backup/report/pdf?days=3")                                              # no header: not archived
    lst = editor.get("/api/report-archive").json()["items"]
    kinds = sorted(x["kind"] for x in lst)
    assert "backup" in kinds and len([k for k in kinds if k == "backup"]) == 1
    b = next(x for x in lst if x["kind"] == "backup")
    assert b["createdBy"] == "editor1" and "days=7" in b["params"] and b["filename"].endswith(".pdf")
    f = editor.get(f"/api/report-archive/{b['id']}/file")
    assert f.status_code == 200 and f.content.startswith(b"%PDF") and f.headers["content-type"] == "application/pdf"
    nobody = _login(app_, "nobody1")
    if r2.status_code == 200:
        sp = next(x for x in lst if x["kind"] == "spend")
        assert all(x["kind"] != "spend" for x in nobody.get("/api/report-archive").json()["items"])   # no spend role
        assert nobody.get(f"/api/report-archive/{sp['id']}/file").status_code == 404
    assert nobody.delete(f"/api/report-archive/{b['id']}").status_code == 403
    # same bytes again: not stored twice; non-report routes (archive download itself) never archived
    n0 = len(editor.get("/api/report-archive").json()["items"])
    editor.get(f"/api/report-archive/{b['id']}/file", headers={"X-ICO-Archive": "1"})
    assert len(editor.get("/api/report-archive").json()["items"]) == n0
    from app import report_archive as ra
    assert ra.kind_of("/api/spend/files/3") is None and ra.kind_of("/api/report-archive/1/file") is None
    assert ra.kind_of("/api/spend/reports/budget.pdf") == ("spend", "spend")
    # per-user trimming never touches other users' copies
    monkeypatch.setattr(ra, "MAX_PER_USER", 2)
    for i in range(3):
        ra.save("viewer1", "/api/reports/pdf", f"i={i}", "", b"%PDF-1.7 v" + bytes([i]))
    db = SessionLocal()
    assert db.query(ReportArchive).filter_by(created_by="viewer1").count() == 2
    assert db.query(ReportArchive).filter_by(created_by="editor1").count() >= 1
    db.close()
    assert admin.delete(f"/api/report-archive/{b['id']}").status_code == 200
