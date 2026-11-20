"""Latest server hardware state per OME source (ICO v2 G4a, docs/design/ico-v2-plan.md §4).

Replaced at each sync like the storage state tables (infra.py store pattern): one row per device and per
non-normal subsystem. hw_source_state keeps the last sync result of each source (an error keeps the previous
rows and says since when they are stale).
"""
from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, Date, DateTime, Float, Index, Integer, String, Text, UniqueConstraint,
)

from ..models import Base, utcnow

LEVELS = ("ok", "warn", "bad", "crit")
POWER_STATES = ("on", "off", "powering_on", "powering_off", "unknown")


def _in(col: str, values) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


class HwSourceState(Base):
    __tablename__ = "hw_source_state"
    __table_args__ = (UniqueConstraint("source_key", name="uq_hw_source_state"),)

    id = Column(Integer, primary_key=True)
    source_key = Column(String(64), nullable=False)
    kind = Column(String(16), nullable=False, default="ome")
    name = Column(String(255), nullable=False)
    device_count = Column(Integer)
    captured_at = Column(DateTime)
    last_ok_at = Column(DateTime)
    last_error = Column(Text)
    last_error_at = Column(DateTime)
    # G4b: slower reads (deep inventory + firmware compliance, warranty) and the alert cursor
    inventory_at = Column(DateTime)
    warranty_at = Column(DateTime)
    alert_cursor = Column(DateTime)                 # newest alert TimeStamp stored (alerts are read from it on)
    baseline_configured = Column(Boolean)           # None = never checked; False = OME has no firmware baseline
    last_warning = Column(Text)                     # a part that failed while the main read worked (e.g. warranty)


class HwDeviceState(Base):
    __tablename__ = "hw_device_state"
    __table_args__ = (
        UniqueConstraint("source_key", "device_id", name="uq_hw_device_state"),
        CheckConstraint(_in("status", LEVELS), name="ck_hw_device_status"),
        CheckConstraint(_in("power_state", POWER_STATES), name="ck_hw_device_power"),
    )

    id = Column(Integer, primary_key=True)
    source_key = Column(String(64), nullable=False)
    device_id = Column(Integer, nullable=False)                 # OME device Id
    service_tag = Column(String(64), index=True)                # norm_serial form, joins hosts / DC assets / Opex
    name = Column(String(255), nullable=False)
    model = Column(String(128))
    idrac_ip = Column(String(64))
    power_state = Column(String(16), nullable=False, default="unknown")
    status = Column(String(8), nullable=False)                  # ok | warn | bad | crit
    status_code = Column(Integer)                               # raw OME code (1000 / 2000 / 3000 / 4000 / 5000)
    status_reason = Column(String(255))                         # "không có dữ liệu" for unknown / no status
    connection = Column(Boolean)
    last_inventory_at = Column(DateTime)
    captured_at = Column(DateTime, nullable=False, default=utcnow)


class HwComponentHealth(Base):
    __tablename__ = "hw_component_health"
    __table_args__ = (
        CheckConstraint(_in("status", LEVELS), name="ck_hw_component_status"),
        Index("ix_hw_component_device", "source_key", "device_id"),
    )

    id = Column(Integer, primary_key=True)
    source_key = Column(String(64), nullable=False)
    device_id = Column(Integer, nullable=False)
    subsystem = Column(String(64), nullable=False)              # PowerSupply, Fan, Memory, Storage…
    status = Column(String(8), nullable=False)
    status_code = Column(Integer)
    detail = Column(Text)
    captured_at = Column(DateTime, nullable=False, default=utcnow)


SEVERITIES = ("crit", "bad", "warn", "info")
COMPLIANCE = ("ok", "upgrade", "downgrade", "unknown")


class HwDeviceInventory(Base):
    """Per-device facts from InventoryDetails (OS, NIC MACs), replaced at each deep inventory (OME_INVENTORY_HOURS)."""
    __tablename__ = "hw_device_inventory"
    __table_args__ = (UniqueConstraint("source_key", "device_id", name="uq_hw_device_inventory"),)

    id = Column(Integer, primary_key=True)
    source_key = Column(String(64), nullable=False)
    device_id = Column(Integer, nullable=False)
    os_name = Column(String(255))
    os_hostname = Column(String(255))
    macs = Column(Text, nullable=False, default="[]")       # JSON ["aa:bb:…"] (norm_mac)
    captured_at = Column(DateTime, nullable=False, default=utcnow)


class HwDiskState(Base):
    __tablename__ = "hw_disk_state"
    __table_args__ = (
        CheckConstraint(_in("status", LEVELS), name="ck_hw_disk_status"),
        Index("ix_hw_disk_device", "source_key", "device_id"),
    )

    id = Column(Integer, primary_key=True)
    source_key = Column(String(64), nullable=False)
    device_id = Column(Integer, nullable=False)
    slot = Column(String(255), nullable=False)               # DiskNumber / slot text
    model = Column(String(128))
    serial = Column(String(64))
    media = Column(String(16))                               # HDD | SSD
    size_gb = Column(Float)
    status = Column(String(8), nullable=False)
    predictive_failure = Column(Boolean, nullable=False, default=False)
    captured_at = Column(DateTime, nullable=False, default=utcnow)


class HwPartState(Base):
    """RAID controllers, power supplies, memory modules (InventoryDetails), replaced at each deep inventory."""
    __tablename__ = "hw_part_state"
    __table_args__ = (
        CheckConstraint(_in("status", LEVELS), name="ck_hw_part_status"),
        CheckConstraint("kind IN ('raid', 'psu', 'memory')", name="ck_hw_part_kind"),
        Index("ix_hw_part_device", "source_key", "device_id"),
    )

    id = Column(Integer, primary_key=True)
    source_key = Column(String(64), nullable=False)
    device_id = Column(Integer, nullable=False)
    kind = Column(String(8), nullable=False)
    name = Column(String(255), nullable=False)
    model = Column(String(128))
    serial = Column(String(64))
    detail = Column(String(255))                             # size / output watts / firmware
    status = Column(String(8), nullable=False)
    captured_at = Column(DateTime, nullable=False, default=utcnow)


class HwFirmwareState(Base):
    __tablename__ = "hw_firmware_state"
    __table_args__ = (
        CheckConstraint("compliance IS NULL OR " + _in("compliance", COMPLIANCE), name="ck_hw_firmware_compliance"),
        Index("ix_hw_firmware_device", "source_key", "device_id"),
    )

    id = Column(Integer, primary_key=True)
    source_key = Column(String(64), nullable=False)
    device_id = Column(Integer, nullable=False)
    component = Column(String(255), nullable=False)
    version = Column(String(64))
    baseline_version = Column(String(64))
    compliance = Column(String(10))                          # NULL: no baseline covers it
    captured_at = Column(DateTime, nullable=False, default=utcnow)


class HwWarranty(Base):
    """Dell warranty entitlements (WarrantyService/Warranties, once a day); several rows per service tag."""
    __tablename__ = "hw_warranty"
    __table_args__ = (Index("ix_hw_warranty_tag", "service_tag"),)

    id = Column(Integer, primary_key=True)
    source_key = Column(String(64), nullable=False, index=True)
    service_tag = Column(String(64), nullable=False)
    service_level = Column(String(255))
    start_date = Column(Date)
    end_date = Column(Date)
    captured_at = Column(DateTime, nullable=False, default=utcnow)


class HwAlert(Base):
    """Hardware alerts of OME (AlertService/Alerts), read incrementally by TimeStamp; append-only, kept
    HW_ALERT_RETENTION_MONTHS (collectors/retention.py). ICO never acknowledges or removes them in OME."""
    __tablename__ = "hw_alert"
    __table_args__ = (
        UniqueConstraint("source_key", "alert_id", name="uq_hw_alert"),
        CheckConstraint(_in("severity", SEVERITIES), name="ck_hw_alert_severity"),
        Index("ix_hw_alert_at", "at"),
    )

    id = Column(Integer, primary_key=True)
    source_key = Column(String(64), nullable=False)
    alert_id = Column(BigInteger, nullable=False)
    device_id = Column(Integer)
    service_tag = Column(String(64), index=True)
    severity = Column(String(8), nullable=False)
    status = Column(String(16))                              # OME StatusName (Not Acknowledged / Acknowledged)
    category = Column(String(128))                           # SubCategoryName (Power Supply, Storage …)
    message_id = Column(String(32))
    message = Column(Text)
    at = Column(DateTime, nullable=False)
    captured_at = Column(DateTime, nullable=False, default=utcnow)


# JSON kept in Text columns is checked on every ORM write (shared/stored_text.py).
from ..shared.stored_text import json_column as _json  # noqa: E402

_json(HwDeviceInventory.macs, list, "Địa chỉ MAC của máy chủ", nullable=False)
