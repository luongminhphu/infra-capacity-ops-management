"""Pure Storage FlashArray (FlashArray//X R3, //C …) — Purity REST 2.x, read-only.

Session: GET /api/api_version → POST /api/<v>/login with header `api-token` → `x-auth-token` for the GETs
→ POST /api/<v>/logout. Reads: arrays (space), arrays/performance, controllers, hardware, open alerts,
volumes (not destroyed). VMware sees a FlashArray volume as naa.624a9370 + volume serial (lower case).
"""
import datetime as dt
import logging
from typing import Any, Dict, List, Optional

from . import StorageConfig, StorageError
from .http import JsonClient

logger = logging.getLogger(__name__)
GB = 1024 ** 3
NAA_PREFIX = "naa.624a9370"
OK_HW = {"ok", "healthy", "not_installed", "unused"}
MAX_PAGES = 50


def naa_of(serial: Optional[str]) -> Optional[str]:
    return f"{NAA_PREFIX}{serial.lower()}" if serial else None


def _gb(v: Any) -> Optional[float]:
    try:
        return round(float(v) / GB, 2) if v is not None else None
    except (TypeError, ValueError):
        return None


def pick_version(versions: List[str]) -> str:
    """Highest 2.x the array offers (Purity 6.x: 2.0 … 2.3x)."""
    twos = []
    for v in versions or []:
        try:
            major, minor = (int(x) for x in str(v).split(".")[:2])
        except ValueError:
            continue
        if major == 2:
            twos.append((major, minor))
    if not twos:
        raise StorageError("Mảng không hỗ trợ REST API 2.x (cần Purity//FA 6.0 trở lên).")
    return "%d.%d" % max(twos)


class PureClient:
    def __init__(self, cfg: StorageConfig):
        if not cfg.api_token:
            raise StorageError("Chưa nhập API token của user chỉ đọc trên FlashArray.")
        self.cfg = cfg
        self.http = JsonClient(cfg, f"https://{cfg.host}:{cfg.port}")
        self.version: Optional[str] = None
        self.auth: Optional[str] = None

    def login(self) -> None:
        self.version = pick_version(self.http.get("/api/api_version").get("version") or [])
        _, hdrs = self.http.request("POST", f"/api/{self.version}/login", headers={"api-token": self.cfg.api_token})
        self.auth = hdrs.get("x-auth-token")
        if not self.auth:
            raise StorageError("FlashArray không trả về phiên đăng nhập (x-auth-token).")
        self.http.headers["x-auth-token"] = self.auth

    def logout(self) -> None:
        if self.auth and self.version:
            try:
                self.http.request("POST", f"/api/{self.version}/logout")
            except StorageError:
                pass
        self.auth = None

    def items(self, path: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        p = {"limit": 500, **(params or {})}
        for _ in range(MAX_PAGES):
            res = self.http.get(f"/api/{self.version}{path}", p)
            out.extend(res.get("items") or [])
            token = res.get("continuation_token")
            if not token:
                break
            p = {**p, "continuation_token": token}
        return out


def parse(arrays, perf, controllers, hardware, alerts, volumes) -> Dict[str, Any]:
    a = (arrays or [{}])[0]
    space = a.get("space") or {}
    pf = (perf or [{}])[0]
    used = space.get("total_used", space.get("total_physical"))
    prov = space.get("total_provisioned", space.get("virtual"))
    ctl = [{"name": c.get("name"), "model": c.get("model"), "status": c.get("status"), "mode": c.get("mode"),
            "version": c.get("version")} for c in controllers or [] if c.get("mode") != "not present"]
    model = next((c["model"] for c in ctl if c.get("model")), None)
    issues = [{"name": h.get("name"), "type": h.get("type"), "status": h.get("status"), "details": h.get("details") or ""}
              for h in hardware or [] if str(h.get("status") or "ok").lower() not in OK_HW]
    sev = {"critical": "critical", "warning": "warning", "info": "info"}
    al = []
    for x in alerts or []:
        s = sev.get(str(x.get("severity") or "").lower())
        if not s:
            continue                                         # "hidden"
        created = x.get("created")
        at = dt.datetime.utcfromtimestamp(created / 1000).replace(microsecond=0).isoformat() + "Z" if isinstance(created, (int, float)) else None
        al.append({"id": str(x.get("id") or x.get("name") or x.get("code")), "severity": s,
                   "summary": (x.get("summary") or x.get("description") or x.get("code") or "")[:300],
                   "component": x.get("component_name") or x.get("component_type") or "", "at": at})
    vols = []
    for v in volumes or []:
        if v.get("destroyed"):
            continue
        vs = v.get("space") or {}
        vols.append({"name": v.get("name"), "container": (v.get("pod") or {}).get("name") or "", "kind": "volume",
                     "sizeGB": _gb(v.get("provisioned")), "usedGB": _gb(vs.get("virtual")),
                     "physicalGB": _gb(vs.get("total_physical")), "naa": naa_of(v.get("serial")), "nfsPath": None})
    lat = lambda us: round(us / 1000, 2) if isinstance(us, (int, float)) else None   # noqa: E731
    return {
        "array": {"vendor": "pure", "name": a.get("name"), "model": model, "version": f"{a.get('os') or 'Purity//FA'} {a.get('version') or ''}".strip(),
                  "capacityGB": _gb(a.get("capacity")), "usedGB": _gb(used), "provisionedGB": _gb(prov),
                  "dataReduction": round(float(space["data_reduction"]), 2) if isinstance(space.get("data_reduction"), (int, float)) else None,
                  "latencyReadMs": lat(pf.get("usec_per_read_op")), "latencyWriteMs": lat(pf.get("usec_per_write_op")),
                  "iops": round((pf.get("reads_per_sec") or 0) + (pf.get("writes_per_sec") or 0)),
                  "throughputMBps": round(((pf.get("read_bytes_per_sec") or 0) + (pf.get("write_bytes_per_sec") or 0)) / 1024 ** 2, 1),
                  "controllers": ctl, "hardwareIssues": issues},
        "volumes": vols, "alerts": al, "nfsEndpoints": [],
    }


def collect(cfg: StorageConfig) -> Dict[str, Any]:
    c = PureClient(cfg)
    c.login()
    try:
        return parse(c.items("/arrays"), c.items("/arrays/performance"), c.items("/controllers"), c.items("/hardware"),
                     c.items("/alerts", {"filter": "state='open'"}), c.items("/volumes", {"filter": "destroyed='false'"}))
    finally:
        c.logout()


def test(cfg: StorageConfig) -> str:
    c = PureClient(cfg)
    c.login()
    try:
        a = (c.items("/arrays") or [{}])[0]
        return f"Kết nối thành công: FlashArray {a.get('name') or cfg.host} ({a.get('os') or 'Purity'} {a.get('version') or ''}, REST {c.version})."
    finally:
        c.logout()
