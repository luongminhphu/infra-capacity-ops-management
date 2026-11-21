"""ICO v2 G9a: configuration baseline — every rule compliant / deviating / not applicable, exceptions (valid, expired, cluster-wide),
build drift in a cluster, Proxmox (no facts) never shows VMware deviations, demo facts, store keeps facts on a read error."""
import datetime as dt
from types import SimpleNamespace as NS

import pytest

from app.baseline import rules as R, service
from app.baseline.models import CfgFactState
from test_cmdb import db  # noqa: F401  (fixture: private SQLite)

TODAY = dt.date(2026, 11, 21)
GOOD_HOST = {"sshRunning": False, "sshPolicy": "off", "shellRunning": False, "shellPolicy": "off", "lockdown": "lockdownNormal",
             "ntpServers": ["ntp1"], "ntpRunning": True, "syslogHost": "udp://syslog:514", "build": "24022510"}


def _f(scope, entity, facts, cluster="PRD", vc="vc1"):
    return {"vcenterKey": vc, "scope": scope, "entity": entity, "cluster": cluster, "facts": facts}


def _rules(**over):
    return R.effective(over)


def _codes(res):
    return sorted((f["rule"], f["entity"]) for f in res["findings"])


@pytest.mark.parametrize("rule,bad,detail", [
    ("esxi_ssh", {"sshRunning": True, "sshPolicy": "on"}, "đang chạy, khởi động: on"),
    ("esxi_ssh", {"sshRunning": False, "sshPolicy": "on"}, "đang tắt, khởi động: on"),      # starts with the host
    ("esxi_shell", {"shellRunning": True}, "đang chạy, khởi động: off"),
    ("lockdown", {"lockdown": "lockdownDisabled"}, "tắt"),
    ("ntp", {"ntpServers": []}, "0 server, ntpd chạy"),
    ("ntp", {"ntpRunning": False}, "1 server, ntpd không chạy"),
    ("syslog", {"syslogHost": ""}, "chưa đặt Syslog.global.logHost"),
])
def test_host_rules(rule, bad, detail):
    res = R.evaluate([_f("host", "esx01", GOOD_HOST), _f("host", "esx02", {**GOOD_HOST, **bad})], _rules(), [], TODAY)
    hits = [f for f in res["findings"] if f["rule"] == rule]
    assert [(f["entity"], f["detail"]) for f in hits] == [("esx02", detail)]
    assert hits[0]["code"].startswith("CFG-") and len(hits[0]["code"]) == 9
    assert res["checked"][rule] == 2


def test_unreadable_fact_is_not_a_deviation():
    res = R.evaluate([_f("host", "esx01", {"build": "1"}), _f("vm", "vm1", {"poweredOn": True})], _rules(), [], TODAY)
    assert res["findings"] == [] and res["checked"]["syslog"] == 0 and res["checked"]["vm_iso"] == 0


def test_cluster_rules_and_build_drift():
    facts = [_f("cluster", "PRD", {"ha": True, "admissionControl": False, "drs": True, "drsBehavior": "partiallyAutomated"}),
             _f("cluster", "DEV", {"ha": True, "admissionControl": True, "drs": True, "drsBehavior": "fullyAutomated"}, cluster="DEV"),
             _f("host", "esx01", {**GOOD_HOST, "build": "24022510"}), _f("host", "esx02", {**GOOD_HOST, "build": "24022510"}),
             _f("host", "esx03", {**GOOD_HOST, "build": "22380479"}),
             _f("host", "dev01", {**GOOD_HOST, "build": "22380479"}, cluster="DEV")]
    res = R.evaluate(facts, _rules(), [], TODAY)
    assert _codes(res) == [("build_drift", "PRD"), ("cluster_drs", "PRD"), ("cluster_ha", "PRD")]
    drift = next(f for f in res["findings"] if f["rule"] == "build_drift")
    assert drift["detail"] == "build 22380479: 1 host; build 24022510: 2 host"
    assert [f["rule"] for f in res["findings"]][0] == "cluster_ha"           # CẢNH BÁO before THEO DÕI


def test_vm_rules_param_and_powered_off():
    facts = [_f("vm", "a", {"poweredOn": True, "toolsStatus": "guestToolsNeedUpgrade", "hwVersion": 13, "isos": ["[DS] x.iso"]}),
             _f("vm", "b", {"poweredOn": True, "toolsStatus": "guestToolsUnmanaged", "hwVersion": 19, "isos": []}),
             _f("vm", "c", {"poweredOn": False, "toolsStatus": "guestToolsNotInstalled", "hwVersion": 19, "isos": ["[DS] y.iso"]})]
    assert _codes(R.evaluate(facts, _rules(), [], TODAY)) == [("vm_hw", "a"), ("vm_iso", "a"), ("vm_tools", "a")]
    res = R.evaluate(facts, _rules(vm_hw={"param": "20", "level": "bad"}, vm_iso={"enabled": False}), [], TODAY)
    assert _codes(res) == [("vm_hw", "a"), ("vm_hw", "b"), ("vm_hw", "c"), ("vm_tools", "a")]
    assert next(f for f in res["findings"] if f["entity"] == "b")["detail"] == "vmx-19 (tối thiểu vmx-20)"


def test_exceptions_entity_cluster_wide_and_expired():
    facts = [_f("host", "esx01", {**GOOD_HOST, "sshRunning": True}), _f("host", "esx02", {**GOOD_HOST, "sshRunning": True}),
             _f("host", "esx03", {**GOOD_HOST, "sshRunning": True}, cluster="DEV")]
    exc = [{"id": 1, "rule": "esxi_ssh", "scope": "host", "vcenterKey": "vc1", "entity": "esx01", "reason": "Vendor hỗ trợ", "until": TODAY + dt.timedelta(days=5)},
           {"id": 2, "rule": "esxi_ssh", "scope": "cluster", "vcenterKey": "", "entity": "DEV", "reason": "Lab", "until": TODAY + dt.timedelta(days=5)},
           {"id": 3, "rule": "esxi_ssh", "scope": "host", "vcenterKey": "vc1", "entity": "esx02", "reason": "Hết hạn", "until": TODAY - dt.timedelta(days=1)}]
    res = R.evaluate(facts, _rules(), exc, TODAY)
    assert _codes(res) == [("esxi_ssh", "esx02")]
    assert sorted((f["entity"], f["exception"]["id"]) for f in res["excepted"]) == [("esx01", 1), ("esx03", 2)]


def test_store_replace_keep_on_error_and_proxmox_clears(db, monkeypatch):  # noqa: F811
    from app.vcenter import demo_generator
    hosts = [{"host": f"esx{i}", "cluster": "PRD" if i < 3 else "DEV", "esxiBuild": "24022510"} for i in range(5)]
    vms = [{"vm": f"vm{i}", "cluster": "PRD", "isPoweredOn": True} for i in range(14)]
    n = service.store(db, "vc1", {"config": demo_generator.collect_config(hosts, vms)})
    db.commit()
    assert n == 5 + 2 + 14
    monkeypatch.setattr("app.vm_ip.attach", lambda _db, rows, **_k: rows)
    rep = service.report(db, TODAY)
    got = {(f["rule"], f["entity"]) for f in rep["findings"]}
    assert {("esxi_ssh", "esx0"), ("esxi_shell", "esx1"), ("lockdown", "esx0"), ("lockdown", "esx2"), ("ntp", "esx3"), ("syslog", "esx4"),
            ("cluster_ha", "PRD"), ("cluster_drs", "DEV"), ("build_drift", "PRD"), ("vm_tools", "vm3"), ("vm_tools", "vm10"),
            ("vm_hw", "vm5"), ("vm_iso", "vm6")} == got
    assert rep["summary"]["findings"] == len(got) and rep["summary"]["hosts"] == 5
    assert service.store(db, "vc1", {"config": None}) == 0                   # read error: previous facts kept
    assert db.query(CfgFactState).count() == 21
    service.store(db, "vc1", {"datastores": []})                              # Proxmox / Nutanix: no baseline
    db.commit()
    assert db.query(CfgFactState).count() == 0 and service.report(db, TODAY)["findings"] == []
    # cards read one object only
    service.store(db, "vc1", {"config": demo_generator.collect_config(hosts, vms)})
    db.commit()
    assert {f["rule"] for f in service.for_entity(db, "host", "esx0", "vc1")} == {"esxi_ssh", "lockdown", "build_drift", "cluster_ha"}
    assert [f["rule"] for f in service.for_entity(db, "vm", "vm6", "vc1")] == ["vm_iso"]


def test_settings_validation(db):  # noqa: F811
    service.save_rule(db, "vm_hw", {"enabled": True, "level": "bad", "param": "17"}, "t")
    db.commit()
    r = next(x for x in service.rules(db) if x["rule"] == "vm_hw")
    assert (r["level"], r["param"]) == ("bad", "17")
    for rule, data in (("nope", {}), ("vm_hw", {"level": "x"}), ("vm_hw", {"param": "abc"})):
        with pytest.raises(ValueError):
            service.save_rule(db, rule, data, "t")
    ok = {"rule": "esxi_ssh", "scope": "host", "entity": "esx01", "reason": "x", "until": "2026-12-01"}
    assert service.add_exception(db, ok, "t", TODAY).until == dt.date(2026, 12, 1)
    for bad in ({**ok, "scope": "vm"}, {**ok, "until": "2026-11-21"}, {**ok, "until": "2028-01-01"}, {**ok, "reason": ""}):
        with pytest.raises(ValueError):
            service.add_exception(db, bad, "t", TODAY)


def test_config_facts_reader_uses_reads_only():
    """The real reader: services / lockdown / NTP / syslog / HA / DRS / tools / hw / ISO from pyVmomi-like objects."""
    from pyVmomi import vim
    from app.vcenter import config_facts

    class Opt:
        def QueryOptions(self, name):
            assert name == "Syslog.global.logHost"
            return [NS(value="udp://log:514")]
    host = object()
    props = {"config.service.service": [NS(key="TSM-SSH", running=True, policy="on"), NS(key="ntpd", running=True, policy="on")],
             "config.lockdownMode": "lockdownDisabled", "config.dateTimeInfo.ntpConfig.server": ["ntp1"],
             "configManager.advancedOption": Opt()}
    import app.vcenter.config_facts as cf
    orig = cf.retrieve_refs
    cf.retrieve_refs = lambda _c, refs, vtype, paths: {"host-1": props} if vtype is vim.HostSystem else {}
    try:
        rows = config_facts._host_facts(None, {"host-1": (host, {"summary": NS(config=NS(product=NS(build="24022510", version="8.0.3")))})},
                                        {"host-1": ("esx01", "PRD")})
    finally:
        cf.retrieve_refs = orig
    f = rows[0]["facts"]
    assert (rows[0]["host"], f["sshRunning"], f["sshPolicy"], f["ntpRunning"], f["lockdown"], f["syslogHost"], f["build"]) == \
        ("esx01", True, "on", True, "lockdownDisabled", "udp://log:514", "24022510")
    assert "shellRunning" not in f
