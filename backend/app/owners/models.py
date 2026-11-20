"""Tables of Hệ thống & Owner (docs/design/ico-v2-plan.md §4: system, system_member, owner_rule).

Table names are plural like the rest of the app ("system" is an SQL keyword). Invariants in the schema:
code / normalised name UNIQUE, criticality 1–4, env and status in fixed sets, e-mails stored lower case,
a VM (CI) in at most one system (UNIQUE ci_id), assignment source in a fixed set.
"""
from sqlalchemy import Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint

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
        CheckConstraint(_lower("support_emails"), name="ck_system_support_lower"),
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
    deputy_email = Column(String(255))                               # backup (người thay thế) of the owner — operations team
    deputy_name = Column(String(128))
    manager_email = Column(String(255))                              # owner's direct manager (escalation, G3)
    cost_center = Column(String(64))
    # Support = the project team called in when operations (owner / backup) need help; contact only, no portal access.
    support_emails = Column(Text)                                    # CSV, at most 5, lower case
    support_name = Column(String(128))                               # team / person
    support_contact = Column(String(255))                            # phone, Teams channel, ticket queue …
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


PROPOSAL_STATUSES = ("open", "accepted", "dismissed")


class OwnerProposal(Base):
    """A vCenter attribute names a system / owner e-mail that ICO does not know: never auto-created, an owner
    admin accepts (creates the system) or dismisses it. key = "system:<normalised name>" | "owner:<email>"."""
    __tablename__ = "owner_proposals"
    __table_args__ = (
        UniqueConstraint("key", name="uq_owner_proposal_key"),
        CheckConstraint(_in("status", PROPOSAL_STATUSES), name="ck_owner_proposal_status"),
        CheckConstraint(_lower("owner_email"), name="ck_owner_proposal_owner_lower"),
        CheckConstraint("vm_count >= 0", name="ck_owner_proposal_vms"),
    )

    id = Column(Integer, primary_key=True)
    key = Column(String(300), nullable=False)
    system_name = Column(String(255))
    owner_email = Column(String(255))
    vm_count = Column(Integer, nullable=False, default=0)
    sample = Column(Text, nullable=False, default="[]")             # JSON list of up to 10 VM names
    status = Column(String(10), nullable=False, default="open")
    first_seen = Column(DateTime, nullable=False, default=utcnow)
    last_seen = Column(DateTime, nullable=False, default=utcnow)
    decided_by = Column(String(255))
    decided_at = Column(DateTime)
    system_id = Column(Integer, ForeignKey("systems.id", ondelete="SET NULL"), index=True)


from ..shared.stored_text import json_column as _json  # noqa: E402

_json(OwnerProposal.sample, list, "VM của đề xuất", nullable=False)


# ---------------------------------------------------------------- owner reviews (G3)
CAMPAIGN_STATUSES = ("draft", "open", "closed")
DECISIONS = ("keep", "decommission", "resize", "transfer", "not_mine")
NOTICE_KINDS = ("open", "reminder1", "reminder2", "escalation", "closing")
NOTICE_STATUSES = ("preview", "pending", "sending", "sent", "failed")


class ReviewCampaign(Base):
    """One review period ("2027-H1"). UNIQUE period_label: a period is opened exactly once, whoever opens it."""
    __tablename__ = "review_campaigns"
    __table_args__ = (
        UniqueConstraint("period_label", name="uq_review_campaign_period"),
        CheckConstraint(_in("status", CAMPAIGN_STATUSES), name="ck_review_campaign_status"),
        CheckConstraint("closed_at IS NULL OR closed_at >= opened_at", name="ck_review_campaign_dates"),
    )

    id = Column(Integer, primary_key=True)
    period_label = Column(String(16), nullable=False)
    opened_at = Column(DateTime, nullable=False, default=utcnow)
    opened_by = Column(String(255), nullable=False, default="system")
    due_at = Column(Date, nullable=False)
    closed_at = Column(DateTime)
    status = Column(String(8), nullable=False, default="open")
    dry_run = Column(Boolean, nullable=False, default=True)       # True: e-mails are built and kept for preview, never sent
    item_count = Column(Integer, nullable=False, default=0)


class ReviewItem(Base):
    """One VM in a campaign. Names are copied (evidence survives renames); owner_email NULL = VM without owner
    (listed for the infrastructure team, nobody is e-mailed)."""
    __tablename__ = "review_items"
    __table_args__ = (
        UniqueConstraint("campaign_id", "ci_id", name="uq_review_item_vm"),
        CheckConstraint("decision IS NULL OR " + _in("decision", DECISIONS), name="ck_review_item_decision"),
        CheckConstraint(_lower("owner_email"), name="ck_review_item_owner_lower"),
        CheckConstraint(_lower("transfer_to"), name="ck_review_item_transfer_lower"),
        CheckConstraint("new_vcpu IS NULL OR new_vcpu > 0", name="ck_review_item_new_vcpu"),
        CheckConstraint("new_ram_gb IS NULL OR new_ram_gb > 0", name="ck_review_item_new_ram"),
        Index("ix_review_items_owner", "campaign_id", "owner_email"),
    )

    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey("review_campaigns.id", ondelete="CASCADE"), nullable=False)
    ci_id = Column(Integer, ForeignKey("ci.id", ondelete="SET NULL"), index=True)
    vm_name = Column(String(255), nullable=False)
    vcenter_key = Column(String(64), nullable=False, default="")
    system_id = Column(Integer, ForeignKey("systems.id", ondelete="SET NULL"), index=True)
    system_code = Column(String(12))
    system_name = Column(String(255))
    owner_email = Column(String(255))
    signals = Column(Text, nullable=False, default="{}")       # JSON object (owners/review.py SIGNALS)
    flagged = Column(Boolean, nullable=False, default=False)   # "nên rà": one of the cleanup signals fired
    reasons = Column(Text, nullable=False, default="[]")       # JSON list of Vietnamese reasons
    decision = Column(String(16))                              # G3b
    decide_by_date = Column(Date)
    decided_by = Column(String(255))
    decided_at = Column(DateTime)
    note = Column(String(500))
    escalated_at = Column(DateTime)
    verified_at = Column(DateTime)
    # G3b: details of the decision
    action_date = Column(Date)                                 # decommission: planned date
    new_vcpu = Column(Integer)                                 # resize: proposed vCPU / RAM
    new_ram_gb = Column(Integer)
    transfer_to = Column(String(255))                          # transfer: proposed owner e-mail
    resolved_at = Column(DateTime)                             # transfer / not_mine: re-assigned by an admin (G2a)
    resolved_by = Column(String(255))


class ReviewNotice(Base):
    """Outbox of the review e-mails. UNIQUE (campaign, recipient, kind): an e-mail is built once and sent at most once
    (a send is claimed by moving pending/failed → sending in its own transaction before the SMTP call)."""
    __tablename__ = "review_notices"
    __table_args__ = (
        UniqueConstraint("campaign_id", "recipient", "kind", name="uq_review_notice"),
        CheckConstraint(_in("kind", NOTICE_KINDS), name="ck_review_notice_kind"),
        CheckConstraint(_in("status", NOTICE_STATUSES), name="ck_review_notice_status"),
        CheckConstraint("attempts >= 0", name="ck_review_notice_attempts"),
    )

    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey("review_campaigns.id", ondelete="CASCADE"), nullable=False)
    recipient = Column(String(255), nullable=False)
    kind = Column(String(12), nullable=False)
    status = Column(String(8), nullable=False, default="pending")
    subject = Column(String(255), nullable=False)
    body_text = Column(Text, nullable=False)
    body_html = Column(Text, nullable=False)
    vm_count = Column(Integer, nullable=False, default=0)
    attempts = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    claimed_at = Column(DateTime)
    sent_at = Column(DateTime)
    error = Column(String(300))


_json(ReviewItem.signals, dict, "Tín hiệu rà soát", nullable=False)
_json(ReviewItem.reasons, list, "Lý do nên rà", nullable=False)


# CSV kept in a Text column is checked on every ORM write (shared/stored_text.py).
from ..shared.stored_text import csv_column as _csv  # noqa: E402

_csv(System.support_emails, "Email support", item=r"[a-z0-9._%+'-]+@[a-z0-9.-]+\.[a-z]{2,}")
