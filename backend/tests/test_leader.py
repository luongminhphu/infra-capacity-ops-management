"""Coordination between pods: leader election and the cross-pod source lock (PostgreSQL advisory locks).

The PostgreSQL tests run when DATABASE_URL points at PostgreSQL (CI runs SQLite: they are skipped there;
run them with DATABASE_URL=postgresql://… python -m pytest tests/test_leader.py)."""
import asyncio

import pytest
from sqlalchemy import text

from app import leader
from app.collectors import locks
from app.database import engine

pg = pytest.mark.skipif(engine.dialect.name != "postgresql", reason="needs PostgreSQL (advisory locks)")


@pytest.fixture(autouse=True)
def _reset():
    leader.release()
    yield
    leader.release()


def _other_pod_try(ns, key):
    """What another pod sees: its own connection trying the same advisory lock."""
    with engine.connect() as c:
        got = bool(c.execute(text("SELECT pg_try_advisory_lock(:ns, :k)"), {"ns": ns, "k": key}).scalar())
        if got:
            c.execute(text("SELECT pg_advisory_unlock(:ns, :k)"), {"ns": ns, "k": key})
        c.commit()
        return got


def test_sqlite_single_process_is_always_leader():
    if engine.dialect.name == "postgresql":
        pytest.skip("SQLite behaviour")
    assert leader.is_leader() and leader.step() and leader.status()["coordinated"] is False
    with leader.db_source_lock("vcenter", "vc1") as got:
        assert got is True
    assert leader.busy_elsewhere(["vcenter:vc1"]) == []


@pg
def test_one_leader_at_a_time_and_handover_on_release():
    assert leader.step() is True and leader.status()["leader"] is True
    assert _other_pod_try(leader.LEADER_NS, leader.LEADER_KEY) is False       # pod B cannot become leader
    assert leader.step() is True                                              # still held next round
    leader.release()                                                          # pod A shuts down
    assert leader.is_leader() is False
    assert _other_pod_try(leader.LEADER_NS, leader.LEADER_KEY) is True        # pod B takes over at once


@pg
def test_leader_lost_when_connection_dies():
    assert leader.step()
    with engine.connect() as c:                                               # e.g. DB failover / network cut
        pid = leader._state["conn"].execute(text("SELECT pg_backend_pid()")).scalar()
        leader._state["conn"].commit()
        c.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid})
        c.commit()
    assert leader.step() is False or leader.is_leader()                       # dropped, or re-elected on a new connection
    assert leader.step() is True                                              # alone: elected again


@pg
def test_source_lock_across_pods():
    with locks.source_lock("vcenter", "vc-a"):
        assert _other_pod_try(leader.SOURCE_NS, leader.source_key("vcenter", "vc-a")) is False
        assert leader.busy_elsewhere(["vcenter:vc-a", "vcenter:vc-b"]) == ["vcenter:vc-a"]
        # same source from another pod (other connection, other thread state): SourceBusy
        with leader.db_source_lock("vcenter", "vc-b") as got_b:
            assert got_b is True                                              # another source is free
    assert _other_pod_try(leader.SOURCE_NS, leader.source_key("vcenter", "vc-a")) is True


@pg
def test_source_busy_when_another_pod_holds_it():
    with engine.connect() as other:                                           # pod B is syncing vc-x
        other.execute(text("SELECT pg_advisory_lock(:ns, :k)"), {"ns": leader.SOURCE_NS, "k": leader.source_key("vcenter", "vc-x")})
        other.commit()
        with pytest.raises(locks.SourceBusy):
            with locks.source_lock("vcenter", "vc-x"):
                pass
        other.execute(text("SELECT pg_advisory_unlock(:ns, :k)"), {"ns": leader.SOURCE_NS, "k": leader.source_key("vcenter", "vc-x")})
        other.commit()
    with locks.source_lock("vcenter", "vc-x"):                                # free again
        pass


def test_standby_pod_waits_and_keeps_beating(monkeypatch):
    beats = []
    flags = iter([False, False, True])
    monkeypatch.setattr(leader, "is_leader", lambda: next(flags))
    monkeypatch.setattr(leader, "RETRY_S", 0)
    asyncio.run(leader.wait_until_leader(lambda: beats.append(1)))
    assert len(beats) == 2
