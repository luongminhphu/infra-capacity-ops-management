"""What an uploaded file really is, from its bytes (never its name): PDF, .docx, .xlsx — Office files
checked for zip structure, path traversal and macros. Shared by every upload (Chi phí, BOM…)."""
import io
import zipfile

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


def office_type(data: bytes) -> str:
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
        mime = office_type(data)
    else:
        raise FileRejected(415, "Định dạng không được hỗ trợ — chỉ nhận PDF, .docx, .xlsx.")
    if mime not in allowed:
        raise FileRejected(415, "Định dạng file không được phép ở đây (" + ", ".join(EXT[m] for m in allowed) + ").")
    return mime


