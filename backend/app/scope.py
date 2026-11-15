"""One scope model for every page and report (super app): which vCenter, which clusters, which hosts.

Query conventions shared by the API:
* `vcenter`  — "all" / empty = every vCenter; a key = that vCenter ("import" = the manual import).
* `clusters` / `hosts` — comma separated. Omitted = no filter; given but empty = filter to nothing
  (a deliberate zero-item selection in the UI)."""
from dataclasses import dataclass
from typing import List, Optional


def split_csv(value: Optional[str]) -> Optional[List[str]]:
    """None when the parameter is omitted; a list (possibly empty) when it is given."""
    if value is None:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def scope_keys(vcenter: Optional[str]) -> Optional[List[str]]:
    """vCenter keys for data_access.get_latest_combined (None = every vCenter). The manual import is
    always merged by get_latest_combined, so "import" needs no special key."""
    return None if (not vcenter or vcenter == "all") else [vcenter]


def scope_label(vcenter: Optional[str]) -> str:
    return "Toàn bộ vCenter" if (not vcenter or vcenter == "all") else ("Dữ liệu nhập tay" if vcenter == "import" else vcenter)


@dataclass
class Scope:
    vcenter: Optional[str] = None
    clusters: Optional[List[str]] = None
    hosts: Optional[List[str]] = None

    @classmethod
    def from_query(cls, vcenter: Optional[str] = "all", clusters: Optional[str] = None, hosts: Optional[str] = None) -> "Scope":
        return cls(vcenter=vcenter if vcenter and vcenter != "all" else None, clusters=split_csv(clusters), hosts=split_csv(hosts))

    @property
    def keys(self) -> Optional[List[str]]:
        return scope_keys(self.vcenter)

    @property
    def label(self) -> str:
        parts = [scope_label(self.vcenter)]
        if self.clusters is not None:
            parts.append(f"cluster: {', '.join(self.clusters) or '(không chọn)'}")
        if self.hosts is not None:
            parts.append(f"host: {', '.join(self.hosts) or '(không chọn)'}")
        return " · ".join(parts)
