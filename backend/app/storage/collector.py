"""Sync one storage source: read the array (demo / Pure / ONTAP), replace its volumes and alerts, update the
array row and record capacity / performance history (metric_points kind "array"). Runs under the per-source
lock (collectors/locks.py: in-process + PostgreSQL advisory lock) like vCenter / NetBackup syncs."""
import datetime as dt
import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from .. import metrics
from ..models import StorageAlertState, StorageArrayState, StorageVolumeState
from . import StorageConfig, StorageError

logger = logging.getLogger(__name__)


def read(cfg: StorageConfig, peers: Optional[List[str]] = None) -> Dict[str, Any]:
    if cfg.demo:
        from . import demo_generator
        return demo_generator.collect(cfg, peers or [cfg.key])
    if cfg.vendor == "pure":
        from . import pure
        return pure.collect(cfg)
    if cfg.vendor == "netapp":
        from . import netapp
        return netapp.collect(cfg)
    raise StorageError(f"Loại storage không hỗ trợ: {cfg.vendor}")


def _parse_at(v: Any) -> Optional[dt.datetime]:
    if not v:
        return None
    try:
        t = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.astimezone(dt.timezone.utc).replace(tzinfo=None) if t.tzinfo else t


def store(db: Session, cfg: StorageConfig, data: Dict[str, Any], now: dt.datetime) -> StorageArrayState:
    a = data["array"]
    row = db.query(StorageArrayState).filter_by(source_key=cfg.key).first() or StorageArrayState(source_key=cfg.key)
    db.add(row)
    row.vendor, row.name = cfg.vendor, (a.get("name") or cfg.name or cfg.key)[:255]
    row.model, row.version = (a.get("model") or "")[:64] or None, (a.get("version") or "")[:128] or None
    for col, key in (("capacity_gb", "capacityGB"), ("used_gb", "usedGB"), ("provisioned_gb", "provisionedGB"),
                     ("data_reduction", "dataReduction"), ("latency_read_ms", "latencyReadMs"), ("latency_write_ms", "latencyWriteMs"),
                     ("iops", "iops"), ("throughput_mbps", "throughputMBps")):
        v = a.get(key)
        setattr(row, col, float(v) if isinstance(v, (int, float)) else None)
    row.controllers = json.dumps(a.get("controllers") or [], ensure_ascii=False)
    row.hardware_issues = json.dumps(a.get("hardwareIssues") or [], ensure_ascii=False)
    row.nfs_endpoints = json.dumps(data.get("nfsEndpoints") or [], ensure_ascii=False)
    row.captured_at = row.last_ok_at = now
    row.last_error = row.last_error_at = None
    db.query(StorageVolumeState).filter_by(source_key=cfg.key).delete(synchronize_session=False)
    db.query(StorageAlertState).filter_by(source_key=cfg.key).delete(synchronize_session=False)
    db.bulk_insert_mappings(StorageVolumeState, [{
        "source_key": cfg.key, "name": str(v.get("name") or "")[:512], "container": (v.get("container") or "")[:255] or None,
        "kind": v.get("kind") or "volume", "size_gb": v.get("sizeGB"), "used_gb": v.get("usedGB"), "physical_gb": v.get("physicalGB"),
        "naa": (v.get("naa") or "")[:64].lower() or None, "nfs_path": (v.get("nfsPath") or "")[:512] or None, "captured_at": now}
        for v in data.get("volumes") or [] if v.get("name")])
    db.bulk_insert_mappings(StorageAlertState, [{
        "source_key": cfg.key, "alert_id": str(x.get("id") or "")[:128], "severity": x.get("severity") or "warning",
        "summary": str(x.get("summary") or "")[:300], "component": (x.get("component") or "")[:255] or None,
        "raised_at": _parse_at(x.get("at")), "captured_at": now} for x in data.get("alerts") or []])
    e = cfg.key
    metrics.record(db, "array", [(e, k, a.get(k)) for k in ("usedGB", "capacityGB", "latencyReadMs", "latencyWriteMs", "iops")
                                 if isinstance(a.get(k), (int, float))], now)
    return row


def record_error(db: Session, cfg: StorageConfig, message: str, now: dt.datetime) -> None:
    row = db.query(StorageArrayState).filter_by(source_key=cfg.key).first()
    if row is None:
        row = StorageArrayState(source_key=cfg.key, vendor=cfg.vendor, name=cfg.name[:255])
        db.add(row)
    row.last_error, row.last_error_at = message[:1000], now


def sync_storage(db: Session, cfg: StorageConfig, peers: Optional[List[str]] = None) -> Dict[str, Any]:
    from ..collectors.locks import source_lock
    with source_lock("storage", cfg.key):           # raises SourceBusy while another sync runs (any pod)
        started = dt.datetime.utcnow()
        try:
            data = read(cfg, peers)
        except Exception as exc:  # noqa: BLE001 — recorded on the array row, then re-raised
            msg = str(exc) if isinstance(exc, StorageError) else f"Lỗi đọc mảng lưu trữ: {type(exc).__name__}"
            logger.warning("Storage sync failed for %s: %s", cfg.key, msg)
            try:
                db.rollback()
                record_error(db, cfg, msg, dt.datetime.utcnow())
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
            raise StorageError(msg) from exc
        now = dt.datetime.utcnow()
        store(db, cfg, data, now)
        db.commit()
        logger.info("Synced storage %s (%s): %d volumes, %d alerts in %.1fs", cfg.key, cfg.vendor, len(data.get("volumes") or []),
                    len(data.get("alerts") or []), (now - started).total_seconds())
        return {"key": cfg.key, "ok": True, "volumes": len(data.get("volumes") or []), "alerts": len(data.get("alerts") or [])}
