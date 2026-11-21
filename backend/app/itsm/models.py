"""ServiceDesk Plus requests (ICO v2 G7a, docs/design/ico-v2-plan.md §4 ITSM).

itsm_request keeps one row per request (UNIQUE source + request id), updated in place when SDP changes it; the cleaned
description is stored (≤ 64 KB) so a better parser (G7b) can re-read it without calling SDP again. itsm_source_state keeps
the read cursor and the backfill progress of each source."""
from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Column, Date, DateTime, Float, Index, Integer, String, Text,
                        UniqueConstraint)

from ..models import Base, utcnow
from ..shared.schema import in_check as _in

REQUEST_KINDS = ("new_vm", "resize", "decommission", "other")
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


from ..shared.stored_text import json_column as _json  # noqa: E402

_json(ItsmRequest.udf, dict, "Trường bổ sung của yêu cầu SDP")
