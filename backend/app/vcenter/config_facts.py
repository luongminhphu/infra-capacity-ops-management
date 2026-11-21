"""Configuration facts of hosts, clusters and VMs for the baseline (ICO v2 G9a) — read in the inventory session, read-only.

PropertyCollector reads only, in batches (retrieve_refs); the one per-host call is HostAdvancedOptionManager.QueryOptions
("Syslog.global.logHost"), a read. A fact that cannot be read is left out (the rule is then "not applicable", never a deviation).
Row shapes (demo_generator.collect_config returns the same):
  hosts:    {host, cluster, facts: {sshRunning, sshPolicy, shellRunning, shellPolicy, lockdown, ntpServers[], ntpRunning, syslogHost,
             build, version}}
  clusters: {cluster, facts: {ha, admissionControl, drs, drsBehavior}}
  vms:      {vm, cluster, facts: {poweredOn, toolsStatus, hwVersion (int), isos[]}}
"""
import logging
import re
from typing import Any, Dict, List, Tuple

from pyVmomi import vim

from .extras import _moid, _sa, retrieve_refs

logger = logging.getLogger(__name__)

HOST_PATHS = ["config.service.service", "config.lockdownMode", "config.dateTimeInfo.ntpConfig.server", "configManager.advancedOption"]
VM_DEVICE_PATH = "config.hardware.device"
SERVICES = {"TSM-SSH": "ssh", "TSM": "shell"}
_VMX = re.compile(r"vmx-(\d+)")


def _syslog(opt_mgr) -> Any:
    """Syslog.global.logHost of one host ('' = not set); None when it cannot be read."""
    try:
        vals = opt_mgr.QueryOptions("Syslog.global.logHost") if opt_mgr is not None else None
    except Exception:  # noqa: BLE001 — not readable with this account / old host: rule not applicable
        return None
    return str(_sa(vals[0], "value") or "") if vals else ""


def _host_facts(content, hosts, host_info) -> List[Dict[str, Any]]:
    props = retrieve_refs(content, [o for o, _ in hosts.values()], vim.HostSystem, HOST_PATHS)
    out = []
    for moid, (_, base) in hosts.items():
        p = props.get(moid, {})
        f: Dict[str, Any] = {}
        services = p.get("config.service.service")
        if services is not None:
            for s in services:
                name = SERVICES.get(_sa(s, "key"))
                if name:
                    f[f"{name}Running"], f[f"{name}Policy"] = bool(_sa(s, "running")), _sa(s, "policy")
            ntpd = next((s for s in services if _sa(s, "key") == "ntpd"), None)
            if ntpd is not None:
                f["ntpRunning"] = bool(_sa(ntpd, "running"))
        if "config.lockdownMode" in p:
            f["lockdown"] = str(p["config.lockdownMode"]) if p["config.lockdownMode"] is not None else None
        if "config.dateTimeInfo.ntpConfig.server" in p:
            f["ntpServers"] = [str(x) for x in (p["config.dateTimeInfo.ntpConfig.server"] or [])]
        f["syslogHost"] = _syslog(p.get("configManager.advancedOption"))
        product = _sa(base.get("summary"), "config", "product")
        f["build"], f["version"] = _sa(product, "build"), _sa(product, "version")
        name, cluster = host_info.get(moid, (base.get("name") or moid, "UNKNOWN"))
        out.append({"host": name, "cluster": cluster, "facts": {k: v for k, v in f.items() if v is not None or k == "lockdown"}})
    return out


def _cluster_facts(compute) -> List[Dict[str, Any]]:
    out = []
    for _, (obj, props) in compute.items():
        if not isinstance(obj, vim.ClusterComputeResource):
            continue                   # a standalone host has no HA / DRS to check
        cfg = props.get("configurationEx")
        das, drs = _sa(cfg, "dasConfig"), _sa(cfg, "drsConfig")
        out.append({"cluster": props.get("name") or _moid(obj),
                    "facts": {"ha": bool(_sa(das, "enabled")), "admissionControl": bool(_sa(das, "admissionControlEnabled")),
                              "drs": bool(_sa(drs, "enabled")), "drsBehavior": str(_sa(drs, "defaultVmBehavior") or "") or None}})
    return out


def _vm_facts(content, vms, host_info) -> List[Dict[str, Any]]:
    on = {moid: obj for moid, (obj, props) in vms.items()
          if str(_sa(props.get("summary"), "runtime", "powerState") or "") == "poweredOn"}
    devices: Dict[str, Dict[str, Any]] = {}
    try:
        devices = retrieve_refs(content, list(on.values()), vim.VirtualMachine, [VM_DEVICE_PATH])
    except Exception:  # noqa: BLE001 — ISO facts are optional
        logger.warning("Could not read VM devices for the ISO check", exc_info=True)
    out = []
    for moid, (_, props) in vms.items():
        s = props.get("summary")
        f: Dict[str, Any] = {"poweredOn": moid in on, "toolsStatus": _sa(s, "guest", "toolsVersionStatus2")}
        m = _VMX.match(str(_sa(s, "config", "hwVersion") or ""))
        if m:
            f["hwVersion"] = int(m.group(1))
        if moid in devices:
            f["isos"] = [str(_sa(d, "backing", "fileName") or "ISO")[:200] for d in devices[moid].get(VM_DEVICE_PATH) or []
                         if isinstance(d, vim.vm.device.VirtualCdrom) and isinstance(_sa(d, "backing"), vim.vm.device.VirtualCdrom.IsoBackingInfo)
                         and _sa(d, "connectable", "connected")]
        _host, cluster = host_info.get(_moid(_sa(s, "runtime", "host")), (None, "UNKNOWN"))
        out.append({"vm": _sa(s, "config", "name") or props.get("name") or moid, "cluster": cluster,
                    "facts": {k: v for k, v in f.items() if v is not None}})
    return out


def read(content, hosts, compute, vms, host_info: Dict[str, Tuple[str, str]]) -> Dict[str, List[Dict[str, Any]]]:
    return {"hosts": _host_facts(content, hosts, host_info), "clusters": _cluster_facts(compute),
            "vms": _vm_facts(content, vms, host_info)}
