"""Vòng đời & bảo mật (ICO v2 G9b): EoGS ESXi / vCenter, VMSA chưa vá, chứng chỉ TLS. Report: everyone; tables, scan: admin."""
import json
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from ..database import get_db
from ..downloads import attachment
from ..lifecycle import service
from ..lifecycle.models import LifecycleEogs, VmsaAdvisory
from ..models import AuditLog, User
from ..security import get_current_user, require_admin
from ..shared.xlsx_io import XlsxError, read_upload

router = APIRouter(prefix="/api/lifecycle", tags=["lifecycle"])
TABLES = {"eogs": LifecycleEogs, "vmsa": VmsaAdvisory}


def _changed(db: Session, user: User, action: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    from .. import inbox
    db.add(AuditLog(username=user.username, action=action, detail=json.dumps(detail, ensure_ascii=False, default=str)))
    db.commit()
    inbox.invalidate()
    return service.report(db)


def _kind(kind: str) -> str:
    if kind not in TABLES:
        raise HTTPException(404, "Bảng không tồn tại.")
    return kind


@router.get("")
def report(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.report(db)


@router.post("/scan")
def scan_now(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Read every TLS certificate now (TLS handshake only), instead of waiting for the daily scan."""
    from .. import data_access
    from ..config import get_settings
    from ..lifecycle import tls
    hosts, _vms, _ = data_access.get_latest_combined(db, None)
    res = tls.scan(db, get_settings(), hosts)
    return _changed(db, user, "lifecycle.tls.scan", res)


@router.post("/import/{kind}")
async def import_table(kind: str, request: Request, file: UploadFile = File(...), user: User = Depends(require_admin),
                       db: Session = Depends(get_db)):
    """Replace the EoGS or VMSA table with the rows of an .xlsx (layout of the template)."""
    _kind(kind)
    data = await read_upload(request, file)
    try:
        rows = await run_in_threadpool(service.parse_sheet, data, kind)
    except (XlsxError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    n = service.replace_table(db, kind, rows, user.username)
    return _changed(db, user, f"lifecycle.{kind}.import", {"rows": n, "file": (file.filename or "")[:120]})


@router.post("/eogs")
def add_eogs(data: Dict[str, Any] = Body(...), user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Add or update one EoGS row (product + version line)."""
    try:
        r = service.clean_eogs(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    row = db.query(LifecycleEogs).filter_by(product=r["product"], version=r["version"]).first() or LifecycleEogs(product=r["product"], version=r["version"])
    row.eogs, row.eotg, row.note, row.updated_by = r["eogs"], r["eotg"], r["note"], user.username
    db.add(row)
    return _changed(db, user, "lifecycle.eogs.save", r)


@router.delete("/{kind}/{rid}")
def delete_row(kind: str, rid: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.get(TABLES[_kind(kind)], rid)
    if row is None:
        raise HTTPException(404, "Không có dòng này.")
    detail = {"product": row.product, "version": row.version, **({"vmsa": row.vmsa} if kind == "vmsa" else {})}
    db.delete(row)
    return _changed(db, user, f"lifecycle.{kind}.delete", detail)


@router.get("/template/{kind}.xlsx")
def template(kind: str, user: User = Depends(get_current_user)):
    return attachment(service.template(_kind(kind)), f"ICO_mau_{kind}.xlsx")


@router.get("/report.xlsx")
def report_xlsx(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from ..excel_lifecycle import build_xlsx
    return attachment(build_xlsx(service.report(db)), "ICO_Vong_doi_bao_mat.xlsx")


@router.get("/report.pdf")
def report_pdf(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from ..report_lifecycle import build_pdf
    return attachment(build_pdf(service.report(db), user.display_name or user.username), "ICO_Vong_doi_bao_mat.pdf")
