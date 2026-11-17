"""Coordination between ICO pods (several replicas, RollingUpdate) through PostgreSQL advisory locks.

* Leader: exactly one pod runs the background jobs (vCenter / NetBackup polling, operations alerts,
  retention, spend reminders). Every pod runs `leader_loop()`; the one holding the session-level
  advisory lock LEADER_KEY on its own dedicated connection is the leader. When that pod stops (deploy,
  crash, node loss) PostgreSQL drops its connection and the lock, and another pod takes over within
  RETRY_S seconds. The web API runs on every pod.
* Source lock: a sync of one source (scheduler or the "Đồng bộ ngay" button, on any pod) holds the
  advisory lock (SOURCE_NS, crc32(kind:key)) for its whole duration, on a dedicated connection.

On SQLite (tests, local single-process runs) there is one process: it is always the leader and the
source lock is the in-process one only (collectors/locks.py keeps that part).
"""
import asyncio
import logging
import threading
import zlib
from contextlib import contextmanager
from typing import Iterator, List

from sqlalchemy import text

logger = logging.getLogger(__name__)

LEADER_NS, LEADER_KEY = 7_410_317, 1           # (int4, int4) form: never collides with the migration lock
SOURCE_NS = 7_410_318
RETRY_S = 15

_state = {"leader": False, "conn": None, "since": None}
_state_lock = threading.Lock()


def _engine():
    from .database import engine
    return engine


def is_postgres() -> bool:
    return _engine().dialect.name == "postgresql"


def is_leader() -> bool:
    if not is_postgres():
        return True
    with _state_lock:
        return bool(_state["leader"])


def status() -> dict:
    pg = is_postgres()
    with _state_lock:                       # not via is_leader(): it takes the same (non-reentrant) lock
        return {"leader": bool(_state["leader"]) if pg else True, "coordinated": pg, "since": _state["since"]}


def _try_acquire() -> bool:
    """One attempt: open a dedicated connection and take the leader lock on it (kept open while leader)."""
    conn = _engine().connect()
    try:
        got = bool(conn.execute(text("SELECT pg_try_advisory_lock(:ns, :k)"), {"ns": LEADER_NS, "k": LEADER_KEY}).scalar())
        conn.commit()                 # end the implicit transaction; the session lock stays
    except Exception:
        conn.close()
        raise
    if not got:
        conn.close()
        return False
    import datetime as dt
    with _state_lock:
        _state.update(leader=True, conn=conn, since=dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z")
    logger.info("This pod is now the ICO leader (background jobs run here)")
    return True


def _still_held() -> bool:
    """The leader connection is alive (a dropped connection means the lock is gone)."""
    with _state_lock:
        conn = _state["conn"]
    if conn is None:
        return False
    try:
        conn.execute(text("SELECT 1")).scalar()
        conn.commit()
        return True
    except Exception:
        return False


def _drop(reason: str) -> None:
    with _state_lock:
        conn, was = _state["conn"], _state["leader"]
        _state.update(leader=False, conn=None, since=None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    if was:
        logger.warning("This pod is no longer the ICO leader (%s)", reason)


def step() -> bool:
    """One election round (also called by tests): keep or try to take leadership. Returns is_leader()."""
    if not is_postgres():
        return True
    if is_leader():
        if not _still_held():
            _drop("leader connection lost")
        return is_leader()
    try:
        _try_acquire()
    except Exception:
        logger.exception("Leader election attempt failed")
    return is_leader()


def release() -> None:
    """Graceful shutdown: give leadership away at once instead of waiting for the connection to time out."""
    with _state_lock:
        conn = _state["conn"]
    if conn is not None:
        try:
            conn.execute(text("SELECT pg_advisory_unlock(:ns, :k)"), {"ns": LEADER_NS, "k": LEADER_KEY})
            conn.commit()
        except Exception:
            pass
    _drop("shutdown")


async def leader_loop() -> None:
    while True:
        await asyncio.to_thread(step)
        await asyncio.sleep(RETRY_S)


async def wait_until_leader(beat=None) -> None:
    """Background jobs call this before each round: standby pods wait here (beating, so readiness stays green)."""
    while not is_leader():
        if beat:
            beat()
        await asyncio.sleep(RETRY_S)


# ---------------------------------------------------------------- per-source lock across pods
def source_key(kind: str, key: str) -> int:
    return zlib.crc32(f"{kind}:{key}".encode()) & 0x7FFFFFFF


@contextmanager
def db_source_lock(kind: str, key: str) -> Iterator[bool]:
    """Yields True when this pod holds the cross-pod lock of the source (always True off PostgreSQL)."""
    if not is_postgres():
        yield True
        return
    conn = _engine().connect()
    try:
        got = bool(conn.execute(text("SELECT pg_try_advisory_lock(:ns, :k)"), {"ns": SOURCE_NS, "k": source_key(kind, key)}).scalar())
        conn.commit()
        try:
            yield got
        finally:
            if got:
                try:
                    conn.execute(text("SELECT pg_advisory_unlock(:ns, :k)"), {"ns": SOURCE_NS, "k": source_key(kind, key)})
                    conn.commit()
                except Exception:
                    pass
    finally:
        conn.close()


def busy_elsewhere(names: List[str]) -> List[str]:
    """`kind:key` names whose cross-pod lock is held right now (by any pod)."""
    if not is_postgres() or not names:
        return []
    try:
        with _engine().connect() as conn:
            held = {int(r[0]) for r in conn.execute(text(
                "SELECT objid FROM pg_locks WHERE locktype = 'advisory' AND granted AND classid = :ns"), {"ns": SOURCE_NS})}
    except Exception:
        return []
    return [n for n in names if source_key(*n.split(":", 1)) in held]

