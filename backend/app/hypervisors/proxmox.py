"""Proxmox VE (read-only REST API /api2/json, port 8006).

Authentication: an API token — header `Authorization: PVEAPIToken=<user>@<realm>!<token id>=<secret>`. The token
holds the built-in role **PVEAuditor** on path `/` (read-only); "Privilege Separation" may stay on. No ticket
login (POST) is ever made: every call is a GET.

Reads, all in one pass per poll:
  /version                              product version (connection test)
  /cluster/status                       cluster name (single-node install: the source name)
  /nodes                                node list: online state, CPU / memory usage
  /nodes/{node}/status                  CPU model / sockets / cores / threads / MHz, memory, pve-manager version
  /cluster/resources?type=vm            every QEMU VM and LXC container (one call, no per-VM request)
  /cluster/resources?type=storage       storages (shared ones once, local ones per node)
"""
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from ..storage import StorageError
from ..storage.http import JsonClient
from . import GIB, health_row, http_view, num

logger = logging.getLogger(__name__)
TOKEN_ID = re.compile(r"^[^@\s!]+@[^@\s!]+![A-Za-z0-9_.-]+$")


class ProxmoxClient:
    def __init__(self, cfg):
        token_id = (cfg.username or "").strip()
        if not TOKEN_ID.match(token_id) or not cfg.password:
            raise StorageError("Cần API token Proxmox: Token ID dạng user@realm!tên-token và Secret của token.")
        self.http = JsonClient(http_view(cfg), f"https://{cfg.host}:{cfg.port}/api2/json", product="Proxmox VE",
                               api_hint="Proxmox VE (6.2+)")
        self.http.headers["Authorization"] = f"PVEAPIToken={token_id}={cfg.password}"

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        body, _ = self.http.request("GET", path, params)
        return (body or {}).get("data")


def _cluster_name(status: List[Dict[str, Any]], fallback: str) -> str:
    for s in status or []:
        if s.get("type") == "cluster" and s.get("name"):
            return str(s["name"])
    return fallback


def parse(cluster: str, nodes: List[Dict[str, Any]], node_status: Dict[str, Dict[str, Any]],
          resources: List[Dict[str, Any]], storages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Pure: API payloads → (host_rows, vm_rows, extras)."""
    hosts, health = [], []
    for n in sorted(nodes or [], key=lambda x: str(x.get("node"))):
        name = str(n.get("node") or "")
        online = n.get("status") == "online"
        st = node_status.get(name) or {}
        ci = st.get("cpuinfo") or {}
        sockets = int(num(ci.get("sockets"))) or None
        per_socket = num(ci.get("cores"))
        cores = per_socket * (sockets or 1) if per_socket else num(n.get("maxcpu"))
        mem_total = num((st.get("memory") or {}).get("total")) or num(n.get("maxmem"))
        mem_used = num((st.get("memory") or {}).get("used")) or num(n.get("mem"))
        version = str(st.get("pveversion") or "")
        hosts.append({
            "cluster": cluster, "host": name, "cores": float(cores), "cpuMhz": num(ci.get("mhz")),
            "ramGB": round(mem_total / GIB, 2),
            "cpuUsagePctRaw": round(num(n.get("cpu")) * 100, 1) if online and n.get("cpu") is not None else None,
            "ramUsagePctRaw": round(mem_used / mem_total * 100, 1) if online and mem_total else None,
            "status": "connected" if online else "disconnected",
            "vendor": None, "model": None,
            # "pve-manager/8.2.4/faa83925c9641325" → 8.2.4; kept in the ESXi version column (hypervisor version)
            "esxiVersion": ("Proxmox VE " + version.split("/")[1]) if version.count("/") >= 1 else (version or None),
            "esxiBuild": version.split("/")[2] if version.count("/") >= 2 else None,
            "cpuModel": ci.get("model"), "cpuSockets": sockets,
            "cpuThreads": int(num(ci.get("cpus"))) or int(num(n.get("maxcpu"))) or None,
            "clusterHa": None, "clusterDrs": None,
        })
        health.append(health_row(name, cluster, online, "Node offline", "connected" if online else "disconnected"))
    vms, ds_vm = [], {}
    for r in resources or []:
        if r.get("type") not in ("qemu", "lxc") or int(num(r.get("template"))):
            continue
        on = r.get("status") == "running"
        maxmem, maxdisk = num(r.get("maxmem")), num(r.get("maxdisk"))
        # QEMU reports disk usage only through the guest agent (0 otherwise): use the provisioned size then.
        used = num(r.get("disk")) if (r.get("type") == "lxc" or num(r.get("disk")) > 0) else maxdisk
        vms.append({
            "cluster": cluster, "host": r.get("node"), "vm": r.get("name") or f"{r.get('type')}-{r.get('vmid')}",
            "instanceUuid": f"{r.get('type')}/{r.get('vmid')}",          # unique in a Proxmox cluster, kept on migration
            "powerState": "Powered On" if on else "Powered Off", "isPoweredOn": on, "ipAddress": None,
            "vcpu": num(r.get("maxcpu")), "ramGB": round(maxmem / GIB, 2),
            "diskProvGB": round(maxdisk / GIB, 2), "diskUsedGB": round(used / GIB, 2), "datastore": "UNKNOWN",
            "cpuUsagePctRaw": round(num(r.get("cpu")) * 100, 1) if on and r.get("cpu") is not None else None,
            "ramUsagePctRaw": round(num(r.get("mem")) / maxmem * 100, 1) if on and maxmem else None,
            "lastActivityDays": None, "powerOffDays": None,
            "guestId": "lxc" if r.get("type") == "lxc" else None, "guestFullName": None, "toolsGuestFullName": None,
            "toolsGuestFamily": None, "toolsStatus": None, "guestHostname": None,
        })
    ds_rows, seen = [], {}
    for s in storages or []:
        if s.get("status") not in (None, "available"):
            continue
        shared = bool(int(num(s.get("shared"))))
        key = f"shared/{s.get('storage')}" if shared else f"{s.get('node')}/{s.get('storage')}"
        if key in seen:
            seen[key]["hostCount"] += 1
            continue
        cap, used = num(s.get("maxdisk")) / GIB, num(s.get("disk")) / GIB
        row = {"key": key[:64], "name": str(s.get("storage")) if shared else f"{s.get('storage')} ({s.get('node')})",
               "type": str(s.get("plugintype") or "")[:16], "capacityGB": round(cap, 2), "freeGB": round(max(cap - used, 0), 2),
               "uncommittedGB": 0, "accessible": True, "maintenance": "normal", "clusters": [cluster], "hostCount": 1,
               "vmCount": ds_vm.get(key, 0), "naa": [], "nfsHost": None, "nfsPath": None}
        seen[key] = row
        ds_rows.append(row)
    return hosts, vms, {"datastores": ds_rows, "snapshots": [], "alarms": [], "health": health}


def collect(cfg) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    c = ProxmoxClient(cfg)
    cluster = _cluster_name(c.get("/cluster/status") or [], cfg.name)
    nodes = c.get("/nodes") or []
    status = {}
    for n in nodes:
        if n.get("status") == "online":
            try:
                status[n["node"]] = c.get(f"/nodes/{n['node']}/status") or {}
            except StorageError:           # one node unreadable: its row keeps the /nodes figures
                logger.warning("Proxmox %s: status of node %s unreadable", cfg.key, n.get("node"), exc_info=True)
    resources = c.get("/cluster/resources", {"type": "vm"}) or []
    storages = c.get("/cluster/resources", {"type": "storage"}) or []
    return parse(cluster, nodes, status, resources, storages)


def test(cfg) -> str:
    c = ProxmoxClient(cfg)
    v = c.get("/version") or {}
    nodes = c.get("/nodes") or []
    cluster = _cluster_name(c.get("/cluster/status") or [], cfg.name)
    return (f"Kết nối thành công: Proxmox VE {v.get('version', '?')} · cụm {cluster} · "
            f"{sum(1 for n in nodes if n.get('status') == 'online')}/{len(nodes)} node online.")
