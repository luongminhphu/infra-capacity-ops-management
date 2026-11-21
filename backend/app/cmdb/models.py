"""Tables of the CI registry (docs/design/ico-v2-plan.md §4).

Invariants in the schema: CHECK on type / status / key kind / relation / confidence, UNIQUE identity
(type, source_kind, source_key), one key value per (kind, value, CI), one link per (from, to, relation).
Rows are owned by cmdb/builder.py: a CI is never deleted, it becomes `retired` when not seen for
CMDB_RETIRE_DAYS; keys and links describe the current state and are replaced at each build.
Timestamps are naive UTC like the rest of the app.
"""
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint

from ..database import Base
from ..shared.schema import in_check as _in
from ..models import utcnow

CI_TYPES = ("vm", "hypervisor_host", "cluster", "physical_server", "datastore", "storage_array", "storage_volume",
            "backup_client", "dc_asset", "opex_item")
CI_STATUSES = ("active", "retired")
SOURCE_KINDS = ("vcenter", "hardware", "storage", "netbackup", "dc_asset", "opex")
KEY_KINDS = ("serial", "hw_uuid", "instance_uuid", "fqdn", "short_name", "ip", "mac", "naa", "nbu_client", "asset_tag",
             "opex_code", "idrac_ip")
RELATIONS = ("hosted_on", "member_of", "runs_on", "records", "covers", "stored_on", "backed_by", "on", "protected_by")
CONFIDENCES = ("strong", "weak")


class Ci(Base):
    """One configuration item. source_key is stable per source (instanceUuid, "<vcenter>:<host>", serial…)."""
    __tablename__ = "ci"
    __table_args__ = (
        UniqueConstraint("type", "source_kind", "source_key", name="uq_ci_identity"),
        CheckConstraint(_in("type", CI_TYPES), name="ck_ci_type"),
        CheckConstraint(_in("status", CI_STATUSES), name="ck_ci_status"),
        CheckConstraint(_in("source_kind", SOURCE_KINDS), name="ck_ci_source_kind"),
        CheckConstraint("last_seen >= first_seen", name="ck_ci_seen"),
        Index("ix_ci_type_status", "type", "status"),
    )

    id = Column(Integer, primary_key=True)
    type = Column(String(24), nullable=False)
    name = Column(String(255), nullable=False)
    source_kind = Column(String(16), nullable=False)
    source_key = Column(String(255), nullable=False)
    status = Column(String(8), nullable=False, default="active")
    first_seen = Column(DateTime, nullable=False, default=utcnow)
    last_seen = Column(DateTime, nullable=False, default=utcnow)
    attrs = Column(Text, nullable=False, default="{}")          # JSON object: small descriptive facts per type


class CiKey(Base):
    """A normalised identifier of a CI (shared/identity.py). The same value may belong to several CIs
    (a serial on the host, its physical server, its DC asset): lookups by (kind, value) use the unique index."""
    __tablename__ = "ci_key"
    __table_args__ = (
        UniqueConstraint("kind", "value_norm", "ci_id", name="uq_ci_key"),
        CheckConstraint(_in("kind", KEY_KINDS), name="ck_ci_key_kind"),
        CheckConstraint("value_norm <> ''", name="ck_ci_key_value"),
    )

    id = Column(Integer, primary_key=True)
    ci_id = Column(Integer, ForeignKey("ci.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(16), nullable=False)
    value_norm = Column(String(255), nullable=False)


class CiLink(Base):
    """Directed relation from_ci → to_ci. strong = shared hard identifier (serial, NAA, inventory parent);
    weak = matched by name / IP (attrs.matchedBy) — never used to group incidents."""
    __tablename__ = "ci_link"
    __table_args__ = (
        UniqueConstraint("from_ci", "to_ci", "relation", name="uq_ci_link"),
        CheckConstraint(_in("relation", RELATIONS), name="ck_ci_link_relation"),
        CheckConstraint(_in("confidence", CONFIDENCES), name="ck_ci_link_confidence"),
        CheckConstraint("from_ci <> to_ci", name="ck_ci_link_self"),
        CheckConstraint("last_seen >= first_seen", name="ck_ci_link_seen"),
    )

    id = Column(Integer, primary_key=True)
    from_ci = Column(Integer, ForeignKey("ci.id", ondelete="CASCADE"), nullable=False)   # leading column of uq_ci_link
    to_ci = Column(Integer, ForeignKey("ci.id", ondelete="CASCADE"), nullable=False, index=True)
    relation = Column(String(16), nullable=False)
    source = Column(String(16), nullable=False)
    confidence = Column(String(8), nullable=False)
    first_seen = Column(DateTime, nullable=False, default=utcnow)
    last_seen = Column(DateTime, nullable=False, default=utcnow)
    attrs = Column(Text, nullable=False, default="{}")          # JSON object, e.g. {"matchedBy": "ip"}


# JSON kept in Text columns is checked on every ORM write (shared/stored_text.py), like spend/models.py.
from ..shared.stored_text import json_column as _json  # noqa: E402

_json(Ci.attrs, dict, "Thuộc tính CI", nullable=False)
_json(CiLink.attrs, dict, "Thuộc tính liên kết CI", nullable=False)


def vc_key(vcenter_key, name) -> str:
    """source_key of a vCenter-scoped CI (host, cluster, datastore): one format for the builder and every lookup."""
    return f"{vcenter_key or ''}:{name}"
