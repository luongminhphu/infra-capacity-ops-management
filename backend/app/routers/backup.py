"""Backup monitoring API (Veritas NetBackup, read-only)."""
import logging
import datetime as dt
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import data_access
from ..backup import analysis
from ..backup.client import NetBackupError
from ..backup.collector import sync_netbackup
from ..collectors.locks import SourceBusy
from ..collectors.scheduler import sync_backups
from ..config import get_settings
from ..database import get_db
from ..backup import schedule as bschedule
from ..pagination import set_page_headers
from ..models import AppSetting, BackupPolicySnapshot, BackupScheduleRule, BackupScopeRule, BackupSyncRun, ReportLogo, User
from ..scope import OwnerScope, owner_scope_query
from ..security import get_current_user, require_admin
from ..timeutil import to_iso_utc

from ..backup.service import _csv_days, _keys, _vms, build_summary, schedule_rules, scope_rules, thresholds  # noqa: F401,E402
from ..features import require_feature
logger = logging.getLogger("vco.routers.backup")
router = APIRouter(prefix="/api/backup", tags=["backup"], dependencies=[Depends(require_feature("backup"))])


def _jsonable(v: Any) -> Any:
    if isinstance(v, dt.datetime):
        return to_iso_utc(v)
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def _cfg(key: str):
    cfg = next((c for c in get_settings().netbackups() if c.key == key), None)
    if not cfg:
        raise HTTPException(404, "Không tìm thấy máy chủ backup với key này")
    return cfg


def _server_public(cfg, db: Session) -> Dict[str, Any]:
    last_ok = (db.query(BackupSyncRun).filter(BackupSyncRun.nbu_key == cfg.key, BackupSyncRun.status == "ok",
                                              BackupSyncRun.jobs_fetched.isnot(None))
               .order_by(BackupSyncRun.started_at.desc()).first())
    last_err = (db.query(BackupSyncRun).filter(BackupSyncRun.nbu_key == cfg.key, BackupSyncRun.status == "error")
                .order_by(BackupSyncRun.started_at.desc()).first())
    err = last_err.error_message if last_err and (not last_ok or last_err.started_at > last_ok.started_at) else None
    return {
        "key": cfg.key, "name": cfg.name, "host": cfg.host, "port": cfg.port, "demo": cfg.demo,
        "auth": "apikey" if cfg.api_key else ("login" if cfg.username else "none"), "apiVersion": cfg.api_version,
        "lastSyncAt": to_iso_utc(last_ok.finished_at) if last_ok else None,
        "lastStatus": "error" if err else ("ok" if last_ok else None),
        "lastError": err, "warning": last_ok.error_message if last_ok else None,
    }


@router.get("/servers")
def list_servers(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [_server_public(c, db) for c in get_settings().netbackups()]


@router.post("/servers/{key}/sync")
def sync_one(key: str, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    cfg = _cfg(key)
    try:
        run = sync_netbackup(db, cfg)
    except SourceBusy as exc:            # the scheduler (or another click) is syncing it right now
        raise HTTPException(409, str(exc)) from exc
    except NetBackupError as exc:
        raise HTTPException(502, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — details go to the log, not to the client
        logger.exception("NetBackup sync failed (server=%s)", key)
        raise HTTPException(502, "Đồng bộ backup thất bại. Xem log máy chủ để biết chi tiết.") from exc
    return {"ok": True, "key": key, "jobs": run.jobs_fetched, "policies": run.policy_count, "pools": run.pool_count,
            "warning": run.error_message, "finishedAt": to_iso_utc(run.finished_at)}


@router.post("/sync-all")
def sync_all(user: User = Depends(require_admin)):
    # Every backup server in parallel, each in its own DB session.
    results = sync_backups(get_settings())
    return {"results": [{**r, "finishedAt": to_iso_utc(r["finishedAt"])} if r.get("ok") else r for r in results]}


@router.get("/summary")
def summary(server: Optional[str] = Query(default="all"), days: int = Query(default=7, ge=1, le=90),
            osc: OwnerScope = Depends(owner_scope_query), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    res = build_summary(db, server, days, osc=osc)
    res["failedJobs"] = res["failedJobs"][:200]
    return _jsonable(res)


@router.get("/jobs")
def jobs(response: Response, server: Optional[str] = Query(default="all"), days: int = Query(default=7, ge=1, le=90),
         outcome: Optional[str] = Query(default=None, pattern="^(success|partial|failed|running)$"),
         category: Optional[str] = Query(default="backup", pattern="^(backup|copy|restore|other|all)$"),
         q: Optional[str] = Query(default=None), offset: int = Query(default=0, ge=0), limit: int = Query(default=500, ge=1, le=5000),
         db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Newest first (start time, then id). `total` (body) and X-Total-Count count every matching job;
    offset / limit select the page (audit API-05)."""
    since = dt.datetime.utcnow() - dt.timedelta(days=days)
    needle = (q or "").strip().lower()
    rows, total = [], 0
    for j in analysis.iter_jobs(db, _keys(server), since):
        if category != "all" and j["category"] != category:
            continue
        if outcome and j["outcome"] != outcome:
            continue
        if needle and not any(needle in str(j.get(f) or "").lower() for f in ("clientName", "policyName", "jobId", "statusText")):
            continue
        if offset <= total and len(rows) < limit:
            rows.append(j)
        total += 1
    set_page_headers(response, total, offset, len(rows))
    return _jsonable({"total": total, "rows": rows})


# ---------------------------------------------------------------------------
# Backup scope: which vCenter / cluster / host must be backed up (e.g. production only)
# ---------------------------------------------------------------------------
class ScopeRuleIn(BaseModel):
    vcenterKey: str
    cluster: Optional[str] = None
    host: Optional[str] = None


class ScopeIn(BaseModel):
    rules: List[ScopeRuleIn] = []


def _scope_tree(db: Session) -> List[Dict[str, Any]]:
    """vCenter → cluster → host from the latest inventory, with powered-on VM counts."""
    hosts, vms, _ = data_access.get_latest_combined(db, include_import=True)
    names = {c.key: c.name for c in get_settings().vcenters()}
    tree: Dict[str, Dict[str, Any]] = {}

    def node(vc, cl, h):
        v = tree.setdefault(vc, {"vcenterKey": vc, "name": names.get(vc, "Dữ liệu import" if vc == "import" else vc), "clusters": {}})
        c = v["clusters"].setdefault(cl, {"name": cl, "hosts": {}})
        return c["hosts"].setdefault(h, {"name": h, "vms": 0, "vmsOn": 0}) if h else None

    for h in hosts:
        node(h.get("vcenterKey") or "import", h.get("cluster") or "UNKNOWN", h.get("host"))
    for v in vms:
        hn = node(v.get("vcenterKey") or "import", v.get("cluster") or "UNKNOWN", v.get("host") or "(không rõ host)")
        hn["vms"] += 1
        hn["vmsOn"] += 1 if v.get("isPoweredOn") else 0
    out = []
    for v in sorted(tree.values(), key=lambda x: x["vcenterKey"]):
        clusters = []
        for c in sorted(v["clusters"].values(), key=lambda x: x["name"]):
            hs = sorted(c["hosts"].values(), key=lambda x: x["name"])
            clusters.append({"name": c["name"], "hosts": hs, "vms": sum(x["vms"] for x in hs), "vmsOn": sum(x["vmsOn"] for x in hs)})
        out.append({"vcenterKey": v["vcenterKey"], "name": v["name"], "clusters": clusters,
                    "vms": sum(c["vms"] for c in clusters), "vmsOn": sum(c["vmsOn"] for c in clusters)})
    return out


@router.get("/scope")
def get_scope(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rules = scope_rules(db)
    return {"rules": rules, "label": analysis.scope_label(rules), "tree": _scope_tree(db)}


@router.put("/scope")
def put_scope(payload: ScopeIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    clean, seen = [], set()
    for r in payload.rules:
        vc = (r.vcenterKey or "").strip()
        if not vc:
            raise HTTPException(400, "Thiếu vCenter trong phạm vi backup")
        cl = (r.cluster or "").strip() or None
        h = (r.host or "").strip() or None
        if h and not cl:
            raise HTTPException(400, "Chọn host phải kèm cluster")
        key = (vc, cl, h)
        if key not in seen:
            seen.add(key)
            clean.append(key)
    db.query(BackupScopeRule).delete()
    for vc, cl, h in clean:
        db.add(BackupScopeRule(vcenter_key=vc[:64], cluster=cl, host=h, updated_by=getattr(user, "username", None)))
    db.commit()
    rules = scope_rules(db)
    return {"rules": rules, "label": analysis.scope_label(rules), "tree": _scope_tree(db)}


# ---------------------------------------------------------------------------
# Backup schedule per system (SLA) — drives the nightly compliance check
# ---------------------------------------------------------------------------
class ScheduleRuleIn(BaseModel):
    system: str
    policyPatterns: Optional[str] = ""
    fullDays: Any = []
    incrDays: Any = []
    startTime: str
    endTime: str
    retentionDays: Optional[Any] = None
    enabled: bool = True
    note: Optional[str] = None


class SchedulesIn(BaseModel):
    rules: List[ScheduleRuleIn] = []


def _known_policies(db: Session) -> List[str]:
    """Policy names from the latest snapshot of every NetBackup server, plus names seen in jobs."""
    from ..models import BackupJob
    names = set()
    for (k,) in db.query(BackupSyncRun.nbu_key).distinct().all():
        run = (db.query(BackupSyncRun).filter(BackupSyncRun.nbu_key == k, BackupSyncRun.status == "ok", BackupSyncRun.policy_count > 0)
               .order_by(BackupSyncRun.started_at.desc()).first())
        if run:
            names |= {p.name for p in db.query(BackupPolicySnapshot.name).filter(BackupPolicySnapshot.run_id == run.id).all()}
    since = dt.datetime.utcnow() - dt.timedelta(days=35)
    names |= {r[0] for r in db.query(BackupJob.policy_name).filter(BackupJob.start_time >= since, BackupJob.category == "backup")
              .distinct().all() if r[0]}
    return sorted(n for n in names if not n.startswith("SLP_"))


def _schedules_out(db: Session) -> Dict[str, Any]:
    rules = schedule_rules(db)
    policies = _known_policies(db)
    for r in rules:
        r["policies"] = [p for p in policies if bschedule.policy_matches(r, p)]
        r["rpoHours"] = bschedule.rule_rpo_hours(r, thresholds(db).schedule_grace_minutes)
    mapped = {p for r in rules for p in r["policies"]}
    return {"rules": rules, "policies": policies, "unmappedPolicies": [p for p in policies if p not in mapped],
            "dayNames": bschedule.DAY_NAMES}


def _save_rules(db: Session, rules: List[Dict[str, Any]], user: User) -> None:
    seen = set()
    for r in rules:
        key = r["system"].lower()
        if key in seen:
            raise HTTPException(400, f"Trùng tên hệ thống: {r['system']}")
        seen.add(key)
    db.query(BackupScheduleRule).delete()
    for r in rules:
        db.add(BackupScheduleRule(system=r["system"], policy_patterns=r["policyPatterns"], full_days=",".join(map(str, r["fullDays"])),
                                  incr_days=",".join(map(str, r["incrDays"])), start_time=r["startTime"], end_time=r["endTime"],
                                  retention_days=r["retentionDays"], enabled=r["enabled"], note=r["note"],
                                  updated_by=getattr(user, "username", None)))
    db.commit()


@router.get("/schedules")
def get_schedules(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _schedules_out(db)


@router.put("/schedules")
def put_schedules(payload: SchedulesIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    try:
        rules = [bschedule.validate_rule(r.model_dump()) for r in payload.rules]
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _save_rules(db, rules, user)
    return _schedules_out(db)


@router.post("/schedules/import")
def import_schedules(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Load the schedule workbook (HSC-Backup-Management template). Systems already defined keep
    their policy mapping; systems not in the file are kept unchanged.

    A plain `def` (FastAPI runs it in the threadpool): openpyxl parsing and the DB writes are
    synchronous and used to block the event loop, /api/health included."""
    data = file.file.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(400, "File quá lớn (tối đa 5 MB)")
    try:
        parsed = bschedule.parse_schedule_xlsx(data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — not an xlsx / corrupt workbook (no internal detail in the answer)
        raise HTTPException(400, f"Không đọc được file Excel ({type(exc).__name__}).") from exc
    current = {r["system"].lower(): r for r in schedule_rules(db)}
    for r in parsed:
        old = current.pop(r["system"].lower(), None)
        if old:
            r["policyPatterns"] = old["policyPatterns"]
            r["enabled"] = old["enabled"]
            r["note"] = old["note"]
    merged = parsed + [bschedule.validate_rule(r) for r in current.values()]
    _save_rules(db, merged, user)
    out = _schedules_out(db)
    out["imported"] = [r["system"] for r in parsed]
    return out


class ThresholdsIn(BaseModel):
    success_target_pct: Optional[float] = None
    success_crit_pct: Optional[float] = None
    rpo_hours: Optional[float] = None
    rpo_crit_hours: Optional[float] = None
    consecutive_fail: Optional[int] = None
    long_running_hours: Optional[float] = None
    queued_hours: Optional[float] = None
    storage_warn_pct: Optional[float] = None
    storage_crit_pct: Optional[float] = None
    storage_days_warn: Optional[int] = None
    window_end_hour: Optional[int] = None
    coverage_days: Optional[int] = None
    coverage_exclude: Optional[str] = None
    schedule_grace_minutes: Optional[int] = None
    backup_day_cutoff_hour: Optional[int] = None
    schedule_mode: Optional[str] = None
    fixed_window_start: Optional[str] = None
    fixed_window_end: Optional[str] = None


def _thresholds_out(th: analysis.BackupThresholds) -> Dict[str, Any]:
    vals = vars(th)
    return {"values": vals, "defaults": vars(analysis.BackupThresholds()),
            "meta": [{"key": k, "label": lbl, "unit": u, "hint": h} for k, lbl, u, h in analysis.THRESHOLD_META]}


@router.get("/thresholds")
def get_thresholds(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _thresholds_out(thresholds(db))


@router.put("/thresholds")
def put_thresholds(payload: ThresholdsIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    for k, v in payload.model_dump(exclude_none=True).items():
        if isinstance(v, (int, float)) and v < 0:
            raise HTTPException(400, f"Giá trị {k} không hợp lệ")
        if k == "schedule_mode" and str(v).strip().lower() not in ("netbackup", "fixed"):
            raise HTTPException(400, "schedule_mode chỉ nhận 'netbackup' hoặc 'fixed'")
        if k in ("fixed_window_start", "fixed_window_end"):
            v = bschedule.parse_hhmm(v)
            if not v:
                raise HTTPException(400, f"{k} phải có dạng HH:MM")
        key = "backup_" + k
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        if row:
            row.value = str(v)[:255]
        else:
            db.add(AppSetting(key=key, value=str(v)[:255]))
    db.commit()
    return _thresholds_out(thresholds(db))


def _logo(db: Session) -> Optional[str]:
    row = db.query(ReportLogo).filter(ReportLogo.id == 1).first()
    return f"data:{row.mime_type};base64,{row.data_base64}" if row else None


def _scope_label(server: Optional[str]) -> str:
    if not server or server == "all":
        return "Toàn bộ máy chủ backup"
    cfg = next((c for c in get_settings().netbackups() if c.key == server), None)
    return cfg.name if cfg else server


def _fname(prefix: str, days: int, ext: str) -> str:
    return f"{prefix}_{days}ngay_{dt.datetime.now().strftime('%Y%m%d_%H%M')}.{ext}"


@router.get("/report/pdf")
def report_pdf(server: Optional[str] = Query(default="all"), days: int = Query(default=7, ge=1, le=90),
               appendix: bool = Query(default=True), ai: bool = Query(default=False),
               osc: OwnerScope = Depends(owner_scope_query), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from .. import report_backup
    res = build_summary(db, server, days, osc=osc)
    if ai:
        from ..ai_tasks import backup_facts
        from .ai_admin import pdf_ai_box
        pdf_ai_box(db, user, "backup", lambda: backup_facts(res))
    servers = [_server_public(c, db) for c in get_settings().netbackups() if not _keys(server) or c.key in _keys(server)]
    label = " · ".join(x for x in (_scope_label(server), osc.describe(db)) if x)
    pdf = report_backup.build_backup_pdf(res, servers, label, logo_data_url=_logo(db), appendix=appendix)
    return StreamingResponse(iter([pdf]), media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{_fname("ICO_Bao_cao_backup", days, "pdf")}"'})


@router.get("/report/xlsx")
def report_xlsx(server: Optional[str] = Query(default="all"), days: int = Query(default=7, ge=1, le=90),
                osc: OwnerScope = Depends(owner_scope_query), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from .. import excel_backup
    res = build_summary(db, server, days, osc=osc)
    since = dt.datetime.utcnow() - dt.timedelta(days=days)
    jobs_rows = list(analysis.iter_jobs(db, _keys(server), since))
    servers = [_server_public(c, db) for c in get_settings().netbackups() if not _keys(server) or c.key in _keys(server)]
    data = excel_backup.build_backup_xlsx(res, jobs_rows, servers, " · ".join(x for x in (_scope_label(server), osc.describe(db)) if x))
    return StreamingResponse(iter([data]), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="{_fname("ICO_Thong_ke_backup", days, "xlsx")}"'})

