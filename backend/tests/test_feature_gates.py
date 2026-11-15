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


def test_export_registry_follows_features_and_roles(app_, admin):
    from test_spend_opex import _login
    ids = {e["id"] for e in admin.get("/api/exports").json()["items"]}
    assert {"capacity-pdf", "backup-pdf", "license-pdf", "budget-pdf", "bom-xlsx"} <= ids
    nobody = _login(app_, "nobody1")                                         # no spend role
    assert not any(e["group"] in ("spend", "renovation") for e in nobody.get("/api/exports").json()["items"])
    _set("license", False)
    assert "license-pdf" not in {e["id"] for e in admin.get("/api/exports").json()["items"]}
    # the report archive keeps exactly the archivable exports of the registry
    from app import exports, report_archive
    assert sorted(report_archive.KIND_LABELS) == sorted({e["group"] for e in exports.EXPORTS if e["archive"]})
    assert report_archive.kind_of("/api/backup/report/xlsx") is None and report_archive.kind_of("/api/backup/report/pdf") == ("backup", "general")


def test_scope_model():
    from app.scope import Scope, split_csv
    assert split_csv(None) is None and split_csv("") == [] and split_csv(" a, ,b ") == ["a", "b"]
    s = Scope.from_query("vc1", "PRD,DEV", None)
    assert s.keys == ["vc1"] and s.clusters == ["PRD", "DEV"] and s.hosts is None and "cluster: PRD, DEV" in s.label
    assert Scope.from_query("all").keys is None
