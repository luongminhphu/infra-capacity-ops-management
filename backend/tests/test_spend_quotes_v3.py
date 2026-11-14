"""V3: AI summary of differences (mocked), unit price history, tờ trình Mẫu B from a comparison,
quick-create Capex / contract from the selected quotation."""
import io
import json

import docx

from app.routers import spend_proposals
from app.spend import ai, proposal_doc

from test_spend_opex import _cat_id, _login, admin, app_, editor  # noqa: F401  (fixtures)
from test_spend_proposals import template
from test_spend_quotes import _new_request, _quote_file, _upload, _xlsx

KEY = "sk-ant-test-v3-0000"


def _selected_request(app_, editor, title="Thay switch FL9", price=450_000_000, vendors=("Alpha", "Beta")):
    approver = _login(app_, "approver1")
    rq = _new_request(editor, title=title)
    code = rq["code"]
    ids = []
    for i, n in enumerate(vendors):
        q = _upload(editor, code, n, _quote_file(price_core=price + i * 20_000_000)).json()["quotations"][-1]
        editor.post(f"/api/spend/quotes/{code}/quotations/{q['id']}/verify")
        ids.append(q["id"])
    approver.post(f"/api/spend/quotes/{code}/select", json={"quotationId": ids[0], "reason": "Giá thấp nhất"})
    return code, ids


def test_ai_summary_sends_short_names_only_and_is_stored(app_, admin, editor, monkeypatch):
    code, ids = _selected_request(app_, editor)
    assert editor.post(f"/api/spend/quotes/{code}/ai-summary").status_code == 409            # AI off: nothing sent
    sent = []

    def fake(cfg, key, payload):
        sent.append((key, payload))
        return json.dumps({"summary": "Beta thiếu dây nhảy.", "differences": [
            {"topic": "Phạm vi", "detail": "Alpha có dây nhảy LC-LC 100 sợi.", "vendors": ["Alpha"]},
            {"topic": "", "detail": "", "vendors": []}], "risks": ["Thiếu vật tư"], "questions": ["Beta xác nhận số lượng SFP?"],
            "extra": "ignored"})
    monkeypatch.setattr(ai, "DIFF_CALL", fake)
    admin.put("/api/spend/proposals/ai", json={"provider": "anthropic", "apiKey": KEY, "allowExternal": True})
    p = admin.get(f"/api/spend/vendor-profiles/{editor.get(f'/api/spend/quotes/{code}').json()['quotations'][0]['vendorId']}").json()
    admin.put(f"/api/spend/vendor-profiles/{p['id']}", json={**p, "shortName": "ALP", "contactEmail": "sales@alpha.example"})
    r = editor.post(f"/api/spend/quotes/{code}/ai-summary")
    assert r.status_code == 200, r.text
    key, payload = sent[0]
    text = json.dumps(payload, ensure_ascii=False)
    assert key == KEY and [v["ncc"] for v in payload["bao_gia"]] == ["ALP", "Beta"]
    assert "sales@alpha.example" not in text and "editor1" not in text
    assert payload["bao_gia"][0]["dong"][0]["don_gia_vnd"] == 450_000_000
    s = r.json()["aiSummary"]
    assert s["summary"] == "Beta thiếu dây nhảy." and len(s["differences"]) == 1 and s["by"] == "editor1" and "extra" not in s
    assert _login(app_, "viewer1").post(f"/api/spend/quotes/{code}/ai-summary").status_code == 403


def test_price_history_by_model_and_year(app_, editor):
    _selected_request(app_, editor, price=450_000_000)
    _selected_request(app_, editor, title="Switch FL10", price=470_000_000, vendors=("Gamma",))
    r = editor.get("/api/spend/price-history", params={"q": "c9500"}).json()
    assert len(r["items"]) == 3 and {i["vendor"] for i in r["items"]} == {"Alpha", "Beta", "Gamma"}
    assert [i["selected"] for i in r["items"] if i["vendor"] == "Alpha"] == [True]
    m = r["byModel"][0]
    assert m["model"] == "C9500-48Y4C" and m["years"][0]["count"] == 3
    assert (m["years"][0]["min"], m["years"][0]["median"], m["years"][0]["max"]) == (450_000_000, 470_000_000, 470_000_000)
    assert editor.get("/api/spend/price-history", params={"q": "ab"}).status_code == 400
    # Unverified lines are not price history.
    rq = _new_request(editor)
    _upload(editor, rq["code"], "Delta", _quote_file(price_core=1))
    assert len(editor.get("/api/spend/price-history", params={"q": "c9500"}).json()["items"]) == 3


def test_capex_and_contract_from_selected_quote(app_, editor):
    code, ids = _selected_request(app_, editor)
    r = editor.post(f"/api/spend/quotes/{code}/capex", json={"categoryId": _cat_id(), "mode": "total", "coverageYears": 3,
                                                           "startDate": "2026-12-01"})
    assert r.status_code == 200, r.text
    cpx = editor.get(f"/api/spend/capex/{r.json()['capexCode']}").json()
    assert cpx["name"] == "Thay switch FL9" and cpx["fiscalYear"] == int(code.split("-")[1])
    assert cpx["amountNet"] == 915_000_000 and cpx["items"][0]["vendorName"] == "Alpha"
    assert editor.post(f"/api/spend/quotes/{code}/capex", json={"categoryId": _cat_id()}).status_code == 409
    c = editor.post(f"/api/spend/quotes/{code}/contract")
    assert c.status_code == 200 and c.json()["contracts"][0]["code"] == c.json()["contractCode"]
    ct = editor.get(f"/api/spend/contracts/{c.json()['contractCode']}").json()
    assert ct["linkKind"] == "capex" and ct["linkCode"] == cpx["code"] and ct["vendorName"] == "Alpha"
    assert editor.post(f"/api/spend/quotes/{code}/contract").status_code == 409
    # One item per line.
    code2, _ = _selected_request(app_, editor, title="Gói 2")
    r2 = editor.post(f"/api/spend/quotes/{code2}/capex", json={"categoryId": _cat_id(), "mode": "lines"}).json()
    items = editor.get(f"/api/spend/capex/{r2['capexCode']}").json()["items"]
    assert [(i["quantity"], i["amountNet"]) for i in items] == [(2, 900_000_000), (100, 15_000_000)]
    # Not selected yet → refused.
    rq = _new_request(editor)
    assert editor.post(f"/api/spend/quotes/{rq['code']}/capex", json={"categoryId": _cat_id()}).status_code == 409


def test_proposal_mau_b_from_quote_comparison(app_, admin, editor):
    code, ids = _selected_request(app_, editor)
    t = admin.post("/api/spend/proposals/templates", files={"file": ("mau-b.docx", template())},
                   data={"kind": "B", "name": "Mẫu B"}).json()
    r = editor.post("/api/spend/proposals", json={"kind": "B", "templateId": t["id"], "subject": "Mua switch FL9", "quoteCode": code})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["quote"]["code"] == code and p["capex"] is None and p["opex"] == []
    out = editor.post(f"/api/spend/proposals/{p['code']}/generate", json={"useAi": False}).json()
    assert out["file"]
    f = editor.get(f"/api/spend/files/{out['file']['id']}")
    text = "\n".join(proposal_doc.text_of(f.content))
    assert "Alpha | 915.000.000 đ" in text and "thấp nhất" in text and "Chọn" in text and "Beta" in text
    # The AI payload carries the comparison and the evaluation by short name.
    from app.database import SessionLocal
    from app.spend.models import Proposal
    db = SessionLocal()
    payload = spend_proposals._payload(db, db.query(Proposal).filter_by(code=p["code"]).one())
    db.close()
    q = payload["so_sanh_bao_gia"]
    assert q["de_xuat"] == "Alpha" and q["ly_do_lua_chon"] == "Giá thấp nhất" and [v["ten_viet_tat"] for v in q["nha_cung_cap"]] == ["Alpha", "Beta"]
    assert q["nha_cung_cap"][1]["chenh_so_voi_thap_nhat_pct"] == 4.4 and len(q["tieu_chi_danh_gia"]) == 9
    assert editor.post("/api/spend/proposals", json={"kind": "B", "templateId": t["id"], "subject": "x", "quoteCode": "BG-1999-0001"}).status_code == 400


def test_proposal_payload_includes_renovation_history(app_, admin, editor):
    from test_spend_renovation import _import
    _import(admin, {"FL12": 2024, "FL3": 2022})
    rn = editor.post("/api/spend/renovations", json={"name": "FL9", "floor": "FL9", "year": 2026, "nodes": 186, "configTags": "10G, Wifi7"}).json()
    rq = _new_request(editor, workstream="network", renovationCode=rn["code"])
    from app.database import SessionLocal
    from app.spend.models import QuoteRequest
    db = SessionLocal()
    h = spend_proposals.quote_payload(db, db.query(QuoteRequest).filter_by(code=rq["code"]).one())["so_sanh_lich_su_cai_tao"]
    db.close()
    assert h["du_an"] == "FL9 (2026)" and h["trung_vi_lich_su"] == 10_969_894
    assert [x["gia_tri_chi_so"] for x in h["lich_su"]] == [11_801_788, 10_138_000]


def test_v3_hardening(app_, admin, editor, monkeypatch):
    code, ids = _selected_request(app_, editor)
    assert editor.get("/api/spend/price-history", params={"q": "%%%"}).status_code == 400
    assert editor.get("/api/spend/price-history", params={"q": "a%_\\"}).status_code == 400
    admin.put("/api/spend/proposals/ai", json={"provider": "anthropic", "apiKey": KEY, "allowExternal": True})
    monkeypatch.setattr(ai, "DIFF_CALL", lambda *a: json.dumps({"summary": "x", "differences": {"a": 1}, "risks": [], "questions": []}))
    assert editor.post(f"/api/spend/quotes/{code}/ai-summary").status_code == 422
    sent = []
    monkeypatch.setattr(ai, "DIFF_CALL", lambda cfg, key, payload: sent.append(payload) or json.dumps(
        {"summary": "ok", "differences": [], "risks": [], "questions": []}))
    from app.database import SessionLocal
    from app.spend.models import Quotation
    db = SessionLocal()
    db.get(Quotation, ids[0]).notes = "Nội bộ: NCC hứa giảm thêm 5%"
    db.commit()
    db.close()
    assert editor.post(f"/api/spend/quotes/{code}/ai-summary").status_code == 200
    assert "Nội bộ" not in json.dumps(sent[0], ensure_ascii=False)
