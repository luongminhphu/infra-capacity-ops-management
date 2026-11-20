"""IP address management (IPAM) sources read-only: GestióIP 3.5 API (commercial API module of GestióIP).

Sources are added in Cài đặt → Nguồn dữ liệu (data_sources, kind "gestioip"); the password is encrypted with
secrets_box and never returned. The GestióIP account only needs read access to the client.

Read-only rule (CLAUDE.md rule 1): the GestióIP API selects the action with `request_type` and accepts it on GET as well
as POST (its own guide deletes a host with a GET) — so the HTTP method proves nothing. The client allows only the
read request types listed in client.READ_REQUEST_TYPES and refuses every other one (create / update / delete / reserve /
discovery) before anything leaves.
"""
from dataclasses import dataclass

from ..storage import StorageError as IpamError  # same error family: the scheduler shows its message as is

KINDS = ("gestioip",)
KIND_LABELS = {"gestioip": "GestióIP IPAM"}
DEFAULT_API_PATH = "/gestioip/api/api.cgi"

__all__ = ["IpamConfig", "IpamError", "KINDS", "KIND_LABELS", "DEFAULT_API_PATH"]


@dataclass
class IpamConfig:
    key: str
    name: str
    host: str
    port: int = 443
    username: str = ""
    password: str = ""
    verify_ssl: bool = False
    ca_pem: str = ""
    demo: bool = False
    timeout: int = 60
    client_name: str = "DEFAULT"          # GestióIP "client" (tenant)
    api_path: str = DEFAULT_API_PATH
    serial_column: str = ""               # custom column of hosts holding the serial / service tag (optional)
    mac_column: str = ""                  # custom column holding the MAC address (optional)
    kind: str = "gestioip"
    origin: str = "ui"
