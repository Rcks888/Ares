#!/usr/bin/env python3
"""Monitor entry point with both recorders (admission capture v1, M2).

    python3 tests/run_monitor_admission_entrypoint_controls.py

Design §7, *Monitor entrypoint re-registration*, *Cross-recorder pairing* and
the isolation items *Recorder unimportable or unwritable*. The REAL
monitor_trades.py is executed from a byte-identical copy in a scratch
directory, via runpy with __name__ == "__main__". Engine dependencies are
stubbed in sys.modules. The monitor recorder is the REAL
engine/monitor_observer.py (loaded from its file, writing to the scratch
logs), except where a control replaces it. The admission recorder is a stub
that notes each call, what the monitor recorder had written at that moment and
the peer it was handed, and can fail on demand; control 90 uses the real one.

Anti-vacuity: the controls are rerun on two mutants of the deployed file,
built from its own bytes:
  * guard removed  -- the admission block's `try/except BaseException: pass`
    replaced by its body. Controls 82 and 83 must FAIL.
  * order swapped  -- the admission block moved before the monitor flush.
    Controls 80, 81, 87 and 89 must FAIL.
Every other control must still pass on each mutant.

Not here: the cross-check verdicts that need two heartbeats in one commit
(exactly one / none / more than one monitor heartbeat with an equal
`cycle_started_at`). They are inspection work (M4); control 88 records the
input they will see when the monitor recorder is unavailable.
"""
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEPLOYED = os.path.join(ROOT, "monitor_trades.py")
MARKER = "MonitorMarkerError"


class MonitorMarkerError(RuntimeError):
    """Unique production failure, so the propagated exception is identifiable."""


# --------------------------------------------------------------------------
# Driver (subprocess)
# --------------------------------------------------------------------------

def _lines(path):
    try:
        return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    except Exception:                                     # noqa: BLE001
        return []


def _load_from_file(name, rel):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def _install(mode, work):
    logs = os.path.join(work, "logs")

    def note(name, payload):
        with open(os.path.join(work, name), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")

    def mod(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m
        return m

    engine = mod("engine")
    engine.__path__ = []
    trade = {"symbol": "T1", "status": "open", "entry_date": "2026-10-01",
             "entry_price": 10.0, "stop_loss": 9.0, "take_profit": 12.0,
             "trailing_stop": 9.0, "peak_price": 10.0, "shares": 5.0}

    def load_trades():
        if mode == "monitor_raises" or mode.startswith("monitor_raises_"):
            raise MonitorMarkerError("load_trades failed")
        return [trade]

    store_attrs = {}
    if mode != "tracker_without_store":
        store_attrs = {
            "_ADMISSION_EVAL": {},
            "_new_admission_store": lambda token: {
                "invocation_token": token, "promotion_expected": False,
                "monitor_completed": False, "loop_exception": None,
                "flushed": False}}
    mod("engine.tracker", load_trades=load_trades,
        save_trades=lambda t: None,
        _load_params=lambda: {"trailing_stop_pct": 0.10,
                              "rsi_extreme_high": 90},
        _close_trade=lambda *a: None, _holding_days=lambda d: 3,
        load_stock=lambda s: None,
        promote_queue=lambda **kw: note("promote.txt", kw), **store_attrs)
    # 10.5 advances the peak, so the monitor saves and calls promote_queue
    # (a stub that captures nothing). 9.8 holds with no state update, which
    # is what control 90 needs for a coverage state the real observer can
    # judge without a real promotion pass.
    live = 9.8 if mode == "admobs_real" else 10.5
    mod("engine.data_feed", get_live_price=lambda s: live,
        disconnect_ib=lambda: None)
    mod("engine.indicators", add_indicators=lambda df: df)
    for n in ("tracker", "data_feed", "indicators"):
        setattr(engine, n, sys.modules[f"engine.{n}"])

    if mode == "monobs_unimportable":
        sys.modules["engine.monitor_observer"] = None
    elif mode == "monobs_flush_keyboardinterrupt":
        def mflush(store):
            raise KeyboardInterrupt("raised inside the monitor flush")
        engine.monitor_observer = mod("engine.monitor_observer", flush=mflush)
    else:
        mo = _load_from_file("engine.monitor_observer",
                             "engine/monitor_observer.py")
        if mode == "monobs_unwritable":
            blocker = os.path.join(work, "blocked")
            open(blocker, "w").write("not a directory\n")
            mo.LOGS_DIR = Path(blocker) / "logs"
        else:
            mo.LOGS_DIR = Path(logs)
        engine.monitor_observer = mo

    if mode == "admobs_unimportable":
        sys.modules["engine.admission_observer"] = None
        return
    if mode == "admobs_real":
        ao = _load_from_file("engine.admission_observer",
                             "engine/admission_observer.py")
        ao.LOGS_DIR = Path(logs)
        engine.admission_observer = ao
        return

    def flush(store, decision_path, peer=None):
        note("adm_flush_calls.txt", {
            "decision_path": decision_path,
            "monitor_heartbeats": len(_lines(os.path.join(
                logs, "monitor_heartbeat_v1.jsonl"))),
            "monitor_health": len(_lines(os.path.join(
                logs, "monitor_health_v1.jsonl"))),
            "peer_token": (peer or {}).get("invocation_token"),
            "peer_started": (peer or {}).get("cycle_started_at"),
            "peer_snapshot": json.dumps(peer, sort_keys=True, default=str),
            "store_token": store.get("invocation_token"),
            "loop_exception": store.get("loop_exception"),
            "monitor_completed": store.get("monitor_completed")})
        if mode.endswith("admflush_keyboardinterrupt"):
            raise KeyboardInterrupt("raised inside the admission flush")
        if mode.endswith("admflush_systemexit"):
            raise SystemExit(7)
        if mode.endswith("admflush_write_failed"):
            return {"attempted": 1, "written": 0, "write_failures": 1,
                    "heartbeat_written": False, "flush_error": None,
                    "skipped_already_flushed": False}
        return {"attempted": 0, "written": 0, "write_failures": 0,
                "heartbeat_written": True, "flush_error": None,
                "skipped_already_flushed": False}

    engine.admission_observer = mod("engine.admission_observer", flush=flush)


def drive(mode, work, copy):
    _install(mode, work)
    import runpy
    runpy.run_path(copy, run_name="__main__")


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------

def run(mode, source):
    work = tempfile.mkdtemp(prefix="mon_adm_entry_")
    try:
        os.makedirs(os.path.join(work, "logs"))
        copy = os.path.join(work, "monitor_trades.py")
        shutil.copyfile(source, copy)
        with open(source, "rb") as a, open(copy, "rb") as b:
            assert hashlib.sha256(a.read()).digest() == \
                hashlib.sha256(b.read()).digest()
        p = subprocess.run([sys.executable, os.path.abspath(__file__),
                            "--drive", mode, work, copy],
                           capture_output=True, text=True, cwd=work,
                           timeout=120)
        tail = [l for l in p.stderr.strip().splitlines() if l.strip()]
        L = os.path.join(work, "logs")
        return {"rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr,
                "last_err": tail[-1] if tail else "",
                "adm_calls": _lines(os.path.join(work, "adm_flush_calls.txt")),
                "mon_beats": _lines(os.path.join(L, "monitor_heartbeat_v1.jsonl")),
                "mon_health": _lines(os.path.join(L, "monitor_health_v1.jsonl")),
                "adm_health": _lines(os.path.join(L, "admission_health_v1.jsonl")),
                "adm_beats": _lines(os.path.join(L, "admission_heartbeat_v1.jsonl")),
                "adm_recs": _lines(os.path.join(L, "admission_v1.jsonl"))}
    finally:
        shutil.rmtree(work, ignore_errors=True)


CONTROLS = []


def control(fn):
    CONTROLS.append(fn)
    return fn


def _ck(cond, label, f):
    if not cond:
        f.append(label)


def _one_call_after_monitor(r, f, beats=1):
    _ck(len(r["adm_calls"]) == 1, f"admission flush calls {len(r['adm_calls'])}", f)
    if r["adm_calls"]:
        c = r["adm_calls"][0]
        _ck(c["decision_path"] == "monitor", f"decision_path {c['decision_path']}", f)
        _ck(c["monitor_heartbeats"] == beats,
            f"monitor heartbeats written before the admission flush: "
            f"{c['monitor_heartbeats']} != {beats}", f)


@control
def control_80_normal_run_pairs_and_orders(src):
    """Exit 0; the admission flush runs once, after the monitor heartbeat;
    its peer is the monitor store whose cycle_started_at the heartbeat
    carries; no health line on either path."""
    r, f = run("ok", src), []
    _ck(r["rc"] == 0, f"exit {r['rc']}: {r['last_err']!r}", f)
    _ck(len(r["mon_beats"]) == 1, f"monitor heartbeats {len(r['mon_beats'])}", f)
    _one_call_after_monitor(r, f)
    if r["adm_calls"] and r["mon_beats"]:
        c, b = r["adm_calls"][0], r["mon_beats"][0]
        _ck(c["peer_started"] == b.get("cycle_started_at")
            and c["peer_started"], "peer cycle_started_at != monitor heartbeat", f)
        _ck(c["peer_token"] == c["store_token"] == 1, "tokens", f)
        _ck(c["monitor_completed"] is True, "monitor_completed not set", f)
    _ck(not r["mon_health"] and not r["adm_health"], "unexpected health lines", f)
    return f


@control
def control_81_raising_monitor_flushes_both_and_propagates(src):
    r, f = run("monitor_raises", src), []
    _ck(r["rc"] == 1 and MARKER in r["last_err"], f"exit/last {r['rc']} {r['last_err']!r}", f)
    _ck(len(r["mon_beats"]) == 1 and MARKER in str(
        r["mon_beats"][0].get("loop_exception")), "monitor heartbeat", f)
    _one_call_after_monitor(r, f)
    if r["adm_calls"]:
        _ck(MARKER in str(r["adm_calls"][0]["loop_exception"]),
            "admission store lacks loop_exception", f)
    return f


@control
def control_82_keyboardinterrupt_in_admission_flush_keeps_in_flight_exception(src):
    r, f = run("monitor_raises_admflush_keyboardinterrupt", src), []
    _ck(r["rc"] == 1 and MARKER in r["last_err"],
        f"in-flight exception replaced: exit {r['rc']} {r['last_err']!r}", f)
    _ck(len(r["mon_beats"]) == 1, "monitor heartbeat missing", f)
    return f


@control
def control_83_systemexit_in_admission_flush_cannot_end_a_completed_monitor(src):
    r, f = run("admflush_systemexit", src), []
    _ck(r["rc"] == 0, f"exit {r['rc']} != 0", f)
    _ck(len(r["mon_beats"]) == 1, "monitor heartbeat missing", f)
    return f


@control
def control_84_admission_write_failure_reports_health_only(src):
    r, f = run("admflush_write_failed", src), []
    _ck(r["rc"] == 0, f"exit {r['rc']}", f)
    _ck([h.get("reason") for h in r["adm_health"]] == ["recorder_write_failed"],
        f"admission health {r['adm_health']}", f)
    _ck(not r["mon_health"] and len(r["mon_beats"]) == 1,
        "monitor evidence affected", f)
    _ck("ADMISSION-HEALTH" in r["stderr"] and "ADMISSION-HEALTH" not in r["stdout"],
        "health not on stderr only", f)
    return f


@control
def control_85_admission_observer_unimportable(src):
    r, f = run("admobs_unimportable", src), []
    _ck(r["rc"] == 0, f"exit {r['rc']}", f)
    _ck([h.get("reason") for h in r["adm_health"]] == ["recorder_import_failed"],
        f"admission health {r['adm_health']}", f)
    _ck(len(r["mon_beats"]) == 1 and not r["mon_health"], "monitor evidence", f)
    return f


@control
def control_86_tracker_without_store(src):
    """A tracker deployed without the admission store: trading continues and
    the import failure is reported."""
    r, f = run("tracker_without_store", src), []
    _ck(r["rc"] == 0, f"exit {r['rc']}", f)
    _ck([h.get("reason") for h in r["adm_health"]] == ["recorder_import_failed"]
        and "_ADMISSION_EVAL" in str(r["adm_health"][0].get("detail")),
        f"admission health {r['adm_health']}", f)
    _ck(not r["adm_calls"], "flush called without a store", f)
    _ck(len(r["mon_beats"]) == 1, "monitor heartbeat", f)
    return f


@control
def control_87_monitor_recorder_unwritable_admission_still_flushes(src):
    """Monitor flush failing (cannot write): its health line comes first, then
    the admission flush still runs."""
    r, f = run("monobs_unwritable", src), []
    _ck(r["rc"] == 0, f"exit {r['rc']}", f)
    _ck([h.get("reason") for h in r["mon_health"]] == ["recorder_write_failed"],
        f"monitor health {r['mon_health']}", f)
    _ck(len(r["adm_calls"]) == 1 and r["adm_calls"][0]["monitor_health"] == 1,
        "admission flush missing, or ran before the monitor health line", f)
    return f


@control
def control_88_monitor_recorder_unimportable_admission_still_flushes(src):
    """No monitor heartbeat; the admission flush still runs and still carries
    the peer identity from the plain-dict monitor store. Inspection (M4) then
    finds no matching heartbeat: peer_unavailable, INCONCLUSIVE (§3.4)."""
    r, f = run("monobs_unimportable", src), []
    _ck(r["rc"] == 0, f"exit {r['rc']}", f)
    _ck([h.get("reason") for h in r["mon_health"]] == ["recorder_import_failed"],
        f"monitor health {r['mon_health']}", f)
    _one_call_after_monitor(r, f, beats=0)
    if r["adm_calls"]:
        _ck(bool(r["adm_calls"][0]["peer_started"]), "peer identity missing", f)
    return f


@control
def control_89_keyboardinterrupt_in_monitor_flush(src):
    """Recorded behaviour, not a requirement change: the monitor flush is the
    pre-existing, registered block and is not guarded against BaseException,
    so a KeyboardInterrupt inside it ends the finally before the admission
    block. The exception propagates; no admission evidence and no admission
    health line are written (bundle v3, observation O1)."""
    r, f = run("monobs_flush_keyboardinterrupt", src), []
    _ck(r["rc"] != 0 and "KeyboardInterrupt" in r["stderr"],
        f"exit {r['rc']}", f)
    _ck(not r["adm_calls"] and not r["adm_health"],
        "admission block ran after a BaseException in the monitor flush", f)
    return f


@control
def control_90_real_observer_pairing_and_read_only_peer(src):
    """Real admission observer: one heartbeat with decision_path monitor,
    peer_status identified, peer fields equal to the monitor heartbeat's, and
    the monitor store unchanged by the admission flush (read-only peer)."""
    r, f = run("admobs_real", src), []
    _ck(r["rc"] == 0, f"exit {r['rc']}: {r['last_err']!r}", f)
    _ck(len(r["adm_beats"]) == 1 and len(r["mon_beats"]) == 1,
        f"heartbeats adm {len(r['adm_beats'])} mon {len(r['mon_beats'])}", f)
    if r["adm_beats"] and r["mon_beats"]:
        a, m = r["adm_beats"][0], r["mon_beats"][0]
        _ck(a["decision_path"] == "monitor" and a["peer_status"] == "identified",
            f"peer_status {a.get('peer_status')}", f)
        _ck(a["peer_cycle_started_at"] == m["cycle_started_at"],
            "pairing key differs", f)
        _ck(a["peer_invocation_token"] == m.get("invocation_token"),
            "peer token differs", f)
        _ck(a["cycle_id"] != m.get("cycle_id"), "shared cycle_id", f)
        _ck(a["call_sites"]["promotion_monitor"]["state"] == "not_expected"
            and a["call_sites"]["promotion_monitor"].get("reason")
            == "no_state_update", f"coverage {a['call_sites']}", f)
    _ck(not r["adm_health"] and not r["mon_health"], "health lines", f)
    return f


def _peer_read_only_in_process():
    """flush() must leave the peer byte-identical (§3.4: read only)."""
    sys.path.insert(0, ROOT)
    from engine import admission_observer as ao
    tmp = tempfile.mkdtemp(prefix="adm_peer_")
    try:
        ao.LOGS_DIR = Path(tmp)
        peer = {"invocation_token": 3, "cycle_id": None,
                "cycle_started_at": "2026-11-03T16:10:00.123456+00:00",
                "observations": [{"symbol": "X", "nested": [1, 2]}],
                "flushed": True}
        before = json.dumps(peer, sort_keys=True)
        ids = (id(peer), id(peer["observations"]))
        store = {"invocation_token": 3, "records": [], "queue_events": [],
                 "monitor_completed": True, "flushed": False}
        s = ao.flush(store, decision_path="monitor", peer=peer)
        same = (json.dumps(peer, sort_keys=True) == before
                and ids == (id(peer), id(peer["observations"])))
        beat = _lines(os.path.join(tmp, "admission_heartbeat_v1.jsonl"))
        unid = ao.flush({"records": [], "flushed": False}, "monitor",
                        peer={"cycle_started_at": None})
        none = ao.flush({"records": [], "flushed": False}, "monitor", peer=None)
        beats = _lines(os.path.join(tmp, "admission_heartbeat_v1.jsonl"))
        return (same and s["heartbeat_written"] and len(beat) == 1
                and beat[0]["peer_status"] == "identified"
                and unid["heartbeat_written"] and none["heartbeat_written"]
                and [b["peer_status"] for b in beats]
                == ["identified", "peer_unidentified", "peer_unavailable"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _mutant(tmp, kind):
    tree = ast.parse(open(DEPLOYED, encoding="utf-8").read())
    main = [n for n in tree.body if isinstance(n, ast.If)
            and ast.unparse(n.test) == "__name__ == '__main__'"][0]
    outer = [n for n in main.body if isinstance(n, ast.Try)][0]
    fb = outer.finalbody
    if not (len(fb) == 2 and isinstance(fb[1], ast.Try)
            and ast.unparse(fb[1].handlers[0].type) == "BaseException"):
        raise ValueError("finally is not [monitor flush, guarded admission block]")
    if kind == "guard_removed":
        outer.finalbody = [fb[0]] + fb[1].body
    elif kind == "order_swapped":
        outer.finalbody = [fb[1], fb[0]]
    path = os.path.join(tmp, f"monitor_trades_{kind}.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(ast.unparse(tree) + "\n")
    return path


def main():
    failed = 0
    for fn in CONTROLS:
        fails = fn(DEPLOYED)
        failed += bool(fails)
        print(f"  {'PASS' if not fails else 'FAIL'}  {fn.__name__}")
        for x in fails:
            print(f"        {x}")
    ok = _peer_read_only_in_process()
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  control_91_peer_read_only_and_peer_status_values")
    must = {"guard_removed": {
                "control_82_keyboardinterrupt_in_admission_flush_keeps_in_flight_exception",
                "control_83_systemexit_in_admission_flush_cannot_end_a_completed_monitor"},
            "order_swapped": {
                "control_80_normal_run_pairs_and_orders",
                "control_81_raising_monitor_flushes_both_and_propagates",
                "control_87_monitor_recorder_unwritable_admission_still_flushes",
                "control_89_keyboardinterrupt_in_monitor_flush"}}
    total = len(CONTROLS) + 1
    tmp = tempfile.mkdtemp(prefix="mon_adm_mut_")
    try:
        for kind, want in must.items():
            print(f"\n  anti-vacuity: mutant `{kind}`")
            path = _mutant(tmp, kind)
            for fn in CONTROLS:
                fails = fn(path)
                good = bool(fails) == (fn.__name__ in want)
                failed += not good
                total += 1
                print(f"  {'PASS' if good else 'FAIL'}  {fn.__name__} "
                      f"{'fails' if fails else 'passes'} on mutant (expected "
                      f"{'fail' if fn.__name__ in want else 'pass'})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n  {total - failed}/{total} monitor admission entrypoint checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--drive":
        drive(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        sys.exit(main())
