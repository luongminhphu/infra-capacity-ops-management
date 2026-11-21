"""ServiceDesk Plus On-Premises REST API v3 — read-only client and parsers (ICO v2 G7a).

GET only (the HTTP client refuses every other method before anything leaves). Headers:
  authtoken: <technician key>                         (secrets_box; never logged, never in an error message)
  Accept: application/vnd.manageengine.sdp.v3+json
Calls:
  GET /api/v3/requests?input_data={"list_info": {row_count, start_index, sort_field, sort_order, search_criteria}}
      — the requests of the configured templates updated after the cursor, oldest update first;
  GET /api/v3/requests/{id} — description (HTML), requester, status, times (ms epoch), approval_status, udf_fields.
Sorted by last_updated_time (not created_time): the cursor is last_updated_time, so a request edited later is read again
and an incremental read never skips one. Field names follow the v3 documentation; a build that answers with other names
is read through the tolerant getters below (`_name`, `_ms`) — check the parsers against a real answer when connecting HSC's
server. Rate: at most rate_per_min calls per minute; a failed call is retried twice with backoff (not on 401 / 403 / 404).
"""
import datetime as dt
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from ..storage.http import JsonClient
from . import ItsmConfig, ItsmError

logger = logging.getLogger(__name__)

ACCEPT = "application/vnd.manageengine.sdp.v3+json"
PAGE = 100
MAX_DESCRIPTION = 64 * 1024
RETRIES = 2
_NO_RETRY = ("(401)", "(403)", "(404)")       # JsonClient words these errors with the status: retrying cannot help


def _get_only(method: str, path: str) -> bool:
    return False                               # every non-GET call is refused (JsonClient checks before sending)


def _ms(v: Any) -> Optional[int]:
    """SDP time: {"value": "1696123456789", "display_value": …} or the bare ms value."""
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return int(str(v).strip()) if v not in (None, "") else None
    except ValueError:
        return None


def ms_to_dt(ms: Optional[int]) -> Optional[dt.datetime]:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).replace(tzinfo=None) if ms else None


def dt_to_ms(d: dt.date) -> int:
    t = d if isinstance(d, dt.datetime) else dt.datetime(d.year, d.month, d.day)
    return int(t.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def _name(v: Any) -> Optional[str]:
    """{"name": …} objects (status, template, department, approval_status) or plain text."""
    if isinstance(v, dict):
        v = v.get("name") or v.get("display_value")
    t = str(v).strip() if v is not None else ""
    return t or None


_SCRIPT = re.compile(r"<(script|style|iframe|object|embed)\b.*?</\1\s*>", re.I | re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_EVENT_ATTR = re.compile(r"\s+on[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
_JS_URL = re.compile(r"(href|src)\s*=\s*([\"']?)\s*javascript:[^\"'>\s]*\2", re.I)


def clean_html(html: Optional[str]) -> Optional[str]:
    """Stored for re-parsing, never rendered as HTML: still drop active content (scripts, handlers, javascript: links) and
    comments, keep the tables (rows / cells / colspan) the parser needs; at most MAX_DESCRIPTION bytes."""
    if not html:
        return None
    s = _JS_URL.sub("", _EVENT_ATTR.sub("", _COMMENT.sub("", _SCRIPT.sub("", str(html)))))
    raw = s.encode("utf-8")[:MAX_DESCRIPTION]
    return raw.decode("utf-8", "ignore")


KIND_WORDS = (("decommission", ("thu hồi", "huỷ vm", "hủy vm", "xoá vm", "xóa vm", "decommission", "delete vm")),
              ("resize", ("nâng cấp", "tăng", "mở rộng", "bổ sung tài nguyên", "resize", "upgrade", "extend")),
              ("new_vm", ("tạo", "cấp mới", "khởi tạo", "new vm", "create vm", "máy ảo mới", "cấp vm")))


def kind_of(template: Optional[str], subject: Optional[str]) -> str:
    """new_vm / resize / decommission / other from the template, then the subject (G7b refines it from the body)."""
    for text in (template, subject):
        t = (text or "").lower()
        for kind, words in KIND_WORDS:
            if any(w in t for w in words):
                return kind
    return "other"


def parse_request(r: Dict[str, Any]) -> Dict[str, Any]:
    """One request (detail answer) → the itsm_request columns."""
    req = r.get("requester") or {}
    approval = r.get("approval_status")
    template, subject = _name(r.get("template")), (str(r.get("subject") or "").strip()[:500] or None)
    udf = r.get("udf_fields") if isinstance(r.get("udf_fields"), dict) else {}
    return {"reqId": str(r.get("id") or "").strip()[:32], "template": template, "subject": subject,
            "kind": kind_of(template, subject),
            "requesterName": _name(req.get("name") if isinstance(req, dict) else req),
            "requesterEmail": (str(req.get("email_id") or "").strip().lower()[:255] or None) if isinstance(req, dict) else None,
            "department": _name(r.get("department") or (req.get("department") if isinstance(req, dict) else None)),
            "status": _name(r.get("status")), "approvalStatus": _name(approval),
            "createdAt": ms_to_dt(_ms(r.get("created_time"))), "resolvedAt": ms_to_dt(_ms(r.get("resolved_time"))),
            "approvedAt": ms_to_dt(_ms((approval or {}).get("approved_time") if isinstance(approval, dict) else None)),
            "updatedMs": _ms(r.get("last_updated_time")), "description": clean_html(r.get("description")),
            "udf": {str(k)[:64]: (v if isinstance(v, (str, int, float, bool)) or v is None else _name(v)) for k, v in udf.items()}}


def list_params(templates: List[str], after_ms: Optional[int], start_index: int) -> Dict[str, str]:
    crit: List[Dict[str, Any]] = []
    if templates:
        crit.append({"field": "template.name", "condition": "is", "values": list(templates)})
    if after_ms is not None:
        crit.append({"field": "last_updated_time", "condition": "greater than", "value": str(after_ms),
                     **({"logical_operator": "AND"} if crit else {})})
    info = {"row_count": PAGE, "start_index": start_index, "sort_field": "last_updated_time", "sort_order": "asc",
            "get_total_count": False, **({"search_criteria": crit} if crit else {})}
    return {"input_data": json.dumps({"list_info": info}, ensure_ascii=False)}


class SdpClient:
    def __init__(self, cfg: ItsmConfig, sleep=time.sleep, clock=time.monotonic):
        if not cfg.api_key:
            raise ItsmError("Chưa nhập technician key của ServiceDesk Plus.")
        self.cfg = cfg
        self.http = JsonClient(cfg, f"https://{cfg.host}:{cfg.port}", product="ServiceDesk Plus", api_hint="ServiceDesk Plus (API v3)",
                               allow=_get_only)
        self.http.headers.update({"authtoken": cfg.api_key, "Accept": ACCEPT})
        self.sleep, self.clock = sleep, clock
        self.gap = 60.0 / max(1, cfg.rate_per_min)
        self._last = None

    def _get(self, path: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        for attempt in range(RETRIES + 1):
            if self._last is not None:                       # rate limit: spread the calls over the minute
                wait = self.gap - (self.clock() - self._last)
                if wait > 0:
                    self.sleep(wait)
            self._last = self.clock()
            try:
                res = self.http.get(path, params)
            except ItsmError as exc:
                if attempt == RETRIES or any(c in str(exc) for c in _NO_RETRY):
                    raise
                logger.info("SDP %s failed (%s), retry %d", path, exc, attempt + 1)
                self.sleep(2 ** attempt)
                continue
            status = res.get("response_status") if isinstance(res, dict) else None
            status = status[0] if isinstance(status, list) and status else status
            if isinstance(status, dict) and str(status.get("status", "success")).lower() != "success":
                msgs = "; ".join(str(m.get("message") or m) for m in status.get("messages") or [] if m)[:300]
                raise ItsmError(f"ServiceDesk Plus báo lỗi ({status.get('status_code')}): {msgs or 'không rõ'}")
            return res
        raise ItsmError("ServiceDesk Plus không trả lời.")       # unreachable (the loop returns or raises)

    def list_page(self, after_ms: Optional[int], start_index: int) -> Tuple[List[Dict[str, Any]], bool]:
        res = self._get("/api/v3/requests", list_params(self.cfg.templates, after_ms, start_index))
        rows = res.get("requests") or []
        more = bool((res.get("list_info") or {}).get("has_more_rows"))
        return [r for r in rows if isinstance(r, dict)], more

    def get(self, req_id: str) -> Dict[str, Any]:
        if not re.fullmatch(r"\d{1,20}", str(req_id)):
            raise ItsmError(f"Mã yêu cầu không hợp lệ: {str(req_id)[:20]}")
        return self._get(f"/api/v3/requests/{req_id}").get("request") or {}


def client(cfg: ItsmConfig):
    if cfg.demo:
        from .demo_generator import DemoSdp
        return DemoSdp(cfg)
    return SdpClient(cfg)


def test(cfg: ItsmConfig) -> str:
    c = SdpClient(cfg)
    c.cfg.timeout = min(cfg.timeout, 30)
    rows, _ = c.list_page(None, 1)
    names = ", ".join(cfg.templates) or "mọi template"
    return f"Kết nối thành công tới ServiceDesk Plus {cfg.host} (đọc được {len(rows)} yêu cầu đầu tiên của {names})."
