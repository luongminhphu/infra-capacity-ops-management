"""ITSM sources read-only (ICO v2 G7a): ManageEngine ServiceDesk Plus On-Premises, REST API v3, technician key.

Requests to create a VM / add resources are read from the templates chosen per source (Cài đặt → Nguồn dữ liệu, kind "sdp"),
first by a backfill from `backfill_from`, then incrementally by `last_updated_time`. The technician key is encrypted with
secrets_box and never returned. Read-only rule (CLAUDE.md rule 1): GET only — the HTTP client refuses every other method.
"""
import datetime as dt
from dataclasses import dataclass, field
from typing import List

from ..storage import StorageError as ItsmError  # same error family: the scheduler shows its message as is

KINDS = ("sdp",)
KIND_LABELS = {"sdp": "ServiceDesk Plus"}

__all__ = ["ItsmConfig", "ItsmError", "KINDS", "KIND_LABELS"]


@dataclass
class ItsmConfig:
    key: str
    name: str
    host: str
    port: int = 8080
    api_key: str = ""                     # technician key (header authtoken)
    verify_ssl: bool = False
    ca_pem: str = ""
    demo: bool = False
    timeout: int = 60
    templates: List[str] = field(default_factory=list)
    backfill_from: dt.date = dt.date(2025, 10, 1)
    rate_per_min: int = 60
    interval_minutes: int = 30
    kind: str = "sdp"
    origin: str = "ui"
