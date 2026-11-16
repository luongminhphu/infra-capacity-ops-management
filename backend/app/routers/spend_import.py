"""Chi phí & Hợp đồng — nhập Opex / Capex từ file Excel (spend/importer.py).

GET  /api/spend/import/template.xlsx?kind=opex|capex  — file mẫu có hướng dẫn và danh mục
POST /api/spend/import/preview?kind=…                 — kiểm tra, không ghi gì
POST /api/spend/import/commit?kind=…&skipErrors=…     — kiểm tra lại rồi ghi trong một giao dịch
Quyền: Biên tập. File .xlsx tối đa 10 MB, 2000 dòng."""
import datetime as dt
import io

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..features import require_feature
from ..models import User
from ..shared.filetype import XLSX, FileRejected, detect_type
from ..spend import importer, settings as spend_settings
from ..spend.models import SpendCategory, SpendCostCenter
from ..spend.roles import require_spend

router = APIRouter(prefix="/api/spend/import", tags=["spend-import"], dependencies=[Depends(require_feature("spend"))])
MAX_BYTES = 10 * 1024 * 1024
KIND = Query(..., pattern="^(opex|capex)$")


async def _read(request: Request, file: UploadFile) -> bytes:
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_BYTES + 64 * 1024:
        raise HTTPException(413, "File quá lớn (tối đa 10 MB).")
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "File quá lớn (tối đa 10 MB).")
    try:
        detect_type(data, allowed=(XLSX,))
    except FileRejected as exc:
        raise HTTPException(exc.status, f"{exc} File .xls cũ: mở bằng Excel rồi lưu lại dạng .xlsx.")
    return data


@router.post("/preview")
async def preview(request: Request, kind: str = KIND, file: UploadFile = File(...),
                  user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    data = await _read(request, file)

    def _run():   # every DB call on one worker thread, none on the event loop (audit BEQ-08)
        return importer.preview(db, kind, data, spend_settings.load(db), user)
    try:
        pv = await run_in_threadpool(_run)
    except importer.ImportError_ as exc:
        raise HTTPException(422, str(exc))
    return importer.public_preview(pv, kind)


@router.post("/commit")
async def commit(request: Request, kind: str = KIND, skipErrors: bool = False, file: UploadFile = File(...),
                 user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    data = await _read(request, file)

    def _run():   # load settings, write, commit / rollback on the same worker thread (audit BEQ-08)
        try:
            out = importer.commit(db, kind, data, spend_settings.load(db), user, request, skipErrors)
            db.commit()
            return out
        except BaseException:
            db.rollback()
            raise
    try:
        return await run_in_threadpool(_run)
    except importer.ImportError_ as exc:
        raise HTTPException(422, str(exc))
    except IntegrityError as exc:
        raise HTTPException(409, "Dữ liệu vừa bị thay đổi bởi thao tác khác — kiểm tra lại rồi nhập lại.") from exc


@router.get("/template.xlsx")
def template(kind: str = KIND, user: User = Depends(require_spend("editor")), db: Session = Depends(get_db)):
    from openpyxl import Workbook
    from openpyxl.worksheet.datavalidation import DataValidation
    from ..excel_style import _section, _sheet_title, _widths, _write_rows

    cats = [c.name for c in db.query(SpendCategory).filter(SpendCategory.active.is_(True)).order_by(SpendCategory.sort_order)]
    ccs = [c.code for c in db.query(SpendCostCenter).filter(SpendCostCenter.active.is_(True)).order_by(SpendCostCenter.code)]
    y = dt.date.today().year
    wb = Workbook()
    ws = wb.active
    if kind == "opex":
        ws.title = "Opex"
        header = [importer.OPEX_COLUMNS[f][0] for f in importer.OPEX_COLUMNS]
        ex = {f: None for f in importer.OPEX_COLUMNS}
        rows = []
        for vals in ({"category": cats[0] if cats else "", "title": "Bảo hành Dell R750 cụm PROD", "vendor": "Dell Việt Nam",
                      "contractRef": "HD-2024-015", "startDate": dt.date(y - 1, 7, 1), "endDate": dt.date(y, 6, 30), "cycle": "Hằng năm",
                      "amountNet": 480000000, "vatPct": 10, "owner": "Nguyễn Văn A", "serials": "SN001, SN002"},
                     {"category": cats[0] if cats else "", "title": "Bảo hành Dell R750 cụm PROD", "vendor": "Dell Việt Nam",
                      "startDate": dt.date(y, 7, 1), "endDate": dt.date(y + 1, 6, 30), "cycle": "Hằng năm", "amountNet": 500000000,
                      "vatPct": 10, "owner": "Nguyễn Văn A", "description": "Kỳ tiếp theo: tự liên kết “Đã gia hạn” với dòng trên"}):
            rows.append([vals.get(f, ex[f]) for f in importer.OPEX_COLUMNS])
        widths = {"A": 14, "B": 24, "C": 36, "D": 24, "E": 16, "F": 12, "G": 14, "H": 14, "I": 12, "J": 13, "K": 18, "L": 8,
                  "M": 18, "N": 10, "O": 14, "P": 20, "Q": 22, "R": 16, "S": 20, "T": 22, "U": 30}
        lists = {"category": cats, "cycle": ["Hằng năm", "Hằng quý", "Hằng tháng", "Một lần"],
                 "status": ["Dự kiến", "Đang hiệu lực", "Đã gia hạn", "Ngừng"], "costCenter": ccs}
        cols = list(importer.OPEX_COLUMNS)
        notes = [
            "Bắt buộc: Loại chi phí, Nội dung dịch vụ, Nhà cung cấp, Ngày bắt đầu, Số tiền (trước hoặc sau VAT).",
            "Ngày kết thúc để trống thì dùng Thời hạn (tháng); cả hai trống thì tính 1 năm.",
            "Trạng thái để trống: kỳ có kỳ tiếp theo trong file → Đã gia hạn (tự liên kết); bắt đầu sau hôm nay → Dự kiến; còn lại → Đang hiệu lực.",
            "Dòng trùng (cùng nội dung + nhà cung cấp + ngày bắt đầu, hoặc trùng Mã Opex đã có) được bỏ qua — nhập lại file không tạo trùng.",
            "Nhà cung cấp chưa có sẽ được tạo; tên gần giống (khác dấu / hoa thường / tên viết tắt đã khai báo) được gộp.",
            "Có thể dùng file đang quản lý: chỉ cần dòng tiêu đề có các cột tương ứng (tên cột tiếng Việt hoặc tiếng Anh).",
        ]
    else:
        ws.title = "Capex"
        header = [importer.CAPEX_COLUMNS[f][0] for f in importer.CAPEX_COLUMNS]
        rows = []
        for vals in ({"project": "Mở rộng cụm PROD", "fiscalYear": y, "goLive": dt.date(y, 6, 15), "projectStatus": "Đã duyệt",
                      "owner": "Trần B", "category": cats[0] if cats else "", "item": "4 máy chủ Dell R760", "vendor": "Dell Việt Nam",
                      "quantity": 4, "amountNet": 2000000000, "vatPct": 10, "startDate": dt.date(y, 6, 15), "coverageYears": 3},
                     {"category": cats[0] if cats else "", "item": "Dịch vụ triển khai", "vendor": "FPT", "quantity": 1,
                      "amountNet": 150000000, "vatPct": 8}):
            rows.append([vals.get(f) for f in importer.CAPEX_COLUMNS])
        widths = {"A": 28, "B": 10, "C": 14, "D": 16, "E": 18, "F": 14, "G": 24, "H": 32, "I": 22, "J": 9, "K": 18, "L": 8,
                  "M": 18, "N": 16, "O": 14, "P": 10, "Q": 16}
        lists = {"category": cats, "projectStatus": ["Nháp", "Đã duyệt", "Đang triển khai", "Đã đóng"], "costCenter": ccs}
        cols = list(importer.CAPEX_COLUMNS)
        notes = [
            "Mỗi dòng là một hạng mục; các dòng cùng Tên dự án (+ Năm tài chính) gộp thành một dự án. Ô Tên dự án để trống = dự án của dòng trên.",
            "Bắt buộc: Tên dự án, Loại chi phí, Hạng mục, Số tiền; Năm tài chính hoặc Ngày go-live.",
            "Trạng thái dự án để trống = Nháp. Dự án Đã duyệt / Đang triển khai / Đã đóng sẽ tự sinh Opex gia hạn dự kiến cho hạng mục có số năm bảo hành"
            " — nếu đã nhập các Opex đó từ file Opex, để Số năm bảo hành = 0 để tránh trùng.",
            "Dự án đã có (cùng tên, cùng năm tài chính) được bỏ qua — nhập lại file không tạo trùng.",
        ]
    _widths(ws, widths)
    last = _write_rows(ws, 1, header, rows, {cols.index(f) + 1: "DD/MM/YYYY" for f in ("startDate", "endDate", "goLive") if f in cols})
    from openpyxl.utils import get_column_letter
    for f in ("amountNet", "amountGross", "renewalAmount"):
        if f in cols:
            for r in range(2, last + 1):
                ws.cell(row=r, column=cols.index(f) + 1).number_format = "#,##0"
    # Lists live on a hidden sheet (long category lists do not fit in an inline validation).
    ref = wb.create_sheet("Danh mục")
    for j, (f, vals) in enumerate(lists.items(), 1):
        if f not in cols or not vals:
            continue
        ref.cell(row=1, column=j, value=f)
        for i, v in enumerate(vals, 2):
            ref.cell(row=i, column=j, value=v)
        letter = get_column_letter(j)
        dv = DataValidation(type="list", formula1=f"='Danh mục'!${letter}$2:${letter}${len(vals) + 1}", allow_blank=True)
        col = get_column_letter(cols.index(f) + 1)
        dv.add(f"{col}2:{col}2001")
        ws.add_data_validation(dv)
    ref.sheet_state = "hidden"
    ws.freeze_panes = "A2"
    g = wb.create_sheet("Hướng dẫn")
    _sheet_title(g, f"Hướng dẫn nhập {'Opex' if kind == 'opex' else 'Capex'}", "Ngân sách & chi phí → Nhập Excel", 1)
    _widths(g, {"A": 130})
    row = _section(g, 4, "Quy tắc", 1)
    for i, n in enumerate(notes):
        g.cell(row=row + i, column=1, value=f"• {n}")
    buf = io.BytesIO()
    wb.save(buf)
    return Response(content=buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="mau-nhap-{kind}.xlsx"', "Cache-Control": "no-store"})
