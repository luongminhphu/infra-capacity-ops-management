"""Dell OpenManage Enterprise 4.x REST — read-only client and parsers.

Session: POST /api/SessionService/Sessions {"UserName", "Password", "SessionType": "API"} → header X-Auth-Token
(+ body "Id") → GETs → DELETE /api/SessionService/Sessions('<Id>'). Reads:
  * /api/DeviceService/Devices — OData pages ($top / $skip, follow @odata.nextLink); servers only (Type 1000);
  * /api/DeviceService/Devices(<Id>)/SubSystemHealth — only for devices whose rollup Status is not normal
    (a normal rollup means every subsystem is normal, so healthy servers cost no extra call).
Field names follow the OME 4.x API guide; where a real appliance answers differently, the parser accepts both
(e.g. RollupStatus as string or number, Identifier vs DeviceServiceTag) — see tests/fixtures/ome_*.json.

Status codes (OME): 1000 normal → ok, 3000 warning → bad, 4000 critical → crit, 2000 unknown / 5000 no status /
anything else → warn with the reason "không có dữ liệu" (ICO's 4 levels: ok / warn / bad / crit).
"""
import datetime as dt
import logging
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from ..shared.identity import norm_ip, norm_serial
from ..storage.http import JsonClient
from . import HardwareConfig, HardwareError

logger = logging.getLogger(__name__)

SESSIONS = "/api/SessionService/Sessions"
_SESSION_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_DELETE_SESSION = re.compile(r"^/api/SessionService/Sessions\('[A-Za-z0-9-]{1,64}'\)$")
DEVICES = "/api/DeviceService/Devices"
PAGE = 200
MAX_PAGES = 100                  # 20 000 devices
MAX_HEALTH_CALLS = 500
TYPE_SERVER = 1000

STATUS_LEVEL = {1000: "ok", 3000: "bad", 4000: "crit"}
STATUS_LABEL = {1000: "Bình thường", 2000: "Không xác định", 3000: "Cảnh báo", 4000: "Nghiêm trọng", 5000: "Không có trạng thái"}
NO_DATA = "không có dữ liệu"
POWER_STATE = {17: "on", 18: "off", 20: "powering_on", 21: "powering_off"}


def allow(method: str, path: str) -> bool:
    """The only writes ICO may send to OME: open and close its own API session."""
    if method == "POST":
        return path == SESSIONS
    if method == "DELETE":
        return bool(_DELETE_SESSION.match(path))
    return False


def _int(v: Any) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def status_of(code: Any) -> Tuple[str, Optional[str]]:
    """(level, reason) for an OME status code; unknown / no status → warn + "không có dữ liệu"."""
    c = _int(code)
    if c in STATUS_LEVEL:
        return STATUS_LEVEL[c], None
    return "warn", NO_DATA


def _parse_time(v: Any) -> Optional[str]:
    """OME writes "2024-03-12 08:15:42.123" (appliance time, UTC by default) or ISO 8601."""
    if not v:
        return None
    s = str(v).strip().replace(" ", "T", 1)
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo:
        t = t.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return t.replace(microsecond=0).isoformat()


def _idrac_ip(mgmt: Any) -> Optional[str]:
    """First IPv4 of DeviceManagement (iDRAC), else the first IPv6."""
    ips = [norm_ip(str(m.get("NetworkAddress") or "")) for m in (mgmt or []) if isinstance(m, dict)]
    ips = [i for i in ips if i]
    v4 = [i for i in ips if ":" not in i]
    return (v4 or ips or [None])[0]


def parse_device(d: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One server of /DeviceService/Devices; None for other device types or rows without an Id."""
    dev_id = _int(d.get("Id"))
    if dev_id is None:
        return None
    if d.get("Type") is not None and _int(d.get("Type")) != TYPE_SERVER:
        return None
    tag = norm_serial(d.get("DeviceServiceTag") or d.get("Identifier")) or None
    level, reason = status_of(d.get("Status"))
    code = _int(d.get("Status"))
    return {
        "deviceId": dev_id, "serviceTag": tag,
        "name": str(d.get("DeviceName") or tag or dev_id)[:255],
        "model": (str(d.get("Model") or "").strip() or None),
        "idracIp": _idrac_ip(d.get("DeviceManagement")),
        "powerState": POWER_STATE.get(_int(d.get("PowerState")), "unknown"),
        "status": level, "statusCode": code, "statusReason": reason,
        "connection": bool(d.get("ConnectionState")) if d.get("ConnectionState") is not None else None,
        "lastInventoryAt": _parse_time(d.get("LastInventoryTime")),
    }


def parse_subsystems(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """/Devices(<Id>)/SubSystemHealth → [{subsystem, status, statusCode, detail}] (detail: the fault messages)."""
    out = []
    for s in (payload or {}).get("value") or []:
        if not isinstance(s, dict):
            continue
        name = str(s.get("SubSystem") or "").strip()
        if not name:
            continue
        code = _int(s.get("RollupStatus", s.get("Status")))
        level, reason = status_of(code)
        faults = [f for f in s.get("FaultList") or [] if isinstance(f, dict)]
        msgs = []
        for f in faults[:5]:
            comp, msg = str(f.get("Component") or "").strip(), str(f.get("Message") or f.get("Fault") or "").strip()
            msgs.append(f"{comp}: {msg}" if comp and msg else comp or msg)
        detail = "; ".join(m for m in msgs if m) or reason
        out.append({"subsystem": name[:64], "status": level, "statusCode": code, "detail": (detail or None) and detail[:1000]})
    return out


class OmeClient:
    def __init__(self, cfg: HardwareConfig):
        if not (cfg.username and cfg.password):
            raise HardwareError("Chưa nhập tài khoản OME (role Viewer) và mật khẩu.")
        self.cfg = cfg
        self.netloc = f"{cfg.host}:{cfg.port}"
        self.http = JsonClient(cfg, f"https://{self.netloc}", product="OpenManage Enterprise", api_hint="OME 4.x", allow=allow)
        self.session_id: Optional[str] = None

    def login(self) -> None:
        body, hdrs = self.http.request("POST", SESSIONS, body={"UserName": self.cfg.username, "Password": self.cfg.password,
                                                                 "SessionType": "API"})
        token = hdrs.get("x-auth-token")
        if not token:
            raise HardwareError("OME không trả về phiên đăng nhập (X-Auth-Token).")
        self.http.headers["X-Auth-Token"] = token
        sid = str((body or {}).get("Id") or "")
        self.session_id = sid if _SESSION_ID.match(sid) else None

    def logout(self) -> None:
        if self.session_id:
            try:
                self.http.request("DELETE", f"{SESSIONS}('{self.session_id}')")
            except HardwareError:
                pass                     # the session expires on its own
        self.session_id = None
        self.http.headers.pop("X-Auth-Token", None)

    def _local(self, link: str) -> str:
        """@odata.nextLink as a path on this appliance (an absolute link to another host is refused)."""
        u = urllib.parse.urlsplit(link)
        if u.scheme or u.netloc:
            if u.netloc.lower() not in (self.netloc.lower(), self.cfg.host.lower()):
                raise HardwareError("OME trả về liên kết trang sang máy chủ khác — dừng đọc.")
        path = u.path if u.path.startswith("/") else "/api/" + u.path.lstrip("/")
        return path + (f"?{u.query}" if u.query else "")

    def pages(self, path: str, top: int = PAGE) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        nxt: Optional[str] = f"{path}?$top={top}&$skip=0"
        skip = 0
        for _ in range(MAX_PAGES):
            res = self.http.get(nxt) or {}
            batch = res.get("value") or []
            out.extend(b for b in batch if isinstance(b, dict))
            link = res.get("@odata.nextLink")
            if link:
                nxt = self._local(str(link))
            elif len(batch) >= top and (res.get("@odata.count") or 0) > len(out):
                skip += top
                nxt = f"{path}?$top={top}&$skip={skip}"
            else:
                return out
        logger.warning("OME %s: more than %d pages, rest skipped", path, MAX_PAGES)
        return out

    def get(self, path: str) -> Dict[str, Any]:
        return self.http.get(path) or {}


def collect(cfg: HardwareConfig) -> Dict[str, Any]:
    c = OmeClient(cfg)
    c.login()
    try:
        devices = [p for p in (parse_device(d) for d in c.pages(DEVICES)) if p]
        health: List[Dict[str, Any]] = []
        calls = 0
        for d in devices:
            if d["status"] == "ok" or calls >= MAX_HEALTH_CALLS:
                continue
            calls += 1
            try:
                subs = parse_subsystems(c.get(f"{DEVICES}({d['deviceId']})/SubSystemHealth"))
            except HardwareError as exc:     # one device without health data must not fail the source
                logger.info("OME SubSystemHealth of device %s: %s", d["deviceId"], exc)
                continue
            health.extend({**s, "deviceId": d["deviceId"]} for s in subs)
        return {"devices": devices, "health": health}
    finally:
        c.logout()


def test(cfg: HardwareConfig) -> str:
    """Log in, read one page of devices, log out."""
    c = OmeClient(cfg)
    c.cfg.timeout = min(cfg.timeout, 30)
    c.login()
    try:
        res = c.get(f"{DEVICES}?$top=1&$skip=0")
        n = res.get("@odata.count")
        return f"Kết nối thành công tới OpenManage Enterprise {cfg.host}" + (f" ({n} thiết bị)." if isinstance(n, int) else ".")
    finally:
        c.logout()
