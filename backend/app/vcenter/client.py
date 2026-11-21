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
import datetime as dt
import http.client
import logging
import socket
import ssl
import threading
import types
from typing import Any, Dict, List, Optional, Tuple

from pyVim import connect as _pyvim_connect
from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim, vmodl

from ..config import VCenterConfig


logger = logging.getLogger("vco.vcenter")


class VCenterConnectionError(RuntimeError):
    pass


# PropertyCollector faults that mean "this vCenter does not support the bulk request as built"
# (old version, unknown property path / type). Only these switch to per-object reads: a network
# error, a timeout or an expired session would only get worse with tens of thousands more calls.
FALLBACK_FAULTS = (vmodl.fault.InvalidArgument, vmodl.fault.InvalidType, vmodl.fault.NotSupported,
                   vmodl.fault.InvalidRequest, vmodl.fault.MethodNotFound)


def timeout_for(cfg: VCenterConfig) -> int:
    """Seconds allowed for one SOAP call to this vCenter (per source, else VCENTER_TIMEOUT_SECONDS)."""
    t = getattr(cfg, "timeout", None)
    if not t:
        from ..config import get_settings
        t = get_settings().vcenter_timeout_seconds
    return max(5, int(t))


def _timeout_error(cfg: VCenterConfig, timeout: int, exc: BaseException) -> VCenterConnectionError:
    return VCenterConnectionError(
        f"vCenter '{cfg.name}' ({cfg.host}) không phản hồi trong {timeout} giây "
        f"(VCENTER_TIMEOUT_SECONDS). Kiểm tra mạng / tải vCenter. Chi tiết: {type(exc).__name__}: {exc}"
    )


# SmartConnect first discovers the API version (GET /sdk/vimServiceVersions.xml, pyVim.connect's
# private __GetElementTree) with a bare HTTP(S)Connection and NO timeout, and no pyVmomi version
# offers a parameter for it (8.0.3 and 9.1 both): httpConnectionTimeout only reaches the SOAP stub
# built after discovery. A vCenter that accepts TCP and never answers would block the sync thread
# forever. So pyVim.connect gets connection factories that are identical to http.client's except
# that a connection built without a timeout uses the one of the current _connect() call
# (thread-local, so parallel syncs of different vCenters each keep their own value).
# Where pyVim.connect looks the classes up depends on the pyVmomi version:
#   - 8.0.x: `http_client.HTTPSConnection(...)` via six.moves -> replace the module reference;
#   - 9.x:   `HTTPSConnection(...)` imported into its globals from pyVmomi.five -> replace the names.
# Both are read at call time, so patching the attributes is enough. A layout matching neither is
# logged (and caught by tests/test_reliability.py::test_version_discovery_timeout_is_hooked).
_tls = threading.local()


def _discovery_timeout() -> Optional[float]:
    return getattr(_tls, "timeout", None) or socket.getdefaulttimeout()


def _HTTPSConnection(*args, **kwargs):
    kwargs.setdefault("timeout", _discovery_timeout())
    return http.client.HTTPSConnection(*args, **kwargs)


def _HTTPConnection(*args, **kwargs):
    kwargs.setdefault("timeout", _discovery_timeout())
    return http.client.HTTPConnection(*args, **kwargs)


class _HttpClientWithTimeout(types.ModuleType):
    """Stand-in for http.client: everything is the real module except the two factories."""
    HTTPSConnection = staticmethod(_HTTPSConnection)
    HTTPConnection = staticmethod(_HTTPConnection)

    def __getattr__(self, name):
        return getattr(http.client, name)


def _hook_discovery_timeout(mod) -> Optional[str]:
    """Route pyVim.connect's version-discovery connections through the factories above."""
    if getattr(mod, "http_client", None) is http.client:                # pyVmomi 8.0.x
        mod.http_client = _HttpClientWithTimeout("http_client")
        return "http_client"
    if isinstance(getattr(mod, "http_client", None), _HttpClientWithTimeout):
        return "http_client"
    if (getattr(mod, "HTTPSConnection", None) in (http.client.HTTPSConnection, _HTTPSConnection)
            and getattr(mod, "HTTPConnection", None) in (http.client.HTTPConnection, _HTTPConnection)):
        mod.HTTPSConnection = _HTTPSConnection                          # pyVmomi 9.x
        mod.HTTPConnection = _HTTPConnection
        return "globals"
    logger.warning("pyVim.connect layout not recognised: vCenter version discovery has no timeout")
    return None


_DISCOVERY_TIMEOUT_HOOK = _hook_discovery_timeout(_pyvim_connect)


def _connect(cfg: VCenterConfig, timeout: Optional[int] = None):
    # httpConnectionTimeout becomes the socket timeout of the SOAP stub: it bounds the login AND
    # every later call on this connection. None (the old value) means wait forever. The version
    # discovery before the login gets the same value through _hook_discovery_timeout.
    timeout = timeout or timeout_for(cfg)
    _tls.timeout = timeout
    context = None
    if not cfg.verify_ssl:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    elif getattr(cfg, "ca_pem", ""):
        context = ssl.create_default_context(cadata=cfg.ca_pem)
    try:
        si = SmartConnect(
            host=cfg.host,
            port=cfg.port,
            user=cfg.username,
            pwd=cfg.password,
            sslContext=context,
            httpConnectionTimeout=timeout,
        )
    except (socket.timeout, TimeoutError) as exc:
        raise _timeout_error(cfg, timeout, exc) from exc
    except Exception as exc:  # noqa: BLE001 - surface a friendly, actionable error
        raise VCenterConnectionError(
            f"Không kết nối được vCenter '{cfg.name}' ({cfg.host}): {exc}"
        ) from exc
    finally:
        _tls.timeout = None
    return si


# ---------------------------------------------------------------------------
# Bulk property retrieval
# ---------------------------------------------------------------------------
# Reading `vm.summary`, `vm.datastore[0].name`, `runtime.host.name`, `host.parent.name`…
# object by object costs one SOAP round trip each — tens of thousands of calls for a few
# thousand VMs. The PropertyCollector returns the same properties for every object of a
# type in a handful of paged calls; references between objects are resolved locally by
# managed-object id.
HOST_PROPS = ["name", "summary", "parent", "runtime.healthSystemRuntime"]   # + hardware sensors (extras)
COMPUTE_PROPS = ["name", "configurationEx"]        # clusters and standalone compute resources
VM_PROPS = ["name", "summary", "datastore", "guest.guestFamily", "snapshot",   # + snapshot tree (extras)
            "parent", "resourcePool", "customValue"]                          # owner sources (ICO v2 G2b)
FOLDER_PROPS = ["name", "parent"]                  # VM folder path, resolved locally by moId
POOL_PROPS = ["name"]
MAX_FOLDER_DEPTH = 32
MAX_CUSTOM_ATTRS = 30
DATASTORE_PROPS = ["name", "summary", "host", "info"]   # capacity / mounts / VMFS extents (NAA) or NFS export (extras)
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


# Preference order of HostSystemIdentificationInfo.identifierType.key: Dell / most OEMs report the
# service tag; others only the system / enclosure serial.
SERIAL_ID_KEYS = ("ServiceTag", "SerialNumberTag", "EnclosureSerialNumberTag")


def host_serial(hw: Any) -> Optional[str]:
    """Hardware serial of a host from summary.hardware.otherIdentifyingInfo (None when not reported)."""
    found: Dict[str, str] = {}
    for info in getattr(hw, "otherIdentifyingInfo", None) or []:
        key = _safe_attr(info, "identifierType", "key")
        val = str(getattr(info, "identifierValue", None) or "").strip()
        if key in SERIAL_ID_KEYS and val and key not in found:
            found[key] = val[:64]
    return next((found[k] for k in SERIAL_ID_KEYS if k in found), None)


def folder_path(folders: Objs, ref: Any) -> Optional[str]:
    """"/Projects/Bank/Core" for a VM whose parent is that folder: names up to the datacenter's hidden
    "vm" folder (dropped). None when the VM has no folder parent (vApp member) or folders were not read."""
    names, cur, seen = [], _moid(ref), set()
    while cur in folders and cur not in seen and len(names) < MAX_FOLDER_DEPTH:
        seen.add(cur)
        _, props = folders[cur]
        parent = _moid(props.get("parent"))
        if parent not in folders:          # the top folder of the datacenter ("vm"): not part of the path
            break
        names.append(str(props.get("name") or cur))
        cur = parent
    if not seen:
        return None
    return ("/" + "/".join(reversed(names)))[:512]


def custom_attrs(values: Any, field_names: Dict[int, str]) -> Dict[str, str]:
    """{attribute name: value} from vm.customValue (keys resolved by CustomFieldsManager.field)."""
    out: Dict[str, str] = {}
    for v in values or []:
        name = field_names.get(getattr(v, "key", None))
        val = str(getattr(v, "value", "") or "").strip()
        if name and val and len(out) < MAX_CUSTOM_ATTRS:
            out[str(name)[:64]] = val[:255]
    return out


def vm_field_names(content) -> Dict[int, str]:
    """Custom attribute definitions for VMs (global or VirtualMachine): one property read per session."""
    try:
        fields = content.customFieldsManager.field if content.customFieldsManager is not None else []
    except Exception:  # noqa: BLE001 — no custom attributes is not an inventory error
        logger.warning("Could not read custom attribute definitions", exc_info=True)
        return {}
    return {f.key: f.name for f in fields or []
            if getattr(f, "managedObjectType", None) in (None, vim.VirtualMachine)}


def build_rows(hosts: Objs, compute: Objs, vms: Objs, datastores: Objs, folders: Optional[Objs] = None,
               pools: Optional[Objs] = None, field_names: Optional[Dict[int, str]] = None
               ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Turn retrieved properties into the app's normalized (host_rows, vm_rows)."""
    folders, pools, field_names = folders or {}, pools or {}, field_names or {}
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
            # Hardware identity (CI registry key) — same summary.hardware object, no extra round-trip.
            "serial": host_serial(hw),
            "hwUuid": (str(_safe_attr(hw, "uuid") or "")[:64] or None),
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
            # Owner sources (G2b): folder path, resource pool ("Resources" = the cluster root pool → None), custom attributes.
            "folderPath": folder_path(folders, props.get("parent")),
            "resourcePool": _pool_name(pools, props.get("resourcePool")),
            "customAttrs": custom_attrs(props.get("customValue"), field_names),
        })
    return host_rows, vm_rows


def _pool_name(pools: Objs, ref: Any) -> Optional[str]:
    hit = pools.get(_moid(ref))
    name = str(hit[1].get("name") or "") if hit else ""
    return name[:255] if name and name != "Resources" else None


def host_names(hosts: Objs, compute: Objs) -> Dict[str, Tuple[str, str]]:
    """host moId -> (host name, cluster name), as build_rows names them."""
    out = {}
    for moid, (_, props) in hosts.items():
        cluster, _, _ = _compute_info(compute, props.get("parent"))
        out[moid] = (props.get("name") or _safe_attr(props.get("summary"), "config", "name") or moid, cluster)
    return out


def collect_inventory(cfg: VCenterConfig) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Connect to one vCenter and return (host_rows, vm_rows) in the app's
    normalized row shape. Raises VCenterConnectionError on failure."""
    hosts, vms, _ = collect_inventory_full(cfg)
    return hosts, vms


def collect_inventory_full(cfg: VCenterConfig, events_since: Optional[dt.datetime] = None
                           ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    """(host_rows, vm_rows, extras) in one session; extras = datastores / snapshots / alarms / health
    (vcenter/extras.py) + `events` / `eventsUntil` (vcenter/events.py, from `events_since`, G6). A failure reading the
    extras or the events never loses the inventory: they come back empty (events: None, the cursor does not move)."""
    si = _connect(cfg)
    try:
        content = si.RetrieveContent()
        specs = [(vim.HostSystem, HOST_PROPS), (vim.ComputeResource, COMPUTE_PROPS),
                 (vim.VirtualMachine, VM_PROPS), (vim.Datastore, DATASTORE_PROPS)]
        try:
            fetched = [_retrieve(content, t, p) for t, p in specs]
        except FALLBACK_FAULTS as exc:      # an old vCenter rejecting the bulk request / a property path
            logger.warning("Bulk property retrieval not supported on %s (%s) — falling back to per-object reads",
                           cfg.key, type(exc).__name__, exc_info=True)
            fetched = [_retrieve_lazy(content, t, p) for t, p in specs]
        try:      # folders / pools / attribute names only feed owner assignment: never lose the inventory over them
            try:
                owner_src = [_retrieve(content, vim.Folder, FOLDER_PROPS), _retrieve(content, vim.ResourcePool, POOL_PROPS)]
            except FALLBACK_FAULTS:
                owner_src = [_retrieve_lazy(content, vim.Folder, FOLDER_PROPS), _retrieve_lazy(content, vim.ResourcePool, POOL_PROPS)]
            field_names = vm_field_names(content)
        except (socket.timeout, TimeoutError, ConnectionError, OSError):
            raise
        except Exception:  # noqa: BLE001
            logger.warning("Could not read folders / resource pools for %s", cfg.key, exc_info=True)
            owner_src, field_names = [{}, {}], {}
        host_rows, vm_rows = build_rows(*fetched, *owner_src, field_names)
        try:
            from . import extras as _extras
            hosts_o, compute_o, vms_o, ds_o = fetched
            extra = _extras.build_extras(content, hosts_o, compute_o, vms_o, ds_o, host_names(hosts_o, compute_o))
        except (socket.timeout, TimeoutError, ConnectionError, OSError):
            raise
        except Exception:  # noqa: BLE001 — operational extras are optional; the inventory is what matters
            logger.warning("Could not build datastore / snapshot / alarm / health data for %s", cfg.key, exc_info=True)
            extra = {"datastores": [], "snapshots": [], "alarms": [], "health": [], "error": True}
        try:
            from . import config_facts
            hosts_o, compute_o, vms_o, _ds = fetched
            extra["config"] = config_facts.read(content, hosts_o, compute_o, vms_o, host_names(hosts_o, compute_o))
        except (socket.timeout, TimeoutError, ConnectionError, OSError):
            raise
        except Exception:  # noqa: BLE001 — the baseline is optional: None keeps the previous facts (G9a)
            logger.warning("Could not read configuration facts for %s", cfg.key, exc_info=True)
            extra["config"] = None
        try:
            from . import perf
            from ..config import get_settings
            st = get_settings()
            hosts_o, _c, vms_o, _ds = fetched
            extra["perf"] = perf.read(content, hosts_o, vms_o, host_names(hosts_o, _c), st.perf_max_vms, st.perf_budget_seconds)
        except (socket.timeout, TimeoutError, ConnectionError, OSError):
            raise
        except Exception:  # noqa: BLE001 — contention counters are optional (G9c): None keeps the previous state
            logger.warning("Could not read performance counters for %s", cfg.key, exc_info=True)
            extra["perf"] = None
        about = getattr(content, "about", None)
        extra["product"] = {"version": getattr(about, "version", None), "build": getattr(about, "build", None),
                            "fullName": getattr(about, "fullName", None)} if about is not None else None   # lifecycle (G9b)
        until = dt.datetime.utcnow()
        try:
            from . import events as _events
            extra["events"] = _events.read(content, events_since or until - _events.FIRST_LOOKBACK, until)
            extra["eventsUntil"] = until
        except (socket.timeout, TimeoutError, ConnectionError, OSError):
            raise
        except Exception:  # noqa: BLE001 — the change log is optional: the next round reads the same window again
            logger.warning("Could not read vCenter events for %s", cfg.key, exc_info=True)
            extra["events"] = None
        return host_rows, vm_rows, extra
    except (socket.timeout, TimeoutError) as exc:
        raise _timeout_error(cfg, timeout_for(cfg), exc) from exc
    except (ConnectionError, OSError) as exc:
        raise VCenterConnectionError(f"Mất kết nối tới vCenter '{cfg.name}' ({cfg.host}) khi đang đọc dữ liệu: {exc}") from exc
    finally:
        try:
            Disconnect(si)
        except Exception:  # noqa: BLE001
            pass


def test_connection(cfg: VCenterConfig, timeout: int = 20) -> str:
    """Log in, read the product name, log out. Returns e.g. "VMware vCenter Server 8.0.2 build-22617221"."""
    si = _connect(cfg, timeout=timeout)
    try:
        return si.content.about.fullName
    finally:
        try:
            Disconnect(si)
        except Exception:  # noqa: BLE001
            pass
