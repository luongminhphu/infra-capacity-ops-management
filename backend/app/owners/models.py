"""Tables of Hệ thống & Owner (docs/design/ico-v2-plan.md §4: system, system_member, owner_rule).

Table names are plural like the rest of the app ("system" is an SQL keyword). Invariants in the schema:
code / normalised name UNIQUE, criticality 1–4, env and status in fixed sets, e-mails stored lower case,
a VM (CI) in at most one system (UNIQUE ci_id), assignment source in a fixed set.
"""
from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String

from ..database import Base
from ..models import utcnow

ENVS = ("prod", "uat", "dev", "dr", "other")
STATUSES = ("active", "retired")
SOURCES = ("manual", "attribute", "tag", "rule", "itsm")      # manual always wins over the automatic ones
CONFIDENCES = ("strong", "weak")
RULE_KINDS = ("folder", "resource_pool", "attribute", "tag", "name_regex", "cluster")


def _in(col: str, values) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


def _lower(col: str) -> str:
    return f"{col} IS NULL OR {col} = lower({col})"


class OwnerSequence(Base):
    """Last number used per code prefix (SYS-0001…), like spend_sequences."""
    __tablename__ = "owner_sequences"

    kind = Column(String(8), primary_key=True)
    last_no = Column(Integer, nullable=False, default=0)


class System(Base):
    __tablename__ = "systems"
    __table_args__ = (
        CheckConstraint("criticality BETWEEN 1 AND 4", name="ck_system_criticality"),
        CheckConstraint(_in("env", ENVS), name="ck_system_env"),
        CheckConstraint(_in("status", STATUSES), name="ck_system_status"),
        CheckConstraint(_lower("owner_email"), name="ck_system_owner_lower"),
        CheckConstraint(_lower("deputy_email"), name="ck_system_deputy_lower"),
        CheckConstraint(_lower("manager_email"), name="ck_system_manager_lower"),
        Index("ix_systems_owner", "owner_email"),
        Index("ix_systems_department", "department"),
    )

    id = Column(Integer, primary_key=True)
    code = Column(String(12), nullable=False, unique=True)          # SYS-0001, never reused
    name = Column(String(255), nullable=False)
    name_norm = Column(String(255), nullable=False, unique=True)    # shared.text.norm_text(name): Excel lookups by name
    project = Column(String(128))
    department = Column(String(128))
    owner_email = Column(String(255))                                # team lead (Entra UPN); NULL = owner not decided yet
    owner_name = Column(String(128))
    deputy_email = Column(String(255))
    manager_email = Column(String(255))                              # owner's direct manager (escalation, G3)
    cost_center = Column(String(64))
    criticality = Column(Integer, nullable=False, default=3)        # 1 = most critical
    env = Column(String(8), nullable=False, default="prod")
    status = Column(String(8), nullable=False, default="active")
    note = Column(String(500))
    created_at = Column(DateTime, nullable=False, default=utcnow)
    created_by = Column(String(255))
    updated_at = Column(DateTime, nullable=False, default=utcnow)
    updated_by = Column(String(255))


class SystemMember(Base):
    """A VM (CI) assigned to a system. One system per VM; `manual` beats every automatic source."""
    __tablename__ = "system_members"
    __table_args__ = (
        CheckConstraint(_in("source", SOURCES), name="ck_system_member_source"),
        CheckConstraint(_in("confidence", CONFIDENCES), name="ck_system_member_confidence"),
    )

    id = Column(Integer, primary_key=True)
    ci_id = Column(Integer, ForeignKey("ci.id", ondelete="CASCADE"), nullable=False, unique=True)
    system_id = Column(Integer, ForeignKey("systems.id", ondelete="CASCADE"), nullable=False, index=True)
    source = Column(String(12), nullable=False, default="manual")
    confidence = Column(String(8), nullable=False, default="strong")
    assigned_by = Column(String(255))
    assigned_at = Column(DateTime, nullable=False, default=utcnow)


class OwnerRule(Base):
    """Automatic assignment rule (used from G2b): first enabled rule by priority whose pattern matches."""
    __tablename__ = "owner_rules"
    __table_args__ = (CheckConstraint(_in("match_kind", RULE_KINDS), name="ck_owner_rule_kind"),)

    id = Column(Integer, primary_key=True)
    priority = Column(Integer, nullable=False, default=100)
    match_kind = Column(String(16), nullable=False)
    pattern = Column(String(255), nullable=False)
    system_id = Column(Integer, ForeignKey("systems.id", ondelete="CASCADE"), nullable=False, index=True)
    enabled = Column(Boolean, nullable=False, default=True)
