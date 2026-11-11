"""Read-only Veritas NetBackup REST API client.

Only GET endpoints are used (plus /login and /logout when a username/password is
configured instead of an API key), so the account needs nothing more than a
view-only RBAC role:

    GET /netbackup/admin/jobs               activity monitor (jobs)
    GET /netbackup/config/policies          policy list
    GET /netbackup/config/policies/{name}   policy detail (clients, schedules)
    GET /netbackup/storage/disk-pools       disk pool capacity

The parsers below are tolerant: attribute names that moved between NetBackup
releases (8.3 → 10.x) are read from several candidates, and anything missing
stays None instead of failing the whole sync.
"""
import datetime as dt
import json
import logging
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..config import NetBackupConfig

logger = logging.getLogger("vco.netbackup")

PAGE_LIMIT = 100
MAX_JOB_PAGES = 300      # 30 000 jobs per sync at most
MAX_POLICIES = 400

BACKUP_TYPES = {"BACKUP", "SNAPSHOT", "ARCHIVE", "DBBACKUP", "BACKUP_FROM_SNAPSHOT", "APP_STATE_CAPTURE"}
COPY_TYPES = {"DUPLICATION", "REPLICATION", "SNAPREPLICATE", "IMPORT", "SNAPIMPORT", "VAULT", "INDEX_FROM_SNAPSHOT"}
RESTORE_TYPES = {"RESTORE", "SNAPRESTORE", "INSTANT_RECOVERY"}


class NetBackupError(Exception):
    """Connection / authentication / permission problem, with a message an operator can act on."""


def nbu_ssl_context(cfg: NetBackupConfig) -> ssl.SSLContext:
    """TLS context for one NetBackup primary server only — vCenter connections are untouched.

    * verify_ssl off → encrypted but unverified (NetBackup ships a self-signed CA).
    * verify_ssl on  → verify against ``cfg.ca_cert`` (the NetBackup CA, PEM) or the system
      trust store. The NetBackup internal CA has no keyUsage extension, which Python 3.13's
      default VERIFY_X509_STRICT rejects, so that flag is cleared; ``getattr`` keeps this
      working on older Pythons that do not define the constant.
    """
    if not cfg.verify_ssl:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    cafile = (cfg.ca_cert or "").strip() or None
    if cafile and not os.path.isfile(cafile):
        raise NetBackupError(f"Không tìm thấy file CA NetBackup: {cafile} (NETBACKUP_i_CA_CERT).")
    try:
        ctx = ssl.create_default_context(cafile=cafile)
    except ssl.SSLError as exc:
        raise NetBackupError(f"File CA NetBackup không hợp lệ ({cafile}): {exc}") from exc
    ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
    return ctx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def nb_time(value: dt.datetime) -> str:
    """NetBackup filter timestamp (UTC, millisecond precision)."""
    return value.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def parse_time(value: Any) -> Optional[dt.datetime]:
    """ISO-8601 from the API → naive UTC datetime (how every DateTime column is stored)."""
    if not value or not isinstance(value, str) or value.startswith("1970-01-01"):
        return None
    v = value.strip().replace("Z", "+00:00")
    try:
        t = dt.datetime.fromisoformat(v)
    except ValueError:
        try:
            t = dt.datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    if t.tzinfo is not None:
        t = t.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return t


def client_key(name: Optional[str]) -> Optional[str]:
    """Normalised host name used to match NetBackup clients with VMs / policy clients:
    lower-case short name (FQDN domain dropped), IP addresses kept as-is."""
    if not name:
        return None
    n = str(name).strip().lower()
    if not n:
        return None
    parts = n.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return n
    return parts[0]


def _first(d: Dict[str, Any], *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return default


def _num(v) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _int(v) -> Optional[int]:
    f = _num(v)
    return int(f) if f is not None else None


def job_category(job_type: Optional[str]) -> str:
    t = (job_type or "").upper()
    if t in BACKUP_TYPES:
        return "backup"
    if t in COPY_TYPES:
        return "copy"
    if t in RESTORE_TYPES:
        return "restore"
    return "other"


# ---------------------------------------------------------------------------
# Payload parsers (shared by the live client and the demo generator)
# ---------------------------------------------------------------------------
def parse_job(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    a = item.get("attributes") or item
    job_id = _int(_first(a, "jobId", default=item.get("id")))
    if job_id is None:
        return None
    job_type = (_first(a, "jobType", default="") or "").upper()
    parent = _int(a.get("parentJobId"))
    start = parse_time(a.get("startTime"))
    end = parse_time(a.get("endTime"))
    elapsed = _num(a.get("elapsedTime"))
    if isinstance(a.get("elapsedTime"), str) and a["elapsedTime"].startswith("PT"):  # ISO duration in some releases
        elapsed = _iso_duration(a["elapsedTime"])
    if elapsed is None and start and end:
        elapsed = (end - start).total_seconds()
    state = (_first(a, "state", default="") or "").upper() or None
    status = _int(a.get("status"))
    if state in ("QUEUED", "ACTIVE", "WAITING_FOR_RETRY", "RE-QUEUED", "INCOMPLETE") and status == 0 and not end:
        status = None  # not finished yet
    return {
        "jobId": job_id,
        "parentJobId": parent if parent not in (0, job_id) else None,
        "jobType": job_type,
        "category": job_category(job_type),
        "policyName": _first(a, "policyName"),
        "policyType": _first(a, "policyType"),
        "scheduleName": _first(a, "scheduleName"),
        "scheduleType": _first(a, "scheduleType"),
        "clientName": _first(a, "clientName"),
        "state": state,
        "status": status,
        "startTime": start,
        "endTime": end,
        "elapsedSec": elapsed,
        "kbytes": _num(_first(a, "kilobytesTransferred", "kilobytesDataTransferred")),
        "files": _int(_first(a, "numberOfFiles")),
        "storageUnit": _first(a, "destinationStorageUnitName"),
        "mediaServer": _first(a, "destinationMediaServerName", "mediaServerName"),
    }


def _iso_duration(v: str) -> Optional[float]:
    import re
    m = re.match(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?$", v)
    if not m:
        return None
    d, h, mi, s = (float(x) if x else 0.0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def parse_policy(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    attrs = item.get("attributes") or {}
    pol = attrs.get("policy") or attrs
    name = _first(pol, "policyName", default=item.get("id"))
    if not name:
        return None
    pa = pol.get("policyAttributes") or {}
    clients = [c.get("hostName") for c in (pol.get("clients") or []) if isinstance(c, dict) and c.get("hostName")]
    schedules = []
    for s in pol.get("schedules") or []:
        if not isinstance(s, dict):
            continue
        ret = None
        copies = ((s.get("backupCopies") or {}).get("copies") or [])
        rp = (copies[0].get("retentionPeriod") if copies and isinstance(copies[0], dict) else None) or s.get("retentionPeriod")
        if isinstance(rp, dict) and rp.get("value") is not None:
            ret = f"{rp.get('value')} {str(rp.get('unit', '')).lower()}".strip()
        schedules.append({"name": s.get("scheduleName"), "type": s.get("scheduleType"),
                          "frequencySec": _int(s.get("frequencySeconds")), "retention": ret})
    sel = (pol.get("backupSelections") or {}).get("selections") or []
    active = pa.get("active")
    if active is None and "active" in pol:
        active = pol.get("active")
    return {
        "name": name,
        "policyType": _first(pol, "policyType"),
        "active": bool(active) if active is not None else None,
        "clients": clients,
        "schedules": schedules,
        "storage": _first(pa, "storage") or None,
        "selection": "; ".join(str(x) for x in sel)[:1000] if sel else None,
    }


def parse_disk_pool(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    a = item.get("attributes") or item
    name = _first(a, "name", "diskPoolName", default=item.get("id"))
    if not name:
        return None
    vols = a.get("diskVolumes") or []
    total = _num(_first(a, "usableSizeBytes", "totalCapacityBytes", "rawSizeBytes"))
    free = _num(_first(a, "availableSpaceBytes", "freeCapacityBytes", "availableCapacityBytes", "freeSpaceBytes"))
    used = _num(_first(a, "usedCapacityBytes", "usedSpaceBytes"))
    if total is None and vols:  # sum the volumes when the pool total is not exposed
        tv = [_num(_first(v, "totalCapacityBytes", "usableSizeBytes", "rawSizeBytes")) for v in vols if isinstance(v, dict)]
        fv = [_num(_first(v, "freeCapacityBytes", "availableSpaceBytes")) for v in vols if isinstance(v, dict)]
        total = sum(x for x in tv if x is not None) or None
        free = sum(x for x in fv if x is not None) if any(x is not None for x in fv) else free
    if used is None and total is not None and free is not None:
        used = max(0.0, total - free)
    if free is None and total is not None and used is not None:
        free = max(0.0, total - used)
    states = {str((v or {}).get("state", "")).upper() for v in vols if isinstance(v, dict)}
    state = "DOWN" if "DOWN" in states else (str(a.get("state")).upper() if a.get("state") else "UP")
    return {
        "name": name,
        "storageType": _first(a, "sType", "storageType", "diskPoolType"),
        "totalBytes": total, "usedBytes": used, "freeBytes": free,
        "highWaterPct": _num(_first(a, "highWaterMark", "highWatermark")),
        "state": state,
    }


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------
class NetBackupClient:
    def __init__(self, cfg: NetBackupConfig):
        self.cfg = cfg
        self.base = f"https://{cfg.host}:{cfg.port}/netbackup"
        self.media_type = f"application/vnd.netbackup+json;version={cfg.api_version}"
        self.token: Optional[str] = cfg.api_key or None
        self._login_token = False
        self.ctx = nbu_ssl_context(cfg)

    # -- low level -------------------------------------------------------
    def _request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None,
                 body: Optional[Dict[str, Any]] = None, extra_headers: Optional[Dict[str, str]] = None) -> Any:
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params, safe="[]:',", quote_via=urllib.parse.quote)
        headers = {"Accept": self.media_type, "Content-Type": self.media_type}
        if self.token:
            headers["Authorization"] = self.token
        headers.update(extra_headers or {})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=self.cfg.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                payload = json.loads(exc.read() or b"{}")
                detail = payload.get("errorMessage") or payload.get("message") or ""
            except Exception:  # noqa: BLE001
                pass
            if exc.code == 401:
                raise NetBackupError("NetBackup từ chối xác thực (401) — kiểm tra API key / tài khoản read-only.") from exc
            if exc.code == 403:
                raise NetBackupError(f"Tài khoản không có quyền đọc {path} (403) — bổ sung quyền View trong RBAC role.") from exc
            if exc.code == 406:
                raise NetBackupError(f"Phiên bản API {self.cfg.api_version} không được hỗ trợ (406) — chỉnh NETBACKUP_i_API_VERSION.") from exc
            raise NetBackupError(f"Lỗi API {exc.code} tại {path}: {detail or exc.reason}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ssl.SSLCertVerificationError):
                raise NetBackupError(
                    f"Chứng chỉ của {self.cfg.host} không được tin cậy ({exc.reason.verify_message}) — khai báo CA NetBackup "
                    "ở NETBACKUP_i_CA_CERT, kiểm tra HOST trùng tên trong chứng chỉ, hoặc đặt NETBACKUP_i_VERIFY_SSL=false.") from exc
            raise NetBackupError(f"Không kết nối được {self.cfg.host}:{self.cfg.port} — {exc.reason}") from exc
        except (TimeoutError, OSError) as exc:
            raise NetBackupError(f"Hết thời gian chờ / lỗi mạng tới {self.cfg.host}:{self.cfg.port} — {exc}") from exc

    def login(self) -> None:
        if self.token:
            return
        if not (self.cfg.username and self.cfg.password):
            raise NetBackupError("Chưa cấu hình NETBACKUP_i_API_KEY hoặc USERNAME/PASSWORD.")
        body = {"domainType": self.cfg.domain_type, "domainName": self.cfg.domain_name or self.cfg.host,
                "userName": self.cfg.username, "password": self.cfg.password}
        res = self._request("POST", "/login", body=body)
        self.token = res.get("token")
        if not self.token:
            raise NetBackupError("Đăng nhập NetBackup không trả về token.")
        self._login_token = True

    def logout(self) -> None:
        if self._login_token and self.token:
            try:
                self._request("POST", "/logout")
            except NetBackupError:
                pass
        self.token = self.cfg.api_key or None
        self._login_token = False

    def get(self, path: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> Any:
        return self._request("GET", path, params=params, extra_headers=headers)

    def paginate(self, path: str, params: Optional[Dict[str, Any]] = None, max_pages: int = MAX_JOB_PAGES) -> Iterable[Dict[str, Any]]:
        offset = 0
        for _ in range(max_pages):
            q = dict(params or {})
            q["page[limit]"] = PAGE_LIMIT
            q["page[offset]"] = offset
            res = self.get(path, q)
            data = (res or {}).get("data") or []
            for it in data:
                yield it
            pag = ((res or {}).get("meta") or {}).get("pagination") or {}
            if len(data) < PAGE_LIMIT or (pag.get("next") is None and pag):
                return
            offset += PAGE_LIMIT

    # -- collection ------------------------------------------------------
    def fetch_jobs(self, since: dt.datetime) -> List[Dict[str, Any]]:
        out = []
        for it in self.paginate("/admin/jobs", {"filter": f"startTime ge {nb_time(since)}", "sort": "-jobId"}):
            j = parse_job(it)
            if j:
                out.append(j)
        return out

    def fetch_policies(self) -> List[Dict[str, Any]]:
        hdr = {"X-NetBackup-Policy-Use-Generic-Schema": "true"}
        res = self.get("/config/policies", headers=hdr)
        out = []
        for it in ((res or {}).get("data") or [])[:MAX_POLICIES]:
            name = it.get("id") or ((it.get("attributes") or {}).get("policy") or {}).get("policyName")
            detail = it
            if name:
                try:
                    detail = (self.get(f"/config/policies/{urllib.parse.quote(str(name), safe='')}", headers=hdr) or {}).get("data") or it
                except NetBackupError as exc:
                    logger.warning("Policy %s detail unavailable: %s", name, exc)
            p = parse_policy(detail)
            if p:
                out.append(p)
        return out

    def fetch_disk_pools(self) -> List[Dict[str, Any]]:
        res = self.get("/storage/disk-pools")
        return [p for p in (parse_disk_pool(it) for it in ((res or {}).get("data") or [])) if p]


def collect(cfg: NetBackupConfig, since: dt.datetime) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """(jobs, policies, disk pools, warnings). Jobs are mandatory; policies and
    storage degrade to a warning so a partially-permissioned account still works."""
    c = NetBackupClient(cfg)
    warnings: List[str] = []
    c.login()
    try:
        jobs = c.fetch_jobs(since)
        policies: List[Dict[str, Any]] = []
        pools: List[Dict[str, Any]] = []
        try:
            policies = c.fetch_policies()
        except NetBackupError as exc:
            warnings.append(f"Policy: {exc}")
        try:
            pools = c.fetch_disk_pools()
        except NetBackupError as exc:
            warnings.append(f"Storage: {exc}")
    finally:
        c.logout()
    return jobs, policies, pools, warnings
