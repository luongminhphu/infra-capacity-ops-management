"""Backup schedule (SLA) per system and the nightly compliance check.

The company schedule (HSC-Backup-Management workbook) is defined per *system*, each
backed up by one or more NetBackup policies, e.g.

    P-CORE-EQUITY   Full Friday, Incremental other days, 20:00 → 04:00 next day, keep Full 14 days
    OTBK-TRADE      Full Thursday only,                    22:00 → 04:00 next day, keep Full 90 days

A night's run spans two calendar days, so every job is assigned to a *backup day*: a job
that starts before the cut-off hour (default 12:00, local time) belongs to the previous
night. For each system and each backup day the check answers:

    ok         every client's last job of the night succeeded, on time, right type
    partial    status 1 (some files skipped)
    late       finished after the window end + grace
    wrongtype  a Full night ran only incremental schedules
    failed     at least one client still failed at the end of the night
    missing    no job at all once the window (+ grace) has closed
    running    still inside the window
    pending    the window has not opened yet

The expected RPO of a system follows its schedule: longest gap between scheduled nights
plus the window length plus grace (daily ≈ 33 h, weekly ≈ 177 h for a 20:00–04:00 window).
"""
import datetime as dt
import fnmatch
import io
import re
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..timeutil import VN_TZ

DAY_NAMES = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "CN"]
_DAY_ALIASES = {
    "monday": 0, "mon": 0, "thu 2": 0, "thứ 2": 0, "t2": 0, "thứ hai": 0,
    "tuesday": 1, "tue": 1, "thu 3": 1, "thứ 3": 1, "t3": 1, "thứ ba": 1,
    "wednesday": 2, "wed": 2, "thu 4": 2, "thứ 4": 2, "t4": 2, "thứ tư": 2,
    "thursday": 3, "thu": 3, "thu 5": 3, "thứ 5": 3, "t5": 3, "thứ năm": 3,
    "friday": 4, "fri": 4, "thu 6": 4, "thứ 6": 4, "t6": 4, "thứ sáu": 4,
    "saturday": 5, "sat": 5, "thu 7": 5, "thứ 7": 5, "t7": 5, "thứ bảy": 5,
    "sunday": 6, "sun": 6, "chủ nhật": 6, "cn": 6,
}

STATUS = {
    "ok": ("ok", "Đạt"), "partial": ("warn", "Một phần"), "late": ("warn", "Trễ"),
    "wrongtype": ("warn", "Sai loại"), "failed": ("crit", "Lỗi"), "missing": ("crit", "Thiếu"),
    "running": ("na", "Đang chạy"), "pending": ("na", "Chưa đến lịch"),
}
FINAL = {"ok", "partial", "late", "wrongtype", "failed", "missing"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def parse_days(value: Any) -> List[int]:
    """'Friday', 'Mon, Wed', 'T7', [4, 5] or '4,5' → sorted weekday numbers (Mon = 0)."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items: Iterable[Any] = value
    else:
        items = re.split(r"[,;/]|\s+và\s+|\s+and\s+", str(value))
    out = set()
    for it in items:
        if isinstance(it, int) or (isinstance(it, str) and it.strip().isdigit()):
            n = int(it)
            if 0 <= n <= 6:
                out.add(n)
            continue
        key = str(it).strip().lower()
        if key in _DAY_ALIASES:
            out.add(_DAY_ALIASES[key])
    return sorted(out)


def parse_hhmm(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, dt.time):
        return f"{value.hour:02d}:{value.minute:02d}"
    if isinstance(value, dt.datetime):
        return f"{value.hour:02d}:{value.minute:02d}"
    if isinstance(value, (int, float)) and 0 <= value < 1:  # Excel day fraction
        mins = round(value * 24 * 60)
        return f"{mins // 60:02d}:{mins % 60:02d}"
    m = re.match(r"^\s*(\d{1,2})[:hH](\d{2})?", str(value))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    return f"{h:02d}:{mi:02d}" if 0 <= h <= 23 and 0 <= mi <= 59 else None


def _hm(s: str) -> Tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def retention_days(text: Optional[str]) -> Optional[float]:
    """'14 days' / '2 weeks' / '3 months' / '1 years' (policy schedule retention) → days."""
    if not text:
        return None
    m = re.match(r"^\s*([\d.]+)\s*([a-zA-Z]+)", str(text))
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2).lower()
    mult = {"day": 1, "days": 1, "week": 7, "weeks": 7, "month": 30, "months": 30, "year": 365, "years": 365,
            "hour": 1 / 24, "hours": 1 / 24}.get(unit)
    return n * mult if mult else None


def patterns_of(rule: Dict[str, Any]) -> List[str]:
    raw = rule.get("policyPatterns") or ""
    return [p.strip() for p in re.split(r"[,\n;]", raw) if p.strip()]


def policy_matches(rule: Dict[str, Any], policy: Optional[str]) -> bool:
    if not policy:
        return False
    name = policy.lower()
    if rule.get("exact"):
        return name == str(rule.get("policyPatterns") or "").lower()
    return any(fnmatch.fnmatchcase(name, p.lower()) for p in patterns_of(rule))


def to_vn(t: dt.datetime) -> dt.datetime:
    return t.replace(tzinfo=dt.timezone.utc).astimezone(VN_TZ)


def backup_day(t: Optional[dt.datetime], cutoff_hour: int = 12) -> Optional[dt.date]:
    """Night a job belongs to: a job starting before the cut-off hour counts for the previous day."""
    if t is None:
        return None
    return (to_vn(t) - dt.timedelta(hours=cutoff_hour)).date()


def _vn_dt(day: dt.date, hhmm: str) -> dt.datetime:
    h, m = _hm(hhmm)
    return dt.datetime(day.year, day.month, day.day, h, m, tzinfo=VN_TZ)


def window(rule: Dict[str, Any], day: dt.date) -> Tuple[dt.datetime, dt.datetime]:
    """(start, end) of the backup window of night `day`, VN aware datetimes. A schedule read from
    NetBackup may have a different window per weekday (`windows`)."""
    w = (rule.get("windows") or {}).get(day.weekday()) or (rule.get("windows") or {}).get(str(day.weekday()))
    start = _vn_dt(day, w["start"] if w else rule["startTime"])
    if start.hour < rule.get("cutoffHour", 12):          # e.g. a 02:00 window belongs to the previous night
        start += dt.timedelta(days=1)
    end = start.replace(hour=_hm(w["end"] if w else rule["endTime"])[0], minute=_hm(w["end"] if w else rule["endTime"])[1])
    if end <= start:
        end += dt.timedelta(days=1)
    return start, end


def expected_type(rule: Dict[str, Any], day: dt.date) -> Optional[str]:
    wd = day.weekday()
    if rule.get("anyType"):                  # fixed window: every night, Full / Incremental not checked
        return "any" if wd in (rule.get("incrDays") or []) else None
    if _calendar_hit(rule, "full", day) or wd in (rule.get("fullDays") or []):
        return "full"
    if _calendar_hit(rule, "incr", day) or wd in (rule.get("incrDays") or []):
        return "incr"
    return None


def _calendar_hit(rule: Dict[str, Any], kind: str, night: dt.date) -> bool:
    """Calendar schedule by day of month / last day of month / specific dates. The run date is the
    night's date, or the next day when the window opens after midnight (before the cut-off)."""
    cal = (rule.get("calendar") or {}).get(kind)
    if not cal:
        return False
    run = night + dt.timedelta(days=1 if cal.get("afterMidnight") else 0)
    return (run.day in (cal.get("monthDays") or []) or (cal.get("lastDayOfMonth") and (run + dt.timedelta(days=1)).month != run.month)
            or run.isoformat() in (cal.get("dates") or []))


def rule_rpo_hours(rule: Dict[str, Any], grace_minutes: int) -> Optional[float]:
    days = sorted(set((rule.get("fullDays") or []) + (rule.get("incrDays") or [])))
    if not rule.get("startTime") or not rule.get("endTime"):
        return None
    if not days:   # only monthly calendar / "Full every N days" schedules
        gap = rule.get("fullEveryDays") or (31 if rule.get("calendar") else None)
        if not gap:
            return None
        s, e = window(rule, dt.date(2024, 1, 1))
        return gap * 24 + (e - s).total_seconds() / 3600.0 + grace_minutes / 60.0
    gaps = [((days[(i + 1) % len(days)] - d) % 7) or 7 for i, d in enumerate(days)]
    monday = dt.date(2024, 1, 1)
    win_h = max((lambda w: (w[1] - w[0]).total_seconds() / 3600.0)(window(rule, monday + dt.timedelta(days=d))) for d in days)
    return max(gaps) * 24 + win_h + grace_minutes / 60.0


# ---------------------------------------------------------------------------
# Schedule read from NetBackup policies
# ---------------------------------------------------------------------------
def _hhmm(minutes: int) -> str:
    minutes %= 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def derive_rule(policy: Dict[str, Any], cutoff_hour: int = 12) -> Optional[Dict[str, Any]]:
    """Expected nights of one policy from its NetBackup schedules:
    * Calendar schedule → its calendar weekdays (or the days its window opens);
    * Frequency ≤ 1 day → every day the window opens;
    * Frequency ≈ weekly with a window on exactly one day → that day;
      weekly with windows on several days → "Full at least every N days" (any of those nights).
    The window of each night is the window of the schedule expected that night."""
    if policy.get("active") is False:
        return None
    full_days: set = set()
    incr_days: set = set()
    win: Dict[int, Dict[str, str]] = {}
    full_every = None
    full_names, retention = [], None
    calendar: Dict[str, Dict[str, Any]] = {}
    notes: List[str] = []
    for sch in policy.get("schedules") or []:
        kind = sch.get("type")
        if kind not in ("FULL", "INCR"):
            continue
        # NetBackup windows are per calendar weekday; a window opening before the cut-off hour
        # (e.g. Tuesday 02:00) belongs to the previous night (Monday).
        night_of = lambda d, start_min: d if start_min >= cutoff_hour * 60 else (d - 1) % 7  # noqa: E731
        win_days = {night_of(w["day"], w["startMin"]): w for w in sch.get("windows") or []}
        first_start = min((w["startMin"] for w in sch.get("windows") or []), default=cutoff_hour * 60)
        freq = sch.get("frequencySec") or 0
        if sch.get("kind") == "Calendar":
            days = {night_of(d, first_start) for d in sch.get("calendarDays") or []}
            if sch.get("monthDays") or sch.get("lastDayOfMonth") or sch.get("specificDates"):
                cal = calendar.setdefault("full" if kind == "FULL" else "incr", {"monthDays": [], "lastDayOfMonth": False, "dates": []})
                cal["monthDays"] = sorted(set(cal["monthDays"]) | set(sch.get("monthDays") or []))
                cal["lastDayOfMonth"] = cal["lastDayOfMonth"] or bool(sch.get("lastDayOfMonth"))
                cal["dates"] = sorted(set(cal["dates"]) | set(sch.get("specificDates") or []))
                cal["afterMidnight"] = first_start < cutoff_hour * 60
                if win_days:
                    w0 = next(iter(win_days.values()))
                    cal["window"] = {"start": _hhmm(w0["startMin"]), "end": _hhmm(w0["startMin"] + w0["durationMin"])}
            elif not days:
                # Calendar schedule without any include date never runs on its own — not expected.
                notes.append(f"{sch.get('name')}: lịch Calendar chưa chọn ngày chạy (không tính)")
        elif freq >= 6 * 86400 and len(win_days) > 1:
            days = set()
            if kind == "FULL":
                full_every = round(freq / 86400)
        else:
            days = set(win_days)
        if kind == "FULL":
            full_names.append(sch.get("name"))
            full_days |= days
            got = retention_days(sch.get("retention"))
            retention = int(round(got)) if got else retention
        else:
            incr_days |= days
        for d in days:
            w = win_days.get(d) or (next(iter(win_days.values())) if win_days else None)
            if w and (kind == "FULL" or d not in win):
                win[d] = {"start": _hhmm(w["startMin"]), "end": _hhmm(w["startMin"] + w["durationMin"])}
    incr_days -= full_days
    if not full_days and not incr_days and not calendar and not full_every:
        return None
    common = Counter((w["start"], w["end"]) for w in win.values()).most_common(1)
    cal_win = next((c["window"] for c in calendar.values() if c.get("window")), None)
    start, end = common[0][0] if common else ((cal_win["start"], cal_win["end"]) if cal_win else ("20:00", "04:00"))
    return {"system": policy["name"], "policyPatterns": policy["name"], "exact": True, "source": "netbackup",
            "fullDays": sorted(full_days), "incrDays": sorted(incr_days), "startTime": start, "endTime": end,
            "windows": win, "fullEveryDays": full_every, "fullScheduleNames": [n for n in full_names if n],
            "retentionDays": retention, "enabled": True, "note": None, "cutoffHour": cutoff_hour,
            "calendar": calendar, "scheduleNotes": notes}


def validate_rule(r: Dict[str, Any]) -> Dict[str, Any]:
    system = str(r.get("system") or "").strip()
    if not system:
        raise ValueError("Thiếu tên hệ thống")
    start, end = parse_hhmm(r.get("startTime")), parse_hhmm(r.get("endTime"))
    if not start or not end:
        raise ValueError(f"{system}: giờ bắt đầu / kết thúc không hợp lệ (HH:MM)")
    full = parse_days(r.get("fullDays"))
    incr = [d for d in parse_days(r.get("incrDays")) if d not in full]
    if not full and not incr:
        raise ValueError(f"{system}: chưa có ngày chạy Full hoặc Incremental")
    ret = r.get("retentionDays")
    try:
        ret = int(float(ret)) if ret not in (None, "") else None
    except (TypeError, ValueError):
        raise ValueError(f"{system}: số ngày giữ bản Full không hợp lệ")
    return {"system": system[:128], "policyPatterns": str(r.get("policyPatterns") or "").strip(), "fullDays": full,
            "incrDays": incr, "startTime": start, "endTime": end, "retentionDays": ret,
            "enabled": r.get("enabled", True) is not False, "note": (r.get("note") or "").strip()[:500] or None}


# ---------------------------------------------------------------------------
# Excel import (HSC-Backup-Management template)
# ---------------------------------------------------------------------------
def parse_schedule_xlsx(data: bytes) -> List[Dict[str, Any]]:
    """Blocks: a system name in column B, a weekday header row (Monday … Sunday) below it,
    the backup type per day on the next row (values or IF formulas) and label / value pairs
    in columns J / K: 'Full Backup', 'Full backup Retention (days)', 'Start time', 'End Time'."""
    from ..platform import xlsx_io

    # Formulas kept (data_only=False): the template computes the daily type with IF(…).
    sheets = xlsx_io.read_rows(data, max_sheets=20, max_rows=3000, max_cols=30, data_only=False)
    rules: List[Dict[str, Any]] = []
    for _title, rows in sheets:
        rows = [tuple(r) for r in rows]
        get = lambda r, c: rows[r][c] if r < len(rows) and c < len(rows[r]) else None  # noqa: E731
        r = 0
        while r < len(rows):
            name = get(r, 1)
            header_row = next((rr for rr in range(r + 1, min(r + 4, len(rows)))
                               if str(get(rr, 1) or "").strip().lower() == "monday"), None)
            label_words = {"monday", "restore point", "incremental", "full backup"}
            if not (isinstance(name, str) and name.strip() and not name.strip().startswith("=")
                    and name.strip().lower() not in label_words and header_row is not None):
                r += 1
                continue
            kv: Dict[str, Any] = {}
            for rr in range(r, min(r + 8, len(rows))):
                label = get(rr, 9)
                if isinstance(label, str):
                    kv[label.strip().lower()] = get(rr, 10)
            full = parse_days(kv.get("full backup"))
            types = [get(header_row + 1, c) for c in range(1, 8)]
            incr: List[int] = []
            for wd, v in enumerate(types):
                txt = str(v or "")
                if txt.startswith("="):            # formula: IF(day = full day, "Full Backup", "Incremental" | "")
                    if '"incremental"' in txt.lower() and wd not in full:
                        incr.append(wd)
                elif "incremental" in txt.lower():
                    incr.append(wd)
                elif "full" in txt.lower() and wd not in full:
                    full.append(wd)
            if "start time" not in kv or "end time" not in kv:
                r += 1
                continue
            ret = kv.get("full backup retention (days)")
            rules.append(validate_rule({
                "system": name.strip(), "fullDays": sorted(full), "incrDays": incr,
                "startTime": kv.get("start time"), "endTime": kv.get("end time"),
                "retentionDays": ret,
            }))
            r = header_row + 2
    if not rules:
        raise ValueError("Không tìm thấy lịch backup theo mẫu (tên hệ thống ở cột B, Monday…Sunday, Start time / End Time ở cột J–K).")
    return rules


# ---------------------------------------------------------------------------
# Compliance evaluation
# ---------------------------------------------------------------------------
def _night_status(rule: Dict[str, Any], day: dt.date, jobs: List[Dict[str, Any]], now_vn: dt.datetime,
                  grace: dt.timedelta) -> Dict[str, Any]:
    exp = expected_type(rule, day)
    w_start, w_end = window(rule, day)
    deadline = w_end + grace
    base = {"date": day.isoformat(), "expected": exp}
    if now_vn < w_start and not jobs:
        return {**base, "status": "pending", "detail": "Chưa đến giờ chạy"}
    if not jobs:
        if now_vn <= deadline:
            return {**base, "status": "running", "detail": "Trong cửa sổ, chưa có job"}
        return {**base, "status": "missing", "detail": "Không có job backup nào trong đêm"}
    running = [j for j in jobs if j["status"] is None]
    by_client: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for j in jobs:
        by_client[j.get("clientKey") or j.get("clientName") or "?"].append(j)
    failed_clients, partial_clients = [], []
    for ck, js in by_client.items():
        done = sorted([j for j in js if j["status"] is not None], key=lambda j: j["endTime"] or j["startTime"])
        if not done:
            continue
        last = done[-1]
        if last["status"] > 1:
            failed_clients.append(last.get("clientName") or ck)
        elif last["status"] == 1:
            partial_clients.append(last.get("clientName") or ck)
    ends = [to_vn(j["endTime"]) for j in jobs if j["endTime"]]
    last_end = max(ends) if ends else None
    late_min = round((last_end - w_end).total_seconds() / 60) if last_end and last_end > w_end else 0
    full_names = {n.lower() for n in rule.get("fullScheduleNames") or [] if n}
    has_full = any((j.get("scheduleType") or "").upper() == "FULL" or (j.get("scheduleName") or "").lower() in full_names
                   for j in jobs if (j["status"] or 0) <= 1)
    info = {**base, "jobs": len(jobs), "clients": len(by_client), "failedClients": sorted(failed_clients),
            "partialClients": sorted(partial_clients), "lastEnd": last_end.isoformat() if last_end else None,
            "lateMinutes": late_min}
    if running and now_vn <= deadline:
        return {**info, "status": "running", "detail": f"{len(running)} job đang chạy"}
    if failed_clients:
        return {**info, "status": "failed", "detail": f"{len(failed_clients)} client lỗi: " + ", ".join(sorted(failed_clients)[:5])}
    if running:
        return {**info, "status": "late", "detail": f"{len(running)} job vẫn chạy sau giờ kết thúc {rule['endTime']}"}
    if exp == "full" and not has_full:
        return {**info, "status": "wrongtype", "detail": "Đêm Full nhưng chỉ chạy Incremental"}
    if last_end and last_end > deadline:
        return {**info, "status": "late", "detail": f"Kết thúc {last_end:%H:%M %d/%m}, trễ {late_min} phút so với {rule['endTime']}"}
    if partial_clients:
        return {**info, "status": "partial", "detail": f"{len(partial_clients)} client thành công một phần"}
    return {**info, "status": "ok", "detail": f"Xong lúc {last_end:%H:%M}" if last_end else ""}


def fixed_rule(policy_name: str, start: str = "20:00", end: str = "04:00", cutoff_hour: int = 12) -> Dict[str, Any]:
    """Fixed nightly window for one policy: a successful run every night between `start` and `end`."""
    return {"system": policy_name, "policyPatterns": policy_name, "exact": True, "source": "fixed", "anyType": True,
            "fullDays": [], "incrDays": list(range(7)), "startTime": start, "endTime": end, "windows": {},
            "retentionDays": None, "enabled": True, "note": None, "cutoffHour": cutoff_hour}


def effective_rules(manual: List[Dict[str, Any]], policies: List[Dict[str, Any]], auto: bool = True,
                    cutoff_hour: int = 12, mode: str = "netbackup", fixed_start: str = "20:00",
                    fixed_end: str = "04:00") -> List[Dict[str, Any]]:
    """Manual / Excel schedules first (they override). Every other active policy:
    mode "netbackup" → schedule read from its NetBackup schedules, or the fixed window when none can be derived;
    mode "fixed"     → the fixed nightly window (e.g. 20:00 → 04:00), backup type not checked."""
    rules = [{**r, "source": r.get("source") or "manual", "cutoffHour": cutoff_hour} for r in manual if r.get("enabled", True)]
    if not auto:
        return rules
    seen = set()
    for p in sorted(policies, key=lambda p: p.get("name") or ""):
        name = p.get("name")
        if not name or name in seen or name.startswith("SLP_") or any(policy_matches(r, name) for r in rules):
            continue
        if p.get("active") is False:
            continue
        seen.add(name)
        derived = derive_rule(p, cutoff_hour) if mode != "fixed" else None
        rules.append(derived or fixed_rule(name, fixed_start, fixed_end, cutoff_hour))
    return rules


def evaluate(rules: List[Dict[str, Any]], jobs: List[Dict[str, Any]], policies: List[Dict[str, Any]],
             now: dt.datetime, days: int, grace_minutes: int = 60, cutoff_hour: int = 12, auto: bool = False,
             mode: str = "netbackup", fixed_start: str = "20:00", fixed_end: str = "04:00") -> Dict[str, Any]:
    """jobs: leaf backup jobs (dicts as in analysis.py) covering at least `days` + 1 nights.
    auto=True adds schedules read from NetBackup for policies no manual schedule covers."""
    rules = effective_rules(rules, policies, auto, cutoff_hour, mode, parse_hhmm(fixed_start) or "20:00",
                            parse_hhmm(fixed_end) or "04:00")
    grace = dt.timedelta(minutes=grace_minutes)
    now_vn = to_vn(now)
    today = backup_day(now, cutoff_hour)
    n = max(1, min(days, 62))
    nights = [today - dt.timedelta(days=i) for i in range(n - 1, -1, -1)]
    policy_names = sorted({p["name"] for p in policies if p.get("name")} | {j["policyName"] for j in jobs if j.get("policyName")})
    out_rules, findings = [], []
    covered: Dict[str, str] = {}
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        matched = [p for p in policy_names if policy_matches(rule, p)]
        for p in matched:
            covered.setdefault(p, rule["system"])
        rjobs: Dict[dt.date, List[Dict[str, Any]]] = defaultdict(list)
        mset = set(matched)
        for j in jobs:
            if j.get("policyName") in mset and j.get("startTime"):
                rjobs[backup_day(j["startTime"], cutoff_hour)].append(j)
        cells = []
        for d in nights:
            if expected_type(rule, d) is None:
                cells.append({"date": d.isoformat(), "expected": None, "status": None, "detail": "Không có lịch"})
                continue
            cells.append(_night_status(rule, d, rjobs.get(d, []), now_vn, grace) if matched
                         else {"date": d.isoformat(), "expected": expected_type(rule, d), "status": None, "detail": "Chưa gán policy"})
        counts = defaultdict(int)
        for c in cells:
            if c["status"]:
                counts[c["status"]] += 1
        evaluated = sum(counts[s] for s in FINAL)
        # Retention configured on NetBackup (Full schedules) vs the schedule sheet
        ret_issues = []
        if rule.get("retentionDays"):
            for p in policies:
                if p.get("name") in mset:
                    for s in p.get("schedules") or []:
                        if (s.get("type") or "").upper() == "FULL":
                            got = retention_days(s.get("retention"))
                            if got is not None and abs(got - rule["retentionDays"]) > 0.5:
                                ret_issues.append(f"{p['name']}/{s.get('name')}: {s.get('retention')}")
        last_full = None
        if rule.get("fullEveryDays"):
            fulls = [j["endTime"] for d_jobs in rjobs.values() for j in d_jobs
                     if (j["status"] or 0) <= 1 and j["endTime"] and ((j.get("scheduleType") or "").upper() == "FULL"
                                                                  or (j.get("scheduleName") or "") in (rule.get("fullScheduleNames") or []))]
            last_full = max(fulls) if fulls else None
        rpo = rule_rpo_hours(rule, grace_minutes)
        r_out = {**rule, "policies": matched, "rpoHours": round(rpo, 1) if rpo else None, "nights": cells,
                 "counts": dict(counts), "evaluated": evaluated,
                 "compliancePct": round(counts["ok"] * 100.0 / evaluated, 1) if evaluated else None,
                 "retentionIssues": ret_issues, "lastFull": to_vn(last_full).isoformat() if last_full else None}
        out_rules.append(r_out)
        sysname = rule["system"]
        if not matched:
            findings.append(("info", sysname, f"Lịch {sysname} chưa gán policy NetBackup",
                             "Chưa kiểm tra được tuân thủ lịch của hệ thống này.",
                             "Khai báo policy (hoặc mẫu tên, vd P-CORE-EQUITY*) thuộc hệ thống ở trang Lịch & tuân thủ."))
            continue
        if rule.get("fullEveryDays") and matched:
            limit = dt.timedelta(days=rule["fullEveryDays"], hours=12) + grace
            if last_full is None or now - last_full > limit:
                findings.append(("warning", sysname, f"{sysname}: không có bản Full trong {rule['fullEveryDays']} ngày",
                                 f"Full gần nhất: {to_vn(last_full):%H:%M %d/%m}" if last_full else "Không thấy bản Full nào trong dữ liệu đã đồng bộ.",
                                 "Kiểm tra schedule Full (tần suất / cửa sổ) và chạy Full bù."))
        bad = [c for c in cells if c["status"] in ("missing", "failed")]
        if bad:
            findings.append(("critical", sysname, f"{sysname}: {len(bad)} đêm backup {'thiếu / lỗi' if len(bad) > 1 else ('thiếu' if bad[0]['status'] == 'missing' else 'lỗi')}",
                             "; ".join(f"{_d(c['date'])} {STATUS[c['status']][1].lower()}" for c in bad[-6:]),
                             "Chạy bù (manual backup) cho các đêm thiếu / lỗi và xử lý nguyên nhân trước đêm kế tiếp."))
        wrong = [c for c in cells if c["status"] == "wrongtype"]
        if wrong:
            findings.append(("warning", sysname, f"{sysname}: đêm Full không chạy Full ({len(wrong)} lần)",
                             ", ".join(_d(c["date"]) for c in wrong[-6:]),
                             "Kiểm tra schedule Full của policy (ngày / cửa sổ) và chạy Full bù để chuỗi restore point không phụ thuộc bản Full cũ."))
        late = [c for c in cells if c["status"] == "late"]
        if late:
            findings.append(("warning", sysname, f"{sysname}: {len(late)} đêm kết thúc sau {rule['endTime']} (+{grace_minutes} phút)",
                             "; ".join(f"{_d(c['date'])} {c.get('detail', '')}" for c in late[-4:]),
                             "Xem lại thời lượng job, luồng song song và tải media server; dời giờ bắt đầu nếu cần."))
        if ret_issues:
            findings.append(("warning", sysname, f"{sysname}: retention bản Full khác lịch ({rule['retentionDays']} ngày)",
                             ", ".join(ret_issues[:6]), "Đối chiếu retention trong schedule Full của policy với lịch đã duyệt."))
    out_rules.sort(key=lambda r: (not r["policies"], r["system"].lower()))   # unmapped manual schedules last
    inactive = {p["name"] for p in policies if p.get("active") is False}
    unmapped = [p for p in policy_names if p not in covered and not p.startswith("SLP_") and p not in inactive]
    summary_eval = sum(r["evaluated"] for r in out_rules)
    summary_ok = sum(r["counts"].get("ok", 0) for r in out_rules)
    return {
        "configured": bool(rules), "nights": [d.isoformat() for d in nights], "rules": out_rules,
        "covered": covered, "unmappedPolicies": unmapped,
        "compliancePct": round(summary_ok * 100.0 / summary_eval, 1) if summary_eval else None,
        "evaluated": summary_eval, "graceMinutes": grace_minutes, "cutoffHour": cutoff_hour,
        "mode": mode if auto else "manual", "fixedWindow": f"{parse_hhmm(fixed_start) or '20:00'}–{parse_hhmm(fixed_end) or '04:00'}",
        "findings": findings,
    }


def _d(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{DAY_NAMES[d.weekday()]} {d:%d/%m}"
