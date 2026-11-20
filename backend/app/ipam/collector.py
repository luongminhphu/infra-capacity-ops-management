"""Sync one IPAM source (GestióIP or its demo): read networks / hosts / VLANs, replace the source's rows and record the
result on ipam_source_state. Per-source lock, leader pod only (scheduler round) or Đồng bộ ngay (admin)."""
import datetime as dt
import json
import logging
from typing import Any, Dict

from sqlalchemy.orm import Session

from . import IpamConfig, IpamError
from .models import IpamHost, IpamNetwork, IpamSourceState, IpamVlan

logger = logging.getLogger(__name__)


def read(cfg: IpamConfig) -> Dict[str, Any]:
    if cfg.demo:
        from . import demo_generator
        return demo_generator.collect(cfg)
    if cfg.kind == "gestioip":
        from . import gestioip
        return gestioip.collect(cfg)
    raise IpamError(f"Loại IPAM không hỗ trợ: {cfg.kind}")


def _state(db: Session, cfg: IpamConfig) -> IpamSourceState:
    row = db.query(IpamSourceState).filter_by(source_key=cfg.key).first()
    if row is None:
        row = IpamSourceState(source_key=cfg.key, kind=cfg.kind, name=(cfg.name or cfg.key)[:255])
        db.add(row)
    return row


def store(db: Session, cfg: IpamConfig, data: Dict[str, Any], now: dt.datetime) -> IpamSourceState:
    nets = {n["cidr"]: n for n in data.get("networks") or []}
    hosts = {h["ip"]: h for h in data.get("hosts") or []}        # IPs are unique in GestióIP; keep one per IP
    row = _state(db, cfg)
    row.kind, row.name = cfg.kind, (cfg.name or cfg.key)[:255]
    row.networks, row.hosts = len(nets), len(hosts)
    row.captured_at = row.last_ok_at = now
    row.last_error = row.last_error_at = None
    for m in (IpamHost, IpamNetwork, IpamVlan):
        db.query(m).filter_by(source_key=cfg.key).delete(synchronize_session=False)
    db.bulk_insert_mappings(IpamNetwork, [{
        "source_key": cfg.key, "network_id": n.get("networkId"), "cidr": n["cidr"], "descr": n.get("descr"),
        "site": n.get("site"), "category": n.get("category"), "comment": n.get("comment"), "vlan": (n.get("vlan") or "")[:64] or None,
        "rootnet": bool(n.get("rootnet")), "custom": json.dumps(n.get("custom") or {}, ensure_ascii=False), "captured_at": now}
        for n in nets.values()])
    db.bulk_insert_mappings(IpamHost, [{
        "source_key": cfg.key, "host_id": h.get("hostId"), "ip": h["ip"], "ip_version": h.get("ipVersion") or "v4",
        "network": h.get("network"), "hostname": h.get("hostname"), "descr": h.get("descr"), "site": h.get("site"),
        "category": h.get("category"), "comment": h.get("comment"), "alive": h.get("alive"), "last_response": h.get("lastResponse"),
        "serial": h.get("serial"), "mac": h.get("mac"), "custom": json.dumps(h.get("custom") or {}, ensure_ascii=False),
        "captured_at": now} for h in hosts.values()])
    db.bulk_insert_mappings(IpamVlan, [{"source_key": cfg.key, "number": v["number"], "name": v.get("name"),
                                        "descr": v.get("descr"), "captured_at": now} for v in data.get("vlans") or []])
    return row


def record_error(db: Session, cfg: IpamConfig, message: str, now: dt.datetime) -> None:
    row = _state(db, cfg)
    row.last_error, row.last_error_at = message[:1000], now


def sync_ipam(db: Session, cfg: IpamConfig) -> Dict[str, Any]:
    from ..collectors.locks import source_lock
    with source_lock("ipam", cfg.key):
        started = dt.datetime.utcnow()
        try:
            data = read(cfg)
        except Exception as exc:  # noqa: BLE001 — recorded on the source row, then re-raised
            msg = str(exc) if isinstance(exc, IpamError) else f"Lỗi đọc GestióIP: {type(exc).__name__}"
            logger.warning("IPAM sync failed for %s: %s", cfg.key, msg)
            try:
                db.rollback()
                record_error(db, cfg, msg, dt.datetime.utcnow())
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
            raise IpamError(msg) from exc
        now = dt.datetime.utcnow()
        store(db, cfg, data, now)
        db.commit()
        logger.info("Synced IPAM %s (%s): %d networks, %d hosts in %.1fs", cfg.key, cfg.kind, len(data.get("networks") or []),
                    len(data.get("hosts") or []), (now - started).total_seconds())
        return {"key": cfg.key, "ok": True, "networks": len(data.get("networks") or []), "hosts": len(data.get("hosts") or [])}
