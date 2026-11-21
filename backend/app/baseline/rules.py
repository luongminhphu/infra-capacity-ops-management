"""Built-in configuration rules and their evaluation (pure: tested with fixed facts).

A check returns (ok, detail): ok True = compliant, False = deviation, None = not applicable / not readable (never reported as
a deviation — a fact that could not be read is not a misconfiguration). Levels use the four-level scale of shared/levels.py
(warn = THEO DÕI, bad = CẢNH BÁO, crit = VƯỢT NGƯỠNG). Only VMware vCenter sources produce facts: Proxmox / Nutanix never
show VMware deviations."""
import datetime as dt
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..capacity import finding_code

Check = Callable[[Dict[str, Any], Any], Tuple[Optional[bool], str]]
TOOLS_OK = ("guestToolsCurrent", "guestToolsUnmanaged", "guestToolsSupportedNew")
TOOLS_TEXT = {"guestToolsNotInstalled": "chưa cài", "guestToolsNeedUpgrade": "cần nâng cấp", "guestToolsTooOld": "quá cũ",
              "guestToolsSupportedOld": "cũ (còn hỗ trợ)", "guestToolsBlacklisted": "bản bị chặn", "guestToolsTooNew": "mới hơn host"}


def _service(name: str) -> Check:
    def check(f: Dict[str, Any], _p: Any) -> Tuple[Optional[bool], str]:
        running, policy = f.get(f"{name}Running"), f.get(f"{name}Policy")
        if running is None:
            return None, ""
        return (not running and policy != "on"), f"đang {'chạy' if running else 'tắt'}, khởi động: {policy or '—'}"
    return check


def _lockdown(f, _p):
    mode = f.get("lockdown")
    if mode is None:
        return None, ""
    return mode != "lockdownDisabled", {"lockdownDisabled": "tắt", "lockdownNormal": "normal", "lockdownStrict": "strict"}.get(mode, mode)


def _ntp(f, _p):
    if "ntpServers" not in f:
        return None, ""
    servers, running = f.get("ntpServers") or [], f.get("ntpRunning")
    return bool(servers) and bool(running), f"{len(servers)} server{', ntpd chạy' if running else ', ntpd không chạy'}"


def _syslog(f, _p):
    host = f.get("syslogHost")
    if host is None:
        return None, ""
    return bool(host.strip()), host.strip() or "chưa đặt Syslog.global.logHost"


def _ha(f, _p):
    if f.get("ha") is None:
        return None, ""
    return bool(f["ha"]) and bool(f.get("admissionControl")), f"HA {'bật' if f['ha'] else 'tắt'}, admission control {'bật' if f.get('admissionControl') else 'tắt'}"


def _drs(f, _p):
    if f.get("drs") is None:
        return None, ""
    return bool(f["drs"]) and f.get("drsBehavior") == "fullyAutomated", f"DRS {'bật' if f['drs'] else 'tắt'}, {f.get('drsBehavior') or '—'}"


def _builds(f, _p):
    builds = f.get("builds")
    if not builds:
        return None, ""
    return len(builds) <= 1, "; ".join(f"build {b}: {n} host" for b, n in sorted(builds.items()))


def _tools(f, _p):
    if not f.get("poweredOn") or not f.get("toolsStatus"):
        return None, ""
    s = f["toolsStatus"]
    return s in TOOLS_OK, f"VMware Tools {TOOLS_TEXT.get(s, s)}"


def _hw(f, p):
    v = f.get("hwVersion")
    if v is None:
        return None, ""
    return v >= int(p), f"vmx-{v} (tối thiểu vmx-{int(p)})"


def _iso(f, _p):
    if not f.get("poweredOn") or "isos" not in f:
        return None, ""
    isos = f.get("isos") or []
    return not isos, ", ".join(isos)[:300]


# key → (scope, label, default level, default param, param label, check)
RULES: Dict[str, Tuple[str, str, str, Any, str, Check]] = {
    "esxi_ssh": ("host", "SSH trên ESXi tắt", "bad", None, "", _service("ssh")),
    "esxi_shell": ("host", "ESXi Shell tắt", "bad", None, "", _service("shell")),
    "lockdown": ("host", "Lockdown mode bật", "warn", None, "", _lockdown),
    "ntp": ("host", "NTP có server và ntpd chạy", "bad", None, "", _ntp),
    "syslog": ("host", "Gửi log tới syslog tập trung", "warn", None, "", _syslog),
    "cluster_ha": ("cluster", "HA bật + admission control", "bad", None, "", _ha),
    "cluster_drs": ("cluster", "DRS bật, fully automated", "warn", None, "", _drs),
    "build_drift": ("cluster", "Cùng build ESXi trong cluster", "warn", None, "", _builds),
    "vm_tools": ("vm", "VMware Tools cập nhật", "warn", None, "", _tools),
    "vm_hw": ("vm", "Phiên bản phần cứng VM tối thiểu", "warn", 14, "vmx tối thiểu", _hw),
    "vm_iso": ("vm", "Không gắn ISO trên VM đang chạy", "warn", None, "", _iso),
}


def effective(overrides: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every rule with the admin override applied (enabled, level, param)."""
    out = []
    for key, (scope, label, level, param, param_label, _c) in RULES.items():
        o = overrides.get(key) or {}
        out.append({"rule": key, "scope": scope, "label": label, "enabled": o.get("enabled", True), "level": o.get("level") or level,
                    "defaultLevel": level, "param": o.get("param") if o.get("param") not in (None, "") else param, "paramLabel": param_label})
    return out


def with_builds(facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add the "builds" fact ({build: hosts}) to every cluster row from its hosts' builds (same vCenter)."""
    per: Dict[Tuple[str, str], Dict[str, int]] = {}
    for f in facts:
        if f["scope"] == "host" and f.get("cluster") and f["facts"].get("build"):
            b = per.setdefault((f["vcenterKey"], f["cluster"]), {})
            b[str(f["facts"]["build"])] = b.get(str(f["facts"]["build"]), 0) + 1
    return [{**f, "facts": {**f["facts"], "builds": per.get((f["vcenterKey"], f["entity"]), {})}} if f["scope"] == "cluster" else f
            for f in facts]


def _excepted(exc: List[Dict[str, Any]], rule: str, f: Dict[str, Any], today: dt.date) -> Optional[Dict[str, Any]]:
    for e in exc:
        if e["rule"] != rule or e["until"] < today or e["vcenterKey"] not in ("", f["vcenterKey"]):
            continue
        if e["scope"] == f["scope"] and e["entity"] == f["entity"]:
            return e
        if e["scope"] == "cluster" and e["entity"] == f.get("cluster") and f["scope"] != "cluster":
            return e                     # an exception on a cluster covers its hosts / VMs for that rule
    return None


def evaluate(facts: List[Dict[str, Any]], rules: List[Dict[str, Any]], exceptions: List[Dict[str, Any]],
             today: dt.date) -> Dict[str, Any]:
    """facts: {vcenterKey, scope, entity, cluster, facts}. Returns {findings, excepted, checked} — findings / excepted rows:
    {code, rule, label, level, scope, entity, cluster, vcenterKey, detail} (+ exception on the excepted ones)."""
    facts = with_builds(facts)
    findings, excepted, checked = [], [], {r["rule"]: 0 for r in rules}
    for r in rules:
        if not r["enabled"]:
            continue
        check = RULES[r["rule"]][5]
        for f in facts:
            if f["scope"] != r["scope"]:
                continue
            ok, detail = check(f["facts"], r["param"])
            if ok is None:
                continue
            checked[r["rule"]] += 1
            if ok:
                continue
            row = {"code": "CFG-" + finding_code(f"cfg:{r['rule']}", f.get("cluster"), f["entity"], f["vcenterKey"]).split("-")[1],
                   "rule": r["rule"], "label": r["label"], "level": r["level"], "scope": f["scope"], "entity": f["entity"],
                   "cluster": f.get("cluster"), "vcenterKey": f["vcenterKey"], "detail": detail}
            e = _excepted(exceptions, r["rule"], f, today)
            if e:
                excepted.append({**row, "exception": {"id": e.get("id"), "reason": e["reason"], "until": e["until"].isoformat()}})
            else:
                findings.append(row)
    rank = {"crit": 0, "bad": 1, "warn": 2}
    findings.sort(key=lambda x: (rank[x["level"]], x["rule"], x["vcenterKey"], x["entity"]))
    return {"findings": findings, "excepted": excepted, "checked": checked}
