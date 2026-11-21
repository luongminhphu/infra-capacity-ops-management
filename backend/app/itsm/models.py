"""ServiceDesk Plus requests (ICO v2 G7a, docs/design/ico-v2-plan.md §4 ITSM).

itsm_request keeps one row per request (UNIQUE source + request id), updated in place when SDP changes it; the cleaned
description is stored (≤ 64 KB) so a better parser (G7b) can re-read it without calling SDP again. itsm_source_state keeps
the read cursor and the backfill progress of each source."""
from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Column, Date, DateTime, Float, ForeignKey, Index, Integer, String,
                        Text, UniqueConstraint)

from ..models import Base, utcnow
from ..shared.schema import in_check as _in

REQUEST_KINDS = ("new_vm", "resize", "decommission", "other")
KIND_LABELS = {"new_vm": "Cấp phát mới (VM provisioning)", "resize": "Nâng cấp / tinh chỉnh (VM reconfiguration)",
               "decommission": "Xoá VM (VM Decommision)", "other": "Khác"}
PARSE_STATUSES = ("parsed", "ai", "review", "failed")


class ItsmSourceState(Base):
    __tablename__ = "itsm_source_state"
    __table_args__ = (UniqueConstraint("source_key", name="uq_itsm_source_state"),)

    id = Column(Integer, primary_key=True)
    source_key = Column(String(64), nullable=False)
    kind = Column(String(16), nullable=False, default="sdp")
    name = Column(String(255), nullable=False)
    cursor_ms = Column(BigInteger)                   # last_updated_time (ms epoch) read up to; None = start of the backfill
    backfill_from = Column(Date)                     # the date the backfill started from (changing it restarts the backfill)
    backfill_done = Column(Boolean, nullable=False, default=False)
    requests = Column(Integer)                       # rows stored for the source
    captured_at = Column(DateTime)                   # last successful read
    last_ok_at = Column(DateTime)
    last_error = Column(Text)
    last_error_at = Column(DateTime)


class ItsmRequest(Base):
    __tablename__ = "itsm_request"
    __table_args__ = (UniqueConstraint("source_key", "req_id", name="uq_itsm_request"),
                      CheckConstraint(_in("kind", REQUEST_KINDS), name="ck_itsm_request_kind"),
                      CheckConstraint(_in("parse_status", PARSE_STATUSES), name="ck_itsm_request_parse"),     # NULL passes
                      Index("ix_itsm_request_created", "created_at"))

    id = Column(Integer, primary_key=True)
    source_key = Column(String(64), nullable=False)
    req_id = Column(String(32), nullable=False)
    template = Column(String(255))
    subject = Column(String(500))
    kind = Column(String(16), nullable=False, default="other")       # from the template name until G7b reads the body
    requester_name = Column(String(255))
    requester_email = Column(String(255), index=True)
    department = Column(String(255))
    project = Column(String(255))
    system_id = Column(Integer)                                     # G7c: matched business system
    status = Column(String(64))
    approval_status = Column(String(64))
    created_at = Column(DateTime)
    approved_at = Column(DateTime)
    resolved_at = Column(DateTime)
    updated_at = Column(DateTime)
    description = Column(Text)                                      # cleaned HTML (tables kept), ≤ 64 KB
    udf = Column(Text)                                              # JSON of the template's additional fields
    parse_status = Column(String(16))                               # G7b: None = not parsed yet
    confidence = Column(Float)
    reviewed_by = Column(String(255))
    reviewed_at = Column(DateTime)
    fetched_at = Column(DateTime, nullable=False, default=utcnow)


LINE_FIELDS = ("vm_name", "env", "os", "vcpu", "ram_gb", "disk_gb", "qty", "project")
# VM reconfiguration: the As-is configuration next to the To-be one (vcpu / ram_gb / disk_gb) → the change
BEFORE_FIELDS = ("vcpu_before", "ram_gb_before", "disk_gb_before")
STORED_FIELDS = LINE_FIELDS + BEFORE_FIELDS


class ItsmRequestLine(Base):
    """One VM (or one resource change) of a request, read from the body table, by AI (status review) or by a person."""
    __tablename__ = "itsm_request_line"
    __table_args__ = (UniqueConstraint("request_id", "line_no", name="uq_itsm_request_line"),)

    id = Column(Integer, primary_key=True)
    request_id = Column(Integer, ForeignKey("itsm_request.id", ondelete="CASCADE"), nullable=False, index=True)
    line_no = Column(Integer, nullable=False)
    vm_name = Column(String(255))
    env = Column(String(64))
    os = Column(String(128))
    project = Column(String(255))
    vcpu = Column(Float)
    ram_gb = Column(Float)
    disk_gb = Column(Float)
    qty = Column(Integer, nullable=False, default=1)
    vcpu_before = Column(Float)                                     # As-is (VM reconfiguration); None = not given
    ram_gb_before = Column(Float)
    disk_gb_before = Column(Float)
    raw = Column(Text)                                              # JSON: the cells the line came from


class ItsmLabelMap(Base):
    """Label of a body table (normalised with shared.text.norm_text) → standard field. Rows here add to / override the
    built-in labels of itsm/parse.py (added from the review screen)."""
    __tablename__ = "itsm_label_map"
    __table_args__ = (UniqueConstraint("label_norm", name="uq_itsm_label_map"),
                      CheckConstraint(_in("field", LINE_FIELDS + ("ignore",)), name="ck_itsm_label_field"))

    id = Column(Integer, primary_key=True)
    label_norm = Column(String(128), nullable=False)
    field = Column(String(16), nullable=False)
    created_by = Column(String(255))
    created_at = Column(DateTime, nullable=False, default=utcnow)


class ItsmRequestCi(Base):
    """A request line ↔ the VM it created / changed / removed (G7c). strong = full name or FQDN (+ created within the request
    window for a new VM); weak = short name only — never used for owner assignment."""
    __tablename__ = "itsm_request_ci"
    __table_args__ = (UniqueConstraint("request_id", "line_no", "ci_id", name="uq_itsm_request_ci"),
                      CheckConstraint(_in("match", ("strong", "weak")), name="ck_itsm_request_ci_match"))

    id = Column(Integer, primary_key=True)
    request_id = Column(Integer, ForeignKey("itsm_request.id", ondelete="CASCADE"), nullable=False, index=True)
    line_no = Column(Integer, nullable=False)
    ci_id = Column(Integer, ForeignKey("ci.id", ondelete="CASCADE"), nullable=False, index=True)
    match = Column(String(8), nullable=False)
    matched_by = Column(String(32), nullable=False)          # name / fqdn / short_name (+ "+window" / "+outside_window" for a new VM)


class ItsmProjectMap(Base):
    """Project (or department) written in requests → business system, confirmed by an admin and reused."""
    __tablename__ = "itsm_project_map"
    __table_args__ = (UniqueConstraint("project_norm", name="uq_itsm_project_map"),)

    id = Column(Integer, primary_key=True)
    project_norm = Column(String(255), nullable=False)
    project = Column(String(255), nullable=False)
    system_id = Column(Integer, ForeignKey("systems.id", ondelete="CASCADE"), nullable=False)
    created_by = Column(String(255))
    created_at = Column(DateTime, nullable=False, default=utcnow)


from ..shared.stored_text import json_column as _json  # noqa: E402

_json(ItsmRequest.udf, dict, "Trường bổ sung của yêu cầu SDP")
_json(ItsmRequestLine.raw, dict, "Ô gốc của dòng yêu cầu")
