"""Tổng kết năm + kế hoạch năm sau (ICO v2 G8): reads the database, the arithmetic is in planning.py (pure, tested).

build(db, year) is the one source of numbers for the page (Nhu cầu hạ tầng → Tổng kết năm / Kế hoạch năm sau), the PDF, the
Excel and the "capacity" line of the budget plan:
  summary     requests of `year` (Vietnam calendar year), counted ones only (parsed or reviewed): new VMs + reconfiguration change
  fulfilment  VMs created by a request (strong link G7c): requested → configuration when it appeared → used 90 / 180 days later
              (configured × average usage % of the vm_snapshots over 7 days from that day)
  plan        year + 1: natural growth of the last 12 months (history, minus the request VMs) + known demand (counted VM requests
              of the last 12 months not provisioned yet + projects typed for next year, × the "dùng thật / xin" factor when on)
              − committed reclaims (review G3: "Thu hồi" not done, "Giảm cấu hình" not applied) → hosts of the standard model.
"""
import datetime as dt
import json
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from .. import planning
from ..sizing import HostProfile
from .models import ItsmRequest, ItsmRequestCi, ItsmRequestLine, ItsmYearPlan

VN = dt.timedelta(hours=7)
WINDOW = dt.timedelta(days=7)          # usage = average over 7 days from day 90 / 180
HORIZONS = (90, 180)
MAX_PROJECTS = 200
DEFAULT_PARAMS = {"useFactor": True, "sockets": 2, "coresPerSocket": 16, "ramGB": 512, "unitPrice": None, "targetPct": 80.0,
                  "defaultCluster": ""}
CLOSED_WORDS = ("cancel", "reject", "huỷ", "hủy", "từ chối")


def _counted_requests(db: Session, kinds, since: dt.datetime, until: Optional[dt.datetime] = None) -> List[ItsmRequest]:
    from .process import counted
    q = counted(db.query(ItsmRequest)).filter(ItsmRequest.kind.in_(kinds), ItsmRequest.created_at >= since)
    if until is not None:
        q = q.filter(ItsmRequest.created_at < until)
    return q.all()


def _lines(db: Session, ids: List[int]) -> Dict[int, List[ItsmRequestLine]]:
    out: Dict[int, List[ItsmRequestLine]] = defaultdict(list)
    for i in range(0, len(ids), 500):
        for ln in db.query(ItsmRequestLine).filter(ItsmRequestLine.request_id.in_(ids[i:i + 500])).order_by(ItsmRequestLine.line_no):
            out[ln.request_id].append(ln)
    return out


def _size(ln: ItsmRequestLine, kind: str) -> Dict[str, float]:
    """Resources of a line × quantity: a new VM its size, a reconfiguration its change (fields with As-is only)."""
    q = ln.qty or 1
    out = {}
    for key, f in (("vcpu", "vcpu"), ("ramGB", "ram_gb"), ("diskGB", "disk_gb")):
        new, old = getattr(ln, f), getattr(ln, f + "_before")
        out[key] = (new or 0.0) * q if kind == "new_vm" else ((new - old) * q if new is not None and old is not None else 0.0)
    return out


def request_lines(db: Session, year: int) -> List[Dict[str, Any]]:
    start = dt.datetime(year, 1, 1) - VN
    reqs = _counted_requests(db, ("new_vm", "resize"), start, dt.datetime(year + 1, 1, 1) - VN)
    lines = _lines(db, [r.id for r in reqs])
    out = []
    for r in reqs:
        for ln in lines[r.id]:
            out.append({"requestId": r.id, "kind": r.kind, "createdAt": r.created_at + VN,
                        "resolvedAt": r.resolved_at + VN if r.resolved_at else None, "department": r.department,
                        "project": ln.project or r.project, "env": ln.env, "vms": (ln.qty or 1) if r.kind == "new_vm" else 0,
                        **_size(ln, r.kind)})
    return out


# ---------------------------------------------------------------- requested → provisioned → used
def _vm_windows(db: Session, wanted: List[Tuple[str, str, dt.datetime]]) -> Dict[Tuple[str, str, dt.datetime], List[tuple]]:
    """Snapshot rows (vcpu, ram, cpu %, ram %) of each (vCenter, VM) in [start, start + 7 days), oldest first."""
    from ..models import VmSnapshot
    out: Dict[Tuple[str, str, dt.datetime], List[tuple]] = defaultdict(list)
    for i in range(0, len(wanted), 100):
        part = wanted[i:i + 100]
        conds = [and_(VmSnapshot.vcenter_key == vc, VmSnapshot.vm == vm, VmSnapshot.captured_at >= t, VmSnapshot.captured_at < t + WINDOW)
                 for vc, vm, t in part]
        rows = (db.query(VmSnapshot.vcenter_key, VmSnapshot.vm, VmSnapshot.captured_at, VmSnapshot.vcpu, VmSnapshot.ram_gb,
                         VmSnapshot.cpu_usage_pct_raw, VmSnapshot.ram_usage_pct_raw)
                .filter(or_(*conds)).order_by(VmSnapshot.captured_at).all())
        for vc, vm, at, vcpu, ram, cpu_pct, ram_pct in rows:
            for key in part:
                if key[0] == (vc or "") and key[1] == vm and key[2] <= at < key[2] + WINDOW:
                    out[key].append((vcpu, ram, cpu_pct, ram_pct))
    return out


def _used(rows: List[tuple]) -> Optional[Dict[str, float]]:
    cpu = [v * p / 100.0 for v, _r, p, _q in rows if v and p is not None]
    ram = [r * q / 100.0 for _v, r, _p, q in rows if r and q is not None]
    if not cpu and not ram:
        return None
    return {"vcpu": round(sum(cpu) / len(cpu), 2) if cpu else 0.0, "ramGB": round(sum(ram) / len(ram), 2) if ram else 0.0}


def fulfilment_items(db: Session, since: dt.datetime, now: dt.datetime) -> List[Dict[str, Any]]:
    """VMs created by counted new-VM requests since `since` (strong links), with what they were given and what they use."""
    from ..cmdb.models import Ci
    from .link import appeared
    rows = (db.query(ItsmRequestCi.ci_id, ItsmRequest, ItsmRequestLine, Ci.name, Ci.attrs)
            .join(ItsmRequest, ItsmRequest.id == ItsmRequestCi.request_id)
            .join(ItsmRequestLine, and_(ItsmRequestLine.request_id == ItsmRequestCi.request_id, ItsmRequestLine.line_no == ItsmRequestCi.line_no))
            .join(Ci, Ci.id == ItsmRequestCi.ci_id)
            .filter(ItsmRequestCi.match == "strong", ItsmRequest.kind == "new_vm", ItsmRequest.created_at >= since).all())
    vms = {cid: (json.loads(attrs or "{}").get("vcenterKey") or "", name) for cid, _r, _l, name, attrs in rows}
    seen = appeared(db, vms, sorted(vms))
    wanted, items = [], []
    for cid, r, ln, name, attrs in rows:
        at = seen.get(cid)
        if at is None:
            continue
        vc = vms[cid][0]
        keys = {h: (vc, name, at + dt.timedelta(days=h)) for h in (0,) + HORIZONS}
        wanted += [k for h, k in keys.items() if k[2] <= now]
        items.append({"ciId": cid, "vm": name, "vcenterKey": vc, "cluster": json.loads(attrs or "{}").get("cluster"),
                      "requestId": r.id, "reqId": r.req_id, "department": r.department, "appearedAt": at, "_keys": keys,
                      "reqVcpu": ln.vcpu or 0.0, "reqRamGB": ln.ram_gb or 0.0})
    snaps = _vm_windows(db, sorted(set(wanted)))
    for it in items:
        keys = it.pop("_keys")
        first = snaps.get(keys[0]) or []
        it["provVcpu"], it["provRamGB"] = (first[0][0] or 0.0, first[0][1] or 0.0) if first else (None, None)
        for h in HORIZONS:
            it[f"used{h}"] = _used(snaps.get(keys[h]) or [])
    return items


# ---------------------------------------------------------------- next year: inputs
def _year_plan(db: Session, year: int) -> Optional[ItsmYearPlan]:
    return db.query(ItsmYearPlan).filter(ItsmYearPlan.year == year).first()


def inputs(db: Session, year: int) -> Dict[str, Any]:
    p = _year_plan(db, year)
    params = {**DEFAULT_PARAMS, **(json.loads(p.params) if p else {})}
    return {"params": params, "projects": json.loads(p.projects) if p else [],
            "pushed": json.loads(p.pushed) if p and p.pushed else None,
            "pushedAt": p.pushed_at.isoformat() + "Z" if p and p.pushed_at else None, "pushedBy": p.pushed_by if p else None}


def _num(v: Any, lo: float, hi: float, label: str, integer: bool = False) -> Optional[float]:
    from ..shared.text import parse_number
    if v in (None, ""):
        return None
    n = parse_number(v)
    if n is None or not lo <= n <= hi:
        raise ValueError(f"{label} phải từ {lo:g} đến {hi:g}.")
    return int(n) if integer else float(n)


def clean_inputs(data: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Validate what the page sends (ValueError with a Vietnamese message)."""
    raw = data.get("params") or {}
    params = dict(DEFAULT_PARAMS)
    params["useFactor"] = bool(raw.get("useFactor", True))
    for key, lo, hi, label, integer in (("sockets", 1, 8, "Số socket", True), ("coresPerSocket", 1, 256, "Core mỗi socket", True),
                                        ("ramGB", 16, 65536, "RAM host (GB)", True), ("targetPct", 50, 100, "Mục tiêu % ngưỡng", False),
                                        ("unitPrice", 0, 10 ** 13, "Đơn giá host (đ)", True)):
        v = _num(raw.get(key), lo, hi, label, integer)
        if v is not None or key == "unitPrice":
            params[key] = v
    params["defaultCluster"] = str(raw.get("defaultCluster") or "")[:128]
    projects = []
    rows = data.get("projects") or []
    if not isinstance(rows, list) or len(rows) > MAX_PROJECTS:
        raise ValueError(f"Danh sách dự án tối đa {MAX_PROJECTS} dòng.")
    for i, r in enumerate(rows, 1):
        if not isinstance(r, dict):
            raise ValueError(f"Dòng {i}: không hợp lệ.")
        name = str(r.get("name") or "").strip()[:255]
        sizes = {k: _num(r.get(k), 0, 10 ** 7, f"Dòng {i}: {label}") or 0.0
                 for k, label in (("vcpu", "vCPU"), ("ramGB", "RAM"), ("diskGB", "Disk"))}
        if not name and not any(sizes.values()):
            continue
        if not name:
            raise ValueError(f"Dòng {i}: thiếu tên dự án.")
        projects.append({"name": name, "department": str(r.get("department") or "").strip()[:255] or None,
                         "cluster": str(r.get("cluster") or "").strip()[:128] or None,
                         "vms": int(_num(r.get("vms"), 0, 100000, f"Dòng {i}: số VM") or 0), **sizes})
    return params, projects


def save_inputs(db: Session, year: int, params: Dict[str, Any], projects: List[Dict[str, Any]], user: str) -> None:
    p = _year_plan(db, year) or ItsmYearPlan(year=year)
    p.params, p.projects = json.dumps(params), json.dumps(projects, ensure_ascii=False)
    p.updated_by, p.updated_at = user, dt.datetime.utcnow()
    db.add(p)


def _pending(db: Session, since: dt.datetime) -> List[Dict[str, Any]]:
    """Counted new-VM requests of the last 12 months whose lines have no strong VM yet (approved, not provisioned)."""
    reqs = [r for r in _counted_requests(db, ("new_vm",), since)
            if not any(w in (r.status or "").lower() for w in CLOSED_WORDS)]
    done = {(rid, n) for rid, n in db.query(ItsmRequestCi.request_id, ItsmRequestCi.line_no)
            .filter(ItsmRequestCi.match == "strong", ItsmRequestCi.request_id.in_([r.id for r in reqs] or [-1]))}
    lines = _lines(db, [r.id for r in reqs])
    out = []
    for r in reqs:
        for ln in lines[r.id]:
            if (r.id, ln.line_no) in done:
                continue
            out.append({"label": f"#{r.req_id} {ln.vm_name or r.subject or ''}".strip(), "source": "approved", "reqId": r.req_id,
                        "department": r.department, "cluster": None, **_size(ln, "new_vm")})
    return out


def _reclaims(db: Session, vms_now: Dict[Tuple[str, str], Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Latest review decision per VM: "Thu hồi" not verified on an active VM, or "Giảm cấu hình" above the current config.
    Only powered-on VMs free CPU / RAM in the capacity model."""
    from ..cmdb.models import Ci
    from ..owners.models import ReviewItem
    latest: Dict[int, ReviewItem] = {}
    for it in (db.query(ReviewItem).join(Ci, Ci.id == ReviewItem.ci_id)
               .filter(ReviewItem.decision.in_(("decommission", "resize")), Ci.status == "active", ReviewItem.verified_at.is_(None))
               .order_by(ReviewItem.decided_at)):
        latest[it.ci_id] = it
    out = []
    for it in latest.values():
        v = vms_now.get((it.vcenter_key or "", it.vm_name))
        if not v or not v.get("isPoweredOn"):
            continue
        vcpu, ram = float(v.get("vcpu") or 0), float(v.get("ramGB") or 0)
        if it.decision == "resize":
            vcpu = max(0.0, vcpu - it.new_vcpu) if it.new_vcpu else 0.0
            ram = max(0.0, ram - it.new_ram_gb) if it.new_ram_gb else 0.0
        if vcpu or ram:
            out.append({"label": it.vm_name, "decision": it.decision, "systemCode": it.system_code, "cluster": v.get("cluster"),
                        "vcpu": vcpu, "ramGB": ram})
    return out


def _clusters(db: Session, items: List[Dict[str, Any]], now: dt.datetime):
    from .. import capacity, data_access, history
    from ..config import get_settings
    policy = data_access.get_effective_policy(db, get_settings())
    hosts, vms, _ = data_access.get_latest_combined(db, None)
    try:
        hist = history.get_resource_history(db, None, 12, "cluster", policy)
    except Exception:  # noqa: BLE001 — no history: natural growth 0, said on the page
        hist = {}
    series = {g.get("label") or g.get("key"): g.get("series") or [] for g in (hist or {}).get("groups", [])}
    out = []
    for name in capacity.get_cluster_list(hosts, vms):
        c_hosts = [h for h in hosts if h.get("cluster") == name]
        if not c_hosts:
            continue
        model = capacity.build_capacity_model(c_hosts, [v for v in vms if v.get("cluster") == name], policy)
        s = series.get(name) or []
        span = (dt.datetime.fromisoformat(str(s[0]["date"])[:10]), dt.datetime.fromisoformat(str(s[-1]["date"])[:10]) + dt.timedelta(days=1)) if s else None
        req = {"vcpu": 0.0, "ramGB": 0.0}
        for it in items:
            if span and it["cluster"] == name and span[0] <= it["appearedAt"] + VN < span[1]:
                req["vcpu"] += it["provVcpu"] or it["reqVcpu"]
                req["ramGB"] += it["provRamGB"] or it["reqRamGB"]
        org = planning.organic_growth(s, req)
        out.append({"name": name, "hosts": [{"cores": h.get("cores"), "ramGB": h.get("ramGB")} for h in c_hosts],
                    "usedVcpu": float(model["usedVcpu"]), "usedRamGB": float(model["usedRamGB"]),
                    "organicVcpu": org["vcpu"], "organicRamGB": org["ramGB"], "organic": org})
    return out, policy, {(v.get("vcenterKey") or "", v["vm"]): v for v in vms}


def build(db: Session, year: int, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """Everything of the two tabs, the PDF and the Excel: summary of `year` and the plan of `year` + 1."""
    from .. import vm_ip
    now = now or dt.datetime.utcnow()
    year_ago = now - dt.timedelta(days=365)
    summary = planning.demand_summary(request_lines(db, year), year)
    items = fulfilment_items(db, min(dt.datetime(year, 1, 1) - VN, year_ago - dt.timedelta(days=HORIZONS[-1])), now)
    year_items = [it for it in items if (it["appearedAt"] + VN).year == year]
    fulfil = planning.fulfilment(year_items)
    inp = inputs(db, year + 1)
    prm = inp["params"]
    clusters, policy, vms_now = _clusters(db, items, now)
    known = _pending(db, year_ago) + [{"label": p["name"], "source": "manual", **p} for p in inp["projects"]]
    node = HostProfile(name=f"{prm['sockets']}×{prm['coresPerSocket']} core · {prm['ramGB']:g} GB", sockets=int(prm["sockets"]),
                       cores_per_socket=int(prm["coresPerSocket"]), ram_gb=float(prm["ramGB"]), unit_price=prm["unitPrice"])
    fc = planning.forecast(clusters, known, _reclaims(db, vms_now), node, policy, float(prm["targetPct"]),
                           factors=fulfil if prm["useFactor"] else None, default_cluster=prm["defaultCluster"] or None)
    for c, src in zip(fc["clusters"], clusters):
        c["organic"] = src["organic"]
    vm_rows = [{k: v for k, v in it.items() if k != "appearedAt"} | {"appearedAt": it["appearedAt"].isoformat() + "Z"} for it in year_items]
    vm_ip.attach(db, vm_rows, vm_key="vm")
    return {"year": year, "planYear": year + 1, "generatedAt": now.isoformat() + "Z", "summary": summary,
            "fulfilment": {**fulfil, "vms": vm_rows}, "inputs": inp, "forecast": fc,
            "policy": {"cpuOvercommitMax": policy.cpu_overcommit_max, "ramOvercommitMax": policy.ram_overcommit_max, "htFactor": policy.ht_factor}}


# ---------------------------------------------------------------- budget plan (spend/budget_plan.py source "capacity")
def budget_snapshot(d: Dict[str, Any]) -> Dict[str, Any]:
    """What is frozen when the plan is sent to the budget: the hosts, the amount and where every number comes from."""
    from ..pdf_kit import vn
    t, n = d["forecast"]["totals"], d["forecast"]["node"]
    year = d["planYear"]
    price = f"{vn(n['unitPrice'])} đ/host" if n["unitPrice"] is not None else "chưa nhập đơn giá"
    detail = (f"{vn(t['nodes'])} host {n['name']} ({price}). Nhu cầu ròng {vn(t['netVcpu'])} vCPU · {vn(t['netRamGB'])} GB RAM "
              f"= hiện tại {vn(t['usedVcpu'])} vCPU · {vn(t['usedRamGB'])} GB + tăng tự nhiên {vn(t['organicVcpu'])} · {vn(t['organicRamGB'])} GB "
              f"+ nhu cầu đã biết {vn(t['knownVcpu'])} · {vn(t['knownRamGB'])} GB"
              f"{' (× hệ số dùng thật / xin)' if d['forecast']['useFactor'] else ''} − thu hồi {vn(t['reclaimVcpu'])} · {vn(t['reclaimRamGB'])} GB. "
              f"N+1, mục tiêu {vn(d['forecast']['targetPct'])} % ngưỡng overcommit. Nguồn: ICO Nhu cầu hạ tầng → Kế hoạch năm {year}.")
    return {"year": year, "nodes": t["nodes"], "amount": t["amount"] or 0, "title": f"Mở rộng hạ tầng ảo hoá {year}",
            "detail": detail[:1000], "clusters": {c["name"]: c["nodes"] for c in d["forecast"]["clusters"] if c["nodes"]}}


def push(db: Session, d: Dict[str, Any], user: str) -> Dict[str, Any]:
    p = _year_plan(db, d["planYear"]) or ItsmYearPlan(year=d["planYear"])
    snap = budget_snapshot(d)
    p.pushed, p.pushed_at, p.pushed_by = json.dumps(snap, ensure_ascii=False), dt.datetime.utcnow(), user
    db.add(p)
    return snap


def plan_suggestions(db: Session, year: int, year_start: dt.date) -> List[Dict[str, Any]]:
    """The "capacity" Capex line of the budget plan of fiscal `year` (sourceId = year: one line per year, refreshed in place)."""
    from decimal import Decimal
    from ..spend.models import SpendCategory
    p = _year_plan(db, year)
    snap = json.loads(p.pushed) if p and p.pushed else None
    if not snap or not snap.get("nodes"):
        return []
    cats = db.query(SpendCategory).filter(SpendCategory.active.is_(True)).order_by(SpendCategory.sort_order, SpendCategory.id).all()
    if not cats:
        return []
    cat = next((c for c in cats if c.code == "HW_SERVER"), cats[0])
    return [{"kind": "capex", "source": "capacity", "sourceId": year, "sourceCode": f"{snap['nodes']} host", "categoryId": cat.id,
             "vendorId": None, "costCenterId": None, "title": snap["title"], "startDate": year_start, "terms": 1,
             "baseNet": int(snap["amount"] or 0), "vatPct": Decimal(10), "detail": snap["detail"], "priority": "must"}]
