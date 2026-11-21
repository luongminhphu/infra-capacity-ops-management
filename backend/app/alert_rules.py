"""Operations alert rules (Cài đặt → Cảnh báo): which inbox items go to Discord, at which level, to which
channels, and quiet hours. Stored in app_settings (value ≤ 255 characters):
  alerts.<source> = "<enabled 0|1>|<min level crit|bad|warn>|<channel ids comma separated, empty = all>"
  alerts.quiet    = "<enabled 0|1>|HH:MM|HH:MM"          (Vietnam time; during it only VƯỢT NGƯỠNG is sent)
An item is pushed when its source's rule is on and either its level is at least the rule's minimum or the
item itself asks for it (capacity / backup keep the 90 % decision: item["push"]).
"""
import datetime as dt
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from .models import AppSetting

LEVEL_RANK = {"crit": 0, "bad": 1, "warn": 2}
LEVEL_LABEL = {"crit": "VƯỢT NGƯỠNG", "bad": "CẢNH BÁO trở lên", "warn": "THEO DÕI trở lên"}
# source → (label, default enabled, default min level)
SOURCES: Dict[str, Tuple[str, bool, str]] = {
    "capacity": ("Capacity (mốc 90 %)", True, "crit"),
    "backup": ("Backup (mốc 90 %)", True, "crit"),
    "datastore": ("Datastore", True, "bad"),
    "snapshot": ("Snapshot VM", True, "crit"),
    "alarm": ("Alarm vCenter", True, "crit"),
    "health": ("Sức khoẻ phần cứng host", True, "bad"),
    "storage": ("Storage (Pure / NetApp)", True, "bad"),
    "asset": ("Thiết bị Data Center (≥ 5 năm)", True, "crit"),
    "hardware": ("Phần cứng máy chủ (Dell OME)", True, "bad"),
    "config": ("Lệch cấu hình (baseline)", False, "bad"),
    "lifecycle": ("Vòng đời, VMSA, chứng chỉ TLS", True, "crit"),
}
VN_OFFSET = dt.timedelta(hours=7)                   # Asia/Ho_Chi_Minh, no DST
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class Rule:
    source: str
    enabled: bool
    min_level: str
    channels: Tuple[str, ...]                        # empty = every channel ticked "Nhận cảnh báo vận hành"

    def wants(self, item: Dict[str, Any]) -> bool:
        if not self.enabled:
            return False
        return bool(item.get("push")) or LEVEL_RANK.get(item.get("level"), 9) <= LEVEL_RANK[self.min_level]

    def to_channel(self, cid: str) -> bool:
        return not self.channels or cid in self.channels


@dataclass(frozen=True)
class Quiet:
    enabled: bool
    start: str
    end: str

    def active(self, now_utc: Optional[dt.datetime] = None) -> bool:
        if not self.enabled:
            return False
        local = ((now_utc or dt.datetime.utcnow()) + VN_OFFSET).strftime("%H:%M")
        if self.start == self.end:
            return False
        if self.start < self.end:                    # e.g. 12:00–13:30
            return self.start <= local < self.end
        return local >= self.start or local < self.end   # crosses midnight, e.g. 22:00–07:00


def _row(db: Session, key: str) -> Optional[str]:
    r = db.get(AppSetting, key)
    return r.value if r else None


def rules(db: Session) -> Dict[str, Rule]:
    out = {}
    for src, (_label, on, lvl) in SOURCES.items():
        raw = _row(db, f"alerts.{src}")
        if raw:
            parts = (raw.split("|") + ["", "", ""])[:3]
            enabled = parts[0] == "1"
            level = parts[1] if parts[1] in LEVEL_RANK else lvl
            chans = tuple(c for c in parts[2].split(",") if c)
            out[src] = Rule(src, enabled, level, chans)
        else:
            out[src] = Rule(src, on, lvl, ())
    return out


def quiet(db: Session) -> Quiet:
    raw = _row(db, "alerts.quiet")
    if not raw:
        return Quiet(False, "22:00", "07:00")
    parts = (raw.split("|") + ["", "", ""])[:3]
    return Quiet(parts[0] == "1", parts[1] if _HHMM.match(parts[1]) else "22:00", parts[2] if _HHMM.match(parts[2]) else "07:00")


def out(db: Session, channels: List[Any]) -> Dict[str, Any]:
    q = quiet(db)
    return {
        "rules": [{"source": s, "label": SOURCES[s][0], "enabled": r.enabled, "minLevel": r.min_level,
                   "channels": list(r.channels)} for s, r in rules(db).items()],
        "quiet": {"enabled": q.enabled, "start": q.start, "end": q.end, "activeNow": q.active()},
        "channels": [{"id": str(c.id), "name": c.name} for c in channels],
        "levels": [{"key": k, "label": v} for k, v in LEVEL_LABEL.items()],
    }


def save(db: Session, body: Dict[str, Any], channel_ids: List[str]) -> None:
    """Validate and store. Raises ValueError with a Vietnamese sentence."""
    valid = set(channel_ids)
    for r in body.get("rules") or []:
        src = r.get("source")
        if src not in SOURCES:
            raise ValueError("Nguồn cảnh báo không hợp lệ.")
        if r.get("minLevel") not in LEVEL_RANK:
            raise ValueError(f"{SOURCES[src][0]}: mức tối thiểu không hợp lệ.")
        chans = [str(c) for c in (r.get("channels") or [])]
        if any(c not in valid for c in chans):
            raise ValueError(f"{SOURCES[src][0]}: kênh không tồn tại hoặc chưa bật nhận cảnh báo vận hành.")
        value = f"{'1' if r.get('enabled') else '0'}|{r['minLevel']}|{','.join(sorted(set(chans)))}"
        if len(value) > 255:
            raise ValueError("Chọn quá nhiều kênh.")
        _put(db, f"alerts.{src}", value)
    q = body.get("quiet")
    if q is not None:
        start, end = str(q.get("start") or ""), str(q.get("end") or "")
        if not (_HHMM.match(start) and _HHMM.match(end)):
            raise ValueError("Giờ im lặng theo dạng HH:MM, vd 22:00 – 07:00.")
        _put(db, "alerts.quiet", f"{'1' if q.get('enabled') else '0'}|{start}|{end}")


def _put(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
