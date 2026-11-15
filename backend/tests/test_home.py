"""Super app SA2: hộp việc (inbox), lịch việc, tuỳ chọn người dùng, cảnh báo Discord ở mức 90 %,
danh sách đợt thanh toán, kết quả quy hoạch trên màn hình."""
import datetime as dt
import json

import pytest

from app import inbox, ops_alerts, secrets_box
from app.database import SessionLocal
from app.models import AuditLog, InboxAck, InboxAssignment, OpsAlertState, UserPref
from app.spend.fiscal import today_vn
from app.spend.models import SpendNotifyChannel

from test_spend_opex import _login, _opex, admin, app_, editor  # noqa: F401  (fixtures)

HOSTS = [{"host": f"esx{i}", "cluster": "PRD", "cores": 32, "ramGB": 512} for i in range(2)] + \
        [{"host": "esx9", "cluster": "DEV", "cores": 32, "ramGB": 512}]
# PRD: RAM 940 / 1024 GB = 91.8 % (≥ 90 %: push), CPU 200 / (64 × 2 × 6) = 26 %. DEV: low.
VMS = [{"name": f"vm{i}", "cluster": "PRD", "host": "esx0", "vcpu": 20, "ramGB": 94, "isPoweredOn": True} for i in range(10)] + \
      [{"name": "dev1", "cluster": "DEV", "host": "esx9", "vcpu": 4, "ramGB": 16, "isPoweredOn": True}]


@pytest.fixture(autouse=True)
def _clean(app_, monkeypatch):
    db = SessionLocal()
    for m in (InboxAck, InboxAssignment, UserPref, OpsAlertState):
        db.query(m).delete()
    db.commit()
    db.close()
    inbox.invalidate()
    from app import data_access
    monkeypatch.setattr(data_access, "get_latest_combined", lambda db, keys=None, include_import=True: (HOSTS, VMS, []))
    yield
    inbox.invalidate()


def test_capacity_items_and_push_level():
    db = SessionLocal()
    items = inbox.capacity_items(db)
    db.close()
    assert [i["title"] for i in items] == ["Cluster PRD: CPU 26% · RAM 92%"]
    it = items[0]
    assert it["level"] == "warn" and it["push"] and it["page"] == "dashboard"


def test_inbox_roles_acks_and_snooze(app_, admin, editor):
    viewer, nobody = _login(app_, "viewer1"), _login(app_, "nobody1")
    end = today_vn() + dt.timedelta(days=10)
    o = editor.post("/api/spend/opex", json=_opex(endDate=end.isoformat(), startDate=(end - dt.timedelta(days=365)).isoformat())).json()
    editor.post(f"/api/spend/opex/{o['code']}/activate") if o["status"] != "active" else None
    inbox.invalidate()
    r = viewer.get("/api/inbox").json()
    spend = [i for i in r["items"] if i["source"] == "spend"]
    assert spend and spend[0]["title"].startswith(o["code"]) and spend[0]["level"] in ("bad", "crit", "warn")
    assert spend[0]["page"] == "spend-opex" and spend[0]["query"] == f"code={o['code']}"
    assert any(i["source"] == "capacity" for i in r["items"]) and r["sources"]["spend"]["count"] >= 1
    assert r["unavailable"] == []
    assert "role" not in spend[0] and "push" not in spend[0]
    # No spend role: no spend items, no spend source card.
    r2 = nobody.get("/api/inbox").json()
    assert not any(i["source"] == "spend" for i in r2["items"]) and "spend" not in r2["sources"]
    # Done → hidden for this user only; undo brings it back; snooze hides until a date.
    key = spend[0]["key"]
    assert viewer.post("/api/inbox/ack", json={"key": key, "action": "done"}).status_code == 200
    assert key not in [i["key"] for i in viewer.get("/api/inbox").json()["items"]]
    assert key in [i["key"] for i in viewer.get("/api/inbox?show=done").json()["items"]]
    assert key in [i["key"] for i in editor.get("/api/inbox").json()["items"]]
    viewer.post("/api/inbox/ack", json={"key": key, "action": "undo"})
    s = viewer.post("/api/inbox/ack", json={"key": key, "action": "snooze", "days": 3}).json()
    assert s["until"] == (today_vn() + dt.timedelta(days=3)).isoformat()
    assert key not in [i["key"] for i in viewer.get("/api/inbox").json()["items"]]
    assert viewer.post("/api/inbox/ack", json={"key": key, "action": "snooze", "days": 400}).status_code == 400
    assert viewer.post("/api/inbox/ack", json={"key": key, "action": "x"}).status_code == 400


def test_quote_and_proposal_tasks_follow_roles(app_, editor):
    from test_spend_quotes import _new_request, _quote_file, _upload
    approver = _login(app_, "approver1")
    rq = _new_request(editor, minQuotes=1)
    q = _upload(editor, rq["code"], "Alpha", _quote_file()).json()["quotations"][0]
    inbox.invalidate()
    titles = [i["title"] for i in editor.get("/api/inbox").json()["items"]]
    assert f"{rq['code']} — 1 báo giá chờ kiểm tra" in titles
    assert not any("chờ chọn nhà cung cấp" in t for t in titles)
    editor.post(f"/api/spend/quotes/{rq['code']}/quotations/{q['id']}/verify")
    inbox.invalidate()
    assert not any("chờ chọn" in i["title"] for i in editor.get("/api/inbox").json()["items"])     # editor cannot select
    assert any("chờ chọn nhà cung cấp" in i["title"] for i in approver.get("/api/inbox").json()["items"])


def test_agenda_lists_dated_events(app_, editor):
    viewer = _login(app_, "viewer1")
    end = today_vn() + dt.timedelta(days=45)
    o = editor.post("/api/spend/opex", json=_opex(endDate=end.isoformat(), startDate=(end - dt.timedelta(days=365)).isoformat())).json()
    c = editor.post("/api/spend/contracts", json={"title": "HĐ bảo hành", "opexCode": o["code"]}).json()
    due = today_vn() + dt.timedelta(days=5)
    editor.post(f"/api/spend/contracts/{c['code']}/payments", json={"title": "Đợt 1", "amount": 100_000_000, "dueDate": due.isoformat()})
    a = viewer.get("/api/agenda?days=60").json()
    kinds = [(x["kind"], x["date"], x["tone"]) for x in a["items"]]
    assert ("payment", due.isoformat(), "bad") in kinds and ("opex", end.isoformat(), "warn") in kinds
    assert [x["date"] for x in a["items"]] == sorted(x["date"] for x in a["items"])
    assert _login(app_, "nobody1").get("/api/agenda").json()["items"] == []
    pays = viewer.get("/api/spend/payments").json()
    assert pays["items"][0]["contractCode"] == c["code"] and pays["openTotal"] == 100_000_000
    assert pays["items"][0]["tone"] == "warn" and pays["items"][0]["docsTotal"] == 5


def test_prefs_are_per_user_and_bounded(app_, editor):
    viewer = _login(app_, "viewer1")
    assert editor.put("/api/me/prefs", json={"theme": "light", "evil": "x"}).json() == {"theme": "light"}
    assert editor.get("/api/me/prefs").json() == {"theme": "light"} and viewer.get("/api/me/prefs").json() == {}
    assert editor.put("/api/me/prefs", json={"inboxFilter": "x" * 5000}).status_code == 413
    # Merge, not replace: each screen sends only its own keys; booleans / enums are validated.
    got = editor.put("/api/me/prefs", json={"historyMonths": 6, "historyGroup": "vcenter", "reportAi": True,
                                            "reportArchive": "yes", "agendaDays": 60}).json()
    assert got == {"theme": "light", "historyMonths": 6, "historyGroup": "vcenter", "reportAi": True, "agendaDays": 60}
    assert editor.put("/api/me/prefs", json={"theme": "dark", "historyMonths": 7}).json()["historyMonths"] == 6
    assert editor.get("/api/me/prefs").json()["theme"] == "dark" and viewer.get("/api/me/prefs").json() == {}


def test_ops_alerts_sent_once_and_rearmed(monkeypatch):
    monkeypatch.setattr(inbox, "PROVIDERS", [("capacity", inbox.capacity_items, False)])
    db = SessionLocal()
    db.query(SpendNotifyChannel).delete()
    db.add(SpendNotifyChannel(kind="discord", name="ops", enabled=True, ops_alerts=True, created_by="t",
                              webhook_enc=secrets_box.encrypt("https://discord.com/api/webhooks/123456789012345678/abcDEF_-1")))
    db.add(SpendNotifyChannel(kind="discord", name="spend only", enabled=True, ops_alerts=False, created_by="t",
                              webhook_enc=secrets_box.encrypt("https://discord.com/api/webhooks/223456789012345678/zzzDEF_-2")))
    db.commit()
    posts = []
    post = lambda url, payload: posts.append((url, payload))  # noqa: E731
    r = ops_alerts.check_and_notify(db, post=post)
    assert r == {"new": 1, "resolved": 0, "sent": 1, "failed": 0}
    assert posts[0][0].endswith("/123456789012345678/abcDEF_-1") and "Cluster PRD" in posts[0][1]["content"] and posts[0][1]["allowed_mentions"] == {"parse": []}
    assert ops_alerts.check_and_notify(db, post=post)["new"] == 0 and len(posts) == 1          # still active: not resent
    from app import data_access
    monkeypatch.setattr(data_access, "get_latest_combined", lambda db, keys=None, include_import=True: (HOSTS, VMS[:3], []))
    r = ops_alerts.check_and_notify(db, post=post)
    assert r["resolved"] == 1 and "Đã hết" in posts[-1][1]["content"]
    monkeypatch.setattr(data_access, "get_latest_combined", lambda db, keys=None, include_import=True: (HOSTS, VMS, []))
    assert ops_alerts.check_and_notify(db, post=post)["new"] == 1 and len(posts) == 3          # re-armed
    db.query(SpendNotifyChannel).delete()
    db.commit()
    db.close()


def test_planning_result_on_screen(admin):
    r = admin.get("/api/reports/planning?growth=5,10&horizon=24")
    assert r.status_code == 200, r.text
    d = r.json()
    assert [c["name"] for c in d["clusters"]] == ["DEV", "PRD"]
    prd = d["clusters"][1]
    assert prd["nodes"] >= 1 and prd["firstNeedLabel"] and prd["hosts"] == 2
    assert len(d["totals"]) == len(d["scenarios"]) and d["recommendations"]["recommendations"] if isinstance(d["recommendations"], dict) else True
    assert admin.get("/api/reports/planning?lead=99").status_code == 422


def test_hardening_inputs_and_retry(app_, admin, editor, monkeypatch):
    from app.routers import home
    r = editor.post("/api/inbox/ack", content=b'{"key": "x", "action": "snooze", "days": Infinity}', headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert admin.get("/api/reports/planning?growth=inf").status_code == 400
    assert admin.get("/api/reports/planning?growth=5,600").status_code == 400
    assert editor.put("/api/me/prefs", json={"agendaDays": 7, "inboxFilter": {"level": "<x>", "source": "spend"}, "theme": "x"}).json() == \
        {"inboxFilter": {"level": "", "source": "spend"}}
    monkeypatch.setattr(home, "MAX_ACKS", 3)
    for i in range(5):
        assert editor.post("/api/inbox/ack", json={"key": f"k{i}", "action": "done"}).status_code == 200
    db = SessionLocal()
    assert db.query(InboxAck).filter_by(username="editor1").count() == 3
    db.close()
    # A forced refresh right after a computation reuses the cache (no recompute storm).
    calls = []
    monkeypatch.setattr(inbox, "PROVIDERS", [("capacity", lambda db: calls.append(1) or [], False)])
    inbox.invalidate()
    editor.get("/api/inbox?fresh=true")
    editor.get("/api/inbox?fresh=true")
    assert len(calls) == 1


def test_ops_alerts_respect_features_and_retry_failed_sends(app_, admin, monkeypatch):
    monkeypatch.setattr(inbox, "PROVIDERS", [("capacity", inbox.capacity_items, False), ("backup", lambda db: [
        inbox.item("backup", "bad", "bk|x", "Độ phủ 50%", feature="backup", push=True)], False)])
    db = SessionLocal()
    db.query(SpendNotifyChannel).delete()
    db.add(SpendNotifyChannel(kind="discord", name="ops", enabled=True, ops_alerts=True, created_by="t",
                              webhook_enc=secrets_box.encrypt("https://discord.com/api/webhooks/123456789012345678/abcDEF_-1")))
    db.commit()
    from app.spend.notify import NotifyError

    def boom(url, payload):
        raise NotifyError("Discord trả mã HTTP 500.")
    assert ops_alerts.check_and_notify(db, post=boom) == {"new": 2, "resolved": 0, "sent": 0, "failed": 1}
    posts = []
    admin.put("/api/settings/features", json={"features": {"backup": False}})
    r = ops_alerts.check_and_notify(db, post=lambda u, p: posts.append(p))
    assert r["new"] == 1 and "Độ phủ" not in posts[0]["content"]          # retried capacity; backup is switched off
    admin.put("/api/settings/features", json={"features": {"backup": True}})
    long = [{"level": "bad", "title": "x" * 300} for _ in range(20)]
    assert len(ops_alerts.messages(long, [])) >= 4 and all(len(m) <= 1900 for m in ops_alerts.messages(long, []))
    db.query(SpendNotifyChannel).delete()
    db.commit()
    db.close()


def test_inbox_assignee_set_shown_cleared_and_permission_checked(app_, admin, editor):
    items = admin.get("/api/inbox").json()["items"]
    cap = next(i for i in items if i["source"] == "capacity")
    assert cap["assignee"] is None
    names = {u["username"] for u in admin.get("/api/inbox/assignees", params={"key": cap["key"]}).json()}
    assert {"admin", "editor1"} <= names
    # not a user directory: an item key is required and must be visible to the caller
    assert admin.get("/api/inbox/assignees").status_code == 404
    assert editor.get("/api/inbox/assignees", params={"key": "no-such-item"}).status_code == 404
    r = admin.post("/api/inbox/assign", json={"key": cap["key"], "assignee": "editor1"})
    assert r.status_code == 200 and r.json()["assignee"]["username"] == "editor1"
    # shared: the editor sees who handles it, and so does anyone else
    mine = next(i for i in editor.get("/api/inbox").json()["items"] if i["key"] == cap["key"])
    assert mine["assignee"]["username"] == "editor1" and mine["assignee"]["assignedBy"]
    db = SessionLocal()
    assert db.query(AuditLog).filter_by(action="inbox.assign").count() >= 1
    db.close()
    assert admin.post("/api/inbox/assign", json={"key": cap["key"], "assignee": "nobody"}).status_code == 400
    assert admin.post("/api/inbox/assign", json={"key": "no-such-item", "assignee": "editor1"}).status_code == 404
    assert admin.post("/api/inbox/assign", json={"key": cap["key"], "assignee": 5}).status_code == 400
    r = editor.post("/api/inbox/assign", json={"key": cap["key"], "assignee": None})
    assert r.status_code == 200 and r.json()["assignee"] is None
    assert next(i for i in admin.get("/api/inbox").json()["items"] if i["key"] == cap["key"])["assignee"] is None


def test_inbox_assign_refuses_people_who_cannot_see_the_item(app_, admin, editor):
    # admin-only items (role 'admin') cannot be given to a non-admin
    from app import inbox as ib
    key = "test|admin-only"
    real = ib._compute
    ib.invalidate()

    def fake(db, today):
        items, errs = real(db, today)
        return items + [ib.item("system", "warn", key, "Chỉ admin", role="admin")], errs
    import pytest as _pt
    mp = _pt.MonkeyPatch()
    mp.setattr(ib, "_compute", fake)
    try:
        r = admin.post("/api/inbox/assign", json={"key": key, "assignee": "editor1"})
        assert r.status_code == 400 and "không có quyền" in r.json()["detail"]
        assert editor.post("/api/inbox/assign", json={"key": key, "assignee": "admin"}).status_code == 404
    finally:
        mp.undo()
        ib.invalidate()
