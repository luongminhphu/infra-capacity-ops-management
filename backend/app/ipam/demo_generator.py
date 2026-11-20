"""Demo GestióIP (DEMO mode, rule 2): IPAM entries built from what the other demo sources report, answered in the same
JSON shape as the GestióIP API and parsed by the real parsers:

  * one host entry per VM IP of the configured demo vCenters (latest inventory in the database), hostname = VM name;
  * one entry per iDRAC IP of the configured demo OME sources, custom column "SN" = service tag;
  * deliberate gaps for the reconciliation rules: every 15th VM IP missing from the IPAM, one hostname that does not match,
    one iDRAC entry with a wrong SN, two IPAM entries no source reports (decommissioned servers).
Networks: one /16 per second octet seen (category server) + 10.30.0.0/16 (OOB iDRAC); VLANs 10, 30, 100."""
from typing import Any, Dict, List

from . import IpamConfig
from .gestioip import network_of, parse_host, parse_network, parse_vlan


def _vm_ips() -> List[Dict[str, Any]]:
    try:
        from .. import data_access
        from ..config import get_settings
        from ..database import SessionLocal
        keys = [c.key for c in get_settings().vcenters() if c.demo]
        if not keys:
            return []
        with SessionLocal() as db:
            _hosts, vms, _ = data_access.get_latest_combined(db, keys, include_import=False)
        return sorted(({"vm": v["vm"], "ip": v.get("ipAddress"), "os": v.get("guestOs") or v.get("osFamily")}
                       for v in vms if v.get("ipAddress")), key=lambda x: x["ip"])
    except Exception:  # noqa: BLE001 — inventory not ready: IPAM demo without VMs
        return []


def _idracs() -> List[Dict[str, Any]]:
    try:
        from ..config import get_settings
        from ..hardware import HardwareConfig
        from ..hardware.demo_generator import _specs
        out = []
        for c in get_settings().hardware():
            if c.demo:
                out += [{"ip": s["ip"], "tag": s["tag"], "name": s["name"]} for s in _specs(HardwareConfig(c.key, c.name, c.host, demo=True))]
        return out
    except Exception:  # noqa: BLE001
        return []


def collect(cfg: IpamConfig) -> Dict[str, Any]:
    raw_hosts, seconds = [], set()
    for i, v in enumerate(_vm_ips()):
        seconds.add(v["ip"].split(".")[1])
        if i % 15 == 14:
            continue                                            # IP in use, not declared in the IPAM
        name = f"old-{v['vm']}" if i == 3 else v["vm"]          # declared under another name
        raw_hosts.append({"id": str(1000 + i), "IP": v["ip"], "hostname": name.lower(), "descr": "", "site": "DC1",
                          "cat": "server", "comment": "", "alive": "up", "last_response": "", "ip_version": "v4",
                          "customColumns": {"SN": "", "OS": str(v.get("os") or ""), "owner": ""}})
    for j, d in enumerate(_idracs()):
        raw_hosts.append({"id": str(5000 + j), "IP": d["ip"], "hostname": d["name"], "descr": "iDRAC", "site": "DC1",
                          "cat": "oob", "comment": "", "alive": "up", "last_response": "", "ip_version": "v4",
                          "customColumns": {"SN": "WRONG01" if j == 0 else d["tag"], "OS": "", "owner": "team hạ tầng"}})
    for k, (ip, name) in enumerate((("10.10.250.10", "decom-app-01"), ("10.30.250.11", "idrac-decom-01"))):
        raw_hosts.append({"id": str(9000 + k), "IP": ip, "hostname": name, "descr": "Đã thanh lý?", "site": "DC1",
                          "cat": "server", "comment": "chưa xoá khỏi IPAM", "alive": "down", "last_response": "01/01/2025 08:00:00",
                          "ip_version": "v4", "customColumns": {"SN": "", "OS": "", "owner": ""}})
    seconds |= {"10"}
    raw_nets = [{"id": str(100 + int(s)), "IP": f"10.{s}.0.0", "BM": "16", "descr": f"Server 10.{s}", "site": "DC1", "cat": "server",
                 "comment": "", "rootnet": "", "customColumns": {"vlan": "100", "usage": ""}} for s in sorted(seconds, key=int) if s != "30"]
    raw_nets.append({"id": "130", "IP": "10.30.0.0", "BM": "16", "descr": "OOB iDRAC", "site": "DC1", "cat": "oob", "comment": "",
                     "rootnet": "", "customColumns": {"vlan": "30", "usage": ""}})
    nets = [n for n in (parse_network(x) for x in raw_nets) if n]
    hosts = [h for h in (parse_host(x, cfg.serial_column or "SN", cfg.mac_column) for x in raw_hosts) if h]
    for h in hosts:
        h["network"] = network_of(h["ip"], nets)
    vlans = [v for v in (parse_vlan(x) for x in ("1,10,mgmt,Quản trị,,white,black", "2,30,oob,iDRAC,,white,black",
                                                  "3,100,server,Máy chủ,,white,black")) if v]
    return {"networks": nets, "hosts": hosts, "vlans": vlans}
