"""Demo Dell OpenManage Enterprise (DEMO mode, rule 2): one PowerEdge per Dell host of every demo VMware vCenter
(same service tag as vcenter/demo_generator.demo_service_tag, so the CI registry joins them by serial), two
bare-metal servers, one server with a failed power supply and one with a fan warning. Same shape as ome.collect.

Derived from the configured demo vCenters and the demo cluster plan — not from the database — so the result
does not depend on which source the scheduler happens to sync first."""
import datetime as dt
import hashlib
from typing import Any, Dict, List, Optional

from ..vcenter.demo_generator import CLUSTER_PLAN, demo_service_tag
from . import HardwareConfig
from .ome import (
    merge_compliance, parse_alert, parse_compliance, parse_device, parse_inventory, parse_subsystems, parse_warranty,
)

BARE_METAL = (("db-oracle-01", "PowerEdge R760"), ("backup-media-01", "PowerEdge R660xs"))
PSU_FAULT_CLUSTER = "PROD-CLUSTER-B"          # first host of this cluster: PSU 1 failed (critical)


def _demo_vcenters() -> List[str]:
    try:
        from ..config import get_settings
        return sorted(c.key for c in get_settings().vcenters() if c.demo and getattr(c, "platform", "vmware") == "vmware")
    except Exception:  # noqa: BLE001 — settings / sources not ready: bare-metal only
        return []


def _device_id(tag: str) -> int:
    return 10000 + int(hashlib.sha1(tag.encode()).hexdigest()[:6], 16) % 900000


def _raw(tag: str, name: str, model: str, ip: str, status: int, power: int = 17) -> Dict[str, Any]:
    """A device as /api/DeviceService/Devices returns it (then parsed like a real answer)."""
    return {"Id": _device_id(tag), "Type": 1000, "Identifier": tag, "DeviceServiceTag": tag, "Model": model,
            "DeviceName": name, "PowerState": power, "Status": status, "ConnectionState": True,
            "LastInventoryTime": "2026-01-01 02:00:00.000",
            "DeviceManagement": [{"ManagementId": 1, "NetworkAddress": ip, "ManagementType": 2,
                                  "InstrumentationName": f"idrac-{tag}"}]}


def _psu_fault() -> Dict[str, Any]:
    return {"value": [
        {"SubSystem": "PowerSupply", "RollupStatus": "4000", "FaultList": [
            {"Component": "PSU.Slot.1", "Message": "Power supply 1 is not receiving input power.", "Severity": "4000", "MessageId": "PSU0003"}]},
        {"SubSystem": "Fan", "RollupStatus": "1000", "FaultList": []},
        {"SubSystem": "Memory", "RollupStatus": "1000", "FaultList": []}]}


def _fan_warning() -> Dict[str, Any]:
    return {"value": [
        {"SubSystem": "Fan", "RollupStatus": "3000", "FaultList": [
            {"Component": "Fan.Embedded.4", "Message": "Fan 4 RPM is less than the lower warning threshold.", "Severity": "3000", "MessageId": "FAN0001"}]},
        {"SubSystem": "PowerSupply", "RollupStatus": "1000", "FaultList": []}]}


def _inventory(spec: Dict[str, Any]) -> Dict[str, Any]:
    """InventoryDetails payload of one demo server (parsed like a real answer)."""
    tag, i = spec["tag"], spec["n"]
    disks = [{"DiskNumber": f"Disk {k} in Backplane 1 of RAID Controller in Slot 1", "ModelNumber": "MZ7KH960HAJR0D3",
              "SerialNumber": f"S47N{tag}{k}", "MediaType": "SSD", "Size": "893.75 GB", "Status": 1000,
              "PredictiveFailureState": "Smart Alert Present" if (spec["predictive"] and k == 1) else "Smart Alert Absent"}
             for k in range(4)]
    psus = [{"Name": f"PS{k + 1} Status", "Model": "PWR SPLY,1400W,RDNT,LTON", "SerialNumber": f"CNLOD{tag}{k}", "OutputWatts": 1400,
             "Status": 4000 if (spec["psuFault"] and k == 0) else 1000} for k in range(2)]
    mems = [{"Name": f"DIMM.Socket.A{k + 1}", "PartNumber": "M393A4K40EB3-CWE", "SerialNumber": f"{tag}M{k}", "Size": "32768 MB",
             "Status": 1000} for k in range(4)]
    mac = lambda p: "B0:7B:25:{:02X}:{:02X}:{:02X}".format(i % 256, (i * 7) % 256, p)  # noqa: E731
    return {"value": [
        {"InventoryType": "serverArrayDisks", "InventoryInfo": disks},
        {"InventoryType": "serverRaidControllers", "InventoryInfo": [{"Name": "PERC H755 Front", "Status": 1000, "FirmwareVersion": "52.16.1-4405"}]},
        {"InventoryType": "serverPowerSupplies", "InventoryInfo": psus},
        {"InventoryType": "serverMemoryDevices", "InventoryInfo": mems},
        {"InventoryType": "serverNetworkInterfaces", "InventoryInfo": [
            {"NicId": "NIC.Integrated.1", "Ports": [{"PortId": f"NIC.Integrated.1-{p}", "Partitions": [{"CurrentMacAddress": mac(p)}]}
                                                    for p in (1, 2)]}]},
        {"InventoryType": "serverOperatingSystems", "InventoryInfo": [{"OsName": spec["os"], "Hostname": spec["hostname"]}]},
        {"InventoryType": "deviceSoftware", "InventoryInfo": [
            {"SoftwareType": "FRMW", "DeviceDescription": "BIOS", "Version": "1.9.2" if spec["oldBios"] else "1.12.1"},
            {"SoftwareType": "FRMW", "DeviceDescription": "Integrated Dell Remote Access Controller", "Version": "7.00.00.171"},
            {"SoftwareType": "FRMW", "DeviceDescription": "PERC H755 Front", "Version": "52.16.1-4405"},
            {"SoftwareType": "APAC", "DeviceDescription": "OS Collector", "Version": "6.0"}]},
    ]}


def _compliance(spec: Dict[str, Any], device_id: int) -> Dict[str, Any]:
    comp = [{"Name": "BIOS", "CurrentVersion": "1.9.2" if spec["oldBios"] else "1.12.1", "Version": "1.12.1",
             "UpdateAction": "UPGRADE" if spec["oldBios"] else "EQUAL", "ComplianceStatus": "CRITICAL" if spec["oldBios"] else "OK"},
            {"Name": "Integrated Dell Remote Access Controller", "CurrentVersion": "7.00.00.171", "Version": "7.00.00.171",
             "UpdateAction": "EQUAL", "ComplianceStatus": "OK"}]
    return {"DeviceId": device_id, "ServiceTag": spec["tag"], "ComponentComplianceReports": comp}


def _warranty(spec: Dict[str, Any], today: dt.date) -> List[Dict[str, Any]]:
    """Base 3-year warranty from 3 years before its end; the end depends on the demo case of the server."""
    end = today + dt.timedelta(days=spec["warrantyDays"])
    start = end - dt.timedelta(days=3 * 365)
    return [{"DeviceIdentifier": spec["tag"], "DeviceType": 1000, "ServiceLevelDescription": "ProSupport Plus Mission Critical 4 Hour",
             "ServiceLevelEntitlementType": "EXTENDED", "StartDate": f"{start} 00:00:00.000", "EndDate": f"{end} 23:59:59.000"}]


def _alerts(specs: List[Dict[str, Any]], today: dt.date, device_ids: Dict[str, int]) -> List[Dict[str, Any]]:
    """Two hardware alerts a day (stable ids per day, so a re-read is a duplicate) + one audit alert that is filtered out."""
    base = int(today.strftime("%Y%m%d")) * 10
    out = []
    for s in specs:
        if s["psuFault"]:
            out.append({"Id": base + 1, "SeverityType": 16, "StatusName": "Not Acknowledged", "CategoryName": "System Health",
                        "SubCategoryName": "Power Supply", "AlertMessageId": "PSU0003", "AlertDeviceType": 1000,
                        "AlertDeviceId": device_ids[s["tag"]], "AlertDeviceIdentifier": s["tag"],
                        "Message": "Power supply 1 is not receiving input power.", "TimeStamp": f"{today} 01:00:00.000"})
            break
    for s in specs:
        if s["fanWarn"]:
            out.append({"Id": base + 2, "SeverityType": 8, "StatusName": "Not Acknowledged", "CategoryName": "System Health",
                        "SubCategoryName": "Fan", "AlertMessageId": "FAN0001", "AlertDeviceType": 1000,
                        "AlertDeviceId": device_ids[s["tag"]], "AlertDeviceIdentifier": s["tag"],
                        "Message": "Fan 4 RPM is less than the lower warning threshold.", "TimeStamp": f"{today} 02:00:00.000"})
    out.append({"Id": base + 3, "SeverityType": 2, "CategoryName": "Audit", "SubCategoryName": "Users",
                "Message": "Successfully logged in.", "TimeStamp": f"{today} 03:00:00.000"})
    return out


def _specs(cfg: HardwareConfig) -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = []
    for vi, vc in enumerate(_demo_vcenters()):
        for ci, (cluster, host_count, *_rest) in enumerate(CLUSTER_PLAN):
            vendor, model = _rest[3]
            if not str(vendor).startswith("Dell"):
                continue                                # HPE hosts are not managed by OME
            for hi in range(host_count):
                host = f"{vc}-{cluster}-esx{hi + 1:02d}"
                tag = demo_service_tag(host)
                # warranty: cluster A covered 1–2 years; cluster B: first two hosts expired, the rest ending within 60 days
                days = (300 + (int(hashlib.sha1(tag.encode()).hexdigest()[:4], 16) % 400) if cluster != PSU_FAULT_CLUSTER
                        else (-200 if hi < 2 else 45))
                specs.append({"tag": tag, "name": f"idrac-{tag.lower()}", "model": model, "ip": f"10.30.{10 + vi * 4 + ci}.{11 + hi}",
                              "status": 4000 if (cluster == PSU_FAULT_CLUSTER and hi == 0) else 1000,
                              "psuFault": cluster == PSU_FAULT_CLUSTER and hi == 0, "fanWarn": False,
                              "predictive": vi == 0 and cluster == "PROD-CLUSTER-A" and hi == 2,
                              "oldBios": vi == 0 and cluster == "PROD-CLUSTER-A" and hi == 1,
                              "os": "VMware ESXi 8.0.2", "hostname": host.lower(), "warrantyDays": days})
    for i, (name, model) in enumerate(BARE_METAL):
        tag = demo_service_tag(f"{cfg.key}/bare-metal/{name}")
        specs.append({"tag": tag, "name": name, "model": model, "ip": f"10.30.90.{11 + i}", "status": 3000 if i == 1 else 1000,
                      "psuFault": False, "fanWarn": i == 1, "predictive": False, "oldBios": False,
                      "os": "Red Hat Enterprise Linux 9.4" if i == 0 else "Microsoft Windows Server 2022",
                      "hostname": f"{name}.corp.local", "warrantyDays": 500 if i == 0 else -30})
    for n, sp in enumerate(specs):
        sp["n"] = n
    return specs


def collect(cfg: HardwareConfig, plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    plan = plan or {}
    specs = _specs(cfg)
    devices = [d for d in (parse_device(_raw(sp["tag"], sp["name"], sp["model"], sp["ip"], sp["status"])) for sp in specs) if d]
    ids = {d["serviceTag"]: d["deviceId"] for d in devices}
    health = []
    for sp in specs:
        fault = _psu_fault() if sp["psuFault"] else _fan_warning() if sp["fanWarn"] else None
        if fault:
            health.extend({**s, "deviceId": ids[sp["tag"]]} for s in parse_subsystems(fault))
    out: Dict[str, Any] = {"devices": devices, "health": health, "warnings": []}
    today = dt.datetime.utcnow().date()
    if plan.get("inventory"):
        inv = {ids[sp["tag"]]: parse_inventory(_inventory(sp)) for sp in specs}
        out["inventory"] = merge_compliance(inv, parse_compliance([_compliance(sp, ids[sp["tag"]]) for sp in specs]))
        out["baselineConfigured"] = True
    if plan.get("warranty"):
        out["warranties"] = [w for sp in specs for w in (parse_warranty(r) for r in _warranty(sp, today)) if w]
    since = plan.get("alertsSince")
    if since is not None:
        alerts = [a for a in (parse_alert(r) for r in _alerts(specs, today, ids)) if a]
        out["alerts"] = [a for a in alerts if dt.datetime.fromisoformat(a["at"]) >= since]
    return out
