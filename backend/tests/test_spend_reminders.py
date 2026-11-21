"""Expiry reminders: once per threshold, catch-up, renew stops them, escaping, secrets never returned."""
import datetime as dt

import pytest

from app.database import SessionLocal
from app.spend import notify, reminders
from app.spend.models import SpendNotifyChannel, SpendReminderLog
from app.spend.notify import NotifyError, ReminderLine, discord_escape, discord_messages, email_content, validate_discord_url

from test_spend_opex import _login, _opex, admin, app_, editor  # noqa: F401  (fixtures)

WEBHOOK = "https://discord.com/api/webhooks/123456789/AbC-def_123"
# 01:00 UTC = 08:00 in Vietnam: business day 2026-11-14.
NOW = dt.datetime(2026, 11, 14, 1, 0)
TODAY = dt.date(2026, 11, 14)


@pytest.fixture()
def sent(monkeypatch):
    calls = []
    monkeypatch.setitem(notify.SENDERS, "discord", lambda url, lines, test=False: calls.append(("discord", url, [l.code for l in lines])))
    monkeypatch.setitem(notify.SENDERS, "email", lambda cfg, ch, lines, test=False: calls.append(("email", ch.name, [l.code for l in lines])))
    return calls


def _expiring(editor, days, today=TODAY, **kw):
    end = today + dt.timedelta(days=days)
    return editor.post("/api/spend/opex", json=_opex(startDate=(end - dt.timedelta(days=364)).isoformat(),
                                                      endDate=end.isoformat(), **kw)).json()


def _run(at=NOW):
    db = SessionLocal()
    try:
        return reminders.run(db, at)
    finally:
        db.close()


def _log():
    db = SessionLocal()
    rows = [(r.threshold_days, r.status, r.attempts) for r in db.query(SpendReminderLog).order_by(SpendReminderLog.id)]
    db.close()
    return rows


def test_channel_secret_is_write_only(admin):
    r = admin.post("/api/spend/admin/channels", json={"kind": "discord", "name": "Kênh hạ tầng", "webhookUrl": WEBHOOK})
    assert r.status_code == 200 and r.json()["hasWebhook"] and WEBHOOK not in r.text
    assert "AbC-def_123" not in admin.get("/api/spend/admin/channels").text
    db = SessionLocal()
    assert "AbC-def_123" not in db.query(SpendNotifyChannel).one().webhook_enc
    db.close()
    # Edit without a new URL keeps the stored one.
    cid = r.json()["id"]
    assert admin.put(f"/api/spend/admin/channels/{cid}", json={"name": "Đổi tên", "enabled": False}).json()["hasWebhook"]
    r = admin.put("/api/spend/admin/smtp", json={"host": "smtp.corp.example", "port": 587, "security": "starttls",
                                                 "username": "ico", "password": "Smtp-Secret-1", "fromAddr": "ico@corp.example"})
    assert r.json()["hasPassword"] and "Smtp-Secret-1" not in r.text
    assert "Smtp-Secret-1" not in admin.get("/api/spend/admin/smtp").text


@pytest.mark.parametrize("url", [
    "http://discord.com/api/webhooks/1/x", "https://evil.example/api/webhooks/1/x", "https://discord.com.evil.example/api/webhooks/1/x",
    "https://discord.com/api/v9/users/@me", "https://user:pw@discord.com/api/webhooks/1/x", "https://discord.com:8443/api/webhooks/1/x",
    "https://169.254.169.254/api/webhooks/1/x",
])
def test_discord_url_ssrf_guard(url):
    with pytest.raises(NotifyError):
        validate_discord_url(url)


def test_reminder_sent_once_per_threshold(admin, editor, sent):
    admin.post("/api/spend/admin/channels", json={"kind": "discord", "name": "Discord", "webhookUrl": WEBHOOK})
    item = _expiring(editor, 60)
    other = _expiring(editor, 120)                          # outside the window today
    s = _run()
    assert s["sent"] == 1 and sent == [("discord", WEBHOOK, [item["code"]])]
    assert _run()["sent"] == 0 and len(sent) == 1           # same day again: nothing new
    assert _run(NOW + dt.timedelta(days=1))["sent"] == 0    # 59 days left: still the 60 threshold
    # 30 days left -> the 30 threshold, once.
    _run(NOW + dt.timedelta(days=30))
    _run(NOW + dt.timedelta(days=30, hours=2))
    # One digest: the 30-day threshold of the first item + the 90-day one of the other (now 90 days left).
    assert [c[2] for c in sent] == [[item["code"]], [item["code"], other["code"]]]
    assert sorted(_log()) == [(30, "sent", 1), (60, "sent", 1), (90, "sent", 1), (90, "skipped", 0)]


def test_catch_up_sends_one_message_and_renew_stops(admin, editor, sent):
    admin.post("/api/spend/admin/channels", json={"kind": "discord", "name": "Discord", "webhookUrl": WEBHOOK})
    item = _expiring(editor, 25)                            # app was down: 90 and 60 were missed
    _run()
    assert len(sent) == 1 and sorted(_log()) == [(30, "sent", 1), (60, "skipped", 0), (90, "skipped", 0)]
    editor.post(f"/api/spend/opex/{item['code']}/renew", json={})
    sent.clear()
    _run(NOW + dt.timedelta(days=30))                       # new item ends a year later; old one renewed
    assert sent == []


def test_changed_end_date_starts_a_new_cycle(admin, editor, sent):
    admin.post("/api/spend/admin/channels", json={"kind": "discord", "name": "Discord", "webhookUrl": WEBHOOK})
    item = _expiring(editor, 30)
    _run()
    new_end = TODAY + dt.timedelta(days=55)
    body = {**_opex(startDate=item["startDate"], endDate=new_end.isoformat()), "rowVersion": item["rowVersion"]}
    assert editor.put(f"/api/spend/opex/{item['code']}", json=body).status_code == 200
    _run(NOW + dt.timedelta(hours=1))
    assert len(sent) == 2                                    # 60 threshold of the new end date


def test_failure_is_retried_then_stops_and_other_channels_continue(admin, editor, sent, monkeypatch):
    admin.post("/api/spend/admin/channels", json={"kind": "discord", "name": "Discord", "webhookUrl": WEBHOOK})
    admin.post("/api/spend/admin/channels", json={"kind": "email", "name": "Email hạ tầng", "recipients": "infra@corp.example"})

    def boom(url, lines, test=False):
        raise NotifyError("Discord trả mã HTTP 500.")
    monkeypatch.setitem(notify.SENDERS, "discord", boom)
    _expiring(editor, 10)
    for i in range(reminders.MAX_ATTEMPTS + 2):
        _run(NOW + dt.timedelta(minutes=15 * i))
    rows = _log()
    assert (30, "failed", reminders.MAX_ATTEMPTS) in rows
    assert [c[0] for c in sent] == ["email"]                 # e-mail delivered once despite Discord failing


def test_stale_pending_is_not_resent(admin, editor, sent):
    admin.post("/api/spend/admin/channels", json={"kind": "discord", "name": "Discord", "webhookUrl": WEBHOOK})
    item = _expiring(editor, 30)
    db = SessionLocal()
    ch = db.query(SpendNotifyChannel).one()
    db.add(SpendReminderLog(opex_id=item["id"], end_date=dt.date.fromisoformat(item["endDate"]), threshold_days=30,
                            channel_id=ch.id, status="pending", attempts=0, created_at=NOW - dt.timedelta(hours=3)))
    db.commit()
    db.close()
    _run()
    assert sent == [] and (30, "unknown", 0) in _log()


def test_manual_run_and_log_api(admin, editor, sent):
    from app.spend.fiscal import today_vn
    admin.post("/api/spend/admin/channels", json={"kind": "discord", "name": "Discord", "webhookUrl": WEBHOOK})
    _expiring(editor, 5, today=today_vn())          # the API runs on the real clock, not on the fixed TODAY
    r = admin.post("/api/spend/admin/reminders/run")
    assert r.status_code == 200 and r.json()["sent"] == 1
    log = admin.get("/api/spend/admin/reminders").json()
    assert log["counts"]["sent"] == 1 and log["state"]["sent"] == 1
    assert _login_editor_cannot(editor)


def _login_editor_cannot(editor):
    return editor.post("/api/spend/admin/reminders/run").status_code == 403


def test_hour_gate(admin, editor, sent):
    admin.post("/api/spend/admin/channels", json={"kind": "discord", "name": "Discord", "webhookUrl": WEBHOOK})
    _expiring(editor, 5)
    assert reminders.tick(dt.datetime(2026, 11, 13, 23, 0)) is None     # 06:00 VN, before 08:00
    assert reminders.tick(dt.datetime(2026, 11, 14, 2, 0))["sent"] == 1


def test_message_escaping():
    evil = ReminderLine(code="OPX-2026-0001", title="@everyone [bấm vào](https://phish.example) **gấp**",
                        vendor="<script>alert(1)</script>", end_date="01/01/2027", days_left=30,
                        amount="1.000 đ", owner="A & B", owner_email=None, link="https://ico.corp.example/#/spend-opex?code=OPX-2026-0001")
    msg = "".join(discord_messages([evil]))
    assert "@everyone" not in msg and "@​everyone" in msg
    assert "](https" not in msg and "\\[bấm vào\\]" in msg
    subject, text, body = email_content([evil])
    assert "<script>" not in body and "&lt;script&gt;" in body and "A &amp; B" in body
    assert discord_escape("a*b_c") == "a\\*b\\_c"


def test_discord_split_respects_limit():
    lines = [ReminderLine(code=f"OPX-2026-{i:04d}", title="x" * 150, vendor="v", end_date="01/01/2027", days_left=30,
                          amount="1 đ", owner="o", owner_email=None, link=None) for i in range(40)]
    chunks = discord_messages(lines)
    assert len(chunks) > 1 and all(len(c) <= 2000 for c in chunks)
    assert sum(c.count("OPX\\-2026") for c in chunks) == 40


def test_email_partial_delivery_is_not_repeated(monkeypatch):
    """Audit: owner b@ rejected → the whole digest was marked failed and it@ / a@ got it again on every retry."""
    from types import SimpleNamespace as NS
    got = []

    def smtp(cfg, to, subject, text, html):
        got.append(to[0])
        if to[0] == "b@corp.example":
            raise NotifyError("550 mailbox unavailable")
    monkeypatch.setattr(notify, "smtp_send", smtp)
    line = lambda o: ReminderLine(code="OPX-1", title="t", vendor="v", end_date="01/01/2027", days_left=30, amount="1 đ",  # noqa: E731
                                  owner=o, owner_email=o, link=None)
    ch = NS(recipients="it@corp.example", include_owner=True)
    with pytest.raises(notify.PartialDelivery):
        notify.send_email(NS(), ch, [line("a@corp.example"), line("b@corp.example")])
    assert got == ["it@corp.example", "a@corp.example", "b@corp.example"]
    got.clear()
    with pytest.raises(NotifyError) as e:                    # nothing went out → a plain failure, retried
        notify.send_email(NS(), NS(recipients="b@corp.example", include_owner=False), [line("o")])
    assert not isinstance(e.value, notify.PartialDelivery)


def test_partial_delivery_counts_as_sent(admin, editor, sent, monkeypatch):
    admin.post("/api/spend/admin/channels", json={"kind": "discord", "name": "Discord", "webhookUrl": WEBHOOK})
    calls = []

    def half(url, lines, test=False):
        calls.append(1)
        raise notify.PartialDelivery("Đã gửi 1 phần, phần 2 lỗi: HTTP 429")
    monkeypatch.setitem(notify.SENDERS, "discord", half)
    _expiring(editor, 10)
    for i in range(3):
        _run(NOW + dt.timedelta(minutes=15 * i))
    assert len(calls) == 1 and (30, "sent", 1) in _log()


def test_discord_part_failure_is_not_resent(app_):  # noqa: F811
    from app import alert_rules, ops_alerts, secrets_box
    from app.models import OpsAlertState
    from app.spend.models import SpendNotifyChannel
    db = SessionLocal()
    db.query(OpsAlertState).delete()
    db.query(SpendNotifyChannel).delete()
    db.add(SpendNotifyChannel(kind="discord", name="ops", enabled=True, ops_alerts=True, created_by="t",
                              webhook_enc=secrets_box.encrypt("https://discord.com/api/webhooks/123456789012345678/abcDEF_-1")))
    db.commit()
    alert_rules  # noqa: B018 — default rules: capacity items with push=True are sent
    items = [{"key": f"cap|c{i}", "alertKey": f"cap|c{i}", "source": "capacity", "level": "bad", "push": True,
              "title": f"Cluster C{i:02d} " + "x" * 300} for i in range(20)]
    posts = []

    def flaky(url, payload):
        if len(posts) == 1:                                  # part 2 fails once (Discord 429)
            posts.append(None)
            raise NotifyError("HTTP 429")
        posts.append(payload["content"])
    import re
    ops_alerts.check_and_notify(db, post=flaky, computed=(items, []))
    ops_alerts.check_and_notify(db, post=flaky, computed=(items, []))
    names = [n for p in posts if p for n in re.findall(r"Cluster C\d\d", p)]
    assert len(posts) > 3 and sorted(names) == [f"Cluster C{i:02d}" for i in range(20)]     # each alert posted exactly once
    db.query(OpsAlertState).delete()
    db.query(SpendNotifyChannel).delete()
    db.commit()
    db.close()
