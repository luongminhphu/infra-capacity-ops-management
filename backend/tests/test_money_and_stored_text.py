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
    for _ in range(3):                                             # what record() writes: integer micro-USD only
        db.add(AiRun(task="home_brief", status="ok", cost_usd_micros=money.usd_to_micros(0.0123456), created_at=now))
    db.commit()
    # each run is 12 346 micro-USD (rounded once when written) → 37 038, never a float accumulation
    assert ai_platform.month_cost_micros(db) == 37038
    assert ai_platform.month_cost(db) == 0.037038
    rep = ai_platform.usage_report(db)
    assert rep["costUsd"] == 0.037 and rep["byTask"]["home_brief"]["costUsd"] == 0.037
    assert [r["costUsd"] for r in rep["recent"]] == [0.012346] * 3
    assert f"{ai_platform.month_cost(db):.2f}".replace(".", ",") == "0,04"


def _add_legacy_column(table, col):
    """Recreate the pre-release-3 Float column so a test can play an older pod writing to it."""
    from sqlalchemy import inspect
    with engine.begin() as conn:
        if col not in {c["name"] for c in inspect(conn).get_columns(table)}:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} FLOAT"))


def _has_column(table, col):
    from sqlalchemy import inspect
    with engine.connect() as conn:
        return col in {c["name"] for c in inspect(conn).get_columns(table)}


def test_ai_row_from_old_pod_counts_after_backfill(ai_clean):
    """A release-2 pod still running during the rollout writes cost_usd (+ micros); a release-1 pod only
    cost_usd. ensure_schema() copies the Float into micros, then (SQLite / DROP_LEGACY_MONEY_COLUMNS) drops it."""
    db = ai_clean
    now = dt.datetime.utcnow()
    _add_legacy_column("ai_runs", "cost_usd")
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO ai_runs (task, status, cost_usd, cost_usd_micros, created_at) VALUES "
                          "('home_brief', 'ok', 0.25, 250000, :t), ('home_brief', 'ok', 0.1234564, NULL, :t), "
                          "('home_brief', 'ok', 9.0, 9000000, :old)"), {"t": now, "old": now - dt.timedelta(days=40)})
    ensure_schema()
    db.expire_all()
    assert not _has_column("ai_runs", "cost_usd")                  # legacy Float column gone
    assert ai_platform.month_cost_micros(db) == 250000 + 123456     # last month's row not counted
    assert sorted(r["costUsd"] for r in ai_platform.usage_report(db)["recent"]) == [0.123456, 0.25, 9.0]


def test_ai_budget_compares_micros(ai_clean, admin):
    db = ai_clean
    assert admin.put("/api/admin/ai", json={"provider": "anthropic", "apiKey": "sk-ant-x-0000", "allowExternal": True,
                                            "budgetUsd": 1, "alertUsd": 1}).status_code == 200
    db.add(AiRun(task="home_brief", status="ok", cost_usd_micros=999999, created_at=dt.datetime.utcnow()))
    db.commit()
    ai_platform.check(db, "home_brief", None)                       # 999 999 < 1 000 000 micro-USD: allowed
    db.add(AiRun(task="home_brief", status="ok", cost_usd_micros=1, created_at=dt.datetime.utcnow()))
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


def test_entitlement_written_as_integer_only(admin, ent_clean):
    r = admin.post("/api/licensing/entitlements", json={"product": "ws_dc", "metric": "pack16", "quantity": 3,
                                                         "unitPrice": 1234.55, "currency": "usd"})
    assert r.status_code == 200, r.text
    row = ent_clean.query(LicenseEntitlement).one()
    assert (row.unit_price_minor, row.currency) == (123455, "USD")
    assert not hasattr(row, "unit_price")                         # release 3: no Float column mapped
    [item] = admin.get("/api/licensing/entitlements").json()["items"]
    assert (item["unitPrice"], item["unitPriceMinor"]) == (1234.55, 123455)
    ws = admin.get("/api/licensing/assess").json()["windowsServer"]["datacenter"]["entitlement"]
    assert ws["totalCost"] == 3703.65                            # 3 × 123 455 cents, not 3703.6499999999996


def test_entitlement_from_old_pod_backfilled_then_column_dropped(ent_clean):
    _add_legacy_column("license_entitlements", "unit_price")
    with engine.begin() as conn:                                  # what a release-1 pod leaves behind: Float only
        conn.execute(text("INSERT INTO license_entitlements (product, metric, quantity, kind, unit_price, currency, has_sa) "
                          "VALUES ('vvf', 'core', 32, 'owned', 1234.5, 'USD', false), "
                          "('ws_std', 'pack2', 3, 'owned', 1234567.6, 'VND', false)"))
    ensure_schema()
    ent_clean.expire_all()
    assert not _has_column("license_entitlements", "unit_price")
    items = {e["product"]: e for e in lic_router._entitlements(ent_clean)}
    assert (items["vvf"]["unitPriceMinor"], items["vvf"]["unitPrice"]) == (123450, 1234.5)
    assert (items["ws_std"]["unitPriceMinor"], items["ws_std"]["unitPrice"]) == (1234568, 1234568.0)
    ensure_schema()                                               # idempotent once the column is gone


def test_postgres_keeps_legacy_columns_unless_asked(monkeypatch):
    """Rule 4: on PostgreSQL the drop waits for DROP_LEGACY_MONEY_COLUMNS (older pods may still read the column)."""
    import inspect as pyinspect
    from app import database
    src = pyinspect.getsource(database.ensure_schema)
    assert "if not pg or get_settings().drop_legacy_money_columns:" in src
    assert "ALTER TABLE ai_runs ALTER COLUMN cost_usd DROP NOT NULL" in src
    from app.config import Settings
    assert Settings.model_fields["drop_legacy_money_columns"].default is False


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
