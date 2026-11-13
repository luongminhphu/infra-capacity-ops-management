"""
Central configuration for Infra Capacity Ops backend.

All secrets (vCenter passwords, JWT secret, admin bootstrap password) come from
environment variables / .env — never hardcoded and never committed to git.
"""
import os
from functools import lru_cache
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# pydantic-settings parses `.env` into typed Settings fields only — it does NOT
# inject the file into os.environ. The vCenter block below reads VCENTER_i_*
# directly from os.environ (they aren't declared as typed Settings fields, since
# the count is dynamic), so load .env into the real process environment too.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


class VCenterConfig:
    def __init__(self, key: str, name: str, host: str, port: int, username: str,
                 password: str, verify_ssl: bool, demo: bool = False):
        self.key = key
        self.name = name
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.demo = demo


class NetBackupConfig:
    """One NetBackup primary (master) server reached over its REST API with a
    read-only identity — an API key (preferred, NetBackup 9.x+) or a
    username/password of a user that only holds a view-only RBAC role."""

    def __init__(self, key: str, name: str, host: str, port: int, api_key: str, username: str, password: str,
                 domain_type: str, domain_name: str, verify_ssl: bool, api_version: str, demo: bool = False,
                 timeout: int = 60, ca_cert: str = ""):
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
        # PEM file with the NetBackup CA certificate, used when verify_ssl is on.
        self.ca_cert = ca_cert


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Infra Capacity Ops Management"
    environment: str = Field(default="production")

    database_url: str = Field(default="postgresql+psycopg2://ico_app:devpassword@localhost:5432/infra_capacity_ops")

    jwt_secret: str = Field(default="change-me-in-.env-please")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 12  # 12h session

    admin_username: str = Field(default="admin")
    admin_password: str = Field(default="")  # required on first boot if no users exist

    cookie_secure: bool = Field(default=False)  # set True when served over HTTPS

    # ---- Sign-in: local accounts and / or Microsoft Entra ID (OIDC) ----
    # auth_mode: "local" (username / password only), "entra" (Entra ID only; local accounts listed in
    # auth_breakglass_users still work as emergency access), "both".
    auth_mode: str = Field(default="local")
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

    # ---- Admins (MCP server access) ----
    # Local accounts that are admins (comma separated; empty = ADMIN_USERNAME).
    admin_users: str = Field(default="")
    # Entra app role values that make a user admin (checked at every Microsoft sign-in).
    entra_admin_roles: str = Field(default="ICO.Admin")

    # ---- MCP server (read-only app logs & performance for an AI client) ----
    mcp_enabled: bool = Field(default=False)
    mcp_token_max_days: int = Field(default=180)
    mcp_mask_hosts: bool = Field(default=True)      # mask IPs / FQDNs in logs returned to the AI
    mcp_allowed_origins: str = Field(default="")    # browser Origins allowed to call /mcp (none by default)
    mcp_rate_per_minute: int = Field(default=120)

    # ---- Observability (app logs + request / SQL timing kept by the app itself) ----
    app_version: str = Field(default="dev")          # set at image build (GIT_SHA)
    obs_retention_days: int = Field(default=30)
    obs_slow_request_ms: int = Field(default=1000)
    obs_slow_query_ms: int = Field(default=200)

    def admin_user_list(self) -> List[str]:
        names = [u.strip().lower() for u in self.admin_users.split(",") if u.strip()]
        return names or [self.admin_username.strip().lower()]

    def entra_admin_role_list(self) -> List[str]:
        return [r.strip() for r in self.entra_admin_roles.split(",") if r.strip()]

    def entra_enabled(self) -> bool:
        return self.auth_mode in ("entra", "both") and bool(self.entra_tenant_id and self.entra_client_id and self.entra_redirect_uri)

    def local_enabled(self) -> bool:
        return self.auth_mode in ("local", "both")

    def breakglass(self) -> List[str]:
        return [u.strip().lower() for u in self.auth_breakglass_users.split(",") if u.strip()]

    poll_interval_minutes: int = Field(default=60)
    # vCenters and backup servers polled at the same time (each in its own thread / DB session).
    sync_concurrency: int = Field(default=4)
    enable_scheduler: bool = Field(default=True)

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
    backup_retention_days: int = Field(default=400)

    cors_origins: str = Field(default="*")

    def cors_origin_list(self) -> List[str]:
        if self.cors_origins == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def vcenters(self) -> List[VCenterConfig]:
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
            ))
        return result


    def netbackups(self) -> List[NetBackupConfig]:
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
