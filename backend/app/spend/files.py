"""Uploaded files: content checks and storage on the volume (SPEND_FILES_DIR).

* Only PDF, .docx and .xlsx; the type is decided from the CONTENT, not the file name.
* Office files must be a sane ZIP: required parts present, no macros, bounded number of
  entries and uncompressed size (zip bomb), no absolute / ".." paths.
* Files are stored under a random name; the user's file name is metadata only.
* Write to tmp/ then rename after the DB row is flushed, so a failed request leaves no
  half-written file under its final name.
"""
import hashlib
import io
import os
import uuid
import zipfile
from pathlib import Path
from typing import Optional, Tuple

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..config import get_settings
from .models import SpendFile

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EXT = {PDF: ".pdf", DOCX: ".docx", XLSX: ".xlsx"}
MAX_ZIP_ENTRIES = 2000
MAX_UNZIPPED = 200 * 1024 * 1024


class FileRejected(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _office_type(data: bytes) -> str:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
    except (zipfile.BadZipFile, ValueError) as exc:
        raise FileRejected(415, "File Office bị hỏng hoặc không phải .docx / .xlsx thật.") from exc
    if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_UNZIPPED:
        raise FileRejected(413, "File Office có cấu trúc bất thường (quá nhiều thành phần hoặc quá lớn khi giải nén).")
    names = {i.filename for i in infos}
    for n in names:
        if n.startswith(("/", "\\")) or ".." in n.replace("\\", "/").split("/"):
            raise FileRejected(415, "File Office chứa đường dẫn không hợp lệ.")
        if n.lower().endswith("vbaproject.bin"):
            raise FileRejected(415, "File chứa macro — chỉ nhận .docx / .xlsx không có macro.")
    if "[Content_Types].xml" not in names:
        raise FileRejected(415, "Không phải file Office Open XML (.docx / .xlsx).")
    ctypes = zf.read("[Content_Types].xml")[:200_000].decode("utf-8", "ignore").lower()
    if "macroenabled" in ctypes:
        raise FileRejected(415, "File chứa macro — chỉ nhận .docx / .xlsx không có macro.")
    if "word/document.xml" in names:
        return DOCX
    if "xl/workbook.xml" in names:
        return XLSX
    raise FileRejected(415, "Chỉ nhận .docx hoặc .xlsx (file Office khác không được hỗ trợ).")


def detect_type(data: bytes, allowed=(PDF, DOCX, XLSX)) -> str:
    if data[:5] == b"%PDF-":
        mime = PDF
    elif data[:4] == b"PK\x03\x04":
        mime = _office_type(data)
    else:
        raise FileRejected(415, "Định dạng không được hỗ trợ — chỉ nhận PDF, .docx, .xlsx.")
    if mime not in allowed:
        raise FileRejected(415, "Định dạng file không được phép ở đây (" + ", ".join(EXT[m] for m in allowed) + ").")
    return mime


def clean_name(name: Optional[str], mime: str) -> str:
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    base = "".join(ch for ch in base if ch.isprintable() and ch not in '<>:"|?*')[:200] or "file"
    stem, ext = os.path.splitext(base)
    return (stem or "file") + EXT[mime] if ext.lower() != EXT[mime] else base


def files_dir() -> Path:
    return Path(get_settings().spend_files_dir)


def path_of(row: SpendFile) -> Path:
    # storage_key is generated here (uuid hex), never user input.
    return files_dir() / row.storage_key[:2] / row.storage_key


async def read_upload(upload: UploadFile) -> bytes:
    limit = get_settings().spend_max_upload_mb * 1024 * 1024
    buf = bytearray()
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > limit:
            raise HTTPException(413, f"File vượt quá {get_settings().spend_max_upload_mb} MB.")
    if not buf:
        raise HTTPException(400, "File rỗng.")
    return bytes(buf)


def store(db: Session, data: bytes, filename: Optional[str], username: str,
          allowed=(PDF, DOCX, XLSX)) -> Tuple[SpendFile, Path, Path]:
    """Validate, write to a temp file and add the DB row (flushed). The caller commits, then
    calls `finalize(tmp, final)`; on error it calls `discard(tmp)`."""
    try:
        mime = detect_type(data, allowed)
    except FileRejected as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    key = uuid.uuid4().hex
    row = SpendFile(storage_key=key, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), mime=mime,
                    original_name=clean_name(filename, mime), uploaded_by=username)
    final = path_of(row)
    tmp = files_dir() / "tmp" / key
    try:
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(data)
    except OSError as exc:
        raise HTTPException(507, "Không ghi được file lên volume lưu trữ (đầy hoặc chưa gắn volume).") from exc
    db.add(row)
    db.flush()
    return row, tmp, final


def finalize(tmp: Path, final: Path) -> None:
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, final)


def discard(tmp: Optional[Path]) -> None:
    if tmp is not None:
        try:
            tmp.unlink()
        except OSError:
            pass


def remove(row: SpendFile) -> None:
    """Delete the bytes on disk (already-missing file counts as removed)."""
    try:
        path_of(row).unlink()
    except FileNotFoundError:
        pass


def disk_usage() -> dict:
    d = files_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        st = os.statvfs(d)
        return {"path": str(d), "freeBytes": st.f_bavail * st.f_frsize, "totalBytes": st.f_blocks * st.f_frsize}
    except OSError:
        return {"path": str(d), "freeBytes": None, "totalBytes": None}
