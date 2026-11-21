"""Tables of the configuration baseline (ICO v2 G9a)."""
from sqlalchemy import Boolean, CheckConstraint, Column, Date, DateTime, Index, Integer, String, Text, UniqueConstraint

from ..models import Base, utcnow
from ..shared.schema import in_check as _in

SCOPES = ("host", "cluster", "vm")
LEVELS = ("warn", "bad", "crit")


class CfgFactState(Base):
    """Latest configuration facts of one host / cluster / VM, replaced per vCenter at each sync."""
    __tablename__ = "cfg_fact_state"
    __table_args__ = (CheckConstraint(_in("scope", SCOPES), name="ck_cfg_fact_scope"),
                      Index("ix_cfg_fact_vc", "vcenter_key", "scope"))

    id = Column(Integer, primary_key=True)
    vcenter_key = Column(String(64), nullable=False)
    scope = Column(String(8), nullable=False)
    entity = Column(String(255), nullable=False)
    cluster = Column(String(255))
    facts = Column(Text, nullable=False, default="{}")          # JSON object (vcenter/config_facts.py)
    captured_at = Column(DateTime, nullable=False, default=utcnow)


class CfgRule(Base):
    """Admin override of one built-in rule (baseline/rules.py RULES): on / off, level, parameter. No row = default."""
    __tablename__ = "cfg_rule"
    __table_args__ = (UniqueConstraint("rule", name="uq_cfg_rule"), CheckConstraint(_in("level", LEVELS), name="ck_cfg_rule_level"))

    id = Column(Integer, primary_key=True)
    rule = Column(String(32), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    level = Column(String(4), nullable=False)
    param = Column(String(64))
    updated_by = Column(String(255))
    updated_at = Column(DateTime, nullable=False, default=utcnow)


class CfgException(Base):
    """An accepted deviation: one rule on one host / cluster / VM (or "*" = every entity of the cluster) until a date."""
    __tablename__ = "cfg_exception"
    __table_args__ = (CheckConstraint(_in("scope", SCOPES), name="ck_cfg_exception_scope"),
                      Index("ix_cfg_exception_rule", "rule"))

    id = Column(Integer, primary_key=True)
    rule = Column(String(32), nullable=False)
    scope = Column(String(8), nullable=False)
    vcenter_key = Column(String(64), nullable=False, default="")
    entity = Column(String(255), nullable=False)
    reason = Column(String(500), nullable=False)
    until = Column(Date, nullable=False)
    created_by = Column(String(255))
    created_at = Column(DateTime, nullable=False, default=utcnow)


from ..shared.stored_text import json_column as _json  # noqa: E402

_json(CfgFactState.facts, dict, "Cấu hình đọc từ vCenter", nullable=False)
