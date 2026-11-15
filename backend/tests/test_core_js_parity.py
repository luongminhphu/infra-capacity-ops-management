"""Parity between backend/app/capacity.py and frontend/js/core.js (CLAUDE.md rule 3).

core.js is a browser script, so it is evaluated in Node's `vm` module with a
bare global (only TextEncoder). Its top level only declares constants and
functions, so no DOM is needed for the pure capacity helpers. All JS calls are
batched into a single `node` process per module to keep the suite fast.
"""
import json
import math
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import capacity  # noqa: E402
from app.capacity import Policy  # noqa: E402

CORE_JS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "js", "core.js"))

# Reads core.js, evaluates it in a sandbox and answers a JSON list of calls on stdin.
# Non-finite numbers do not survive JSON, so they travel as {"$num": "NaN" | "Infinity" | "-Infinity"}.
RUNNER = r"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(process.argv[process.argv.length - 1], 'utf8');
const ctx = vm.createContext({ TextEncoder, console });
vm.runInContext(src + `
;globalThis.__ico = { appState, getStatusForPct, combineStatus, buildCapacityModel,
  buildHostRows, buildClusterRows, buildDatastoreRows, findingCode };`, ctx, { filename: 'core.js' });
const api = ctx.__ico;
const dec = (v) => {
  if (Array.isArray(v)) return v.map(dec);
  if (v && typeof v === 'object') {
    if ('$num' in v) return Number(v.$num);
    const o = {}; for (const k of Object.keys(v)) o[k] = dec(v[k]); return o;
  }
  return v;
};
const enc = (v) => {
  if (typeof v === 'number' && !isFinite(v)) return { $num: String(v) };
  if (Array.isArray(v)) return v.map(enc);
  if (v && typeof v === 'object') { const o = {}; for (const k of Object.keys(v)) o[k] = enc(v[k]); return o; }
  return v;
};
const calls = dec(JSON.parse(fs.readFileSync(0, 'utf8')));
const out = calls.map(({ fn, args, policy, hosts, vms }) => {
  if (policy) Object.assign(api.appState.policy, policy);
  if (hosts) api.appState.hosts = hosts;
  if (vms) api.appState.vms = vms;
  return api[fn](...(args || []));
});
process.stdout.write(JSON.stringify(enc(out)));
"""


def _enc(v):
    if isinstance(v, float) and not math.isfinite(v):
        return {"$num": "NaN" if math.isnan(v) else ("Infinity" if v > 0 else "-Infinity")}
    if isinstance(v, (list, tuple)):
        return [_enc(x) for x in v]
    if isinstance(v, dict):
        return {k: _enc(x) for k, x in v.items()}
    return v


def _dec(v):
    if isinstance(v, list):
        return [_dec(x) for x in v]
    if isinstance(v, dict):
        if set(v) == {"$num"}:
            return float(v["$num"].replace("Infinity", "inf"))
        return {k: _dec(x) for k, x in v.items()}
    return v


def _run_js(calls):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH")
    proc = subprocess.run([node, "-e", RUNNER, "--", CORE_JS], input=json.dumps(_enc(calls)).encode("utf-8"),
                          capture_output=True, timeout=30)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return _dec(json.loads(proc.stdout.decode("utf-8")))


def _num_equal(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if math.isnan(a) or math.isnan(b):
            return math.isnan(a) and math.isnan(b)
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    return a == b


# --- fixtures --------------------------------------------------------------

def _host(name, cores, ram, cluster):
    return {"host": name, "cluster": cluster, "cores": cores, "ramGB": ram, "vcenterKey": "vc1"}


def _vm(name, host, cluster, vcpu, ram, on=True, prov=0.0, used=0.0, ds="DS1"):
    return {"vm": name, "host": host, "cluster": cluster, "vcpu": vcpu, "ramGB": ram, "isPoweredOn": on,
            "diskProvGB": prov, "diskUsedGB": used, "datastore": ds, "vcenterKey": "vc1"}


FIXTURES = {
    # Skill worked example: 50 % CPU, 87.9 % RAM.
    "worked_example": (
        [_host("esx01", 32, 512, "CL1"), _host("esx02", 32, 512, "CL1")],
        [_vm("a", "esx01", "CL1", 192, 450, prov=500, used=300), _vm("b", "esx02", "CL1", 192, 450, prov=500, used=420)],
        {"cpuOvercommitMax": 6, "ramOvercommitMax": 1.0, "htFactor": 2},
    ),
    # Powered-off VMs, uneven hosts, custom policy, two clusters.
    "mixed_power_two_clusters": (
        [_host("h1", 24, 384, "PROD"), _host("h2", 40, 768, "PROD"), _host("h3", 16, 256, "DR")],
        [_vm("v1", "h1", "PROD", 96, 300), _vm("v2", "h2", "PROD", 200, 700, on=False, prov=900, used=850),
         _vm("v3", "h2", "PROD", 130, 512.5, prov=100, used=10), _vm("v4", "h3", "DR", 70, 260)],
        {"cpuOvercommitMax": 4, "ramOvercommitMax": 1.2, "htFactor": 1},
    ),
    # Over threshold (crit) and exactly at the 100 % band.
    "over_and_exact": (
        [_host("x1", 10, 100, "HOT"), _host("x2", 10, 100, "HOT")],
        [_vm("big", "x1", "HOT", 121, 100), _vm("small", "x2", "HOT", 1, 99)],
        {"cpuOvercommitMax": 6, "ramOvercommitMax": 1.0, "htFactor": 2},
    ),
    # Zero capacity: everything NaN / NA, never Infinity.
    "zero_capacity": (
        [_host("z1", 0, 0, "EMPTY")],
        [_vm("orphan", "z1", "EMPTY", 4, 8)],
        {"cpuOvercommitMax": 6, "ramOvercommitMax": 1.0, "htFactor": 2},
    ),
}

STATUS_PCTS = [-1.0, 0.0, 49.99, 50.0, 79.999, 80.0, 80.0001, 89.99, 90.0, 99.99, 100.0, 100.0000001, 100.01, 150.0,
               float("nan"), float("inf"), float("-inf")]
STATUS_BANDS = [(80, 100), (50, 80), (80, 90)]

COMBOS = [["ok", "warn"], ["warn", "bad"], ["bad", "crit", "ok"], ["na", "ok"], ["na", "bad"], ["na", "na"], ["ok"]]
LABELS = {"crit": "VƯỢT NGƯỠNG", "bad": "CẢNH BÁO", "warn": "THEO DÕI", "ok": "AN TOÀN", "na": "NA"}

FINDING_TUPLES = [
    ("host_cpu", "CL-PROD", "esx01.lab.local", "vc1"),
    ("host_ram", "CL-PROD", "esx01.lab.local", ""),
    ("overcommit_cpu", "Cụm Hà Nội", "Cụm Hà Nội", ""),
    ("overcommit_ram", "CL|pipe", "x", None),
    ("vm_idle", "Cụm Đà Nẵng", "Máy chủ kế toán – bản sao", "vc-hn"),
    ("vm_off", None, None, None),
    ("vm_overprovision", "c", "VM 😀 emoji", "vc2"),
    ("brand_new_type", "c", "e", "v"),
]

MODEL_KEYS = ["hostCount", "vmCount", "poweredOnCount", "poweredOffCount", "totalCores", "totalLogicalCores",
              "htFactor", "totalRamGB", "provStorageGB", "usedStorageGB", "usedVcpu", "usedRamGB", "freeCores",
              "freeLogicalCores", "freeRamGB", "freeStorageGB", "cpuPct", "ramPct", "storagePct", "cpuOvercommit",
              "ramOvercommit"]


def _py_policy(p):
    return Policy(cpu_overcommit_max=p["cpuOvercommitMax"], ram_overcommit_max=p["ramOvercommitMax"],
                  ht_factor=p["htFactor"])


def _build_calls():
    calls, index = [], {}

    def add(key, call):
        index[key] = len(calls)
        calls.append(call)

    for band in STATUS_BANDS:
        for pct in STATUS_PCTS:
            add(("status", band, repr(pct)), {"fn": "getStatusForPct", "args": [pct, *band]})
    for combo in COMBOS:
        add(("combine", tuple(combo)), {"fn": "combineStatus", "args": [{"text": LABELS[c], "cls": c} for c in combo]})
    for name, (hosts, vms, pol) in FIXTURES.items():
        add(("model", name), {"fn": "buildCapacityModel", "args": [hosts, vms], "policy": pol})
        add(("hosts", name), {"fn": "buildHostRows", "policy": pol, "hosts": hosts, "vms": vms})
        add(("clusters", name), {"fn": "buildClusterRows", "policy": pol, "hosts": hosts, "vms": vms})
    for t in FINDING_TUPLES:
        add(("finding", t), {"fn": "findingCode", "args": list(t)})
    ds_vms = [_vm("d1", "h", "C", 1, 1, prov=100, used=75, ds="DS-75"),
              _vm("d2", "h", "C", 1, 1, prov=100, used=87, ds="DS-87"),
              _vm("d3", "h", "C", 1, 1, prov=100, used=95, ds="DS-95")]
    add(("datastores",), {"fn": "buildDatastoreRows", "hosts": [], "vms": ds_vms})
    return calls, index, ds_vms


@pytest.fixture(scope="module")
def js():
    calls, index, ds_vms = _build_calls()
    results = _run_js(calls)
    return {"get": lambda key: results[index[key]], "ds_vms": ds_vms}


# --- tests -----------------------------------------------------------------

@pytest.mark.parametrize("band", STATUS_BANDS)
def test_status_for_pct_grid_matches(js, band):
    for pct in STATUS_PCTS:
        assert js["get"](("status", band, repr(pct))) == capacity.get_status_for_pct(pct, *band), (pct, band)


def test_combine_status_matches(js):
    for combo in COMBOS:
        py = capacity.combine_status(*[{"text": LABELS[c], "cls": c} for c in combo])
        assert js["get"](("combine", tuple(combo))) == py, combo


@pytest.mark.parametrize("name", list(FIXTURES))
def test_capacity_model_matches(js, name):
    hosts, vms, pol = FIXTURES[name]
    py = capacity.build_capacity_model(hosts, vms, _py_policy(pol))
    got = js["get"](("model", name))
    for key in MODEL_KEYS:
        assert _num_equal(got[key], py[key]), (name, key, got[key], py[key])
        if isinstance(py[key], float):
            assert not math.isinf(py[key]) and not math.isinf(got[key]), (name, key)


@pytest.mark.parametrize("name", list(FIXTURES))
def test_host_rows_match(js, name):
    hosts, vms, pol = FIXTURES[name]
    py_rows = capacity.build_host_rows(hosts, vms, _py_policy(pol))
    js_rows = js["get"](("hosts", name))
    assert len(js_rows) == len(py_rows)
    for jr, pr in zip(js_rows, py_rows):
        for key in ("vcpuUsed", "ramUsedGB", "cpuOvercommit", "ramOvercommit", "cpuPct", "ramPct", "vmCount"):
            assert _num_equal(jr[key], pr[key]), (name, pr["host"], key, jr[key], pr[key])
        for key in ("cpuStatus", "ramStatus", "overall"):
            assert jr[key] == pr[key], (name, pr["host"], key)


@pytest.mark.parametrize("name", list(FIXTURES))
def test_cluster_rows_match(js, name):
    hosts, vms, pol = FIXTURES[name]
    py_rows = capacity.build_cluster_rows(hosts, vms, _py_policy(pol))
    js_rows = js["get"](("clusters", name))
    assert [r["name"] for r in js_rows] == [r["name"] for r in py_rows]
    for jr, pr in zip(js_rows, py_rows):
        assert (jr["hosts"], jr["vms"]) == (pr["hosts"], pr["vms"])
        for key in ("cpuStatus", "ramStatus", "overall"):
            assert jr[key] == pr[key], (name, pr["name"], key)
        for key in ("cpuPct", "ramPct", "cpuOvercommit", "ramOvercommit"):
            assert _num_equal(jr["model"][key], pr["model"][key]), (name, pr["name"], key)


def test_fixture_statuses_cover_every_level(js):
    # Guard against fixtures drifting into all-OK, which would make the parity tests weak.
    seen = set()
    for name in FIXTURES:
        for r in js["get"](("hosts", name)):
            seen.update({r["cpuStatus"]["cls"], r["ramStatus"]["cls"]})
    assert {"ok", "warn", "bad", "crit", "na"} <= seen


@pytest.mark.parametrize("t", FINDING_TUPLES)
def test_finding_code_matches(js, t):
    assert js["get"](("finding", t)) == capacity.finding_code(*t)


# Was a strict xfail: core.js used dsWarn 70 / dsHigh 85; aligned to capacity.py 80 / 90 (approved 16/11/2026).
def test_datastore_status_matches(js):
    py = {r["name"]: r["status"] for r in capacity.build_datastore_rows(js["ds_vms"])}
    got = {r["name"]: r["status"] for r in js["get"](("datastores",))}
    assert got == py
