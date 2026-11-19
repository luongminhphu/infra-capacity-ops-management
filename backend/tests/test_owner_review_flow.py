"""ICO v2 G3b: decisions, reminders → escalation → closing on a fake clock, reclaimed resources, re-assign queue,
next-period verification, owner portal IDOR, PDF / Excel."""
import datetime as dt
import json
import re

import pytest

from app.cmdb.models import Ci
from app.owners import review
from app.owners.models import ReviewCampaign, ReviewItem, ReviewNotice, System, SystemMember
from app.shared import mailer
from test_cmdb import db  # noqa: F401  (fixture)

NOW = dt.datetime(2027, 1, 5, 2, 0)          # opened 05/01/2027 → due 26/01/2027 (21 days)
SMTP = mailer.SmtpSettings(host="smtp.test", port=587, security="starttls", username="", password="", from_addr="ico@corp.example")


def at(day, hour=2):
    return dt.datetime(2027, 1 if day <= 31 else 2, day if day <= 31 else day - 31, hour)


@pytest.fixture()
def flow(db, monkeypatch):  # noqa: F811
    from app import data_access
    from app.config import get_settings
    sa = System(code="SYS-0001", name="Core", name_norm="core", owner_email="lead.a@corp.example", manager_email="boss.a@corp.example")
    sb = System(code="SYS-0002", name="Web", name_norm="web", owner_email="lead.b@corp.example")           # no manager
    db.add_all([sa, sb])
    db.flush()
    rows, vms = [], {}
    for name, sys_, vcpu, ram, disk in (("a-1", sa, 4, 16, 100), ("a-2", sa, 8, 32, 200), ("a-3", sa, 2, 8, 50),
                                        ("b-1", sb, 4, 8, 40), ("b-2", sb, 2, 4, 20)):
        c = Ci(type="vm", name=name, source_kind="vcenter", source_key=f"u-{name}", status="active", first_seen=NOW, last_seen=NOW,
               attrs=json.dumps({"vcenterKey": "vc1"}))
        db.add(c)
        db.flush()
        vms[name] = c.id
        db.add(SystemMember(ci_id=c.id, system_id=sys_.id, source="manual", assigned_at=NOW))
        rows.append({"vm": name, "vcenterKey": "vc1", "isPoweredOn": True, "vcpu": vcpu, "ramGB": ram, "diskProvGB": disk,
                     "lastActivityDays": 0})
    db.commit()
    monkeypatch.setattr(data_access, "get_latest_combined", lambda db, keys=None, include_import=True: ([], rows, []))
    monkeypatch.setattr(review, "_backup_states", lambda db: None)
    monkeypatch.setattr(review, "_snapshot_days", lambda db, today: {})
    monkeypatch.setattr(review, "_smtp", lambda db: SMTP)
    s = get_settings()
    for k, v in {"review_infra_email": "infra@corp.example", "review_unit_cost_vcpu": 100_000, "review_unit_cost_ram_gb": 50_000,
                 "review_unit_cost_disk_gb": 1_000}.items():
        monkeypatch.setattr(s, k, v)
    sent = []
    monkeypatch.setattr(mailer, "send", lambda cfg, to, subject, text, html: sent.append((to[0], subject)))
    c = review.open_campaign(db, dry_run=False, now=NOW)
    return {"c": c, "sent": sent, "vms": vms, "sa": sa.id, "sb": sb.id}


def _items(db, c):  # noqa: F811
    return {i.vm_name: i for i in db.query(ReviewItem).filter_by(campaign_id=c.id)}


def _kinds(db, c):  # noqa: F811
    return sorted((n.kind, n.recipient) for n in db.query(ReviewNotice).filter_by(campaign_id=c.id))


def test_full_cycle_on_fake_clock(db, flow):  # noqa: F811
    c = flow["c"]
    review.tick(db, NOW)
    assert sorted(t for t, _ in flow["sent"]) == ["lead.a@corp.example", "lead.b@corp.example"]           # open
    it = _items(db, c)
    review.decide(db, c, [it["a-1"], it["a-2"]], {"decision": "keep"}, "lead.a@corp.example", now=at(8))
    # before due − 7: nothing new
    review.tick(db, at(18))
    assert len(flow["sent"]) == 2
    # due − 7 (19/01): reminder1 to owners with undecided VMs only
    review.tick(db, at(19))
    assert [k for k in _kinds(db, c) if k[0] == "reminder1"] == [("reminder1", "lead.a@corp.example"), ("reminder1", "lead.b@corp.example")]
    review.tick(db, at(20))                                                                                 # same stage: nothing new
    assert sum(1 for k in _kinds(db, c) if k[0] == "reminder1") == 2
    review.decide(db, c, [_items(db, c)["a-3"]], {"decision": "decommission", "actionDate": "2027-02-15"}, "lead.a@corp.example", now=at(21))
    # due − 2 (24/01): reminder2 only to B (A has decided everything)
    review.tick(db, at(24))
    assert [k for k in _kinds(db, c) if k[0] == "reminder2"] == [("reminder2", "lead.b@corp.example")]
    # due (26/01): escalation — B's system has no manager → team hạ tầng with the "thiếu quản lý" warning
    r = review.tick(db, at(26))
    assert r["missingManager"] == 2 and [k for k in _kinds(db, c) if k[0] == "escalation"] == [("escalation", "infra@corp.example")]
    esc_mail = db.query(ReviewNotice).filter_by(campaign_id=c.id, kind="escalation").one()
    assert "thiếu quản lý" in esc_mail.body_html and all(i.escalated_at for i in _items(db, c).values() if i.vm_name.startswith("b-"))
    # due + 7 (02/02): closing, once
    assert review.tick(db, at(33))["closed"] == 1 and db.get(ReviewCampaign, c.id).status == "closed"
    assert review.tick(db, at(34))["closed"] == 0
    assert ("closing", "infra@corp.example") in _kinds(db, c)
    # every notice went out exactly once
    assert len(flow["sent"]) == len(set(flow["sent"])) == db.query(ReviewNotice).filter_by(campaign_id=c.id, status="sent").count() == 7
    # owners can no longer change; team hạ tầng settles the undecided ones
    with pytest.raises(review.Invalid):
        review.decide(db, db.get(ReviewCampaign, c.id), [_items(db, c)["b-1"]], {"decision": "keep"}, "lead.b@corp.example")
    review.decide(db, db.get(ReviewCampaign, c.id), [_items(db, c)["b-1"]], {"decision": "keep"}, "infra", infra=True)
    s = review.summary(db, db.get(ReviewCampaign, c.id))
    assert (s["totals"]["decided"], s["totals"]["undecided"], s["totals"]["escalated"]) == (4, 1, 2)
    assert next(i for i in s["items"] if i["vm"] == "b-2")["decisionLabel"] == "Chờ team hạ tầng xác nhận"


def test_reclaimed_resources_add_up(db, flow):  # noqa: F811
    c = flow["c"]
    it = _items(db, c)
    review.decide(db, c, [it["a-1"], it["a-2"]], {"decision": "decommission", "actionDate": "2027-02-01"}, "lead.a@corp.example", now=NOW)
    review.decide(db, c, [it["a-3"]], {"decision": "decommission", "actionDate": "2027-02-01"}, "lead.a@corp.example", now=NOW)
    review.decide(db, c, [it["b-1"]], {"decision": "resize", "newVcpu": 2, "newRamGB": 4}, "lead.b@corp.example", now=NOW)
    review.decide(db, c, [it["b-2"]], {"decision": "transfer", "transferTo": "New.Owner@corp.example"}, "lead.b@corp.example", now=NOW)
    s = review.summary(db, c)
    # 3 VM thu hồi: 4 + 8 + 2 = 14 vCPU, 16 + 32 + 8 = 56 GB, 350 GB disk; resize b-1: −2 vCPU, −4 GB
    assert s["reclaim"]["vcpu"] == 16.0 and s["reclaim"]["ramGB"] == 60.0 and s["reclaim"]["diskGB"] == 350.0
    assert sum(i["reclaim"]["vcpu"] for i in s["items"] if i["decision"] == "decommission") == 14.0
    assert s["reclaim"]["vndMonthly"] == 16 * 100_000 + 60 * 50_000 + 350 * 1_000 and s["reclaim"]["vndYearly"] == s["reclaim"]["vndMonthly"] * 12
    assert {x["decision"]: x["count"] for x in s["byDecision"]} == {"keep": 0, "decommission": 3, "resize": 1, "transfer": 1, "not_mine": 0}
    assert (s["totals"]["responsePct"], s["totals"]["queue"]) == (100.0, 1)
    assert _items(db, c)["b-2"].transfer_to == "new.owner@corp.example"


@pytest.mark.parametrize("data, msg", [
    ({"decision": "bogus"}, "Quyết định không hợp lệ"),
    ({"decision": "decommission"}, "Thu hồi cần ngày dự kiến"),
    ({"decision": "decommission", "actionDate": "2026-12-01"}, "Ngày thu hồi dự kiến phải"),
    ({"decision": "resize"}, "Giảm cấu hình cần vCPU hoặc RAM"),
    ({"decision": "resize", "newVcpu": 8}, "vCPU đề xuất (8) phải nhỏ hơn hiện tại (4)"),
    ({"decision": "transfer", "transferTo": "x"}, "Chuyển owner cần email"),
    ({"decision": "transfer", "transferTo": "lead.a@corp.example"}, "Email người nhận trùng owner"),
    ({"decision": "keep", "note": "x" * 501}, "Ghi chú dài quá"),
])
def test_decision_validation(db, flow, data, msg):  # noqa: F811
    with pytest.raises(review.Invalid, match=re.escape(msg)):
        review.decide(db, flow["c"], [_items(db, flow["c"])["a-1"]], data, "lead.a@corp.example", now=NOW)


def test_queue_resolve_and_next_period_verification(db, flow):  # noqa: F811
    c = flow["c"]
    it = _items(db, c)
    review.decide(db, c, [it["a-1"]], {"decision": "decommission", "actionDate": "2027-01-20"}, "lead.a@corp.example", now=NOW)
    review.decide(db, c, [it["a-2"]], {"decision": "decommission", "actionDate": "2027-01-20"}, "lead.a@corp.example", now=NOW)
    review.decide(db, c, [it["b-2"]], {"decision": "not_mine"}, "lead.b@corp.example", now=NOW)
    assert [i.vm_name for i in review.not_cleaned(db, dt.date(2027, 1, 19))] == []                    # not due yet
    assert [i.vm_name for i in review.not_cleaned(db, dt.date(2027, 1, 21))] == ["a-1", "a-2"]
    db.get(Ci, flow["vms"]["a-1"]).status = "retired"                                                    # a-1 cleaned in vCenter
    db.commit()
    assert review.verify(db, at(21)) == 1 and review.verify(db, at(22)) == 0
    assert [i.vm_name for i in review.not_cleaned(db, dt.date(2027, 1, 21))] == ["a-2"]
    with pytest.raises(review.Invalid):
        review.resolve(db, _items(db, c)["a-1"], "admin")
    review.resolve(db, _items(db, c)["b-2"], "admin")
    assert review.summary(db, c)["totals"]["queue"] == 0 and review.summary(db, c)["totals"]["verified"] == 1


def test_inbox_review_items(db, flow):  # noqa: F811
    from app import inbox
    c = flow["c"]
    it = _items(db, c)
    review.decide(db, c, [it["a-1"]], {"decision": "decommission", "actionDate": "2027-01-20"}, "lead.a@corp.example", now=NOW)
    review.decide(db, c, [it["b-2"]], {"decision": "transfer", "transferTo": "x@corp.example"}, "lead.b@corp.example", now=NOW)
    review.tick(db, at(33))                                                                              # closed
    got = {i["title"] for i in inbox.review_items(db, dt.date(2027, 2, 2))}
    assert got == {"Kỳ rà soát 2027-H1: 3 VM chờ team hạ tầng xác nhận", "1 VM cần gán lại owner sau rà soát",
                   "1 VM đã quyết định thu hồi nhưng chưa dọn"}


def test_pdf_and_excel_report(db, flow):  # noqa: F811
    from openpyxl import load_workbook
    import io
    from app import excel_review, report_review
    c = flow["c"]
    it = _items(db, c)
    review.decide(db, c, [it["a-1"], it["a-2"], it["a-3"]], {"decision": "decommission", "actionDate": "2027-02-01"}, "lead.a@corp.example", now=NOW)
    s = review.summary(db, c)
    html = report_review.render_html(s, "Admin")
    assert "Kết quả rà soát owner — kỳ 2027-H1" in html and "thu hồi 14 vCPU · 56 GB RAM · 350 GB disk" in html and "60,0%" in html
    assert report_review.build_pdf(s, "Admin")[:4] == b"%PDF"
    wb = load_workbook(io.BytesIO(excel_review.build_xlsx(s)))
    assert wb.sheetnames == ["Tổng kết", "Theo owner", "Chi tiết VM"]
    vals = {r[0].value: r[1].value for r in wb["Tổng kết"].iter_rows(min_row=5) if r[0].value}
    assert (vals["vCPU thu hồi"], vals["Tỷ lệ phản hồi (%)"], vals["Quyết định: Thu hồi"]) == (14.0, 60.0, 3)


# ---------------------------------------------------------------- owner portal API (shared DB): IDOR + editing window
@pytest.fixture()
def portal(app_):  # noqa: F811
    from app.cmdb.models import CiKey, CiLink
    from app.database import SessionLocal, ensure_schema
    from app.models import User
    from app.security import hash_password
    from test_spend_opex import PW
    ensure_schema()
    s = SessionLocal()

    def wipe():
        for m in (ReviewNotice, ReviewItem, ReviewCampaign, SystemMember, System, CiLink, CiKey, Ci):
            s.query(m).delete()
        s.query(User).filter(User.username.in_(["rv.a@corp.example", "rv.b@corp.example"])).delete(synchronize_session=False)
        s.commit()
    wipe()
    for n in ("rv.a@corp.example", "rv.b@corp.example"):
        s.add(User(username=n, password_hash=hash_password(PW), email=n, access_scope="owner"))
    sa = System(code="SYS-8001", name="A", name_norm="a8", owner_email="rv.a@corp.example")
    sb = System(code="SYS-8002", name="B", name_norm="b8", owner_email="rv.b@corp.example")
    s.add_all([sa, sb])
    s.flush()
    c = ReviewCampaign(period_label="2098-H1", due_at=dt.date(2098, 1, 26), status="open", dry_run=True)
    s.add(c)
    s.flush()
    ids = {}
    for name, sys_ in (("rv-a1", sa), ("rv-a2", sa), ("rv-b1", sb)):
        it = ReviewItem(campaign_id=c.id, vm_name=name, vcenter_key="vc1", system_id=sys_.id, system_code=sys_.code,
                        owner_email=sys_.owner_email, signals=json.dumps({"vcpu": 4, "ramGB": 16}), reasons="[]")
        s.add(it)
        s.flush()
        ids[name] = it.id
    s.commit()
    out = {"c": c.id, **ids}
    s.close()
    yield out
    s = SessionLocal()
    wipe()
    s.close()


def test_owner_portal_review_idor_and_closed_campaign(app_, portal):  # noqa: F811
    from app.database import SessionLocal
    from test_spend_opex import _login
    a, b = _login(app_, "rv.a@corp.example"), _login(app_, "rv.b@corp.example")
    assert [x["periodLabel"] for x in a.get("/api/owner/reviews").json()["items"]] == ["2098-H1"]
    mine = a.get(f"/api/owner/reviews/{portal['c']}").json()
    assert sorted(i["vm"] for i in mine["items"]) == ["rv-a1", "rv-a2"] and mine["editable"] is True
    # B cannot decide A's VM, even mixed with its own; nothing is saved
    r = b.put(f"/api/owner/reviews/{portal['c']}/items", json={"itemIds": [portal["rv-b1"], portal["rv-a1"]], "decision": "keep"})
    assert r.status_code == 404
    s = SessionLocal()
    assert s.get(ReviewItem, portal["rv-b1"]).decision is None and s.get(ReviewItem, portal["rv-a1"]).decision is None
    s.close()
    assert b.get("/api/owner/reviews/999999").status_code == 404
    ok = a.put(f"/api/owner/reviews/{portal['c']}/items", json={"itemIds": [portal["rv-a1"], portal["rv-a2"]], "decision": "resize",
                                                               "newVcpu": 2})
    assert ok.json() == {"saved": 2}
    assert a.put(f"/api/owner/reviews/{portal['c']}/items", json={"itemIds": [portal["rv-a1"]], "decision": "resize",
                                                                 "newVcpu": 6}).status_code == 400
    s = SessionLocal()
    it = s.get(ReviewItem, portal["rv-a1"])
    assert (it.decision, it.new_vcpu, it.decided_by) == ("resize", 2, "rv.a@corp.example")
    s.get(ReviewCampaign, portal["c"]).status = "closed"
    s.commit()
    s.close()
    closed = a.put(f"/api/owner/reviews/{portal['c']}/items", json={"itemIds": [portal["rv-a1"]], "decision": "keep"})
    assert closed.status_code == 409 and "đã đóng" in closed.json()["detail"]
    assert a.get(f"/api/owner/reviews/{portal['c']}").json()["editable"] is False
    # owners cannot reach the admin side
    assert a.get(f"/api/reviews/{portal['c']}/summary").status_code == 403


def test_admin_review_endpoints(app_, admin, portal):  # noqa: F811
    s = admin.get(f"/api/reviews/{portal['c']}/summary").json()
    assert s["totals"]["owned"] == 3
    assert admin.put(f"/api/reviews/{portal['c']}/items", json={"itemIds": [portal["rv-b1"]], "decision": "not_mine"}).json() == {"saved": 1}
    q = admin.get("/api/reviews/queue/reassign").json()["items"]
    assert [i["vm"] for i in q] == ["rv-b1"]
    assert admin.post(f"/api/reviews/items/{portal['rv-b1']}/resolve").json() == {"ok": True}
    assert admin.get("/api/reviews/queue/reassign").json()["items"] == []
    pdf = admin.get(f"/api/reviews/{portal['c']}/report.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert admin.get(f"/api/reviews/{portal['c']}/report.xlsx").status_code == 200


from test_spend_opex import admin, app_  # noqa: E402,F401  (fixtures)
