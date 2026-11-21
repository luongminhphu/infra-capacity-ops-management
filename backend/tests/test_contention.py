"""ICO v2 G9c: contention — ready / co-stop formula, levels, sustained balloon / swap, cluster share, the QueryPerf reader (batches,
cut by limit, counter ids), store + metrics history, inbox items."""
import datetime as dt
from types import SimpleNamespace as NS

from pyVmomi import vim

from app.contention import rules as R, service
from app.contention.models import ContentionState
from app.models import MetricPoint
from app.vcenter import perf
from test_cmdb import db  # noqa: F401  (fixture: private SQLite)


def test_ready_formula_and_units():
    assert perf.ready_pct(1600, 4) == 2.0                 # 1 600 ms in a 20 s sample on 4 vCPU = 2 %
    assert perf.ready_pct(2000, 1) == 10.0 and perf.ready_pct(100, 0) is None
    assert perf.to_row({"ready": 3200, "costop": 800, "balloon": 524288, "swapped": 0, "latency": 23.44}, 8) == \
        {"readyPct": 2.0, "costopPct": 0.5, "balloonMB": 512.0, "swappedMB": 0.0, "latencyMs": 23.4}


def test_levels_and_sustained_memory():
    assert R.assess({"readyPct": 4.9}) == ("ok", [])
    assert R.assess({"readyPct": 5.0}) == ("warn", ["CPU ready 5%"])
    assert R.assess({"readyPct": 12.5, "costopPct": 3.1, "latencyMs": 55}) == ("bad", ["CPU ready 12,5%", "độ trễ disk 55 ms", "co-stop 3,1%"])
    assert R.assess({"balloonMB": 512, "balloonPolls": 2})[0] == "ok"            # not yet sustained
    assert R.assess({"balloonMB": 512, "balloonPolls": 3}) == ("warn", ["balloon 512 MB (3 lần liên tiếp)"])
    assert R.assess({"swappedMB": 64, "swapPolls": 3})[0] == "bad"


def test_cluster_share():
    vms = [{"vcenterKey": "vc1", "cluster": "PRD", "level": "warn" if i < 2 else "ok", "readyPct": 6 if i < 2 else 1} for i in range(10)]
    vms += [{"vcenterKey": "vc1", "cluster": "DEV", "level": "warn" if i < 1 else "ok", "readyPct": 1} for i in range(10)]
    c = {x["cluster"]: x for x in R.clusters(vms)}
    assert (c["PRD"]["level"], c["PRD"]["sharePct"], c["PRD"]["readyAvg"]) == ("bad", 20.0, 2.0)   # exactly 20 % → CẢNH BÁO
    assert (c["DEV"]["level"], c["DEV"]["sharePct"]) == ("ok", 10.0)


class _PM:
    """PerformanceManager stand-in: counter list + QueryPerf answering each entity with fixed samples."""
    def __init__(self):
        self.calls = []
        names = [("cpu", "ready", "summation", 1), ("cpu", "costop", "summation", 2), ("mem", "vmmemctl", "average", 3),
                 ("mem", "swapped", "average", 4), ("disk", "maxTotalLatency", "latest", 5), ("cpu", "usage", "average", 6)]
        self.perfCounter = [NS(key=k, groupInfo=NS(key=g), nameInfo=NS(key=n), rollupType=r) for g, n, r, k in names]

    def QueryPerf(self, querySpec):
        self.calls.append(len(querySpec))
        out = []
        for s in querySpec:
            vals = {1: [1600, 1600], 2: [0], 3: [0], 4: [0], 5: [10, 30]}
            out.append(NS(entity=s.entity, value=[NS(id=NS(counterId=m.counterId), value=vals[m.counterId]) for m in s.metricId]))
        return out


def test_reader_batches_cut_and_counters():
    pm = _PM()
    vms = {f"vm-{i}": (vim.VirtualMachine(f"vm-{i}"), {"summary": NS(runtime=NS(powerState="poweredOn" if i != 3 else "poweredOff",
                                                                                host=vim.HostSystem("host-1")),
                                                                    config=NS(name=f"app{i:03d}", numCpu=4 if i % 2 else 8))})
           for i in range(120)}
    hosts = {"host-1": (vim.HostSystem("host-1"), {"name": "esx01"})}
    res = perf.read(NS(perfManager=pm), hosts, vms, {"host-1": ("esx01", "PRD")}, max_vms=100)
    assert pm.calls == [50, 50, 1]                       # 2 VM batches of 50 + 1 host batch; one call per batch, not per VM
    assert (len(res["vms"]), res["cut"]) == (100, 19)    # 119 powered on, largest vCPU first, 19 cut and reported
    assert all(v["vcpu"] == 8 for v in res["vms"][:59]) and res["vms"][0]["readyPct"] == 1.0     # 1 600 ms on 8 vCPU = 1 %
    assert res["hosts"] == [{"host": "esx01", "cluster": "PRD", "balloonMB": 0.0, "latencyMs": 20.0}]


def test_store_history_and_inbox(db, monkeypatch):  # noqa: F811
    from app import inbox
    monkeypatch.setattr("app.vm_ip.attach", lambda _db, rows, **_k: rows)
    hot = {"vm": "db01", "cluster": "PRD", "host": "esx01", "vcpu": 8, "readyPct": 12.0, "costopPct": 1.0, "balloonMB": 0.0,
           "swappedMB": 256.0, "latencyMs": 5.0}
    quiet = [{"vm": f"app{i}", "cluster": "PRD", "host": "esx01", "vcpu": 2, "readyPct": 0.5, "costopPct": 0.0, "balloonMB": 0.0,
              "swappedMB": 0.0, "latencyMs": 2.0} for i in range(9)]
    t0 = dt.datetime(2026, 11, 21, 1, 0)
    for k in range(3):
        n = service.store(db, "vc1", {"perf": {"vms": [hot] + quiet, "hosts": [{"host": "esx01", "cluster": "PRD", "latencyMs": 4.0, "balloonMB": 0}]}},
                          t0 + dt.timedelta(minutes=15 * k))
        db.commit()
    assert n == 11
    assert db.query(MetricPoint).filter_by(kind="vm").count() == 3 * 10 * 3     # 3 points per VM and sync
    s = db.query(ContentionState).filter_by(name="db01").one()
    assert s.swap_polls == 3
    rep = service.report(db)
    assert (rep["vms"][0]["name"], rep["vms"][0]["level"]) == ("db01", "bad")
    assert rep["clusters"][0]["sharePct"] == 10.0 and rep["summary"]["badVms"] == 1
    h = service.history(db, "vm", "db01", "vc1", 7, now=t0 + dt.timedelta(hours=1))
    assert [p[1] for p in h["readyPct"]] == [12.0, 12.0, 12.0]
    assert service.store(db, "vc1", {"perf": None}) == 0 and db.query(ContentionState).count() == 11    # read error: kept
    items = inbox.contention_items(db)
    assert [i["title"] for i in items] == ["VM db01: nghẽn tài nguyên"]
    assert "CPU ready 12%" in items[0]["detail"] and "swap 256 MB (3 lần liên tiếp)" in items[0]["detail"]
