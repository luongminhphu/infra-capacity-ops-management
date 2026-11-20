"""Settings → Nguồn dữ liệu: add / edit / test / disable / delete vCenter and NetBackup servers.
Admin only. Secrets are write-only: responses say whether one is saved, never what it is."""
import datetime as dt
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import secrets_box, sources
from ..config import get_settings
from ..database import get_db
from ..models import AuditLog, DataSource, User
from ..security import get_current_user, require_admin

router = APIRouter(prefix="/api/admin/sources", tags=["sources"])
# Status of every source for any signed-in user (top bar) + "Đồng bộ ngay" for every source (admin).
status_router = APIRouter(prefix="/api/sources", tags=["sources"])
FAMILY_OF_KIND = {"vcenter": "virtualization", "proxmox": "virtualization", "nutanix": "virtualization", "netbackup": "backup",
                  "pure": "storage", "netapp": "storage", "ome": "hardware", "gestioip": "ipam"}


@status_router.get("/status")
def sources_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Connection state of every polled source from its last sync (no host, no secret)."""
    from .. import source_status
    items = source_status.status_all(db)
    return {"summary": source_status.summary(items), "items": items,
            "staleHours": round(source_status.stale_after().total_seconds() / 3600, 1)}


def _sync_task(fam: str, cfg, settings):
    from ..collectors import scheduler
    if fam == "virtualization":
        return lambda: scheduler._sync_vcenter_task(cfg, settings)
    if fam == "backup":
        return lambda: scheduler._sync_backup_task(cfg, settings)
    if fam == "storage":
        peers = [c.key for c in settings.storages() if c.vendor == cfg.vendor and c.demo]
        return lambda: scheduler._sync_storage_task(cfg, peers or [cfg.key])
    if fam == "ipam":
        return lambda: scheduler._sync_ipam_task(cfg)
    return lambda: scheduler._sync_ome_task(cfg)


@status_router.post("/sync-all")
def sync_all_sources(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Read every source now (read-only, same as a scheduler round; each source under its own lock). The CI registry,
    owner rules and alerts follow at the next scheduler round on the leader pod."""
    from .. import inbox, source_status
    from ..collectors import scheduler
    s = get_settings()
    results = scheduler.sync_everything(s)
    db.add(AuditLog(username=user.username, action="sources.sync_all", client_ip=request.client.host if request.client else None,
                    detail=json.dumps({"ok": sum(1 for r in results if r.get("ok")), "total": len(results)})))
    db.commit()
    inbox.invalidate()
    items = source_status.status_all(db)
    return {"results": [{k: v for k, v in r.items() if k in ("key", "vcenterKey", "ok", "error", "skipped", "timedOut")} for r in results],
            "summary": source_status.summary(items), "items": items}


def _audit(db: Session, user: User, action: str, request: Request, row: DataSource, **extra) -> None:
    db.add(AuditLog(username=user.username, action=action, client_ip=request.client.host if request.client else None,
                    detail=json.dumps({"kind": row.kind, "key": row.key, "host": row.host, **extra})))


def _row(db: Session, source_id: int) -> DataSource:
    row = db.get(DataSource, source_id)
    if not row:
        raise HTTPException(404, "Không tìm thấy nguồn dữ liệu")
    return row


@router.get("")
def list_sources(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    s = get_settings()
    rows = db.query(DataSource).order_by(DataSource.kind, DataSource.id).all()
    ui_keys = {(r.kind, r.key) for r in rows}
    env = [sources.public_env(c, "vcenter", ("vcenter", c.key) in ui_keys) for c in s.env_vcenters()] + \
          [sources.public_env(c, "netbackup", ("netbackup", c.key) in ui_keys) for c in s.env_netbackups()]
    from .. import source_status
    status = {(i["family"], i["key"]): i for i in source_status.status_all(db)}
    items = [sources.public(r) for r in rows] + env
    for it in items:
        st = status.get((FAMILY_OF_KIND.get(it["kind"]), it["key"])) if it.get("enabled") else None
        it["sync"] = {k: st[k] for k in ("state", "stateText", "level", "lastSyncAt", "lastOkAt", "error")} if st else None
    return {"secretsKeyConfigured": secrets_box.dedicated_key(), "secrets": secrets_box.status(db),
            "domainTypes": list(sources.NBU_DOMAIN_TYPES), "items": items}


@router.post("/{source_id}/sync")
def sync_source(source_id: int, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Read one UI source now (read-only), whatever its kind; the answer says whether it worked and why not."""
    from .. import inbox, source_status
    from ..collectors import scheduler
    row = _row(db, source_id)
    if not row.enabled:
        raise HTTPException(409, "Nguồn đang tắt — bật lại rồi đồng bộ.")
    fam = FAMILY_OF_KIND.get(row.kind)
    s = get_settings()
    cfg = next((c for f, _k, c in source_status.configured() if f == fam and c.key == row.key), None)
    if cfg is None:
        raise HTTPException(404, "Nguồn chưa được nạp — tải lại trang rồi thử lại.")
    res = scheduler.run_syncs([_sync_task(fam, cfg, s)], [{"key": row.key}], 1, scheduler.deadline_seconds(s))[0]
    _audit(db, user, "sources.sync", request, row, ok=bool(res.get("ok")))
    db.commit()
    inbox.invalidate()
    st = next((i for i in source_status.status_all(db) if i["family"] == fam and i["key"] == row.key), None)
    return {"ok": bool(res.get("ok")), "error": res.get("error"), "skipped": bool(res.get("skipped")), "status": st}


@router.post("/rotate-secrets")
def rotate_secrets(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Re-encrypt now every secret still on a previous key (also done at each pod start)."""
    if not secrets_box.dedicated_key():
        raise HTTPException(409, "Chưa đặt SECRETS_KEY — thêm vào Secret rồi khởi động lại app.")
    res = secrets_box.rotate(db)
    db.add(AuditLog(username=user.username, action="secrets.rotate", client_ip=request.client.host if request.client else None,
                    detail=json.dumps(res)))
    db.commit()
    sources.invalidate_cache()
    return res


@router.get("/orphans")
def list_orphans(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    from .. import orphans
    return {"items": orphans.scan(db)}


@router.post("/orphans/purge")
async def purge_orphan(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_admin),
                       db: Session = Depends(get_db)):
    """Delete the history of a key no source uses any more. `confirm` must repeat the key."""
    from fastapi.concurrency import run_in_threadpool
    from .. import inbox, orphans
    family, key = str(data.get("family") or ""), str(data.get("key") or "")
    if not key or data.get("confirm") != key:
        raise HTTPException(400, "Gõ lại đúng mã nguồn để xác nhận xoá.")
    try:
        res = await run_in_threadpool(orphans.purge, db, family, key)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    db.add(AuditLog(username=user.username, action="sources.orphan_purge", client_ip=request.client.host if request.client else None,
                    detail=json.dumps({"family": family, "key": key, "deleted": res})))
    db.commit()
    inbox.invalidate()
    return {"deleted": res, "rows": sum(res.values())}


@router.post("")
def create_source(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    row = DataSource(enabled=True)
    try:
        sources.apply_input(row, data, creating=True)
    except sources.SourceError as exc:
        raise HTTPException(400, str(exc)) from exc
    if db.query(DataSource).filter_by(kind=row.kind, key=row.key).first():
        raise HTTPException(409, f"Mã {row.key} đã tồn tại.")
    if row.kind in sources.HYPERVISOR_KINDS:     # vCenter / Proxmox / Nutanix share one history family
        clash = db.query(DataSource).filter(DataSource.kind.in_(sources.HYPERVISOR_KINDS), DataSource.key == row.key).first()
        env_clash = row.kind != "vcenter" and any(c.key == row.key for c in get_settings().env_vcenters())
        if clash or env_clash:
            raise HTTPException(409, f"Mã {row.key} đã dùng cho một nền tảng ảo hoá khác — chọn mã khác.")
    row.updated_by, row.updated_at = user.username, dt.datetime.utcnow()
    db.add(row)
    db.flush()
    _audit(db, user, "source.create", request, row)
    db.commit()
    sources.invalidate_cache()
    return sources.public(row)


@router.put("/{source_id}")
def update_source(source_id: int, request: Request, data: Dict[str, Any] = Body(...),
                  user: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _row(db, source_id)
    try:
        sources.apply_input(row, data, creating=False)
    except sources.SourceError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    row.updated_by, row.updated_at = user.username, dt.datetime.utcnow()
    _audit(db, user, "source.update", request, row,
           secretChanged=bool(data.get("password") or data.get("apiKey") or data.get("clearPassword") or data.get("clearApiKey")))
    db.commit()
    sources.invalidate_cache()
    return sources.public(row)


@router.delete("/{source_id}")
def delete_source(source_id: int, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Removes the connection only; collected history stays under the same key."""
    row = _row(db, source_id)
    _audit(db, user, "source.delete", request, row)
    db.delete(row)
    db.commit()
    sources.invalidate_cache()
    return {"ok": True}


def _target(row: DataSource) -> tuple:
    o = sources._opts(row) if row.kind == "netbackup" else {}
    return ((row.host or "").lower(), row.port, (o.get("domainType") or ""), (o.get("domainName") or "").lower())


def _target_changed(existing: DataSource, probe: DataSource) -> bool:
    return _target(existing) != _target(probe)


def _drop_reused_secrets(probe: DataSource, data: Dict[str, Any]) -> None:
    """The form points at another server than the saved source: never send the saved
    (write-only) password / API key there — only secrets typed in this request are used."""
    if not data.get("password"):
        probe.password_enc = None
    if not data.get("apiKey"):
        probe.api_key_enc = None
    if not probe.demo and not (probe.password_enc or probe.api_key_enc):
        raise sources.SourceError("Đã đổi host / cổng / domain so với nguồn đã lưu: nhập lại mật khẩu "
                                  "(hoặc API key) để kiểm tra kết nối.")


@router.post("/test")
def test_source(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    """Test the values in the form (not saved). An empty password / API key uses the saved one,
    only while host, port and NetBackup domain match the saved source (SEC-09)."""
    source_id: Optional[int] = data.get("id")
    existing = _row(db, int(source_id)) if source_id else None
    probe = DataSource(kind=existing.kind if existing else data.get("kind"), key=existing.key if existing else "test",
                       password_enc=existing.password_enc if existing else None,
                       api_key_enc=existing.api_key_enc if existing else None, options=existing.options if existing else None)
    try:
        sources.apply_input(probe, {**data, "key": probe.key, "kind": probe.kind}, creating=existing is None)
        if existing and _target_changed(existing, probe):
            _drop_reused_secrets(probe, data)
        message, ok = sources.test_connection(sources.to_config(probe), probe.kind), True
    except sources.SourceError as exc:
        message, ok = str(exc), False
    if existing:
        existing.last_test_at, existing.last_test_ok, existing.last_test_message = dt.datetime.utcnow(), ok, message[:1000]
        _audit(db, user, "source.test", request, existing, ok=ok, targetHost=probe.host, targetPort=probe.port)
        db.commit()
    return {"ok": ok, "message": message}


@router.post("/import-env")
def import_env(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Copy every variable-based source (credentials included) into Settings; existing keys are skipped."""
    s = get_settings()
    added = []
    for kind, configs in (("vcenter", s.env_vcenters()), ("netbackup", s.env_netbackups())):
        for cfg in configs:
            if db.query(DataSource).filter_by(kind=kind, key=cfg.key).first():
                continue
            row = sources.row_from_env(cfg, kind, user.username)
            db.add(row)
            db.flush()
            _audit(db, user, "source.import_env", request, row)
            added.append(f"{kind}:{cfg.key}")
    db.commit()
    sources.invalidate_cache()
    return {"added": added}
