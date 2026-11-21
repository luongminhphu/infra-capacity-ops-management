"""GestióIP 3.5 API — read-only client and parsers (API Guide v29).

One endpoint (default /gestioip/api/api.cgi), HTTP Basic auth, parameters in URL form, `output_type=json`.
Reads (GET only, and only these request types — see READ_REQUEST_TYPES):
  version                       — connection test;
  listNetworks (no_csv=yes)     — every non-root network of the client: id, IP, BM, descr, site, cat, comment, rootnet,
                                  customColumns (vlan, usage …);
  listHosts (no_csv=yes)        — every host (IP) entry: id, IP, hostname, descr, site, cat, comment, alive,
                                  last_response, ip_version, customColumns (serial / MAC / OS … as defined by the admin);
  listVlans                     — CSV rows "id,number,name,descr,provider,bg,font".
Pagination: limit (1–9999) + page (from 0). An API error comes back as a non-empty "error" field (HTTP 200).
The JSON shape is GestióIP's XML turned into JSON (a list of one element may arrive as an object), so the parsers accept both.
"""
import base64
import functools
import ipaddress
import logging
from typing import Any, Dict, List, Optional

from ..shared.identity import norm_ip, norm_mac, norm_serial
from ..storage.http import JsonClient
from . import IpamConfig, IpamError

logger = logging.getLogger(__name__)

# The ONLY request types ICO may send (GestióIP also accepts writes on GET, so the method is not a guard).
READ_REQUEST_TYPES = frozenset({"version", "listClients", "listHosts", "readHost", "listNetworks", "readNetwork",
                                "usedNetworkAddresses", "listVlans", "readVlan"})
PAGE = 9999
MAX_PAGES = 20


def _first(v: Any) -> Any:
    return v[0] if isinstance(v, list) and v else v


def _txt(v: Any, n: int = 255) -> Optional[str]:
    if isinstance(v, (dict, list)):
        return None
    t = str(v).strip() if v is not None else ""
    return t[:n] or None


def _rows(obj: Any, name: str) -> List[Any]:
    """Every value under key `name` anywhere in the answer (object or list → list)."""
    out: List[Any] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == name:
                out.extend(v if isinstance(v, list) else [v])
            else:
                out.extend(_rows(v, name))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_rows(v, name))
    return out


def api_error(res: Any) -> Optional[str]:
    """The "error" text of a GestióIP answer ({"<request>Result": {"error": "…"}}), None when empty."""
    for e in _rows(res, "error"):
        e = _first(e)
        if isinstance(e, dict):
            e = e.get("string") or ""
        if str(e or "").strip():
            return str(e).strip()[:300]
    return None


def _custom(h: Dict[str, Any]) -> Dict[str, str]:
    cc = h.get("customColumns") or {}
    if not isinstance(cc, dict):
        return {}
    return {str(k)[:64]: str(v).strip()[:255] for k, v in cc.items() if isinstance(v, (str, int, float)) and str(v).strip()}


def _pick(custom: Dict[str, str], column: str) -> Optional[str]:
    if not column:
        return None
    want = column.strip().lower()
    return next((v for k, v in custom.items() if k.strip().lower() == want), None)


def parse_host(h: Dict[str, Any], serial_column: str = "", mac_column: str = "") -> Optional[Dict[str, Any]]:
    if not isinstance(h, dict):
        return None
    ip = norm_ip(h.get("IP") or h.get("ip"))
    if not ip:
        return None
    custom = _custom(h)
    return {"hostId": _txt(h.get("id"), 32), "ip": ip, "ipVersion": "v6" if ":" in ip else "v4",
            "hostname": _txt(h.get("hostname")), "descr": _txt(h.get("descr")), "site": _txt(h.get("site"), 128),
            "category": _txt(h.get("cat"), 128), "comment": _txt(h.get("comment"), 500), "alive": _txt(h.get("alive"), 32),
            "lastResponse": _txt(h.get("last_response"), 64),
            "serial": norm_serial(_pick(custom, serial_column)) or None, "mac": norm_mac(_pick(custom, mac_column)),
            "custom": custom}


def parse_network(n: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(n, dict):
        return None
    ip, bm = _txt(n.get("IP") or n.get("ip"), 64), _txt(n.get("BM") or n.get("bm"), 4)
    try:
        net = ipaddress.ip_network(f"{ip}/{bm}", strict=False)
    except (TypeError, ValueError):
        return None
    custom = _custom(n)
    return {"networkId": _txt(n.get("id"), 32), "cidr": str(net), "descr": _txt(n.get("descr")), "site": _txt(n.get("site"), 128),
            "category": _txt(n.get("cat"), 128), "comment": _txt(n.get("comment"), 500),
            "rootnet": str(n.get("rootnet") or "").strip().lower() in ("1", "y", "yes", "true"),
            "vlan": _pick(custom, "vlan"), "custom": custom}


def parse_vlan(v: Any) -> Optional[Dict[str, Any]]:
    """listVlans CSV "id,number,name,descr,provider,bg,font" (or an object in a newer version)."""
    if isinstance(v, dict):
        num, name, descr = v.get("number"), v.get("name"), v.get("descr") or v.get("comment")
    else:
        parts = [p.strip() for p in str(v or "").split(",")]
        if len(parts) < 3:
            return None
        num, name, descr = parts[1], parts[2], parts[3] if len(parts) > 3 else None
    try:
        number = int(str(num).strip())
    except (TypeError, ValueError):
        return None
    return {"number": number, "name": _txt(name, 128), "descr": _txt(descr)}


@functools.lru_cache(maxsize=8192)
def _net(cidr: str):
    return ipaddress.ip_network(cidr)        # parsed once per CIDR, not once per (host × network)


def network_of(ip: str, nets: List[Dict[str, Any]]) -> Optional[str]:
    """Most specific network (longest prefix) holding `ip`."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return None
    best = None
    for n in nets:
        net = _net(n["cidr"])
        if a.version == net.version and a in net and (best is None or net.prefixlen > best.prefixlen):
            best = net
    return str(best) if best else None


class GestioIpClient:
    def __init__(self, cfg: IpamConfig):
        if not (cfg.username and cfg.password):
            raise IpamError("Chưa nhập tài khoản GestióIP và mật khẩu.")
        self.cfg = cfg
        path = "/" + (cfg.api_path or "").strip().lstrip("/")
        self.path = path if path != "/" else "/gestioip/api/api.cgi"
        self.http = JsonClient(cfg, f"https://{cfg.host}:{cfg.port}", product="GestióIP", api_hint="GestióIP (module API)")
        token = base64.b64encode(f"{cfg.username}:{cfg.password}".encode()).decode()
        self.http.headers["Authorization"] = f"Basic {token}"

    def call(self, request_type: str, **params: Any) -> Any:
        if request_type not in READ_REQUEST_TYPES:
            raise IpamError(f"Chặn request_type {request_type}: ICO chỉ đọc dữ liệu GestióIP.")
        q = {"request_type": request_type, "output_type": "json", **{k: v for k, v in params.items() if v is not None}}
        if request_type != "version":
            q.setdefault("client_name", self.cfg.client_name or "DEFAULT")
        res = self.http.get(self.path, q)
        err = api_error(res)
        if err:
            raise IpamError(f"GestióIP báo lỗi ({request_type}): {err}")
        return res

    def pages(self, request_type: str, row_key: str, **params: Any) -> List[Any]:
        out: List[Any] = []
        for page in range(MAX_PAGES):
            batch = _rows(self.call(request_type, limit=PAGE, page=page, **params), row_key)
            out.extend(batch)
            if len(batch) < PAGE:
                return out
        logger.warning("GestióIP %s: more than %d pages, rest skipped", request_type, MAX_PAGES)
        return out


def collect(cfg: IpamConfig) -> Dict[str, Any]:
    c = GestioIpClient(cfg)
    nets = [n for n in (parse_network(x) for x in c.pages("listNetworks", "Network", no_csv="yes")) if n]
    hosts = [h for h in (parse_host(x, cfg.serial_column, cfg.mac_column) for x in c.pages("listHosts", "Host", no_csv="yes")) if h]
    try:
        vlans = [v for v in (parse_vlan(x) for x in _rows(c.call("listVlans"), "Vlan")) if v]
    except IpamError as exc:            # VLANs are optional context: never fail the source for them
        logger.info("GestióIP listVlans: %s", exc)
        vlans = []
    for h in hosts:
        h["network"] = network_of(h["ip"], nets)
    return {"networks": nets, "hosts": hosts, "vlans": vlans}


def test(cfg: IpamConfig) -> str:
    """Read the API version and one host page of the client."""
    c = GestioIpClient(cfg)
    c.cfg.timeout = min(cfg.timeout, 30)
    v = next((str(_first(x)) for x in _rows(c.call("version"), "version") if x), "?")
    c.call("listHosts", limit=1, page=0)
    return f"Kết nối thành công tới GestióIP {cfg.host} (API {v}, client {cfg.client_name or 'DEFAULT'})."
