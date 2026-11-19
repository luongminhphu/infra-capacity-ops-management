"""ICO v2 G3a: owner review campaigns — items + signals, one e-mail per owner, dry run, idempotent outbox, escaping."""
import datetime as dt
import json

import pytest

from app.cmdb.models import Ci
from app.owners import review
from app.owners.models import ReviewCampaign, ReviewItem, ReviewNotice, System, SystemMember
from app.shared import mailer
from test_cmdb import db  # noqa: F401  (fixture)

NOW = dt.datetime(2027, 1, 5, 2, 0)            # 05/01/2027 09:00 Vietnam — January is a start month
SMTP = mailer.SmtpSettings(host="smtp.test", port=587, security="starttls", username="", password="", from_addr="ico@corp.example")


def _vm(db, name, vc="vc1"):  # noqa: F811
    c = Ci(type="vm", name=name, source_kind="vcenter", source_key=f"u-{name}", status="active", first_seen=NOW, last_seen=NOW,
           attrs=json.dumps({"vcenterKey": vc}))
    db.add(c)
    db.flush()
    return c.id


def _row(name, on=True, off=None, idle=None, vcpu=2, ram=8, disk=100):
    return {"vm": name, "vcenterKey": "vc1", "isPoweredOn": on, "powerOffDays": off, "lastActivityDays": idle, "vcpu": vcpu,
            "ramGB": ram, "diskProvGB": disk, "cpuUsagePctRaw": 5, "ramUsagePctRaw": 30}


@pytest.fixture()
def world(db, monkeypatch):  # noqa: F811
    """3 owners, 10 owned VMs (owner 1 across 2 systems), 2 VMs without owner."""
    from app import data_access
    systems = {}
    for code, name, owner in (("SYS-0001", "Core", "lead.a@corp.example"), ("SYS-0002", "Core API", "lead.a@corp.example"),
                              ("SYS-0003", "Web", "lead.b@corp.example"), ("SYS-0004", "DWH", "lead.c@corp.example"),
                              ("SYS-0005", "Không owner", None)):
        s = System(code=code, name=name, name_norm=name.lower(), owner_email=owner)
        db.add(s)
        db.flush()
        systems[code] = s.id
    plan = {"SYS-0001": ["a-1", "a-2", "<script>alert(1)</script>"], "SYS-0002": ["a-api"],
            "SYS-0003": ["b-1", "b-2", "b-off"], "SYS-0004": ["c-1", "c-idle", "c-snap"], "SYS-0005": ["orphan-in-system"]}
    rows = []
    for code, vms in plan.items():
        for n in vms:
            db.add(SystemMember(ci_id=_vm(db, n), system_id=systems[code], source="manual", assigned_at=NOW))
    _vm(db, "unassigned-1")
    db.commit()
    for n in [n for vms in plan.values() for n in vms] + ["unassigned-1"]:
        rows.append(_row(n, on=n != "b-off", off=40 if n == "b-off" else None, idle=35 if n == "c-idle" else 0, vcpu=4 if n == "a-1" else 2))
    monkeypatch.setattr(data_access, "get_latest_combined", lambda db, keys=None, include_import=True: ([], rows, []))
    monkeypatch.setattr(review, "_backup_states", lambda db: {("vc1", "b-2"): "unprotected", ("vc1", "a-2"): "failing"})
    monkeypatch.setattr(review, "_snapshot_days", lambda db, today: {("vc1", "c-snap"): 20.0, ("vc1", "b-1"): 2.0})
    monkeypatch.setattr(review, "_smtp", lambda db: SMTP)
    sent = []
    monkeypatch.setattr(mailer, "send", lambda cfg, to, subject, text, html: sent.append((to[0], subject)))
    return {"systems": systems, "sent": sent}


def _items(db, c):  # noqa: F811
    return {i.vm_name: i for i in db.query(ReviewItem).filter_by(campaign_id=c.id)}


def test_open_builds_items_signals_and_one_email_per_owner(db, world):  # noqa: F811
    c = review.open_campaign(db, now=NOW)
    assert (c.period_label, c.due_at, c.dry_run, c.item_count) == ("2027-H1", dt.date(2027, 1, 26), True, 12)
    items = _items(db, c)
    assert sum(1 for i in items.values() if i.owner_email) == 10
    assert {n for n, i in items.items() if not i.owner_email} == {"orphan-in-system", "unassigned-1"}   # infra team list
    flagged = {n: json.loads(i.reasons) for n, i in items.items() if i.flagged}
    assert flagged == {"b-off": ["Tắt 40 ngày"], "c-idle": ["Không hoạt động 35 ngày"], "c-snap": ["Snapshot 20 ngày"],
                       "b-2": ["Chưa có backup"], "a-2": ["Backup đang lỗi"]}
    sig = json.loads(items["a-1"].signals)
    assert (sig["vcpu"], sig["backupCoverage"], sig["lastReviewDecision"]) == (4, "protected", None)
    notices = db.query(ReviewNotice).filter_by(campaign_id=c.id).order_by(ReviewNotice.recipient).all()
    assert [(n.recipient, n.kind, n.status, n.vm_count) for n in notices] == [
        ("lead.a@corp.example", "open", "preview", 4), ("lead.b@corp.example", "open", "preview", 3),
        ("lead.c@corp.example", "open", "preview", 3)]
    assert notices[0].subject == "[ICO] Rà soát hệ thống 2027-H1 — 4 VM cần xác nhận trước 26/01/2027"
    # dry run: nothing goes out
    assert review.deliver(db, NOW)["sent"] == 0 and world["sent"] == []


def test_email_escapes_vcenter_strings_and_lists_flagged_first(db, world):  # noqa: F811
    c = review.open_campaign(db, now=NOW)
    n = db.query(ReviewNotice).filter_by(campaign_id=c.id, recipient="lead.a@corp.example").one()
    assert "<script>" not in n.body_html and "&lt;script&gt;alert(1)&lt;/script&gt;" in n.body_html
    assert n.body_html.index("a-2") < n.body_html.index("a-1")                      # "nên rà" first
    assert "Tổng tài nguyên:</b> 10 vCPU" in n.body_html                             # 4 + 2 + 2 + 2
    assert "token" not in n.body_html.lower() and "Xác nhận trên ICO" in n.body_html


def test_period_opens_once_and_mails_are_sent_once(db, world):  # noqa: F811
    first = review.tick(db, NOW)
    assert first["opened"] == "2027-H1"
    assert review.tick(db, NOW + dt.timedelta(hours=1))["opened"] is None           # same period: not reopened
    with pytest.raises(review.Exists):
        review.open_campaign(db, "2027-H1", now=NOW)
    assert db.query(ReviewCampaign).count() == 1
    c = db.query(ReviewCampaign).one()
    assert review.send_campaign(db, c, "admin")["sent"] == 3
    assert sorted(t for t, _ in world["sent"]) == ["lead.a@corp.example", "lead.b@corp.example", "lead.c@corp.example"]
    assert review.deliver(db, NOW)["sent"] == 0 and review.tick(db, NOW)["sent"] == 0 and len(world["sent"]) == 3
    # outside a start month nothing opens
    assert review.tick(db, dt.datetime(2027, 3, 1, 2))["opened"] is None


def test_smtp_failure_is_retried_next_round_without_duplicates(db, world, monkeypatch):  # noqa: F811
    c = review.open_campaign(db, dry_run=False, now=NOW)
    calls = {"n": 0}

    def flaky(cfg, to, subject, text, html):
        calls["n"] += 1
        if to[0] == "lead.b@corp.example" and calls["n"] <= 3:
            raise mailer.MailError("Gửi email thất bại: SMTPServerDisconnected.")
        world["sent"].append((to[0], subject))
    monkeypatch.setattr(mailer, "send", flaky)
    r1 = review.deliver(db, NOW)
    assert (r1["sent"], r1["failed"]) == (2, 1)
    b = db.query(ReviewNotice).filter_by(campaign_id=c.id, recipient="lead.b@corp.example").one()
    assert (b.status, b.attempts, b.error) == ("failed", 1, "Gửi email thất bại: SMTPServerDisconnected.")
    r2 = review.deliver(db, NOW + dt.timedelta(minutes=10))
    assert (r2["sent"], r2["failed"]) == (1, 0)
    assert sorted(t for t, _ in world["sent"]) == ["lead.a@corp.example", "lead.b@corp.example", "lead.c@corp.example"]
    db.refresh(b)
    assert (b.status, b.attempts, b.error) == ("sent", 2, None)


def test_claimed_rows_are_not_sent_twice_and_stale_claims_retry(db, world):  # noqa: F811
    c = review.open_campaign(db, dry_run=False, now=NOW)
    a = db.query(ReviewNotice).filter_by(campaign_id=c.id, recipient="lead.a@corp.example").one()
    a.status, a.claimed_at, a.attempts = "sending", NOW, 1                            # another sender is on it
    db.commit()
    assert review.deliver(db, NOW + dt.timedelta(minutes=5))["sent"] == 2              # b and c only
    assert "lead.a@corp.example" not in {t for t, _ in world["sent"]}
    assert review.deliver(db, NOW + dt.timedelta(minutes=45))["sent"] == 1              # stale claim → retried
    assert sorted(t for t, _ in world["sent"]).count("lead.a@corp.example") == 1


def test_no_smtp_waits_without_spending_attempts(db, world, monkeypatch):  # noqa: F811
    review.open_campaign(db, dry_run=False, now=NOW)
    monkeypatch.setattr(review, "_smtp", lambda db: None)
    r = review.deliver(db, NOW)
    assert (r["sent"], r["waiting"]) == (0, 3) and r["error"].startswith("Chưa cấu hình máy chủ SMTP")
    assert {(n.status, n.attempts) for n in db.query(ReviewNotice)} == {("pending", 0)}


def test_last_decision_carried_to_next_campaign(db, world):  # noqa: F811
    c1 = review.open_campaign(db, now=NOW)
    it = db.query(ReviewItem).filter_by(campaign_id=c1.id, vm_name="b-off").one()
    it.decision = "decommission"
    db.commit()
    c2 = review.open_campaign(db, now=dt.datetime(2027, 7, 2, 2))
    assert c2.period_label == "2027-H2" and c2.dry_run is False                       # only the first campaign is a dry run
    assert json.loads(_items(db, c2)["b-off"].signals)["lastReviewDecision"] == "decommission"


def test_review_api_permissions(app_, admin):  # noqa: F811
    from test_spend_opex import _login
    viewer = _login(app_, "viewer1")
    assert viewer.get("/api/reviews").status_code == 403
    assert viewer.post("/api/reviews", json={}).status_code == 403
    assert admin.get("/api/reviews").status_code == 200
    assert admin.post("/api/reviews", json={"dueDays": 500}).status_code == 422
    assert admin.get("/api/reviews/999999").status_code == 404


from test_spend_opex import admin, app_  # noqa: E402,F401  (fixtures)


def test_review_api_open_preview_send(app_, admin, monkeypatch):  # noqa: F811
    from app.cmdb.models import CiKey, CiLink
    from app.database import SessionLocal, ensure_schema
    ensure_schema()
    s = SessionLocal()
    for m in (ReviewNotice, ReviewItem, ReviewCampaign, SystemMember, System, CiLink, CiKey, Ci):
        s.query(m).delete()
    sys_ = System(code="SYS-7001", name="API sys", name_norm="api sys", owner_email="api.lead@corp.example")
    s.add(sys_)
    s.flush()
    s.add(SystemMember(ci_id=_vm(s, "api-vm-1"), system_id=sys_.id, source="manual", assigned_at=NOW))
    s.commit()
    s.close()
    monkeypatch.setattr(review, "_backup_states", lambda db: None)
    sent = []
    monkeypatch.setattr(review, "_smtp", lambda db: SMTP)
    monkeypatch.setattr(mailer, "send", lambda cfg, to, subject, text, html: sent.append(to[0]))
    try:
        r = admin.post("/api/reviews", json={"periodLabel": "2099-H1", "dueDays": 14})
        assert r.status_code == 200 and r.json()["dryRun"] is True                      # first campaign: dry run
        cid = r.json()["id"]
        assert admin.post("/api/reviews", json={"periodLabel": "2099-H1"}).status_code == 409
        d = admin.get(f"/api/reviews/{cid}").json()
        assert (d["counts"]["owned"], d["counts"]["owners"], d["counts"]["notices"]) == (1, 1, {"preview": 1})
        nid = d["notices"][0]["id"]
        p = admin.get(f"/api/reviews/{cid}/notices/{nid}").json()
        assert p["subject"].startswith("[ICO] Rà soát hệ thống 2099-H1 — 1 VM") and "api-vm-1" in p["bodyHtml"]
        assert admin.get(f"/api/reviews/{cid + 1}/notices/{nid}").status_code == 404
        assert sent == []
        assert admin.post(f"/api/reviews/{cid}/send").json()["sent"] == 1 and sent == ["api.lead@corp.example"]
        assert admin.post(f"/api/reviews/{cid}/send").json()["sent"] == 0               # never twice
    finally:
        s = SessionLocal()
        for m in (ReviewNotice, ReviewItem, ReviewCampaign, SystemMember, System, CiLink, CiKey, Ci):
            s.query(m).delete()
        s.commit()
        s.close()
