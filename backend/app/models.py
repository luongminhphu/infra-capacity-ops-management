import datetime as dt

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, Date, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
)
from sqlalchemy.orm import relationship

from .database import Base


def utcnow():
    return dt.datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(255), unique=True, nullable=False, index=True)   # UPN can exceed 64 chars
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    # Sign-in source: "local" (password) or "entra" (Microsoft Entra ID, no usable password).
    auth_source = Column(String(16), nullable=False, default="local")
    external_id = Column(String(64), nullable=True, index=True)   # Entra object id (oid)
    email = Column(String(255), nullable=True)
    last_login_at = Column(DateTime, nullable=True)
    # Entra users: refreshed from the app roles in the ID token at every sign-in (ENTRA_ADMIN_ROLES).
    # Local users: decided by ADMIN_USERS instead, this column is ignored.
    is_admin = Column(Boolean, nullable=False, default=False)
    # Module Chi phí & Hợp đồng: none | viewer | editor | approver (admins have every right).
    # Source "entra" = granted by an app role (refreshed at each sign-in), "ui" = set by an admin.
    spend_role = Column(String(16), nullable=False, default="none")
    spend_role_source = Column(String(8), nullable=True)
    # Bumped to sign the account out everywhere (passkey revoked / deleted): sessions carry it as "sv".
    session_version = Column(Integer, nullable=False, default=0)


class ApiToken(Base):
    """Personal bearer token of an admin for the MCP endpoint. Only the SHA-256 is stored."""
    __tablename__ = "api_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    prefix = Column(String(16), nullable=False)          # first characters, to recognise the token
    scope = Column(String(32), nullable=False, default="mcp")
    created_at = Column(DateTime, nullable=False, default=utcnow)
    expires_at = Column(DateTime, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)

    user = relationship("User")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    at = Column(DateTime, nullable=False, default=utcnow, index=True)
    username = Column(String(255), nullable=True)
    action = Column(String(64), nullable=False, index=True)    # mcp.tool, mcp.token.create, ...
    detail = Column(Text, nullable=True)                       # JSON
    client_ip = Column(String(64), nullable=True)


class AppLog(Base):
    """WARNING and above from the app's own loggers (secrets redacted before storing)."""
    __tablename__ = "app_logs"

    id = Column(Integer, primary_key=True)
    at = Column(DateTime, nullable=False, index=True)
    level = Column(String(10), nullable=False, index=True)
    logger = Column(String(128), nullable=False)
    message = Column(Text, nullable=False)
    exc_type = Column(String(128), nullable=True)
    traceback = Column(Text, nullable=True)
    fingerprint = Column(String(40), nullable=False, index=True)   # groups repeats of the same error
    app_version = Column(String(64), nullable=True)


class PerfBucket(Base):
    """Request timings aggregated per 5 minutes and route template."""
    __tablename__ = "perf_buckets"
    __table_args__ = (UniqueConstraint("bucket_start", "method", "route", name="uq_perf_bucket"),)

    id = Column(Integer, primary_key=True)
    bucket_start = Column(DateTime, nullable=False, index=True)
    method = Column(String(8), nullable=False)
    route = Column(String(255), nullable=False)
    count = Column(Integer, nullable=False, default=0)
    errors_5xx = Column(Integer, nullable=False, default=0)
    errors_4xx = Column(Integer, nullable=False, default=0)
    total_ms = Column(Float, nullable=False, default=0)
    max_ms = Column(Float, nullable=False, default=0)
    db_ms = Column(Float, nullable=False, default=0)
    db_queries = Column(Integer, nullable=False, default=0)
    histogram = Column(Text, nullable=False)          # JSON list of counts per latency bucket
    app_version = Column(String(64), nullable=True)


class SnapshotRun(Base):
    """One collection event: either a live vCenter query or a manual file import."""
    __tablename__ = "snapshot_runs"

    id = Column(Integer, primary_key=True)
    vcenter_key = Column(String(64), nullable=True, index=True)  # null when source == 'import'
    source = Column(String(16), nullable=False, default="live")  # 'live' | 'import'
    status = Column(String(16), nullable=False, default="ok")  # 'ok' | 'error'
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, default=utcnow)
    finished_at = Column(DateTime, nullable=True)

    hosts = relationship("HostSnapshot", back_populates="run", cascade="all, delete-orphan")
    vms = relationship("VmSnapshot", back_populates="run", cascade="all, delete-orphan")
    clusters = relationship("ClusterAggregate", back_populates="run", cascade="all, delete-orphan")
    findings = relationship("ComplianceFindingSnapshot", back_populates="run", cascade="all, delete-orphan")


class HostSnapshot(Base):
    __tablename__ = "host_snapshots"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("snapshot_runs.id"), nullable=False, index=True)
    vcenter_key = Column(String(64), nullable=True, index=True)
    cluster = Column(String(128), nullable=False, default="UNKNOWN")
    host = Column(String(255), nullable=False)
    cores = Column(Float, default=0)
    cpu_mhz = Column(Float, default=0)
    ram_gb = Column(Float, default=0)
    cpu_usage_pct_raw = Column(Float, nullable=True)
    ram_usage_pct_raw = Column(Float, nullable=True)
    status = Column(String(64), default="Connected")
    # --- Inventory & License Assessment (hardware facts, read-only from vCenter) ---
    vendor = Column(String(128), nullable=True)
    model = Column(String(128), nullable=True)
    esxi_version = Column(String(64), nullable=True)
    esxi_build = Column(String(64), nullable=True)
    cpu_model = Column(String(255), nullable=True)
    cpu_sockets = Column(Integer, nullable=True)      # physical processors (numCpuPkgs)
    cpu_threads = Column(Integer, nullable=True)      # logical threads (numCpuThreads) — NOT licensable cores
    cluster_ha = Column(Boolean, nullable=True)       # vSphere HA enabled on the parent cluster
    cluster_drs = Column(Boolean, nullable=True)      # DRS enabled on the parent cluster
    captured_at = Column(DateTime, default=utcnow, index=True)

    run = relationship("SnapshotRun", back_populates="hosts")


class VmSnapshot(Base):
    __tablename__ = "vm_snapshots"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("snapshot_runs.id"), nullable=False, index=True)
    vcenter_key = Column(String(64), nullable=True, index=True)
    cluster = Column(String(128), nullable=False, default="UNKNOWN")
    host = Column(String(255), nullable=True)
    vm = Column(String(255), nullable=False)
    # vCenter instanceUuid: stable across rename / vMotion / cross-cluster
    # migration, so history can tell a moved VM from a deleted + created one.
    instance_uuid = Column(String(64), nullable=True, index=True)
    power_state = Column(String(32), default="Powered On")
    is_powered_on = Column(Boolean, default=True)
    ip_address = Column(String(64), nullable=True)
    vcpu = Column(Float, default=0)
    ram_gb = Column(Float, default=0)
    disk_prov_gb = Column(Float, default=0)
    disk_used_gb = Column(Float, default=0)
    datastore = Column(String(255), default="UNKNOWN")
    cpu_usage_pct_raw = Column(Float, nullable=True)
    ram_usage_pct_raw = Column(Float, nullable=True)
    last_activity_days = Column(Float, nullable=True)
    power_off_days = Column(Float, nullable=True)
    # --- Guest OS evidence for OS classification (kept raw; classification is derived) ---
    guest_id = Column(String(64), nullable=True)            # config.guestId (configured OS type)
    guest_full_name = Column(String(255), nullable=True)    # config.guestFullName (configured)
    tools_guest_full_name = Column(String(255), nullable=True)  # guest.guestFullName (reported by VMware Tools)
    tools_guest_family = Column(String(64), nullable=True)  # guest.guestFamily (windowsGuest / linuxGuest / otherGuestFamily)
    tools_status = Column(String(32), nullable=True)        # guest.toolsRunningStatus
    guest_hostname = Column(String(255), nullable=True)
    captured_at = Column(DateTime, default=utcnow, index=True)

    run = relationship("SnapshotRun", back_populates="vms")


class ClusterAggregate(Base):
    """Precomputed per-cluster capacity model for fast trend queries (Executive view)."""
    __tablename__ = "cluster_aggregates"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("snapshot_runs.id"), nullable=False, index=True)
    vcenter_key = Column(String(64), nullable=True, index=True)
    cluster = Column(String(128), nullable=False)
    host_count = Column(Integer, default=0)
    vm_count = Column(Integer, default=0)
    total_cores = Column(Float, default=0)
    total_logical_cores = Column(Float, default=0)
    total_ram_gb = Column(Float, default=0)
    used_vcpu = Column(Float, default=0)
    used_ram_gb = Column(Float, default=0)
    prov_storage_gb = Column(Float, default=0)
    used_storage_gb = Column(Float, default=0)
    cpu_overcommit = Column(Float, nullable=True)
    ram_overcommit = Column(Float, nullable=True)
    cpu_pct = Column(Float, nullable=True)
    ram_pct = Column(Float, nullable=True)
    captured_at = Column(DateTime, default=utcnow, index=True)

    run = relationship("SnapshotRun", back_populates="clusters")


class ComplianceFindingSnapshot(Base):
    __tablename__ = "compliance_findings"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("snapshot_runs.id"), nullable=False, index=True)
    vcenter_key = Column(String(64), nullable=True, index=True)
    severity = Column(String(16), nullable=False)  # critical | warning | info
    type = Column(String(32), nullable=False)
    type_label = Column(String(128), nullable=True)
    entity = Column(String(255), nullable=True)
    cluster = Column(String(128), nullable=True)
    metric = Column(Text, nullable=True)
    recommendation = Column(Text, nullable=True)
    captured_at = Column(DateTime, default=utcnow, index=True)

    run = relationship("SnapshotRun", back_populates="findings")


class AppSetting(Base):
    """Simple key/value store for adjustable compliance thresholds."""
    __tablename__ = "app_settings"

    key = Column(String(64), primary_key=True)
    value = Column(String(255), nullable=False)


class ReportLogo(Base):
    """Singleton row (id=1) holding the company logo used on PDF report covers.

    Stored as base64 text in Postgres (not on local disk) so it survives pod
    restarts/rescheduling on KubeSphere without needing a persistent volume.
    """
    __tablename__ = "report_logo"

    id = Column(Integer, primary_key=True, default=1)
    filename = Column(String(255), nullable=True)
    mime_type = Column(String(64), nullable=False)
    data_base64 = Column(Text, nullable=False)
    uploaded_at = Column(DateTime, default=utcnow)


# =====================================================================
# Inventory & License Assessment module
# =====================================================================

class VmOsOverride(Base):
    """Manual OS classification override for one VM. Keyed by vCenter +
    instanceUuid when available (stable across rename/vMotion), falling back to
    vCenter + VM name for imported data without UUIDs."""
    __tablename__ = "vm_os_overrides"

    id = Column(Integer, primary_key=True)
    vcenter_key = Column(String(64), nullable=True, index=True)
    instance_uuid = Column(String(64), nullable=True, index=True)
    vm = Column(String(255), nullable=False, index=True)
    category = Column(String(32), nullable=False)          # windows_server | windows_client | linux | appliance | other_unknown
    os_version = Column(String(128), nullable=True)
    os_edition = Column(String(64), nullable=True)          # e.g. Standard / Datacenter (Windows Server)
    note = Column(Text, nullable=True)
    updated_by = Column(String(64), nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class LicenseRuleSet(Base):
    """Versioned, auditable licensing rule set. `params` is JSON text; the
    engine never hardcodes policy values — it reads them from the active row.
    Seeded defaults carry their public source URL and start as
    status='needs_verification' until a user records who verified them and when."""
    __tablename__ = "license_rule_sets"

    id = Column(Integer, primary_key=True)
    product = Column(String(32), nullable=False, index=True)    # windows_server | vmware
    key = Column(String(64), nullable=False, index=True)        # e.g. ws_core_2022, vmware_core_2024
    version = Column(Integer, nullable=False, default=1)
    name = Column(String(255), nullable=False)
    params_json = Column(Text, nullable=False, default="{}")
    source_url = Column(Text, nullable=True)
    source_note = Column(Text, nullable=True)
    verified_at = Column(DateTime, nullable=True)
    verified_by = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False, default="needs_verification")  # needs_verification | verified | retired
    is_active = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=utcnow)
    created_by = Column(String(64), nullable=True)


class LicenseEntitlement(Base):
    """Licenses the organisation already owns (or quotes being evaluated)."""
    __tablename__ = "license_entitlements"

    id = Column(Integer, primary_key=True)
    product = Column(String(32), nullable=False, index=True)    # ws_std | ws_dc | vsphere_std | vsphere_ent_plus | vvf | vcf | vmware_cpu_legacy
    metric = Column(String(32), nullable=False, default="core")  # core | pack2 | pack16 | cpu
    quantity = Column(Float, nullable=False, default=0)
    kind = Column(String(16), nullable=False, default="owned")   # owned = đang sở hữu · none = xác nhận CHƯA có license (quantity 0, unit_price = báo giá nếu có)
    unit_price = Column(Float, nullable=True)
    currency = Column(String(8), nullable=False, default="VND")
    has_sa = Column(Boolean, nullable=False, default=False)      # Software Assurance / subscription (license mobility)
    scope_vcenter = Column(String(64), nullable=True)            # null = all vCenters
    scope_cluster = Column(String(128), nullable=True)           # null = all clusters in scope
    valid_to = Column(DateTime, nullable=True)
    note = Column(Text, nullable=True)
    updated_by = Column(String(64), nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class LicenseAssessment(Base):
    """Immutable assessment snapshot: every input needed to re-run the
    computation (inventory rows, overrides, rule-set versions + params,
    entitlements) plus the result, so a reviewer can reproduce it later."""
    __tablename__ = "license_assessments"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    created_by = Column(String(64), nullable=True)
    title = Column(String(255), nullable=True)
    note = Column(Text, nullable=True)
    scope_json = Column(Text, nullable=False, default="{}")
    inputs_json = Column(Text, nullable=False)
    result_json = Column(Text, nullable=False)
    result_hash = Column(String(64), nullable=True)


# =====================================================================
# Backup monitoring module (Veritas NetBackup, read-only REST API)
# =====================================================================

class BackupSyncRun(Base):
    """One poll of a NetBackup primary server (jobs are upserted, policies and
    storage are captured as a snapshot of this run)."""
    __tablename__ = "backup_sync_runs"

    id = Column(Integer, primary_key=True)
    nbu_key = Column(String(64), nullable=False, index=True)
    source = Column(String(16), nullable=False, default="live")  # live | demo
    status = Column(String(16), nullable=False, default="ok")    # ok | error
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, default=utcnow, index=True)
    finished_at = Column(DateTime, nullable=True)
    jobs_fetched = Column(Integer, default=0)
    policy_count = Column(Integer, default=0)
    pool_count = Column(Integer, default=0)

    policies = relationship("BackupPolicySnapshot", back_populates="run", cascade="all, delete-orphan")
    storage = relationship("BackupStorageSnapshot", back_populates="run", cascade="all, delete-orphan")


class BackupJob(Base):
    """A NetBackup job (activity monitor row). Upserted by (primary server, jobId):
    a job seen ACTIVE in one poll is updated with its final status in the next."""
    __tablename__ = "backup_jobs"
    __table_args__ = (UniqueConstraint("nbu_key", "job_id", name="uq_backup_job"),)

    id = Column(Integer, primary_key=True)
    nbu_key = Column(String(64), nullable=False, index=True)
    job_id = Column(BigInteger, nullable=False)
    parent_job_id = Column(BigInteger, nullable=True)
    job_type = Column(String(32), nullable=True)
    category = Column(String(16), nullable=False, default="other")  # backup | copy | restore | other
    policy_name = Column(String(255), nullable=True, index=True)
    policy_type = Column(String(64), nullable=True)
    schedule_name = Column(String(255), nullable=True)
    schedule_type = Column(String(64), nullable=True)
    client_name = Column(String(255), nullable=True)
    client_key = Column(String(255), nullable=True, index=True)  # normalised short host name
    state = Column(String(16), nullable=True)                    # QUEUED | ACTIVE | DONE | ...
    status = Column(Integer, nullable=True)                      # NetBackup status code (0 ok, 1 partial)
    start_time = Column(DateTime, nullable=True, index=True)
    end_time = Column(DateTime, nullable=True)
    elapsed_sec = Column(Float, nullable=True)
    kbytes = Column(Float, nullable=True)
    files = Column(BigInteger, nullable=True)
    storage_unit = Column(String(255), nullable=True)
    media_server = Column(String(255), nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class BackupPolicySnapshot(Base):
    __tablename__ = "backup_policy_snapshots"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("backup_sync_runs.id"), nullable=False, index=True)
    nbu_key = Column(String(64), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    policy_type = Column(String(64), nullable=True)
    active = Column(Boolean, nullable=True)
    clients_json = Column(Text, nullable=True)     # JSON list of client host names
    schedules_json = Column(Text, nullable=True)   # JSON list of {name, type, frequencySec, retention}
    storage = Column(String(255), nullable=True)
    selection = Column(Text, nullable=True)        # backup selections / VMware query (shortened)
    captured_at = Column(DateTime, default=utcnow, index=True)

    run = relationship("BackupSyncRun", back_populates="policies")


class BackupStorageSnapshot(Base):
    __tablename__ = "backup_storage_snapshots"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("backup_sync_runs.id"), nullable=False, index=True)
    nbu_key = Column(String(64), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    storage_type = Column(String(64), nullable=True)   # PureDisk (MSDP), AdvancedDisk, DataDomain…
    total_bytes = Column(Float, nullable=True)
    used_bytes = Column(Float, nullable=True)
    free_bytes = Column(Float, nullable=True)
    high_water_pct = Column(Float, nullable=True)
    state = Column(String(32), nullable=True)          # UP | DOWN
    captured_at = Column(DateTime, default=utcnow, index=True)

    run = relationship("BackupSyncRun", back_populates="storage")


class BackupScopeRule(Base):
    """Systems that must be backed up (e.g. production only): VM coverage is measured on VMs
    matching any rule. No rows = every VM in the vCenter inventory is in scope."""
    __tablename__ = "backup_scope_rules"

    id = Column(Integer, primary_key=True)
    vcenter_key = Column(String(64), nullable=False)
    cluster = Column(String(128), nullable=True)   # null = whole vCenter
    host = Column(String(255), nullable=True)      # null = whole cluster
    updated_by = Column(String(64), nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class BackupScheduleRule(Base):
    """Approved backup schedule of one system (e.g. P-CORE-EQUITY): which NetBackup policies
    belong to it (names or glob patterns), Full / Incremental weekdays (Mon = 0), the nightly
    window (start → end, may cross midnight) and Full retention. Drives the nightly
    compliance check and the per-system RPO."""
    __tablename__ = "backup_schedule_rules"

    id = Column(Integer, primary_key=True)
    system = Column(String(128), nullable=False, unique=True)
    policy_patterns = Column(Text, nullable=True)        # "P-CORE-EQ-*, VM-COREEQ" — comma separated, * wildcard
    full_days = Column(String(32), nullable=False, default="")   # "4" = Friday
    incr_days = Column(String(32), nullable=False, default="")   # "0,1,2,3,5,6"
    start_time = Column(String(5), nullable=False)       # "20:00"
    end_time = Column(String(5), nullable=False)         # "04:00" (next day when <= start)
    retention_days = Column(Integer, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    note = Column(Text, nullable=True)
    updated_by = Column(String(64), nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class DataSource(Base):
    """A vCenter or NetBackup server added in Settings → Nguồn dữ liệu. Passwords / API keys
    are stored encrypted (secrets_box.py) and are never returned by the API."""
    __tablename__ = "data_sources"
    __table_args__ = (UniqueConstraint("kind", "key", name="uq_data_source_kind_key"),)

    id = Column(Integer, primary_key=True)
    kind = Column(String(16), nullable=False, index=True)          # vcenter | netbackup
    key = Column(String(64), nullable=False)                       # history is stored under this key: never changes
    name = Column(String(128), nullable=False)
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False)
    username = Column(String(255), nullable=True)
    password_enc = Column(Text, nullable=True)
    api_key_enc = Column(Text, nullable=True)
    verify_ssl = Column(Boolean, nullable=False, default=False)
    ca_pem = Column(Text, nullable=True)                           # CA chain, public data
    demo = Column(Boolean, nullable=False, default=False)
    enabled = Column(Boolean, nullable=False, default=True)
    options = Column(Text, nullable=True)                          # JSON: NetBackup api_version, domain_type, ...
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow)
    updated_by = Column(String(255), nullable=True)
    last_test_at = Column(DateTime, nullable=True)
    last_test_ok = Column(Boolean, nullable=True)
    last_test_message = Column(Text, nullable=True)


# ---------------------------------------------------------------- super app (SA2)
class InboxAssignment(Base):
    """Who handles an inbox item ("Ai xử lý"): one assignee per item key, shared by every user.
    The key includes the level, so an escalated item comes back unassigned for re-triage."""
    __tablename__ = "inbox_assignments"
    __table_args__ = (UniqueConstraint("item_key", name="uq_inbox_assignment"),)

    id = Column(Integer, primary_key=True)
    item_key = Column(String(255), nullable=False)
    assignee = Column(String(255), nullable=False, index=True)       # User.username
    assigned_by = Column(String(255), nullable=False)
    assigned_at = Column(DateTime, nullable=False, default=utcnow)


class InboxAck(Base):
    """A user marked an inbox item done or snoozed it (the item key includes its level, so a worse
    level shows again)."""
    __tablename__ = "inbox_acks"
    __table_args__ = (UniqueConstraint("username", "item_key", name="uq_inbox_ack"),
                      CheckConstraint("state IN ('done', 'snooze')", name="ck_inbox_ack_state"))

    id = Column(Integer, primary_key=True)
    username = Column(String(255), nullable=False, index=True)
    item_key = Column(String(255), nullable=False)
    state = Column(String(8), nullable=False)
    until = Column(Date, nullable=True)                 # snooze end (inclusive)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class UserPref(Base):
    """Small per-user preferences kept server-side (theme, inbox filter…), JSON ≤ 4 KB."""
    __tablename__ = "user_prefs"

    username = Column(String(255), primary_key=True)
    data = Column(Text, nullable=False, default="{}")
    updated_at = Column(DateTime, nullable=False, default=utcnow)


class OpsAlertState(Base):
    """Operations alert pushed to Discord (capacity / backup at the 90 % level): sent once while
    active, re-armed when it clears."""
    __tablename__ = "ops_alert_state"

    key = Column(String(255), primary_key=True)
    title = Column(String(500), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    first_at = Column(DateTime, nullable=False, default=utcnow)
    last_sent_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    last_error = Column(String(500), nullable=True)
    # Inbox provider of the alert ("capacity" / "backup"): a provider that failed keeps its alerts as they are.
    source = Column(String(16), nullable=True)
    # Comma-separated ids of the Discord channels that received the current state of the alert
    # (active: the alert; inactive: the alert, still owed its "Đã hết"). A channel that failed is retried.
    sent_channels = Column(Text, nullable=True)


class AiRun(Base):
    """One AI call (or a refusal by the platform policy): task, user, tokens, cost — never the content."""
    __tablename__ = "ai_runs"
    __table_args__ = (CheckConstraint("status IN ('ok', 'error', 'blocked', 'cached')", name="ck_ai_run_status"),)

    id = Column(Integer, primary_key=True)
    task = Column(String(32), nullable=False, index=True)
    username = Column(String(255), nullable=True)
    model = Column(String(64), nullable=True)
    status = Column(String(8), nullable=False)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    cost_usd = Column(Float, nullable=False, default=0.0)
    input_hash = Column(String(64), nullable=True)
    latency_ms = Column(Integer, nullable=True)
    message = Column(String(300), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)


class AiCache(Base):
    """Result of an AI task for identical facts (bản tin, tổng kết) — re-used instead of paying again."""
    __tablename__ = "ai_cache"

    key = Column(String(64), primary_key=True)                  # sha256(task + facts)
    task = Column(String(32), nullable=False)
    output = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class ImportMapping(Base):
    """Column mapping confirmed by a user for one file layout (header signature) — re-used on the
    next import of the same layout without asking AI again (super app SA5 / A4)."""
    __tablename__ = "import_mappings"
    __table_args__ = (UniqueConstraint("target", "signature", name="uq_import_mapping"),)

    id = Column(Integer, primary_key=True)
    target = Column(String(16), nullable=False)                  # hosts | vms
    signature = Column(String(64), nullable=False)               # sha256 of the normalised header list
    headers_json = Column(Text, nullable=False)
    mapping_json = Column(Text, nullable=False)                  # {field: header}
    source = Column(String(8), nullable=False, default="manual")  # ai | manual
    uses = Column(Integer, nullable=False, default=0)
    created_by = Column(String(128))
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_by = Column(String(128))
    updated_at = Column(DateTime, nullable=False, default=utcnow)


class ReportArchive(Base):
    """Copy of a PDF report as it was exported (Tài liệu → Kho báo cáo, super app SA6). Stored in the
    database so every pod sees it; size, count and age are capped (report_archive.py)."""
    __tablename__ = "report_archive"

    id = Column(Integer, primary_key=True)
    kind = Column(String(32), nullable=False, index=True)        # capacity | backup | license | sizing | spend | renovation | other
    scope = Column(String(8), nullable=False, default="general")  # general | spend (needs a spend role to read)
    title = Column(String(255), nullable=False)
    filename = Column(String(255), nullable=False)
    params = Column(String(1000))                                # query string of the export (without secrets)
    size_bytes = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False)
    content = Column(LargeBinary, nullable=False)
    created_by = Column(String(128), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)


class UserPasskey(Base):
    """A WebAuthn credential (passkey) of a local account: iPhone / Mac (iCloud Keychain), Android,
    Windows Hello or a security key. Only the public key is stored."""
    __tablename__ = "user_passkeys"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    credential_id = Column(String(512), nullable=False, unique=True)     # base64url
    public_key = Column(Text, nullable=False)                           # base64url COSE key
    sign_count = Column(BigInteger, nullable=False, default=0)
    transports = Column(String(120))
    aaguid = Column(String(40))
    backed_up = Column(Boolean, nullable=False, default=False)          # synced passkey (iCloud Keychain…)
    name = Column(String(80), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    last_used_at = Column(DateTime)


class PasskeyInvite(Base):
    """One-time link that lets a local account (created without a password) register its first
    passkey. Only the SHA-256 of the token is stored."""
    __tablename__ = "passkey_invites"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime)
    created_by = Column(String(255), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)


# Tables of the Chi phí & Hợp đồng module register on the same metadata (create_all).
from .spend import models as _spend_models  # noqa: E402,F401
