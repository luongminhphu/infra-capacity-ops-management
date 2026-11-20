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

from ..shared.identity import norm_ip, norm_mac, norm_serial
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
WARRANTIES = "/api/WarrantyService/Warranties"
BASELINES = "/api/UpdateService/Baselines"
ALERTS = "/api/AlertService/Alerts"
MAX_INVENTORY_CALLS = 2000
MAX_BASELINES = 20
MAX_ALERT_PAGES = 25             # 5 000 alerts per round; the rest next round (the cursor only moves over stored ones)
HARDWARE_ALERT_CATEGORY = "system health"
ALERT_SEVERITY = {16: "crit", 8: "bad", 1: "warn"}          # 2 info / 4 normal → info

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


# ---------------------------------------------------------------- deep inventory (InventoryDetails)
_SIZE = re.compile(r"^\s*([\d.,]+)\s*([KMGT]?B)?\s*$", re.I)
_UNIT_GB = {"KB": 1 / 1024 ** 2, "MB": 1 / 1024, "GB": 1.0, "TB": 1024.0, "B": 1 / 1024 ** 3}


def size_gb(v: Any) -> Optional[float]:
    """"558.38 GB" / "1.75 TB" / "32768 MB" / bytes (number) → GB."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return round(float(v) / 1024 ** 3, 2) if v > 1024 ** 2 else float(v)
    m = _SIZE.match(str(v).replace(",", ""))
    if not m:
        return None
    return round(float(m.group(1)) * _UNIT_GB[(m.group(2) or "GB").upper()], 2)


def _level(row: Dict[str, Any]) -> str:
    """Status of an inventory row: numeric code first, else the status text; nothing at all → ok (no evidence)."""
    code = _int(row.get("Status"))
    if code is not None:
        return status_of(code)[0]
    txt = str(row.get("StatusString") or row.get("StatusTypeString") or row.get("Status") or "").strip().lower()
    if not txt or txt in ("ok", "normal", "healthy", "ready", "online"):
        return "ok"
    if txt in ("critical", "failed", "error", "offline"):
        return "crit"
    return "bad" if txt in ("warning", "degraded", "non-critical") else "warn"


def _txt(v: Any, n: int = 255) -> Optional[str]:
    t = str(v).strip() if v is not None else ""
    return t[:n] or None


def _macs(node: Any, out: List[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if "macaddress" in k.lower() and isinstance(v, str):
                m = norm_mac(v)
                if m and m != "00:00:00:00:00:00" and m not in out:
                    out.append(m)
            else:
                _macs(v, out)
    elif isinstance(node, list):
        for v in node:
            _macs(v, out)


def parse_inventory(payload: Dict[str, Any]) -> Dict[str, Any]:
    """/Devices(<Id>)/InventoryDetails → {os, hostname, macs, disks, parts, software}. Unknown types are ignored."""
    by_type = {str(x.get("InventoryType") or ""): x.get("InventoryInfo") or [] for x in (payload or {}).get("value") or []
               if isinstance(x, dict)}
    rows = lambda t: [r for r in by_type.get(t) or [] if isinstance(r, dict)]  # noqa: E731
    out: Dict[str, Any] = {"os": None, "hostname": None, "macs": [], "disks": [], "parts": [], "software": []}
    for r in rows("serverArrayDisks"):
        pfs = r.get("PredictiveFailureState")
        predictive = pfs if isinstance(pfs, bool) else ("present" in str(pfs or "").lower() and "absent" not in str(pfs or "").lower())
        out["disks"].append({"slot": _txt(r.get("DiskNumber") or r.get("Name") or r.get("Slot") or r.get("Id")) or "?",
                             "model": _txt(r.get("ModelNumber") or r.get("Model"), 128), "serial": _txt(r.get("SerialNumber"), 64),
                             "media": _txt(r.get("MediaType"), 16), "sizeGB": size_gb(r.get("Size")),
                             "status": _level(r), "predictiveFailure": bool(predictive)})
    for kind, t in (("raid", "serverRaidControllers"), ("psu", "serverPowerSupplies"), ("memory", "serverMemoryDevices")):
        for r in rows(t):
            if kind == "raid":
                detail = f"FW {r['FirmwareVersion']}" if r.get("FirmwareVersion") else None
            elif kind == "psu":
                detail = f"{r['OutputWatts']} W" if r.get("OutputWatts") else None
            else:
                gb = size_gb(r.get("Size"))
                detail = f"{gb:g} GB" if gb else None
            out["parts"].append({"kind": kind, "name": _txt(r.get("Name") or r.get("Location") or r.get("DeviceDescription") or r.get("Fqdd")) or kind,
                                 "model": _txt(r.get("Model") or r.get("PartNumber"), 128), "serial": _txt(r.get("SerialNumber"), 64),
                                 "detail": detail, "status": _level(r)})
    _macs(rows("serverNetworkInterfaces"), out["macs"])
    for r in rows("serverOperatingSystems"):
        out["os"] = _txt(" ".join(str(x) for x in (r.get("OsName"), r.get("OsVersion")) if x))
        out["hostname"] = _txt(r.get("Hostname"))
        break
    for r in rows("deviceSoftware"):
        if str(r.get("SoftwareType") or "FRMW").upper() != "FRMW":
            continue
        name = _txt(r.get("DeviceDescription") or r.get("InstanceId") or r.get("ComponentId"))
        if name:
            out["software"].append({"component": name, "version": _txt(r.get("Version"), 64)})
    return out


def _norm_component(n: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(n or "").lower()).strip()


def parse_compliance(reports: List[Dict[str, Any]]) -> Dict[int, Dict[str, Dict[str, Any]]]:
    """DeviceComplianceReports of every baseline → {deviceId: {component key: {component, version, baselineVersion,
    compliance}}}; compliance ok | upgrade (older than baseline) | downgrade (newer) | unknown."""
    out: Dict[int, Dict[str, Dict[str, Any]]] = {}
    for rep in reports:
        dev = _int(rep.get("DeviceId"))
        if dev is None:
            continue
        for comp in rep.get("ComponentComplianceReports") or []:
            if not isinstance(comp, dict) or not comp.get("Name"):
                continue
            action = str(comp.get("UpdateAction") or "").upper()
            status = str(comp.get("ComplianceStatus") or "").upper()
            if action == "UPGRADE" or status in ("CRITICAL", "WARNING"):
                c = "upgrade"
            elif action == "DOWNGRADE" or status == "DOWNGRADE":
                c = "downgrade"
            elif status == "OK" or action == "EQUAL":
                c = "ok"
            else:
                c = "unknown"
            out.setdefault(dev, {})[_norm_component(comp["Name"])] = {
                "component": _txt(comp["Name"]), "version": _txt(comp.get("CurrentVersion"), 64),
                "baselineVersion": _txt(comp.get("Version") or comp.get("BaselineVersion"), 64), "compliance": c}
    return out


def merge_compliance(inv: Dict[int, Dict[str, Any]], comp: Dict[int, Dict[str, Dict[str, Any]]]) -> Dict[int, Dict[str, Any]]:
    """Firmware rows per device: installed firmware (deviceSoftware) with the baseline version / compliance when a
    baseline covers the component; components only in a compliance report are added."""
    for dev, d in inv.items():
        rep = dict(comp.get(dev) or {})
        rows = []
        for sw in d.get("software") or []:
            hit = rep.pop(_norm_component(sw["component"]), None)
            rows.append({**sw, "baselineVersion": hit and hit["baselineVersion"], "compliance": hit and hit["compliance"]})
        rows.extend(rep.values())
        d["firmware"] = rows
    return inv


# ---------------------------------------------------------------- warranty / alerts
def _date(v: Any) -> Optional[str]:
    t = _parse_time(v)
    return t[:10] if t else None


def parse_warranty(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """WarrantyService/Warranties row → {serviceTag, serviceLevel, start, end} (ISO dates)."""
    tag = norm_serial(r.get("DeviceIdentifier") or r.get("ServiceTag"))
    if not tag:
        return None
    return {"serviceTag": tag, "serviceLevel": _txt(r.get("ServiceLevelDescription") or r.get("ServiceLevelCode")),
            "start": _date(r.get("StartDate")), "end": _date(r.get("EndDate"))}


def parse_alert(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """AlertService/Alerts row → hardware alert (category System Health on a server), else None."""
    aid, at = _int(r.get("Id")), _parse_time(r.get("TimeStamp"))
    if aid is None or not at:
        return None
    if str(r.get("CategoryName") or "").strip().lower() != HARDWARE_ALERT_CATEGORY:
        return None
    if r.get("AlertDeviceType") is not None and _int(r.get("AlertDeviceType")) != TYPE_SERVER:
        return None
    return {"alertId": aid, "deviceId": _int(r.get("AlertDeviceId")),
            "serviceTag": norm_serial(r.get("AlertDeviceIdentifier")) or None,
            "severity": ALERT_SEVERITY.get(_int(r.get("SeverityType")), "info"), "status": _txt(r.get("StatusName"), 16),
            "category": _txt(r.get("SubCategoryName") or r.get("CategoryName"), 128), "messageId": _txt(r.get("AlertMessageId"), 32),
            "message": _txt(r.get("Message"), 2000), "at": at}


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

    def pages(self, path: str, top: int = PAGE, query: str = "", max_pages: int = MAX_PAGES) -> List[Dict[str, Any]]:
        """Every row of an OData collection. `query`: extra OData options already URL-encoded ($filter, $orderby)."""
        out: List[Dict[str, Any]] = []
        pre = f"{path}?{query}&" if query else f"{path}?"
        nxt: Optional[str] = f"{pre}$top={top}&$skip=0"
        skip = 0
        for _ in range(max_pages):
            res = self.http.get(nxt) or {}
            batch = res.get("value") or []
            out.extend(b for b in batch if isinstance(b, dict))
            link = res.get("@odata.nextLink")
            if link:
                nxt = self._local(str(link))
            elif len(batch) >= top and (res.get("@odata.count") or 0) > len(out):
                skip += top
                nxt = f"{pre}$top={top}&$skip={skip}"
            else:
                return out
        logger.warning("OME %s: more than %d pages, rest skipped", path, max_pages)
        return out

    def get(self, path: str) -> Dict[str, Any]:
        return self.http.get(path) or {}


def collect(cfg: HardwareConfig, plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One OME session. Always: devices + subsystem health. `plan` adds the slower reads (collector.py decides when):
    inventory (InventoryDetails per server + firmware baselines), warranty, alerts since a cursor. A failing optional
    read is reported in "warnings" and never fails the devices read."""
    plan = plan or {}
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
        out: Dict[str, Any] = {"devices": devices, "health": health, "warnings": []}
        if plan.get("inventory"):
            _optional(out, "Inventory", lambda: _read_inventory(c, devices, out))
        if plan.get("warranty"):
            _optional(out, "Bảo hành", lambda: out.__setitem__(
                "warranties", [w for w in (parse_warranty(r) for r in c.pages(WARRANTIES)) if w]))
        if plan.get("alertsSince") is not None:
            _optional(out, "Alert", lambda: out.__setitem__("alerts", _read_alerts(c, plan["alertsSince"])))
        return out
    finally:
        c.logout()


def _optional(out: Dict[str, Any], label: str, fn) -> None:
    try:
        fn()
    except HardwareError as exc:
        logger.info("OME optional read %s failed: %s", label, exc)
        out["warnings"].append(f"{label}: {exc}")


def _read_inventory(c: OmeClient, devices: List[Dict[str, Any]], out: Dict[str, Any]) -> None:
    inv: Dict[int, Dict[str, Any]] = {}
    for d in devices[:MAX_INVENTORY_CALLS]:
        try:
            inv[d["deviceId"]] = parse_inventory(c.get(f"{DEVICES}({d['deviceId']})/InventoryDetails"))
        except HardwareError as exc:
            logger.info("OME InventoryDetails of device %s: %s", d["deviceId"], exc)
    reports: List[Dict[str, Any]] = []
    baselines = c.pages(BASELINES)
    for b in baselines[:MAX_BASELINES]:
        bid = _int(b.get("Id"))
        if bid is not None:
            reports.extend(c.pages(f"{BASELINES}({bid})/DeviceComplianceReports"))
    out["inventory"] = merge_compliance(inv, parse_compliance(reports))
    out["baselineConfigured"] = bool(baselines)


def _read_alerts(c: OmeClient, since: dt.datetime) -> List[Dict[str, Any]]:
    flt = urllib.parse.quote(f"TimeStamp ge '{since.strftime('%Y-%m-%d %H:%M:%S.000')}'", safe="")
    rows = c.pages(ALERTS, query=f"$filter={flt}&$orderby={urllib.parse.quote('TimeStamp asc', safe='')}", max_pages=MAX_ALERT_PAGES)
    return [a for a in (parse_alert(r) for r in rows) if a]


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
