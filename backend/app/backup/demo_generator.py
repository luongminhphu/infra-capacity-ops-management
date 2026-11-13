"""Synthetic NetBackup REST payloads for NETBACKUP_i_DEMO=true.

The generator returns the same JSON shapes as the real API (``data[].attributes``),
so the demo exercises the very parsers used against a live primary server.
Everything is deterministic per (server key, calendar day): re-syncing returns
the same job ids with their latest state, exactly like polling the activity
monitor, and finished jobs never change.

Scenarios built in (so every report section has something to show):
* VMware policies protecting most — not all — VMs found in the vCenter inventory;
* a file server failing three nights in a row (status 58, client unreachable);
* an Oracle client that keeps finishing with status 1 (partial);
* a SQL client skipped because the backup window closed (status 196);
* a client listed in a policy that never resolves (status 48);
* an inactive legacy policy, an MSDP pool above 85 % and duplication jobs to DR.
"""
import datetime as dt
import hashlib
import random
from typing import Any, Dict, List, Optional, Tuple

from ..config import NetBackupConfig

DAY = dt.timedelta(days=1)


def _rnd(*parts: Any) -> random.Random:
    seed = int(hashlib.sha256("/".join(str(p) for p in parts).encode()).hexdigest(), 16) % (2 ** 32)
    return random.Random(seed)


def _iso(t: Optional[dt.datetime]) -> Optional[str]:
    return t.strftime("%Y-%m-%dT%H:%M:%S.000Z") if t else None


def _policies(cfg: NetBackupConfig, vm_names: List[str]) -> List[Dict[str, Any]]:
    """Internal policy model: name, type, active, clients, start hour (UTC), full weekday, size GB."""
    vms = sorted({v for v in vm_names if not v.lower().startswith("vcls")})
    if not vms:
        vms = [f"app-vm-{i:03d}" for i in range(1, 61)]
    r = _rnd(cfg.key, "coverage")
    protected = [v for v in vms if r.random() < 0.86]  # ~14 % of VMs left out of every policy
    half = len(protected) // 2
    master = cfg.host.split(".")[0]
    return [
        {"name": "VM-PROD-DAILY", "type": "VMware", "active": True, "clients": protected[:half], "hour": 13,
         "fullDay": 5, "gb": (20, 180), "query": "vmware:/?filter=Displayname Contains 'prod'", "stu": "stu-msdp-01"},
        {"name": "VM-NONPROD-DAILY", "type": "VMware", "active": True, "clients": protected[half:], "hour": 15,
         "fullDay": 6, "gb": (10, 120), "query": "vmware:/?filter=Displayname NotContains 'prod'", "stu": "stu-msdp-01"},
        {"name": "WIN-FS-DAILY", "type": "MS-Windows", "active": True, "clients": ["fs01", "fs02", "fs03", "fs04"], "hour": 14,
         "fullDay": 5, "gb": (300, 900), "stu": "stu-msdp-01"},
        {"name": "LNX-APP-DAILY", "type": "Standard", "active": True,
         "clients": ["app01.corp.local", "app02.corp.local", "app03.corp.local", "app04.corp.local", "app05.corp.local", "app06-old.corp.local"],
         "hour": 16, "fullDay": 6, "gb": (15, 60), "stu": "stu-msdp-01"},
        {"name": "ORA-DB-DAILY", "type": "Oracle", "active": True, "clients": ["oradb01", "oradb02"], "hour": 19,
         "fullDay": 6, "gb": (400, 1200), "stu": "stu-msdp-01"},
        {"name": "SQL-DB-DAILY", "type": "MS-SQL-Server", "active": True, "clients": ["sqldb01", "sqldb02", "sqldb03"], "hour": 18,
         "fullDay": 6, "gb": (80, 400), "stu": "stu-msdp-01"},
        {"name": "NBU-CATALOG", "type": "NBU-Catalog", "active": True, "clients": [master], "hour": 21, "fullDay": 0,
         "gb": (40, 60), "stu": "stu-advdisk-01"},
        {"name": "LEGACY-ERP", "type": "Standard", "active": False, "clients": ["erp-old01"], "hour": 12, "fullDay": 6,
         "gb": (50, 100), "stu": "stu-msdp-01"},
    ]


def _fail_status(client: str, day_idx: int, r: random.Random, days_ago: int) -> int:
    """Scenario rules; day_idx counts days since epoch, days_ago 0 = today."""
    if client == "fs03" and days_ago <= 2:
        return 58
    if client == "app06-old.corp.local":
        return 48
    if client == "sqldb03" and days_ago <= 1:
        return 196
    if client == "oradb02" and r.random() < 0.7:
        return 1
    if client.endswith("-vm-007") and days_ago <= 3:
        return 156
    x = r.random()
    if x < 0.025:
        return r.choice([13, 24, 41, 84, 129, 4200])
    if x < 0.04:
        return 1
    return 0


def _job(job_id: int, parent: Optional[int], jtype: str, pol: Dict[str, Any], sched: str, stype: str, client: str,
         start: dt.datetime, dur: float, kb: float, status: int, now: dt.datetime, stu: str) -> Optional[Dict[str, Any]]:
    if start > now:
        return None
    end = start + dt.timedelta(seconds=dur)
    running = end > now
    attrs = {
        "jobId": job_id, "parentJobId": parent or job_id, "jobType": jtype, "policyName": pol["name"], "policyType": pol["type"],
        "scheduleName": sched, "scheduleType": stype, "clientName": client,
        "state": "ACTIVE" if running else "DONE", "status": 0 if running else status,
        "startTime": _iso(start), "endTime": None if running else _iso(end),
        "elapsedTime": int(((now if running else end) - start).total_seconds()),
        "kilobytesTransferred": int(kb * (min(1.0, (now - start).total_seconds() / dur) if running else (1 if status in (0, 1) else 0.1))),
        "numberOfFiles": int(kb / 180) if status in (0, 1) else 0,
        "destinationStorageUnitName": stu, "destinationMediaServerName": "media01",
    }
    return {"type": "job", "id": str(job_id), "attributes": attrs}


def _jobs_for_day(cfg: NetBackupConfig, pols: List[Dict[str, Any]], day: dt.date, now: dt.datetime) -> List[Dict[str, Any]]:
    day_idx = (day - dt.date(2020, 1, 1)).days
    days_ago = (now.date() - day).days
    base = 1_000_000 + day_idx * 2000
    seq = 0
    out: List[Dict[str, Any]] = []
    for pol in pols:
        if not pol["active"]:
            continue
        full = day.weekday() == pol["fullDay"]
        sched, stype = ("Full-Weekly", "FULL") if full else ("Diff-Daily", "DIFFERENTIAL_INCREMENTAL")
        if pol["type"] == "NBU-Catalog":
            sched, stype = "Catalog-Daily", "FULL"
        t0 = dt.datetime.combine(day, dt.time(pol["hour"], 0))
        is_vm = pol["type"] == "VMware"
        parent_id = None
        if is_vm:  # discovery parent job (one per policy run)
            seq += 1
            parent_id = base + seq
        child_ids: List[Tuple[int, int]] = []
        for ci, client in enumerate(pol["clients"]):
            r = _rnd(cfg.key, day.isoformat(), pol["name"], client)
            status = _fail_status(client, day_idx, r, days_ago)
            gb = r.uniform(*pol["gb"]) * (1.0 if full or stype == "FULL" else r.uniform(0.04, 0.12))
            start = t0 + dt.timedelta(minutes=(ci // 6) * 25 + r.randint(0, 20))
            mbps = r.uniform(150, 400) if is_vm else r.uniform(40, 160)
            dur = max(120.0, gb * 1024 / mbps) * (0.3 if status not in (0, 1) else 1)
            if status == 196:
                dur, gb = 5.0, 0.0
            seq += 1
            jid = base + seq
            if is_vm:  # snapshot job → backup child
                snap = _job(jid, parent_id, "SNAPSHOT", pol, sched, stype, client, start, 90, 0, 0 if status != 156 else 156, now, pol["stu"])
                if snap:
                    out.append(snap)
                    child_ids.append((jid, 0 if status != 156 else 156))
                if status == 156:
                    continue
                seq += 1
                bj = _job(base + seq, jid, "BACKUP", pol, sched, stype, client, start + dt.timedelta(seconds=95), dur, gb * 1024 * 1024, status, now, pol["stu"])
            else:
                bj = _job(jid, None, "BACKUP", pol, sched, stype, client, start, dur, gb * 1024 * 1024, status, now, pol["stu"])
            if bj:
                out.append(bj)
                child_ids.append((int(bj["id"]), bj["attributes"]["status"]))
            # SLP duplication of database / VM images to the DR pool
            if status in (0, 1) and pol["type"] in ("VMware", "Oracle", "MS-SQL-Server") and bj:
                seq += 1
                dup_status = 0 if r.random() > 0.03 else 84
                dj = _job(base + seq, None, "DUPLICATION", {**pol, "name": "SLP_" + pol["name"]}, "", "", client,
                          start + dt.timedelta(seconds=dur + 1800), dur * 0.8, gb * 1024 * 1024, dup_status, now, "stu-msdp-dr")
                if dj:
                    out.append(dj)
        if is_vm and parent_id and child_ids:
            worst = max((s for _, s in child_ids), default=0)
            pj = _job(parent_id, None, "BACKUP", pol, sched, stype, cfg.host.split(".")[0], t0, 120, 0, worst, now, pol["stu"])
            if pj:
                out.append(pj)
    return out


def jobs_payload(cfg: NetBackupConfig, since: dt.datetime, vm_names: List[str], now: Optional[dt.datetime] = None) -> List[Dict[str, Any]]:
    now = now or dt.datetime.utcnow()
    pols = _policies(cfg, vm_names)
    out: List[Dict[str, Any]] = []
    day = (since - DAY).date()
    while day <= now.date():
        out.extend(j for j in _jobs_for_day(cfg, pols, day, now) if j["attributes"]["startTime"] >= _iso(since))
        day += DAY
    return out


def policies_payload(cfg: NetBackupConfig, vm_names: List[str]) -> List[Dict[str, Any]]:
    out = []
    for p in _policies(cfg, vm_names):
        is_vm = p["type"] == "VMware"
        out.append({"type": "policy", "id": p["name"], "attributes": {"policy": {
            "policyName": p["name"], "policyType": p["type"],
            "policyAttributes": {"active": p["active"], "storage": p["stu"]},
            # VMware Intelligent Policy: clients are discovered by query, not listed.
            "clients": [] if is_vm else [{"hostName": c} for c in p["clients"]],
            "backupSelections": {"selections": [p["query"]] if is_vm else ["ALL_LOCAL_DRIVES" if p["type"] == "MS-Windows" else "/"]},
            "schedules": _demo_schedules(p),
        }}})
    return out


def _nb_dow(weekday: int) -> int:
    """Python weekday (Mon = 0) → NetBackup dayOfWeek (1 = Sunday … 7 = Saturday)."""
    return (weekday + 1) % 7 + 1


def _demo_schedules(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Schedules as the NetBackup REST API returns them: Full = Calendar on the full day, Incremental =
    Frequency 1 day on the other days; 8 h windows in the primary server's local time (VN = UTC+7)."""
    vn_start = (p["hour"] + 7) % 24
    shift = 1 if p["hour"] + 7 >= 24 else 0              # a UTC evening start can be the next VN calendar day
    full_wd = (p["fullDay"] + shift) % 7
    def windows(days):
        return [{"dayOfWeek": _nb_dow(d), "startSeconds": vn_start * 3600, "durationSeconds": 8 * 3600 if d in days else 0}
                for d in range(7)]
    if p["type"] == "NBU-Catalog":
        return [{"scheduleName": "Catalog-Daily", "backupType": "Full Backup", "scheduleType": "Frequency",
                 "frequencySeconds": 86400, "startWindow": windows(range(7)),
                 "backupCopies": {"copies": [{"retentionPeriod": {"value": 2, "unit": "WEEKS"}}]}}]
    return [
        {"scheduleName": "Full-Weekly", "backupType": "Full Backup", "scheduleType": "Calendar",
         "includeDates": {"recurringDaysOfWeek": [f"{w}:{_nb_dow(full_wd)}" for w in range(1, 6)]},
         "startWindow": windows([full_wd]), "backupCopies": {"copies": [{"retentionPeriod": {"value": 5, "unit": "WEEKS"}}]}},
        {"scheduleName": "Diff-Daily", "backupType": "Differential Incremental Backup", "scheduleType": "Frequency",
         "frequencySeconds": 86400, "startWindow": windows([d for d in range(7) if d != full_wd]),
         "backupCopies": {"copies": [{"retentionPeriod": {"value": 14, "unit": "DAYS"}}]}},
    ]


POOLS = [
    # name, type, total TiB, used fraction at day 0, daily growth fraction
    ("msdp-pool-01", "PureDisk", 240, 0.80, 0.0019),
    ("msdp-dr-pool", "PureDisk", 160, 0.70, 0.0012),
    ("advdisk-staging", "AdvancedDisk", 20, 0.42, 0.0004),
]


def pools_payload(cfg: NetBackupConfig, at: Optional[dt.datetime] = None) -> List[Dict[str, Any]]:
    at = at or dt.datetime.utcnow()
    day = (at - dt.datetime(2020, 1, 1)).total_seconds() / 86400.0
    out = []
    for name, stype, tib, used0, growth in POOLS:
        r = _rnd(cfg.key, name)
        total = tib * 1024 ** 4
        # Slow saw-tooth: expiring images reclaim space every ~60 days.
        frac = min(0.985, used0 + growth * ((day + r.randint(0, 30)) % 60) + r.uniform(-0.002, 0.002))
        out.append({"type": "diskPool", "id": name, "attributes": {
            "name": name, "sType": stype, "usableSizeBytes": total, "availableSpaceBytes": total * (1 - frac),
            "highWaterMark": 98, "lowWaterMark": 80, "diskVolumes": [{"name": f"{name}-vol1", "state": "UP"}]}})
    return out
