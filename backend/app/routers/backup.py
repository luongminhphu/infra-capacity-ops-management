"""Backup monitoring API (Veritas NetBackup, read-only)."""
import datetime as dt
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import data_access
from ..backup import analysis
from ..backup.client import NetBackupError
from ..backup.collector import sync_netbackup
from ..config import get_settings
from ..database import get_db
from ..models import AppSetting, BackupScopeRule, BackupSyncRun, ReportLogo, User
from ..security import get_current_user
from ..timeutil import to_iso_utc

router = APIRouter(prefix="/api/backup", tags=["backup"])


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
        raise HTTPException(404, "Không tìm thấy NetBackup server với key này")
    return cfg


def _keys(server: Optional[str]) -> Optional[List[str]]:
    return None if not server or server == "all" else [server]


def thresholds(db: Session) -> analysis.BackupThresholds:
    overrides = {r.key: r.value for r in db.query(AppSetting).filter(AppSetting.key.like("backup_%")).all()}
    return analysis.thresholds_from(overrides)


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


def _vms(db: Session):
    _, vms, _ = data_access.get_latest_combined(db, include_import=True)
    return vms


def scope_rules(db: Session) -> List[Dict[str, Any]]:
    return [{"vcenterKey": r.vcenter_key, "cluster": r.cluster, "host": r.host}
            for r in db.query(BackupScopeRule).order_by(BackupScopeRule.vcenter_key, BackupScopeRule.cluster, BackupScopeRule.host).all()]


def build_summary(db: Session, server: Optional[str], days: int) -> Dict[str, Any]:
    return analysis.build_report(db, keys=_keys(server), days=days, th=thresholds(db), vms=_vms(db), scope=scope_rules(db))


@router.get("/servers")
def list_servers(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [_server_public(c, db) for c in get_settings().netbackups()]


@router.post("/servers/{key}/sync")
def sync_one(key: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    cfg = _cfg(key)
    try:
        run = sync_netbackup(db, cfg)
    except NetBackupError as exc:
        raise HTTPException(502, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Đồng bộ NetBackup thất bại: {exc}") from exc
    return {"ok": True, "key": key, "jobs": run.jobs_fetched, "policies": run.policy_count, "pools": run.pool_count,
            "warning": run.error_message, "finishedAt": to_iso_utc(run.finished_at)}


@router.post("/sync-all")
def sync_all(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    out = []
    for cfg in get_settings().netbackups():
        try:
            run = sync_netbackup(db, cfg)
            out.append({"key": cfg.key, "ok": True, "jobs": run.jobs_fetched, "warning": run.error_message})
        except Exception as exc:  # noqa: BLE001
            out.append({"key": cfg.key, "ok": False, "error": str(exc)})
    return {"results": out}


@router.get("/summary")
def summary(server: Optional[str] = Query(default="all"), days: int = Query(default=7, ge=1, le=90),
            db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    res = build_summary(db, server, days)
    res["failedJobs"] = res["failedJobs"][:200]
    return _jsonable(res)


@router.get("/jobs")
def jobs(server: Optional[str] = Query(default="all"), days: int = Query(default=7, ge=1, le=90),
         outcome: Optional[str] = Query(default=None, pattern="^(success|partial|failed|running)$"),
         category: Optional[str] = Query(default="backup", pattern="^(backup|copy|restore|other|all)$"),
         q: Optional[str] = Query(default=None), limit: int = Query(default=500, ge=1, le=5000),
         db: Session = Depends(get_db), user: User = Depends(get_current_user)):
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
        total += 1
        if len(rows) < limit:
            rows.append(j)
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
def put_scope(payload: ScopeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
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


def _thresholds_out(th: analysis.BackupThresholds) -> Dict[str, Any]:
    vals = vars(th)
    return {"values": vals, "defaults": vars(analysis.BackupThresholds()),
            "meta": [{"key": k, "label": lbl, "unit": u} for k, lbl, u in analysis.THRESHOLD_META]}


@router.get("/thresholds")
def get_thresholds(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _thresholds_out(thresholds(db))


@router.put("/thresholds")
def put_thresholds(payload: ThresholdsIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    for k, v in payload.model_dump(exclude_none=True).items():
        if isinstance(v, (int, float)) and v < 0:
            raise HTTPException(400, f"Giá trị {k} không hợp lệ")
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
        return "Toàn bộ NetBackup"
    cfg = next((c for c in get_settings().netbackups() if c.key == server), None)
    return cfg.name if cfg else server


def _fname(prefix: str, days: int, ext: str) -> str:
    return f"{prefix}_{days}ngay_{dt.datetime.now().strftime('%Y%m%d_%H%M')}.{ext}"


@router.get("/report/pdf")
def report_pdf(server: Optional[str] = Query(default="all"), days: int = Query(default=7, ge=1, le=90),
               appendix: bool = Query(default=True), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from .. import report_backup
    res = build_summary(db, server, days)
    servers = [_server_public(c, db) for c in get_settings().netbackups() if not _keys(server) or c.key in _keys(server)]
    pdf = report_backup.build_backup_pdf(res, servers, _scope_label(server), logo_data_url=_logo(db), appendix=appendix)
    return StreamingResponse(iter([pdf]), media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{_fname("Bao_cao_backup", days, "pdf")}"'})


@router.get("/report/xlsx")
def report_xlsx(server: Optional[str] = Query(default="all"), days: int = Query(default=7, ge=1, le=90),
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from .. import excel_backup
    res = build_summary(db, server, days)
    since = dt.datetime.utcnow() - dt.timedelta(days=days)
    jobs_rows = list(analysis.iter_jobs(db, _keys(server), since))
    servers = [_server_public(c, db) for c in get_settings().netbackups() if not _keys(server) or c.key in _keys(server)]
    data = excel_backup.build_backup_xlsx(res, jobs_rows, servers, _scope_label(server))
    return StreamingResponse(iter([data]), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="{_fname("Thong_ke_backup", days, "xlsx")}"'})

