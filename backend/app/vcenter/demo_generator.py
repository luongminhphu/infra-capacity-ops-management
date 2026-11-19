"""
Synthetic inventory generator used when a vCenter is configured with DEMO=true
(see .env.example). Lets the app be evaluated end-to-end — login, live "sync",
Executive/Technical views, compliance engine, historical trend — before real
vCenter credentials are wired in. Clearly surfaced in the UI as demo data.
"""
import hashlib
import random
import time
import uuid
from typing import Any, Dict, List, Tuple

from ..config import VCenterConfig

CLUSTER_PLAN = [
    # (cluster, hosts, cores/host, RAM GB/host, sockets, vendor/model, ESXi version/build, cpu model)
    ("PROD-CLUSTER-A", 6, 40, 512, 2, ("Dell Inc.", "PowerEdge R750"), ("8.0.2", "22380479"), "Intel(R) Xeon(R) Gold 6338 CPU @ 2.00GHz"),
    ("PROD-CLUSTER-B", 4, 32, 384, 2, ("Dell Inc.", "PowerEdge R740"), ("7.0.3", "21930508"), "Intel(R) Xeon(R) Gold 6226R CPU @ 2.90GHz"),
    ("DR-CLUSTER", 3, 24, 256, 2, ("HPE", "ProLiant DL380 Gen10"), ("7.0.3", "21930508"), "Intel(R) Xeon(R) Gold 5118 CPU @ 2.30GHz"),
]

# Guest OS mix (weight, guestId, configured guestFullName, Tools guestFullName, guestFamily)
GUEST_OS_PLAN = [
    (26, "windows2019srvNext_64Guest", "Microsoft Windows Server 2019 (64-bit)", "Microsoft Windows Server 2019 (64-bit)", "windowsGuest"),
    (16, "windows2022srvNext_64Guest", "Microsoft Windows Server 2022 (64-bit)", "Microsoft Windows Server 2022 (64-bit)", "windowsGuest"),
    (8, "windows9Server64Guest", "Microsoft Windows Server 2016 (64-bit)", "Microsoft Windows Server 2016 (64-bit)", "windowsGuest"),
    (4, "windows8Server64Guest", "Microsoft Windows Server 2012 R2 (64-bit)", "Microsoft Windows Server 2012 R2 (64-bit)", "windowsGuest"),
    (5, "windows9_64Guest", "Microsoft Windows 10 (64-bit)", "Microsoft Windows 10 (64-bit)", "windowsGuest"),
    (3, "windows11_64Guest", "Microsoft Windows 11 (64-bit)", "Microsoft Windows 11 (64-bit)", "windowsGuest"),
    (12, "rhel8_64Guest", "Red Hat Enterprise Linux 8 (64-bit)", "Red Hat Enterprise Linux 8.8 (Ootpa)", "linuxGuest"),
    (10, "ubuntu64Guest", "Ubuntu Linux (64-bit)", "Ubuntu 22.04.3 LTS", "linuxGuest"),
    (4, "oracleLinux8_64Guest", "Oracle Linux 8 (64-bit)", "Oracle Linux Server 8.9", "linuxGuest"),
    (4, "vmwarePhoton64Guest", "VMware Photon OS (64-bit)", "VMware Photon OS (64-bit)", "linuxGuest"),
    (3, "other4xLinux64Guest", "Other 4.x or later Linux (64-bit)", None, None),
    (2, "otherGuest64", "Other (64-bit)", None, None),
    (3, "windows2019srvNext_64Guest", "Microsoft Windows Server 2019 (64-bit)", None, None),  # tools not running → config evidence only
]


def _seeded_random(key: str) -> random.Random:
    seed = int(hashlib.sha256(key.encode()).hexdigest(), 16) % (2 ** 32)
    return random.Random(seed)


# Dell service tags are 7 characters from digits + consonant-heavy letters (no vowels → no words).
_TAG_ALPHABET = "0123456789BCDFGHJKLMNPQRSTVWXYZ"


def demo_service_tag(host_name: str) -> str:
    """Stable 7-character Dell-style service tag for a demo host (derived from the name only, so it does
    not consume the inventory's random stream). DC-asset / Opex demo rows can reuse it for a serial match."""
    n = int(hashlib.sha256(f"svctag/{host_name}".encode()).hexdigest(), 16)
    out = []
    for _ in range(7):
        n, r = divmod(n, len(_TAG_ALPHABET))
        out.append(_TAG_ALPHABET[r])
    return "".join(out)


def demo_host_identity(vcenter_key: str, platform: str, vendor: str, host_name: str) -> Dict[str, Any]:
    """serial / hwUuid as each platform's collector reports them: vCenter → service tag (Dell) or
    serial (HPE) + BIOS uuid; Nutanix → node serial, no uuid; Proxmox → nothing."""
    if platform == "proxmox":
        return {"serial": None, "hwUuid": None}
    tag = demo_service_tag(host_name)
    serial = tag if str(vendor).startswith("Dell") else f"CZJ{tag}"     # HPE serials: 10 characters "CZJ…"
    if platform == "nutanix":
        return {"serial": f"NX{tag}", "hwUuid": None}
    return {"serial": serial, "hwUuid": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{vcenter_key}/host/{host_name}"))}


# Owner sources of the demo (G2b): folder per project (weight, folder, System attribute, Owner attribute).
# About 1 VM in 4 also carries the "System" / "Owner" custom attributes; ~8 % stay in "Discovered virtual machine".
DEMO_PROJECTS = [
    (24, "/Projects/CoreBanking/App", "Core Banking", "lead.corebanking@hsc.example"),
    (10, "/Projects/CoreBanking/DB", "Core Banking", "lead.corebanking@hsc.example"),
    (14, "/Projects/Payments/Gateway", "Thanh toán", "lead.payments@hsc.example"),
    (8, "/Projects/Payments/Batch", "Thanh toán", "lead.payments@hsc.example"),
    (14, "/Projects/Web", "Website", "lead.web@hsc.example"),
    (12, "/Projects/DataWarehouse", "Kho dữ liệu", "lead.dwh@hsc.example"),
    (10, "/Shared/Internal", "Ứng dụng nội bộ", "lead.internal@hsc.example"),
    (8, "/Discovered virtual machine", None, None),
]
DEMO_POOLS = {"/Projects/DataWarehouse": "RP-Analytics", "/Projects/Web": "RP-Web"}


def demo_owner_sources(vcenter_key: str, vm_name: str, platform: str, infra_vm: bool) -> Dict[str, Any]:
    """folderPath / resourcePool / customAttrs of a demo VM, stable per name (own hash: the seeded inventory
    stream is not touched). Proxmox / Nutanix have no vCenter folders or custom attributes."""
    if platform != "vmware":
        return {"folderPath": None, "resourcePool": None, "customAttrs": {}}
    if infra_vm:
        return {"folderPath": "/Infrastructure/VMware", "resourcePool": None, "customAttrs": {}}
    h = int(hashlib.sha256(f"owner/{vcenter_key}/{vm_name}".encode()).hexdigest(), 16)
    pick, total = h % 100, 0
    for weight, folder, system, owner in DEMO_PROJECTS:
        total += weight
        if pick < total:
            break
    attrs: Dict[str, str] = {}
    if system and (h >> 8) % 4 == 0:
        attrs["System"] = system
    if owner and (h >> 12) % 5 == 0:
        attrs["Owner"] = owner
    return {"folderPath": folder, "resourcePool": DEMO_POOLS.get(folder), "customAttrs": attrs}


def collect_inventory(cfg: VCenterConfig) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    rnd = _seeded_random(cfg.key)
    # Slow time-based drift so repeated polls show gentle organic growth in the
    # trend charts instead of static flat lines.
    drift = (time.time() / 86400.0) % 30  # 0..30 "day" cycle

    host_rows: List[Dict[str, Any]] = []
    vm_rows: List[Dict[str, Any]] = []

    os_weights = [g[0] for g in GUEST_OS_PLAN]
    platform = getattr(cfg, "platform", "vmware")
    tag = {"proxmox": "pve", "nutanix": "ahv"}.get(platform, "esx")
    for cluster, host_count, cores_per_host, ram_per_host_gb, sockets, (vendor, model), (esxi_ver, esxi_build), cpu_model in CLUSTER_PLAN:
        if platform == "proxmox":
            esxi_ver, esxi_build = "Proxmox VE 8.2.4", "faa83925c9641325"
        elif platform == "nutanix":
            (vendor, model), (esxi_ver, esxi_build) = ("Nutanix", "NX-3170-G8"), ("AHV 10.0 · AOS 6.10", "6.10")
        for hi in range(host_count):
            host_name = f"{cfg.key}-{cluster}-{tag}{hi + 1:02d}"
            host_rows.append({
                "cluster": cluster,
                "host": host_name,
                "cores": float(cores_per_host),
                "cpuMhz": 2400.0,
                "ramGB": float(ram_per_host_gb),
                "cpuUsagePctRaw": round(20 + rnd.random() * 40, 1),
                "ramUsagePctRaw": round(25 + rnd.random() * 45, 1),
                "status": "connected",
                "vendor": vendor, "model": model, "esxiVersion": esxi_ver, "esxiBuild": esxi_build,
                "cpuModel": cpu_model, "cpuSockets": sockets, "cpuThreads": cores_per_host * 2,
                "clusterHa": True, "clusterDrs": cluster != "DR-CLUSTER",
                **demo_host_identity(cfg.key, platform, vendor, host_name),
            })

        vm_count = int(host_count * rnd.uniform(6, 10))
        for vi in range(vm_count):
            host_name = f"{cfg.key}-{cluster}-{tag}{rnd.randint(1, host_count):02d}"
            is_on = rnd.random() > 0.08
            vcpu = rnd.choice([2, 2, 4, 4, 4, 8, 8, 16])
            ram_gb = rnd.choice([4, 8, 8, 16, 16, 32, 64])
            # Allocation (vCPU/RAM) stays fixed per VM like a real vCenter — any change
            # there is a genuine resize. The slow drift only nudges *usage* so trend
            # charts still move gently between polls.
            growth = 1 + (drift / 30.0) * 0.15  # up to +15% usage growth over the cycle
            cpu_usage = max(0.5, rnd.gauss(18, 14) * growth) if is_on else 0
            idle_days = round(rnd.uniform(0, 75), 1) if is_on and cpu_usage < 8 else None
            power_off_days = round(rnd.uniform(1, 180), 1) if not is_on else None
            prov_gb = round(ram_gb * rnd.uniform(2, 6), 1)
            vm_name = f"{cluster.lower()}-vm-{vi + 1:03d}"
            _, guest_id, guest_full, tools_full, tools_family = rnd.choices(GUEST_OS_PLAN, weights=os_weights)[0]
            tools_running = is_on and tools_full is not None
            if guest_id == "vmwarePhoton64Guest":
                vm_name = f"{cluster.lower()}-{rnd.choice(['vcsa', 'vrops', 'nsx-mgr', 'vrli'])}-{vi + 1:02d}"
            vm_rows.append({
                "cluster": cluster,
                "host": host_name,
                "vm": vm_name,
                # Stable per VM (not per cluster/host) like vCenter's instanceUuid.
                "instanceUuid": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{cfg.key}/{vm_name}")),
                "powerState": "Powered On" if is_on else "Powered Off",
                "isPoweredOn": is_on,
                "ipAddress": f"10.{rnd.randint(10,60)}.{rnd.randint(0,255)}.{rnd.randint(2,254)}",
                "vcpu": float(vcpu),
                "ramGB": float(ram_gb),
                "diskProvGB": prov_gb,
                "diskUsedGB": round(prov_gb * rnd.uniform(0.3, 0.85), 1),
                "datastore": f"DS-{cluster}-{rnd.randint(1,3):02d}",
                "cpuUsagePctRaw": round(cpu_usage, 1) if is_on else 0.0,
                "ramUsagePctRaw": round(min(98.0, max(2, rnd.gauss(35, 15) * growth)), 1) if is_on else 0.0,
                "lastActivityDays": idle_days,
                "powerOffDays": power_off_days,
                "guestId": guest_id,
                "guestFullName": guest_full,
                "toolsGuestFullName": tools_full if tools_running else None,
                "toolsGuestFamily": tools_family if tools_running else None,
                "toolsStatus": "guestToolsRunning" if tools_running else ("guestToolsNotRunning" if is_on else "guestToolsNotRunning"),
                "guestHostname": (vm_name.upper() if tools_running else None),
                **demo_owner_sources(cfg.key, vm_name, platform, guest_id == "vmwarePhoton64Guest"),
            })

    return host_rows, vm_rows


# ---------------------------------------------------------------- operational extras (vcenter/extras.py shape)
DEMO_NFS_HOST = "10.20.0.11"            # data LIF of the demo NetApp SVM


def demo_backing(vc_key: str, ds_name: str, ds_type: str) -> Dict[str, Any]:
    """Storage correlation keys of a demo datastore, built the way the arrays expose them (storage/demo_generator
    builds the matching volumes): VMFS on Pure → naa.624a9370 + serial, on NetApp → naa.600a0980 + hex(serial)."""
    h = hashlib.sha1(f"{vc_key}/{ds_name}".encode()).hexdigest()
    if ds_type == "NFS":
        return {"naa": [], "nfsHost": DEMO_NFS_HOST, "nfsPath": "/" + ds_name.lower().replace("-", "_")}
    if int(h[-1], 16) % 3:                       # ~2/3 on Pure, 1/3 on NetApp
        return {"naa": ["naa.624a9370" + h[:24]], "nfsHost": None, "nfsPath": None}
    return {"naa": ["naa.600a0980" + h[:12].upper().encode().hex()], "nfsHost": None, "nfsPath": None}


def collect_extras(cfg: VCenterConfig, host_rows: List[Dict[str, Any]], vm_rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Datastores sized from the demo VMs (one nearly full and growing, one at the watch level), VM snapshots
    of various ages (incl. short-lived backup snapshots), a few triggered alarms and two hosts with sensor issues."""
    import datetime as _dt
    rnd = _seeded_random(cfg.key + "/extras")
    drift = (time.time() / 86400.0) % 30
    now = _dt.datetime.utcnow().replace(microsecond=0)
    iso = lambda t: t.isoformat() + "Z"  # noqa: E731
    by_ds: Dict[str, Dict[str, Any]] = {}
    for v in vm_rows:
        d = by_ds.setdefault(v["datastore"], {"used": 0.0, "prov": 0.0, "vms": 0, "cluster": v["cluster"]})
        d["used"] += v["diskUsedGB"]
        d["prov"] += v["diskProvGB"]
        d["vms"] += 1
    hosts_by_cluster: Dict[str, int] = {}
    for h in host_rows:
        hosts_by_cluster[h["cluster"]] = hosts_by_cluster.get(h["cluster"], 0) + 1
    datastores = []
    order = sorted(by_ds, key=lambda n: -by_ds[n]["used"])          # the busiest datastores carry the story
    full_name, watch_name = (order + [None, None])[:2]
    for i, (name, d) in enumerate(sorted(by_ds.items())):
        # target fill: the busiest datastore nearly full and growing with the drift cycle, the next at ~86 %
        fill = 0.905 + drift * 0.0015 if name == full_name else 0.86 if name == watch_name else rnd.uniform(0.45, 0.78)
        cap = round(d["used"] / fill, 1)
        ds_type = "NFS" if i % 4 == 3 else "VMFS"
        datastores.append({
            "key": f"datastore-{1000 + i}", "name": name, "type": ds_type, **demo_backing(cfg.key, name, ds_type),
            "capacityGB": cap, "freeGB": round(max(0.0, cap - d["used"]), 1), "uncommittedGB": round(max(0.0, d["prov"] - d["used"]), 1),
            "accessible": True, "maintenance": "normal", "clusters": [d["cluster"]],
            "hostCount": hosts_by_cluster.get(d["cluster"], 0), "vmCount": d["vms"],
        })
    snapshots = []
    for v in vm_rows:
        if rnd.random() > 0.09:
            continue
        kind = rnd.random()
        if kind < 0.3:      # backup software snapshot, minutes old
            items = [{"name": "NBU_SNAPSHOT " + v["vm"], "createdAt": iso(now - _dt.timedelta(minutes=rnd.randint(5, 90))),
                      "description": "NetBackup snapshot", "quiesced": True, "depth": 0}]
        else:
            n = 1 if kind < 0.75 else rnd.randint(2, 4)
            age0 = rnd.choice([0.6, 2.5, 5, 12, 26, 48])
            items = [{"name": f"truoc-nang-cap-{k + 1}", "createdAt": iso(now - _dt.timedelta(days=age0 - k * 0.4)),
                      "description": "Snapshot trước khi vá" if k == 0 else "", "quiesced": False, "depth": k} for k in range(n)]
        times = sorted(i["createdAt"] for i in items)
        snapshots.append({"vm": v["vm"], "instanceUuid": v.get("instanceUuid"), "cluster": v["cluster"], "host": v.get("host"),
                          "count": len(items), "oldestAt": times[0], "newestAt": times[-1],
                          "sizeGB": round(v["diskUsedGB"] * rnd.uniform(0.02, 0.6), 1), "items": items})
    full = next((x for x in datastores if x["name"] == full_name), None)
    alarms = []
    if full:
        alarms.append({"key": "alarm-ds-1", "entityType": "datastore", "entity": full["name"], "cluster": full["clusters"][0],
                       "alarm": "Datastore usage on disk", "status": "red", "at": iso(now - _dt.timedelta(hours=5)), "acknowledged": False})
    if host_rows:
        h = host_rows[min(1, len(host_rows) - 1)]
        alarms.append({"key": "alarm-host-1", "entityType": "host", "entity": h["host"], "cluster": h["cluster"],
                       "alarm": "Host memory usage", "status": "yellow", "at": iso(now - _dt.timedelta(hours=30)), "acknowledged": False})
        h2 = host_rows[-1]
        alarms.append({"key": "alarm-host-2", "entityType": "host", "entity": h2["host"], "cluster": h2["cluster"],
                       "alarm": "Host hardware power status", "status": "red", "at": iso(now - _dt.timedelta(hours=2)), "acknowledged": False})
    if vm_rows:
        v = vm_rows[3 % len(vm_rows)]
        alarms.append({"key": "alarm-vm-1", "entityType": "vm", "entity": v["vm"], "cluster": v["cluster"],
                       "alarm": "Virtual machine CPU usage", "status": "yellow", "at": iso(now - _dt.timedelta(minutes=40)), "acknowledged": True})
    health = []
    for idx, h in enumerate(host_rows):
        issues = []
        if idx == len(host_rows) - 1:
            issues.append({"kind": "power", "name": "Power Supply 2 Status: Failure detected", "state": "red", "reading": None})
        if idx == 2:
            issues.append({"kind": "fan", "name": "Fan 4 RPM", "state": "yellow", "reading": 1680})
        health.append({"host": h["host"], "cluster": h["cluster"],
                       "overallStatus": "red" if any(i["state"] == "red" for i in issues) else "yellow" if issues else "green",
                       "connection": "connected", "maintenance": False, "issues": issues})
    return {"datastores": datastores, "snapshots": snapshots, "alarms": alarms, "health": health}
