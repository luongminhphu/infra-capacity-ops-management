"""Reading uploaded Office files safely — the one way every module opens an .xlsx.

Guards: zip-bomb (declared unzipped size of each part and in total, number of entries), read-only
streaming mode, and bounded sheets / rows / columns. Callers get plain lists of cell values."""
import io
import zipfile
from typing import Any, List, Tuple

MAX_PART = 15 * 1024 * 1024        # one XML part (sharedStrings.xml, sheet1.xml…) once unzipped
MAX_TOTAL = 60 * 1024 * 1024       # whole package once unzipped
MAX_ENTRIES = 2000


async def read_upload(request, file, max_bytes: int = 10 * 1024 * 1024) -> bytes:
    """Bytes of an uploaded .xlsx (FastAPI Request + UploadFile): size cap before and while reading, content checked as xlsx.
    Raises HTTPException 413 / 415 with a Vietnamese message."""
    from fastapi import HTTPException
    from .filetype import XLSX, FileRejected, detect_type
    mb = max_bytes // (1024 * 1024)
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > max_bytes + 64 * 1024:
        raise HTTPException(413, f"File quá lớn (tối đa {mb} MB).")
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(413, f"File quá lớn (tối đa {mb} MB).")
    try:
        detect_type(data, allowed=(XLSX,))
    except FileRejected as exc:
        raise HTTPException(exc.status, f"{exc} File .xls cũ: mở bằng Excel rồi lưu lại dạng .xlsx.") from None
    return data


class XlsxError(ValueError):
    """The file cannot be read; the message is safe to show."""


def guard_zip(data: bytes) -> None:
    """Refuse Office files (xlsx / docx) whose parts would blow up memory when parsed. Uses the sizes
    declared in the zip directory; zipfile stops reading at the declared size, so forging them does
    not get past this check."""
    try:
        infos = zipfile.ZipFile(io.BytesIO(data)).infolist()
    except zipfile.BadZipFile:
        raise XlsxError("File không phải Office Open XML (.xlsx / .docx) hợp lệ.") from None
    if len(infos) > MAX_ENTRIES or sum(i.file_size for i in infos) > MAX_TOTAL or any(i.file_size > MAX_PART for i in infos):
        raise XlsxError("File quá lớn khi giải nén.")


def open_workbook(data: bytes, data_only: bool = True):
    """Read-only workbook after the zip guard. Raises XlsxError."""
    from openpyxl import load_workbook
    guard_zip(data)
    try:
        return load_workbook(io.BytesIO(data), data_only=data_only, read_only=True)
    except XlsxError:
        raise
    except Exception as exc:  # noqa: BLE001 — malformed workbook
        raise XlsxError(f"Không đọc được file Excel ({type(exc).__name__}).") from None


def _trim(row) -> List[Any]:
    """Read-only mode pads every row to max_col with None: drop the empty tail."""
    r = list(row)
    while r and r[-1] is None:
        r.pop()
    return r


def read_rows(data: bytes, max_sheets: int = 10, max_rows: int = 5000, max_cols: int = 40,
              data_only: bool = True) -> List[Tuple[str, List[List[Any]]]]:
    """[(sheet title, rows)] with every row a list of cell values, bounded. Raises XlsxError."""
    wb = open_workbook(data, data_only=data_only)
    try:
        return [(ws.title, [_trim(r) for r in ws.iter_rows(max_row=max_rows, max_col=max_cols, values_only=True)])
                for ws in wb.worksheets[:max_sheets]]
    except Exception as exc:  # noqa: BLE001
        raise XlsxError(f"Không đọc được file Excel ({type(exc).__name__}).") from None
    finally:
        wb.close()
