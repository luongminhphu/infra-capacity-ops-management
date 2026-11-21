"""Review phase C (docs/design/ico-ai-mcp-review.md): AI masking covers e-mails and the newer sources, budget held in
the database (every pod), Hỏi ICO tools for the newer modules, ai_runs request id / prompt version / feedback."""
import datetime as dt
import json

import pytest

from app import ai_platform, ask_ico, inbox, observability as obs
from app.database import SessionLocal
from app.ipam.models import IpamHost
from app.models import AiRun
from app.spend.models import SpendAiConfig
from test_ai_platform import KEY, _clean, _enable  # noqa: F401  (autouse fixture)
from test_spend_opex import _login, admin, app_, editor  # noqa: F401  (fixtures)


def test_masker_hides_emails_and_restores_them():
    m = ai_platform.Masker(["ds-prd-01"])
    masked = m.mask({"owner": "Lead.CB@Corp.Example quản lý ds-prd-01", "list": ["bk@corp.example"]})
    text = json.dumps(masked)
    assert "@" not in text and "ds-prd-01" not in text and text.count("<email:") == 2 and "<host:" in text
    assert m.unmask(masked) == {"owner": "Lead.CB@Corp.Example quản lý ds-prd-01", "list": ["bk@corp.example"]}


def test_known_names_include_ipam_hostnames(app_):  # noqa: F811
    db = SessionLocal()
    db.query(IpamHost).filter(IpamHost.source_key == "zz-ipam").delete()
    db.add(IpamHost(source_key="zz-ipam", ip="10.9.9.9", hostname="zz-ipam-host01"))
    db.commit()
    try:
        ai_platform._names_cache.update(names=None)
        assert "zz-ipam-host01" in ai_platform._known_names(db)
        text = json.dumps(ai_platform.masker_for(db).mask("lỗi trên zz-ipam-host01"))
        assert "zz-ipam-host01" not in text
    finally:
        db.query(IpamHost).filter(IpamHost.source_key == "zz-ipam").delete()
        db.commit()
        db.close()


def _cfg(db):
    return db.get(SpendAiConfig, 1)


def test_budget_is_reserved_in_the_database(app_, admin):  # noqa: F811
    _enable(admin, budgetUsd=1, alertUsd=1)
    db = SessionLocal()
    ai_platform._reserve(db, "ask_ico", 600_000)                   # 0,60 USD held
    assert _cfg(db).reserved_micros == 600_000
    with pytest.raises(ai_platform.AiBlocked):                     # another pod: 0,60 + 0,60 > 1 USD
        ai_platform._reserve(db, "ask_ico", 600_000)
    ai_platform._release(600_000)
    db.expire_all()
    assert _cfg(db).reserved_micros == 0
    ai_platform._reserve(db, "ask_ico", 900_000)
    # a crash left it there: after RESERVE_STALE it no longer counts
    s = SessionLocal()
    s.query(SpendAiConfig).update({"reserved_at": dt.datetime.utcnow() - dt.timedelta(minutes=30)})
    s.commit()
    s.close()
    ai_platform._reserve(db, "ask_ico", 900_000)                   # replaces the stale hold instead of adding to it
    db.expire_all()
    assert _cfg(db).reserved_micros == 900_000
    ai_platform._release(5_000_000)                            # never below zero
    db.expire_all()
    assert _cfg(db).reserved_micros == 0
    db.close()


def test_run_records_request_id_prompt_version_and_releases(app_, admin):  # noqa: F811
    _enable(admin, budgetUsd=100)
    db = SessionLocal()
    user = db.query(ai_platform.User).filter_by(username="admin").one()
    with obs.job_context("unit") as job:
        res, cached = ai_platform.run(db, user, "ask_ico", lambda: {"answer": "ok"}, reserve=2.0)
    assert res == {"answer": "ok"} and cached is False
    run = db.get(AiRun, ai_platform.last_run_id())
    assert run.request_id == job.id and run.prompt_version == ai_platform.PROMPT_VERSIONS["ask_ico"]
    db.expire_all()
    assert (_cfg(db).reserved_micros or 0) == 0                     # released after the call
    db.close()


def test_ask_tools_for_newer_modules(app_, admin):  # noqa: F811
    assert set(ask_ico.SOURCES) == set(inbox.SOURCE_LABELS)        # viec_can_lam enum follows the hộp việc sources
    enum = next(t for t in ask_ico.TOOLS if t["name"] == "viec_can_lam")["input_schema"]["properties"]["source"]["enum"]
    assert set(enum) == {"all", *inbox.SOURCE_LABELS}
    db = SessionLocal()
    user = db.query(ai_platform.User).filter_by(username="admin").one()
    box = ask_ico.Toolbox(db, user)
    st = box.run("trang_thai_nguon", {})
    assert "tong_quat" in st and isinstance(st["nguon"], list)
    hw = box.run("phan_cung", {"level": "crit"})
    assert "loi" in hw or "may_chu" in hw
    systems = box.run("he_thong", {"q": "không-có-hệ-thống-này"})
    assert systems["tong"] == 0
    assert "loi" in box.run("he_thong", {"vm": "zz-no-such-vm"})
    db.add(IpamHost(source_key="zz-ipam2", ip="10.9.9.10", hostname="zz-ip-host", network="10.9.9.0/24"))
    db.commit()
    try:
        ip = box.run("tra_ip", {"ip": "10.9.9.10"})
        assert ip["ipam"][0]["hostname"] == "zz-ip-host" and ip["ipam"][0]["mang"] == "10.9.9.0/24"
        with pytest.raises(ValueError):
            box.run("tra_ip", {"ip": "not-an-ip"})
    finally:
        db.query(IpamHost).filter(IpamHost.source_key == "zz-ipam2").delete()
        db.commit()
        db.close()


def test_feedback_only_on_own_successful_answer(app_, admin, editor):  # noqa: F811
    db = SessionLocal()
    mine = AiRun(task="ask_ico", username="admin", status="ok")
    blocked = AiRun(task="ask_ico", username="admin", status="blocked")
    db.add_all([mine, blocked])
    db.commit()
    ids = mine.id, blocked.id
    db.close()
    assert admin.post(f"/api/ai/ask/{ids[0]}/feedback", json={"value": "up"}).json() == {"ok": True}
    assert admin.post(f"/api/ai/ask/{ids[0]}/feedback", json={"value": "great"}).status_code == 400
    assert editor.post(f"/api/ai/ask/{ids[0]}/feedback", json={"value": "down"}).status_code == 404     # not theirs
    assert admin.post(f"/api/ai/ask/{ids[1]}/feedback", json={"value": "up"}).status_code == 404
    db = SessionLocal()
    assert db.get(AiRun, ids[0]).feedback == "up"
    db.close()
    assert admin.post(f"/api/ai/ask/{ids[0]}/feedback", json={"value": None}).status_code == 200
    db = SessionLocal()
    assert db.get(AiRun, ids[0]).feedback is None
    db.close()


def test_reservation_keeps_the_start_of_a_busy_stretch(app_, admin):  # noqa: F811
    """Audit: every new call moved reserved_at forward, so a leaked hold (pod killed mid-call) never expired."""
    from app.spend.models import SpendAiConfig
    _enable(admin, budgetUsd=10, alertUsd=10)
    db = SessionLocal()
    db.query(SpendAiConfig).update({"reserved_micros": 0, "reserved_at": None})
    db.commit()
    ai_platform._reserve(db, "ask_ico", 100_000)
    first = db.query(SpendAiConfig.reserved_at).scalar()
    ai_platform._reserve(db, "ask_ico", 100_000)
    db.expire_all()
    assert db.query(SpendAiConfig.reserved_at).scalar() == first
    ai_platform._release(200_000)
    db.close()


def test_masker_phrases_ipv6_and_people():
    m = ai_platform.Masker(["SQL Server 01", "web(prod)", "esx-a-01"], key=b"k")
    out = m.mask({"text": "VM SQL Server 01, web(prod) on esx-a-01 at 2001:db8::5, 10:30:00", "ownerName": "Nguyễn Văn A"})
    assert "SQL Server 01" not in out["text"] and "web(prod)" not in out["text"] and "2001:db8::5" not in out["text"]
    assert "10:30:00" in out["text"] and out["ownerName"].startswith("<person:")
    assert m.unmask(out["ownerName"]) == "Nguyễn Văn A" and "SQL Server 01" in m.unmask(out["text"])
