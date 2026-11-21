"""vCenter / NetBackup sources: environment variables (VCENTER_i_* / NETBACKUP_i_*) merged with
the ones added in Settings → Nguồn dữ liệu (table data_sources).

* A UI source with the same key as a variable-based one replaces it (after "Chuyển vào giao
  diện" the variables can be removed from the ConfigMap / Secret).
* Disabled UI sources are left out everywhere (not polled, not listed); their history stays.
* Secrets are decrypted only here, when building the config objects the collectors use.
"""
import ipaddress
import json
import logging
import re
import ssl
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .config import NetBackupConfig, Settings, VCenterConfig, _positive_int
from .models import DataSource
from . import secrets_box

logger = logging.getLogger("vco.sources")

KINDS = ("vcenter", "proxmox", "nutanix", "netbackup", "pure", "netapp", "ome", "gestioip", "sdp")
STORAGE_KINDS = ("pure", "netapp")
HARDWARE_KINDS = ("ome",)        # Dell OpenManage Enterprise (hardware/)
IPAM_KINDS = ("gestioip",)       # GestióIP IPAM (ipam/)
ITSM_KINDS = ("sdp",)            # ServiceDesk Plus On-Prem (itsm/, G7a)
_TEMPLATE_RE = re.compile(r"^[^\x00-\x1f<>\"]{1,120}$")
_API_PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]{1,127}$")
_COLUMN_RE = re.compile(r"^[^\x00-\x1f<>]{1,64}$")
# Hypervisor managers: one history family (snapshot runs keyed by the source key), so keys are unique across them.
HYPERVISOR_KINDS = ("vcenter", "proxmox", "nutanix")
NBU_DOMAIN_TYPES = ("unixpwd", "vx", "NT", "ldap", "msad")
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
_HOST_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
_API_VERSION_RE = re.compile(r"^\d{1,2}\.\d{1,2}$")

_cache_lock = threading.Lock()
_cache: Dict[str, Tuple[float, list]] = {}
CACHE_SECONDS = 10


def invalidate_cache() -> None:
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------------------
# Config objects
# ---------------------------------------------------------------------------
def _opts(row: DataSource) -> Dict[str, Any]:
    try:
        return json.loads(row.options or "{}")
    except ValueError:
        return {}


def _secret(row: DataSource, field: str) -> str:
    try:
        value = secrets_box.decrypt(getattr(row, field))
    except secrets_box.SecretUnreadable:
        logger.warning("Saved secret of source %s/%s cannot be decrypted (SECRETS_KEY changed?) — enter it again",
                       row.kind, row.key)
        return ""
    from .observability import register_secret
    register_secret(value)
    return value


def to_config(row: DataSource):
    if row.kind in ITSM_KINDS:
        import datetime as _dt
        from .itsm import ItsmConfig
        o = _opts(row)
        return ItsmConfig(key=row.key, name=row.name, host=row.host, port=row.port, api_key=_secret(row, "api_key_enc"),
                          verify_ssl=bool(row.verify_ssl), ca_pem=row.ca_pem or "", demo=bool(row.demo),
                          timeout=int(o.get("timeout") or 60), templates=list(o.get("templates") or []),
                          backfill_from=_dt.date.fromisoformat(o.get("backfillFrom") or "2025-10-01"),
                          rate_per_min=int(o.get("ratePerMin") or 60), interval_minutes=int(o.get("intervalMinutes") or 30),
                          kind=row.kind)
    if row.kind in IPAM_KINDS:
        from .ipam import DEFAULT_API_PATH, IpamConfig
        o = _opts(row)
        return IpamConfig(key=row.key, name=row.name, host=row.host, port=row.port, username=row.username or "",
                          password=_secret(row, "password_enc"), verify_ssl=bool(row.verify_ssl), ca_pem=row.ca_pem or "",
                          demo=bool(row.demo), timeout=int(o.get("timeout") or 60), client_name=o.get("clientName") or "DEFAULT",
                          api_path=o.get("apiPath") or DEFAULT_API_PATH, serial_column=o.get("serialColumn") or "",
                          mac_column=o.get("macColumn") or "", kind=row.kind)
    if row.kind in HARDWARE_KINDS:
        from .hardware import HardwareConfig
        return HardwareConfig(key=row.key, name=row.name, host=row.host, port=row.port, username=row.username or "",
                              password=_secret(row, "password_enc"), verify_ssl=bool(row.verify_ssl), ca_pem=row.ca_pem or "",
                              demo=bool(row.demo), timeout=int(_opts(row).get("timeout") or 60), kind=row.kind)
    if row.kind in STORAGE_KINDS:
        from .storage import StorageConfig
        return StorageConfig(key=row.key, name=row.name, vendor=row.kind, host=row.host, port=row.port,
                             username=row.username or "", password=_secret(row, "password_enc"),
                             api_token=_secret(row, "api_key_enc"), verify_ssl=bool(row.verify_ssl), ca_pem=row.ca_pem or "",
                             demo=bool(row.demo), timeout=int(_opts(row).get("timeout") or 30))
    if row.kind in HYPERVISOR_KINDS:
        from .hypervisors import KIND_PLATFORM
        return VCenterConfig(key=row.key, name=row.name, host=row.host, port=row.port, username=row.username or "",
                             password=_secret(row, "password_enc"), verify_ssl=bool(row.verify_ssl), demo=bool(row.demo),
                             ca_pem=row.ca_pem or "", origin="ui",
                             timeout=_positive_int(_opts(row).get("timeout")), platform=KIND_PLATFORM[row.kind])
    o = _opts(row)
    return NetBackupConfig(key=row.key, name=row.name, host=row.host, port=row.port,
                           api_key=_secret(row, "api_key_enc"), username=row.username or "",
                           password=_secret(row, "password_enc"), domain_type=o.get("domainType") or "unixpwd",
                           domain_name=o.get("domainName") or "", verify_ssl=bool(row.verify_ssl),
                           api_version=o.get("apiVersion") or "8.0", demo=bool(row.demo),
                           timeout=int(o.get("timeout") or 60), ca_pem=row.ca_pem or "", origin="ui")


def _ui_rows(kind: str) -> List[DataSource]:
    from .database import SessionLocal
    try:
        with SessionLocal() as db:
            rows = db.query(DataSource).filter(DataSource.kind == kind).order_by(DataSource.id).all()
            db.expunge_all()
            return rows
    except Exception:  # noqa: BLE001 — table not created yet (first start) or DB down: env sources still work
        logger.debug("data_sources unavailable", exc_info=True)
        return []


def _merged(kind: str, settings: Settings) -> list:
    with _cache_lock:
        hit = _cache.get(kind)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    env = settings.env_vcenters() if kind == "vcenter" else settings.env_netbackups() if kind == "netbackup" else []
    rows = _ui_rows(kind)
    ui_keys = {r.key for r in rows}
    merged = [c for c in env if c.key not in ui_keys] + [to_config(r) for r in rows if r.enabled]
    with _cache_lock:
        _cache[kind] = (time.time(), merged)
    return merged


def vcenter_configs(settings: Settings) -> List[VCenterConfig]:
    """Every hypervisor manager: VMware vCenter (UI + environment), Proxmox VE and Nutanix (UI only)."""
    return _merged("vcenter", settings) + _merged("proxmox", settings) + _merged("nutanix", settings)


def platform_of(key: Optional[str]) -> Optional[str]:
    """vmware | proxmox | nutanix for a history key; None for imported data. A key no longer configured keeps
    vmware (the only platform before Proxmox / Nutanix were added)."""
    if not key or key == "import":
        return None
    from .config import get_settings
    for c in vcenter_configs(get_settings()):
        if c.key == key:
            return getattr(c, "platform", "vmware")
    return "vmware"


def netbackup_configs(settings: Settings) -> List[NetBackupConfig]:
    return _merged("netbackup", settings)


def storage_configs(settings: Settings) -> list:
    """Pure FlashArray + NetApp ONTAP sources (Settings only — no environment variables)."""
    return _merged("pure", settings) + _merged("netapp", settings)


def itsm_configs(settings: Settings) -> list:
    """ServiceDesk Plus sources (Settings only)."""
    return _merged("sdp", settings)


def ipam_configs(settings: Settings) -> list:
    """GestióIP IPAM sources (Settings only)."""
    return _merged("gestioip", settings)


def hardware_configs(settings: Settings) -> list:
    """Dell OpenManage Enterprise sources (Settings only — no environment variables)."""
    return _merged("ome", settings)


# ---------------------------------------------------------------------------
# Validation (input from the Settings form)
# ---------------------------------------------------------------------------
class SourceError(ValueError):
    """Invalid input; message is shown to the admin."""


def validate_host(host: str) -> str:
    h = (host or "").strip()
    if not h:
        raise SourceError("Chưa nhập địa chỉ máy chủ.")
    if "://" in h or "/" in h or " " in h:
        raise SourceError("Chỉ nhập tên máy chủ hoặc IP (không kèm https:// hay đường dẫn).")
    try:
        ipaddress.ip_address(h)
        return h
    except ValueError:
        pass
    if not _HOST_RE.match(h):
        raise SourceError("Tên máy chủ không hợp lệ.")
    return h.lower()


def validate_ca_pem(pem: Optional[str]) -> str:
    pem = (pem or "").strip()
    if not pem:
        return ""
    if "-----BEGIN CERTIFICATE-----" not in pem or "PRIVATE KEY" in pem:
        raise SourceError("Chỉ dán chứng chỉ CA dạng PEM (-----BEGIN CERTIFICATE-----), không dán private key.")
    try:
        ssl.create_default_context(cadata=pem)
    except ssl.SSLError as exc:
        raise SourceError(f"Chứng chỉ CA không đọc được: {exc.reason or exc}") from exc
    return pem + "\n"


def apply_input(row: DataSource, data: Dict[str, Any], creating: bool) -> None:
    """Validate the form and copy it onto the row. Empty password / API key = keep the saved one."""
    if creating:
        kind = data.get("kind")
        if kind not in KINDS:
            raise SourceError("Loại nguồn không hợp lệ.")
        key = (data.get("key") or "").strip().lower()
        if not _KEY_RE.match(key):
            raise SourceError("Mã nguồn chỉ gồm chữ thường, số, dấu gạch ngang (2–41 ký tự), vd vc-hcm-01.")
        row.kind, row.key = kind, key
    kind = row.kind
    name = (data.get("name") or "").strip()
    if not name or len(name) > 128:
        raise SourceError("Tên hiển thị bắt buộc, tối đa 128 ký tự.")
    row.name = name
    row.demo = bool(data.get("demo"))
    row.host = validate_host(data.get("host") or (f"{row.key}.demo.local" if row.demo else ""))
    try:
        from .hypervisors import DEFAULT_PORTS
        port = int(data.get("port") or (1556 if kind == "netbackup" else DEFAULT_PORTS.get(kind, 443)))
    except (TypeError, ValueError):
        raise SourceError("Cổng không hợp lệ.") from None
    if not 1 <= port <= 65535:
        raise SourceError("Cổng phải trong khoảng 1–65535.")
    row.port = port
    row.username = (data.get("username") or "").strip()[:255] or None
    row.verify_ssl = bool(data.get("verifySsl"))
    if "caPem" in data:
        row.ca_pem = validate_ca_pem(data.get("caPem")) or None
    if data.get("password"):
        row.password_enc = secrets_box.encrypt(data["password"])
    if data.get("clearPassword"):
        row.password_enc = None
    if kind == "netbackup":
        if data.get("apiKey"):
            row.api_key_enc = secrets_box.encrypt(data["apiKey"])
        if data.get("clearApiKey"):
            row.api_key_enc = None
        o = _opts(row)
        dt_ = data.get("domainType") or o.get("domainType") or "unixpwd"
        if dt_ not in NBU_DOMAIN_TYPES:
            raise SourceError(f"Domain type phải là một trong: {', '.join(NBU_DOMAIN_TYPES)}.")
        api_version = str(data.get("apiVersion") or o.get("apiVersion") or "8.0").strip()
        if not _API_VERSION_RE.match(api_version):
            raise SourceError("API version dạng số, vd 8.0 (NetBackup 10.0) hoặc 12.0 (10.4).")
        try:
            timeout = max(10, min(int(data.get("timeout") or o.get("timeout") or 60), 600))
        except (TypeError, ValueError):
            raise SourceError("Timeout không hợp lệ.") from None
        row.options = json.dumps({"domainType": dt_, "domainName": (data.get("domainName") or "").strip()[:255],
                                  "apiVersion": api_version, "timeout": timeout})
        if not row.demo and not (row.api_key_enc or (row.username and row.password_enc)):
            raise SourceError("Cần API key, hoặc user + mật khẩu của tài khoản NetBackup chỉ đọc.")
    elif kind == "pure":
        if data.get("apiKey"):
            row.api_key_enc = secrets_box.encrypt(data["apiKey"])
        if data.get("clearApiKey"):
            row.api_key_enc = None
        row.username = None
        row.password_enc = None
        row.options = json.dumps({"timeout": _storage_timeout(data, row)})
        if not row.demo and not row.api_key_enc:
            raise SourceError("Cần API token của user chỉ đọc (readonly) trên FlashArray.")
    elif kind == "netapp":
        row.api_key_enc = None
        row.options = json.dumps({"timeout": _storage_timeout(data, row)})
        if not row.demo and not (row.username and row.password_enc):
            raise SourceError("Cần user + mật khẩu của tài khoản ONTAP role readonly (ứng dụng http).")
    elif kind == "sdp":
        import datetime as _dt
        from .config import get_settings
        env = get_settings()
        if data.get("apiKey"):
            row.api_key_enc = secrets_box.encrypt(data["apiKey"])
        if data.get("clearApiKey"):
            row.api_key_enc = None
        row.username, row.password_enc = None, None
        o = _opts(row)
        raw = data.get("templates") if "templates" in data else o.get("templates") or env.itsm_templates
        names = [t.strip() for t in (raw.split(",") if isinstance(raw, str) else raw or []) if str(t).strip()]
        if not names and not row.demo:
            raise SourceError("Nhập ít nhất một template yêu cầu (tên đúng như trên ServiceDesk Plus), phân cách bằng dấu phẩy.")
        if len(names) > 20 or not all(_TEMPLATE_RE.match(t) for t in names):
            raise SourceError("Tối đa 20 template, mỗi tên 1–120 ký tự, không có ký tự đặc biệt.")
        try:
            start = _dt.date.fromisoformat(str(data.get("backfillFrom") or o.get("backfillFrom") or env.itsm_backfill_from))
            rate = int(data.get("ratePerMin") or o.get("ratePerMin") or env.itsm_rate_per_min)
            every = int(data.get("intervalMinutes") or o.get("intervalMinutes") or env.itsm_interval_minutes)
            timeout = max(10, min(int(data.get("timeout") or o.get("timeout") or 60), 300))
        except (TypeError, ValueError):
            raise SourceError("Ngày nạp lùi (YYYY-MM-DD), số lời gọi / phút hoặc chu kỳ không hợp lệ.") from None
        if not _dt.date(2015, 1, 1) <= start <= _dt.date.today():
            raise SourceError("Ngày nạp lùi phải từ 2015 đến hôm nay.")
        if not 1 <= rate <= 600 or not 5 <= every <= 1440:
            raise SourceError("Số lời gọi / phút 1–600, chu kỳ đọc 5–1.440 phút.")
        row.options = json.dumps({"timeout": timeout, "templates": names, "backfillFrom": start.isoformat(), "ratePerMin": rate,
                                  "intervalMinutes": every}, ensure_ascii=False)
        if not row.demo and not row.api_key_enc:
            raise SourceError("Cần technician key của tài khoản chỉ có quyền xem yêu cầu (ServiceDesk Plus → Hồ sơ cá nhân → "
                              "Tạo API key).")
    elif kind == "gestioip":
        row.api_key_enc = None
        o = _opts(row)
        client = str(data.get("clientName") if "clientName" in data else o.get("clientName") or "DEFAULT").strip() or "DEFAULT"
        api_path = str(data.get("apiPath") if "apiPath" in data else o.get("apiPath") or "").strip() or "/gestioip/api/api.cgi"
        cols = {f: str(data.get(f) if f in data else o.get(f) or "").strip() for f in ("serialColumn", "macColumn")}
        if not _COLUMN_RE.match(client):
            raise SourceError("Tên client GestióIP 1–64 ký tự, không có ký tự đặc biệt.")
        if not _API_PATH_RE.match(api_path):
            raise SourceError("Đường dẫn API dạng /gestioip/api/api.cgi (chỉ chữ, số, . _ - /).")
        for f, label in (("serialColumn", "Cột serial"), ("macColumn", "Cột MAC")):
            if cols[f] and not _COLUMN_RE.match(cols[f]):
                raise SourceError(f"{label}: tên cột tuỳ chỉnh 1–64 ký tự, không có ký tự đặc biệt.")
        try:
            timeout = max(10, min(int(data.get("timeout") or o.get("timeout") or 60), 300))
        except (TypeError, ValueError):
            raise SourceError("Timeout không hợp lệ.") from None
        row.options = json.dumps({"timeout": timeout, "clientName": client, "apiPath": api_path, **cols})
        if not row.demo and not (row.username and row.password_enc):
            raise SourceError("Cần user + mật khẩu của tài khoản GestióIP (chỉ cần quyền đọc client).")
    elif kind == "ome":
        row.api_key_enc = None
        try:
            timeout = max(10, min(int(data.get("timeout") or _opts(row).get("timeout") or 60), 300))
        except (TypeError, ValueError):
            raise SourceError("Timeout không hợp lệ.") from None
        row.options = json.dumps({"timeout": timeout})
        if not row.demo and not (row.username and row.password_enc):
            raise SourceError("Cần user + mật khẩu của tài khoản OME role Viewer (chỉ đọc).")
    elif kind == "proxmox":
        from .hypervisors.proxmox import TOKEN_ID
        row.api_key_enc = None
        if row.username and not TOKEN_ID.match(row.username):
            raise SourceError("Token ID dạng user@realm!tên-token, vd ico@pve!monitor.")
        if not row.demo and not (row.username and row.password_enc):
            raise SourceError("Cần Token ID và Secret của API token Proxmox (role PVEAuditor).")
    elif kind == "nutanix":
        row.api_key_enc = None
        if not row.demo and not (row.username and row.password_enc):
            raise SourceError("Cần user + mật khẩu Prism Element role Viewer (chỉ đọc).")
    elif not row.demo and not (row.username and row.password_enc):
        raise SourceError("Cần user + mật khẩu của tài khoản vCenter chỉ đọc.")
    if "enabled" in data:
        row.enabled = bool(data.get("enabled"))


def _storage_timeout(data: Dict[str, Any], row: DataSource) -> int:
    try:
        return max(10, min(int(data.get("timeout") or _opts(row).get("timeout") or 30), 300))
    except (TypeError, ValueError):
        raise SourceError("Timeout không hợp lệ.") from None


def public(row: DataSource) -> Dict[str, Any]:
    """What the Settings page sees — never a secret."""
    readable = True
    for f in ("password_enc", "api_key_enc"):
        try:
            secrets_box.decrypt(getattr(row, f))
        except secrets_box.SecretUnreadable:
            readable = False
    o = _opts(row)
    return {
        "id": row.id, "kind": row.kind, "key": row.key, "name": row.name, "host": row.host, "port": row.port,
        "username": row.username or "", "verifySsl": bool(row.verify_ssl), "hasCaPem": bool(row.ca_pem),
        "caPem": row.ca_pem or "", "demo": bool(row.demo), "enabled": bool(row.enabled), "origin": "ui",
        "hasPassword": bool(row.password_enc), "hasApiKey": bool(row.api_key_enc), "secretsReadable": readable,
        "domainType": o.get("domainType"), "domainName": o.get("domainName"), "apiVersion": o.get("apiVersion"),
        "timeout": o.get("timeout"), "clientName": o.get("clientName"), "apiPath": o.get("apiPath"),
        "serialColumn": o.get("serialColumn"), "macColumn": o.get("macColumn"), "updatedBy": row.updated_by,
        "templates": o.get("templates"), "backfillFrom": o.get("backfillFrom"), "ratePerMin": o.get("ratePerMin"),
        "intervalMinutes": o.get("intervalMinutes"),
        "updatedAt": row.updated_at.isoformat() + "Z" if row.updated_at else None,
        "lastTest": {"at": row.last_test_at.isoformat() + "Z", "ok": row.last_test_ok, "message": row.last_test_message}
        if row.last_test_at else None,
    }


def public_env(cfg, kind: str, replaced: bool) -> Dict[str, Any]:
    return {
        "id": None, "kind": kind, "key": cfg.key, "name": cfg.name, "host": cfg.host, "port": cfg.port,
        "username": getattr(cfg, "username", "") or "", "verifySsl": bool(cfg.verify_ssl), "demo": bool(cfg.demo),
        "enabled": not replaced, "origin": "env", "replacedByUi": replaced,
        "hasPassword": bool(getattr(cfg, "password", "")), "hasApiKey": bool(getattr(cfg, "api_key", "")),
        "apiVersion": getattr(cfg, "api_version", None), "domainType": getattr(cfg, "domain_type", None),
        "domainName": getattr(cfg, "domain_name", None),
    }


def row_from_env(cfg, kind: str, user: str) -> DataSource:
    """Copy a variable-based source (secrets included) into the table."""
    row = DataSource(kind=kind, key=cfg.key, name=cfg.name, host=cfg.host, port=cfg.port,
                     username=cfg.username or None, password_enc=secrets_box.encrypt(cfg.password),
                     verify_ssl=bool(cfg.verify_ssl), demo=bool(cfg.demo), enabled=True, updated_by=user)
    ca_file = getattr(cfg, "ca_cert", "")
    if ca_file:
        try:
            with open(ca_file) as f:
                row.ca_pem = validate_ca_pem(f.read()) or None
        except (OSError, SourceError):
            logger.warning("CA file %s of %s not copied", ca_file, cfg.key)
    if kind == "netbackup":
        row.api_key_enc = secrets_box.encrypt(cfg.api_key)
        row.options = json.dumps({"domainType": cfg.domain_type, "domainName": cfg.domain_name,
                                  "apiVersion": cfg.api_version, "timeout": cfg.timeout})
    return row


# ---------------------------------------------------------------------------
# Connection test (read-only: log in, read one thing, log out)
# ---------------------------------------------------------------------------
def test_connection(cfg, kind: str) -> str:
    if cfg.demo:
        return "Chế độ DEMO — không kết nối máy chủ thật."
    if kind in ITSM_KINDS:
        from .itsm import ItsmError, sdp
        try:
            return sdp.test(cfg)
        except ItsmError as exc:
            raise SourceError(str(exc)) from exc
    if kind in IPAM_KINDS:
        from .ipam import IpamError, gestioip
        try:
            return gestioip.test(cfg)
        except IpamError as exc:
            raise SourceError(str(exc)) from exc
    if kind in HARDWARE_KINDS:
        from .hardware import HardwareError, ome
        try:
            return ome.test(cfg)
        except HardwareError as exc:
            raise SourceError(str(exc)) from exc
    if kind in STORAGE_KINDS:
        from .storage import StorageError, netapp, pure
        try:
            return (pure if kind == "pure" else netapp).test(cfg)
        except StorageError as exc:
            raise SourceError(str(exc)) from exc
    if kind in ("proxmox", "nutanix"):
        from .hypervisors import nutanix, proxmox
        from .storage import StorageError
        try:
            return (proxmox if kind == "proxmox" else nutanix).test(cfg)
        except StorageError as exc:
            raise SourceError(str(exc)) from exc
    if kind == "vcenter":
        from .vcenter import client as vc
        try:
            return "Kết nối thành công: " + vc.test_connection(cfg)
        except vc.VCenterConnectionError as exc:
            raise SourceError(str(exc)) from exc
    from .backup import client as nb
    c = nb.NetBackupClient(cfg)
    c.cfg.timeout = min(cfg.timeout, 30)
    try:
        c.login()
        c.get("/admin/jobs", {"page[limit]": 1})
        return f"Kết nối thành công tới NetBackup {cfg.host} (API {cfg.api_version}, " \
               f"{'API key' if cfg.api_key else 'user ' + cfg.username})."
    except nb.NetBackupError as exc:
        raise SourceError(str(exc)) from exc
    finally:
        c.logout()
