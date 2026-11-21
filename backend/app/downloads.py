"""One way to answer a file download (PDF / Excel / e-mail draft / zip) from any router.

attachment(content, filename) → Response with the media type from the extension, an ASCII `filename=` plus the
RFC 5987 `filename*=` (Vietnamese names survive; the web's uiDownload reads it) and `Cache-Control: private, no-store`
(reports hold internal data: never cached by a proxy)."""
import datetime as dt
import re
from typing import Optional
from urllib.parse import quote

from fastapi.responses import Response

from .shared.filetype import XLSX

MEDIA = {"pdf": "application/pdf", "xlsx": XLSX, "zip": "application/zip", "eml": "message/rfc822", "csv": "text/csv; charset=utf-8"}


def stamp() -> str:
    """Date part of a report file name, e.g. vm-theo-he-thong-20261121.pdf."""
    return f"{dt.datetime.now():%Y%m%d}"


def attachment(content: bytes, filename: str, media: Optional[str] = None) -> Response:
    ascii_name = re.sub(r'[^A-Za-z0-9._-]+', "_", filename) or "download"
    media = media or MEDIA.get(filename.rsplit(".", 1)[-1].lower(), "application/octet-stream")
    return Response(content, media_type=media, headers={
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}",
        "Cache-Control": "private, no-store"})
