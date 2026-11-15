"""Quản lý thông tin → Chuẩn hoá BOM: upload a raw vendor BOM (Dell configurator, Cisco CCW, HPE,
generic Excel) → preview grouped by category with a per-configuration summary → professional Excel.
Stateless: nothing is stored; the browser sends the (possibly edited) preview back for the export."""
import datetime as dt
import io
import re
from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from .. import bom, excel_bom
from ..features import require_feature
from ..models import User
from ..security import get_current_user
from ..spend import files

router = APIRouter(prefix="/api/bom", tags=["bom"], dependencies=[Depends(require_feature("bom"))])
MAX_UPLOAD = 15 * 1024 * 1024
MAX_JSON = 8 * 1024 * 1024          # an edited preview of MAX_ITEMS lines


def _cap(request: Request, limit: int) -> None:
    try:
        n = int(request.headers.get("content-length") or 0)
    except ValueError:
        n = 0
    if n > limit:
        raise HTTPException(413, "Dữ liệu gửi lên quá lớn.")


@router.post("/parse")
async def parse(request: Request, file: UploadFile = File(...), user: User = Depends(get_current_user)):
    _cap(request, MAX_UPLOAD + 64 * 1024)
    data = bytearray()
    while chunk := await file.read(1024 * 1024):
        data.extend(chunk)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "File BOM vượt quá 15 MB.")
    try:
        files.detect_type(bytes(data), allowed=(files.XLSX,))
    except files.FileRejected:
        raise HTTPException(415, "Chỉ nhận file Excel .xlsx (lưu lại file .xls / .csv thành .xlsx trước).") from None
    try:
        out = await run_in_threadpool(bom.parse, bytes(data))      # CPU-bound: keep the event loop free
    except bom.BomError as exc:
        raise HTTPException(422, str(exc)) from None
    except (ValueError, ArithmeticError, TypeError):
        raise HTTPException(422, "File có số liệu không đọc được (số quá lớn / sai định dạng) — kiểm tra lại cột SL / giá.") from None
    out["layoutLabel"] = bom.LAYOUT_LABELS.get(out.get("layout"), out.get("layout"))
    out["fileName"] = re.sub(r"[\x00-\x1f]", "", file.filename or "")[:200]
    return out


def _clean_configs(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise HTTPException(400, "Không có cấu hình nào để xuất.")
    if len(raw) > bom.MAX_CONFIGS:
        raise HTTPException(413, f"Tối đa {bom.MAX_CONFIGS} cấu hình.")
    out, n_items = [], 0
    for c in raw:
        if not isinstance(c, dict):
            continue
        raw_items = c.get("items")
        items = [it for it in raw_items if isinstance(it, dict)][:1000] if isinstance(raw_items, list) else []
        n_items += len(items)
        if n_items > bom.MAX_ITEMS:
            raise HTTPException(413, f"Tối đa {bom.MAX_ITEMS} dòng hàng.")
        try:
            qty = max(1, min(100000, int(c.get("qty") or 1)))
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(400, "Số lượng cấu hình phải là số nguyên.") from None
        raw_sum = c.get("summary")
        summary = [[str(s[0])[:60], str(s[1])[:800]] for s in (raw_sum[:20] if isinstance(raw_sum, list) else [])
                   if isinstance(s, (list, tuple)) and len(s) == 2]
        out.append({"title": str(c.get("title") or "Cấu hình")[:200],
                    "category": c.get("category") if c.get("category") in bom.CATEGORY_LABELS else "other",
                    "qty": qty, "qtyMode": "total" if c.get("qtyMode") == "total" else "per_unit",
                    "priceBasis": "per_unit" if c.get("priceBasis") == "per_unit" else "total",
                    "summary": summary, "items": items})
    return out


@router.post("/summary")
def resummarise(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(get_current_user)):
    """Summary of one configuration after the user changed its category / quantity mode."""
    _cap(request, MAX_JSON)
    cfg = _clean_configs([data])[0]
    try:
        return {"summary": bom.resummarise(cfg)}
    except (ValueError, ArithmeticError, TypeError):
        raise HTTPException(422, "Cấu hình có số liệu không hợp lệ.") from None


@router.post("/export")
def export(request: Request, data: Dict[str, Any] = Body(...), user: User = Depends(get_current_user)):
    _cap(request, MAX_JSON)
    p = data.get("project") if isinstance(data.get("project"), dict) else {}
    vat = p.get("vatPct")
    project = {"name": str(p.get("name") or "")[:120], "customer": str(p.get("customer") or "")[:120],
               "preparedBy": str(p.get("preparedBy") or user.username)[:80], "note": str(p.get("note") or "")[:1000],
               "currency": "USD" if p.get("currency") == "USD" else "VND",
               "vatPct": vat if isinstance(vat, (int, float)) and not isinstance(vat, bool) and 0 < vat <= 20 else None,
               "hideEmpty": p.get("hideEmpty") is not False}
    configs = _clean_configs(data.get("configs"))
    if not any(c["items"] for c in configs):
        raise HTTPException(400, "Không có dòng hàng nào để xuất.")
    content = excel_bom.build_bom_xlsx({"project": project, "configs": configs})
    slug = re.sub(r"[^A-Za-z0-9]+", "_", bom.norm_text(project["name"] or "bom")).strip("_")[:40] or "bom"
    name = f"BOM_{slug}_{dt.date.today():%Y%m%d}.xlsx"
    return StreamingResponse(io.BytesIO(content), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "private, no-store"})
