"""Canonical identity keys shared by every source (CI registry, DC assets, Opex, NetBackup, storage).

Pure functions only — no imports from business modules. Each returns a normalised string, or an
empty string / None for unusable input (documented per function) so callers can skip the key.
"""
import ipaddress
import re
from typing import Any, Optional

_NON_ALNUM = re.compile(r"[^A-Z0-9]")
_HEX = re.compile(r"[^0-9a-f]")
_MAC_SEP = re.compile(r"[\s:.\-]")
_MAC12 = re.compile(r"[0-9a-f]{12}")


def norm_serial(s: Any) -> str:
    """Serial number / service tag: upper case, letters and digits only ("71fhkz3 " → "71FHKZ3"); "" if none."""
    return _NON_ALNUM.sub("", str(s or "").upper())[:128]


def norm_fqdn(s: Any) -> str:
    """DNS name: trimmed, lower case, trailing dot dropped ("ESX01.Corp.Local." → "esx01.corp.local")."""
    return str(s if s is not None else "").strip().lower().rstrip(".")[:255]


def short_name(s: Any) -> str:
    """First DNS label of a host name ("esx01.corp.local" → "esx01"); an IPv4 address is kept whole."""
    n = norm_fqdn(s)
    if not n:
        return ""
    return n if norm_ip(n) else n.split(".")[0]


def norm_mac(s: Any) -> Optional[str]:
    """MAC address as aa:bb:cc:dd:ee:ff from any of 'AA-BB-..', 'aabb.ccdd.eeff', 'AABBCCDDEEFF'; None if not 48 bits."""
    h = _MAC_SEP.sub("", str(s if s is not None else "").lower())
    if not _MAC12.fullmatch(h):
        return None
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


def norm_naa(s: Any) -> str:
    """SCSI NAA identifier: lower case, "naa." prefix and separators dropped ("naa.624A9370..." → "624a9370...")."""
    n = str(s if s is not None else "").strip().lower()
    if n.startswith("naa."):
        n = n[4:]
    return _HEX.sub("", n)[:64]


def norm_ip(s: Any) -> Optional[str]:
    """IPv4 / IPv6 address in canonical text form; None when the value is not a valid address."""
    v = str(s if s is not None else "").strip()
    if not v:
        return None
    try:
        return str(ipaddress.ip_address(v))
    except ValueError:
        return None


def nbu_client_key(name: Any) -> Optional[str]:
    """Key matching NetBackup clients with VMs / policy clients: lower-case short name (domain dropped),
    an IPv4 address kept whole; None for empty input."""
    if name is None:
        return None
    n = str(name).strip().lower()
    if not n:
        return None
    parts = n.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return n
    return parts[0]
