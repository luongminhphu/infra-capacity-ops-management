"""Backup status analysis — the operations criteria of the module.

Pure functions over plain dicts (``analyze``) plus a loader (``build_report``)
that reads the database. Criteria, all thresholds configurable
(``BackupThresholds``, stored in app_settings with the ``backup_`` prefix):

1. Job success rate      completed backup jobs in the period, counted on *leaf*
                         jobs (a parent/discovery job is not counted twice with
                         its children); status 0 = success, 1 = partial
                         (counted as successful but reported), ≥ 2 = failed.
2. RPO per client        hours since the last successful backup (status 0/1)
                         vs. the RPO target; consecutive failures.
3. Failure causes        status codes grouped by who must act (client/network,
                         data, storage, snapshot, window, configuration).
4. Policy health         inactive policies, active policies that did not run,
                         failure rate per policy.
5. Storage               disk pool usage %, state, growth trend and days until
                         the high-water mark.
6. Running / queued      jobs running or queued longer than the limits.
7. Backup window         night jobs finishing after the window end hour.
8. Copies (DR)           duplication / replication success.
9. VM coverage           VMs in the vCenter inventory with no successful backup
                         (matched by VM name, guest host name or IP).
"""
import datetime as dt
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, fields
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..timeutil import VN_TZ
from . import status_codes as sc
from .client import client_key

SEV_ORDER = {"critical": 0, "warning": 1, "info": 2}
LOOKBACK_DAYS = 35  # history read for "last successful backup"


@dataclass
class BackupThresholds:
    success_target_pct: float = 95.0
    success_crit_pct: float = 85.0
    rpo_hours: float = 26.0
    rpo_crit_hours: float = 72.0
    consecutive_fail: int = 2
    long_running_hours: float = 12.0
    queued_hours: float = 4.0
    storage_warn_pct: float = 80.0
    storage_crit_pct: float = 90.0
    storage_days_warn: int = 30
    window_end_hour: int = 7
    coverage_days: int = 7
    coverage_exclude: str = "vCLS,template,tmpl"


THRESHOLD_META = [
    ("success_target_pct", "Tỷ lệ backup thành công mục tiêu", "%"),
    ("success_crit_pct", "Tỷ lệ thành công mức nghiêm trọng (dưới)", "%"),
    ("rpo_hours", "RPO mục tiêu — tối đa giờ từ bản backup thành công gần nhất", "giờ"),
    ("rpo_crit_hours", "Quá RPO mức nghiêm trọng", "giờ"),
    ("consecutive_fail", "Số lần lỗi liên tiếp mức nghiêm trọng", "lần"),
    ("long_running_hours", "Job chạy quá lâu", "giờ"),
    ("queued_hours", "Job chờ (queued) quá lâu", "giờ"),
    ("storage_warn_pct", "Disk pool — mức cảnh báo", "%"),
    ("storage_crit_pct", "Disk pool — mức nghiêm trọng", "%"),
    ("storage_days_warn", "Cảnh báo khi disk pool đầy trong vòng", "ngày"),
    ("window_end_hour", "Giờ kết thúc cửa sổ backup đêm (giờ VN)", "giờ"),
    ("coverage_days", "VM được coi là có backup nếu thành công trong vòng", "ngày"),
    ("coverage_exclude", "Bỏ qua VM có tên chứa (phân cách bởi dấu phẩy)", ""),
]


def thresholds_from(overrides: Dict[str, Any]) -> BackupThresholds:
    th = BackupThresholds()
    for f in fields(BackupThresholds):
        raw = overrides.get("backup_" + f.name)
        if raw is None:
            continue
        cur = getattr(th, f.name)
        try:
            val = str(raw) if isinstance(cur, str) else (int(float(raw)) if isinstance(cur, int) else float(raw))
        except (TypeError, ValueError):
            continue
        setattr(th, f.name, val)
    return th


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _tb(kb: Optional[float]) -> float:
    return (kb or 0) / 1024 ** 3


def _hours(a: Optional[dt.datetime], b: Optional[dt.datetime]) -> Optional[float]:
    if not a or not b:
        return None
    return (b - a).total_seconds() / 3600.0


def _vn(t: Optional[dt.datetime]) -> Optional[dt.datetime]:
    return t.replace(tzinfo=dt.timezone.utc).astimezone(VN_TZ) if t else None


def _pct(a: float, b: float) -> Optional[float]:
    return round(a * 100.0 / b, 1) if b else None


def _finding(sev: str, area: str, entity: str, title: str, detail: str, action: str) -> Dict[str, Any]:
    return {"severity": sev, "area": area, "entity": entity, "title": title, "detail": detail, "action": action}


def _leaf_jobs(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    parents = {(j["nbuKey"], j["parentJobId"]) for j in jobs if j.get("parentJobId")}
    return [j for j in jobs if (j["nbuKey"], j["jobId"]) not in parents]


def _linreg_slope(points: List[Tuple[float, float]]) -> Optional[float]:
    if len(points) < 3:
        return None
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    den = sum((p[0] - mx) ** 2 for p in points)
    if den <= 0:
        return None
    return sum((p[0] - mx) * (p[1] - my) for p in points) / den


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------
def analyze(jobs: List[Dict[str, Any]], policies: List[Dict[str, Any]], storage: List[Dict[str, Any]],
            storage_history: List[Dict[str, Any]], vms: Optional[List[Dict[str, Any]]], th: BackupThresholds,
            now: dt.datetime, days: int = 7) -> Dict[str, Any]:
    """jobs: dicts with nbuKey, jobId, parentJobId, jobType, category, policyName, policyType, scheduleType,
    clientName, clientKey, state, status, startTime, endTime, elapsedSec, kbytes (≥ LOOKBACK_DAYS of history)."""
    start = now - dt.timedelta(days=days)
    day1 = now - dt.timedelta(hours=24)
    backup_all = _leaf_jobs([j for j in jobs if j["category"] == "backup"])
    backup = [j for j in backup_all if j["startTime"] and j["startTime"] >= start]
    done = [j for j in backup if j["status"] is not None]
    outcome = Counter(sc.outcome(j["status"]) for j in done)
    ok_n = outcome["success"] + outcome["partial"]
    rate = _pct(ok_n, len(done))
    done24 = [j for j in done if j["startTime"] >= day1]
    rate24 = _pct(sum(1 for j in done24 if (j["status"] or 0) <= 1), len(done24))
    data_tb = sum(_tb(j["kbytes"]) for j in done if (j["status"] or 0) <= 1)
    data24 = sum(_tb(j["kbytes"]) for j in done24 if (j["status"] or 0) <= 1)
    running = [j for j in backup_all if j["status"] is None and (j["startTime"] or now) >= now - dt.timedelta(days=7)]

    # -- daily trend (VN calendar days) --------------------------------------
    daily_map: Dict[str, Dict[str, float]] = {}
    for d in range(days - 1, -1, -1):
        key = _vn(now - dt.timedelta(days=d)).strftime("%Y-%m-%d")
        daily_map[key] = {"success": 0, "partial": 0, "failed": 0, "tb": 0.0}
    for j in done:
        k = _vn(j["startTime"]).strftime("%Y-%m-%d")
        if k in daily_map:
            o = sc.outcome(j["status"])
            daily_map[k][o] += 1
            if o != "failed":
                daily_map[k]["tb"] += _tb(j["kbytes"])
    daily = [{"date": k, **{kk: (round(v, 2) if kk == "tb" else int(v)) for kk, v in d.items()}} for k, d in daily_map.items()]

    # -- failure causes ------------------------------------------------------
    failed = [j for j in done if (j["status"] or 0) > 1]
    code_n = Counter(j["status"] for j in failed)
    code_clients: Dict[int, set] = defaultdict(set)
    grp_n: Counter = Counter()
    grp_clients: Dict[str, set] = defaultdict(set)
    for j in failed:
        g = sc.group_of(j["status"])
        grp_n[g] += 1
        grp_clients[g].add(j["clientName"] or "?")
        code_clients[j["status"]].add(j["clientName"] or "?")
    top_codes = [{"code": c, "desc": sc.describe(c), "group": sc.GROUPS[sc.group_of(c)], "count": n,
                  "clients": sorted(code_clients[c])[:8], "clientCount": len(code_clients[c])} for c, n in code_n.most_common(12)]
    failure_groups = [{"group": g, "label": sc.GROUPS[g], "short": sc.GROUP_SHORT[g], "count": n, "clientCount": len(grp_clients[g]),
                       "action": sc.GROUP_ACTION[g]} for g, n in grp_n.most_common()]
    partial_clients = sorted({j["clientName"] for j in done if j["status"] == 1 and j["clientName"]})

    # -- policies (latest snapshot) -------------------------------------------
    pol_by_name = {(p["nbuKey"], p["name"]): p for p in policies}
    active_clients: Dict[str, List[str]] = defaultdict(list)  # client key → policy names
    for p in policies:
        if p.get("active"):
            for c in p.get("clients") or []:
                active_clients[client_key(c)].append(p["name"])

    # -- clients -------------------------------------------------------------
    by_client: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for j in backup_all:
        if j["clientKey"]:
            by_client[j["clientKey"]].append(j)
    for ck in active_clients:
        by_client.setdefault(ck, [])
    clients = []
    for ck, js in by_client.items():
        js_done = sorted([j for j in js if j["status"] is not None], key=lambda j: j["endTime"] or j["startTime"], reverse=True)
        last = js_done[0] if js_done else None
        last_ok = next((j for j in js_done if j["status"] <= 1), None)
        consec = 0
        for j in js_done:
            if j["status"] > 1:
                consec += 1
            else:
                break
        hrs = _hours(last_ok["endTime"] or last_ok["startTime"], now) if last_ok else None
        win = [j for j in js_done if j["startTime"] >= start]
        win_fail = sum(1 for j in win if j["status"] > 1)
        name = (last or (js[0] if js else None) or {}).get("clientName") or ck
        pols = sorted({j["policyName"] for j in js if j["policyName"]} | set(active_clients.get(ck, [])))
        if not js_done:
            status, label, note = "crit", "Không có job", "Có trong policy đang bật nhưng không có job backup nào trong 35 ngày."
        elif not last_ok:
            status, label, note = "crit", "Chưa backup được", f"Không có bản backup thành công trong {LOOKBACK_DAYS} ngày; lỗi gần nhất: {sc.describe(last['status'])}."
        elif consec >= th.consecutive_fail or (hrs or 0) > th.rpo_crit_hours:
            status, label = "crit", ("Lỗi liên tiếp" if consec >= th.consecutive_fail else "Quá RPO")
            note = f"{consec} lần lỗi liên tiếp" if consec else ""
            note = (note + "; " if note else "") + f"bản backup thành công gần nhất cách đây {hrs:.0f} giờ."
        elif hrs > th.rpo_hours:
            status, label, note = "warn", "Quá RPO", f"Bản backup thành công gần nhất cách đây {hrs:.0f} giờ (RPO {th.rpo_hours:.0f} giờ)."
        elif last["status"] > 1:
            status, label, note = "warn", "Lỗi lần gần nhất", sc.describe(last["status"])
        elif last["status"] == 1:
            status, label, note = "warn", "Thành công một phần", "Có file không backup được ở lần gần nhất."
        else:
            status, label, note = "ok", "Đạt", ""
        clients.append({
            "client": name, "key": ck, "policies": pols, "policyTypes": sorted({j["policyType"] for j in js if j["policyType"]}),
            "lastJobAt": (last["endTime"] or last["startTime"]) if last else None, "lastStatus": last["status"] if last else None,
            "lastStatusText": sc.describe(last["status"]) if last else "—",
            "lastSuccessAt": (last_ok["endTime"] or last_ok["startTime"]) if last_ok else None,
            "hoursSinceSuccess": round(hrs, 1) if hrs is not None else None, "consecutiveFail": consec,
            "jobs": len(win), "failed": win_fail, "tb": round(sum(_tb(j["kbytes"]) for j in win if j["status"] <= 1), 3),
            "status": status, "statusLabel": label, "note": note,
        })
    clients.sort(key=lambda c: ({"crit": 0, "warn": 1, "ok": 2}[c["status"]], -(c["hoursSinceSuccess"] or 1e9), c["client"]))
    rpo_ok = sum(1 for c in clients if c["hoursSinceSuccess"] is not None and c["hoursSinceSuccess"] <= th.rpo_hours)

    # -- policy table --------------------------------------------------------
    pol_jobs: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for j in backup:
        if j["policyName"]:
            pol_jobs[(j["nbuKey"], j["policyName"])].append(j)
    last_run: Dict[Tuple[str, str], dt.datetime] = {}
    for j in backup_all:
        k = (j["nbuKey"], j["policyName"])
        if j["startTime"] and (k not in last_run or j["startTime"] > last_run[k]):
            last_run[k] = j["startTime"]
    policy_rows = []
    for k in sorted(set(pol_by_name) | set(pol_jobs)):
        p = pol_by_name.get(k, {"nbuKey": k[0], "name": k[1], "policyType": None, "active": None, "clients": [], "schedules": []})
        js = [j for j in pol_jobs.get(k, []) if j["status"] is not None]
        f = sum(1 for j in js if j["status"] > 1)
        lr = last_run.get(k)
        n_clients = len(p.get("clients") or []) or len({j["clientKey"] for j in pol_jobs.get(k, []) if j["clientKey"]})
        freq = min([s.get("frequencySec") or 86400 for s in p.get("schedules") or []] or [86400])
        if p.get("active") is False:
            st, lbl = "na", "Không hoạt động"
        elif not lr or _hours(lr, now) > max(48.0, freq / 1800.0):
            st, lbl = "crit", "Không chạy"
        elif js and _pct(len(js) - f, len(js)) < th.success_crit_pct:
            st, lbl = "crit", "Lỗi nhiều"
        elif f:
            st, lbl = "warn", "Có lỗi"
        else:
            st, lbl = "ok", "Đạt"
        policy_rows.append({
            "nbuKey": k[0], "name": k[1], "policyType": p.get("policyType") or (pol_jobs.get(k) or [{}])[0].get("policyType"),
            "active": p.get("active"), "clients": n_clients, "storage": p.get("storage"),
            "schedules": ", ".join(f"{s.get('name')} ({s.get('retention') or '—'})" for s in p.get("schedules") or []),
            "lastRunAt": lr, "jobs": len(js), "failed": f, "successRate": _pct(len(js) - f, len(js)), "status": st, "statusLabel": lbl,
        })
    policy_rows.sort(key=lambda r: ({"crit": 0, "warn": 1, "ok": 2, "na": 3}[r["status"]], r["name"]))

    # -- storage -------------------------------------------------------------
    hist: Dict[Tuple[str, str], List[Tuple[dt.datetime, float, float]]] = defaultdict(list)
    for s in storage_history:
        if s.get("usedBytes") is not None and s.get("totalBytes"):
            hist[(s["nbuKey"], s["name"])].append((s["capturedAt"], s["usedBytes"], s["totalBytes"]))
    storage_rows = []
    for s in storage:
        tot, used = s.get("totalBytes"), s.get("usedBytes")
        pct = _pct(used, tot) if tot and used is not None else None
        h = sorted(hist.get((s["nbuKey"], s["name"]), []))
        recent = [(_hours(h[0][0], t) / 24.0, u) for t, u, _ in h if t >= now - dt.timedelta(days=30)] if h else []
        slope = _linreg_slope(recent)  # bytes / day
        hwm = (s.get("highWaterPct") or 98.0) / 100.0
        dtf = ((tot * hwm - used) / slope) if (slope and slope > 0 and tot and used is not None) else None
        if s.get("state") == "DOWN":
            st, lbl = "crit", "DOWN"
        elif pct is not None and pct >= th.storage_crit_pct:
            st, lbl = "crit", "Sắp đầy"
        elif dtf is not None and dtf < th.storage_days_warn / 2:
            st, lbl = "crit", "Sắp đầy"
        elif (pct is not None and pct >= th.storage_warn_pct) or (dtf is not None and dtf < th.storage_days_warn):
            st, lbl = "warn", "Cần theo dõi"
        else:
            st, lbl = "ok", "Đạt"
        storage_rows.append({
            "nbuKey": s["nbuKey"], "name": s["name"], "type": s.get("storageType"), "state": s.get("state"),
            "totalTB": round(tot / 1024 ** 4, 2) if tot else None, "usedTB": round(used / 1024 ** 4, 2) if used is not None else None,
            "freeTB": round((tot - used) / 1024 ** 4, 2) if tot and used is not None else None, "usedPct": pct,
            "highWaterPct": s.get("highWaterPct"), "growthTBPerDay": round(slope / 1024 ** 4, 3) if slope is not None else None,
            "daysToFull": round(dtf) if dtf is not None and dtf >= 0 else (0 if dtf is not None else None),
            "status": st, "statusLabel": lbl,
        })
    storage_rows.sort(key=lambda r: -(r["usedPct"] or 0))
    trend_dates = sorted({_vn(t).strftime("%Y-%m-%d") for v in hist.values() for t, _, _ in v if t >= now - dt.timedelta(days=30)})
    trend_series = []
    for (k, name), pts in sorted(hist.items()):
        last_by_day: Dict[str, float] = {}
        for t, u, tot in sorted(pts):
            if t >= now - dt.timedelta(days=30):
                last_by_day[_vn(t).strftime("%Y-%m-%d")] = round(u * 100.0 / tot, 1)
        trend_series.append({"name": name, "nbuKey": k, "values": [last_by_day.get(d) for d in trend_dates]})

    # -- long running / queued -------------------------------------------------
    long_jobs = []
    for j in running:
        age = _hours(j["startTime"], now) or 0
        if j["state"] == "QUEUED" and age > th.queued_hours:
            long_jobs.append({**_job_brief(j), "ageHours": round(age, 1), "kind": "Chờ quá lâu"})
        elif j["state"] != "QUEUED" and age > th.long_running_hours:
            long_jobs.append({**_job_brief(j), "ageHours": round(age, 1), "kind": "Chạy quá lâu"})

    # -- backup window overrun -------------------------------------------------
    overrun = []
    for j in done:
        s, e = _vn(j["startTime"]), _vn(j["endTime"])
        if not e:
            continue
        night = s.hour >= 18 or s.hour < th.window_end_hour
        end_limit = (s if s.hour < th.window_end_hour else s + dt.timedelta(days=1)).replace(hour=th.window_end_hour, minute=0, second=0)
        if night and e > end_limit:
            overrun.append(j)

    # -- copies (duplication / replication) -----------------------------------
    copies = [j for j in jobs if j["category"] == "copy" and j["startTime"] and j["startTime"] >= start and j["status"] is not None]
    copy_failed = [j for j in copies if j["status"] > 1]

    # -- VM coverage -------------------------------------------------------------
    coverage = _coverage(vms, backup_all, policies, th, now)

    # -- findings ------------------------------------------------------------------
    findings = []
    if rate is not None:
        if rate < th.success_crit_pct:
            findings.append(_finding("critical", "Job", "Toàn hệ thống", f"Tỷ lệ backup thành công {_fmt(rate)}% — dưới mức {_fmt(th.success_crit_pct, 0)}%",
                                     f"{len(failed)} / {len(done)} job lỗi trong {days} ngày.", "Xử lý theo nhóm nguyên nhân lỗi bên dưới, ưu tiên nhóm có nhiều client nhất."))
        elif rate < th.success_target_pct:
            findings.append(_finding("warning", "Job", "Toàn hệ thống", f"Tỷ lệ backup thành công {_fmt(rate)}% — chưa đạt mục tiêu {_fmt(th.success_target_pct, 0)}%",
                                     f"{len(failed)} / {len(done)} job lỗi trong {days} ngày.", "Xử lý các client lỗi lặp lại để đưa tỷ lệ về mục tiêu."))
    for c in clients:
        if c["status"] == "crit":
            findings.append(_finding("critical", "Client", c["client"], f"{c['client']}: {c['statusLabel'].lower()}", c["note"],
                                     sc.GROUP_ACTION[sc.group_of(c["lastStatus"])] if c["lastStatus"] and c["lastStatus"] > 1
                                     else "Kiểm tra lịch chạy và trạng thái client trong policy."))
    rpo_warn = [c for c in clients if c["status"] == "warn" and c["statusLabel"] == "Quá RPO"]
    if rpo_warn:
        findings.append(_finding("warning", "Client", f"{len(rpo_warn)} client", f"{len(rpo_warn)} client quá RPO {th.rpo_hours:.0f} giờ",
                                 ", ".join(c["client"] for c in rpo_warn[:10]), "Kiểm tra lịch backup và chạy bù (manual backup) cho các client này."))
    if partial_clients:
        findings.append(_finding("warning", "Job", f"{len(partial_clients)} client", f"{len(partial_clients)} client có job thành công một phần (status 1)",
                                 ", ".join(partial_clients[:10]), "Rà soát file bị bỏ qua (file khoá, quyền truy cập, đường dẫn không tồn tại) trong log job."))
    for g in failure_groups[:3]:
        findings.append(_finding("warning" if g["count"] < 10 else "critical", "Nguyên nhân lỗi", g["label"],
                                 f"{g['count']} job lỗi do {g['label'].lower()} ({g['clientCount']} client)", "", g["action"]))
    for p in policy_rows:
        if p["status"] == "crit" and p["statusLabel"] == "Không chạy":
            findings.append(_finding("critical", "Policy", p["name"], f"Policy {p['name']} không chạy",
                                     "Policy đang bật nhưng không có job backup trong 48 giờ gần nhất.",
                                     "Kiểm tra lịch (schedule), cửa sổ backup và trạng thái frozen/suspended của policy."))
    inactive = [p for p in policy_rows if p["status"] == "na"]
    if inactive:
        findings.append(_finding("info", "Policy", f"{len(inactive)} policy", f"{len(inactive)} policy đang tắt (inactive)",
                                 ", ".join(p["name"] for p in inactive[:10]), "Xác nhận đây là chủ đích; gỡ policy không còn dùng để tránh hiểu nhầm về độ phủ."))
    for s in storage_rows:
        if s["status"] in ("crit", "warn"):
            dtf = f"; dự kiến chạm ngưỡng high-water sau ~{s['daysToFull']} ngày" if s["daysToFull"] is not None else ""
            what = "đang DOWN" if s["state"] == "DOWN" else f"đã dùng {_fmt(s['usedPct'], 0)}%"
            findings.append(_finding("critical" if s["status"] == "crit" else "warning", "Storage", s["name"],
                                     f"Disk pool {s['name']} {what}",
                                     f"Còn trống {s['freeTB']} TB{dtf}.",
                                     "Rà soát retention, image hết hạn chưa dọn, chạy dọn dẹp (image cleanup) hoặc lập kế hoạch mở rộng dung lượng."))
    for j in long_jobs[:10]:
        findings.append(_finding("warning", "Job", j["client"] or j["policy"], f"Job {j['jobId']} {j['kind'].lower()} ({j['ageHours']:.0f} giờ)",
                                 f"Policy {j['policy']}, trạng thái {j['state']}.", "Kiểm tra tài nguyên media server / storage unit và job đang giữ tài nguyên."))
    if overrun:
        cl = Counter(j["clientName"] for j in overrun)
        findings.append(_finding("info", "Cửa sổ backup", f"{len(overrun)} job", f"{len(overrun)} job đêm kết thúc sau {th.window_end_hour}:00",
                                 "Nhiều nhất: " + ", ".join(f"{c} ({n})" for c, n in cl.most_common(5)),
                                 "Dời lịch full, tăng luồng song song hoặc chuyển sang incremental forever / accelerator."))
    if copy_failed:
        findings.append(_finding("warning", "Bản sao DR", f"{len(copy_failed)} job", f"{len(copy_failed)} job nhân bản (duplication/replication) lỗi",
                                 f"Tỷ lệ thành công bản sao: {_fmt(_pct(len(copies) - len(copy_failed), len(copies)))}%.",
                                 "Kiểm tra SLP backlog (nbstlutil report) và kết nối tới storage đích."))
    if coverage.get("available"):
        if coverage["unprotected"]:
            findings.append(_finding("critical" if coverage["pct"] is not None and coverage["pct"] < 90 else "warning", "Độ phủ VM",
                                     f"{coverage['unprotected']} VM", f"{coverage['unprotected']} VM đang chạy chưa có trong backup",
                                     ", ".join(v["vm"] for v in coverage["unprotectedVms"][:10]),
                                     "Xác nhận với System Owner VM có cần backup; bổ sung vào query của VMware policy hoặc ghi nhận ngoại lệ."))
        if coverage["failing"]:
            findings.append(_finding("warning", "Độ phủ VM", f"{coverage['failing']} VM", f"{coverage['failing']} VM có job nhưng chưa backup thành công trong {th.coverage_days} ngày",
                                     ", ".join(v["vm"] for v in coverage["failingVms"][:10]), "Xem lỗi job snapshot / backup của từng VM."))
    findings.sort(key=lambda f: SEV_ORDER[f["severity"]])
    counts = {k: sum(1 for f in findings if f["severity"] == k) for k in SEV_ORDER}
    readiness = "crit" if counts["critical"] else ("warn" if counts["warning"] else "ok")

    crit_clients = sum(1 for c in clients if c["status"] == "crit")
    worst_pool = storage_rows[0] if storage_rows else None
    kpis = {
        "jobs": len(done), "success": outcome["success"], "partial": outcome["partial"], "failed": outcome["failed"],
        "running": len(running), "successRate": rate, "successRate24h": rate24, "jobs24h": len(done24),
        "dataTB": round(data_tb, 2), "dataTB24h": round(data24, 2),
        "clients": len(clients), "clientsOk": sum(1 for c in clients if c["status"] == "ok"), "clientsCrit": crit_clients,
        "clientsWarn": sum(1 for c in clients if c["status"] == "warn"), "rpoCompliancePct": _pct(rpo_ok, len(clients)),
        "policies": len(policy_rows), "policiesActive": sum(1 for p in policy_rows if p["active"] is not False),
        "storageMaxPct": worst_pool["usedPct"] if worst_pool else None, "storageMaxName": worst_pool["name"] if worst_pool else None,
        "copyJobs": len(copies), "copyFailed": len(copy_failed), "copyRate": _pct(len(copies) - len(copy_failed), len(copies)),
        "coveragePct": coverage.get("pct"), "longJobs": len(long_jobs), "windowOverrun": len(overrun),
        "avgDurationMin": round(sum(j["elapsedSec"] or 0 for j in done) / len(done) / 60, 1) if done else None,
    }
    return {
        "period": {"days": days, "from": start, "to": now},
        "thresholds": asdict(th),
        "kpis": kpis, "daily": daily, "failureGroups": failure_groups, "topCodes": top_codes,
        "clients": clients, "policies": policy_rows, "storage": storage_rows,
        "storageTrend": {"dates": trend_dates, "series": trend_series},
        "longJobs": long_jobs, "coverage": coverage, "findings": findings, "findingCounts": counts,
        "readiness": readiness, "headline": _headline(kpis, th, coverage, days),
        "failedJobs": [_job_brief(j) for j in sorted(failed, key=lambda j: j["startTime"], reverse=True)[:500]],
    }


def _job_brief(j: Dict[str, Any]) -> Dict[str, Any]:
    return {"nbuKey": j["nbuKey"], "jobId": j["jobId"], "jobType": j["jobType"], "policy": j["policyName"], "schedule": sc.schedule_label(j.get("scheduleType")),
            "client": j["clientName"], "state": j["state"], "status": j["status"], "statusText": sc.describe(j["status"]),
            "group": sc.GROUPS[sc.group_of(j["status"])] if j["status"] and j["status"] > 1 else "",
            "startTime": j["startTime"], "endTime": j["endTime"], "elapsedSec": j["elapsedSec"],
            "gb": round((j["kbytes"] or 0) / 1024 ** 2, 2)}


def _coverage(vms: Optional[List[Dict[str, Any]]], backup_all: List[Dict[str, Any]], policies: List[Dict[str, Any]],
              th: BackupThresholds, now: dt.datetime) -> Dict[str, Any]:
    if not vms:
        return {"available": False}
    excl = [x.strip().lower() for x in (th.coverage_exclude or "").split(",") if x.strip()]
    since = now - dt.timedelta(days=th.coverage_days)
    seen: Dict[str, str] = {}  # client key → best outcome ('ok' beats 'fail')
    for j in backup_all:
        ck = j["clientKey"]
        if not ck or j["status"] is None:
            continue
        if j["status"] <= 1 and (j["endTime"] or j["startTime"]) >= since:
            seen[ck] = "ok"
        else:
            seen.setdefault(ck, "fail")
    for p in policies:
        if p.get("active"):
            for c in p.get("clients") or []:
                seen.setdefault(client_key(c), "fail")
    rows, excluded, off = [], 0, 0
    for v in vms:
        name = v.get("vm") or ""
        if any(e in name.lower() for e in excl):
            excluded += 1
            continue
        if not v.get("isPoweredOn"):
            off += 1
            continue
        keys = [k for k in (client_key(name), client_key(v.get("guestHostname")), v.get("ipAddress")) if k]
        res = next((seen[k] for k in keys if seen.get(k) == "ok"), None) or next((seen[k] for k in keys if k in seen), None)
        rows.append({"vm": name, "vcenterKey": v.get("vcenterKey"), "cluster": v.get("cluster"), "ip": v.get("ipAddress"),
                     "vcpu": v.get("vcpu"), "ramGB": v.get("ramGB"), "diskGB": v.get("diskProvGB"),
                     "state": "protected" if res == "ok" else ("failing" if res == "fail" else "unprotected")})
    n = len(rows)
    prot = sum(1 for r in rows if r["state"] == "protected")
    by_cluster: Dict[Tuple[str, str], Counter] = defaultdict(Counter)
    for r in rows:
        by_cluster[(r["vcenterKey"] or "import", r["cluster"])][r["state"]] += 1
    return {
        "available": True, "total": n, "protected": prot,
        "failing": sum(1 for r in rows if r["state"] == "failing"),
        "unprotected": sum(1 for r in rows if r["state"] == "unprotected"),
        "excluded": excluded, "poweredOff": off, "pct": _pct(prot, n),
        "byCluster": sorted([{"vcenterKey": k[0], "cluster": k[1], "total": sum(c.values()), "protected": c["protected"],
                              "failing": c["failing"], "unprotected": c["unprotected"], "pct": _pct(c["protected"], sum(c.values()))}
                             for k, c in by_cluster.items()], key=lambda r: (r["pct"] if r["pct"] is not None else 101, r["cluster"])),
        "unprotectedVms": [r for r in rows if r["state"] == "unprotected"],
        "failingVms": [r for r in rows if r["state"] == "failing"],
    }


def _fmt(v: Optional[float], d: int = 1) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v:,.{d}f}".replace(",", "§").replace(".", ",").replace("§", ".")


def _headline(k: Dict[str, Any], th: BackupThresholds, cov: Dict[str, Any], days: int) -> str:
    if not k["jobs"]:
        return "Chưa có dữ liệu job backup trong kỳ — kiểm tra kết nối NetBackup hoặc chạy đồng bộ."
    parts = [f"Tỷ lệ backup thành công {days} ngày {_fmt(k['successRate'])}% (mục tiêu {_fmt(th.success_target_pct, 0)}%)"]
    if k["clients"]:
        parts.append(f"{_fmt(k['rpoCompliancePct'])}% client đạt RPO {_fmt(th.rpo_hours, 0)} giờ")
    if k["clientsCrit"]:
        parts.append(f"{k['clientsCrit']} client cần xử lý ngay")
    if cov.get("available") and cov.get("unprotected"):
        parts.append(f"{cov['unprotected']} VM chưa được backup")
    if k["storageMaxPct"] is not None and k["storageMaxPct"] >= th.storage_warn_pct:
        parts.append(f"disk pool {k['storageMaxName']} đã dùng {_fmt(k['storageMaxPct'], 0)}%")
    return " · ".join(parts) + "."


# ---------------------------------------------------------------------------
# Database loader
# ---------------------------------------------------------------------------
def _job_dict(r) -> Dict[str, Any]:
    return {"nbuKey": r.nbu_key, "jobId": r.job_id, "parentJobId": r.parent_job_id, "jobType": r.job_type, "category": r.category,
            "policyName": r.policy_name, "policyType": r.policy_type, "scheduleType": r.schedule_type, "clientName": r.client_name,
            "clientKey": r.client_key, "state": r.state, "status": r.status, "startTime": r.start_time, "endTime": r.end_time,
            "elapsedSec": r.elapsed_sec, "kbytes": r.kbytes}


def load_inputs(db, keys: Optional[List[str]], now: dt.datetime, days: int) -> Dict[str, Any]:
    from ..models import BackupJob, BackupPolicySnapshot, BackupStorageSnapshot, BackupSyncRun
    since = now - dt.timedelta(days=max(days, LOOKBACK_DAYS))
    q = db.query(BackupJob).filter(BackupJob.start_time >= since)
    if keys is not None:
        q = q.filter(BackupJob.nbu_key.in_(keys))
    jobs = [_job_dict(r) for r in q.all()]
    all_keys = keys if keys is not None else [r[0] for r in db.query(BackupSyncRun.nbu_key).distinct().all()]
    policies, storage, history = [], [], []
    for k in all_keys:
        prun = (db.query(BackupSyncRun).filter(BackupSyncRun.nbu_key == k, BackupSyncRun.status == "ok", BackupSyncRun.policy_count > 0)
                .order_by(BackupSyncRun.started_at.desc()).first())
        if prun:
            for p in db.query(BackupPolicySnapshot).filter(BackupPolicySnapshot.run_id == prun.id).all():
                policies.append({"nbuKey": k, "name": p.name, "policyType": p.policy_type, "active": p.active,
                                 "clients": json.loads(p.clients_json or "[]"), "schedules": json.loads(p.schedules_json or "[]"),
                                 "storage": p.storage, "selection": p.selection})
        srun = (db.query(BackupSyncRun).filter(BackupSyncRun.nbu_key == k, BackupSyncRun.status == "ok", BackupSyncRun.pool_count > 0)
                .order_by(BackupSyncRun.started_at.desc()).first())
        if srun:
            storage += [_storage_dict(s) for s in srun.storage]
        history += [_storage_dict(s) for s in db.query(BackupStorageSnapshot).filter(
            BackupStorageSnapshot.nbu_key == k, BackupStorageSnapshot.captured_at >= now - dt.timedelta(days=31)).all()]
    return {"jobs": jobs, "policies": policies, "storage": storage, "history": history}


def _storage_dict(s) -> Dict[str, Any]:
    return {"nbuKey": s.nbu_key, "name": s.name, "storageType": s.storage_type, "totalBytes": s.total_bytes, "usedBytes": s.used_bytes,
            "freeBytes": s.free_bytes, "highWaterPct": s.high_water_pct, "state": s.state, "capturedAt": s.captured_at}


def build_report(db, keys: Optional[List[str]] = None, days: int = 7, th: Optional[BackupThresholds] = None,
                 vms: Optional[List[Dict[str, Any]]] = None, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    now = now or dt.datetime.utcnow()
    inp = load_inputs(db, keys, now, days)
    return analyze(inp["jobs"], inp["policies"], inp["storage"], inp["history"], vms, th or BackupThresholds(), now, days)


def iter_jobs(db, keys: Optional[List[str]], since: dt.datetime) -> Iterable[Dict[str, Any]]:
    from ..models import BackupJob
    q = db.query(BackupJob).filter(BackupJob.start_time >= since)
    if keys is not None:
        q = q.filter(BackupJob.nbu_key.in_(keys))
    for r in q.order_by(BackupJob.start_time.desc()).limit(20000):
        d = _job_dict(r)
        d.update({"scheduleName": r.schedule_name, "scheduleLabel": sc.schedule_label(r.schedule_type), "files": r.files, "storageUnit": r.storage_unit, "mediaServer": r.media_server,
                  "statusText": sc.describe(r.status), "group": sc.GROUPS[sc.group_of(r.status)] if r.status and r.status > 1 else "",
                  "outcome": sc.outcome(r.status)})
        yield d
