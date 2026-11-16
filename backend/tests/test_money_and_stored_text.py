"""DAT-10 release 2 (money read from integer columns) and SCH-10 (JSON / CSV text validated on write)."""
import datetime as dt

import pytest
from sqlalchemy import text

from app import ai_platform
from app.database import SessionLocal, engine, ensure_schema
from app.models import AiRun, AuditLog, BackupScheduleRule, LicenseEntitlement, LicenseRuleSet, OpsAlertState, UserPref
from app.routers import licensing as lic_router
from app.shared import money
from app.shared.stored_text import InvalidStoredValue, check_csv, check_json
from app.spend.models import SpendAiConfig, SpendNotifyChannel

from test_spend_opex import _login, admin, app_, editor  # noqa: F401  (fixtures)


# ---------------------------------------------------------------------------
# money helpers
# ---------------------------------------------------------------------------

def test_money_helpers():
    assert money.to_minor(1234.5, "USD") == 123450
    assert money.to_minor(1234.55, "usd") == 123455               # not 123454 (float 123454.99999)
    assert money.from_minor(123455, "USD") == 1234.55
    assert money.from_minor(1_234_568, "VND") == 1_234_568.0
    assert money.usd_to_micros(0.0123456) == 12346               # HALF_UP on the decimal value
    assert money.micros_to_usd(37038) == 0.037038
    assert money.mul_minor(123455, 5, 16) == 38580                # 38579.6875 → 38580
    assert money.mul_minor(123455, 3) == 370365


# ---------------------------------------------------------------------------
# AI cost: integer micro-USD sums
# ---------------------------------------------------------------------------

@pytest.fixture()
def ai_clean(app_):
    db = SessionLocal()
    db.query(AiRun).delete()
    db.query(SpendAiConfig).delete()
    db.commit()
    yield db
    db.query(AiRun).delete()
    db.query(SpendAiConfig).delete()
    db.commit()
    db.close()


def test_ai_month_cost_is_an_integer_sum(ai_clean):
    db = ai_clean
    now = dt.datetime.utcnow()
    for _ in range(3):                                             # what record() writes: both columns
        db.add(AiRun(task="home_brief", status="ok", cost_usd=0.0123456, cost_usd_micros=money.usd_to_micros(0.0123456), created_at=now))
    db.commit()
    # each run is 12 346 micro-USD (rounded once when written) → 37 038, never a float accumulation
    assert ai_platform.month_cost_micros(db) == 37038
    assert ai_platform.month_cost(db) == 0.037038
    rep = ai_platform.usage_report(db)
    assert rep["costUsd"] == 0.037 and rep["byTask"]["home_brief"]["costUsd"] == 0.037
    assert [r["costUsd"] for r in rep["recent"]] == [0.012346] * 3
    assert f"{ai_platform.month_cost(db):.2f}".replace(".", ",") == "0,04"


def test_ai_row_from_old_pod_counts(ai_clean):
    """A release-1 pod writing after ensure_schema() ran leaves cost_usd_micros NULL: still summed exactly."""
    db = ai_clean
    now = dt.datetime.utcnow()
    db.add(AiRun(task="home_brief", status="ok", cost_usd=0.25, cost_usd_micros=250000, created_at=now))
    db.add(AiRun(task="home_brief", status="ok", cost_usd=0.1234564, created_at=now))   # old pod: Float only
    db.add(AiRun(task="home_brief", status="ok", cost_usd=9.0, cost_usd_micros=9_000_000,
                 created_at=now - dt.timedelta(days=40)))                                 # last month: not counted
    db.commit()
    assert ai_platform.month_cost_micros(db) == 250000 + 123456
    assert ai_platform.usage_report(db)["recent"][1]["costUsd"] == 0.123456


def test_ai_budget_compares_micros(ai_clean, admin):
    db = ai_clean
    assert admin.put("/api/admin/ai", json={"provider": "anthropic", "apiKey": "sk-ant-x-0000", "allowExternal": True,
                                            "budgetUsd": 1, "alertUsd": 1}).status_code == 200
    db.add(AiRun(task="home_brief", status="ok", cost_usd=0.999999, cost_usd_micros=999999, created_at=dt.datetime.utcnow()))
    db.commit()
    ai_platform.check(db, "home_brief", None)                       # 999 999 < 1 000 000 micro-USD: allowed
    db.add(AiRun(task="home_brief", status="ok", cost_usd=0.000001, cost_usd_micros=1, created_at=dt.datetime.utcnow()))
    db.commit()
    with pytest.raises(ai_platform.AiBlocked) as exc:
        ai_platform.check(db, "home_brief", None)
    assert "1,00 / 1 USD" in str(exc.value)


# ---------------------------------------------------------------------------
# License entitlements: integer minor units
# ---------------------------------------------------------------------------

@pytest.fixture()
def ent_clean(app_):
    db = SessionLocal()
    db.query(LicenseEntitlement).delete()
    db.commit()
    yield db
    db.query(LicenseEntitlement).delete()
    db.commit()
    db.close()


def test_entitlement_written_as_integer_and_float(admin, ent_clean):
    r = admin.post("/api/licensing/entitlements", json={"product": "ws_dc", "metric": "pack16", "quantity": 3,
                                                         "unitPrice": 1234.55, "currency": "usd"})
    assert r.status_code == 200, r.text
    row = ent_clean.query(LicenseEntitlement).one()
    assert (row.unit_price_minor, row.unit_price, row.currency) == (123455, 1234.55, "USD")   # Float still written
    [item] = admin.get("/api/licensing/entitlements").json()["items"]
    assert (item["unitPrice"], item["unitPriceMinor"]) == (1234.55, 123455)
    ws = admin.get("/api/licensing/assess").json()["windowsServer"]["datacenter"]["entitlement"]
    assert ws["totalCost"] == 3703.65                            # 3 × 123 455 cents, not 3703.6499999999996


def test_entitlement_from_old_pod_reads_correctly_before_and_after_backfill(ent_clean):
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO license_entitlements (product, metric, quantity, kind, unit_price, currency, has_sa) "
                          "VALUES ('vvf', 'core', 32, 'owned', 1234.5, 'USD', false), "
                          "('ws_std', 'pack2', 3, 'owned', 1234567.6, 'VND', false)"))
    rows = {r.product: r for r in ent_clean.query(LicenseEntitlement)}
    assert rows["vvf"].unit_price_minor is None                  # what a release-1 pod leaves behind
    before = {e["product"]: e for e in lic_router._entitlements(ent_clean)}
    assert (before["vvf"]["unitPriceMinor"], before["vvf"]["unitPrice"]) == (123450, 1234.5)
    assert (before["ws_std"]["unitPriceMinor"], before["ws_std"]["unitPrice"]) == (1234568, 1234568.0)
    ensure_schema()                                               # backfill on the next start
    ent_clean.expire_all()
    rows = {r.product: r for r in ent_clean.query(LicenseEntitlement)}
    assert (rows["vvf"].unit_price_minor, rows["ws_std"].unit_price_minor) == (123450, 1234568)
    assert {e["product"]: e for e in lic_router._entitlements(ent_clean)} == before


def test_ai_backfill_after_old_pod_write(ai_clean):
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO ai_runs (task, status, cost_usd, created_at) VALUES ('home_brief', 'ok', 0.0123456, :t)"),
                     {"t": dt.datetime.utcnow()})
    ensure_schema()
    ai_clean.expire_all()
    assert [r.cost_usd_micros for r in ai_clean.query(AiRun)] == [12346]


# ---------------------------------------------------------------------------
# SCH-10: JSON / CSV text columns
# ---------------------------------------------------------------------------

def test_check_json_and_csv():
    assert check_json('{"a": 1}', dict, "X") == '{"a": 1}'
    assert check_json(None, dict, "X") is None
    with pytest.raises(InvalidStoredValue, match="X: không phải JSON hợp lệ"):
        check_json('{"a": ', dict, "X")
    with pytest.raises(InvalidStoredValue, match=r"phải là đối tượng JSON"):
        check_json("[1]", dict, "X")
    with pytest.raises(InvalidStoredValue, match=r"phải là danh sách JSON"):
        check_json("{}", list, "X")
    with pytest.raises(InvalidStoredValue, match="không được để trống"):
        check_json(None, dict, "X", nullable=False)
    with pytest.raises(InvalidStoredValue, match="json.dumps"):
        check_json({"a": 1}, dict, "X")
    assert check_csv("0,1,6", "D", item=r"[0-6]") == "0,1,6"
    assert check_csv("", "D", item=r"[0-6]") == ""
    with pytest.raises(InvalidStoredValue, match="“7”"):
        check_csv("0,7", "D", item=r"[0-6]")
    with pytest.raises(InvalidStoredValue, match="phần tử rỗng"):
        check_csv("1,,2", "D")


def test_orm_writes_are_checked():
    with pytest.raises(InvalidStoredValue, match="Tuỳ chọn người dùng"):
        UserPref(username="x", data="{broken")
    with pytest.raises(InvalidStoredValue, match="Tham số rule license: phải là đối tượng JSON"):
        LicenseRuleSet(product="vmware", key="k", name="n", params_json="[]")
    with pytest.raises(InvalidStoredValue, match="Ngày chạy Full"):
        BackupScheduleRule(system="S", full_days="4,7")
    with pytest.raises(InvalidStoredValue, match="Email người nhận"):
        SpendNotifyChannel(name="c", kind="email", recipients="a@b.vn, not-an-email")
    s = OpsAlertState(key="k", title="t")
    with pytest.raises(InvalidStoredValue):
        s.sent_channels = "1,,2"
    s.sent_channels = "1,2"
    assert SpendAiConfig(tasks_json='{"home_brief": true}').tasks_json == '{"home_brief": true}'
    assert AuditLog(action="a", detail='"any JSON value"').detail == '"any JSON value"'


def test_mcp_audit_detail_stays_valid_json():
    """The MCP audit row used to be json.dumps(...)[:2000]: long arguments produced broken JSON."""
    import json
    from app import mcp_server
    src = open(mcp_server.__file__, encoding="utf-8").read()
    assert "default=str)[:2000]" not in src
    big = {"q": "x" * 5000}
    detail = json.dumps({"tool": "t", "argsTruncated": json.dumps(big, default=str)[:1500], "token": "p"})
    assert AuditLog(action="mcp.tool", detail=detail).detail == detail


def test_api_answers_422_in_vietnamese(app_, admin):
    """A write of the wrong shape inside a request handler surfaces as 422 with the message, nothing saved."""
    from fastapi import Depends
    from sqlalchemy.orm import Session
    from app.database import get_db

    def _bad(db: Session = Depends(get_db)):
        db.add(UserPref(username="sch10", data="not json"))
        db.commit()

    app_.add_api_route("/api/__sch10_probe", _bad, methods=["POST"])
    app_.router.routes.insert(0, app_.router.routes.pop())          # ahead of the static catch-all
    try:
        r = admin.post("/api/__sch10_probe")
    finally:
        app_.router.routes[:] = [rt for rt in app_.router.routes if getattr(rt, "path", "") != "/api/__sch10_probe"]
    assert r.status_code == 422
    assert r.json() == {"detail": "Tuỳ chọn người dùng: không phải JSON hợp lệ."}
    db = SessionLocal()
    assert db.get(UserPref, "sch10") is None
    db.close()
