"""
Read-only vCenter inventory collector using pyVmomi (vSphere Web Services SDK —
supported against vCenter 6.7/7.x/8.x). Only ever issues property-collector
READS through the ContainerView API; never calls any mutating method, matching
the read-only `infra.mon@vsphere.local` service account this app is designed
for.

Output rows use the SAME camelCase field names as the frontend's
appState.hosts / appState.vms so they can be persisted and served back to the
UI with zero translation layer (see app/capacity.py docstring).
"""
import logging
import ssl
from typing import Any, Dict, List, Optional, Tuple

from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim, vmodl

from ..config import VCenterConfig


logger = logging.getLogger("vco.vcenter")


class VCenterConnectionError(RuntimeError):
    pass


def _connect(cfg: VCenterConfig):
    context = None
    if not cfg.verify_ssl:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    try:
        si = SmartConnect(
            host=cfg.host,
            port=cfg.port,
            user=cfg.username,
            pwd=cfg.password,
            sslContext=context,
        )
    except Exception as exc:  # noqa: BLE001 - surface a friendly, actionable error
        raise VCenterConnectionError(
            f"Không kết nối được vCenter '{cfg.name}' ({cfg.host}): {exc}"
        ) from exc
    return si


# ---------------------------------------------------------------------------
# Bulk property retrieval
# ---------------------------------------------------------------------------
# Reading `vm.summary`, `vm.datastore[0].name`, `runtime.host.name`, `host.parent.name`…
# object by object costs one SOAP round trip each — tens of thousands of calls for a few
# thousand VMs. The PropertyCollector returns the same properties for every object of a
# type in a handful of paged calls; references between objects are resolved locally by
# managed-object id.
HOST_PROPS = ["name", "summary", "parent"]
COMPUTE_PROPS = ["name", "configurationEx"]        # clusters and standalone compute resources
VM_PROPS = ["name", "summary", "datastore", "guest.guestFamily"]
DATASTORE_PROPS = ["name"]
PAGE_SIZE = 1000

Objs = Dict[str, Tuple[Any, Dict[str, Any]]]       # moId -> (managed object, {property path: value})


def _retrieve(content, vim_type, paths: List[str]) -> Objs:
    pc = vmodl.query.PropertyCollector
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim_type], True)
    try:
        spec = pc.FilterSpec(
            objectSet=[pc.ObjectSpec(obj=view, skip=True, selectSet=[
                pc.TraversalSpec(name="view", path="view", skip=False, type=vim.view.ContainerView)])],
            propSet=[pc.PropertySpec(type=vim_type, pathSet=paths, all=False)],
        )
        collector = content.propertyCollector
        result = collector.RetrievePropertiesEx([spec], pc.RetrieveOptions(maxObjects=PAGE_SIZE))
        out: Objs = {}
        while result:
            for oc in result.objects or []:
                out[oc.obj._moId] = (oc.obj, {p.name: p.val for p in (oc.propSet or [])})
            result = collector.ContinueRetrievePropertiesEx(result.token) if result.token else None
        return out
    finally:
        view.Destroy()


def _retrieve_lazy(content, vim_type, paths: List[str]) -> Objs:
    """Fallback: same shape, one property read at a time (slow, but needs nothing special)."""
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim_type], True)
    try:
        objs = list(view.view)
    finally:
        view.Destroy()
    return {o._moId: (o, {p: _safe_attr(o, *p.split(".")) for p in paths}) for o in objs}


def _safe_attr(obj, *path, default=None):
    cur = obj
    for name in path:
        try:
            cur = getattr(cur, name)
        except Exception:  # noqa: BLE001
            return default
        if cur is None:
            return default
    return cur


def _moid(ref) -> Optional[str]:
    return getattr(ref, "_moId", None) if ref is not None else None


def _mb_to_gb(mb: float) -> float:
    return (mb or 0) / 1024.0


def _compute_info(compute: Objs, ref) -> Tuple[str, Any, Any]:
    """(cluster / compute resource name, HA enabled, DRS enabled) for a host's parent.
    HA/DRS are None for standalone hosts. Licensing scope follows these: a VM in an
    HA/DRS cluster may run on any host of that cluster, so every host must be licensed."""
    hit = compute.get(_moid(ref))
    if not hit:
        return "UNKNOWN", None, None
    obj, props = hit
    if not isinstance(obj, vim.ClusterComputeResource):
        return props.get("name") or "UNKNOWN", None, None
    cfg = props.get("configurationEx")
    das, drs = getattr(cfg, "dasConfig", None), getattr(cfg, "drsConfig", None)
    return (props.get("name") or "UNKNOWN",
            bool(das.enabled) if das is not None else None,
            bool(drs.enabled) if drs is not None else None)


def build_rows(hosts: Objs, compute: Objs, vms: Objs, datastores: Objs) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Turn retrieved properties into the app's normalized (host_rows, vm_rows)."""
    host_rows: List[Dict[str, Any]] = []
    host_info: Dict[str, Tuple[str, str]] = {}          # host moId -> (host name, cluster name)
    for moid, (_, props) in hosts.items():
        summary = props.get("summary")
        hw = _safe_attr(summary, "hardware")
        qs = _safe_attr(summary, "quickStats")
        cores = getattr(hw, "numCpuCores", 0) or 0
        cpu_mhz_per_core = getattr(hw, "cpuMhz", 0) or 0
        ram_gb = _mb_to_gb(hw.memorySize / (1024 * 1024)) if hw is not None and hw.memorySize else 0
        # quickStats gives instantaneous MHz/MB usage — a live proxy for CPU/RAM usage; trend
        # accuracy improves as the scheduler accumulates history.
        total_mhz = cores * cpu_mhz_per_core
        cpu_usage_pct = (qs.overallCpuUsage / total_mhz * 100) if qs is not None and total_mhz else None
        ram_usage_pct = (qs.overallMemoryUsage / (ram_gb * 1024) * 100) if qs is not None and ram_gb else None
        cluster, ha, drs = _compute_info(compute, props.get("parent"))
        product = _safe_attr(summary, "config", "product")
        name = props.get("name") or _safe_attr(summary, "config", "name") or moid
        host_info[moid] = (name, cluster)
        runtime = _safe_attr(summary, "runtime")
        host_rows.append({
            "cluster": cluster,
            "host": name,
            "cores": float(cores),
            "cpuMhz": float(cpu_mhz_per_core),
            "ramGB": float(ram_gb or 0),
            "cpuUsagePctRaw": cpu_usage_pct,
            "ramUsagePctRaw": ram_usage_pct,
            "status": str(runtime.connectionState) if runtime is not None else "connected",
            # Inventory & License Assessment facts
            "vendor": _safe_attr(hw, "vendor"),
            "model": _safe_attr(hw, "model"),
            "esxiVersion": _safe_attr(product, "version"),
            "esxiBuild": _safe_attr(product, "build"),
            "cpuModel": _safe_attr(hw, "cpuModel"),
            "cpuSockets": int(_safe_attr(hw, "numCpuPkgs", default=0) or 0) or None,
            "cpuThreads": int(_safe_attr(hw, "numCpuThreads", default=0) or 0) or None,
            "clusterHa": ha,
            "clusterDrs": drs,
        })

    vm_rows: List[Dict[str, Any]] = []
    for _, (_, props) in vms.items():
        summary = props.get("summary")
        cfgd = _safe_attr(summary, "config")
        qs = _safe_attr(summary, "quickStats")
        storage = _safe_attr(summary, "storage")
        runtime = _safe_attr(summary, "runtime")
        is_on = _safe_attr(runtime, "powerState") == vim.VirtualMachinePowerState.poweredOn
        vcpu = getattr(cfgd, "numCpu", 0) or 0
        ram_gb = _mb_to_gb(cfgd.memorySizeMB) if cfgd is not None else 0
        committed_gb = _mb_to_gb((storage.committed or 0) / (1024 * 1024)) if storage is not None else 0
        uncommitted_gb = _mb_to_gb((storage.uncommitted or 0) / (1024 * 1024)) if storage is not None else 0
        cpu_usage_pct = (qs.overallCpuUsage / (vcpu * 2000) * 100) if qs is not None and vcpu else None  # ~2 GHz/vCPU
        ram_usage_pct = (qs.hostMemoryUsage / (ram_gb * 1024) * 100) if qs is not None and ram_gb else None
        host_name, cluster = host_info.get(_moid(_safe_attr(runtime, "host")), (None, "UNKNOWN"))
        ds_refs = props.get("datastore") or []
        ds = datastores.get(_moid(ds_refs[0])) if len(ds_refs) else None
        guest = _safe_attr(summary, "guest")
        vm_rows.append({
            "cluster": cluster,
            "host": host_name,
            "vm": getattr(cfgd, "name", None) or props.get("name"),
            # instanceUuid is unique per vCenter and survives rename / vMotion / cross-cluster
            # migration; the BIOS uuid can be duplicated by clones.
            "instanceUuid": getattr(cfgd, "instanceUuid", None) or None,
            "powerState": "Powered On" if is_on else "Powered Off",
            "isPoweredOn": is_on,
            "ipAddress": _safe_attr(guest, "ipAddress"),
            "vcpu": float(vcpu),
            "ramGB": float(ram_gb or 0),
            "diskProvGB": float(committed_gb + uncommitted_gb),
            "diskUsedGB": float(committed_gb),
            "datastore": (ds[1].get("name") if ds else None) or "UNKNOWN",
            "cpuUsagePctRaw": cpu_usage_pct,
            "ramUsagePctRaw": ram_usage_pct,
            # Not derivable from one snapshot — filled from polling history by the scheduler.
            "lastActivityDays": None,
            "powerOffDays": None,
            # Guest OS evidence: configured type (always present) and what VMware Tools reports.
            "guestId": _safe_attr(cfgd, "guestId"),
            "guestFullName": _safe_attr(cfgd, "guestFullName"),
            "toolsGuestFullName": _safe_attr(guest, "guestFullName"),
            "toolsGuestFamily": props.get("guest.guestFamily"),
            "toolsStatus": _safe_attr(guest, "toolsRunningStatus"),
            "guestHostname": _safe_attr(guest, "hostName"),
        })
    return host_rows, vm_rows


def collect_inventory(cfg: VCenterConfig) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Connect to one vCenter and return (host_rows, vm_rows) in the app's
    normalized row shape. Raises VCenterConnectionError on failure."""
    si = _connect(cfg)
    try:
        content = si.RetrieveContent()
        specs = [(vim.HostSystem, HOST_PROPS), (vim.ComputeResource, COMPUTE_PROPS),
                 (vim.VirtualMachine, VM_PROPS), (vim.Datastore, DATASTORE_PROPS)]
        try:
            fetched = [_retrieve(content, t, p) for t, p in specs]
        except Exception as exc:  # noqa: BLE001 — e.g. an old vCenter rejecting a property path
            logger.warning("Bulk property retrieval failed on %s (%s) — falling back to per-object reads",
                           cfg.key, type(exc).__name__)
            fetched = [_retrieve_lazy(content, t, p) for t, p in specs]
        return build_rows(*fetched)
    finally:
        try:
            Disconnect(si)
        except Exception:  # noqa: BLE001
            pass
