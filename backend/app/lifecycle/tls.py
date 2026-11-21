"""Server certificates of every source, read by a TLS handshake only (ICO v2 G9b) — no HTTP request is sent.

The handshake does not verify the chain (a self-signed or expired certificate must still be read); nothing is sent after it.
Targets: each vCenter (443), its ESXi hosts (443), NetBackup primary (1556), Dell OME, storage arrays, GestióIP, ServiceDesk Plus
and the app's own PUBLIC_URL. A DEMO source gets a generated certificate, no socket is opened. Runs on the leader, once a day."""
import datetime as dt
import hashlib
import json
import logging
import socket
import ssl
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import TlsCertState

logger = logging.getLogger(__name__)

TIMEOUT = 5.0
WORKERS = 16
EVERY = dt.timedelta(hours=24)
MAX_TARGETS = 2000


def targets(settings, host_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every endpoint to check: {kind, name, host, port, demo, vcenterKey}."""
    out = []

    def add(kind, cfg, default_port, port=None):
        host = getattr(cfg, "host", "") or ""
        if host:
            out.append({"kind": kind, "name": getattr(cfg, "name", "") or host, "host": host,
                        "port": int(port or getattr(cfg, "port", 0) or default_port), "demo": bool(getattr(cfg, "demo", False)),
                        "vcenterKey": getattr(cfg, "key", None) if kind == "vcenter" else None})
    vcs = {c.key: c for c in settings.vcenters()}
    for c in vcs.values():
        add("vcenter", c, 443)
    for h in host_rows:
        vc = vcs.get(h.get("vcenterKey") or "")
        if vc is not None and getattr(vc, "platform", "vmware") == "vmware" and h.get("host"):
            out.append({"kind": "esxi", "name": h["host"], "host": h["host"], "port": 443, "demo": bool(vc.demo), "vcenterKey": vc.key})
    for c in settings.netbackups():
        add("netbackup", c, 1556, 1556)
    for kind, cfgs in (("ome", settings.hardware()), ("storage", settings.storages()), ("ipam", settings.ipam()), ("itsm", settings.itsm())):
        for c in cfgs:
            add(kind, c, 443)
    u = urlparse(settings.public_url or "")
    if u.scheme == "https" and u.hostname:
        out.append({"kind": "app", "name": "ICO (PUBLIC_URL)", "host": u.hostname, "port": u.port or 443, "demo": False, "vcenterKey": None})
    seen, uniq = set(), []
    for t in out:
        t["target"] = f"{t['kind']}:{t['host']}:{t['port']}"[:255]
        if t["target"] not in seen:
            seen.add(t["target"])
            uniq.append(t)
    return uniq[:MAX_TARGETS]


def _name(n) -> str:
    return ", ".join(f"{a.rfc4514_attribute_name}={a.value}" for a in n)[:500]


def parse(der: bytes) -> Dict[str, Any]:
    from cryptography import x509
    cert = x509.load_der_x509_certificate(der)
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        names = [str(v) for v in san.get_values_for_type(x509.DNSName)] + [str(v) for v in san.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        names = []
    not_after = getattr(cert, "not_valid_after_utc", None)
    not_after = not_after.replace(tzinfo=None) if not_after is not None else cert.not_valid_after
    return {"notAfter": not_after, "subject": _name(cert.subject), "issuer": _name(cert.issuer), "san": names[:50],
            "selfSigned": cert.issuer == cert.subject}


def read(host: str, port: int, timeout: float = TIMEOUT) -> Dict[str, Any]:
    """Certificate of host:port from the TLS handshake (chain not verified: the certificate itself is what is checked)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
        if not der:
            return {"error": "Máy chủ không gửi chứng chỉ."}
        return parse(der)
    except (OSError, ssl.SSLError, ValueError) as exc:
        return {"error": f"Không đọc được chứng chỉ: {type(exc).__name__}: {str(exc)[:200]}"}


def demo_cert(t: Dict[str, Any], now: dt.datetime) -> Dict[str, Any]:
    """Stable fake certificate: most last a year, a few expire soon, ESXi hosts are self-signed."""
    n = int(hashlib.sha1(t["target"].encode()).hexdigest()[:6], 16)
    days = (12, 45, 80)[n % 3] if n % 5 == 0 else 200 + n % 400
    issuer = f"CN={t['host']}" if t["kind"] == "esxi" else "CN=Corp Issuing CA, O=Demo"
    return {"notAfter": (now + dt.timedelta(days=days)).replace(microsecond=0), "subject": f"CN={t['host']}", "issuer": issuer,
            "san": [t["host"]], "selfSigned": t["kind"] == "esxi"}


def scan(db: Session, settings, host_rows: List[Dict[str, Any]], now: Optional[dt.datetime] = None) -> Dict[str, int]:
    """Read every target (in parallel, short timeout) and replace tls_cert_state. Caller commits."""
    now = now or dt.datetime.utcnow()
    ts = targets(settings, host_rows)
    real = [t for t in ts if not t["demo"]]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        got = dict(zip([t["target"] for t in real], pool.map(lambda t: read(t["host"], t["port"]), real)))
    db.query(TlsCertState).delete(synchronize_session=False)
    rows = []
    for t in ts:
        c = demo_cert(t, now) if t["demo"] else got[t["target"]]
        rows.append({"target": t["target"], "kind": t["kind"], "name": str(t["name"])[:255], "host": t["host"][:255], "port": t["port"],
                     "vcenter_key": t["vcenterKey"], "not_after": c.get("notAfter"), "subject": c.get("subject"), "issuer": c.get("issuer"),
                     "san": json.dumps(c.get("san") or []), "self_signed": c.get("selfSigned"), "error": (c.get("error") or None) and c["error"][:500],
                     "checked_at": now})
    db.bulk_insert_mappings(TlsCertState, rows)
    return {"targets": len(rows), "errors": sum(1 for r in rows if r["error"])}


def maybe_scan() -> Optional[Dict[str, int]]:
    """Leader job: scan when the last one is older than EVERY (None = not due)."""
    from .. import data_access
    from ..config import get_settings
    from ..database import SessionLocal
    with SessionLocal() as db:
        last = db.query(func.max(TlsCertState.checked_at)).scalar()
        if last is not None and dt.datetime.utcnow() - last < EVERY:
            return None
        hosts, _vms, _ = data_access.get_latest_combined(db, None)
        res = scan(db, get_settings(), hosts)
        db.commit()
        return res
