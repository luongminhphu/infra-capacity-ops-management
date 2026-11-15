"""Backup report as a service: the one entry point every module uses (page, Trang chủ / hộp việc,
thẻ VM, AI phân tích lỗi, Hỏi ICO) instead of importing the router."""
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from .. import data_access
from ..models import AppSetting, BackupScheduleRule, BackupScopeRule
from . import analysis


def _keys(server: Optional[str]) -> Optional[List[str]]:
    return None if not server or server == "all" else [server]


def thresholds(db: Session) -> analysis.BackupThresholds:
    overrides = {r.key: r.value for r in db.query(AppSetting).filter(AppSetting.key.like("backup_%")).all()}
    return analysis.thresholds_from(overrides)


def _vms(db: Session):
    _, vms, _ = data_access.get_latest_combined(db, include_import=True)
    return vms


def scope_rules(db: Session) -> List[Dict[str, Any]]:
    return [{"vcenterKey": r.vcenter_key, "cluster": r.cluster, "host": r.host}
            for r in db.query(BackupScopeRule).order_by(BackupScopeRule.vcenter_key, BackupScopeRule.cluster, BackupScopeRule.host).all()]


def _csv_days(v: Optional[str]) -> List[int]:
    return [int(x) for x in (v or "").split(",") if x.strip().isdigit()]


def schedule_rules(db: Session) -> List[Dict[str, Any]]:
    return [{"id": r.id, "system": r.system, "policyPatterns": r.policy_patterns or "", "fullDays": _csv_days(r.full_days),
             "incrDays": _csv_days(r.incr_days), "startTime": r.start_time, "endTime": r.end_time,
             "retentionDays": r.retention_days, "enabled": bool(r.enabled), "note": r.note}
            for r in db.query(BackupScheduleRule).order_by(BackupScheduleRule.system).all()]


def build_summary(db: Session, server: Optional[str], days: int, check_feature: bool = False) -> Dict[str, Any]:
    """Full backup report for a server ("all" = every server) over `days`. check_feature: answer 404
    when the Backup module is switched off (callers outside the gated router)."""
    if check_feature:
        from ..features import ensure_feature
        ensure_feature(db, "backup")
    return analysis.build_report(db, keys=_keys(server), days=days, th=thresholds(db), vms=_vms(db), scope=scope_rules(db),
                                 schedules=schedule_rules(db))


