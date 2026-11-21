"""ICO v2 G9b: End of General Support levels, VMSA build matching (below / equal / above, version lines), certificate levels,
certificate parsing, import validation, demo seed, budget plan suggestion, inbox items."""
import datetime as dt
import json
from types import SimpleNamespace as NS

import pytest

from app.lifecycle import rules as R, service, tls
from test_cmdb import db  # noqa: F401  (fixture: private SQLite)

TODAY = dt.date(2026, 11, 21)
NOW = dt.datetime(2026, 11, 21, 3, 0)
EOGS = [{"product": "esxi", "version": "7.0", "eogs": dt.date(2025, 10, 2), "eotg": None},
        {"product": "esxi", "version": "8.0", "eogs": dt.date(2027, 4, 1), "eotg": None},
        {"product": "esxi", "version": "8.0.3", "eogs": dt.date(2028, 1, 1), "eotg": None},     # longer prefix wins
        {"product": "vcenter", "version": "8.0", "eogs": dt.date(2027, 11, 1), "eotg": None}]
VMSA = [{"vmsa": "VMSA-2025-0004", "title": "x", "cvss": 9.3, "product": "esxi", "version": "8.0", "fixedBuild": 24585383},
        {"vmsa": "VMSA-2025-0004", "title": "x", "cvss": 9.3, "product": "esxi", "version": "7.0", "fixedBuild": 24585291},
        {"vmsa": "VMSA-2024-0011", "title": "y", "cvss": 7.1, "product": "esxi", "version": "8.0", "fixedBuild": 23825572}]


def _p(name, version, build, product="esxi"):
    return {"product": product, "name": name, "vcenterKey": "vc1", "cluster": "PRD", "version": version, "build": build}


def test_version_lines_and_levels():
    assert R.in_line("8.0.2", "8.0") and R.in_line("8.0", "8.0") and not R.in_line("8.01", "8.0") and not R.in_line(None, "8.0")
    assert [R.eogs_level(d, TODAY) for d in (dt.date(2026, 11, 20), dt.date(2027, 5, 20), dt.date(2027, 5, 21), dt.date(2027, 11, 21),
                                             dt.date(2027, 11, 22))] == ["crit", "bad", "warn", "warn", "ok"]       # 180 / 181 / 365 / 366 days
    assert [R.vmsa_level(c) for c in (9.8, 9.0, 8.9, 7.0, 6.9)] == ["crit", "crit", "bad", "bad", "warn"]
    assert [R.cert_level(NOW + dt.timedelta(days=d), NOW) for d in (-1, 30, 31, 60, 61, 90, 91)] == ["crit", "crit", "bad", "bad", "warn", "warn", "ok"]
    assert R.cert_level(None, NOW) == "na"


def test_evaluate_eogs_vmsa_and_certs():
    products = [_p("esx70", "7.0.3", "21930508"), _p("esx80", "8.0.2", "22380479"), _p("esx803", "8.0.3", "24585383"),
                _p("esx-unknown", "6.7.0", "17700523"), _p("vc1", "8.0.3", "24022515", "vcenter"), _p("odd", "8.0.2", "n/a")]
    certs = [{"target": "esxi:a:443", "notAfter": NOW + dt.timedelta(days=10)}, {"target": "x:b:1556", "notAfter": None, "error": "refused"}]
    res = R.evaluate(products, EOGS, VMSA, certs, TODAY, NOW)
    life = {r["name"]: (r["line"], r["level"], r["daysLeft"]) for r in res["lifecycle"]}
    assert life == {"esx70": ("7.0", "crit", -415), "esx80": ("8.0", "bad", 131), "esx803": ("8.0.3", "ok", 406),
                    "esx-unknown": (None, "na", None), "vc1": ("8.0", "warn", 345), "odd": ("8.0", "bad", 131)}
    assert sorted((r["name"], r["vmsa"]) for r in res["vmsa"]) == [
        ("esx70", "VMSA-2025-0004"), ("esx80", "VMSA-2024-0011"), ("esx80", "VMSA-2025-0004")]   # equal build = patched; no build = skip
    assert res["vmsa"][0]["level"] == "crit"
    assert [(c["target"], c["level"], c["daysLeft"]) for c in res["certs"]] == [("esxi:a:443", "crit", 10), ("x:b:1556", "na", None)]


def _self_signed(cn: str, days: int) -> bytes:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(7)
            .not_valid_before(now).not_valid_after(now + dt.timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(cn)]), critical=False).sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.DER)


def test_certificate_parse_and_unreachable_target():
    c = tls.parse(_self_signed("esx01.corp.local", 365))
    assert (c["notAfter"], c["subject"], c["san"], c["selfSigned"]) == (dt.datetime(2027, 1, 1), "CN=esx01.corp.local", ["esx01.corp.local"], True)
    assert tls.read("127.0.0.1", 1, timeout=0.5)["error"].startswith("Không đọc được chứng chỉ")


def test_targets_from_sources():
    st = NS(vcenters=lambda: [NS(key="vc1", name="VC 1", host="vc1.corp", port=443, demo=False, platform="vmware"),
                              NS(key="px", name="PVE", host="pve.corp", port=8006, demo=False, platform="proxmox")],
            netbackups=lambda: [NS(key="nbu", name="NBU", host="nbu.corp", port=443, demo=False)],
            hardware=lambda: [NS(key="ome", name="OME", host="ome.corp", port=443, demo=True)], storages=lambda: [], ipam=lambda: [],
            itsm=lambda: [], public_url="https://ico.corp.vn")
    hosts = [{"host": "esx01", "vcenterKey": "vc1"}, {"host": "pve01", "vcenterKey": "px"}]
    t = {x["target"]: x for x in tls.targets(st, hosts)}
    assert sorted(t) == ["app:ico.corp.vn:443", "esxi:esx01:443", "netbackup:nbu.corp:1556", "ome:ome.corp:443",
                         "vcenter:pve.corp:8006", "vcenter:vc1.corp:443"]          # Proxmox hosts are not ESXi
    assert t["ome:ome.corp:443"]["demo"] and t["esxi:esx01:443"]["vcenterKey"] == "vc1"


def test_import_validation():
    good = {"product": "ESXi", "version": "8.0", "eogs": "11/10/2027"}
    assert service.clean_eogs(good)["eogs"] == dt.date(2027, 10, 11)
    for bad in ({**good, "product": "Hyper-V"}, {**good, "version": "8.x"}, {**good, "eogs": ""}, {**good, "eogs": "2027-13-01"}):
        with pytest.raises(ValueError):
            service.clean_eogs(bad)
    v = service.clean_vmsa({"vmsa": "vmsa-2025-0004", "cvss": "9,3", "product": "vcsa", "version": "7.0", "fixedBuild": "24322018"})
    assert (v["vmsa"], v["cvss"], v["product"], v["fixedBuild"]) == ("VMSA-2025-0004", 9.3, "vcenter", 24322018)
    for bad in ({"vmsa": "X", "cvss": 11, "product": "esxi", "version": "8.0", "fixedBuild": 1},
                {"vmsa": "X", "cvss": 5, "product": "esxi", "version": "8.0", "fixedBuild": "abc"}):
        with pytest.raises(ValueError):
            service.clean_vmsa(bad)
    rows = service.parse_sheet(service.template("vmsa"), "vmsa")             # the template is importable as is
    assert [(r["vmsa"], r["version"], r["fixedBuild"]) for r in rows] == [("VMSA-2025-0004", "8.0", 24585383), ("VMSA-2025-0004", "7.0", 24585291)]
    assert len(service.parse_sheet(service.template("eogs"), "eogs")) == 3


def test_db_report_demo_seed_budget_and_inbox(db, monkeypatch):  # noqa: F811
    from app import inbox
    from app.models import HostSnapshot, SnapshotRun
    from app.spend.models import SpendCategory
    from app.config import get_settings
    monkeypatch.setattr(type(get_settings()), "vcenters", lambda _self: [NS(key="vc1", platform="vmware")])
    run = SnapshotRun(vcenter_key="vc1", source="live", status="ok", started_at=NOW, finished_at=NOW)
    db.add(run)
    db.flush()
    db.add_all([HostSnapshot(run_id=run.id, vcenter_key="vc1", cluster="PRD", host="esx70", cores=32, esxi_version="7.0.3", esxi_build="21930508"),
                HostSnapshot(run_id=run.id, vcenter_key="vc1", cluster="PRD", host="esx80", cores=32, esxi_version="8.0.2", esxi_build="22380479"),
                SpendCategory(code="LIC_VMWARE", name="License VMware", grp="license", active=True)])
    service.store_product(db, "vc1", {"product": {"version": "7.0.3", "build": "21477706", "fullName": "vCenter 7"}})
    service.seed_demo(db)
    service.seed_demo(db)                                    # only once: tables not empty
    db.commit()
    rep = service.report(db, TODAY, NOW)
    assert {(r["name"], r["level"]) for r in rep["lifecycle"]} == {("esx70", "crit"), ("vc1", "crit"), ("esx80", "warn")}   # 8.0 ends 11/10/2027: 324 days
    assert sorted((r["name"], r["vmsa"]) for r in rep["vmsa"]) == [("esx70", "VMSA-2025-0004"), ("esx80", "VMSA-2024-0011"),
                                                                    ("esx80", "VMSA-2025-0004"), ("vc1", "VMSA-2024-0019")]
    assert len(rep["eogsTable"]) == 4 and rep["summary"]["vmsaHosts"] == 3
    [s] = service.plan_suggestions(db, 2026, dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    assert (s["source"], s["sourceId"], s["kind"], s["startDate"], s["sourceCode"]) == ("lifecycle", 2026, "capex", dt.date(2026, 1, 1), "2 đối tượng")
    assert "1 host ESXi, 1 vCenter" in s["detail"]
    assert service.plan_suggestions(db, 2024, dt.date(2024, 1, 1), dt.date(2024, 12, 31)) == []
    items = inbox.lifecycle_items(db)
    titles = sorted(i["title"] for i in items)
    assert any(t.startswith("VMSA-2024-0019 (CVSS 9.8)") for t in titles) and any("ESXi 7.0 đã hết hỗ trợ" in t for t in titles)
    assert json.dumps(items, default=str)
