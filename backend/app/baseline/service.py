"""Configuration baseline — storage, settings and the one report every consumer reads (page, Excel, PDF, Hộp việc, cards)."""
import datetime as dt
import json
from collections import defaultdict
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import rules as R
from .models import CfgException, CfgFactState, CfgRule

SCOPE_LABELS = {"host": "Host ESXi", "cluster": "Cluster", "vm": "VM"}


def store(db: Session, vcenter_key: str, extras: Dict[str, Any], now: Optional[dt.datetime] = None) -> int:
    """Replace the facts of one vCenter. extras["config"] None = could not read (keep the previous facts); missing = a source
    without a baseline (Proxmox / Nutanix): its facts are cleared. Caller commits."""
    if "config" in (extras or {}) and extras["config"] is None:
        return 0
    cfg = (extras or {}).get("config") or {}
    now = now or dt.datetime.utcnow()
    db.query(CfgFactState).filter(CfgFactState.vcenter_key == vcenter_key).delete(synchronize_session=False)
    rows = ([{"scope": "host", "entity": h["host"], "cluster": h.get("cluster"), "facts": h["facts"]} for h in cfg.get("hosts") or []]
            + [{"scope": "cluster", "entity": c["cluster"], "cluster": c["cluster"], "facts": c["facts"]} for c in cfg.get("clusters") or []]
            + [{"scope": "vm", "entity": v["vm"], "cluster": v.get("cluster"), "facts": v["facts"]} for v in cfg.get("vms") or []])
    db.bulk_insert_mappings(CfgFactState, [{"vcenter_key": vcenter_key, "scope": r["scope"], "entity": str(r["entity"])[:255],
                                            "cluster": (r["cluster"] or None) and str(r["cluster"])[:255],
                                            "facts": json.dumps(r["facts"], ensure_ascii=False), "captured_at": now} for r in rows])
    return len(rows)


def rules(db: Session) -> List[Dict[str, Any]]:
    return R.effective({o.rule: {"enabled": o.enabled, "level": o.level, "param": o.param} for o in db.query(CfgRule)})


def exceptions(db: Session) -> List[Dict[str, Any]]:
    return [{"id": e.id, "rule": e.rule, "scope": e.scope, "vcenterKey": e.vcenter_key or "", "entity": e.entity, "reason": e.reason,
             "until": e.until, "createdBy": e.created_by} for e in db.query(CfgException).order_by(CfgException.until)]


def report(db: Session, today: Optional[dt.date] = None) -> Dict[str, Any]:
    """Findings, accepted deviations, rule list with counts — the same dict for the page, the Excel / PDF and Hộp việc."""
    from .. import vm_ip
    from ..spend.fiscal import today_vn
    today = today or today_vn()
    facts = _fact_rows(db.query(CfgFactState))
    rl = rules(db)
    exc = exceptions(db)
    res = R.evaluate(facts, rl, exc, today)
    per = defaultdict(lambda: {"findings": 0, "excepted": 0})
    for f in res["findings"]:
        per[f["rule"]]["findings"] += 1
    for f in res["excepted"]:
        per[f["rule"]]["excepted"] += 1
    for r in rl:
        r.update({"checked": res["checked"].get(r["rule"], 0), **per[r["rule"]], "scopeLabel": SCOPE_LABELS[r["scope"]]})
    is_vm = lambda r: r["scope"] == "vm"  # noqa: E731
    vm_ip.attach(db, res["findings"], vm_key="entity", only=is_vm)
    vm_ip.attach(db, res["excepted"], vm_key="entity", only=is_vm)
    captured = db.query(func.max(CfgFactState.captured_at)).scalar()
    return {"rules": rl, "findings": res["findings"], "excepted": res["excepted"],
            "exceptions": [{**e, "until": e["until"].isoformat()} for e in exc],
            "summary": {"findings": len(res["findings"]), "excepted": len(res["excepted"]),
                        "byLevel": {lv: sum(1 for f in res["findings"] if f["level"] == lv) for lv in ("crit", "bad", "warn")},
                        "hosts": sum(1 for f in facts if f["scope"] == "host"), "vms": sum(1 for f in facts if f["scope"] == "vm"),
                        "capturedAt": captured.isoformat() + "Z" if captured else None}}


def _fact_rows(q) -> List[Dict[str, Any]]:
    return [{"vcenterKey": f.vcenter_key, "scope": f.scope, "entity": f.entity, "cluster": f.cluster, "facts": json.loads(f.facts or "{}")}
            for f in q]


def for_entity(db: Session, scope: str, name: str, vcenter_key: str) -> List[Dict[str, Any]]:
    """Deviations (and accepted ones) of one host / VM for its card — reads only that object's facts; a host also gets its
    cluster's rows (HA / DRS / build drift)."""
    from ..spend.fiscal import today_vn
    q = db.query(CfgFactState).filter(CfgFactState.vcenter_key == (vcenter_key or ""))
    own = _fact_rows(q.filter(CfgFactState.scope == scope, CfgFactState.entity == name))
    if not own:
        return []
    cluster = own[0]["cluster"]
    if scope == "host" and cluster:
        own = _fact_rows(q.filter(CfgFactState.cluster == cluster, CfgFactState.scope.in_(("host", "cluster"))))
    res = R.evaluate(own, rules(db), exceptions(db), today_vn())
    keep = lambda f: (f["scope"] == scope and f["entity"] == name) or (scope == "host" and f["scope"] == "cluster")  # noqa: E731
    return [f for f in res["findings"] if keep(f)] + [f for f in res["excepted"] if keep(f)]


def save_rule(db: Session, rule: str, data: Dict[str, Any], user: str) -> None:
    if rule not in R.RULES:
        raise ValueError("Quy tắc không tồn tại.")
    level = data.get("level") or R.RULES[rule][2]
    if level not in ("warn", "bad", "crit"):
        raise ValueError("Mức không hợp lệ.")
    param = data.get("param")
    if R.RULES[rule][4]:
        try:
            param = str(int(param)) if param not in (None, "") else None
        except (TypeError, ValueError):
            raise ValueError(f"{R.RULES[rule][4]} phải là số nguyên.") from None
        if param is not None and not 1 <= int(param) <= 99:
            raise ValueError(f"{R.RULES[rule][4]} từ 1 đến 99.")
    else:
        param = None
    o = db.query(CfgRule).filter(CfgRule.rule == rule).first() or CfgRule(rule=rule)
    o.enabled, o.level, o.param, o.updated_by, o.updated_at = bool(data.get("enabled", True)), level, param, user, dt.datetime.utcnow()
    db.add(o)


def add_exception(db: Session, data: Dict[str, Any], user: str, today: dt.date) -> CfgException:
    rule, scope = data.get("rule"), data.get("scope")
    if rule not in R.RULES:
        raise ValueError("Quy tắc không tồn tại.")
    if scope not in ("host", "cluster", "vm") or (scope != "cluster" and scope != R.RULES[rule][0]):
        raise ValueError("Phạm vi không hợp lệ cho quy tắc này (đối tượng của quy tắc, hoặc cả cluster).")
    entity, reason = str(data.get("entity") or "").strip()[:255], str(data.get("reason") or "").strip()[:500]
    if not entity or not reason:
        raise ValueError("Cần đối tượng và lý do.")
    try:
        until = dt.date.fromisoformat(str(data.get("until") or ""))
    except ValueError:
        raise ValueError("Ngày hết hạn ngoại lệ không hợp lệ (yyyy-mm-dd).") from None
    if not today < until <= today + dt.timedelta(days=366):
        raise ValueError("Ngoại lệ có hạn từ ngày mai tới tối đa 1 năm.")
    e = CfgException(rule=rule, scope=scope, vcenter_key=str(data.get("vcenterKey") or "")[:64], entity=entity, reason=reason,
                     until=until, created_by=user)
    db.add(e)
    return e
