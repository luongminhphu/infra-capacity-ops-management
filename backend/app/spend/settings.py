"""Module settings kept in app_settings (key spend.*), validated on write."""
import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from ..models import AppSetting

PREFIX = "spend."


@dataclass
class SpendSettings:
    fiscal_start_month: int = 1
    reminder_days: List[int] = field(default_factory=lambda: [90, 60, 30])
    reminder_hour: int = 8                 # Vietnam time
    app_base_url: str = ""                 # link in reminders, e.g. https://ico.corp.example
    purge_days: int = 30                   # phase 4: delay before old contract versions are purged

    def public(self) -> Dict[str, Any]:
        d = asdict(self)
        return {"fiscalStartMonth": d["fiscal_start_month"], "reminderDays": d["reminder_days"],
                "reminderHour": d["reminder_hour"], "appBaseUrl": d["app_base_url"], "purgeDays": d["purge_days"]}


class SettingsError(ValueError):
    pass


def load(db: Session) -> SpendSettings:
    rows = {r.key[len(PREFIX):]: r.value for r in db.query(AppSetting).filter(AppSetting.key.like(PREFIX + "%")).all()}
    s = SpendSettings()
    try:
        if "fiscal_start_month" in rows:
            s.fiscal_start_month = int(rows["fiscal_start_month"])
        if "reminder_days" in rows:
            s.reminder_days = [int(x) for x in rows["reminder_days"].split(",") if x.strip()]
        if "reminder_hour" in rows:
            s.reminder_hour = int(rows["reminder_hour"])
        if "purge_days" in rows:
            s.purge_days = int(rows["purge_days"])
    except ValueError:
        pass   # a hand-edited bad value falls back to the default instead of breaking the module
    s.app_base_url = rows.get("app_base_url", "")
    return s


def validate(data: Dict[str, Any], current: SpendSettings) -> SpendSettings:
    s = SpendSettings(**asdict(current))
    try:
        if "fiscalStartMonth" in data:
            s.fiscal_start_month = int(data["fiscalStartMonth"])
        if "reminderDays" in data:
            s.reminder_days = sorted({int(x) for x in data["reminderDays"]}, reverse=True)
        if "reminderHour" in data:
            s.reminder_hour = int(data["reminderHour"])
        if "purgeDays" in data:
            s.purge_days = int(data["purgeDays"])
    except (TypeError, ValueError) as exc:
        raise SettingsError("Giá trị cài đặt phải là số nguyên.") from exc
    if "appBaseUrl" in data:
        s.app_base_url = str(data["appBaseUrl"] or "").strip().rstrip("/")
    if not 1 <= s.fiscal_start_month <= 12:
        raise SettingsError("Tháng bắt đầu năm tài chính phải từ 1 đến 12.")
    if not 1 <= len(s.reminder_days) <= 6 or any(not 1 <= d <= 365 for d in s.reminder_days):
        raise SettingsError("Mốc nhắc: 1–6 mốc, mỗi mốc từ 1 đến 365 ngày.")
    if not 0 <= s.reminder_hour <= 23:
        raise SettingsError("Giờ gửi nhắc phải từ 0 đến 23.")
    if not 1 <= s.purge_days <= 365:
        raise SettingsError("Thời gian chờ xoá phải từ 1 đến 365 ngày.")
    if s.app_base_url:
        u = urlparse(s.app_base_url)
        if u.scheme not in ("http", "https") or not u.netloc or len(s.app_base_url) > 200 or u.query or u.fragment:
            raise SettingsError("Địa chỉ ứng dụng phải có dạng https://ten-may-chu[/duong-dan].")
    return s


def save(db: Session, s: SpendSettings) -> None:
    values = {"fiscal_start_month": str(s.fiscal_start_month), "reminder_days": ",".join(map(str, s.reminder_days)),
              "reminder_hour": str(s.reminder_hour), "app_base_url": s.app_base_url, "purge_days": str(s.purge_days)}
    for k, v in values.items():
        db.merge(AppSetting(key=PREFIX + k, value=v))


def get_state(db: Session, key: str) -> Dict[str, Any]:
    row = db.get(AppSetting, PREFIX + "state." + key)
    try:
        return json.loads(row.value) if row else {}
    except ValueError:
        return {}


def set_state(db: Session, key: str, value: Dict[str, Any]) -> None:
    text = json.dumps(value, default=lambda o: o.isoformat() if isinstance(o, (dt.date, dt.datetime)) else str(o))
    db.merge(AppSetting(key=PREFIX + "state." + key, value=text[:255]))
