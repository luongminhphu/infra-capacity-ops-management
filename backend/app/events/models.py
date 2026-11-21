"""Event store + incident lifecycle (ICO v2 G5, docs/design/ico-v2-plan.md §4 ③).

ops_event is append-only (one row per state transition of a watched item); ops_event_open holds what is open now, so each
round compares against it; ops_incident groups the open events of one CI."""
from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint

from ..models import Base, utcnow
from ..shared.schema import in_check as _in

LEVELS = ("warn", "bad", "crit")
TRANSITIONS = ("open", "worse", "better", "clear")
STATUSES = ("open", "acked", "resolved")


class OpsIncident(Base):
    __tablename__ = "ops_incident"
    __table_args__ = (CheckConstraint(_in("status", STATUSES), name="ck_ops_incident_status"),
                      CheckConstraint(_in("peak_level", LEVELS), name="ck_ops_incident_peak"),
                      Index("ix_ops_incident_status_ci", "status", "ci_id"))

    id = Column(Integer, primary_key=True)
    ci_id = Column(Integer, ForeignKey("ci.id", ondelete="SET NULL"), index=True)
    root_ci_id = Column(Integer, ForeignKey("ci.id", ondelete="SET NULL"))        # G6 (graph grouping); = ci_id for now
    group_key = Column(String(255), nullable=False)          # "ci:<id>", or the event key when the item has no CI
    source = Column(String(32), nullable=False)              # source of the first event
    title = Column(String(500), nullable=False)
    status = Column(String(16), nullable=False, default="open")
    peak_level = Column(String(8), nullable=False)
    opened_at = Column(DateTime, nullable=False, default=utcnow, index=True)
    acked_at = Column(DateTime)
    acked_by = Column(String(255))
    resolved_at = Column(DateTime)
    resolved_by = Column(String(255))                         # "system" = every event cleared
    assignee = Column(String(255))
    event_count = Column(Integer, nullable=False, default=0)
    flapping = Column(Boolean, nullable=False, default=False)
    flap_alerted = Column(Boolean, nullable=False, default=False)
    flap_sent_channels = Column(Text)                        # CSV of Discord channel ids told about the flapping
    ticket_ref = Column(String(64))
    note = Column(Text)


class OpsEvent(Base):
    __tablename__ = "ops_event"
    __table_args__ = (CheckConstraint(_in("transition", TRANSITIONS), name="ck_ops_event_transition"),
                      Index("ix_ops_event_key_at", "event_key", "at"), Index("ix_ops_event_ci_at", "ci_id", "at"))

    id = Column(Integer, primary_key=True)
    at = Column(DateTime, nullable=False, default=utcnow, index=True)
    source = Column(String(32), nullable=False)
    ci_id = Column(Integer)                                   # no FK: history outlives a retired / deleted CI
    incident_id = Column(Integer, index=True)
    event_key = Column(String(255), nullable=False)
    level = Column(String(8))                                 # level after the transition (None on clear)
    transition = Column(String(8), nullable=False)
    title = Column(String(500), nullable=False)
    detail = Column(Text)


class OpsEventOpen(Base):
    __tablename__ = "ops_event_open"
    __table_args__ = (UniqueConstraint("source", "event_key", name="uq_ops_event_open"),
                      CheckConstraint(_in("level", LEVELS), name="ck_ops_event_open_level"))

    id = Column(Integer, primary_key=True)
    source = Column(String(32), nullable=False)
    event_key = Column(String(255), nullable=False)
    ci_id = Column(Integer)
    incident_id = Column(Integer, ForeignKey("ops_incident.id", ondelete="SET NULL"), index=True)
    level = Column(String(8), nullable=False)
    title = Column(String(500), nullable=False)
    opened_at = Column(DateTime, nullable=False, default=utcnow)
    last_seen = Column(DateTime, nullable=False, default=utcnow)



CHANGE_KINDS = ("vm_created", "vm_removed", "vm_reconfigured", "vm_migrated", "vm_powered_on", "vm_powered_off",
                "host_connection_lost", "host_maintenance_enter", "host_maintenance_exit", "ha_vm_restarted",
                "ha_host_failed", "alarm_status")


class ChangeLog(Base):
    """Who changed what, when (G6): vCenter events read with eventManager.QueryEvents (read-only), one row per event."""
    __tablename__ = "change_log"
    __table_args__ = (UniqueConstraint("vcenter_key", "vc_event_id", name="uq_change_log_event"),
                      CheckConstraint(_in("kind", CHANGE_KINDS), name="ck_change_log_kind"),
                      Index("ix_change_log_ci_at", "ci_id", "at"))

    id = Column(Integer, primary_key=True)
    at = Column(DateTime, nullable=False, index=True)
    vcenter_key = Column(String(64), nullable=False)
    vc_event_id = Column(Integer, nullable=False)                # vCenter event key (unique per vCenter)
    ci_id = Column(Integer)                                       # no FK: history outlives a retired CI
    kind = Column(String(32), nullable=False, index=True)
    user_name = Column(String(255), index=True)
    object_type = Column(String(16))                              # vm / host / cluster / datastore
    object_name = Column(String(255))
    message = Column(String(1000))
    detail = Column(Text)                                         # JSON: event type, from / to, alarm…
    handled = Column(Boolean, nullable=False, default=False)      # incident kinds: already turned into an ops_event


class ChangeCursor(Base):
    """End of the last event window read from each vCenter (the next read starts there)."""
    __tablename__ = "change_cursor"

    vcenter_key = Column(String(64), primary_key=True)
    until = Column(DateTime, nullable=False)

from ..shared.stored_text import csv_column as _csv, json_column as _json  # noqa: E402

_csv(OpsIncident.flap_sent_channels, "Kênh Discord đã báo chập chờn", item=r"\d+")
_json(ChangeLog.detail, dict, "Chi tiết event vCenter")
