"""Vendor quotations (V1): parsing, formula checks, comparison, evaluation form, selection.
All data is synthetic; amounts mirror the kind of discrepancies found in real renovation files."""
import io
import json
from decimal import Decimal

import pytest
from openpyxl import Workbook, load_workbook

from app.spend import ai, quotes as qm

from test_spend_opex import _login, admin, app_, editor  # noqa: F401  (fixtures)

D = Decimal
KEY = "sk-ant-test-quotes-0000"


# ---------------------------------------------------------------- pure logic
@pytest.mark.parametrize("raw,expected", [
    ("1.234.567", D("1234567")), ("1,234,567", D("1234567")), ("12.977.000 đ", D("12977000")),
    ("1.234,5", D("1234.5")), ("1,234.5", D("1234.5")), ("8%", D("8")), ("2,5", D("2.5")), ("0.5", D("0.5")),
    ("1.000", D("1000")), (913109394, D("913109394")), ("", None), ("—", None), (None, None),
])
def test_parse_number(raw, expected):
    assert qm.parse_number(raw) == expected


def test_check_finds_line_subtotal_vat_and_total_errors():
    lines = [
        qm.Line("item", "Cáp Cat6A", unit="m", qty=D(305), unit_price=D(25000), amount=D(7625000)),
        qm.Line("item", "Patch panel 24 port", qty=D(4), unit_price=D(2500000), amount=D(9000000)),     # 4 × 2,5tr = 10tr
        qm.Line("item", "Ổ cắm mạng", qty=D(20), unit_price=None, amount=D(1000000)),
        qm.Line("item", "Patch panel 24 port", qty=D(1), unit_price=D(100), amount=D(0)),
    ]
    f, comp = qm.check(lines, subtotal=D(18_000_000), discount=None, vat=D(1_500_000), total=D(19_000_000), vat_pct=D(8))
    codes = sorted(x.code for x in f)
    assert codes == ["DUP", "LINE", "LINE", "MISSING", "SUBTOTAL", "TOTAL", "VAT", "ZERO"]
    line = next(x for x in f if x.code == "LINE" and x.line_index == 1)
    assert (line.expected, line.actual) == (D(10_000_000), D(9_000_000))
    assert comp["subtotal"] == D(17_625_000)
    assert comp["vat"] == D(1_440_000) and comp["total"] == D(18_000_000 + 1_500_000)


def test_check_guesses_vat_rate_and_accepts_rounding():
    lines = [qm.Line("item", "Switch 48 port", qty=D(2), unit_price=D("45500000.4"), amount=D(91_000_001))]
    f, comp = qm.check(lines, subtotal=D(91_000_001), discount=None, vat=D(9_100_000), total=D(100_100_001), vat_pct=None)
    assert f == [] and comp["vatPct"] == D(10)


def test_align_groups_by_model_then_description():
    a = [{"id": 1, "description": "Switch core 48 cổng 10G", "model": "C9500-48Y4C", "amountVnd": D(500)},
         {"id": 2, "description": "Dây nhảy quang LC-LC 3m", "model": None, "amountVnd": D(10)}]
    b = [{"id": 3, "description": "Thiết bị chuyển mạch lõi", "model": "c9500-48y4c", "amountVnd": D(450)},
         {"id": 4, "description": "Dây nhảy quang LC-LC 3 m", "model": None, "amountVnd": D(12)},
         {"id": 5, "description": "Công lắp đặt", "model": None, "amountVnd": D(30)}]
    rows = qm.align([(10, a), (20, b)])
    assert len(rows) == 3
    core = rows[0]
    assert set(core["cells"]) == {10, 20} and core["minQuotation"] == 20 and core["spreadPct"] == 11.1
    assert set(rows[1]["cells"]) == {10, 20} and rows[1]["minQuotation"] == 10
    assert set(rows[2]["cells"]) == {20}


def test_evaluation_totals_reproduce_form():
    """Weights of the form: A 15/15/10/10/10/10 %, B three criteria × 10 %."""
    weights = [w for _, w in qm.STANDARD_CRITERIA] + [D("0.1")] * 3
    ttc = [D(9)] * 9
    thrak = [D(9)] * 6 + [D(10), D(10), D("9.5")]
    vnt = [D(10)] * 4 + [D("9.5"), D("9.5")] + [D(10)] * 3
    totals = [qm.weighted_total(zip(weights, s)) for s in (ttc, thrak, vnt)]
    assert totals == [D("9.00"), D("9.25"), D("9.90")]
    assert [qm.classify(t) for t in totals] == ["A", "A", "A"]
    assert [qm.classify(D(x)) for x in ("7.99", "7", "6.5", "5.99")] == ["B", "B", "C", "Không đạt"]
    assert qm.weighted_total(zip(weights, ttc[:-1] + [None])) is None
    assert qm.validate_criteria([("A", n, w) for n, w in qm.STANDARD_CRITERIA] + [("B", "x", D("0.3"))]).startswith("Khung đánh giá mở rộng")
    assert qm.validate_criteria([("A", n, w) for n, w in qm.STANDARD_CRITERIA] + [("B", "x", D("0.15")), ("B", "y", D("0.15"))]) is None


def test_price_score_hint():
    hint = qm.suggest_price_score({1: D(100), 2: D(110), 3: D(150), 4: None})
    assert hint == {1: D(10), 2: D(8), 3: D(1)}


# ---------------------------------------------------------------- helpers
def _xlsx(rows):
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _quote_file(price_core=450_000_000, wrong_line=False, extra=()):
    amount = 2 * price_core + (5_000_000 if wrong_line else 0)
    items = [[1, "Switch core 48 cổng", "C9500-48Y4C", "Cái", 2, price_core, amount],
             [2, "Dây nhảy quang LC-LC 3m", None, "Sợi", 100, 150_000, 15_000_000], *extra]
    sub = sum(r[6] for r in items)
    vat = round(sub * 0.08)
    return _xlsx([["CÔNG TY TNHH MẪU"], ["BẢNG BÁO GIÁ"], [],
                  ["STT", "Tên hàng hoá", "Model", "ĐVT", "Số lượng", "Đơn giá", "Thành tiền"], *items,
                  [None, "Cộng", None, None, None, None, sub], [None, "Thuế VAT 8%", None, None, None, None, vat],
                  [None, "Tổng cộng", None, None, None, None, sub + vat]])


def _new_request(editor, **kw):
    body = {"title": "Thay thiết bị mạng FL9", "workstream": "network", "estimateAmount": 814_752_128, **kw}
    r = editor.post("/api/spend/quotes", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _upload(editor, code, vendor, content, name="bao-gia.xlsx"):
    return editor.post(f"/api/spend/quotes/{code}/quotations", data={"vendorName": vendor}, files={"file": (name, content)})


# ---------------------------------------------------------------- API
def test_upload_excel_reads_lines_and_flags_formula_error(editor):
    rq = _new_request(editor)
    assert rq["code"].startswith("BG-") and rq["status"] == "draft"
    assert [c["name"] for c in rq["evaluation"]["criteria"] if c["section"] == "B"][0].startswith("Hiểu biết về mô hình")
    d = _upload(editor, rq["code"], "Công ty Mạng Alpha", _quote_file(wrong_line=True)).json()
    q = d["quotations"][0]
    assert d["status"] == "comparing" and q["state"] == "extracted" and q["method"] == "excel"
    assert q["itemCount"] == 2 and q["vatPct"] == 8.0
    assert q["statedSubtotal"] == 920_000_000 and q["computedSubtotal"] == 920_000_000
    assert [f["code"] for f in q["findings"]] == ["LINE"]
    assert q["findings"][0]["expected"] == 900_000_000 and "lệch 5.000.000" in q["findings"][0]["message"]
    # Same vendor twice in one request is refused.
    assert _upload(editor, rq["code"], "CÔNG TY MẠNG ALPHA", _quote_file()).status_code == 409


def test_edit_lines_rechecks_and_verify_and_select(app_, admin, editor):
    approver = _login(app_, "approver1")
    rq = _new_request(editor)
    code = rq["code"]
    _upload(editor, code, "Alpha Networks", _quote_file(price_core=449_054_697))           # 913.109.394 trước VAT
    _upload(editor, code, "Beta Tech", _quote_file(price_core=470_000_000))
    d = _upload(editor, code, "Gamma JSC", _quote_file(price_core=480_000_000, wrong_line=True)).json()
    alpha, beta, gamma = d["quotations"]
    assert alpha["subtotalVnd"] == 913_109_394
    # A person fixes nothing but confirms Alpha; Gamma's vendor error stays visible.
    q = editor.get(f"/api/spend/quotes/{code}/quotations/{gamma['id']}").json()
    q["lines"][0]["amount"] = 960_000_000                                                  # corrected on purpose
    upd = editor.put(f"/api/spend/quotes/{code}/quotations/{gamma['id']}", json=q)
    assert upd.status_code == 200, upd.text
    assert [f["code"] for f in upd.json()["findings"]] == ["SUBTOTAL"]           # printed subtotal no longer matches the lines
    assert editor.put(f"/api/spend/quotes/{code}/quotations/{gamma['id']}", json=q).status_code == 409   # stale rowVersion
    # Only verified quotations can be selected, only by an approver.
    assert approver.post(f"/api/spend/quotes/{code}/select", json={"quotationId": alpha["id"]}).status_code == 409
    editor.post(f"/api/spend/quotes/{code}/quotations/{alpha['id']}/verify")
    assert editor.post(f"/api/spend/quotes/{code}/select", json={"quotationId": alpha["id"]}).status_code == 403
    r = approver.post(f"/api/spend/quotes/{code}/select", json={"quotationId": alpha["id"]})
    assert r.status_code == 409 and "1/3" in r.json()["detail"]
    for x in (beta, gamma):
        editor.post(f"/api/spend/quotes/{code}/quotations/{x['id']}/verify")
    # Short name is used in the conclusion.
    vid = alpha["vendorId"]
    p = admin.get(f"/api/spend/vendor-profiles/{vid}").json()
    assert admin.put(f"/api/spend/vendor-profiles/{vid}", json={**p, "shortName": "ALPHA"}).status_code == 200
    sel = approver.post(f"/api/spend/quotes/{code}/select", json={"quotationId": alpha["id"]}).json()
    assert sel["status"] == "selected" and sel["evaluation"]["conclusion"] == "Đề xuất chọn ALPHA"
    assert any("lệch 98.357.266" in w for w in sel["warnings"]), sel["warnings"]   # 913.109.394 vs dự toán 814.752.128
    assert editor.put(f"/api/spend/quotes/{code}", json={"title": "x"}).status_code == 409
    assert approver.post(f"/api/spend/quotes/{code}/reopen").json()["status"] == "comparing"
    lst = editor.get("/api/spend/quotes").json()
    assert lst[0]["code"] == code and lst[0]["quotations"] == 3


def test_comparison_ranks_vendors_and_manual_match(editor):
    rq = _new_request(editor)
    code = rq["code"]
    _upload(editor, code, "Alpha", _quote_file(price_core=450_000_000))
    _upload(editor, code, "Beta", _quote_file(price_core=495_000_000, extra=[[3, "Công cấu hình", None, "Gói", 1, 20_000_000, 20_000_000]]))
    c = editor.get(f"/api/spend/quotes/{code}/comparison").json()
    a, b = c["columns"]
    assert (a["rank"], b["rank"]) == (1, 2)
    assert a["subtotalVnd"] == 915_000_000 and b["subtotalVnd"] == 1_025_000_000 and b["diffPct"] == 12.0
    assert len(c["rows"]) == 3 and c["commonRows"] == 2
    assert a["commonTotalVnd"] == 915_000_000 and b["commonTotalVnd"] == 1_005_000_000
    core = c["rows"][0]
    assert core["minQuotation"] == str(a["quotationId"]) and core["spreadPct"] == 10.0
    # Manually put Beta's "Công cấu hình" into the patch-cord row: that row now has 3 cells → refused? no — one per quotation.
    beta_line = next(x for x in c["rows"] if len(x["cells"]) == 1)["cells"][str(b["quotationId"])]["id"]
    c2 = editor.post(f"/api/spend/quotes/{code}/match", json={"lineId": beta_line, "key": "cong-cau-hinh"}).json()
    assert any(r["key"] == "cong-cau-hinh" for r in c2["rows"])


def test_evaluation_save_and_export_form(editor):
    rq = _new_request(editor, workstream="cabling", title="Thi công cáp FL9")
    code = rq["code"]
    ids = [x["id"] for x in [_upload(editor, code, n, _quote_file()).json()["quotations"][-1] for n in ("TTC", "Thakral One", "VNT")]]
    crit = rq["evaluation"]["criteria"]
    assert [round(c["weight"], 2) for c in crit if c["section"] == "B"] == [0.1, 0.1, 0.1]
    scores = {"TTC": [9] * 9, "Thakral One": [9] * 6 + [10, 10, 9.5], "VNT": [10] * 4 + [9.5, 9.5] + [10] * 3}
    body = {"department": "ITD", "date": "2026-09-17", "service": "Thi công hạ tầng cáp", "evaluator": "=HYPERLINK(\"x\")",
            "approver": "Trưởng ITD",
            "criteria": [{"section": c["section"], "name": c["name"], "weight": c["weight"],
                          "scores": {str(qid): s[i] for qid, s in zip(ids, scores.values())}} for i, c in enumerate(crit)]}
    r = editor.put(f"/api/spend/quotes/{code}/evaluation", json=body)
    assert r.status_code == 200, r.text
    ev = r.json()["evaluation"]
    assert [ev["totals"][str(i)] for i in ids] == [9.0, 9.25, 9.9]
    assert [ev["classes"][str(i)] for i in ids] == ["A", "A", "A"]
    # Bad weights / scores are refused.
    bad = json.loads(json.dumps(body))
    bad["criteria"][0]["weight"] = 0.2
    assert "70%" in editor.put(f"/api/spend/quotes/{code}/evaluation", json=bad).json()["detail"]
    bad = json.loads(json.dumps(body))
    bad["criteria"][0]["scores"][str(ids[0])] = 11
    assert editor.put(f"/api/spend/quotes/{code}/evaluation", json=bad).status_code == 400
    # Excel: same layout, formulas for Total and class, typed text never a formula.
    x = editor.get(f"/api/spend/quotes/{code}/evaluation.xlsx")
    assert x.status_code == 200
    ws = load_workbook(io.BytesIO(x.content)).active
    assert ws["A1"].value == "PHIẾU ĐÁNH GIÁ NHÀ CUNG CẤP" and ws["A6"].value == "Bộ phận đánh giá:"
    assert [ws.cell(row=12, column=c).value for c in (8, 9, 10)] == ["TTC", "Thakral One", "VNT"]
    total = next(r for r in range(13, 40) if ws.cell(row=r, column=1).value == "Total")
    assert ws.cell(row=total, column=8).value.startswith("=ROUND(SUMPRODUCT(")
    assert ws.cell(row=total + 1, column=8).value.startswith("=IF(H")
    texts = [c for row in ws.iter_rows() for c in row if isinstance(c.value, str) and "HYPERLINK" in c.value]
    assert texts and all(c.data_type == "s" for c in texts)
    cx = editor.get(f"/api/spend/quotes/{code}/comparison.xlsx")
    wb = load_workbook(io.BytesIO(cx.content))
    assert wb.sheetnames[0] == "So sánh" and "Kiểm tra" in wb.sheetnames and len(wb.sheetnames) == 5


def test_vendor_profile_short_name_unique_and_aliases(admin, editor):
    rq = _new_request(editor)
    d = _upload(editor, rq["code"], "Thakral One Vietnam", _quote_file()).json()
    vid = d["quotations"][0]["vendorId"]
    p = editor.get(f"/api/spend/vendor-profiles/{vid}").json()
    r = editor.put(f"/api/spend/vendor-profiles/{vid}", json={**p, "shortName": "THRAKONE", "aliases": ["Thakral One"],
                                                             "tags": ["Cabling", "Network"]})
    assert r.status_code == 200 and r.json()["label"] == "THRAKONE" and r.json()["tags"] == ["Cabling", "Network"]
    other = _upload(editor, _new_request(editor)["code"], "Công ty khác", _quote_file()).json()["quotations"][0]["vendorId"]
    p2 = editor.get(f"/api/spend/vendor-profiles/{other}").json()
    assert editor.put(f"/api/spend/vendor-profiles/{other}", json={**p2, "shortName": "thrakone"}).status_code == 409
    assert editor.put(f"/api/spend/vendor-profiles/{other}", json={**p2, "status": "blocked"}).status_code == 400
    # Alias and short name resolve to the same vendor when uploading.
    rq3 = _new_request(editor)
    q1 = _upload(editor, rq3["code"], "thakral one", _quote_file()).json()["quotations"][0]
    assert q1["vendorId"] == vid and q1["vendorLabel"] == "THRAKONE"
    assert _upload(editor, rq3["code"], "THRAKONE", _quote_file()).status_code == 409
    prof = editor.get(f"/api/spend/vendor-profiles/{vid}").json()
    assert len(prof["quotes"]) == 2
    lst = {v["id"]: v for v in editor.get("/api/spend/vendor-profiles").json()}
    assert lst[vid]["quotes"] == 2 and lst[vid]["won"] == 0


def test_pdf_quotation_read_by_ai_mocked(admin, editor, monkeypatch):
    rq = _new_request(editor)
    code = rq["code"]
    d = _upload(editor, code, "Scan Co", b"%PDF-1.7\n% fake scan", name="scan.pdf").json()
    q = d["quotations"][0]
    assert q["state"] == "pending" and not d["aiEnabled"]
    calls = []
    monkeypatch.setattr(ai, "QUOTE_CALL", lambda cfg, key, pdf: calls.append((key, pdf)) or json.dumps({
        "quote_no": "BG-001", "quote_date": "2026-09-01", "valid_until": "2026-10-01", "currency": "VND",
        "vat_pct": "8", "subtotal": "1.000.000", "discount": "", "vat": "80.000", "total": "1.080.000",
        "delivery_terms": "4 tuần", "warranty_terms": "36 tháng", "payment_terms": "",
        "lines": [{"line_type": "item", "description": "Camera hội nghị", "model": "PTZ-1", "unit": "Bộ",
                   "qty": "2", "unit_price": "500.000", "amount": "1.000.000"},
                  {"line_type": "bogus", "description": "Loa", "model": "", "unit": "", "qty": "1", "unit_price": "", "amount": ""}]}))
    r = editor.post(f"/api/spend/quotes/{code}/quotations/{q['id']}/extract")
    assert r.status_code == 422 and calls == []                                        # AI off → nothing sent
    assert admin.put("/api/spend/proposals/ai", json={"provider": "anthropic", "apiKey": KEY, "allowExternal": True}).status_code == 200
    r = editor.post(f"/api/spend/quotes/{code}/quotations/{q['id']}/extract")
    assert r.status_code == 200, r.text
    out = r.json()
    assert calls and calls[0][0] == KEY and calls[0][1].startswith(b"%PDF")
    assert out["method"] == "ai" and out["quoteNo"] == "BG-001" and out["validUntil"] == "2026-10-01"
    assert out["statedTotal"] == 1_080_000 and out["warrantyTerms"] == "36 tháng" and KEY not in r.text
    assert [l["lineType"] for l in out["lines"]] == ["item", "item"]
    assert [f["code"] for f in out["findings"]] == ["MISSING"]
    monkeypatch.setattr(ai, "QUOTE_CALL", lambda *a: "not json")
    assert editor.post(f"/api/spend/quotes/{code}/quotations/{q['id']}/extract").status_code == 422


def test_roles(app_, editor):
    viewer, nobody = _login(app_, "viewer1"), _login(app_, "nobody1")
    rq = _new_request(editor)
    assert viewer.get(f"/api/spend/quotes/{rq['code']}").status_code == 200
    assert viewer.post("/api/spend/quotes", json={"title": "x"}).status_code == 403
    assert nobody.get("/api/spend/quotes").status_code == 403
    assert nobody.get("/api/spend/vendor-profiles").status_code == 403
    assert editor.delete(f"/api/spend/quotes/{rq['code']}").status_code == 403
