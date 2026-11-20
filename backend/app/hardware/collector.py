"""Sync one hardware source (Dell OME, or its demo): read devices + subsystem health, replace the source's rows
(like storage/collector.py) and record the result on hw_source_state. Runs under the per-source lock
(collectors/locks.py) on the leader pod only, as part of the scheduler round.

Slower reads ride on the same session when due (G4b): deep inventory + firmware compliance every
OME_INVENTORY_HOURS, warranty every OME_WARRANTY_HOURS, hardware alerts every round from a cursor (the newest
TimeStamp stored; read with "ge" so nothing at the boundary is missed, duplicates dropped by UNIQUE(source, alert id)).
A part that failed is retried next round and noted in last_warning; it never fails the source."""
import json
import datetime as dt
import logging
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from . import HardwareConfig, HardwareError
from .models import (
    HwAlert, HwComponentHealth, HwDeviceInventory, HwDeviceState, HwDiskState, HwFirmwareState, HwPartState, HwSourceState,
    HwWarranty,
)

logger = logging.getLogger(__name__)


def plan_for(state: Optional[HwSourceState], now: dt.datetime) -> Dict[str, Any]:
    """Which slower reads are due this round."""
    from ..config import get_settings
    s = get_settings()

    def due(at: Optional[dt.datetime], hours: int) -> bool:
        return at is None or now - at >= dt.timedelta(hours=max(1, hours))
    cursor = state.alert_cursor if state else None
    return {"inventory": due(state.inventory_at if state else None, s.ome_inventory_hours),
            "warranty": due(state.warranty_at if state else None, s.ome_warranty_hours),
            "alertsSince": cursor or now - dt.timedelta(days=max(1, s.ome_alert_backfill_days))}


def read(cfg: HardwareConfig, plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if cfg.demo:
        from . import demo_generator
        return demo_generator.collect(cfg, plan)
    if cfg.kind == "ome":
        from . import ome
        return ome.collect(cfg, plan)
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
    if "inventory" in data:
        _store_inventory(db, cfg.key, data["inventory"], now)
        row.inventory_at = now
        row.baseline_configured = bool(data.get("baselineConfigured"))
    if "warranties" in data:
        db.query(HwWarranty).filter_by(source_key=cfg.key).delete(synchronize_session=False)
        db.bulk_insert_mappings(HwWarranty, [{
            "source_key": cfg.key, "service_tag": w["serviceTag"][:64], "service_level": (w.get("serviceLevel") or "")[:255] or None,
            "start_date": _day(w.get("start")), "end_date": _day(w.get("end")), "captured_at": now} for w in data["warranties"]])
        row.warranty_at = now
    if "alerts" in data:
        newest = store_alerts(db, cfg.key, data["alerts"], now)
        if newest and (row.alert_cursor is None or newest > row.alert_cursor):
            row.alert_cursor = newest
    row.last_warning = "; ".join(data.get("warnings") or [])[:1000] or None
    return row


def _day(v: Any) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _store_inventory(db: Session, key: str, inv: Dict[int, Dict[str, Any]], now: dt.datetime) -> None:
    for m in (HwDeviceInventory, HwDiskState, HwPartState, HwFirmwareState):
        db.query(m).filter_by(source_key=key).delete(synchronize_session=False)
    db.bulk_insert_mappings(HwDeviceInventory, [{
        "source_key": key, "device_id": dev, "os_name": d.get("os"), "os_hostname": d.get("hostname"),
        "macs": json.dumps((d.get("macs") or [])[:64]), "captured_at": now} for dev, d in inv.items()])
    db.bulk_insert_mappings(HwDiskState, [{
        "source_key": key, "device_id": dev, "slot": x["slot"][:255], "model": x.get("model"), "serial": x.get("serial"),
        "media": x.get("media"), "size_gb": x.get("sizeGB"), "status": x.get("status") or "warn",
        "predictive_failure": bool(x.get("predictiveFailure")), "captured_at": now}
        for dev, d in inv.items() for x in d.get("disks") or []])
    db.bulk_insert_mappings(HwPartState, [{
        "source_key": key, "device_id": dev, "kind": x["kind"], "name": x["name"][:255], "model": x.get("model"),
        "serial": x.get("serial"), "detail": (x.get("detail") or "")[:255] or None, "status": x.get("status") or "warn",
        "captured_at": now} for dev, d in inv.items() for x in d.get("parts") or []])
    db.bulk_insert_mappings(HwFirmwareState, [{
        "source_key": key, "device_id": dev, "component": x["component"][:255], "version": x.get("version"),
        "baseline_version": x.get("baselineVersion"), "compliance": x.get("compliance"), "captured_at": now}
        for dev, d in inv.items() for x in d.get("firmware") or [] if x.get("component")])


def store_alerts(db: Session, key: str, alerts, now: dt.datetime) -> Optional[dt.datetime]:
    """Insert the alerts not stored yet (alert id per source is unique); returns the newest TimeStamp seen."""
    alerts = [a for a in alerts or [] if a.get("alertId") is not None and a.get("at")]
    if not alerts:
        return None
    ids = {a["alertId"] for a in alerts}
    have = {i for (i,) in db.query(HwAlert.alert_id).filter(HwAlert.source_key == key, HwAlert.alert_id.in_(ids))}
    fresh: Dict[int, Dict[str, Any]] = {}
    for a in alerts:
        if a["alertId"] not in have:
            fresh[a["alertId"]] = a
    db.bulk_insert_mappings(HwAlert, [{
        "source_key": key, "alert_id": a["alertId"], "device_id": a.get("deviceId"), "service_tag": a.get("serviceTag"),
        "severity": a.get("severity") or "info", "status": a.get("status"), "category": a.get("category"),
        "message_id": a.get("messageId"), "message": a.get("message"), "at": dt.datetime.fromisoformat(a["at"]),
        "captured_at": now} for a in fresh.values()])
    return max(dt.datetime.fromisoformat(a["at"]) for a in alerts)


def record_error(db: Session, cfg: HardwareConfig, message: str, now: dt.datetime) -> None:
    row = _state(db, cfg)
    row.last_error, row.last_error_at = message[:1000], now


def sync_hardware(db: Session, cfg: HardwareConfig) -> Dict[str, Any]:
    from ..collectors.locks import source_lock
    with source_lock("hardware", cfg.key):          # raises SourceBusy while another sync runs (any pod)
        started = dt.datetime.utcnow()
        plan = plan_for(db.query(HwSourceState).filter_by(source_key=cfg.key).first(), started)
        try:
            data = read(cfg, plan)
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
        out = {"key": cfg.key, "ok": True, "devices": n, "health": h}
        for k, label in (("inventory", "inventory"), ("warranties", "warranties"), ("alerts", "alerts")):
            if k in data:
                out[label] = len(data[k])
        if data.get("warnings"):
            out["warning"] = "; ".join(data["warnings"])
        return out
