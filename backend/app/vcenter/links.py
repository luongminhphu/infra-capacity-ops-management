"""vSphere Client deep links (read-only: a URL the user opens in the browser, no call to vCenter).

The HTML5 client (6.7 / 7 / 8) opens an inventory object from its managed object reference:
  https://<vcenter>/ui/#?extensionId=vsphere.core.inventory.serverObjectViewsExtension
      &objectId=urn:vmomi:<vim type>:<moId>:<vCenter instanceUuid>&navigator=<tree>
Every part comes from vCenter or the source settings, so each one is checked before it goes into a URL."""
import re
from typing import Dict, Optional, Tuple
from urllib.parse import quote

_HOST = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
_MOID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")             # host-123, vm-45, datastore-12, domain-c7
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
NAVIGATOR = {"HostSystem": "hostsAndClustersView", "ClusterComputeResource": "hostsAndClustersView",
             "ComputeResource": "hostsAndClustersView", "ResourcePool": "hostsAndClustersView",
             "VirtualMachine": "vmsAndTemplatesView", "VirtualApp": "vmsAndTemplatesView",
             "Datastore": "storageView", "StoragePod": "storageView",
             "Network": "networkingView", "DistributedVirtualPortgroup": "networkingView",
             "Datacenter": "hostsAndClustersView", "Folder": "hostsAndClustersView"}


def object_url(host: Optional[str], port: Optional[int], vim_type: Optional[str], moid: Optional[str],
               vc_uuid: Optional[str]) -> Optional[str]:
    """None unless every part is well-formed (no URL built from unexpected input)."""
    if not (host and vim_type and moid and vc_uuid):
        return None
    if not _HOST.match(host) or vim_type not in NAVIGATOR or not _MOID.match(moid) or not _UUID.match(vc_uuid):
        return None
    if port is not None and not (isinstance(port, int) and 0 < port < 65536):
        return None
    authority = host if port in (None, 443) else f"{host}:{port}"
    urn = quote(f"urn:vmomi:{vim_type}:{moid}:{vc_uuid.lower()}", safe=":-")
    return (f"https://{authority}/ui/#?extensionId=vsphere.core.inventory.serverObjectViewsExtension"
            f"&objectId={urn}&navigator=vsphere.core.viTree.{NAVIGATOR[vim_type]}")


def ui_hosts(settings) -> Dict[str, Tuple[str, Optional[int]]]:
    """vCenter key → (host, port) of the real VMware vCenters (demo and Proxmox / Nutanix have no vSphere Client)."""
    out = {}
    for c in settings.vcenters():
        if getattr(c, "demo", False) or getattr(c, "platform", "vmware") != "vmware" or not c.host:
            continue
        out[c.key] = (c.host, c.port)
    return out
