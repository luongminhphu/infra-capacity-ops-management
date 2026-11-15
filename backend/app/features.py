"""Modules that can be shown / hidden in Settings (stored in app_settings as feature.<key>)."""
from typing import Dict

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from .database import get_db
from .models import AppSetting

FEATURES = {
    "backup": {"label": "Backup", "desc": "Giám sát, tuân thủ lịch và báo cáo backup."},
    "license": {"label": "License", "desc": "Đánh giá license, What-if license và snapshot license."},
    "sizing": {"label": "Sizing máy chủ", "desc": "Tính số host cần mua cho dự án mới."},
    "planning": {"label": "Quy hoạch tài nguyên", "desc": "Dự phóng tăng trưởng và thời điểm bổ sung node."},
    "whatif": {"label": "What-if capacity", "desc": "Mô phỏng thêm / nâng cấp host, thêm / resize VM."},
    "bom": {"label": "Chuẩn hoá BOM", "desc": "Chuẩn hoá BOM Dell / HPE / Cisco thành file Excel trình bày chuyên nghiệp."},
    "spend": {"label": "Chi phí & Hợp đồng IT", "desc": "Opex, nhắc hạn gia hạn, Capex, hợp đồng và tờ trình."},
}


def get_features(db: Session) -> Dict[str, bool]:
    rows = {r.key: r.value for r in db.query(AppSetting).filter(AppSetting.key.like("feature.%")).all()}
    return {k: rows.get(f"feature.{k}", "true") != "false" for k in FEATURES}


def is_on(features: Dict[str, bool], key: str) -> bool:
    """One way to read a flag from get_features(): unknown keys are on."""
    return features.get(key) is not False


def ensure_feature(db: Session, key: str) -> None:
    """For code paths outside a gated router (services, AI tools): 404 when the module is off,
    exactly like require_feature."""
    if not is_on(get_features(db), key):
        raise HTTPException(404, f"Tính năng {FEATURES[key]['label']} đang tắt (Cài đặt → Tính năng).")


def require_feature(key: str):
    """Router dependency: a hidden module's API answers 404, like the page in the menu."""
    def _dep(db: Session = Depends(get_db)) -> None:
        ensure_feature(db, key)
    return _dep
