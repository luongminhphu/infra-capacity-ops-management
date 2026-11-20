"""Demo Dell OpenManage Enterprise (DEMO mode, rule 2): one PowerEdge per Dell host of every demo VMware vCenter
(same service tag as vcenter/demo_generator.demo_service_tag, so the CI registry joins them by serial), two
bare-metal servers, one server with a failed power supply and one with a fan warning. Same shape as ome.collect.

Derived from the configured demo vCenters and the demo cluster plan — not from the database — so the result
does not depend on which source the scheduler happens to sync first."""
import hashlib
from typing import Any, Dict, List

from ..vcenter.demo_generator import CLUSTER_PLAN, demo_service_tag
from . import HardwareConfig
from .ome import parse_device, parse_subsystems

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


def collect(cfg: HardwareConfig) -> Dict[str, Any]:
    raws: List[Dict[str, Any]] = []
    faults: Dict[str, Dict[str, Any]] = {}
    for vi, vc in enumerate(_demo_vcenters()):
        for ci, (cluster, host_count, *_rest) in enumerate(CLUSTER_PLAN):
            vendor, model = _rest[3]
            if not str(vendor).startswith("Dell"):
                continue                                # HPE hosts are not managed by OME
            for hi in range(host_count):
                host = f"{vc}-{cluster}-esx{hi + 1:02d}"
                tag = demo_service_tag(host)
                faulty = cluster == PSU_FAULT_CLUSTER and hi == 0
                raws.append(_raw(tag, f"idrac-{tag.lower()}", model, f"10.30.{10 + vi * 4 + ci}.{11 + hi}", 4000 if faulty else 1000))
                if faulty:
                    faults[tag] = _psu_fault()
    for i, (name, model) in enumerate(BARE_METAL):
        tag = demo_service_tag(f"{cfg.key}/bare-metal/{name}")
        warn = i == 1
        raws.append(_raw(tag, name, model, f"10.30.90.{11 + i}", 3000 if warn else 1000))
        if warn:
            faults[tag] = _fan_warning()
    devices = [d for d in (parse_device(r) for r in raws) if d]
    health = []
    for d in devices:
        if d["serviceTag"] in faults:
            health.extend({**s, "deviceId": d["deviceId"]} for s in parse_subsystems(faults[d["serviceTag"]]))
    return {"devices": devices, "health": health}
