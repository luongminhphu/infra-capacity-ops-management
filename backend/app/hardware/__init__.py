"""Server hardware monitored read-only through Dell OpenManage Enterprise (OME 4.x, one instance per source).

Sources are added in Cài đặt → Nguồn dữ liệu (data_sources, kind "ome"); the password is encrypted with
secrets_box and never returned by the API. The OME account needs the Viewer role only.

Read-only: GET only. The one exception is the OME session (POST /api/SessionService/Sessions to log in,
DELETE /api/SessionService/Sessions('<id>') to log out) — CLAUDE.md rule 1. Every other POST / PUT / PATCH /
DELETE (alert acknowledge, jobs, firmware update, power) is refused by the client before it leaves.
"""
from dataclasses import dataclass

from ..storage import StorageError as HardwareError  # same error family: the scheduler shows its message as is

KINDS = ("ome",)
KIND_LABELS = {"ome": "Dell OpenManage Enterprise"}

__all__ = ["HardwareConfig", "HardwareError", "KINDS", "KIND_LABELS"]


@dataclass
class HardwareConfig:
    key: str
    name: str
    host: str
    port: int = 443
    username: str = ""               # OME account, role Viewer
    password: str = ""
    verify_ssl: bool = False
    ca_pem: str = ""
    demo: bool = False
    timeout: int = 60
    kind: str = "ome"
    origin: str = "ui"
