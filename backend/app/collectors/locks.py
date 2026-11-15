"""Per-source single-flight lock shared by the scheduler and the manual "Đồng bộ" buttons.

Two syncs of the same source at once duplicate the work against vCenter / NetBackup and race on
the same rows (uq_backup_job). The lock is an in-process `threading.Lock` keyed by source kind
and key: enough because the app runs ONE replica with `strategy: Recreate` (k8s/04-app.yaml),
so no two processes ever poll at the same time. Moving to several replicas or RollingUpdate
needs a database lock (e.g. pg_try_advisory_lock) instead.

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
    lock = _lock_for(kind, key)
    if not lock.acquire(blocking=False):
        raise SourceBusy(kind, key)
    try:
        yield
    finally:
        lock.release()


def busy_sources() -> List[str]:
    """`kind:key` of every source being synced right now."""
    with _guard:
        items = list(_locks.items())
    return sorted(name for name, lock in items if lock.locked())
