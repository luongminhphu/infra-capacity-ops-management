"""Infrastructure health pages (Giai đoạn 1) and operations alert rules (Cài đặt → Cảnh báo)."""
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import alert_rules, infra
from ..database import get_db
from ..downloads import attachment, stamp
from ..models import AuditLog, User
from ..scope import OwnerScope, Scope, in_vm_scope, owner_scope_query
from ..security import get_current_user, require_admin

router = APIRouter(prefix="/api", tags=["infra"])


def _scope(vcenter: Optional[str], clusters: Optional[str], hosts: Optional[str]) -> Scope:
    return Scope.from_query(vcenter or "all", clusters, hosts)


@router.get("/infra/summary")
def summary(vcenter: Optional[str] = "all", clusters: Optional[str] = None, hosts: Optional[str] = None,
            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return infra.summary(db, _scope(vcenter, clusters, hosts))


@router.get("/infra/datastores")
def datastores(vcenter: Optional[str] = "all", clusters: Optional[str] = None, hosts: Optional[str] = None,
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return infra.datastores(db, _scope(vcenter, clusters, hosts))


@router.get("/infra/datastores/{vcenter_key}/{ds_key}/history")
def datastore_history(vcenter_key: str, ds_key: str, days: int = 30, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    return infra.datastore_history(db, vcenter_key, ds_key, days)


@router.get("/infra/storage")
def storage(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return infra.storage(db)


@router.post("/infra/storage/sync")
def storage_sync(user: User = Depends(require_admin)):
    """Read every storage array now (read-only), like the scheduler round does."""
    from ..collectors.scheduler import sync_storages
    from ..config import get_settings
    return {"results": sync_storages(get_settings())}


@router.get("/infra/storage/{source_key}/history")
def storage_history(source_key: str, days: int = 30, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    import datetime as dt
    from .. import metrics
    now = dt.datetime.utcnow()
    since = now - dt.timedelta(days=max(1, min(days, 1095)))
    out = {}
    for m in ("usedGB", "capacityGB", "latencyReadMs", "latencyWriteMs", "iops"):
        out[m] = [[t.replace(microsecond=0).isoformat() + "Z", round(v, 2)] for t, v in metrics.series(db, "array", [source_key], m, since, now)[source_key]]
    return out


@router.get("/infra/snapshots")
def snapshots(vcenter: Optional[str] = "all", clusters: Optional[str] = None, hosts: Optional[str] = None,
              osc: OwnerScope = Depends(owner_scope_query), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return osc.filter_vms(db, infra.snapshots(db, _scope(vcenter, clusters, hosts)))


@router.get("/infra/alarms")
def alarms(vcenter: Optional[str] = "all", clusters: Optional[str] = None, hosts: Optional[str] = None,
           osc: OwnerScope = Depends(owner_scope_query), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = infra.alarms(db, _scope(vcenter, clusters, hosts))
    r = osc.resolve(db)
    if r is None:
        return rows
    # owner filter: VM alarms of the VMs in scope only (host / cluster alarms are not owned by a system)
    return [a for a in rows if a["entityType"] == "vm" and in_vm_scope(r, {"vcenterKey": a["vcenterKey"], "vm": a["entity"]})]


def _scope_label(db: Session, sc: Scope, osc: OwnerScope) -> str:
    parts = []
    if sc.vcenter:
        parts.append(f"vCenter {sc.vcenter}")
    if sc.clusters:
        parts.append("cluster " + ", ".join(sc.clusters[:5]) + (f" và {len(sc.clusters) - 5} khác" if len(sc.clusters) > 5 else ""))
    if sc.hosts:
        parts.append(f"{len(sc.hosts)} host")
    label = osc.describe(db) if osc.active else ""
    return " · ".join(parts + ([label] if label else []))


@router.get("/infra/report.pdf")
def infra_report_pdf(vcenter: Optional[str] = "all", clusters: Optional[str] = None, hosts: Optional[str] = None,
                     osc: OwnerScope = Depends(owner_scope_query), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Sức khoẻ hạ tầng (PDF): datastore, mảng lưu trữ, snapshot VM, alarm, sức khoẻ host — the rows of the pages."""
    from .. import report_infra
    sc = _scope(vcenter, clusters, hosts)
    data = report_infra.build_pdf(infra.report_data(db, sc, osc), user.display_name or user.username, _scope_label(db, sc, osc))
    return attachment(data, f"suc-khoe-ha-tang-{stamp()}.pdf")


@router.get("/infra/report.xlsx")
def infra_report_xlsx(vcenter: Optional[str] = "all", clusters: Optional[str] = None, hosts: Optional[str] = None,
                      osc: OwnerScope = Depends(owner_scope_query), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from .. import excel_infra
    sc = _scope(vcenter, clusters, hosts)
    data = excel_infra.build_xlsx(infra.report_data(db, sc, osc), _scope_label(db, sc, osc))
    return attachment(data, f"suc-khoe-ha-tang-{stamp()}.xlsx")


@router.get("/infra/health")
def health(vcenter: Optional[str] = "all", clusters: Optional[str] = None, hosts: Optional[str] = None,
           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return infra.host_health(db, _scope(vcenter, clusters, hosts))


# ---------------------------------------------------------------- alert rules (admin)
def _ops_channels(db: Session):
    from ..spend.models import SpendNotifyChannel
    return (db.query(SpendNotifyChannel).filter(SpendNotifyChannel.kind == "discord", SpendNotifyChannel.ops_alerts.is_(True))
            .order_by(SpendNotifyChannel.name).all())


@router.get("/settings/alerts")
def get_alert_rules(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    return alert_rules.out(db, _ops_channels(db))


@router.put("/settings/alerts")
def put_alert_rules(request: Request, body: Dict[str, Any] = Body(...), user: User = Depends(require_admin),
                    db: Session = Depends(get_db)):
    before = alert_rules.out(db, [])
    try:
        alert_rules.save(db, body, [str(c.id) for c in _ops_channels(db)])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    db.flush()
    after = alert_rules.out(db, [])
    db.add(AuditLog(username=user.username, action="settings.alerts", client_ip=(request.client.host if request.client else None),
                    detail=json.dumps({"before": {"rules": before["rules"], "quiet": before["quiet"]},
                                       "after": {"rules": after["rules"], "quiet": after["quiet"]}}, ensure_ascii=False)))
    db.commit()
    return alert_rules.out(db, _ops_channels(db))
