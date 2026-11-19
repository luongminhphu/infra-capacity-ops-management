"""Automatic VM → system assignment (ICO v2 G2b), run on the leader after the CI registry build.

Priority, highest first:  manual  >  attribute  >  tag (G2d)  >  rule  >  itsm (G7).
  attribute  vCenter custom attribute: "System" = system code or name; else "Owner" = owner e-mail of exactly one system.
             Names of the two attributes are settings (Cài đặt → Owner). A value naming nothing ICO knows becomes a
             proposal (owner_proposals) — systems are never created automatically.
  rule       owner_rules by priority (then the longest pattern): folder (path prefix: "/Projects/Bank" matches
             "/Projects/Bank/Core"), resource_pool, cluster (exact, case-insensitive), attribute ("Name=value"),
             name_regex (re.search on the VM name; ≤ 200 characters, no nested quantifiers / back-references).
The engine only writes the assignments it owns (attribute, rule); a manual one is never touched. plan() computes the
whole diff without writing (the "Chạy thử" button); apply() writes exactly that plan; run() = plan + apply + commit.
"""
import datetime as dt
import json
import logging
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, insert, update
from sqlalchemy.orm import Session

from ..cmdb.models import Ci
from ..models import AppSetting, AuditLog
from ..shared.text import norm_text
from .models import OwnerProposal, OwnerRule, System, SystemMember

logger = logging.getLogger("vco.owners")

RANK = {"manual": 0, "attribute": 1, "tag": 2, "rule": 3, "itsm": 4}
MANAGED = ("attribute", "rule")             # sources this engine adds and removes
UI_KINDS = ("folder", "resource_pool", "cluster", "attribute", "name_regex")
KIND_LABELS = {"folder": "Thư mục vCenter", "resource_pool": "Resource pool", "cluster": "Cluster",
               "attribute": "Thuộc tính vCenter", "name_regex": "Tên VM (regex)"}
ATTR_SETTINGS = {"system": "owners.attr.system", "owner": "owners.attr.owner"}
DEFAULT_ATTRS = {"system": "System", "owner": "Owner"}
MAX_PATTERN = 200
MAX_RULES = 500
MAX_NAME = 255                              # regex input bound: VM names are ≤ 255 characters
# Catastrophic backtracking comes from a quantified group that itself contains a quantifier, e.g. (a+)+, (\w*)*.
_NESTED = re.compile(r"\((?:[^()\\]|\\.)*[*+?}](?:[^()\\]|\\.)*\)\s*[*+?{]")
_BACKREF = re.compile(r"\\[1-9]|\(\?P=")
_lock = threading.Lock()


class Invalid(ValueError):
    pass


# ---------------------------------------------------------------- settings / validation
def attr_names(db: Session) -> Dict[str, str]:
    rows = {k: v for k, v in db.query(AppSetting.key, AppSetting.value).filter(AppSetting.key.in_(list(ATTR_SETTINGS.values())))}
    return {k: rows.get(key, DEFAULT_ATTRS[k]) for k, key in ATTR_SETTINGS.items()}


def save_attr_names(db: Session, names: Dict[str, Any]) -> Dict[str, str]:
    out = {}
    for k, key in ATTR_SETTINGS.items():
        v = " ".join(str(names.get(k) or "").split())[:64]
        out[k] = v
        db.merge(AppSetting(key=key, value=v))
    return out


def check_regex(p: str) -> "re.Pattern":
    if len(p) > MAX_PATTERN:
        raise Invalid(f"Regex dài quá {MAX_PATTERN} ký tự.")
    if _NESTED.search(p) or _BACKREF.search(p):
        raise Invalid("Regex có lượng từ lồng nhau hoặc tham chiếu ngược (vd (a+)+, \\1) — viết lại đơn giản hơn.")
    try:
        return re.compile(p, re.IGNORECASE)
    except re.error as exc:
        raise Invalid(f"Regex không hợp lệ: {exc}.") from None


def _folder(p: str) -> str:
    p = "/" + "/".join(x.strip() for x in str(p).replace("\\", "/").split("/") if x.strip())
    return p.lower()


def clean_rule(d: Dict[str, Any], system_ids: set) -> Dict[str, Any]:
    kind = d.get("matchKind")
    if kind not in UI_KINDS:
        raise Invalid(f"Loại quy tắc “{str(kind)[:20]}” không hợp lệ.")
    pattern = str(d.get("pattern") or "").strip()
    if not pattern:
        raise Invalid("Quy tắc cần giá trị so khớp.")
    if len(pattern) > MAX_PATTERN:
        raise Invalid(f"Giá trị so khớp dài quá {MAX_PATTERN} ký tự.")
    if kind == "name_regex":
        check_regex(pattern)
    elif kind == "attribute" and ("=" not in pattern or not pattern.split("=", 1)[0].strip()):
        raise Invalid("Quy tắc thuộc tính viết dạng Tên=giá trị (vd Phòng ban=Tài chính).")
    elif kind == "folder":
        pattern = "/" + "/".join(x.strip() for x in pattern.replace("\\", "/").split("/") if x.strip())
    sid = d.get("systemId")
    if not isinstance(sid, int) or sid not in system_ids:
        raise Invalid("Quy tắc trỏ tới hệ thống không tồn tại.")
    pr = d.get("priority", 100)
    if not isinstance(pr, int) or isinstance(pr, bool) or not 1 <= pr <= 100000:
        raise Invalid("Thứ tự ưu tiên là số nguyên 1–100000.")
    return {"match_kind": kind, "pattern": pattern, "system_id": sid, "priority": pr, "enabled": d.get("enabled") is not False}


def rule_out(r: OwnerRule) -> Dict[str, Any]:
    return {"id": r.id, "matchKind": r.match_kind, "kindLabel": KIND_LABELS.get(r.match_kind, r.match_kind), "pattern": r.pattern,
            "systemId": r.system_id, "priority": r.priority, "enabled": bool(r.enabled)}


# ---------------------------------------------------------------- matching
class _Rule:
    def __init__(self, rid, kind, pattern, system_id, priority):
        self.id, self.kind, self.system_id, self.priority, self.pattern = rid, kind, system_id, priority, pattern
        if kind == "name_regex":
            self.rx = check_regex(pattern)
        elif kind == "folder":
            self.value = _folder(pattern)
        elif kind == "attribute":
            k, v = pattern.split("=", 1)
            self.attr, self.value = norm_text(k), v.strip().lower()
        else:
            self.value = pattern.strip().lower()

    def match(self, vm: Dict[str, Any]) -> bool:
        if self.kind == "folder":
            f = _folder(vm.get("folderPath") or "") if vm.get("folderPath") else ""
            return bool(f) and (f == self.value or f.startswith(self.value.rstrip("/") + "/"))
        if self.kind == "resource_pool":
            return (vm.get("resourcePool") or "").lower() == self.value
        if self.kind == "cluster":
            return (vm.get("cluster") or "").lower() == self.value
        if self.kind == "attribute":
            return any(norm_text(k) == self.attr and str(v).strip().lower() == self.value for k, v in (vm.get("customAttrs") or {}).items())
        return bool(self.rx.search((vm.get("name") or "")[:MAX_NAME]))


def _compile(rules: List[Dict[str, Any]]) -> List[_Rule]:
    out = [_Rule(r.get("id"), r["match_kind"], r["pattern"], r["system_id"], r["priority"])
           for r in rules if r.get("enabled", True) and r["match_kind"] in UI_KINDS]
    return sorted(out, key=lambda r: (r.priority, -len(r.pattern), r.id or 0))


def _attr(attrs: Dict[str, str], name: str) -> str:
    if not name:
        return ""
    want = norm_text(name)
    return next((str(v).strip() for k, v in (attrs or {}).items() if norm_text(k) == want), "")


def _decide(vm: Dict[str, Any], names: Dict[str, str], by_code, by_name, by_owner, rules: List[_Rule],
            proposals: Dict[str, Dict[str, Any]]) -> Optional[Tuple[int, str, str]]:
    """(system_id, source, reason) or None; unknown attribute values are recorded in `proposals`."""
    attrs = vm.get("customAttrs") or {}
    sys_val, owner_val = _attr(attrs, names["system"]), _attr(attrs, names["owner"]).lower()
    owner_ok = bool(owner_val) and bool(re.match(r"^[a-z0-9._%+'-]+@[a-z0-9.-]+\.[a-z]{2,}$", owner_val))
    if sys_val:
        s = by_code.get(sys_val.upper()) or by_name.get(norm_text(sys_val))
        if s is not None:
            return s.id, "attribute", f"{names['system']}={sys_val}"
        p = proposals.setdefault(f"system:{norm_text(sys_val)}"[:300], {"system_name": sys_val[:255], "owner_email": None, "vms": []})
        if owner_ok and not p["owner_email"]:
            p["owner_email"] = owner_val
        p["vms"].append(vm["name"])
    elif owner_ok:
        hits = by_owner.get(owner_val, [])
        if len(hits) == 1:
            return hits[0].id, "attribute", f"{names['owner']}={owner_val}"
        if not hits:
            p = proposals.setdefault(f"owner:{owner_val}"[:300], {"system_name": None, "owner_email": owner_val, "vms": []})
            p["vms"].append(vm["name"])
    for r in rules:
        if r.match(vm):
            return r.system_id, "rule", f"{KIND_LABELS[r.kind]}: {r.pattern}"
    return None


# ---------------------------------------------------------------- plan / apply / run
def _vms(db: Session) -> List[Dict[str, Any]]:
    out = []
    for cid, name, attrs in db.query(Ci.id, Ci.name, Ci.attrs).filter(Ci.type == "vm", Ci.status == "active"):
        a = json.loads(attrs or "{}")
        out.append({"ciId": cid, "name": name, "cluster": a.get("cluster"), "vcenterKey": a.get("vcenterKey"),
                    "folderPath": a.get("folderPath"), "resourcePool": a.get("resourcePool"), "customAttrs": a.get("customAttrs") or {}})
    return out


def plan(db: Session, rules: Optional[List[Dict[str, Any]]] = None, names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Diff between the current assignments and what the rules give. `rules` / `names` = a draft (Chạy thử);
    None = what is saved. Writes nothing."""
    if rules is None:
        rules = [{"id": r.id, "match_kind": r.match_kind, "pattern": r.pattern, "system_id": r.system_id,
                  "priority": r.priority, "enabled": r.enabled} for r in db.query(OwnerRule)]
    names = names or attr_names(db)
    systems = db.query(System).filter(System.status == "active").all()
    by_code = {s.code.upper(): s for s in systems}
    by_name = {s.name_norm: s for s in systems}
    by_owner: Dict[str, List[System]] = {}
    for s in systems:
        if s.owner_email:
            by_owner.setdefault(s.owner_email, []).append(s)
    active_ids = {s.id for s in systems}
    compiled = [r for r in _compile(rules) if r.system_id in active_ids]
    cur = {m.ci_id: m for m in db.query(SystemMember)}
    proposals: Dict[str, Dict[str, Any]] = {}
    adds, changes, removes, kept, same, none = [], [], [], 0, 0, 0
    for vm in _vms(db):
        want = _decide(vm, names, by_code, by_name, by_owner, compiled, proposals)
        m = cur.get(vm["ciId"])
        if want is None:
            if m is not None and m.source in MANAGED:
                removes.append({"ciId": vm["ciId"], "vm": vm["name"], "fromSystemId": m.system_id, "fromSource": m.source})
            elif m is None:
                none += 1
            elif m.source == "manual":
                kept += 1
            continue
        sid, source, reason = want
        if m is None:
            adds.append({"ciId": vm["ciId"], "vm": vm["name"], "systemId": sid, "source": source, "reason": reason})
        elif (m.system_id, m.source) == (sid, source):
            same += 1
        elif m.source not in MANAGED and RANK[m.source] < RANK[source]:
            # manual (or a tag above a rule) outranks the engine; its own earlier result never does
            kept += m.source == "manual"
            same += m.source != "manual"
        else:
            changes.append({"ciId": vm["ciId"], "vm": vm["name"], "systemId": sid, "source": source, "reason": reason,
                            "fromSystemId": m.system_id, "fromSource": m.source})
    for p in proposals.values():
        p["vms"] = sorted(set(p["vms"]))
    return {"adds": adds, "changes": changes, "removes": removes, "proposals": proposals,
            "counts": {"add": len(adds), "change": len(changes), "remove": len(removes), "keptManual": kept,
                       "unchanged": same, "unassigned": none, "proposals": len(proposals)}}


def public_plan(db: Session, p: Dict[str, Any], limit: int = 200) -> Dict[str, Any]:
    names = {s.id: f"{s.code} — {s.name}" for s in db.query(System)}
    row = lambda x: {**x, "system": names.get(x.get("systemId")), "fromSystem": names.get(x.get("fromSystemId"))}  # noqa: E731
    return {"counts": p["counts"], "adds": [row(x) for x in p["adds"][:limit]], "changes": [row(x) for x in p["changes"][:limit]],
            "removes": [row(x) for x in p["removes"][:limit]],
            "proposals": [{"key": k, "systemName": v["system_name"], "ownerEmail": v["owner_email"], "vmCount": len(v["vms"])}
                          for k, v in sorted(p["proposals"].items())][:limit]}


def apply(db: Session, p: Dict[str, Any], username: str = "system") -> Dict[str, int]:
    """Write a plan (bulk statements; caller commits)."""
    now = dt.datetime.utcnow()
    by = f"auto:{username}" if username != "system" else "auto"
    conf = {"attribute": "strong", "rule": "weak"}
    if p["adds"]:
        db.execute(insert(SystemMember), [{"ci_id": x["ciId"], "system_id": x["systemId"], "source": x["source"],
                                           "confidence": conf[x["source"]], "assigned_by": by, "assigned_at": now} for x in p["adds"]])
    if p["changes"]:
        ids = {m.ci_id: m.id for m in db.query(SystemMember.id, SystemMember.ci_id)
               .filter(SystemMember.ci_id.in_([x["ciId"] for x in p["changes"]]))}
        db.execute(update(SystemMember), [{"id": ids[x["ciId"]], "system_id": x["systemId"], "source": x["source"],
                                           "confidence": conf[x["source"]], "assigned_by": by, "assigned_at": now}
                                          for x in p["changes"] if x["ciId"] in ids])
    rm = [x["ciId"] for x in p["removes"]]
    for i in range(0, len(rm), 500):
        db.execute(delete(SystemMember).where(SystemMember.ci_id.in_(rm[i:i + 500]), SystemMember.source.in_(MANAGED)))
    _save_proposals(db, p["proposals"], now)
    c = p["counts"]
    if c["add"] or c["change"] or c["remove"]:
        db.add(AuditLog(username=username, action="owners.rules.apply",
                        detail=json.dumps({k: c[k] for k in ("add", "change", "remove", "proposals")})))
    return {k: c[k] for k in ("add", "change", "remove", "keptManual", "unchanged", "unassigned", "proposals")}


def _save_proposals(db: Session, found: Dict[str, Dict[str, Any]], now: dt.datetime) -> None:
    have = {p.key: p for p in db.query(OwnerProposal)}
    for key, v in found.items():
        sample = json.dumps(v["vms"][:10], ensure_ascii=False)
        p = have.get(key)
        if p is None:
            db.add(OwnerProposal(key=key, system_name=v["system_name"], owner_email=v["owner_email"], vm_count=len(v["vms"]),
                                 sample=sample, status="open", first_seen=now, last_seen=now))
        elif (p.vm_count, p.sample, p.owner_email) != (len(v["vms"]), sample, v["owner_email"] or p.owner_email) or p.status == "open":
            if (p.vm_count, p.sample) != (len(v["vms"]), sample):
                p.vm_count, p.sample = len(v["vms"]), sample
            if v["owner_email"] and not p.owner_email:
                p.owner_email = v["owner_email"]
            if now - (p.last_seen or now) >= dt.timedelta(hours=1):
                p.last_seen = now
    for key, p in have.items():                  # an open proposal no VM points to any more is dropped
        if key not in found and p.status == "open":
            db.delete(p)


def run(db: Session, username: str = "system") -> Dict[str, int]:
    with _lock:
        p = plan(db)
        res = apply(db, p, username)
        db.commit()
        return res
