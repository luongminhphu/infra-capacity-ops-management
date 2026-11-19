"""Nutanix AHV through Prism Element (REST API v2.0, port 9440) — one source per Nutanix cluster.

Authentication: HTTP Basic with a local or directory user holding the **Viewer** role (read-only). Every call is
a GET (Prism Central v3 lists VMs with POST: not used, rule 1). Point the source at the cluster virtual IP of
Prism Element.

Reads per poll:
  /cluster/                                   cluster name, AOS version
  /hosts/                                     hosts: CPU sockets / cores / threads / MHz, memory, model, usage (ppm)
  /vms/?include_vm_disk_config=true&include_vm_nic_config=true   (paged with offset / length)
  /storage_containers/                        containers: capacity / usage → datastores
"""
import base64
import logging
from typing import Any, Dict, List, Tuple

from ..storage import StorageError
from ..storage.http import JsonClient
from . import GIB, health_row, http_view, num

logger = logging.getLogger(__name__)
PAGE = 500
MAX_VMS = 20000
BASE = "/PrismGateway/services/rest/v2.0"


class PrismClient:
    def __init__(self, cfg):
        if not cfg.username or not cfg.password:
            raise StorageError("Cần user + mật khẩu Prism Element có role Viewer (chỉ đọc).")
        self.http = JsonClient(http_view(cfg), f"https://{cfg.host}:{cfg.port}{BASE}", product="Nutanix Prism",
                               api_hint="Prism Element (AOS 5.x – 6.x, API v2.0)")
        token = base64.b64encode(f"{cfg.username}:{cfg.password}".encode()).decode()
        self.http.headers["Authorization"] = f"Basic {token}"

    def get(self, path: str, params: Dict[str, Any] = None) -> Any:
        body, _ = self.http.request("GET", path, params)
        return body or {}

    def entities(self, path: str, params: Dict[str, Any] = None, paged: bool = False) -> List[Dict[str, Any]]:
        if not paged:
            return self.get(path, params).get("entities") or []
        out: List[Dict[str, Any]] = []
        offset = 0
        while len(out) < MAX_VMS:
            page = self.get(path, {**(params or {}), "offset": offset, "length": PAGE})
            ents = page.get("entities") or []
            out.extend(ents)
            total = int(num((page.get("metadata") or {}).get("grand_total_entities") or (page.get("metadata") or {}).get("total_entities"), 0))
            offset += len(ents)
            if not ents or len(ents) < PAGE or (total and offset >= total):
                break
        return out


def _ppm_pct(stats: Dict[str, Any], key: str):
    v = (stats or {}).get(key)
    return None if v in (None, "", "-1") else round(num(v) / 10000, 1)


def _usage(c: Dict[str, Any]) -> Tuple[float, float]:
    """(capacity GiB, used GiB) of a storage container; key names differ slightly between AOS versions."""
    u = c.get("usage_stats") or {}
    cap = num(u.get("storage.user_capacity_bytes")) or num(u.get("storage.capacity_bytes")) or num(c.get("max_capacity"))
    used = num(u.get("storage.user_usage_bytes")) or num(u.get("storage.usage_bytes"))
    return cap / GIB, used / GIB


def parse(cluster: Dict[str, Any], hosts: List[Dict[str, Any]], vms: List[Dict[str, Any]],
          containers: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Pure: API payloads → (host_rows, vm_rows, extras)."""
    cname = str(cluster.get("name") or "Nutanix")
    aos = cluster.get("version")
    host_rows, health, by_uuid = [], [], {}
    for h in sorted(hosts or [], key=lambda x: str(x.get("name"))):
        name = str(h.get("name") or h.get("uuid") or "")
        by_uuid[h.get("uuid")] = name
        state = str(h.get("state") or "NORMAL").upper()
        maint = bool(h.get("host_in_maintenance_mode")) or str(h.get("hypervisor_state") or "").startswith("kEnteringMaintenance")
        ok = state == "NORMAL"
        stats = h.get("stats") or {}
        model = h.get("block_model_name") or h.get("block_model")
        host_rows.append({
            "cluster": cname, "host": name, "cores": num(h.get("num_cpu_cores")), "cpuMhz": round(num(h.get("cpu_frequency_in_hz")) / 1e6, 1),
            "ramGB": round(num(h.get("memory_capacity_in_bytes")) / GIB, 2),
            "cpuUsagePctRaw": _ppm_pct(stats, "hypervisor_cpu_usage_ppm"),
            "ramUsagePctRaw": _ppm_pct(stats, "hypervisor_memory_usage_ppm"),
            "status": "connected" if ok else state.lower(),
            "vendor": "Nutanix" if str(model or "").upper().startswith("NX") else None, "model": model,
            "esxiVersion": h.get("hypervisor_full_name") or (f"AHV · AOS {aos}" if aos else "AHV"), "esxiBuild": aos,
            "cpuModel": h.get("cpu_model"), "cpuSockets": int(num(h.get("num_cpu_sockets"))) or None,
            "cpuThreads": int(num(h.get("num_cpu_threads"))) or None, "clusterHa": None, "clusterDrs": None,
            # Prism v2.0 /hosts reports the node serial ("serial"; the OEM service tag on Dell XC / HPE DX);
            # "block_serial" is the chassis shared by several nodes, so it is not a host key. Its "uuid" is a
            # Nutanix-internal id, not the BIOS uuid → hwUuid stays None.
            "serial": (str(h.get("serial") or "").strip()[:64] or None), "hwUuid": None,
        })
        health.append(health_row(name, cname, ok, f"Trạng thái host {state}", maintenance=maint))
    by_container = {c.get("storage_container_uuid"): str(c.get("name") or "") for c in containers or []}
    ct_vms: Dict[str, int] = {}
    vm_rows = []
    for v in vms or []:
        disks = [d for d in (v.get("vm_disk_info") or []) if not d.get("is_cdrom")]
        prov = sum(num(d.get("size")) for d in disks) / GIB
        cts = [d.get("storage_container_uuid") for d in disks if d.get("storage_container_uuid")]
        for ct in set(cts):
            ct_vms[ct] = ct_vms.get(ct, 0) + 1
        on = str(v.get("power_state") or "").lower() == "on"
        ips = [n.get("ip_address") for n in (v.get("vm_nics") or []) if n.get("ip_address")]
        vm_rows.append({
            "cluster": cname, "host": by_uuid.get(v.get("host_uuid")), "vm": v.get("name") or v.get("uuid"),
            "instanceUuid": v.get("uuid"), "powerState": "Powered On" if on else "Powered Off", "isPoweredOn": on,
            "ipAddress": ips[0] if ips else None,
            "vcpu": num(v.get("num_vcpus"), 1) * (num(v.get("num_cores_per_vcpu"), 1) or 1),
            "ramGB": round(num(v.get("memory_mb")) / 1024, 2), "diskProvGB": round(prov, 2), "diskUsedGB": round(prov, 2),
            "datastore": by_container.get(cts[0], "UNKNOWN") if cts else "UNKNOWN",
            "cpuUsagePctRaw": None, "ramUsagePctRaw": None, "lastActivityDays": None, "powerOffDays": None,
            "guestId": None, "guestFullName": v.get("guest_os") or None, "toolsGuestFullName": None, "toolsGuestFamily": None,
            "toolsStatus": None, "guestHostname": None,
        })
    ds_rows = []
    for c in containers or []:
        cap, used = _usage(c)
        uid = str(c.get("storage_container_uuid") or c.get("name"))
        ds_rows.append({"key": uid[:64], "name": str(c.get("name") or uid), "type": "nutanix", "capacityGB": round(cap, 2),
                        "freeGB": round(max(cap - used, 0), 2), "uncommittedGB": 0, "accessible": True, "maintenance": "normal",
                        "clusters": [cname], "hostCount": len(host_rows), "vmCount": ct_vms.get(c.get("storage_container_uuid"), 0),
                        "naa": [], "nfsHost": None, "nfsPath": None})
    return host_rows, vm_rows, {"datastores": ds_rows, "snapshots": [], "alarms": [], "health": health}


def collect(cfg) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    c = PrismClient(cfg)
    cluster = c.get("/cluster/")
    hosts = c.entities("/hosts/")
    vms = c.entities("/vms/", {"include_vm_disk_config": "true", "include_vm_nic_config": "true"}, paged=True)
    containers = c.entities("/storage_containers/")
    return parse(cluster, hosts, vms, containers)


def test(cfg) -> str:
    c = PrismClient(cfg)
    cl = c.get("/cluster/")
    hosts = c.entities("/hosts/")
    return f"Kết nối thành công: Nutanix cụm {cl.get('name', '?')} · AOS {cl.get('version', '?')} · {len(hosts)} host."
