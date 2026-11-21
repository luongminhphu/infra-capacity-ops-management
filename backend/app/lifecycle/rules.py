"""Lifecycle & security evaluation (pure, tested with fixed data).

products: {product ("esxi" | "vcenter"), name, vcenterKey, cluster, version ("8.0.2"), build ("22380479")}
eogs:     {product, version (prefix "8.0"), eogs (date), eotg}      — the longest matching prefix wins
vmsa:     {vmsa, title, cvss, product, version (prefix), fixedBuild} — exposed when the build is below fixedBuild
certs:    {target, kind, name, host, port, notAfter (datetime | None), selfSigned, error, …}
Levels on the four-level scale: crit VƯỢT NGƯỠNG, bad CẢNH BÁO, warn THEO DÕI, ok AN TOÀN."""
import datetime as dt
from typing import Any, Dict, List, Optional, Sequence

EOGS_DAYS = (180, 365)                 # ≤ 180 days → bad, ≤ 365 → warn, past → crit
CERT_DAYS = (30, 60, 90)               # ≤ 30 days (or expired) → crit, ≤ 60 → bad, ≤ 90 → warn


def in_line(version: Optional[str], prefix: str) -> bool:
    """"8.0.2" is in line "8.0" (and "8.0"), not in "8.01"."""
    v = str(version or "").strip()
    return bool(v) and (v == prefix or v.startswith(prefix + "."))


def build_no(build: Any) -> Optional[int]:
    try:
        return int(str(build).strip())
    except (TypeError, ValueError):
        return None


def eogs_level(eogs: dt.date, today: dt.date) -> str:
    days = (eogs - today).days
    return "crit" if days < 0 else "bad" if days <= EOGS_DAYS[0] else "warn" if days <= EOGS_DAYS[1] else "ok"


def vmsa_level(cvss: float) -> str:
    return "crit" if cvss >= 9 else "bad" if cvss >= 7 else "warn"


def cert_level(not_after: Optional[dt.datetime], now: dt.datetime, days: Sequence[int] = CERT_DAYS) -> str:
    if not_after is None:
        return "na"
    left = (not_after - now).total_seconds() / 86400
    return "crit" if left <= days[0] else "bad" if left <= days[1] else "warn" if left <= days[2] else "ok"


def match_eogs(p: Dict[str, Any], eogs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    hits = [e for e in eogs if e["product"] == p["product"] and in_line(p.get("version"), e["version"])]
    return max(hits, key=lambda e: len(e["version"])) if hits else None


def open_vmsa(p: Dict[str, Any], vmsa: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    b = build_no(p.get("build"))
    if b is None:
        return []
    return [a for a in vmsa if a["product"] == p["product"] and in_line(p.get("version"), a["version"]) and b < a["fixedBuild"]]


def evaluate(products: List[Dict[str, Any]], eogs: List[Dict[str, Any]], vmsa: List[Dict[str, Any]], certs: List[Dict[str, Any]],
             today: dt.date, now: dt.datetime, cert_days: Sequence[int] = CERT_DAYS) -> Dict[str, Any]:
    life, exposed = [], []
    for p in products:
        e = match_eogs(p, eogs)
        row = {**p, "eogs": e["eogs"].isoformat() if e else None, "eotg": e["eotg"].isoformat() if e and e.get("eotg") else None,
               "line": e["version"] if e else None, "daysLeft": (e["eogs"] - today).days if e else None,
               "level": eogs_level(e["eogs"], today) if e else "na"}
        life.append(row)
        for a in open_vmsa(p, vmsa):
            exposed.append({**p, "vmsa": a["vmsa"], "title": a.get("title"), "cvss": a["cvss"], "fixedBuild": a["fixedBuild"],
                            "level": vmsa_level(a["cvss"])})
    rank = {"crit": 0, "bad": 1, "warn": 2, "ok": 3, "na": 4}
    life.sort(key=lambda r: (rank[r["level"]], r["product"], r["name"]))
    exposed.sort(key=lambda r: (-r["cvss"], r["vmsa"], r["name"]))
    cert_rows = []
    for c in certs:
        na = c.get("notAfter")
        lv = "na" if c.get("error") else cert_level(na, now, cert_days)      # not readable from here: shown, never alerted
        cert_rows.append({**c, "notAfter": na.isoformat() + "Z" if na else None,
                          "daysLeft": int((na - now).total_seconds() // 86400) if na else None, "level": lv})
    cert_rows.sort(key=lambda r: (rank[r["level"]], r["daysLeft"] if r["daysLeft"] is not None else 10 ** 6, r["target"]))
    return {"lifecycle": life, "vmsa": exposed, "certs": cert_rows}
