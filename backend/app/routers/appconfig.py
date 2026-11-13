"""App-wide configuration editable by admins: login-screen logo and feature (module) toggles."""
import base64
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..features import FEATURES, get_features
from ..models import AppSetting, ReportLogo, User
from ..schemas import LogoUpload
from ..security import get_current_user, require_admin
from ..timeutil import to_iso_utc
from .settings import DATA_URL_RE, MAX_LOGO_BYTES

router = APIRouter(tags=["app-config"])
LOGIN_LOGO_ID = 2          # report_logo row 1 = PDF cover logo, row 2 = login screen logo


@router.get("/api/public/branding")
def branding(db: Session = Depends(get_db)):
    """No sign-in needed: what the login screen shows."""
    row = db.get(ReportLogo, LOGIN_LOGO_ID)
    return {"loginLogo": f"data:{row.mime_type};base64,{row.data_base64}" if row else None}


@router.put("/api/settings/login-logo")
def upload_login_logo(payload: LogoUpload, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    match = DATA_URL_RE.match(payload.data_url.strip())
    if not match:
        raise HTTPException(400, "Định dạng ảnh không hợp lệ. Chỉ chấp nhận PNG, JPEG, SVG hoặc WEBP.")
    mime_type, b64 = match.group(1).lower(), match.group(2)
    if mime_type == "image/svg+xml":
        # Shown to everyone before sign-in: refuse SVG with scripts or external references.
        svg = base64.b64decode(b64, validate=True).decode("utf-8", "ignore").lower()
        if any(t in svg for t in ("<script", "javascript:", "onload=", "onerror=", "<foreignobject", "href=\"http", "href='http")):
            raise HTTPException(400, "Logo SVG chứa script hoặc liên kết ngoài — dùng PNG/WEBP hoặc SVG thuần.")
    try:
        if len(base64.b64decode(b64, validate=True)) > MAX_LOGO_BYTES:
            raise HTTPException(400, f"Kích thước logo vượt quá {MAX_LOGO_BYTES // (1024 * 1024)}MB cho phép.")
    except (ValueError, TypeError):
        raise HTTPException(400, "Không thể giải mã dữ liệu ảnh đã tải lên.")
    row = db.get(ReportLogo, LOGIN_LOGO_ID) or ReportLogo(id=LOGIN_LOGO_ID)
    row.mime_type, row.data_base64, row.filename = mime_type, b64, payload.filename
    db.merge(row)
    db.commit()
    return {"dataUrl": f"data:{mime_type};base64,{b64}", "filename": payload.filename}


@router.get("/api/settings/login-logo")
def get_login_logo(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(ReportLogo, LOGIN_LOGO_ID)
    if not row:
        return {"dataUrl": None, "filename": None, "uploadedAt": None}
    return {"dataUrl": f"data:{row.mime_type};base64,{row.data_base64}", "filename": row.filename,
            "uploadedAt": to_iso_utc(row.uploaded_at)}


@router.delete("/api/settings/login-logo")
def delete_login_logo(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    row = db.get(ReportLogo, LOGIN_LOGO_ID)
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True}


class FeaturesUpdate(BaseModel):
    features: Dict[str, bool]


def _features_out(db: Session):
    on = get_features(db)
    return {"features": on, "catalog": [{"key": k, **v, "enabled": on[k]} for k, v in FEATURES.items()]}


@router.get("/api/settings/features")
def list_features(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _features_out(db)


@router.put("/api/settings/features")
def update_features(body: FeaturesUpdate, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    unknown = set(body.features) - set(FEATURES)
    if unknown:
        raise HTTPException(400, f"Tính năng không tồn tại: {', '.join(sorted(unknown))}")
    for k, v in body.features.items():
        db.merge(AppSetting(key=f"feature.{k}", value="true" if v else "false"))
    db.commit()
    return _features_out(db)
