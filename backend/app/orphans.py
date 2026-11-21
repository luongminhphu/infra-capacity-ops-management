"""Data left by sources that are no longer configured (Cài đặt → Nguồn dữ liệu → Dữ liệu mồ côi).

Deleting a source keeps its history under its key (a source re-added with the same key continues it). Keys
renamed, removed, or used by DEMO runs therefore leave rows behind. `scan` lists, per family, every key that
has rows but no source (UI row — enabled or not — or environment variable); `purge` deletes them in small
chunks so a large history never holds a long lock. Imported data (vcenter_key NULL / "import") is never
an orphan.
"""
import datetime as dt
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from .config import get_settings
from .models import (
    BackupJob, BackupPolicySnapshot, BackupStorageSnapshot, BackupSyncRun, BackupScopeRule, ClusterAggregate,
    ComplianceFindingSnapshot, DataSource, DatastoreState, HostHealthState, HostSnapshot, MetricPoint, MetricRollup,
    SnapshotRun, StorageAlertState, StorageArrayState, StorageVolumeState, VcAlarmState, VmOsOverride, VmSnapshot,
    VmSnapshotState,
)
from .ipam.models import IpamHost, IpamNetwork, IpamSourceState, IpamVlan
from .itsm.models import ItsmRequest, ItsmSourceState
from .hardware.models import (
    HwAlert, HwComponentHealth, HwDeviceInventory, HwDeviceState, HwDiskState, HwFirmwareState, HwPartState, HwSourceState,
    HwWarranty,
)

FAMILIES = {"vcenter": "Nền tảng ảo hoá", "netbackup": "Máy chủ backup", "storage": "Storage", "hardware": "Phần cứng (Dell OME)",
            "ipam": "IPAM (GestióIP)", "itsm": "ServiceDesk Plus"}
KIND_FAMILY = {"vcenter": "vcenter", "proxmox": "vcenter", "nutanix": "vcenter", "netbackup": "netbackup",
               "pure": "storage", "netapp": "storage", "ome": "hardware",
               "gestioip": "ipam", "sdp": "itsm"}
NOT_A_SOURCE = {None, "", "import"}
CHUNK = 5000

# family → [(label, model, key column)], children before parents (FK run_id → snapshot_runs / backup_sync_runs).
TABLES: Dict[str, List[Tuple[str, Any, str]]] = {
    "vcenter": [
        ("VM (lịch sử)", VmSnapshot, "vcenter_key"), ("Host (lịch sử)", HostSnapshot, "vcenter_key"),
        ("Cluster (lịch sử)", ClusterAggregate, "vcenter_key"), ("Phát hiện compliance", ComplianceFindingSnapshot, "vcenter_key"),
        ("Lần đồng bộ", SnapshotRun, "vcenter_key"), ("Datastore", DatastoreState, "vcenter_key"),
        ("Snapshot VM", VmSnapshotState, "vcenter_key"), ("Alarm", VcAlarmState, "vcenter_key"),
        ("Sức khoẻ host", HostHealthState, "vcenter_key"), ("Phân loại HĐH sửa tay", VmOsOverride, "vcenter_key"),
        ("Phạm vi backup", BackupScopeRule, "vcenter_key"),
    ],
    "netbackup": [
        ("Job backup", BackupJob, "nbu_key"), ("Policy (lịch sử)", BackupPolicySnapshot, "nbu_key"),
        ("Disk pool (lịch sử)", BackupStorageSnapshot, "nbu_key"), ("Lần đồng bộ", BackupSyncRun, "nbu_key"),
    ],
    "storage": [
        ("Volume / LUN", StorageVolumeState, "source_key"), ("Alert", StorageAlertState, "source_key"),
        ("Mảng", StorageArrayState, "source_key"),
    ],
    "itsm": [("Yêu cầu SDP", ItsmRequest, "source_key"), ("Trạng thái nguồn", ItsmSourceState, "source_key")],
    "ipam": [
        ("IP (IPAM)", IpamHost, "source_key"), ("Network (IPAM)", IpamNetwork, "source_key"), ("VLAN (IPAM)", IpamVlan, "source_key"),
        ("Trạng thái nguồn", IpamSourceState, "source_key"),
    ],
    "hardware": [
        ("Sức khoẻ linh kiện", HwComponentHealth, "source_key"), ("Ổ đĩa", HwDiskState, "source_key"),
        ("Linh kiện", HwPartState, "source_key"), ("Firmware", HwFirmwareState, "source_key"),
        ("Inventory máy chủ", HwDeviceInventory, "source_key"), ("Bảo hành Dell", HwWarranty, "source_key"),
        ("Alert phần cứng", HwAlert, "source_key"), ("Máy chủ", HwDeviceState, "source_key"),
        ("Trạng thái nguồn", HwSourceState, "source_key"),
    ],
}
# Time series: family → metric kind, entity = key (array) or "key:datastore".
METRIC_KIND = {"vcenter": ("datastore", True), "storage": ("array", False)}


def configured(db: Session) -> Dict[str, Set[str]]:
    """Keys of every source known in Settings (UI rows enabled or not, environment variables)."""
    out: Dict[str, Set[str]] = {f: set() for f in FAMILIES}
    for kind, key in db.query(DataSource.kind, DataSource.key):
        if kind in KIND_FAMILY:
            out[KIND_FAMILY[kind]].add(key)
    s = get_settings()
    out["vcenter"] |= {c.key for c in s.env_vcenters()}
    out["netbackup"] |= {c.key for c in s.env_netbackups()}
    return out


def _metric_filter(model, family: str, key: str):
    kind, prefixed = METRIC_KIND[family]
    ent = model.entity.like(f"{key}:%", escape="\\") if prefixed else model.entity == key
    return (model.kind == kind) & ent


def _escape(key: str) -> str:
    return key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _last_seen(db: Session, family: str, key: str) -> Optional[dt.datetime]:
    if family == "vcenter":
        return db.query(func.max(SnapshotRun.started_at)).filter(SnapshotRun.vcenter_key == key).scalar()
    if family == "netbackup":
        return db.query(func.max(BackupSyncRun.started_at)).filter(BackupSyncRun.nbu_key == key).scalar()
    if family == "itsm":
        return db.query(func.max(ItsmSourceState.captured_at)).filter(ItsmSourceState.source_key == key).scalar()
    if family == "ipam":
        return db.query(func.max(IpamSourceState.captured_at)).filter(IpamSourceState.source_key == key).scalar()
    if family == "hardware":
        return db.query(func.max(HwSourceState.captured_at)).filter(HwSourceState.source_key == key).scalar()
    return db.query(func.max(StorageArrayState.captured_at)).filter(StorageArrayState.source_key == key).scalar()


def scan(db: Session) -> List[Dict[str, Any]]:
    known = configured(db)
    found: Dict[Tuple[str, str], Dict[str, int]] = {}
    for family, tables in TABLES.items():
        for label, model, col in tables:
            c = getattr(model, col)
            for key, n in db.query(c, func.count()).group_by(c):
                if key in NOT_A_SOURCE or key in known[family]:
                    continue
                found.setdefault((family, key), {})[label] = found.get((family, key), {}).get(label, 0) + n
    for family, (kind, prefixed) in METRIC_KIND.items():
        for model in (MetricPoint, MetricRollup):
            for ent, n in db.query(model.entity, func.count()).filter(model.kind == kind).group_by(model.entity):
                key = ent.split(":", 1)[0] if prefixed else ent
                if key in NOT_A_SOURCE or key in known[family]:
                    continue
                d = found.setdefault((family, key), {})
                d["Chuỗi số liệu"] = d.get("Chuỗi số liệu", 0) + n
    out = []
    for (family, key), tables in sorted(found.items()):
        last = _last_seen(db, family, key)
        out.append({"family": family, "familyLabel": FAMILIES[family], "key": key, "rows": sum(tables.values()),
                    "tables": [{"label": k, "rows": v} for k, v in tables.items()],
                    "lastSeen": last.isoformat() + "Z" if last else None})
    return out


def _chunked_delete(db: Session, model, cond) -> int:
    total = 0
    while True:
        ids = [i for (i,) in db.query(model.id).filter(cond).limit(CHUNK)]
        if not ids:
            return total
        total += db.query(model).filter(model.id.in_(ids)).delete(synchronize_session=False)
        db.commit()


def purge(db: Session, family: str, key: str) -> Dict[str, int]:
    """Delete every row of `key` in `family`. Refused while a source with that key is configured."""
    if family not in FAMILIES:
        raise ValueError("Loại nguồn không hợp lệ.")
    if key in NOT_A_SOURCE:
        raise ValueError("Dữ liệu nhập tay không thuộc nguồn nào — không xoá ở đây.")
    if key in configured(db)[family]:
        raise ValueError("Nguồn này vẫn đang được cấu hình — xoá nguồn trước rồi mới dọn dữ liệu.")
    out: Dict[str, int] = {}
    for label, model, col in TABLES[family]:
        if hasattr(model, "id"):
            n = _chunked_delete(db, model, getattr(model, col) == key)
        else:
            n = db.query(model).filter(getattr(model, col) == key).delete(synchronize_session=False)
            db.commit()
        if n:
            out[label] = out.get(label, 0) + n
    if family in METRIC_KIND:
        esc = _escape(key)
        for model in (MetricPoint, MetricRollup):
            n = _chunked_delete(db, model, _metric_filter(model, family, esc if METRIC_KIND[family][1] else key))
            if n:
                out["Chuỗi số liệu"] = out.get("Chuỗi số liệu", 0) + n
    return out
