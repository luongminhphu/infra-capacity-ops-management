from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    """Serialized with camelCase aliases (displayName) to match every other
    API response in this app — the frontend reads `user.displayName`."""
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    username: str
    display_name: Optional[str] = None
    is_admin: bool = False
    spend_role: str = "none"     # effective role in Chi phí & Hợp đồng: none|viewer|editor|approver|admin
    owner_admin: bool = False    # may manage Hệ thống & Owner (admins always may)
    access_scope: str = "full"   # "full" = the app, "owner" = the owner portal only


class ImportPayload(BaseModel):
    """Client already parsed & normalized the CSV/JSON on the frontend (reusing the
    existing normalizeAndValidate logic) — the backend just persists it as a
    SnapshotRun so imported data also feeds the historical trend charts."""
    # bounded and typed: one bad / huge payload must not bloat the DB or 500 half-way (SEC-14)
    hosts: List[Dict[str, Any]] = Field(default_factory=list, max_length=5000)
    vms: List[Dict[str, Any]] = Field(default_factory=list, max_length=50000)
    label: Optional[str] = Field(default="Import thủ công", max_length=200)


class SettingsUpdate(BaseModel):
    cpu_overcommit_max: Optional[float] = None
    ram_overcommit_max: Optional[float] = None
    ht_factor: Optional[float] = None
    idle_days: Optional[int] = None
    include_storage: Optional[bool] = None


class LogoUpload(BaseModel):
    """Logo submitted as a data URL (e.g. 'data:image/png;base64,iVBORw0...'),
    exactly what FileReader.readAsDataURL() produces in the browser — the
    frontend can PUT this straight from the <input type=file> without any
    extra encoding step."""
    data_url: str
    filename: Optional[str] = None
