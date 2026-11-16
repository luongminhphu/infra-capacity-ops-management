"""OS overrides for license classification: one row per VM (DAT-08)."""
import datetime as dt

import pytest
from sqlalchemy import false, text
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal, engine, ensure_schema
from app.licensing.inventory import _overrides_map
from app.models import VmOsOverride
from app.routers import licensing

from test_spend_opex import _login, admin, app_, editor  # noqa: F401  (fixtures)

BODY = {"vcenterKey": "vc-ovr", "instanceUuid": "u-ovr-1", "vm": "app-01", "category": "linux", "osVersion": "RHEL 8"}


def _rows(db):
    return db.query(VmOsOverride).filter_by(vcenter_key="vc-ovr").order_by(VmOsOverride.id).all()


@pytest.fixture()
def clean():
    db = SessionLocal()
    db.query(VmOsOverride).filter_by(vcenter_key="vc-ovr").delete()
    db.commit()
    yield db
    db.query(VmOsOverride).filter_by(vcenter_key="vc-ovr").delete()
    db.commit()
    db.close()


def test_upsert_twice_keeps_one_row_and_index_rejects_duplicates(admin, clean):
    assert admin.put("/api/licensing/overrides", json=BODY).status_code == 200
    assert admin.put("/api/licensing/overrides", json={**BODY, "vm": "app-01-renamed", "osVersion": "RHEL 9"}).status_code == 200
    assert admin.put("/api/licensing/overrides", json={**BODY, "instanceUuid": None, "vm": "imported-01"}).status_code == 200
    assert admin.put("/api/licensing/overrides", json={**BODY, "instanceUuid": None, "vm": "imported-01", "osVersion": "x"}).status_code == 200
    rows = _rows(clean)
    assert [(r.instance_uuid, r.vm, r.os_version) for r in rows] == [("u-ovr-1", "app-01-renamed", "RHEL 9"), (None, "imported-01", "x")]
    clean.add(VmOsOverride(vcenter_key="vc-ovr", instance_uuid="u-ovr-1", vm="other", category="linux"))
    with pytest.raises(IntegrityError):
        clean.commit()
    clean.rollback()


def test_concurrent_insert_is_retried_as_update(admin, clean, monkeypatch):
    """The other request inserted after our SELECT: our INSERT hits the unique index, the retry updates."""
    assert admin.put("/api/licensing/overrides", json=BODY).status_code == 200
    original, calls = licensing._same_override, []

    def blind_first(*a):
        calls.append(1)
        q = original(*a)
        return q.filter(false()) if len(calls) == 1 else q

    monkeypatch.setattr(licensing, "_same_override", blind_first)
    r = admin.put("/api/licensing/overrides", json={**BODY, "category": "windows_server", "osVersion": "2019"})
    assert r.status_code == 200 and len(calls) == 2
    assert [(x.category, x.os_version) for x in _rows(clean)] == [("windows_server", "2019")]


def test_legacy_duplicates_newest_wins_and_index_waits_for_cleanup(admin, clean):
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS uq_vm_os_override_uuid"))
    t = dt.datetime(2026, 1, 1)
    clean.add_all([VmOsOverride(vcenter_key="vc-ovr", instance_uuid="u-ovr-1", vm="app-01", category="linux", updated_at=t),
                   VmOsOverride(vcenter_key="vc-ovr", instance_uuid="u-ovr-1", vm="app-01", category="windows_server",
                                updated_at=t + dt.timedelta(days=1))])
    clean.commit()
    ensure_schema()                                     # duplicates present: index not created, start-up not blocked
    assert engine.dialect.name != "sqlite" or clean.execute(text(
        "SELECT 1 FROM sqlite_master WHERE name = 'uq_vm_os_override_uuid'")).first() is None
    assert _overrides_map(clean)["vc-ovr|uuid|u-ovr-1"]["category"] == "windows_server"     # newest
    newest = max(_rows(clean), key=lambda r: r.updated_at)
    assert admin.delete(f"/api/licensing/overrides/{newest.id}").status_code == 200
    clean.expire_all()
    assert _rows(clean) == []                           # the older duplicate does not resurface
    ensure_schema()
    assert engine.dialect.name != "sqlite" or clean.execute(text(
        "SELECT 1 FROM sqlite_master WHERE name = 'uq_vm_os_override_uuid'")).first() is not None
