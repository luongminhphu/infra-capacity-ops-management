"""Storage arrays monitored read-only: Pure Storage FlashArray (Purity REST 2.x — FlashArray//X, //C) and
NetApp ONTAP (REST, ONTAP ≥ 9.6 — AFF / FAS). Sources are added in Cài đặt → Nguồn dữ liệu (data_sources,
kind "pure" / "netapp"); secrets are encrypted with secrets_box and never returned by the API.

Read-only: GET only. The one exception is Pure's session endpoints (POST /api/2.x/login with the API token,
POST /api/2.x/logout) — the same exception as NetBackup's /login and /logout (CLAUDE.md rule 1).
"""
from dataclasses import dataclass

VENDORS = ("pure", "netapp")
VENDOR_LABELS = {"pure": "Pure Storage FlashArray", "netapp": "NetApp ONTAP"}


class StorageError(RuntimeError):
    """Connection / API error, message written for the admin (Vietnamese)."""


@dataclass
class StorageConfig:
    key: str
    name: str
    vendor: str                      # pure | netapp
    host: str
    port: int = 443
    username: str = ""               # NetApp (role readonly)
    password: str = ""
    api_token: str = ""              # Pure (API token of a read-only user)
    verify_ssl: bool = False
    ca_pem: str = ""
    demo: bool = False
    timeout: int = 30
    origin: str = "ui"
