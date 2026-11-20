"""Rebuild the CI registry from what ICO already stores (latest inventory, storage, backup, DC assets, Opex).

Runs on the leader after each sync round (collectors/scheduler.py) — reads the database only, never a source.
Set-based: the whole desired graph is computed in memory, then registry.py writes the diff in a few bulk
statements per table (one short transaction each). Two builds in a row change nothing.

Types and identities (source_key):
  vm               instanceUuid (fallback "<vcenter>:<name>")          keys instance_uuid, short_name, fqdn, ip
  hypervisor_host  "<vcenter>:<host>"                                  keys fqdn, short_name, ip, serial, hw_uuid
  cluster          "<vcenter>:<cluster>"
  physical_server  norm_serial(serial)  (host serial / Dell OME device / DC asset server / Opex hardware warranty)
                                                                       keys serial, idrac_ip (OME)
  datastore        "<vcenter>:<ds_key>"                                keys naa
  storage_array    array source key;  storage_volume "<array>:<container>:<name>"   keys naa
  backup_client    "<nbu>:<client key>"                                key nbu_client
  dc_asset         dc_assets.asset_key                                 keys serial, asset_tag
  opex_item        Opex code                                           key opex_code
"""
import datetime as dt
import json
import logging
import threading
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from .. import data_access, metrics
from ..config import get_settings
from ..models import utcnow
from ..shared.identity import nbu_client_key, norm_fqdn, norm_ip, norm_naa, norm_serial, short_name
from . import registry
from .registry import Identity

logger = logging.getLogger("vco.cmdb")

CLIENT_WINDOW_DAYS = 35          # backup clients: jobs of the last 35 days (monthly fulls) + active policy clients
MAX_CLIENTS = 20000
_lock = threading.Lock()         # one build per process (the scheduler and a test / admin call never overlap)


class Graph:
    """Desired state of one build: CIs, their keys and the links between them (by identity)."""

    def __init__(self) -> None:
        self.cis: Dict[Identity, Dict[str, Any]] = {}
        self.keys: Dict[Identity, Set[Tuple[str, str]]] = defaultdict(set)
        self.links: Dict[Tuple[Identity, Identity, str], Dict[str, Any]] = {}

    def ci(self, type_: str, source_kind: str, source_key: str, name: str, attrs: Optional[Dict[str, Any]] = None) -> Identity:
        ident = (type_, source_kind, str(source_key)[:255])
        cur = self.cis.get(ident)
        if cur is None:
            self.cis[ident] = {"name": name, "attrs": {k: v for k, v in (attrs or {}).items() if v not in (None, "")}}
        else:                                           # several sources describe the same CI (physical_server)
            for k, v in (attrs or {}).items():
                if v not in (None, "") and k not in cur["attrs"]:
                    cur["attrs"][k] = v
        return ident

    def key(self, ident: Identity, kind: str, value: Optional[str]) -> None:
        if value:
            self.keys[ident].add((kind, str(value)[:255]))

    def link(self, a: Identity, b: Identity, relation: str, source: str, confidence: str,
             attrs: Optional[Dict[str, Any]] = None) -> None:
        if a == b:
            return
        lid = (a, b, relation)
        cur = self.links.get(lid)
        if cur is None or (cur["confidence"] == "weak" and confidence == "strong"):
            self.links[lid] = {"source": source, "confidence": confidence, "attrs": attrs or {}}


def _physical(g: Graph, serial: Optional[str], origin: str, vendor: Optional[str] = None, model: Optional[str] = None) -> Optional[Identity]:
    sk = norm_serial(serial)
    if not sk:
        return None
    ident = g.ci("physical_server", "hardware", sk, sk, {"vendor": vendor, "model": model})
    origins = set(g.cis[ident]["attrs"].get("origins") or [])
    g.cis[ident]["attrs"]["origins"] = sorted(origins | {origin})
    g.key(ident, "serial", sk)
    return ident


def _name_keys(g: Graph, ident: Identity, name: Optional[str]) -> None:
    n = norm_fqdn(name)
    if not n:
        return
    ip = norm_ip(n)
    if ip:
        g.key(ident, "ip", ip)
        return
    g.key(ident, "short_name", short_name(n))
    if "." in n:
        g.key(ident, "fqdn", n)


# ---------------------------------------------------------------- sources
def _fresh_vcenters(db: Session, now: dt.datetime, days: int) -> Dict[str, str]:
    """Configured vCenter / Proxmox / Nutanix sources whose latest good run is recent enough: key → platform.
    A source down longer than the retire window stops refreshing its CIs, which then retire."""
    out = {}
    for cfg in get_settings().vcenters():
        run = data_access.latest_run_for_key(db, cfg.key, source="live")
        if run is not None and (run.finished_at or run.started_at or now) >= now - dt.timedelta(days=days):
            out[cfg.key] = getattr(cfg, "platform", "vmware") or "vmware"
    return out


def _datastores(db: Session, g: Graph, vcs: Dict[str, str]) -> Dict[Tuple[str, str], Identity]:
    """Datastore CIs + backed_by links to the array volume (infra.py correlation). Returns (vcenter, name) → CI."""
    from .. import infra
    from ..models import DatastoreState, StorageVolumeState
    by_naa, by_nfs, ips, arrays = infra._correlation(db)       # configured arrays only, same matching as tab Storage
    vol_ci: Dict[Tuple[str, str, str], Identity] = {}
    for src, a in arrays.items():
        g.ci("storage_array", "storage", src, a.name or src, {"vendor": a.vendor, "model": a.model})
    for v in db.query(StorageVolumeState).filter(StorageVolumeState.source_key.in_(list(arrays) or [""])):
        ident = g.ci("storage_volume", "storage", f"{v.source_key}:{v.container or ''}:{v.name}", v.name,
                     {"array": arrays[v.source_key].name if v.source_key in arrays else v.source_key, "kind": v.kind,
                      "container": v.container})
        g.key(ident, "naa", norm_naa(v.naa))
        g.link(ident, ("storage_array", "storage", v.source_key), "on", "storage", "strong")
        vol_ci[(v.source_key, v.container or "", v.name)] = ident
    by_name: Dict[Tuple[str, str], List[Identity]] = defaultdict(list)
    for r in db.query(DatastoreState).filter(DatastoreState.vcenter_key.in_(list(vcs) or [""])):
        ident = g.ci("datastore", "vcenter", f"{r.vcenter_key}:{r.ds_key}", r.name,
                     {"vcenterKey": r.vcenter_key, "dsType": r.ds_type})
        b = json.loads(r.backing) if r.backing else {}
        naas = [n for n in (b.get("naa") or []) if n]
        for n in naas:
            g.key(ident, "naa", norm_naa(n))
        by_name[(r.vcenter_key, r.name)].append(ident)
        vol = infra._match(r.backing, by_naa, by_nfs, ips) if arrays else None
        if vol is not None:
            strong = any(n in by_naa for n in naas)
            g.link(ident, vol_ci[(vol.source_key, vol.container or "", vol.name)], "backed_by", "storage",
                   "strong" if strong else "weak", {"matchedBy": "naa" if strong else "nfs_path"})
    return {k: v[0] for k, v in by_name.items() if len(v) == 1}       # a name used twice in one vCenter: no link


def _inventory(db: Session, g: Graph, vcs: Dict[str, str], ds_by_name: Dict[Tuple[str, str], Identity]
               ) -> Tuple[List[Dict[str, Any]], Dict[Tuple[str, str], Identity], Dict[Tuple[str, str], Identity]]:
    hosts, vms, _ = data_access.get_latest_combined(db, list(vcs), include_import=False)
    host_ci: Dict[Tuple[str, str], Identity] = {}
    for h in hosts:
        vc = h.get("vcenterKey") or ""
        platform = vcs.get(vc, "vmware")
        ident = g.ci("hypervisor_host", "vcenter", f"{vc}:{h['host']}", h["host"],
                     {"vcenterKey": vc, "cluster": h.get("cluster"), "platform": platform, "vendor": h.get("vendor"),
                      "model": h.get("model")})
        _name_keys(g, ident, h["host"])
        g.key(ident, "serial", norm_serial(h.get("serial")))
        g.key(ident, "hw_uuid", str(h.get("hwUuid") or "").strip().lower()[:64])
        host_ci[(vc, h["host"])] = ident
        if h.get("cluster"):
            cl = g.ci("cluster", "vcenter", f"{vc}:{h['cluster']}", h["cluster"], {"vcenterKey": vc, "platform": platform})
            g.link(ident, cl, "member_of", "vcenter", "strong")
        phys = _physical(g, h.get("serial"), "host", h.get("vendor"), h.get("model"))
        if phys:
            g.link(ident, phys, "runs_on", "vcenter", "strong", {"matchedBy": "serial"})
    vm_ci: Dict[Tuple[str, str], Identity] = {}
    used_uuid: Set[str] = set()
    for v in vms:
        vc = v.get("vcenterKey") or ""
        uuid = str(v.get("instanceUuid") or "").strip().lower()
        key = uuid if uuid and uuid not in used_uuid else f"{vc}:{uuid or v['vm']}"
        used_uuid.add(uuid)
        ident = g.ci("vm", "vcenter", key, v["vm"],
                     {"vcenterKey": vc, "cluster": v.get("cluster"), "host": v.get("host"), "platform": vcs.get(vc, "vmware"),
                      "powerOn": bool(v.get("isPoweredOn")), "vcpu": v.get("vcpu"), "ramGB": v.get("ramGB"),
                      # owner sources (G2b), read by owners/rules.py
                      "folderPath": v.get("folderPath"), "resourcePool": v.get("resourcePool"),
                      "customAttrs": v.get("customAttrs") or None})
        g.key(ident, "instance_uuid", uuid)
        g.key(ident, "short_name", short_name(v["vm"]) if not norm_ip(v["vm"]) else None)
        _name_keys(g, ident, v.get("guestHostname"))
        g.key(ident, "ip", norm_ip(v.get("ipAddress")))
        vm_ci[(vc, v["vm"])] = ident
        if (vc, v.get("host")) in host_ci:
            g.link(ident, host_ci[(vc, v["host"])], "hosted_on", "vcenter", "strong")
        ds = ds_by_name.get((vc, v.get("datastore") or ""))
        if ds:
            g.link(ident, ds, "stored_on", "vcenter", "strong")
    return vms, host_ci, vm_ci


def _backup(db: Session, g: Graph, now: dt.datetime, vms: List[Dict[str, Any]], vm_ci: Dict[Tuple[str, str], Identity]) -> None:
    from ..models import BackupJob, BackupPolicySnapshot, BackupSyncRun
    nbus = [c.key for c in get_settings().netbackups()]
    if not nbus:
        return
    names: Dict[Tuple[str, str], str] = {}
    since = now - dt.timedelta(days=CLIENT_WINDOW_DAYS)
    rows = (db.query(BackupJob.nbu_key, BackupJob.client_key, BackupJob.client_name)
            .filter(BackupJob.nbu_key.in_(nbus), BackupJob.start_time >= since, BackupJob.client_key.isnot(None))
            .distinct().limit(MAX_CLIENTS))
    for nbu, ck, name in rows:
        if not ck:
            continue
        cur = names.get((nbu, ck))
        if cur is None or (name and name < cur):           # deterministic display name: smallest spelling
            names[(nbu, ck)] = name or ck
    for nbu in nbus:
        run = (db.query(BackupSyncRun.id).filter(BackupSyncRun.nbu_key == nbu, BackupSyncRun.status == "ok",
                                                 BackupSyncRun.policy_count > 0)
               .order_by(BackupSyncRun.started_at.desc()).first())
        if run is None:
            continue
        for (clients, active) in db.query(BackupPolicySnapshot.clients_json, BackupPolicySnapshot.active).filter(
                BackupPolicySnapshot.run_id == run.id):
            if active is False:
                continue
            for c in json.loads(clients or "[]"):
                ck = nbu_client_key(c)
                if ck and (nbu, ck) not in names:
                    names[(nbu, ck)] = str(c)
    by_key: Dict[str, List[Identity]] = defaultdict(list)
    for (nbu, ck), name in sorted(names.items()):
        ident = g.ci("backup_client", "netbackup", f"{nbu}:{ck}", name, {"nbuKey": nbu})
        g.key(ident, "nbu_client", ck)
        by_key[ck].append(ident)
    if not by_key:
        return
    for v in vms:
        ident = vm_ci.get((v.get("vcenterKey") or "", v["vm"]))
        if ident is None:
            continue
        done: Set[Identity] = set()
        for value, how in ((nbu_client_key(v["vm"]), "vm_name"), (nbu_client_key(v.get("guestHostname")), "guest_hostname"),
                           (norm_ip(v.get("ipAddress")), "ip")):
            for client in by_key.get(value or "", []):
                if client not in done:
                    g.link(ident, client, "protected_by", "netbackup", "weak", {"matchedBy": how})
                    done.add(client)


def _hardware(db: Session, g: Graph, now: dt.datetime, days: int) -> None:
    """Dell OME servers (configured sources, synced within the retire window) → physical_server by service tag.
    A device matching no host is still a physical_server (bare-metal), named after its OME device name."""
    from ..hardware.models import HwDeviceState
    keys = [c.key for c in get_settings().hardware()]
    if not keys:
        return
    for d in db.query(HwDeviceState).filter(HwDeviceState.source_key.in_(keys),
                                            HwDeviceState.captured_at >= now - dt.timedelta(days=days)):
        ident = _physical(g, d.service_tag, "ome", "Dell Inc.", d.model)
        if ident is None:
            continue
        attrs = g.cis[ident]["attrs"]
        attrs.update({k: v for k, v in {"omeSource": d.source_key, "omeDeviceId": d.device_id, "omeName": d.name,
                                         "idracIp": d.idrac_ip, "hwStatus": d.status, "powerState": d.power_state}.items()
                      if v not in (None, "")})
        if not attrs.get("model") and d.model:
            attrs["model"] = d.model
        if attrs.get("origins") == ["ome"] and d.name:
            g.cis[ident]["name"] = d.name[:255]              # bare-metal: the OME name says more than the tag
        g.key(ident, "idrac_ip", norm_ip(d.idrac_ip))


def _dc_assets(db: Session, g: Graph) -> None:
    from ..models import DcAsset
    for a in db.query(DcAsset).filter(DcAsset.removed.is_(False)):
        ident = g.ci("dc_asset", "dc_asset", a.asset_key, a.device_id or a.serial or a.asset_key,
                     {"category": a.category, "brand": a.brand, "model": a.model, "location": a.location, "env": a.env})
        g.key(ident, "serial", a.serial_key or norm_serial(a.serial))
        g.key(ident, "asset_tag", norm_serial(a.asset_tag))
        if a.category == "server":
            phys = _physical(g, a.serial_key or a.serial, "dc_asset", a.brand, a.model)
            if phys:
                g.link(ident, phys, "records", "dc_asset", "strong", {"matchedBy": "serial"})


def _opex(db: Session, g: Graph, host_ci: Dict[Tuple[str, str], Identity]) -> None:
    from ..spend.models import OpexAsset, OpexItem, SpendCategory
    rows = (db.query(OpexItem.code, OpexItem.title, OpexItem.status, OpexItem.end_date, SpendCategory.grp,
                     OpexAsset.serial, OpexAsset.vcenter_key, OpexAsset.host_name)
            .join(OpexAsset, OpexAsset.opex_id == OpexItem.id).join(SpendCategory, SpendCategory.id == OpexItem.category_id))
    by_vc_host: Dict[Tuple[str, str], Identity] = {}
    by_host: Dict[str, List[Identity]] = defaultdict(list)
    for (vc, host), ident in host_ci.items():
        by_vc_host[(vc, host.lower())] = ident
        by_host[host.lower()].append(ident)
    for code, title, status, end, grp, serial, vc, host in rows:
        ident = g.ci("opex_item", "opex", code, title or code,
                     {"code": code, "status": status, "endDate": end.isoformat() if end else None, "group": grp})
        g.key(ident, "opex_code", code)
        sk = norm_serial(serial)
        phys = ("physical_server", "hardware", sk) if sk else None
        if sk and (phys in g.cis or grp == "hw_warranty"):      # only hardware warranties create a server
            phys = _physical(g, sk, "opex")
        if phys in g.cis:
            g.link(ident, phys, "covers", "opex", "strong", {"matchedBy": "serial"})
        elif host:
            hn = host.strip().lower()
            hit = by_vc_host.get((vc, hn)) if vc else None
            if hit is None and not vc and len(by_host.get(hn, [])) == 1:       # no vCenter given: only when unambiguous
                hit = by_host[hn][0]
            if hit:
                g.link(ident, hit, "covers", "opex", "weak", {"matchedBy": "host_name"})


# ---------------------------------------------------------------- build
def collect(db: Session, now: dt.datetime, retire_days: int) -> Graph:
    g = Graph()
    vcs = _fresh_vcenters(db, now, retire_days)
    ds_by_name = _datastores(db, g, vcs)
    vms, host_ci, vm_ci = _inventory(db, g, vcs, ds_by_name)
    _backup(db, g, now, vms, vm_ci)
    _hardware(db, g, now, retire_days)
    _dc_assets(db, g)
    _opex(db, g, host_ci)
    return g


def build_all(db: Session, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """Rebuild the registry; returns counters (also logged and recorded in metric_points kind "cmdb")."""
    with _lock:
        started = time.monotonic()
        now = now or utcnow()
        days = max(1, int(get_settings().cmdb_retire_days))
        g = collect(db, now, days)
        ids, ci_n = registry.upsert_cis(db, g.cis, now)
        db.commit()
        key_n = registry.set_keys(db, {ids[i]: k for i, k in g.keys.items() if i in ids} |
                                  {cid: set() for i, cid in ids.items() if i not in g.keys})
        db.commit()
        links = {(ids[a], ids[b], rel): d for (a, b, rel), d in g.links.items() if a in ids and b in ids}
        link_n = registry.set_links(db, links, now)
        db.commit()
        retired = registry.retire_unseen(db, now, days)
        db.commit()
        from ..owners.service import invalidate_scope_cache
        invalidate_scope_cache()                        # VM names / uuids of the owner filter may have changed
        seconds = time.monotonic() - started
        out = {"cis": len(ids), "links": len(links), "ciInserted": ci_n["inserted"], "ciUpdated": ci_n["updated"],
               "keysInserted": key_n["inserted"], "keysDeleted": key_n["deleted"], "linksInserted": link_n["inserted"],
               "linksDeleted": link_n["deleted"], "retired": retired, "seconds": round(seconds, 3)}
        metrics.record(db, "cmdb", [("build", "seconds", seconds), ("build", "cis", len(ids)), ("build", "links", len(links))], at=now)
        db.commit()
        return out
