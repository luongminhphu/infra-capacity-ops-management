"""
Central configuration for Infra Capacity Ops backend.

All secrets (vCenter passwords, JWT secret, admin bootstrap password) come from
environment variables / .env — never hardcoded and never committed to git.
"""
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal, List, Optional

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# pydantic-settings parses `.env` into typed Settings fields only — it does NOT
# inject the file into os.environ. The vCenter block below reads VCENTER_i_*
# directly from os.environ (they aren't declared as typed Settings fields, since
# the count is dynamic), so load .env into the real process environment too.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


def _positive_int(v) -> Optional[int]:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


class VCenterConfig:
    def __init__(self, key: str, name: str, host: str, port: int, username: str,
                 password: str, verify_ssl: bool, demo: bool = False, ca_pem: str = "", origin: str = "env",
                 timeout: Optional[int] = None, platform: str = "vmware"):
        self.key = key
        # vmware (vCenter, pyVmomi) | proxmox (Proxmox VE API token) | nutanix (Prism Element v2.0): app/hypervisors/
        self.platform = platform
        self.name = name
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.demo = demo
        self.ca_pem = ca_pem          # CA chain (PEM text) trusted for this vCenter, when verify_ssl is on
        self.origin = origin          # "env" (ConfigMap / .env) or "ui" (Settings → Nguồn dữ liệu)
        # Seconds per SOAP call (connect / each PropertyCollector page). None → VCENTER_TIMEOUT_SECONDS.
        self.timeout = timeout


class NetBackupConfig:
    """One NetBackup primary (master) server reached over its REST API with a
    read-only identity — an API key (preferred, NetBackup 9.x+) or a
    username/password of a user that only holds a view-only RBAC role."""

    def __init__(self, key: str, name: str, host: str, port: int, api_key: str, username: str, password: str,
                 domain_type: str, domain_name: str, verify_ssl: bool, api_version: str, demo: bool = False,
                 timeout: int = 60, ca_cert: str = "", ca_pem: str = "", origin: str = "env"):
        self.key = key
        self.name = name
        self.host = host
        self.port = port
        self.api_key = api_key
        self.username = username
        self.password = password
        self.domain_type = domain_type
        self.domain_name = domain_name
        self.verify_ssl = verify_ssl
        self.api_version = api_version
        self.demo = demo
        self.timeout = timeout
        # NetBackup CA used when verify_ssl is on: a PEM file path (env) or PEM text (Settings UI).
        self.ca_cert = ca_cert
        self.ca_pem = ca_pem
        self.origin = origin


DEFAULT_JWT_SECRET = "change-me-in-.env-please"
# Placeholder values shipped in .env.example / k8s/02-secret.example.yaml: never valid keys.
PLACEHOLDER_SECRETS = frozenset({
    DEFAULT_JWT_SECRET,
    "change-me-to-a-long-random-string",
    "change-me-to-another-long-random-string",
})
MIN_JWT_SECRET_LEN = 16


def jwt_secret_problem(secret: Optional[str]) -> Optional[str]:
    """Why JWT_SECRET is unsafe to start with (None = acceptable). Anyone knowing the key can
    sign an admin session cookie and decrypt stored source passwords (secrets_box fallback)."""
    value = (secret or "").strip()
    if not value:
        return "JWT_SECRET is empty"
    if value in PLACEHOLDER_SECRETS:
        return "JWT_SECRET is the default / example placeholder"
    if len(value) < MIN_JWT_SECRET_LEN:
        return f"JWT_SECRET is shorter than {MIN_JWT_SECRET_LEN} characters"
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Infra Capacity Ops Management"
    environment: str = Field(default="production")

    database_url: str = Field(default="postgresql+psycopg2://ico_app:devpassword@localhost:5432/infra_capacity_ops")

    jwt_secret: str = Field(default=DEFAULT_JWT_SECRET)
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 12  # 12h session

    admin_username: str = Field(default="admin")
    admin_password: str = Field(default="")  # required on first boot if no users exist

    cookie_secure: bool = Field(default=False)  # set True when served over HTTPS (implied by an https PUBLIC_URL)
    # External URL users type, e.g. https://ico.contoso.com or https://ico-contoso.msappproxy.net (Microsoft Entra
    # Application Proxy). Used for the Entra redirect URI, the passkey relying party (RP ID = its host name)
    # and secure cookies. Behind App Proxy the Host header is the internal one, so this must be set.
    public_url: str = Field(default="")

    # ---- Sign-in: local accounts and / or Microsoft Entra ID (OIDC) ----
    # auth_mode: "local" (username / password only), "entra" (Entra ID only; local accounts listed in
    # auth_breakglass_users still work as emergency access), "both".
    auth_mode: Literal["local", "entra", "both"] = Field(default="local")
    auth_breakglass_users: str = Field(default="")          # comma separated local usernames, e.g. "admin"
    entra_tenant_id: str = Field(default="")
    entra_client_id: str = Field(default="")
    entra_client_secret: str = Field(default="")            # Secret, never in the ConfigMap
    entra_redirect_uri: str = Field(default="")             # https://<external url>/api/auth/oidc/callback
    entra_post_logout_redirect_uri: str = Field(default="") # external URL of the app (optional)
    entra_authority_host: str = Field(default="https://login.microsoftonline.com")
    # MFA: enforced by Conditional Access; the app double-checks the "amr" claim.
    # "if-present" = reject when amr is present without mfa · "strict" = amr must contain mfa · "off".
    entra_require_mfa: str = Field(default="if-present")
    entra_allowed_groups: str = Field(default="")           # optional: group object IDs (comma separated)
    entra_allowed_roles: str = Field(default="")            # optional: app role values (comma separated)
    entra_login_label: str = Field(default="Đăng nhập bằng Microsoft")
    # Login page goes straight to Microsoft when Entra is the only way in (seamless SSO behind App Proxy).
    entra_auto_redirect: bool = Field(default=False)
    # Local accounts: "passkey" (Face ID / Touch ID / security key, no password — break-glass users keep
    # their password), "password", or "both".
    auth_local_method: Literal["passkey", "password", "both"] = Field(default="both")
    passkey_rp_name: str = Field(default="Infra Capacity Ops")

    # ---- Admins (MCP server access) ----
    # Local accounts that are admins (comma separated; empty = ADMIN_USERNAME).
    admin_users: str = Field(default="")
    # Entra app role values that make a user admin (checked at every Microsoft sign-in).
    entra_admin_roles: str = Field(default="ICO.Admin")
    # App role of system owners: allowed to sign in; a user holding only this role gets the owner portal (access_scope=owner).
    entra_owner_role: str = Field(default="ICO.Owner")

    # ---- MCP server (read-only app logs & performance for an AI client) ----
    mcp_enabled: bool = Field(default=False)
    mcp_token_max_days: int = Field(default=180)
    mcp_mask_hosts: bool = Field(default=True)      # mask IPs / FQDNs in logs returned to the AI
    mcp_allowed_origins: str = Field(default="")    # browser Origins allowed to call /mcp (none by default)
    mcp_rate_per_minute: int = Field(default=120)

    # ---- Infrastructure health thresholds (app/infra.py) ----
    ds_watch_pct: float = Field(default=80)         # datastore used %: THEO DÕI
    ds_warn_pct: float = Field(default=90)          # CẢNH BÁO
    ds_crit_pct: float = Field(default=95)          # VƯỢT NGƯỠNG
    ds_full_warn_days: int = Field(default=30)      # forecast: full within N days → CẢNH BÁO
    ds_full_crit_days: int = Field(default=7)       # → VƯỢT NGƯỠNG
    ds_overprov_pct: float = Field(default=150)     # (used + uncommitted) / capacity: THEO DÕI
    snapshot_watch_hours: int = Field(default=24)   # older than this: THEO DÕI (backup snapshots live minutes)
    snapshot_warn_days: int = Field(default=3)      # CẢNH BÁO
    snapshot_crit_days: int = Field(default=7)      # VƯỢT NGƯỠNG
    snapshot_crit_gb: float = Field(default=200)    # snapshot data ≥ this: VƯỢT NGƯỠNG
    snapshot_chain_warn: int = Field(default=3)     # chain of ≥ N snapshots: CẢNH BÁO

    storage_latency_warn_ms: float = Field(default=5)    # array read / write latency: CẢNH BÁO
    storage_latency_crit_ms: float = Field(default=20)   # VƯỢT NGƯỠNG
    storage_stale_minutes: int = Field(default=60)       # last good read older than this: CẢNH BÁO
    asset_age_limit_months: int = Field(default=60)      # DC device in use this long (Green policy): CẢNH BÁO
    asset_age_warn_months: int = Field(default=48)       # approaching the limit: THEO DÕI

    # ---- Owner reviews (owners/review.py): a campaign every REVIEW_PERIOD_MONTHS, opened in the listed months ----
    review_period_months: int = Field(default=6)
    review_start_months: str = Field(default="1,7")
    review_due_days: int = Field(default=21)
    review_snapshot_days: int = Field(default=14)        # snapshot older than this → "nên rà" (idle / power-off use idle_days)
    review_dry_run: bool = Field(default=False)          # campaigns after the first; the first one is always a dry run
    review_infra_email: str = Field(default="")         # team hạ tầng: escalations without a manager, the closing summary
    # Unit costs (VND per month) to value the reclaimed resources; 0 = not valued.
    review_unit_cost_vcpu: int = Field(default=0)
    review_unit_cost_ram_gb: int = Field(default=0)
    review_unit_cost_disk_gb: int = Field(default=0)

    # ---- CI registry (app/cmdb/): a CI not seen for this many days becomes "retired" (never deleted) ----
    cmdb_retire_days: int = Field(default=7)
    # ---- Dell OME (hardware/): deep inventory / firmware compliance and warranty are read less often than the round ----
    ome_inventory_hours: int = Field(default=12)
    ome_warranty_hours: int = Field(default=24)
    ome_alert_backfill_days: int = Field(default=7)          # first read of AlertService: this far back
    hw_alert_retention_months: int = Field(default=13)       # hw_alert rows older than this are purged
    event_correlate_minutes: int = Field(default=15)          # G6: an event joins an incident of a strongly linked CI opened this recently

    # ---- Time series (app/metrics.py): raw points → hourly → daily ----
    metrics_raw_days: int = Field(default=7)
    metrics_hourly_days: int = Field(default=90)
    metrics_daily_days: int = Field(default=1095)

    # ---- Observability (app logs + request / SQL timing kept by the app itself) ----
    app_version: str = Field(default="dev")          # set at image build (GIT_SHA)
    obs_retention_days: int = Field(default=30)
    obs_slow_request_ms: int = Field(default=1000)
    obs_slow_query_ms: int = Field(default=200)

    # ---- Module Chi phí & Hợp đồng IT (spend) ----
    spend_files_dir: str = Field(default="/data/files")     # volume (PVC ico-files / compose ico_files)
    spend_max_upload_mb: int = Field(default=20)
    # Any request body above this is refused with 413 before a route reads it (chunked uploads included).
    # Keep it above SPEND_MAX_UPLOAD_MB.
    max_request_mb: int = Field(default=32)
    # Use X-Forwarded-For for the client IP in logs / audit only when a trusted proxy sets it.
    trust_forwarded_for: bool = Field(default=False)
    # Entra app role values giving a spend role (checked at every Microsoft sign-in).
    entra_spend_viewer_roles: str = Field(default="ICO.Spend.Viewer")
    entra_spend_editor_roles: str = Field(default="ICO.Spend.Editor")
    entra_spend_approver_roles: str = Field(default="ICO.Spend.Approver")

    def admin_user_list(self) -> List[str]:
        names = [u.strip().lower() for u in self.admin_users.split(",") if u.strip()]
        return names or [self.admin_username.strip().lower()]

    def entra_owner_role_list(self) -> List[str]:
        return [r.strip() for r in self.entra_owner_role.split(",") if r.strip()]

    def entra_admin_role_list(self) -> List[str]:
        return [r.strip() for r in self.entra_admin_roles.split(",") if r.strip()]

    def public_base(self) -> str:
        return self.public_url.strip().rstrip("/")

    def oidc_redirect_uri(self) -> str:
        """ENTRA_REDIRECT_URI, or derived from PUBLIC_URL."""
        if self.entra_redirect_uri:
            return self.entra_redirect_uri
        return f"{self.public_base()}/api/auth/oidc/callback" if self.public_base() else ""

    def secure_cookies(self) -> bool:
        return bool(self.cookie_secure or self.public_base().lower().startswith("https://"))

    def passkey_enabled(self) -> bool:
        return self.auth_local_method in ("passkey", "both")

    def password_enabled_for(self, username: str) -> bool:
        return self.auth_local_method in ("password", "both") or username.lower() in self.breakglass()

    def entra_enabled(self) -> bool:
        return self.auth_mode in ("entra", "both") and bool(self.entra_tenant_id and self.entra_client_id and self.oidc_redirect_uri())

    def local_enabled(self) -> bool:
        return self.auth_mode in ("local", "both")

    def breakglass(self) -> List[str]:
        return [u.strip().lower() for u in self.auth_breakglass_users.split(",") if u.strip()]

    poll_interval_minutes: int = Field(default=60)
    # vCenters and backup servers polled at the same time (each in its own thread / DB session).
    sync_concurrency: int = Field(default=4)
    enable_scheduler: bool = Field(default=True)
    # /api/health/ready fails (503) when the scheduler heartbeat is stale. Set false when several
    # replicas run (the scheduler state is per pod): the scheduler is then reported but not gating.
    readiness_require_scheduler: bool = Field(default=True)
    # DAT-10 release 3: drop the legacy Float money columns (license_entitlements.unit_price, ai_runs.cost_usd).
    # Turn on only once no pod of an older release is running (rule 4: never drop a column old pods still use).
    drop_legacy_money_columns: bool = Field(default=False)
    # Socket timeout of every vCenter SOAP call (login, each PropertyCollector page). Per source:
    # VCENTER_i_TIMEOUT. pyVmomi waits forever without it (one hung vCenter froze every poll).
    vcenter_timeout_seconds: int = Field(default=120)
    # Hard deadline of one source's sync inside a poll round / "sync all": past it the source is
    # reported as failed and the round goes on (the worker thread ends when its socket times out).
    sync_source_deadline_seconds: int = Field(default=1800)
    # vCenter snapshot history (runs, hosts, VMs, cluster aggregates, findings) older than this
    # many months is purged once a day; the latest successful run per vCenter is always kept. 0 = keep all.
    snapshot_retention_months: int = Field(default=6)

    # Compliance / overcommit default thresholds (mirrors the original client-side policy)
    cpu_overcommit_max: float = 6.0
    ram_overcommit_max: float = 1.0
    ht_factor: float = 2.0
    idle_days: int = 30
    # Storage assessment is OFF by default: shared SAN + thick provisioning
    # makes per-VM "used / provisioned" meaningless for capacity planning.
    # Can be toggled at runtime from the UI ("Tiêu chí đánh giá" dropdown).
    include_storage: bool = False

    # Up to 4 vCenters configured via env vars VCENTER_1_* .. VCENTER_4_*
    vcenter_count: int = Field(default=4)

    # Up to 4 NetBackup primary servers via NETBACKUP_1_* .. NETBACKUP_4_*
    netbackup_count: int = Field(default=4)
    # First sync pulls this many days of job history; later syncs are incremental.
    backup_initial_days: int = Field(default=14)
    # Job rows older than this are purged after each sync (keeps the table bounded).
    backup_retention_days: int = Field(default=183)   # 6 months of job / run / pool history (decision 16/11/2026)

    # Empty = no cross-origin access: the frontend is served by this app on the same origin.
    cors_origins: str = Field(default="")

    def cors_origin_list(self) -> List[str]:
        if (self.cors_origins or "").strip() == "*":
            return ["*"]
        return [o.strip() for o in (self.cors_origins or "").split(",") if o.strip()]

    # ---- Data sources ----
    # Sources come from VCENTER_i_* / NETBACKUP_i_* variables and from Settings → Nguồn dữ liệu
    # (table data_sources, secrets encrypted with SECRETS_KEY). A UI source with the same key
    # replaces the variable-based one. vcenters() / netbackups() return the merged, enabled list.
    secrets_key: str = Field(default="")
    # Former SECRETS_KEY value(s), comma separated, still accepted for reading while the stored secrets are
    # re-encrypted with the new key at start-up (secrets_box.rotate). Remove once the pods have restarted.
    secrets_key_previous: str = Field(default="")

    def vcenters(self) -> List[VCenterConfig]:
        from .sources import vcenter_configs
        return vcenter_configs(self)

    def netbackups(self) -> List[NetBackupConfig]:
        from .sources import netbackup_configs
        return netbackup_configs(self)

    def storages(self) -> list:
        """Pure FlashArray / NetApp ONTAP sources (Cài đặt → Nguồn dữ liệu)."""
        from .sources import storage_configs
        return storage_configs(self)

    def ipam(self) -> list:
        """GestióIP IPAM sources (Cài đặt → Nguồn dữ liệu)."""
        from .sources import ipam_configs
        return ipam_configs(self)

    def hardware(self) -> list:
        """Dell OpenManage Enterprise sources (Cài đặt → Nguồn dữ liệu)."""
        from .sources import hardware_configs
        return hardware_configs(self)

    def env_vcenters(self) -> List[VCenterConfig]:
        result = []
        for i in range(1, self.vcenter_count + 1):
            prefix = f"VCENTER_{i}_"
            host = os.environ.get(prefix + "HOST")
            if not host:
                continue
            demo = os.environ.get(prefix + "DEMO", "false").lower() in ("1", "true", "yes")
            result.append(VCenterConfig(
                key=os.environ.get(prefix + "KEY", f"vc{i}"),
                name=os.environ.get(prefix + "NAME", host),
                host=host,
                port=int(os.environ.get(prefix + "PORT", "443")),
                username=os.environ.get(prefix + "USERNAME", "infra.mon@vsphere.local"),
                password=os.environ.get(prefix + "PASSWORD", ""),
                verify_ssl=os.environ.get(prefix + "VERIFY_SSL", "false").lower() in ("1", "true", "yes"),
                demo=demo,
                timeout=_positive_int(os.environ.get(prefix + "TIMEOUT")),
            ))
        return result


    def env_netbackups(self) -> List[NetBackupConfig]:
        result = []
        for i in range(1, self.netbackup_count + 1):
            prefix = f"NETBACKUP_{i}_"
            env = lambda k, d="": os.environ.get(prefix + k, d)  # noqa: E731
            host = env("HOST")
            demo = env("DEMO", "false").lower() in ("1", "true", "yes")
            if not host and not demo:
                continue
            result.append(NetBackupConfig(
                key=env("KEY", f"nbu{i}"),
                name=env("NAME", host or f"NetBackup demo {i}"),
                host=host or f"nbu-demo-{i}.local",
                port=int(env("PORT", "1556") or 1556),
                api_key=env("API_KEY"),
                username=env("USERNAME"),
                password=env("PASSWORD"),
                domain_type=env("DOMAIN_TYPE", "unixpwd") or "unixpwd",
                domain_name=env("DOMAIN_NAME"),
                verify_ssl=env("VERIFY_SSL", "false").lower() in ("1", "true", "yes"),
                api_version=env("API_VERSION", "8.0") or "8.0",
                demo=demo,
                timeout=int(env("TIMEOUT", "60") or 60),
                # Per server, or one CA file shared by every server (NETBACKUP_CA_CERT).
                ca_cert=env("CA_CERT") or os.environ.get("NETBACKUP_CA_CERT", ""),
            ))
        return result


@lru_cache()
def get_settings() -> Settings:
    return Settings()
