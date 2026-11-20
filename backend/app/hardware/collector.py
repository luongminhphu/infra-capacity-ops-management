"""Sync one hardware source (Dell OME, or its demo): read devices + subsystem health, replace the source's rows
(like storage/collector.py) and record the result on hw_source_state. Runs under the per-source lock
(collectors/locks.py) on the leader pod only, as part of the scheduler round."""
import datetime as dt
import logging
from typing import Any, Dict

from sqlalchemy.orm import Session

from . import HardwareConfig, HardwareError
from .models import HwComponentHealth, HwDeviceState, HwSourceState

logger = logging.getLogger(__name__)


def read(cfg: HardwareConfig) -> Dict[str, Any]:
    if cfg.demo:
        from . import demo_generator
        return demo_generator.collect(cfg)
    if cfg.kind == "ome":
        from . import ome
        return ome.collect(cfg)
    raise HardwareError(f"Loại nguồn phần cứng không hỗ trợ: {cfg.kind}")


def _at(v: Any):
    if not v:
        return None
    try:
        return dt.datetime.fromisoformat(str(v))
    except ValueError:
        return None


def _state(db: Session, cfg: HardwareConfig) -> HwSourceState:
    row = db.query(HwSourceState).filter_by(source_key=cfg.key).first()
    if row is None:
        row = HwSourceState(source_key=cfg.key, kind=cfg.kind, name=(cfg.name or cfg.key)[:255])
        db.add(row)
    return row


def store(db: Session, cfg: HardwareConfig, data: Dict[str, Any], now: dt.datetime) -> HwSourceState:
    devices = {d["deviceId"]: d for d in data.get("devices") or []}      # an Id twice (paging overlap) is kept once
    row = _state(db, cfg)
    row.kind, row.name = cfg.kind, (cfg.name or cfg.key)[:255]
    row.device_count = len(devices)
    row.captured_at = row.last_ok_at = now
    row.last_error = row.last_error_at = None
    db.query(HwComponentHealth).filter_by(source_key=cfg.key).delete(synchronize_session=False)
    db.query(HwDeviceState).filter_by(source_key=cfg.key).delete(synchronize_session=False)
    db.bulk_insert_mappings(HwDeviceState, [{
        "source_key": cfg.key, "device_id": d["deviceId"], "service_tag": (d.get("serviceTag") or "")[:64] or None,
        "name": str(d.get("name") or d["deviceId"])[:255], "model": (d.get("model") or "")[:128] or None,
        "idrac_ip": (d.get("idracIp") or "")[:64] or None, "power_state": d.get("powerState") or "unknown",
        "status": d.get("status") or "warn", "status_code": d.get("statusCode"),
        "status_reason": (d.get("statusReason") or "")[:255] or None, "connection": d.get("connection"),
        "last_inventory_at": _at(d.get("lastInventoryAt")), "captured_at": now} for d in devices.values()])
    db.bulk_insert_mappings(HwComponentHealth, [{
        "source_key": cfg.key, "device_id": h["deviceId"], "subsystem": str(h.get("subsystem") or "")[:64],
        "status": h.get("status") or "warn", "status_code": h.get("statusCode"), "detail": h.get("detail"),
        "captured_at": now} for h in data.get("health") or [] if h.get("deviceId") in devices and h.get("subsystem")])
    return row


def record_error(db: Session, cfg: HardwareConfig, message: str, now: dt.datetime) -> None:
    row = _state(db, cfg)
    row.last_error, row.last_error_at = message[:1000], now


def sync_hardware(db: Session, cfg: HardwareConfig) -> Dict[str, Any]:
    from ..collectors.locks import source_lock
    with source_lock("hardware", cfg.key):          # raises SourceBusy while another sync runs (any pod)
        started = dt.datetime.utcnow()
        try:
            data = read(cfg)
        except Exception as exc:  # noqa: BLE001 — recorded on the source row, then re-raised
            msg = str(exc) if isinstance(exc, HardwareError) else f"Lỗi đọc OpenManage Enterprise: {type(exc).__name__}"
            logger.warning("Hardware sync failed for %s: %s", cfg.key, msg)
            try:
                db.rollback()
                record_error(db, cfg, msg, dt.datetime.utcnow())
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
            raise HardwareError(msg) from exc
        now = dt.datetime.utcnow()
        store(db, cfg, data, now)
        db.commit()
        n, h = len(data.get("devices") or []), len(data.get("health") or [])
        logger.info("Synced hardware %s (%s): %d devices, %d subsystem rows in %.1fs", cfg.key, cfg.kind, n, h,
                    (now - started).total_seconds())
        return {"key": cfg.key, "ok": True, "devices": n, "health": h}
