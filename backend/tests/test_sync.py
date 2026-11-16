"""Sync performance refactor: same results as before, fewer round trips."""
import datetime as dt
import random
from types import SimpleNamespace as NS

import pytest
from pyVmomi import vim, vmodl

from app.collectors import scheduler
from app.config import get_settings
from app.database import SessionLocal
from app.models import BackupJob, SnapshotRun, VmSnapshot
from app.vcenter import client as vc


@pytest.fixture(autouse=True, scope="module")
def _schema():
    from app.database import ensure_schema
    ensure_schema()


# ---------------------------------------------------------------------------
# Idle / powered-off streaks: one query instead of a history walk per VM
# ---------------------------------------------------------------------------
def _reference(history, v, now):
    """The previous algorithm: walk this VM's snapshots newest first."""
    if v["isPoweredOn"]:
        if v["cpuUsagePctRaw"] is not None and v["cpuUsagePctRaw"] >= 5.0:
            return 0
        first = None
        for h in history:
            if h["on"] and (h["cpu"] or 0) < 5.0:
                first = h["at"]
            else:
                break
    else:
        first = None
        for h in history:
            if not h["on"]:
                first = h["at"]
            else:
                break
    return max(0.0, (now - first).total_seconds() / 86400.0) if first else 0


def test_enrich_activity_matches_full_history_walk():
    rnd = random.Random(7)
    key = "vc-enrich-test"
    names = [f"vm{i}" for i in range(40)]
    hist = {n: [] for n in names}                    # newest first, like the old query
    t0 = dt.datetime(2026, 1, 1)
    db = SessionLocal()
    try:
        for k in range(30):
            now = t0 + dt.timedelta(hours=6 * k)
            rows = []
            for n in names:
                if rnd.random() < 0.08:              # VM missing from this poll
                    continue
                on = rnd.random() < 0.7
                cpu = None if rnd.random() < 0.1 else rnd.choice([1.0, 2.0, 3.0, 50.0])
                rows.append({"vm": n, "isPoweredOn": on, "cpuUsagePctRaw": cpu})
            expected = {r["vm"]: _reference(hist[r["vm"]], r, now) for r in rows}
            scheduler._enrich_activity(db, key, rows, now)
            for r in rows:
                got = r["lastActivityDays"] if r["isPoweredOn"] else r["powerOffDays"]
                assert got == pytest.approx(expected[r["vm"]], abs=1e-6), (k, r)
            run = SnapshotRun(vcenter_key=key, source="live", status="ok", started_at=now, finished_at=now)
            db.add(run)
            db.flush()
            for r in rows:
                db.add(VmSnapshot(run_id=run.id, vcenter_key=key, cluster="c", vm=r["vm"], is_powered_on=r["isPoweredOn"],
                                  cpu_usage_pct_raw=r["cpuUsagePctRaw"], last_activity_days=r.get("lastActivityDays"),
                                  power_off_days=r.get("powerOffDays"), captured_at=now))
                hist[r["vm"]].insert(0, {"at": now, "on": r["isPoweredOn"], "cpu": r["cpuUsagePctRaw"]})
            db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# vCenter: bulk PropertyCollector retrieval
# ---------------------------------------------------------------------------
def _prop(name, val):
    return NS(name=name, val=val)


class FakeCollector:
    """Pages RetrievePropertiesEx results like vCenter does (token until the last page)."""

    def __init__(self, by_type):
        self.by_type, self.calls, self.pending = by_type, 0, []

    def RetrievePropertiesEx(self, specs, options):
        self.calls += 1
        spec = specs[0]
        vim_type = spec.propSet[0].type
        paths = spec.propSet[0].pathSet
        objs = [NS(obj=o, propSet=[_prop(p, props[p]) for p in paths if props.get(p) is not None])
                for o, props in self.by_type.get(vim_type, [])]
        self.pending = [objs[i:i + 2] for i in range(0, len(objs), 2)] or [[]]
        return self._next()

    def ContinueRetrievePropertiesEx(self, token):
        self.calls += 1
        return self._next()

    def _next(self):
        page = self.pending.pop(0)
        return NS(objects=page, token="more" if self.pending else None)


class FakeView(vim.view.ContainerView):
    """A real ContainerView type (PropertyCollector specs are type-checked) that needs no server."""

    def Destroy(self):
        pass


def _inventory():
    cl = vim.ClusterComputeResource("domain-c1")
    standalone = vim.ComputeResource("domain-s9")
    h1, h2, h3 = vim.HostSystem("host-1"), vim.HostSystem("host-2"), vim.HostSystem("host-3")
    ds = vim.Datastore("datastore-5")
    hw = NS(numCpuCores=32, cpuMhz=2500, memorySize=512 * 1024 ** 3, vendor="Dell", model="R760", cpuModel="Xeon",
            numCpuPkgs=2, numCpuThreads=64)
    hsum = NS(hardware=hw, quickStats=NS(overallCpuUsage=40000, overallMemoryUsage=262144),
              runtime=NS(connectionState="connected"), config=NS(product=NS(version="8.0.2", build="22380479")))
    vmsum = NS(config=NS(name="app01", numCpu=4, memorySizeMB=8192, instanceUuid="uuid-1", guestId="windows2019srv_64Guest",
                         guestFullName="Windows Server 2019"),
               quickStats=NS(overallCpuUsage=800, hostMemoryUsage=4096),
               storage=NS(committed=50 * 1024 ** 3, uncommitted=50 * 1024 ** 3),
               runtime=NS(powerState=vim.VirtualMachinePowerState.poweredOn, host=h2),
               guest=NS(ipAddress="10.0.0.5", guestFullName="Microsoft Windows Server 2019", toolsRunningStatus="guestToolsRunning",
                        hostName="app01.corp"))
    vm = vim.VirtualMachine("vm-10")
    return {
        vim.HostSystem: [(h1, {"name": "esx01", "summary": hsum, "parent": cl}),
                         (h2, {"name": "esx02", "summary": hsum, "parent": cl}),
                         (h3, {"name": "esx-solo", "summary": hsum, "parent": standalone})],
        vim.ComputeResource: [(cl, {"name": "PROD", "configurationEx": NS(dasConfig=NS(enabled=True), drsConfig=NS(enabled=False))}),
                              (standalone, {"name": "esx-solo", "configurationEx": NS()})],
        vim.VirtualMachine: [(vm, {"name": "app01", "summary": vmsum, "datastore": [ds], "guest.guestFamily": "windowsGuest"})],
        vim.Datastore: [(ds, {"name": "DS-PROD-01"})],
    }


def _content(collector):
    return NS(propertyCollector=collector, rootFolder=object(),
              viewManager=NS(CreateContainerView=lambda root, types, recursive: FakeView("session[test]view")))


def test_bulk_retrieval_builds_same_rows(monkeypatch):
    collector = FakeCollector(_inventory())
    si = NS(RetrieveContent=lambda: _content(collector))
    monkeypatch.setattr(vc, "_connect", lambda cfg: si)
    monkeypatch.setattr(vc, "Disconnect", lambda si: None)
    hosts, vms = vc.collect_inventory(NS(key="vc-x"))
    # 4 object types, the 3-host type spans 2 pages → 5 calls in total, whatever the inventory size.
    assert collector.calls == 5
    by_name = {h["host"]: h for h in hosts}
    assert by_name["esx01"]["cluster"] == "PROD" and by_name["esx01"]["clusterHa"] is True
    assert by_name["esx01"]["clusterDrs"] is False and by_name["esx01"]["cores"] == 32.0
    assert by_name["esx01"]["ramGB"] == 512.0 and by_name["esx01"]["cpuUsagePctRaw"] == pytest.approx(50.0)
    assert by_name["esx-solo"]["cluster"] == "esx-solo" and by_name["esx-solo"]["clusterHa"] is None
    assert by_name["esx01"]["esxiVersion"] == "8.0.2" and by_name["esx01"]["cpuSockets"] == 2
    v = vms[0]
    assert (v["vm"], v["host"], v["cluster"], v["datastore"]) == ("app01", "esx02", "PROD", "DS-PROD-01")
    assert v["isPoweredOn"] and v["vcpu"] == 4.0 and v["ramGB"] == 8.0
    assert v["diskProvGB"] == pytest.approx(100.0) and v["diskUsedGB"] == pytest.approx(50.0)
    assert v["toolsGuestFamily"] == "windowsGuest" and v["ipAddress"] == "10.0.0.5" and v["instanceUuid"] == "uuid-1"


def test_falls_back_to_per_object_reads(monkeypatch):
    inv = _inventory()

    class Broken(FakeCollector):
        def RetrievePropertiesEx(self, specs, options):
            raise vmodl.fault.InvalidArgument()

    def lazy(content, vim_type, paths):
        return {o._moId: (o, {p: props.get(p) for p in paths}) for o, props in inv.get(vim_type, [])}

    monkeypatch.setattr(vc, "_retrieve_lazy", lazy)
    monkeypatch.setattr(vc, "_connect", lambda cfg: NS(RetrieveContent=lambda: _content(Broken(inv))))
    monkeypatch.setattr(vc, "Disconnect", lambda si: None)
    hosts, vms = vc.collect_inventory(NS(key="vc-x"))
    assert len(hosts) == 3 and vms[0]["host"] == "esx02"


# ---------------------------------------------------------------------------
# Syncing several sources at once; NetBackup job upsert
# ---------------------------------------------------------------------------
def test_sync_everything_runs_sources_in_parallel_and_isolates_failures(monkeypatch):
    from app.config import NetBackupConfig, VCenterConfig
    s = get_settings()
    good = VCenterConfig("vc-par-1", "VC 1", "", 443, "", "", False, demo=True)
    bad = VCenterConfig("vc-par-2", "VC 2", "unreachable.invalid", 443, "u", "p", False, demo=False)
    monkeypatch.setattr(vc, "_connect", lambda cfg: (_ for _ in ()).throw(vc.VCenterConnectionError("down")))
    monkeypatch.setattr(type(s), "vcenters", lambda self: [good, bad])
    nbu = next(iter(s.netbackups()), None)
    monkeypatch.setattr(type(s), "netbackups", lambda self: [nbu] if nbu else [])
    results = scheduler.sync_everything(s)
    by_key = {r.get("vcenterKey") or r.get("key"): r for r in results}
    assert by_key["vc-par-1"]["ok"] and by_key["vc-par-1"]["vmCount"] > 0
    assert by_key["vc-par-2"]["ok"] is False and "down" in by_key["vc-par-2"]["error"]
    if nbu:
        assert by_key[nbu.key]["ok"]
    db = SessionLocal()
    try:
        assert db.query(SnapshotRun).filter_by(vcenter_key="vc-par-2", status="error").count() == 1
        assert db.query(VmSnapshot).filter_by(vcenter_key="vc-par-1").count() == by_key["vc-par-1"]["vmCount"]
    finally:
        db.close()
    assert isinstance(NetBackupConfig, type)


def test_upsert_jobs_only_touches_changed_rows():
    from app.backup.collector import _upsert_jobs
    t = dt.datetime(2026, 3, 1, 21)
    jobs = [{"jobId": i, "clientName": f"c{i}", "status": 0, "state": "DONE", "startTime": t, "endTime": t,
             "jobType": "BACKUP", "category": "backup"} for i in range(1, 6)]
    db = SessionLocal()
    try:
        assert _upsert_jobs(db, "nbu-upsert", jobs) == 5
        db.commit()
        stamp = {r.job_id: r.updated_at for r in db.query(BackupJob).filter_by(nbu_key="nbu-upsert")}
        changed = [dict(j) for j in jobs]
        changed[2]["status"] = 156
        _upsert_jobs(db, "nbu-upsert", changed + [{**jobs[0], "jobId": 99}])
        db.commit()
        rows = {r.job_id: r for r in db.query(BackupJob).filter_by(nbu_key="nbu-upsert")}
        assert len(rows) == 6 and rows[3].status == 156
        assert rows[3].updated_at > stamp[3]
        assert all(rows[i].updated_at == stamp[i] for i in (1, 2, 4, 5))   # unchanged jobs not rewritten
    finally:
        db.close()


# ---------------------------------------------------------------------------
# DAT-01: stored history uses the same effective policy as the live dashboard
# ---------------------------------------------------------------------------
def test_sync_uses_admin_saved_ht_factor_like_dashboard():
    from app import capacity, data_access
    from app.config import VCenterConfig
    from app.models import AppSetting, ClusterAggregate

    settings = get_settings()
    assert settings.ht_factor == 2.0                       # env default
    cfg = VCenterConfig("vc-policy-test", "VC policy", "", 443, "", "", False, demo=True)
    db = SessionLocal()
    try:
        db.add(AppSetting(key="ht_factor", value="1.0"))   # admin override in Cài đặt
        db.commit()
        run = scheduler.sync_vcenter(db, cfg, settings)
        stored = {a.cluster: a for a in db.query(ClusterAggregate).filter_by(run_id=run.id)}
        assert stored

        # What the dashboard computes from the same snapshot.
        hosts, vms, _ = data_access.get_latest_combined(db, [cfg.key], include_import=False)
        live = {c["name"]: c["model"] for c in
                capacity.build_cluster_rows(hosts, vms, data_access.get_effective_policy(db, settings))}
        assert set(live) == set(stored)
        env_policy = capacity.Policy(cpu_overcommit_max=settings.cpu_overcommit_max,
                                     ram_overcommit_max=settings.ram_overcommit_max, ht_factor=2.0, idle_days=settings.idle_days)
        env = {c["name"]: c["model"] for c in capacity.build_cluster_rows(hosts, vms, env_policy)}
        for name, agg in stored.items():
            m = live[name]
            assert agg.total_cores > 0
            assert agg.total_logical_cores == agg.total_cores == m["totalLogicalCores"]   # HT 1.0, not 2.0
            assert agg.cpu_pct == pytest.approx(m["cpuPct"])
            # The env-only policy the scheduler used before doubles the threads, halves the CPU %.
            assert env[name]["totalLogicalCores"] == 2 * agg.total_logical_cores
            assert agg.cpu_pct == pytest.approx(2 * env[name]["cpuPct"])
    finally:
        db.query(AppSetting).filter_by(key="ht_factor").delete()
        db.commit()
        db.close()



def test_enrich_activity_matches_by_uuid_rename_and_duplicate_names():
    """DAT-06: a renamed VM keeps its off-streak (same uuid); another VM with the same name but a
    different uuid does not inherit it."""
    key = "vc-uuid-test"
    t0 = dt.datetime(2026, 2, 1)
    db = SessionLocal()
    try:
        run = SnapshotRun(vcenter_key=key, source="live", status="ok", started_at=t0, finished_at=t0)
        db.add(run)
        db.flush()
        db.add(VmSnapshot(run_id=run.id, vcenter_key=key, cluster="c", vm="app-01", instance_uuid="u-1",
                          is_powered_on=False, power_off_days=3, captured_at=t0))
        db.commit()
        now = t0 + dt.timedelta(days=2)
        rows = [{"vm": "app-01-renamed", "instanceUuid": "u-1", "isPoweredOn": False},   # same VM, new name
                {"vm": "app-01", "instanceUuid": "u-2", "isPoweredOn": False}]           # new VM reusing the name
        scheduler._enrich_activity(db, key, rows, now)
        assert rows[0]["powerOffDays"] == pytest.approx(5.0)      # 3 days before t0 + 2 days since
        assert rows[1]["powerOffDays"] == 0
    finally:
        db.close()
