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
