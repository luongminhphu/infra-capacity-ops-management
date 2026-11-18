"""Hypervisor managers other than VMware vCenter: Proxmox VE and Nutanix (Prism Element).

They are configured like a vCenter (Settings → Nguồn dữ liệu), read with GET only (rule 1), and return the
same normalised rows as `vcenter/client.collect_inventory_full`: (host_rows, vm_rows, extras). The history is
stored under the source key exactly like a vCenter's, so capacity / compliance / trends / what-if work as is.
`platform` on the config (vmware | proxmox | nutanix) picks the collector.
"""
from dataclasses import dataclass
from typing import Any, Dict

PLATFORMS = ("vmware", "proxmox", "nutanix")
PLATFORM_LABELS = {"vmware": "VMware vCenter", "proxmox": "Proxmox VE", "nutanix": "Nutanix AHV"}
KIND_PLATFORM = {"vcenter": "vmware", "proxmox": "proxmox", "nutanix": "nutanix"}
DEFAULT_PORTS = {"vcenter": 443, "proxmox": 8006, "nutanix": 9440}
GIB = 1024 ** 3


@dataclass
class HttpView:
    """What storage.http.JsonClient needs from a config (host, port, TLS, timeout)."""
    host: str
    port: int
    verify_ssl: bool
    ca_pem: str
    timeout: int


def http_view(cfg) -> HttpView:
    return HttpView(host=cfg.host, port=int(cfg.port), verify_ssl=bool(cfg.verify_ssl), ca_pem=cfg.ca_pem or "",
                    timeout=int(cfg.timeout or 60))


def num(v: Any, default: float = 0.0) -> float:
    """Numbers come as int, float or string ("1234") depending on the API version."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def health_row(host: str, cluster: str, ok: bool, issue: str = "", connection: str = "connected",
               maintenance: bool = False) -> Dict[str, Any]:
    return {"host": host, "cluster": cluster, "overallStatus": "green" if ok else "red", "connection": connection,
            "maintenance": maintenance, "issues": [] if ok else [{"name": issue or "Host không sẵn sàng", "state": "red"}]}
