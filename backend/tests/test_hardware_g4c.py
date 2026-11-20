"""ICO v2 G4c: Phần cứng page API (list / filters / detail / owner scope), physical server card OME block, Ctrl + K by
service tag, PDF / Excel numbers equal to the page, iDRAC link only for a valid IP."""
import io
import json

import pytest

from app.database import SessionLocal
from app.hardware import service
from app.hardware.models import (
    HwAlert, HwComponentHealth, HwDeviceInventory, HwDeviceState, HwDiskState, HwFirmwareState, HwPartState, HwSourceState,
    HwWarranty,
)
from app.models import DataSource
from app.vcenter.demo_generator import demo_service_tag
from test_spend_opex import _login, admin, app_  # noqa: F401  (fixtures)

HW_MODELS = (HwAlert, HwComponentHealth, HwDeviceInventory, HwDiskState, HwFirmwareState, HwPartState, HwWarranty,
             HwDeviceState, HwSourceState)


@pytest.mark.parametrize("ip,url", [
    ("10.30.10.11", "https://10.30.10.11"), (" 10.0.0.1 ", "https://10.0.0.1"), ("fe80::1", "https://[fe80::1]"),
    ("idrac.corp.local", None), ("javascript:alert(1)", None), ("10.0.0.1/admin", None), ("", None), (None, None),
    ("10.0.0.1\" onmouseover=\"x", None)])
def test_idrac_link_only_for_a_valid_ip(ip, url):
    assert service.idrac_url(ip) == url


@pytest.fixture()
def demo(admin):
    """Demo vCenter vcd + demo OME, synced, CI registry built."""
    from app import sources
    from app.cmdb import builder
    from app.collectors import scheduler
    from app.config import get_settings
    s = SessionLocal()
    for m in HW_MODELS:
        s.query(m).delete()
    s.query(DataSource).filter(DataSource.kind.in_(("ome", "vcenter"))).delete(synchronize_session=False)
    s.commit()
    a = admin
    assert a.post("/api/admin/sources", json={"kind": "vcenter", "key": "vcd", "name": "VC demo", "host": "", "demo": True}).status_code == 200
    assert a.post("/api/admin/sources", json={"kind": "ome", "key": "ome-demo", "name": "OME demo", "host": "", "demo": True}).status_code == 200
    sources.invalidate_cache()
    st = get_settings()
    scheduler.sync_vcenter(s, st.vcenters()[0], st)
    scheduler.sync_hardware_sources(st)
    builder.build_all(s)
    yield s
    for m in HW_MODELS:
        s.query(m).delete()
    s.query(DataSource).filter(DataSource.kind.in_(("ome", "vcenter"))).delete(synchronize_session=False)
    s.commit()
    sources.invalidate_cache()
    s.close()


def test_list_summary_levels_and_filters(demo, admin):
    d = admin.get("/api/hardware").json()
    s = d["summary"]
    assert d["configured"] is True and s["servers"] == 12 and s["inScope"] == 12
    # PSU fault (crit), predictive disk (crit), BIOS behind baseline (warn), fan warning bare-metal (bad)
    assert s["byLevel"] == {"crit": 2, "bad": 1, "warn": 1, "ok": 8}
    assert (s["disksPredictive"], s["firmwareBehind"]) == (1, 1)
    assert (s["warrantyExpired"], s["warrantyExpiring90"], s["noOpex"]) == (3, 2, 9)
    assert [i["level"] for i in d["items"]][:4] == ["crit", "crit", "bad", "warn"]          # worst first
    pred = next(i for i in d["items"] if i["disksPredictive"])
    assert pred["level"] == "crit" and "1 ổ đĩa dự báo hỏng" in pred["reasons"] and pred["idracUrl"].startswith("https://10.30.")
    assert pred["hosts"][0]["name"] == "vcd-PROD-CLUSTER-A-esx03" and pred["vmCount"] >= 0
    bare = next(i for i in d["items"] if i["name"] == "db-oracle-01")
    assert bare["hosts"] == [] and bare["level"] == "ok"
    crit = admin.get("/api/hardware?status=crit").json()
    assert crit["summary"]["servers"] == 2 and crit["summary"]["inScope"] == 12
    r740 = admin.get("/api/hardware?model=PowerEdge%20R740").json()
    assert r740["summary"]["servers"] == 4 and all(i["model"] == "PowerEdge R740" for i in r740["items"])
    soon = admin.get("/api/hardware?warrantyDays=60").json()        # 2 expired hosts + 2 ending in 45 days + backup-media-01
    assert soon["summary"]["servers"] == 5
    assert admin.get("/api/hardware?status=red").status_code == 422


def test_detail_has_parts_firmware_warranty_alerts_and_vms(demo, admin):
    items = admin.get("/api/hardware").json()["items"]
    psu = next(i for i in items if any("Bộ nguồn" in r for r in i["reasons"]))
    d = admin.get(f"/api/hardware/servers/{psu['sourceKey']}/{psu['deviceId']}").json()
    assert d["level"] == "crit" and len(d["disks"]) == 4 and {p["kind"] for p in d["parts"]} == {"raid", "psu", "memory"}
    assert any(p["kind"] == "psu" and p["status"] == "crit" for p in d["parts"])
    assert any(x["subsystem"] == "PowerSupply" and x["status"] == "crit" for x in d["subsystems"])
    assert any(a["category"] == "Power Supply" and a["severity"] == "crit" for a in d["alerts"])
    assert d["warranties"] and d["warranties"][0]["end"] == d["warranty"]["dellEnd"]
    assert d["hosts"][0]["name"] == "vcd-PROD-CLUSTER-B-esx01" and d["vmCount"] == len(d["vms"])
    assert d["macs"] and d["osHostname"] == "vcd-prod-cluster-b-esx01"
    bios = next(i for i in items if i["firmware"]["behind"])
    fw = admin.get(f"/api/hardware/servers/{bios['sourceKey']}/{bios['deviceId']}").json()["firmwareRows"]
    assert [(f["component"], f["compliance"]) for f in fw if f["compliance"] == "upgrade"] == [("BIOS", "upgrade")]
    assert admin.get("/api/hardware/servers/ome-demo/1").status_code == 404
    assert admin.get(f"/api/hardware/servers/not-configured/{psu['deviceId']}").status_code == 404


def test_owner_scope_keeps_servers_running_the_systems_vms(demo, admin):
    from app.cmdb.models import Ci, CiLink
    host = demo.query(Ci).filter_by(type="hypervisor_host", source_key="vcd:vcd-PROD-CLUSTER-A-esx02").one()
    vms = [i for (i,) in demo.query(CiLink.from_ci).filter_by(to_ci=host.id, relation="hosted_on")]
    assert vms, "demo host without VM"
    s = admin.post("/api/systems", json={"name": "Hệ thống G4c", "criticality": 2}).json()
    try:
        assert admin.post(f"/api/systems/{s['id']}/members", json={"ciIds": vms, "move": True}).status_code == 200
        from app.owners.service import invalidate_scope_cache
        invalidate_scope_cache()
        d = admin.get(f"/api/hardware?systems={s['id']}").json()
        assert [i["serviceTag"] for i in d["items"]] == [demo_service_tag("vcd-PROD-CLUSTER-A-esx02")]
        assert d["items"][0]["systems"][0]["code"] == s["code"] and d["ownerScope"].startswith("Đang lọc theo owner")
    finally:
        admin.delete(f"/api/systems/{s['id']}")


def test_server_card_ome_block_and_search_by_service_tag(demo, admin):
    tag = demo_service_tag("vcd-PROD-CLUSTER-A-esx03")
    card = admin.get(f"/api/entities/server/{tag.lower()}").json()
    assert card["serial"] == tag and card["ome"]["level"] == "crit" and card["ome"]["disksPredictive"] == 1
    assert card["ome"]["idracUrl"].startswith("https://") and card["ome"]["warranty"]["dellEnd"]
    bare_tag = next(i["serviceTag"] for i in admin.get("/api/hardware").json()["items"] if i["name"] == "backup-media-01")
    bare = admin.get(f"/api/entities/server/{bare_tag}").json()
    assert bare["serial"] == bare_tag and bare["name"] == bare_tag and bare["ome"]["name"] == "backup-media-01"
    hits = admin.get(f"/api/search?q={bare_tag[:5]}").json()["items"]
    hit = next(h for h in hits if h["type"] == "server" and h["label"] == bare_tag)
    assert "backup-media-01" in hit["sub"] and hit["entity"]["name"] == bare_tag


def test_pdf_and_excel_match_the_page(demo, admin):
    from openpyxl import load_workbook
    from app import exports
    from app.report_hardware import render_html
    page = admin.get("/api/hardware").json()
    s = page["summary"]
    html = render_html(page, "Tester")
    lv = s["byLevel"]
    assert (f"{s['servers']} máy chủ Dell: {lv['crit']} VƯỢT NGƯỠNG, {lv['bad']} CẢNH BÁO, {lv['warn']} THEO DÕI; "
            f"{s['disksPredictive']} ổ đĩa dự báo hỏng; {s['warrantyExpired']} máy hết bảo hành Dell.") in html
    for i in page["items"]:
        assert i["name"] in html
    r = admin.get("/api/hardware/report.pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    x = admin.get("/api/hardware/report.xlsx?status=crit")
    assert x.status_code == 200
    wb = load_workbook(io.BytesIO(x.content))
    assert wb.sheetnames == ["Máy chủ", "Bảo hành", "Firmware"]
    names = [c.value for c in wb["Máy chủ"]["A"] if c.value and c.row > 3]
    crit = [i["name"] for i in admin.get("/api/hardware?status=crit").json()["items"]]
    assert names[-len(crit):] == crit
    fw = [c.value for c in wb["Firmware"]["F"] if c.value]
    assert "Đúng baseline" in fw
    assert exports.find("/api/hardware/report.pdf")["archive"] is True and exports.find("/api/hardware/report.xlsx")


def test_html_is_escaped(demo, admin):
    from app.report_hardware import render_html
    page = admin.get("/api/hardware").json()
    page["items"][0]["name"] = "<script>alert(1)</script>"
    assert "<script>alert(1)</script>" not in render_html(page, "<b>x</b>")
