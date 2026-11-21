"""Latest contention figures per VM / host (ICO v2 G9c), replaced per vCenter at each sync."""
from sqlalchemy import CheckConstraint, Column, DateTime, Float, Index, Integer, String

from ..models import Base, utcnow
from ..shared.schema import in_check as _in


class ContentionState(Base):
    __tablename__ = "contention_state"
    __table_args__ = (CheckConstraint(_in("kind", ("vm", "host")), name="ck_contention_kind"),
                      Index("ix_contention_vc", "vcenter_key", "kind"))

    id = Column(Integer, primary_key=True)
    vcenter_key = Column(String(64), nullable=False)
    kind = Column(String(4), nullable=False)
    name = Column(String(255), nullable=False)
    cluster = Column(String(255))
    host = Column(String(255))
    vcpu = Column(Integer)
    ready_pct = Column(Float)
    costop_pct = Column(Float)
    balloon_mb = Column(Float)
    swapped_mb = Column(Float)
    latency_ms = Column(Float)
    balloon_polls = Column(Integer, nullable=False, default=0)     # consecutive syncs with balloon > 0
    swap_polls = Column(Integer, nullable=False, default=0)        # consecutive syncs with swapped > 0
    captured_at = Column(DateTime, nullable=False, default=utcnow)
