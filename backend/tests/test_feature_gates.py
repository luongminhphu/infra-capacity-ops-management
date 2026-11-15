"""Feature switches answer the same way everywhere (super app): a module switched off in Cài đặt →
Tính năng returns 404 on its API, while shared pages (Tài sản → ESXi / Hệ điều hành) keep working."""
import pytest

from app.database import SessionLocal
from app.features import ensure_feature, is_on
from app.models import AppSetting

from test_spend_opex import admin, app_  # noqa: F401  (fixtures)


def _set(key: str, on: bool) -> None:
    db = SessionLocal()
    row = db.get(AppSetting, f"feature.{key}") or AppSetting(key=f"feature.{key}")
    row.value = "true" if on else "false"
    db.merge(row)
    db.commit()
    db.close()


@pytest.fixture(autouse=True)
def _restore(app_):
    yield
    for k in ("license", "planning", "backup"):
        _set(k, True)


def test_license_off_keeps_asset_endpoints(app_, admin):
    _set("license", False)
    for path in ("/api/licensing/assess", "/api/licensing/rules", "/api/licensing/entitlements", "/api/licensing/assessments"):
        assert admin.get(path).status_code == 404, path
    assert admin.post("/api/licensing/whatif", json={}).status_code == 404
    for path in ("/api/licensing/inventory", "/api/licensing/clusters", "/api/licensing/overrides"):
        assert admin.get(path).status_code == 200, path                       # Tài sản → ESXi / Hệ điều hành
    _set("license", True)
    assert admin.get("/api/licensing/rules").status_code == 200


def test_planning_off(app_, admin):
    _set("planning", False)
    for path in ("/api/reports/planning", "/api/reports/pdf?view=planning"):
        r = admin.get(path)
        assert r.status_code == 404 and "đang tắt" in r.json()["detail"], path
    _set("planning", True)
    assert "đang tắt" not in admin.get("/api/reports/planning").json().get("detail", "")


def test_helpers():
    assert is_on({}, "backup") and is_on({"backup": True}, "backup") and not is_on({"backup": False}, "backup")
    _set("backup", False)
    db = SessionLocal()
    with pytest.raises(Exception) as e:
        ensure_feature(db, "backup")
    assert getattr(e.value, "status_code", None) == 404
    db.close()
