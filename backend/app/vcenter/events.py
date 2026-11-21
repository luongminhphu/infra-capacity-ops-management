"""vCenter events for the change log and incident grouping (ICO v2 G6) — READ ONLY.

eventManager.QueryEvents(filter) over the window cursor → now, only the event types below. vCenter returns at most
MAX_PER_QUERY events per call: a full answer means the window may hold more, so it is split in two and both halves are
read again (down to MIN_WINDOW). Same pyVmomi session as the inventory (client.collect_inventory_full)."""
import datetime as dt
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

KIND_OF = {
    "VmCreatedEvent": "vm_created", "VmRemovedEvent": "vm_removed", "VmReconfiguredEvent": "vm_reconfigured",
    "VmRelocatedEvent": "vm_migrated", "DrsVmMigratedEvent": "vm_migrated", "VmMigratedEvent": "vm_migrated",
    "VmPoweredOffEvent": "vm_powered_off", "VmPoweredOnEvent": "vm_powered_on",
    "HostConnectionLostEvent": "host_connection_lost", "HostEnteringMaintenanceModeEvent": "host_maintenance_enter",
    "HostExitMaintenanceModeEvent": "host_maintenance_exit", "VmRestartedOnAlternateHostEvent": "ha_vm_restarted",
    "DasHostFailedEvent": "ha_host_failed", "AlarmStatusChangedEvent": "alarm_status",
}
MAX_PER_QUERY = 1000
MIN_WINDOW = dt.timedelta(seconds=10)
FIRST_LOOKBACK = dt.timedelta(hours=24)        # no cursor yet: read the last day only
MAX_EVENTS = 20000                              # one sync never stores more (a huge backlog continues next round)


def _utc(t) -> Optional[dt.datetime]:
    if t is None:
        return None
    return t.astimezone(dt.timezone.utc).replace(tzinfo=None) if t.tzinfo else t


def _name(arg) -> Optional[str]:
    return getattr(arg, "name", None) if arg is not None else None


def parse(ev: Any) -> Optional[Dict[str, Any]]:
    """One pyVmomi event → {id, at, kind, eventType, userName, objectType, objectName, message, detail}."""
    etype = type(ev).__name__
    kind = KIND_OF.get(etype)
    if kind is None:
        return None
    vm, host = _name(getattr(ev, "vm", None)), _name(getattr(ev, "host", None))
    detail: Dict[str, Any] = {"eventType": etype}
    if host:
        detail["host"] = host
    if etype == "AlarmStatusChangedEvent":
        ent = getattr(ev, "entity", None)
        otype, oname = ("vm", vm) if vm else ("host", host) if host else ("entity", _name(ent))
        detail.update({"alarm": _name(getattr(ev, "alarm", None)), "from": getattr(ev, "from_", None), "to": getattr(ev, "to", None)})
    elif vm:
        otype, oname = "vm", vm
    else:
        otype, oname = "host", host
    if kind == "vm_migrated":                   # migrated / relocated events: `host` is the new host, sourceHost the old one
        detail.update({k: v for k, v in (("fromHost", _name(getattr(ev, "sourceHost", None))), ("toHost", host)) if v})
    return {"id": int(getattr(ev, "key", 0) or 0), "at": _utc(getattr(ev, "createdTime", None)), "kind": kind, "eventType": etype,
            "userName": (getattr(ev, "userName", None) or None), "objectType": otype, "objectName": oname,
            "message": (getattr(ev, "fullFormattedMessage", None) or "")[:1000], "detail": detail}


def read(content, since: dt.datetime, until: dt.datetime) -> List[Dict[str, Any]]:
    """Every watched event in (since, until] (naive UTC), oldest first."""
    from pyVmomi import vim
    out: List[Dict[str, Any]] = []

    def window(b: dt.datetime, e: dt.datetime) -> None:
        if len(out) >= MAX_EVENTS:
            return
        spec = vim.event.EventFilterSpec(eventTypeId=list(KIND_OF),
                                         time=vim.event.EventFilterSpec.ByTime(beginTime=b.replace(tzinfo=dt.timezone.utc),
                                                                               endTime=e.replace(tzinfo=dt.timezone.utc)))
        got = content.eventManager.QueryEvents(spec) or []
        if len(got) >= MAX_PER_QUERY and e - b > MIN_WINDOW:       # full page: there may be more → split the window
            mid = b + (e - b) / 2
            window(b, mid)
            window(mid, e)
            return
        out.extend(x for x in (parse(ev) for ev in got) if x and x["at"])
    window(since, until)
    uniq = {(x["id"], x["at"]): x for x in out}                      # the two halves share their boundary second
    return sorted(uniq.values(), key=lambda x: (x["at"], x["id"]))
