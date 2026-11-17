"""Tables of the "Chi phí & Hợp đồng IT" module (docs/design/spend-module.md §1).

Invariants live in the schema: CHECK constraints for enums / ranges / money, UNIQUE for
business codes and the once-only reminder log, FKs with an explicit ON DELETE.
Money is VND in whole đồng (BigInteger); percentages are Numeric(5,2). Dates of the
business (start / end) are Date; timestamps are naive UTC like the rest of the app.
"""
from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric,
    String, Text, UniqueConstraint,
)

from ..database import Base
from ..models import utcnow

CATEGORY_GROUPS = ("hw_warranty", "license", "dc_maintenance", "outsourced", "other")
BILLING_CYCLES = ("one_time", "monthly", "quarterly", "yearly")
OPEX_STATUSES = ("planned", "active", "renewed", "stopped")
OPEX_ORIGINS = ("manual", "capex", "renewal")
FILE_MIMES = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
CHANNEL_KINDS = ("email", "discord")
REMINDER_STATUSES = ("pending", "sent", "failed", "skipped", "unknown")


def _in(col: str, values) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


class _Audited:
    created_at = Column(DateTime, nullable=False, default=utcnow)
    created_by = Column(String(255), nullable=False)
    updated_at = Column(DateTime, nullable=True)
    updated_by = Column(String(255), nullable=True)


class SpendCategory(Base):
    __tablename__ = "spend_categories"
    __table_args__ = (
        CheckConstraint(_in("grp", CATEGORY_GROUPS), name="ck_spend_category_grp"),
        CheckConstraint("renewal_pct IS NULL OR (renewal_pct >= 0 AND renewal_pct <= 100)", name="ck_spend_category_pct"),
    )

    id = Column(Integer, primary_key=True)
    code = Column(String(32), nullable=False, unique=True)
    name = Column(String(128), nullable=False)
    grp = Column(String(16), nullable=False)
    renewal_pct = Column(Numeric(5, 2), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)


class SpendVendor(Base):
    __tablename__ = "spend_vendors"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    name_norm = Column(String(255), nullable=False, unique=True)
    tax_code = Column(String(32), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    # Vendor profile (added after the first release: columns also listed in database.ensure_schema()).
    short_name = Column(String(64), nullable=True)
    aliases = Column(Text, nullable=True)            # other spellings, one per line ("Thakral One" / "THRAKONE")
    address = Column(String(500), nullable=True)
    contact_name = Column(String(128), nullable=True)
    contact_email = Column(String(255), nullable=True)
    contact_phone = Column(String(64), nullable=True)
    website = Column(String(255), nullable=True)
    tags = Column(String(500), nullable=True)        # lĩnh vực, comma separated
    notes = Column(Text, nullable=True)
    status = Column(String(12), nullable=True, default="active")   # active | paused | blocked
    status_reason = Column(String(500), nullable=True)


class SpendCostCenter(Base):
    __tablename__ = "spend_cost_centers"

    id = Column(Integer, primary_key=True)
    code = Column(String(32), nullable=False, unique=True)
    name = Column(String(128), nullable=False)
    active = Column(Boolean, nullable=False, default=True)


class SpendSequence(Base):
    """Last number used per (kind, year) for OPX / CPX / HD / TT codes."""
    __tablename__ = "spend_sequences"

    kind = Column(String(8), primary_key=True)
    year = Column(Integer, primary_key=True)
    last_no = Column(Integer, nullable=False, default=0)


class CapexProject(_Audited, Base):
    """Created now so Opex can reference it with a real FK; the API arrives in phase 2."""
    __tablename__ = "capex_projects"
    __table_args__ = (
        CheckConstraint(_in("status", ("draft", "approved", "in_progress", "closed")), name="ck_capex_status"),
        CheckConstraint("fiscal_year BETWEEN 2000 AND 2100", name="ck_capex_fy"),
    )

    id = Column(Integer, primary_key=True)
    code = Column(String(20), nullable=False, unique=True)
    fiscal_year = Column(Integer, nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    cost_center_id = Column(Integer, ForeignKey("spend_cost_centers.id", ondelete="RESTRICT"), nullable=True, index=True)
    owner_name = Column(String(128), nullable=True)
    status = Column(String(12), nullable=False, default="draft")
    go_live_date = Column(Date, nullable=True)
    row_version = Column(Integer, nullable=False, default=1)


class CapexItem(Base):
    __tablename__ = "capex_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_capex_item_qty"),
        CheckConstraint("amount_net >= 0 AND amount_gross >= amount_net", name="ck_capex_item_amount"),
        CheckConstraint("vat_pct >= 0 AND vat_pct <= 100", name="ck_capex_item_vat"),
        CheckConstraint("coverage_years BETWEEN 0 AND 10", name="ck_capex_item_years"),
        CheckConstraint("renewal_pct IS NULL OR (renewal_pct >= 0 AND renewal_pct <= 100)", name="ck_capex_item_pct"),
        CheckConstraint("renewal_amount IS NULL OR renewal_amount >= 0", name="ck_capex_item_renewal"),
    )

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("capex_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    category_id = Column(Integer, ForeignKey("spend_categories.id", ondelete="RESTRICT"), nullable=False, index=True)
    description = Column(String(255), nullable=False)
    vendor_id = Column(Integer, ForeignKey("spend_vendors.id", ondelete="RESTRICT"), nullable=True, index=True)
    quantity = Column(Integer, nullable=False, default=1)
    amount_net = Column(BigInteger, nullable=False)
    vat_pct = Column(Numeric(5, 2), nullable=False, default=10)
    amount_gross = Column(BigInteger, nullable=False)
    start_date = Column(Date, nullable=True)
    coverage_years = Column(Integer, nullable=False, default=0)
    renewal_pct = Column(Numeric(5, 2), nullable=True)
    renewal_amount = Column(BigInteger, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, nullable=False, default=utcnow)


class OpexItem(_Audited, Base):
    __tablename__ = "opex_items"
    __table_args__ = (
        CheckConstraint("fiscal_year BETWEEN 2000 AND 2100", name="ck_opex_fy"),
        CheckConstraint("end_date >= start_date", name="ck_opex_dates"),
        CheckConstraint(_in("billing_cycle", BILLING_CYCLES), name="ck_opex_cycle"),
        CheckConstraint("amount_net >= 0 AND amount_gross >= amount_net", name="ck_opex_amount"),
        CheckConstraint("vat_pct >= 0 AND vat_pct <= 100", name="ck_opex_vat"),
        CheckConstraint(_in("status", OPEX_STATUSES), name="ck_opex_status"),
        CheckConstraint(_in("origin", OPEX_ORIGINS), name="ck_opex_origin"),
        CheckConstraint("amount_mode IN ('auto', 'manual')", name="ck_opex_amount_mode"),
        CheckConstraint("origin <> 'capex' OR status <> 'planned' OR confirmed_at IS NULL", name="ck_opex_confirmed"),
        Index("ix_opex_fy_status", "fiscal_year", "status"),
        Index("ix_opex_status_end", "status", "end_date"),
    )

    id = Column(Integer, primary_key=True)
    code = Column(String(20), nullable=False, unique=True)
    fiscal_year = Column(Integer, nullable=False)
    category_id = Column(Integer, ForeignKey("spend_categories.id", ondelete="RESTRICT"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    vendor_id = Column(Integer, ForeignKey("spend_vendors.id", ondelete="RESTRICT"), nullable=False, index=True)
    contract_ref = Column(String(64), nullable=True)
    po_ref = Column(String(64), nullable=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    billing_cycle = Column(String(12), nullable=False)
    amount_net = Column(BigInteger, nullable=False)
    vat_pct = Column(Numeric(5, 2), nullable=False, default=10)
    amount_gross = Column(BigInteger, nullable=False)
    cost_center_id = Column(Integer, ForeignKey("spend_cost_centers.id", ondelete="RESTRICT"), nullable=True, index=True)
    owner_name = Column(String(128), nullable=False)
    owner_email = Column(String(255), nullable=True)
    status = Column(String(12), nullable=False)
    stop_reason = Column(String(500), nullable=True)
    origin = Column(String(8), nullable=False, default="manual")
    capex_project_id = Column(Integer, ForeignKey("capex_projects.id", ondelete="SET NULL"), nullable=True, index=True)
    # NULLs do not collide in a UNIQUE index (PostgreSQL and SQLite): one generated Opex per Capex item.
    capex_item_id = Column(Integer, ForeignKey("capex_items.id", ondelete="SET NULL"), nullable=True, unique=True)
    amount_mode = Column(String(8), nullable=False, default="manual")
    confirmed_at = Column(DateTime, nullable=True)
    confirmed_by = Column(String(255), nullable=True)
    # UNIQUE: an item has at most one successor — renewing twice is refused by the database.
    renewed_from_id = Column(Integer, ForeignKey("opex_items.id", ondelete="SET NULL"), nullable=True, unique=True)
    row_version = Column(Integer, nullable=False, default=1)


class OpexAsset(Base):
    """A device covered by the Opex item. vcenter_key + host_name is a soft link to the
    collected inventory (hosts only exist as snapshots, there is no stable host table)."""
    __tablename__ = "opex_assets"

    id = Column(Integer, primary_key=True)
    opex_id = Column(Integer, ForeignKey("opex_items.id", ondelete="CASCADE"), nullable=False, index=True)
    serial = Column(String(128), nullable=False, index=True)
    description = Column(String(255), nullable=True)
    vcenter_key = Column(String(64), nullable=True)
    host_name = Column(String(255), nullable=True)


class SpendFile(Base):
    """Uploaded file metadata. When the file is purged by policy the row stays (audit),
    `purged_at` records it and downloads answer 410."""
    __tablename__ = "spend_files"
    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="ck_spend_file_size"),
        CheckConstraint(_in("mime", FILE_MIMES), name="ck_spend_file_mime"),
    )

    id = Column(Integer, primary_key=True)
    storage_key = Column(String(64), nullable=False, unique=True)
    sha256 = Column(String(64), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    mime = Column(String(80), nullable=False)
    original_name = Column(String(255), nullable=False)
    uploaded_by = Column(String(255), nullable=False)
    uploaded_at = Column(DateTime, nullable=False, default=utcnow)
    purged_at = Column(DateTime, nullable=True)
    purged_by = Column(String(255), nullable=True)
    purge_reason = Column(String(255), nullable=True)


class OpexAttachment(Base):
    __tablename__ = "opex_attachments"

    opex_id = Column(Integer, ForeignKey("opex_items.id", ondelete="CASCADE"), primary_key=True)
    file_id = Column(Integer, ForeignKey("spend_files.id", ondelete="RESTRICT"), primary_key=True, index=True)


class SpendNotifyChannel(_Audited, Base):
    __tablename__ = "spend_notify_channels"
    __table_args__ = (
        CheckConstraint(_in("kind", CHANNEL_KINDS), name="ck_notify_kind"),
        CheckConstraint("(kind = 'discord' AND webhook_enc IS NOT NULL) OR "
                        "(kind = 'email' AND (recipients IS NOT NULL OR include_owner))", name="ck_notify_target"),
    )

    id = Column(Integer, primary_key=True)
    kind = Column(String(8), nullable=False)
    name = Column(String(128), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    webhook_enc = Column(Text, nullable=True)          # secrets_box; never returned by the API
    recipients = Column(Text, nullable=True)           # comma separated e-mail addresses
    include_owner = Column(Boolean, nullable=False, default=False)
    ops_alerts = Column(Boolean, nullable=True, default=False)   # also receives operations alerts (capacity / backup ≥ 90 %)
    last_test_at = Column(DateTime, nullable=True)
    last_test_ok = Column(Boolean, nullable=True)


class SpendSmtpConfig(Base):
    """Exactly one row (id = 1)."""
    __tablename__ = "spend_smtp_config"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_smtp_single"),
        CheckConstraint("security IN ('starttls', 'ssl', 'none')", name="ck_smtp_security"),
        CheckConstraint("port BETWEEN 1 AND 65535", name="ck_smtp_port"),
    )

    id = Column(Integer, primary_key=True)
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False, default=587)
    security = Column(String(8), nullable=False, default="starttls")
    username = Column(String(255), nullable=True)
    password_enc = Column(Text, nullable=True)
    from_addr = Column(String(255), nullable=False)
    updated_at = Column(DateTime, nullable=False, default=utcnow)
    updated_by = Column(String(255), nullable=False)


class SpendReminderLog(Base):
    """One row per (item, its end date, threshold, channel): the UNIQUE constraint is what
    makes "each threshold is sent once" hold even if the job runs twice."""
    __tablename__ = "spend_reminder_log"
    __table_args__ = (
        UniqueConstraint("opex_id", "end_date", "threshold_days", "channel_id", name="uq_reminder_once"),
        CheckConstraint(_in("status", REMINDER_STATUSES), name="ck_reminder_status"),
        CheckConstraint("threshold_days > 0", name="ck_reminder_threshold"),
        Index("ix_reminder_status", "status", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    opex_id = Column(Integer, ForeignKey("opex_items.id", ondelete="CASCADE"), nullable=False, index=True)
    end_date = Column(Date, nullable=False)
    threshold_days = Column(Integer, nullable=False)
    channel_id = Column(Integer, ForeignKey("spend_notify_channels.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(8), nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(String(500), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    sent_at = Column(DateTime, nullable=True)


class SpendSavedFilter(Base):
    """A budget-report filter saved by a user; `shared` makes it visible to every spend viewer.
    filters_json is genuinely free-form (validated by report.parse_filters when used)."""
    __tablename__ = "spend_saved_filters"
    __table_args__ = (UniqueConstraint("owner_username", "name", name="uq_saved_filter_owner_name"),)

    id = Column(Integer, primary_key=True)
    owner_username = Column(String(255), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    filters_json = Column(Text, nullable=False)
    shared = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)


CONTRACT_STATUSES = ("draft", "negotiating", "final", "signed")
EDITOR_PARTIES = ("legal", "finance", "partner")


class Contract(_Audited, Base):
    """A contract tied to exactly one Opex item or one Capex project (CHECK). Versions form a
    single branch: an upload must name the latest version it was based on (current_version_no,
    optimistic lock) and UNIQUE (contract_id, version_no) on the versions backs it up.
    The final version is referenced by number (no FK, avoiding a circular FK with versions)."""
    __tablename__ = "contracts"
    __table_args__ = (
        CheckConstraint("(opex_id IS NOT NULL AND capex_project_id IS NULL) OR "
                        "(opex_id IS NULL AND capex_project_id IS NOT NULL)", name="ck_contract_one_link"),
        CheckConstraint(_in("status", CONTRACT_STATUSES), name="ck_contract_status"),
        CheckConstraint("(status IN ('final', 'signed')) = (final_version_no IS NOT NULL)", name="ck_contract_final"),
        CheckConstraint("(status = 'signed') = (signed_file_id IS NOT NULL)", name="ck_contract_signed"),
        CheckConstraint("current_version_no >= 0", name="ck_contract_version"),
    )

    id = Column(Integer, primary_key=True)
    code = Column(String(20), nullable=False, unique=True)
    title = Column(String(255), nullable=False)
    vendor_id = Column(Integer, ForeignKey("spend_vendors.id", ondelete="RESTRICT"), nullable=True, index=True)
    opex_id = Column(Integer, ForeignKey("opex_items.id", ondelete="RESTRICT"), nullable=True, index=True)
    capex_project_id = Column(Integer, ForeignKey("capex_projects.id", ondelete="RESTRICT"), nullable=True, index=True)
    status = Column(String(12), nullable=False, default="draft", index=True)   # contracts list filter (SCH-09)
    note = Column(Text, nullable=True)
    current_version_no = Column(Integer, nullable=False, default=0)
    final_version_no = Column(Integer, nullable=True)
    finalized_at = Column(DateTime, nullable=True)
    finalized_by = Column(String(255), nullable=True)
    signed_file_id = Column(Integer, ForeignKey("spend_files.id", ondelete="RESTRICT"), nullable=True, index=True)
    signed_at = Column(DateTime, nullable=True)
    signed_by = Column(String(255), nullable=True)
    purge_due_at = Column(DateTime, nullable=True, index=True)
    purge_cancelled_at = Column(DateTime, nullable=True)
    purge_cancelled_by = Column(String(255), nullable=True)
    purge_done_at = Column(DateTime, nullable=True)


class ContractVersion(Base):
    """Immutable upload (v1, v2 …). Only the purge job touches a row afterwards: it deletes the
    file bytes and clears text_content, keeping the metadata and the hash for the audit."""
    __tablename__ = "contract_versions"
    __table_args__ = (
        UniqueConstraint("contract_id", "version_no", name="uq_contract_version"),
        CheckConstraint(_in("editor_party", EDITOR_PARTIES), name="ck_contract_version_party"),
        CheckConstraint("version_no >= 1", name="ck_contract_version_no"),
    )

    id = Column(Integer, primary_key=True)
    contract_id = Column(Integer, ForeignKey("contracts.id", ondelete="RESTRICT"), nullable=False, index=True)
    version_no = Column(Integer, nullable=False)
    editor_party = Column(String(8), nullable=False)
    file_id = Column(Integer, ForeignKey("spend_files.id", ondelete="RESTRICT"), nullable=False, index=True)
    text_content = Column(Text, nullable=True)        # JSON list of paragraphs, for comparing versions
    note = Column(String(1000), nullable=True)
    uploaded_by = Column(String(255), nullable=False)
    uploaded_at = Column(DateTime, nullable=False, default=utcnow)


PROPOSAL_KINDS = ("A", "B")
PROPOSAL_STATUSES = ("draft", "submitted", "approved", "rejected")
RECIPIENT_ROLES = ("de_xuat", "kiem_soat", "tham_van", "phe_duyet")
AI_PROVIDERS = ("none", "anthropic")


class ProposalTemplate(Base):
    """An uploaded .docx proposal template (Mẫu A: to the CEO, Mẫu B: to Procurement / COO).
    outline_json caches the structure read from the file (sections, their kind) for the UI and the AI."""
    __tablename__ = "proposal_templates"
    __table_args__ = (CheckConstraint(_in("kind", PROPOSAL_KINDS), name="ck_proposal_template_kind"),)

    id = Column(Integer, primary_key=True)
    kind = Column(String(1), nullable=False)
    name = Column(String(128), nullable=False)
    description = Column(String(500), nullable=True)
    file_id = Column(Integer, ForeignKey("spend_files.id", ondelete="RESTRICT"), nullable=False, index=True)
    outline_json = Column(Text, nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    uploaded_by = Column(String(255), nullable=False)
    uploaded_at = Column(DateTime, nullable=False, default=utcnow)


class ProposalRecipient(Base):
    """People of the approval block for each template kind (configured in Settings, never hard-coded)."""
    __tablename__ = "proposal_recipients"
    __table_args__ = (
        CheckConstraint(_in("kind", PROPOSAL_KINDS), name="ck_proposal_recipient_kind"),
        CheckConstraint(_in("role", RECIPIENT_ROLES), name="ck_proposal_recipient_role"),
    )

    id = Column(Integer, primary_key=True)
    kind = Column(String(1), nullable=False, index=True)
    role = Column(String(10), nullable=False)
    salutation = Column(String(16), nullable=True)        # "Anh", "Chị"…
    name = Column(String(128), nullable=False)
    title = Column(String(255), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)


class Proposal(_Audited, Base):
    """A tờ trình tied to one Capex project or to one or more Opex items (proposal_opex);
    the "exactly one of the two" rule spans tables and is enforced by the service."""
    __tablename__ = "proposals"
    __table_args__ = (
        CheckConstraint(_in("kind", PROPOSAL_KINDS), name="ck_proposal_kind"),
        CheckConstraint(_in("status", PROPOSAL_STATUSES), name="ck_proposal_status"),
    )

    id = Column(Integer, primary_key=True)
    code = Column(String(20), nullable=False, unique=True)
    kind = Column(String(1), nullable=False)
    template_id = Column(Integer, ForeignKey("proposal_templates.id", ondelete="RESTRICT"), nullable=False, index=True)
    subject = Column(String(255), nullable=False)
    department = Column(String(128), nullable=True)
    context_note = Column(Text, nullable=True)            # background written by the user, sent to the AI
    status = Column(String(10), nullable=False, default="draft")
    capex_project_id = Column(Integer, ForeignKey("capex_projects.id", ondelete="RESTRICT"), nullable=True, index=True)
    quote_request_id = Column(Integer, ForeignKey("quote_requests.id", ondelete="RESTRICT"), nullable=True, index=True)
    file_id = Column(Integer, ForeignKey("spend_files.id", ondelete="RESTRICT"), nullable=True, index=True)
    ai_provider_used = Column(String(16), nullable=True)
    ai_model = Column(String(64), nullable=True)
    generated_at = Column(DateTime, nullable=True)
    generated_by = Column(String(255), nullable=True)


class ProposalOpex(Base):
    __tablename__ = "proposal_opex"

    proposal_id = Column(Integer, ForeignKey("proposals.id", ondelete="CASCADE"), primary_key=True)
    opex_id = Column(Integer, ForeignKey("opex_items.id", ondelete="RESTRICT"), primary_key=True, index=True)


class ProposalContract(Base):
    """Contract <-> proposal link (separate table: no FK column can be added to contracts later)."""
    __tablename__ = "proposal_contracts"

    proposal_id = Column(Integer, ForeignKey("proposals.id", ondelete="CASCADE"), primary_key=True)
    contract_id = Column(Integer, ForeignKey("contracts.id", ondelete="CASCADE"), primary_key=True, index=True)


class SpendAiConfig(Base):
    """Exactly one row. Nothing leaves the network unless provider != 'none' AND allow_external."""
    __tablename__ = "spend_ai_config"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_ai_single"),
        CheckConstraint(_in("provider", AI_PROVIDERS), name="ck_ai_provider"),
        CheckConstraint("effort IN ('low', 'medium', 'high')", name="ck_ai_effort"),
    )

    id = Column(Integer, primary_key=True)
    provider = Column(String(16), nullable=False, default="none")
    model = Column(String(64), nullable=False, default="claude-opus-5-5")
    effort = Column(String(8), nullable=False, default="medium")
    api_key_enc = Column(Text, nullable=True)
    allow_external = Column(Boolean, nullable=False, default=False)
    # Platform settings (super app SA4) — the table is the app-wide AI configuration.
    tasks_json = Column(Text, nullable=True)                    # {"home_brief": true, …}; missing = default
    mask_names = Column(Boolean, nullable=True, default=True)   # hostname / IP / VM names masked before sending
    budget_usd = Column(Integer, nullable=True, default=100)    # monthly hard limit
    alert_usd = Column(Integer, nullable=True, default=50)      # admin alert threshold
    alert_month = Column(String(7), nullable=True)              # "2026-11": alert already sent this month
    rate_per_hour = Column(Integer, nullable=True, default=30)  # calls per user per hour
    updated_at = Column(DateTime, nullable=False, default=utcnow)
    updated_by = Column(String(255), nullable=False)


PAYMENT_STATUSES = ("pending", "submitted", "paid")


class ContractPayment(_Audited, Base):
    """One payment instalment of a contract (đợt thanh toán). Status: pending (collecting the
    documents) -> submitted (documents complete, handed to accounting) -> paid."""
    __tablename__ = "contract_payments"
    __table_args__ = (
        UniqueConstraint("contract_id", "seq", name="uq_contract_payment_seq"),
        CheckConstraint("seq >= 1", name="ck_payment_seq"),
        CheckConstraint("amount >= 0", name="ck_payment_amount"),
        CheckConstraint("percent IS NULL OR (percent >= 0 AND percent <= 100)", name="ck_payment_percent"),
        CheckConstraint(_in("status", PAYMENT_STATUSES), name="ck_payment_status"),
        CheckConstraint("(status = 'paid') = (paid_at IS NOT NULL)", name="ck_payment_paid"),
        CheckConstraint("paid_amount IS NULL OR paid_amount >= 0", name="ck_payment_paid_amount"),
        Index("ix_contract_payment_status_due", "status", "due_date"),     # agenda / payments list (SCH-09)
    )

    id = Column(Integer, primary_key=True)
    contract_id = Column(Integer, ForeignKey("contracts.id", ondelete="RESTRICT"), nullable=False, index=True)
    seq = Column(Integer, nullable=False)
    title = Column(String(255), nullable=False)
    amount = Column(BigInteger, nullable=False)                 # VND, after VAT
    percent = Column(Numeric(5, 2), nullable=True)              # % of the contract value, informative
    due_date = Column(Date, nullable=True)
    condition = Column(String(500), nullable=True)              # "Sau khi nghiệm thu", "Khi ký hợp đồng"…
    status = Column(String(10), nullable=False, default="pending")
    submitted_at = Column(DateTime, nullable=True)
    submitted_by = Column(String(255), nullable=True)
    paid_at = Column(Date, nullable=True)
    paid_by = Column(String(255), nullable=True)
    paid_amount = Column(BigInteger, nullable=True)
    payment_ref = Column(String(64), nullable=True)             # số chứng từ / UNC
    note = Column(Text, nullable=True)
    row_version = Column(Integer, nullable=False, default=1)


class PaymentDocument(Base):
    """One line of the document checklist of an instalment (hóa đơn, biên bản nghiệm thu…)."""
    __tablename__ = "payment_documents"
    __table_args__ = (
        CheckConstraint("(done AND done_at IS NOT NULL) OR (NOT done AND done_at IS NULL)", name="ck_payment_doc_done"),
    )

    id = Column(Integer, primary_key=True)
    payment_id = Column(Integer, ForeignKey("contract_payments.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    required = Column(Boolean, nullable=False, default=True)
    done = Column(Boolean, nullable=False, default=False)
    done_at = Column(DateTime, nullable=True)
    done_by = Column(String(255), nullable=True)
    note = Column(String(500), nullable=True)
    file_id = Column(Integer, ForeignKey("spend_files.id", ondelete="RESTRICT"), nullable=True, index=True)
    sort_order = Column(Integer, nullable=False, default=0)


class PaymentDocDefault(Base):
    """Document checklist copied into every new instalment (Settings → Chi phí & nhắc hạn)."""
    __tablename__ = "spend_payment_doc_defaults"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False, unique=True)
    required = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)


QUOTE_STATUSES = ("draft", "comparing", "selected", "closed")
WORKSTREAMS = ("cabling", "network", "vc", "other")
QUOTATION_STATES = ("pending", "extracted", "verified", "failed")
LINE_TYPES = ("item", "discount", "subtotal", "vat", "total", "note")


class QuoteRequest(_Audited, Base):
    """Gói so sánh báo giá (BG-<FY>-<số>): several vendor quotations for the same scope."""
    __tablename__ = "quote_requests"
    __table_args__ = (
        CheckConstraint(_in("status", QUOTE_STATUSES), name="ck_quote_status"),
        CheckConstraint(_in("workstream", WORKSTREAMS), name="ck_quote_workstream"),
        CheckConstraint("min_quotes BETWEEN 1 AND 10", name="ck_quote_min"),
        CheckConstraint("(status IN ('selected', 'closed')) = (selected_quotation_id IS NOT NULL)", name="ck_quote_selected"),
        CheckConstraint("estimate_amount IS NULL OR estimate_amount >= 0", name="ck_quote_estimate"),
    )

    id = Column(Integer, primary_key=True)
    code = Column(String(20), nullable=False, unique=True)
    title = Column(String(255), nullable=False)
    workstream = Column(String(10), nullable=False, default="other")
    description = Column(Text, nullable=True)
    capex_project_id = Column(Integer, ForeignKey("capex_projects.id", ondelete="RESTRICT"), nullable=True, index=True)
    renovation_project_id = Column(Integer, ForeignKey("renovation_projects.id", ondelete="RESTRICT"), nullable=True, index=True)
    estimate_amount = Column(BigInteger, nullable=True)          # dự toán (before VAT), compared with the chosen quote
    min_quotes = Column(Integer, nullable=False, default=3)
    status = Column(String(10), nullable=False, default="draft")
    # Selected quotation: plain id (no FK) to avoid a circular FK with quotations; checked by the service.
    selected_quotation_id = Column(Integer, nullable=True)
    selection_reason = Column(Text, nullable=True)
    selected_at = Column(DateTime, nullable=True)
    selected_by = Column(String(255), nullable=True)
    # Phiếu đánh giá NCC (EVALUATION FORM header)
    eval_department = Column(String(64), nullable=True)
    eval_date = Column(Date, nullable=True)
    eval_service = Column(String(255), nullable=True)
    eval_conclusion = Column(String(255), nullable=True)
    eval_evaluator = Column(String(128), nullable=True)
    eval_approver = Column(String(128), nullable=True)
    # AI summary of content / configuration differences between the quotations (JSON), V3
    ai_summary = Column(Text, nullable=True)
    ai_summary_at = Column(DateTime, nullable=True)
    ai_summary_by = Column(String(255), nullable=True)


class Quotation(_Audited, Base):
    """One vendor's quotation inside a request; stated totals as printed, computed totals from the lines."""
    __tablename__ = "quotations"
    __table_args__ = (
        UniqueConstraint("request_id", "vendor_id", name="uq_quotation_vendor"),
        CheckConstraint(_in("state", QUOTATION_STATES), name="ck_quotation_state"),
        CheckConstraint("(state = 'verified') = (verified_at IS NOT NULL)", name="ck_quotation_verified"),
        CheckConstraint("fx_rate IS NULL OR fx_rate > 0", name="ck_quotation_fx"),
    )

    id = Column(Integer, primary_key=True)
    request_id = Column(Integer, ForeignKey("quote_requests.id", ondelete="RESTRICT"), nullable=False, index=True)
    vendor_id = Column(Integer, ForeignKey("spend_vendors.id", ondelete="RESTRICT"), nullable=False, index=True)
    file_id = Column(Integer, ForeignKey("spend_files.id", ondelete="RESTRICT"), nullable=True, index=True)
    quote_no = Column(String(64), nullable=True)
    quote_date = Column(Date, nullable=True)
    valid_until = Column(Date, nullable=True)
    currency = Column(String(3), nullable=False, default="VND")
    fx_rate = Column(Numeric(14, 4), nullable=True)               # VND per unit of currency
    vat_pct = Column(Numeric(5, 2), nullable=True)
    stated_subtotal = Column(BigInteger, nullable=True)
    stated_discount = Column(BigInteger, nullable=True)
    stated_vat = Column(BigInteger, nullable=True)
    stated_total = Column(BigInteger, nullable=True)
    delivery_terms = Column(String(500), nullable=True)
    warranty_terms = Column(String(500), nullable=True)
    payment_terms = Column(String(500), nullable=True)
    notes = Column(Text, nullable=True)
    extraction_method = Column(String(8), nullable=True)          # excel | docx | ai | manual
    state = Column(String(10), nullable=False, default="pending")
    extraction_message = Column(String(500), nullable=True)
    verified_at = Column(DateTime, nullable=True)
    verified_by = Column(String(255), nullable=True)
    row_version = Column(Integer, nullable=False, default=1)


class QuotationLine(Base):
    __tablename__ = "quotation_lines"
    __table_args__ = (CheckConstraint(_in("line_type", LINE_TYPES), name="ck_quotation_line_type"),)

    id = Column(Integer, primary_key=True)
    quotation_id = Column(Integer, ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False, index=True)
    seq = Column(Integer, nullable=False)
    line_type = Column(String(8), nullable=False, default="item")
    description = Column(Text, nullable=False)
    model = Column(String(255), nullable=True)
    unit = Column(String(32), nullable=True)
    qty = Column(Numeric(16, 3), nullable=True)
    unit_price = Column(Numeric(18, 2), nullable=True)            # in the quotation currency
    amount = Column(Numeric(18, 2), nullable=True)                # "thành tiền" as printed
    # Per-line VAT as printed: rate (0 for "KCT" / "-") and tax amount; NULL = the quotation's rate applies.
    vat_pct = Column(Numeric(5, 2), nullable=True)
    vat_amount = Column(Numeric(18, 2), nullable=True)
    # Multiplier when the line prices ONE set of a bundle quoted N times ("Phòng họp × 2" with a per-room
    # breakdown): the line counts amount × sets. NULL = 1.
    sets = Column(Numeric(10, 3), nullable=True)
    match_key = Column(String(255), nullable=True)                # manual alignment across vendors
    cost_group_id = Column(Integer, ForeignKey("renovation_cost_groups.id", ondelete="SET NULL"), nullable=True, index=True)


class QuotationFinding(Base):
    """Result of the formula / completeness checks (regenerated at each check)."""
    __tablename__ = "quotation_findings"

    id = Column(Integer, primary_key=True)
    quotation_id = Column(Integer, ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False, index=True)
    line_id = Column(Integer, nullable=True)
    code = Column(String(12), nullable=False)
    severity = Column(String(8), nullable=False)                  # crit | warn | info
    expected = Column(Numeric(20, 2), nullable=True)
    actual = Column(Numeric(20, 2), nullable=True)
    message = Column(String(500), nullable=False)


class EvaluationCriterion(Base):
    """Criteria of the EVALUATION FORM of one request (A: standard 70%, B: extended 30%, 2–4 lines)."""
    __tablename__ = "evaluation_criteria"
    __table_args__ = (
        CheckConstraint("section IN ('A', 'B')", name="ck_eval_section"),
        CheckConstraint("weight > 0 AND weight <= 1", name="ck_eval_weight"),
    )

    id = Column(Integer, primary_key=True)
    request_id = Column(Integer, ForeignKey("quote_requests.id", ondelete="CASCADE"), nullable=False, index=True)
    section = Column(String(1), nullable=False)
    seq = Column(Integer, nullable=False)
    name = Column(String(255), nullable=False)
    weight = Column(Numeric(5, 4), nullable=False)


class EvaluationScore(Base):
    __tablename__ = "evaluation_scores"
    __table_args__ = (
        UniqueConstraint("criterion_id", "quotation_id", name="uq_eval_score"),
        CheckConstraint("score >= 1 AND score <= 10", name="ck_eval_score"),
    )

    id = Column(Integer, primary_key=True)
    criterion_id = Column(Integer, ForeignKey("evaluation_criteria.id", ondelete="CASCADE"), nullable=False, index=True)
    quotation_id = Column(Integer, ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False, index=True)
    score = Column(Numeric(4, 2), nullable=False)
    scored_by = Column(String(255), nullable=False)
    scored_at = Column(DateTime, nullable=False, default=utcnow)


# ---------------------------------------------------------------- office renovation (V2)
RENOVATION_STATUSES = ("planning", "executing", "closed")
RENOVATION_WORKSTREAMS = ("cabling", "network", "vc")
COST_KINDS = ("estimate", "awarded", "actual")       # dự toán | báo giá trúng | thực chi
COST_SOURCES = ("manual", "import", "quote")


class RenovationProject(_Audited, Base):
    """Dự án cải tạo văn phòng (CTVP-<năm>-<số>): IT part of an office floor renovation, with its scale
    (nodes, headcount, meeting rooms) so costs of different floors compare per node / per room."""
    __tablename__ = "renovation_projects"
    __table_args__ = (
        CheckConstraint(_in("status", RENOVATION_STATUSES), name="ck_renov_status"),
        CheckConstraint("year BETWEEN 2000 AND 2100", name="ck_renov_year"),
        CheckConstraint("(nodes IS NULL OR nodes >= 0) AND (headcount IS NULL OR headcount >= 0) AND "
                        "(hub_rooms IS NULL OR hub_rooms >= 0) AND (meeting_rooms IS NULL OR meeting_rooms >= 0) AND "
                        "(vc_rooms IS NULL OR vc_rooms >= 0) AND (area_m2 IS NULL OR area_m2 >= 0) AND "
                        "(access_points IS NULL OR access_points >= 0)", name="ck_renov_scale"),
        UniqueConstraint("floor", "year", name="uq_renov_floor_year"),
    )

    id = Column(Integer, primary_key=True)
    code = Column(String(20), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    floor = Column(String(32), nullable=False)                    # FL6, FL12…
    year = Column(Integer, nullable=False)
    status = Column(String(10), nullable=False, default="planning")
    is_historical = Column(Boolean, nullable=False, default=False)
    nodes = Column(Integer, nullable=True)
    headcount = Column(Integer, nullable=True)
    area_m2 = Column(Integer, nullable=True)
    hub_rooms = Column(Integer, nullable=True)
    meeting_rooms = Column(Integer, nullable=True)
    vc_rooms = Column(Integer, nullable=True)
    access_points = Column(Integer, nullable=True)
    config_tags = Column(String(255), nullable=True)               # "10G, Wifi7" — comma separated
    capex_project_id = Column(Integer, ForeignKey("capex_projects.id", ondelete="RESTRICT"), nullable=True, index=True)
    notes = Column(Text, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    closed_by = Column(String(255), nullable=True)
    row_version = Column(Integer, nullable=False, default=1)


class RenovationCostGroup(Base):
    """Nhóm chi phí chuẩn of a workstream (CABLING MATERIALS, Switch…) with aliases used to map lines."""
    __tablename__ = "renovation_cost_groups"
    __table_args__ = (
        UniqueConstraint("workstream", "name", name="uq_renov_group"),
        CheckConstraint(_in("workstream", RENOVATION_WORKSTREAMS), name="ck_renov_group_ws"),
    )

    id = Column(Integer, primary_key=True)
    workstream = Column(String(10), nullable=False)
    name = Column(String(255), nullable=False)
    aliases = Column(Text, nullable=True)                         # one per line
    seq = Column(Integer, nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)


class RenovationCost(Base):
    """Amount of one cost group of one project, per kind (dự toán / báo giá trúng / thực chi)."""
    __tablename__ = "renovation_costs"
    __table_args__ = (
        UniqueConstraint("project_id", "group_id", "kind", name="uq_renov_cost"),
        CheckConstraint(_in("kind", COST_KINDS), name="ck_renov_cost_kind"),
        CheckConstraint(_in("source", COST_SOURCES), name="ck_renov_cost_source"),
        CheckConstraint("amount >= 0", name="ck_renov_cost_amount"),
    )

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("renovation_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    group_id = Column(Integer, ForeignKey("renovation_cost_groups.id", ondelete="RESTRICT"), nullable=False, index=True)
    kind = Column(String(8), nullable=False)
    amount = Column(BigInteger, nullable=False)
    source = Column(String(8), nullable=False, default="manual")
    note = Column(String(500), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=utcnow)
    updated_by = Column(String(255), nullable=False)


# ---------------------------------------------------------------- Kế hoạch ngân sách năm sau (docs/design/spend-budget-plan.md)
PLAN_STATUSES = ("draft", "submitted", "approved", "superseded")
PLAN_LINE_SOURCES = ("renewal", "committed", "capex", "new")
PLAN_PRIORITIES = ("must", "should", "could")


class BudgetPlan(_Audited, Base):
    """The IT budget plan of one fiscal year, version by version (v1, v2 … after a revision).
    At most one approved version per year: approving a new one supersedes the previous one."""
    __tablename__ = "budget_plans"
    __table_args__ = (
        UniqueConstraint("fiscal_year", "version", name="uq_budget_plan_version"),
        CheckConstraint(_in("status", PLAN_STATUSES), name="ck_budget_plan_status"),
        CheckConstraint("fiscal_year BETWEEN 2000 AND 2100", name="ck_budget_plan_fy"),
        CheckConstraint("uplift_pct >= 0 AND uplift_pct <= 100", name="ck_budget_plan_uplift"),
        CheckConstraint("budget_cap IS NULL OR budget_cap >= 0", name="ck_budget_plan_cap"),
    )

    id = Column(Integer, primary_key=True)
    fiscal_year = Column(Integer, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    name = Column(String(255), nullable=False)
    status = Column(String(12), nullable=False, default="draft")
    uplift_pct = Column(Numeric(5, 2), nullable=False, default=5)      # default price increase of renewals
    budget_cap = Column(BigInteger, nullable=True)                     # ceiling after VAT; None = no ceiling
    notes = Column(Text, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    submitted_by = Column(String(255), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(String(255), nullable=True)
    revised_from_id = Column(Integer, ForeignKey("budget_plans.id", ondelete="SET NULL"), nullable=True, index=True)
    row_version = Column(Integer, nullable=False, default=1)


class BudgetPlanLine(Base):
    """One line of a plan. `source` + `source_id` tie it to what suggested it (an Opex to renew, an
    Opex already booked in the year, a Capex project of the year); `new` lines are typed by hand.
    amount_mode 'auto' follows base × (1 + uplift); 'manual' keeps the amount typed by the user."""
    __tablename__ = "budget_plan_lines"
    __table_args__ = (
        CheckConstraint(_in("source", PLAN_LINE_SOURCES), name="ck_bpl_source"),
        CheckConstraint(_in("kind", ("opex", "capex")), name="ck_bpl_kind"),
        CheckConstraint(_in("priority", PLAN_PRIORITIES), name="ck_bpl_priority"),
        CheckConstraint("amount_mode IN ('auto', 'manual')", name="ck_bpl_amount_mode"),
        CheckConstraint("amount_net >= 0 AND amount_gross >= amount_net AND base_net >= 0", name="ck_bpl_amount"),
        CheckConstraint("vat_pct >= 0 AND vat_pct <= 100", name="ck_bpl_vat"),
        CheckConstraint("uplift_pct IS NULL OR (uplift_pct >= 0 AND uplift_pct <= 100)", name="ck_bpl_uplift"),
        CheckConstraint("terms >= 1 AND terms <= 366", name="ck_bpl_terms"),
        UniqueConstraint("plan_id", "source", "source_id", name="uq_bpl_source"),
        Index("ix_bpl_plan", "plan_id", "sort_order"),
    )

    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("budget_plans.id", ondelete="CASCADE"), nullable=False)
    source = Column(String(10), nullable=False)
    source_id = Column(Integer, nullable=True)          # opex_items.id or capex_projects.id; NULL for new lines
    source_code = Column(String(20), nullable=True)     # OPX-… / CPX-… shown to the user
    kind = Column(String(5), nullable=False)
    category_id = Column(Integer, ForeignKey("spend_categories.id", ondelete="RESTRICT"), nullable=False, index=True)
    vendor_id = Column(Integer, ForeignKey("spend_vendors.id", ondelete="SET NULL"), nullable=True, index=True)
    cost_center_id = Column(Integer, ForeignKey("spend_cost_centers.id", ondelete="SET NULL"), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    start_date = Column(Date, nullable=True)            # first payment / start in the year -> quarter
    terms = Column(Integer, nullable=False, default=1)  # renewals starting inside the year
    base_net = Column(BigInteger, nullable=False, default=0)   # today's cost (before uplift), before VAT
    uplift_pct = Column(Numeric(5, 2), nullable=True)   # None = the plan's default (renewals only)
    amount_mode = Column(String(8), nullable=False, default="auto")
    amount_net = Column(BigInteger, nullable=False, default=0)
    vat_pct = Column(Numeric(5, 2), nullable=False, default=10)
    amount_gross = Column(BigInteger, nullable=False, default=0)
    priority = Column(String(8), nullable=False, default="must")
    included = Column(Boolean, nullable=False, default=True)
    note = Column(String(1000), nullable=True)          # justification, or why the line is left out
    stale = Column(Boolean, nullable=False, default=False)     # its source no longer suggests it (after a refresh)
    applied_code = Column(String(20), nullable=True)    # OPX / CPX created from a `new` line once the plan is approved
    sort_order = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, nullable=False, default=utcnow)
    updated_by = Column(String(255), nullable=True)


# SCH-10: JSON / CSV kept in Text columns are checked on every ORM write (type stays Text, rule 4).
from ..shared.stored_text import csv_column as _csv, json_column as _json  # noqa: E402

_csv(SpendNotifyChannel.recipients, "Email người nhận", item=r"[^@\s,]+@[^@\s,]+")
_json(SpendSavedFilter.filters_json, dict, "Bộ lọc đã lưu", nullable=False)
_json(ContractVersion.text_content, list, "Nội dung văn bản hợp đồng")
_json(ProposalTemplate.outline_json, dict, "Cấu trúc mẫu tờ trình", nullable=False)
_json(SpendAiConfig.tasks_json, dict, "Danh sách tác vụ AI")
