"""Latest server hardware state per OME source (ICO v2 G4a, docs/design/ico-v2-plan.md §4).

Replaced at each sync like the storage state tables (infra.py store pattern): one row per device and per
non-normal subsystem. hw_source_state keeps the last sync result of each source (an error keeps the previous
rows and says since when they are stale).
"""
from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Index, Integer, String, Text, UniqueConstraint

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
