"""Super app SA4: shared AI platform — pricing, masking, policy (switches, 100 USD budget, rate),
cache, accounting (ai_runs), 50 USD alert, admin API, bản tin trang chủ, tổng kết PDF."""
import datetime as dt
import json
from types import SimpleNamespace as NS

import pytest

from app import ai_platform, report_pdf
from app.database import SessionLocal
from app.models import AiCache, AiRun
from app.spend import ai as transport
from app.spend.models import SpendAiConfig

from test_spend_opex import _login, admin, app_, editor  # noqa: F401  (fixtures)

KEY = "sk-ant-test-platform-0000"
HOSTS = [{"host": "esx01.corp.local", "cluster": "PRD", "cores": 32, "ramGB": 512, "vcenterKey": "vc1"}]
VMS = [{"vm": "sql-prod-01", "cluster": "PRD", "host": "esx01.corp.local", "vcpu": 16, "ramGB": 470, "isPoweredOn": True,
        "ipAddress": "10.1.2.3", "vcenterKey": "vc1"}]


@pytest.fixture(autouse=True)
def _clean(app_, monkeypatch):
    db = SessionLocal()
    db.query(AiRun).delete()
    db.query(AiCache).delete()
    db.query(SpendAiConfig).delete()
    db.commit()
    db.close()
    from app import data_access, inbox
    inbox.invalidate()
    ai_platform._names_cache.update(names=None)
    monkeypatch.setattr(data_access, "get_latest_combined", lambda db, keys=None, include_import=True: (HOSTS, VMS, []))
    yield
    inbox.invalidate()


def _enable(admin, **kw):
    r = admin.put("/api/admin/ai", json={"provider": "anthropic", "apiKey": KEY, "allowExternal": True, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _fake(answer, seen, model="claude-opus-5-5", tin=10_000, tout=2_000):
    def call(cfg, key, system, text, schema, max_tokens):
        seen.append({"key": key, "system": system, "text": text})
        ai_platform.note_usage(NS(model=model, usage=NS(input_tokens=tin, output_tokens=tout)))
        return json.dumps(answer, ensure_ascii=False)
    return call


def test_price_and_masking():
    assert ai_platform.price("claude-opus-5-5", 1_000_000, 1_000_000) == 24.0
    assert ai_platform.price("claude-haiku-5-5", 1_000_000, 0) == 0.10
    assert ai_platform.price("unknown-model", 1_000_000, 0) == 10.0                 # priced like the most expensive
    m = ai_platform.Masker(["esx01.corp.local", "esx01", "sql-prod-01"])
    masked = m.mask({"a": "ESX01.corp.local và sql-prod-01 tại 10.1.2.3", "b": ["esx01 chậm"]})
    text = json.dumps(masked)
    assert "esx01" not in text.lower() and "sql-prod-01" not in text and "10.1.2.3" not in text and "<host:" in text and "<ip:" in text
    assert m.unmask(masked)["a"] == "ESX01.corp.local và sql-prod-01 tại 10.1.2.3"
    toks = set(__import__("re").findall(r"<host:[0-9a-f]+>", text))
    assert len(toks) == 3 and all(len(t) >= len("<host:") + 12 + 1 for t in toks)       # 3 distinct names, ≥ 12 hex
    assert "sql-prod-01x" == m.unmask(m.mask("sql-prod-01x"))                            # only whole words are masked


def test_policy_blocks_and_records(app_, admin, editor, monkeypatch):
    seen = []
    monkeypatch.setattr(transport, "JSON_CALL", _fake({"headline": "x", "points": [], "decisions": []}, seen))
    assert editor.post("/api/ai/home-brief").status_code == 409 and seen == []    # AI off
    _enable(admin, tasks={"home_brief": False})
    r = editor.post("/api/ai/home-brief")
    assert r.status_code == 409 and "đang tắt" in r.json()["detail"] and seen == []
    db = SessionLocal()
    assert [x.status for x in db.query(AiRun).order_by(AiRun.id)] == ["blocked"]          # ≤ 1 blocked row / user / minute
    db.close()


def test_home_brief_masked_cached_and_accounted(app_, admin, editor, monkeypatch):
    seen = []
    answer = {"headline": "Cluster PRD RAM 92% — cần xử lý <host:" + "x" * 6 + ">",
              "points": [{"level": "bad", "text": "VM <ip:abcdef> và host esx01 cần xem"}, {"level": "weird", "text": "y"}],
              "decisions": ["Bổ sung RAM trong quý này"]}
    monkeypatch.setattr(transport, "JSON_CALL", _fake(answer, seen))
    _enable(admin)
    r = editor.post("/api/ai/home-brief")
    assert r.status_code == 200, r.text
    d = r.json()
    assert seen[0]["key"] == KEY and "esx01.corp.local" not in seen[0]["text"] and "<host:" in seen[0]["text"]
    assert "DỮ LIỆU" in seen[0]["system"] and d["points"][1]["level"] == "info" and not d["cached"]
    d2 = editor.post("/api/ai/home-brief").json()
    assert d2["cached"] and len(seen) == 1                                            # same facts → cache, no new call
    u = admin.get("/api/admin/ai").json()["usage"]
    assert u["byTask"]["home_brief"]["calls"] == 1 and u["byTask"]["home_brief"]["cached"] == 1
    assert u["costUsd"] == pytest.approx(0.08)                                       # 10k × 4 + 2k × 20 per million
    assert KEY not in json.dumps(admin.get("/api/admin/ai").json())


def test_budget_rate_and_alert(app_, admin, editor, monkeypatch):
    seen = []
    monkeypatch.setattr(transport, "JSON_CALL", _fake({"headline": "x", "points": [], "decisions": []}, seen))
    _enable(admin, budgetUsd=100, alertUsd=50, ratePerHour=2)
    db = SessionLocal()
    db.add(AiRun(task="quote_extract", status="ok", cost_usd=49.99, created_at=dt.datetime.utcnow()))
    db.commit()
    posts = []
    assert ai_platform.alert_if_needed(db, post=lambda u, p: posts.append(p)) is False         # 49,99 < 50
    db.add(AiRun(task="quote_extract", status="ok", cost_usd=0.02, created_at=dt.datetime.utcnow()))
    db.commit()
    assert ai_platform.alert_if_needed(db, post=lambda u, p: posts.append(p)) is True
    assert ai_platform.alert_if_needed(db, post=lambda u, p: posts.append(p)) is False         # once per month
    from app import inbox
    inbox.invalidate()
    titles = [i["title"] for i in admin.get("/api/inbox?fresh=true").json()["items"] if i["source"] == "system"]
    assert titles and "50,01 USD" in titles[0]
    assert not any(i["source"] == "system" for i in editor.get("/api/inbox").json()["items"])      # admins only
    # Rate: 2 calls per user per hour.
    assert editor.post("/api/ai/home-brief").status_code == 200
    db.query(AiCache).delete()
    db.commit()
    editor.post("/api/ai/home-brief")
    db.query(AiCache).delete()
    db.commit()
    assert "giới hạn 2" in editor.post("/api/ai/home-brief").json()["detail"]
    # Budget: 100 USD reached → everything stops.
    db.add(AiRun(task="quote_extract", status="ok", cost_usd=60.0, created_at=dt.datetime.utcnow()))
    db.commit()
    r = admin.post("/api/ai/home-brief")
    assert r.status_code == 409 and "hết hạn mức" in r.json()["detail"]
    db.close()


def test_admin_api_rules(app_, admin, editor):
    assert editor.get("/api/admin/ai").status_code == 403
    assert editor.put("/api/admin/ai", json={"provider": "anthropic"}).status_code == 403
    assert admin.put("/api/admin/ai", json={"budgetUsd": 40, "alertUsd": 50}).status_code == 400
    assert admin.put("/api/admin/ai", json={"budgetUsd": "x"}).status_code == 400
    d = _enable(admin, maskNames=False, tasks={"report_summary": False, "nope": True})
    assert d["hasApiKey"] and not d["maskNames"] and {t["key"]: t["on"] for t in d["tasks"]}["report_summary"] is False
    s = editor.get("/api/ai/status").json()
    assert s["enabled"] and s["tasks"]["home_brief"] and not s["tasks"]["report_summary"]


def test_pdf_summary_box(app_, admin, monkeypatch):
    seen = []
    monkeypatch.setattr(transport, "JSON_CALL", _fake({"headline": "Ổn định <script>", "points": [{"level": "ok", "text": "CPU 26%"}],
                                                       "decisions": []}, seen))
    r = admin.get("/api/reports/pdf?view=executive&ai=1")
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and seen == []           # AI off: PDF still built
    _enable(admin)
    r = admin.get("/api/reports/pdf?view=executive&ai=1")
    assert r.status_code == 200 and len(seen) == 1 and "esx01.corp.local" not in seen[0]["text"]
    from app import ai_tasks
    html = ai_tasks.box_html({"headline": "Ổn định <script>", "points": [{"level": "ok", "text": "a"}], "decisions": ["b"]})
    assert "&lt;script&gt;" in html and "<script>" not in html
    tok = report_pdf.AI_BOX.set(html)
    try:
        out = report_pdf._inject_ai_box('<html><body><div class="cover"></div><div class="section">S</div></body></html>')
    finally:
        report_pdf.AI_BOX.reset(tok)
    assert out.index("AI soạn") < out.index('<div class="section">')


def test_budget_and_backup_pdf_accept_ai_flag(app_, admin):
    assert admin.post("/api/spend/reports/budget.pdf", json={"ai": True}).status_code == 200
    r = admin.get("/api/backup/report/pdf?ai=1")
    assert r.status_code in (200, 404)


def test_usage_counted_when_refused(app_, admin, monkeypatch):
    """A refused / truncated answer is still billed: the platform records its tokens."""
    _enable(admin)

    class Resp:
        model, stop_reason, content = "claude-opus-5-5", "refusal", []
        usage = NS(input_tokens=1000, output_tokens=500)

    class Client:
        def __init__(self, **kw):
            self.beta = NS(messages=NS(create=lambda **kw: Resp()))
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", Client)
    monkeypatch.setattr(transport, "JSON_CALL", transport._call_anthropic_json)
    r = admin.post("/api/ai/home-brief")
    assert r.status_code == 422
    db = SessionLocal()
    run = db.query(AiRun).filter_by(task="home_brief", status="error").one()
    assert run.output_tokens == 500 and run.cost_usd == pytest.approx(0.014)
    db.close()
