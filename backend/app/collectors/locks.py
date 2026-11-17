"""Per-source single-flight lock shared by the scheduler and the manual "Đồng bộ" buttons.
Two syncs of the same source at once duplicate the work against vCenter / NetBackup and race on
the same rows (uq_backup_job). Two layers:
* an in-process `threading.Lock` keyed by source kind and key (threads of one pod);
* a PostgreSQL advisory lock (leader.db_source_lock) held for the whole sync, so a manual sync on
  pod B waits for nothing and gets "đang đồng bộ" while the leader pod A polls the same source.
Acquisition never waits: a busy source raises SourceBusy (HTTP 409 for a manual sync, "skipped
this round" for the scheduler).
"""
import threading
from contextlib import contextmanager
from typing import Dict, Iterator, List

BUSY_MESSAGE = "Đang đồng bộ nguồn này, thử lại sau."

_locks: Dict[str, threading.Lock] = {}
_guard = threading.Lock()


class SourceBusy(RuntimeError):
    """Another sync of the same source is still running."""

    def __init__(self, kind: str, key: str):
        super().__init__(BUSY_MESSAGE)
        self.kind, self.key = kind, key


def _lock_for(kind: str, key: str) -> threading.Lock:
    name = f"{kind}:{key}"
    with _guard:
        lock = _locks.get(name)
        if lock is None:
            lock = _locks[name] = threading.Lock()
        return lock


@contextmanager
def source_lock(kind: str, key: str) -> Iterator[None]:
    from ..leader import db_source_lock
    lock = _lock_for(kind, key)
    if not lock.acquire(blocking=False):
        raise SourceBusy(kind, key)
    try:
        with db_source_lock(kind, key) as got:
            if not got:                      # another pod is syncing this source
                raise SourceBusy(kind, key)
            yield
    finally:
        lock.release()


def busy_sources() -> List[str]:
    """`kind:key` of every source being synced right now."""
    from ..leader import busy_elsewhere
    with _guard:
        items = list(_locks.items())
    here = {name for name, lock in items if lock.locked()}
    return sorted(here | set(busy_elsewhere(known_sources())))


def known_sources():
    """`kind:key` of every configured source (to ask PostgreSQL which ones another pod is syncing)."""
    try:
        from ..config import get_settings
        s = get_settings()
        return [f"vcenter:{c.key}" for c in s.vcenters()] + [f"netbackup:{c.key}" for c in s.netbackups()]
    except Exception:
        return []
