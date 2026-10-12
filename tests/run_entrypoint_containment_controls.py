#!/usr/bin/env python3
"""Containment controls for the DEPLOYED monitor entry point.

    python3 tests/run_entrypoint_containment_controls.py

Executes the real monitor_trades.py bytes through runpy with every engine
dependency stubbed, and asserts that observation can neither alter nor mask the
production outcome.

The source is executed from a copy at a temporary path, and this suite asserts
the copy's sha256 equals the deployed file's before running it. Relocation is
what keeps the health emitter -- which resolves its output relative to its own
__file__ -- out of the production logs directory. The bytes are identical, so
this is not the replica problem that let an exception-swallowing mutation pass
an earlier control.

Nothing here touches logs/virtual_trades.json, logs/tracker_parity_v1.jsonl or
any other production artefact.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEPLOYED = ROOT / "monitor_trades.py"
DRIVER = HERE / "support" / "entrypoint_driver.py"

RESULTS = []


def control(why):
    def wrap(fn):
        RESULTS.append((fn.__name__, why, fn))
        return fn
    return wrap


def eq(got, want, label):
    if got != want:
        raise AssertionError(f"{label}: got {got!r}, want {want!r}")


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(mode):
    """Run one scenario. Returns (returncode, stdout, stderr, workdir files)."""
    work = Path(tempfile.mkdtemp())
    copy = work / "monitor_trades.py"
    shutil.copyfile(DEPLOYED, copy)
    if _digest(copy) != _digest(DEPLOYED):
        raise AssertionError("the executed copy is not the deployed bytes")

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, str(DRIVER), mode, str(work), str(copy)],
        capture_output=True, text=True, env=env, cwd=str(work))

    logs = work / "logs"
    def read(name):
        p = logs / name
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

    return {
        "code": proc.returncode,
        "out": proc.stdout,
        "err": proc.stderr,
        "observations": read("monitor_observation_v1.jsonl"),
        "heartbeats": read("monitor_heartbeat_v1.jsonl"),
        "health": read("monitor_health_v1.jsonl"),
        "work": work,
    }


# --------------------------------------------------------------------------

@control("the harness not actually running the monitor, which would make every "
         "containment claim below vacuous")
def control_50_deployed_entrypoint_runs_and_records():
    r = run("normal_hold")
    eq(r["code"], 0, f"clean cycle exit (stderr: {r['err'][-500:]})")
    eq(len(r["observations"]), 1, "one observation written")
    eq(len(r["heartbeats"]), 1, "one heartbeat written")
    eq(r["health"], [], "no health failure on a clean cycle")
    rec = r["observations"][0]
    eq(rec["symbol"], "T1", "symbol")
    eq(rec["decision"], "state_update", "peak advanced, so not a plain hold")
    eq(rec["record_complete"], True, f"violations: {rec['contract_violations']}")
    eq(r["heartbeats"][0]["coverage_complete"], True, "coverage")


@control("the production exception being swallowed or replaced by the flush in "
         "the finally block, which would hide a failed cycle")
def control_51_production_exception_propagates():
    r = run("load_raises")
    if r["code"] == 0:
        raise AssertionError("a raising monitor exited 0: exception swallowed")
    if "MonitorMarkerError" not in r["err"]:
        raise AssertionError(f"original exception not propagated:\n{r['err'][-800:]}")
    if "load_trades failed" not in r["err"]:
        raise AssertionError("exception message replaced")
    # The evidence still reaches disk, and says the cycle did not complete.
    eq(len(r["heartbeats"]), 1, "heartbeat written despite the exception")
    beat = r["heartbeats"][0]
    eq(beat["monitor_completed"], False, "monitor_completed")
    eq(beat["coverage_complete"], False, "an aborted cycle claims no coverage")
    if "MonitorMarkerError" not in str(beat.get("loop_exception")):
        raise AssertionError(f"loop_exception not recorded: {beat}")


@control("a recorder that cannot be imported stopping the monitor, or failing "
         "silently so that an unobserved cycle looks like a healthy one")
def control_52_unimportable_recorder_does_not_stop_trading():
    r = run("recorder_import_fails")
    eq(r["code"], 0, f"monitor must still complete (stderr: {r['err'][-500:]})")
    eq(r["observations"], [], "no observations, as expected")
    eq(r["heartbeats"], [], "no heartbeat, as expected")
    eq(len(r["health"]), 1, "the failure is explicitly recorded")
    h = r["health"][0]
    eq(h["observation_failed"], True, "observation_failed")
    eq(h["reason"], "recorder_import_failed", "reason")
    if not h["detail"]:
        raise AssertionError("the import error was not retained")
    if "OBSERVATION-HEALTH" not in r["err"]:
        raise AssertionError("the failure was not announced on stderr")
    # And the monitor's own output is unaffected.
    if "T1" not in r["out"]:
        raise AssertionError("trading output changed when the recorder was absent")


@control("a recorder that ran but could not write its evidence being "
         "indistinguishable from one that wrote successfully")
def control_53_unwritable_recorder_is_reported():
    r = run("recorder_cannot_write")
    eq(r["code"], 0, f"monitor must still complete (stderr: {r['err'][-500:]})")
    eq(r["observations"], [], "nothing could be written")
    eq(r["heartbeats"], [], "nothing could be written")
    eq(len(r["health"]), 1, "the write failure is recorded")
    eq(r["health"][0]["reason"], "recorder_write_failed", "reason")


@control("a recorder write failure masking a production exception -- the "
         "worst case, where both fail and only the recorder's problem surfaces")
def control_54_write_failure_cannot_mask_the_exception():
    r = run("raises_and_recorder_cannot_write")
    if r["code"] == 0:
        raise AssertionError("exit 0 while the monitor raised")
    if "MonitorMarkerError" not in r["err"]:
        raise AssertionError(
            f"the production exception was lost behind the recorder's own "
            f"failure:\n{r['err'][-800:]}")
    eq(len(r["health"]), 1, "the recorder failure is also reported")


@control("a close being recorded as completed before _close_trade returned, or "
         "the decision price being booked as the fill price")
def control_55_close_is_recorded_as_four_separate_facts():
    r = run("closes_position")
    eq(r["code"], 0, f"exit (stderr: {r['err'][-500:]})")
    rec = r["observations"][0]
    eq(rec["decision"], "closed", "decision")
    eq(rec["record_complete"], True, f"violations: {rec['contract_violations']}")
    eq(rec["close_requested"], True, "close_requested")
    eq(rec["close_function_completed"], True, "close_function_completed")
    eq(rec["close_price_argument"], 9.0, "the stop the monitor decided against")
    eq(rec["booked_exit_price"], 8.99, "the price _close_trade actually booked")
    if rec["close_price_argument"] == rec["booked_exit_price"]:
        raise AssertionError("decision price and booked price collapsed")
    beat = r["heartbeats"][0]
    eq(beat["trade_state_save_attempted"], True, "save attempted")
    eq(beat["trade_state_save_completed"], True, "save completed")


@control("a save that raised being reported as completed, which would claim "
         "persistence for an exit that was never written to disk")
def control_56_failed_save_is_not_reported_as_completed():
    r = run("save_raises")
    if r["code"] == 0:
        raise AssertionError("a failed save exited 0")
    beat = r["heartbeats"][0]
    eq(beat["trade_state_save_attempted"], True, "attempted")
    eq(beat["trade_state_save_completed"], False, "must not claim completion")
    eq(beat["coverage_complete"], False, "coverage")
    # The in-memory close still happened and is still recorded as such. That is
    # the point: the trade was mutated but never persisted.
    rec = r["observations"][0]
    eq(rec["decision"], "closed", "the close did occur in memory")
    eq(rec["close_function_completed"], True, "_close_trade returned")


@control("a serialization failure losing the heartbeat as well as the record, "
         "so a cycle with one unserialisable field reports nothing at all")
def control_58_serialization_failure_is_contained_and_reported():
    r = run("serialization_fails")
    eq(r["code"], 0, f"monitor must still complete (stderr: {r['err'][-500:]})")
    # The record could not be written, but the cycle still published a
    # heartbeat saying so, and the failure is named.
    eq(r["observations"], [], "the unserialisable record could not be written")
    eq(len(r["heartbeats"]), 1, "the heartbeat still reached disk")
    beat = r["heartbeats"][0]
    if not beat["write_failures"]:
        raise AssertionError(f"write failure not counted: {beat}")
    eq(beat["coverage_complete"], False, "coverage refused")
    eq(len(r["health"]), 1, "the failure is reported on the health path")
    eq(r["health"][0]["reason"], "recorder_write_failed", "reason")
    # And trading output is unaffected.
    if "T1" not in r["out"]:
        raise AssertionError("trading output changed on a serialization failure")


@control("the builtins-only health fallback raising when it cannot write, "
         "which would turn a diagnostic into the cause of a failed cycle")
def control_59_unwritable_health_output_cannot_break_the_cycle():
    r = run("health_unwritable")
    eq(r["code"], 0, f"monitor must still complete (stderr: {r['err'][-800:]})")
    eq(r["health"], [], "nothing could be written, as expected")
    eq(r["heartbeats"], [], "the recorder was unavailable")
    # The cycle is now genuinely unobservable on disk. That is acceptable only
    # because trading completed and the monitor's own output is intact.
    if "T1" not in r["out"]:
        raise AssertionError("trading output changed when health was unwritable")
    if "Traceback" in r["err"]:
        raise AssertionError(f"the health emitter raised:\n{r['err'][-800:]}")


@control("an unwritable health path replacing a production exception already "
         "in flight -- the diagnostic destroying the fault it describes")
def control_60_unwritable_health_preserves_an_in_flight_exception():
    r = run("health_unwritable_and_raises")
    if r["code"] == 0:
        raise AssertionError("exit 0 while the monitor raised")
    if "MonitorMarkerError" not in r["err"]:
        raise AssertionError(
            f"the production exception was replaced by the health path:\n"
            f"{r['err'][-800:]}")
    if "load_trades failed" not in r["err"]:
        raise AssertionError("exception message replaced")
    eq(r["health"], [], "health could not be written, and did not raise")


@control("a close that mutated the trade and then failed being recorded as a "
         "completed close with a booked price")
def control_61_partial_close_mutation_is_not_a_completed_close():
    r = run("close_raises_after_mutation")
    if r["code"] == 0:
        raise AssertionError("exit 0 while _close_trade raised")
    if "MonitorMarkerError" not in r["err"]:
        raise AssertionError(f"exception not propagated:\n{r['err'][-500:]}")
    eq(len(r["observations"]), 1, "the partial record was still published")
    rec = r["observations"][0]
    # What WAS observed: the request and the price argued for.
    eq(rec["close_requested"], True, "the request is recorded")
    eq(rec["close_price_argument"], 9.0, "the argument is recorded")
    # What was NOT observed, and must not be invented.
    eq(rec["close_function_completed"], None, "no completion claimed")
    eq(rec["booked_exit_price"], None, "no booked price invented")
    eq(rec["decision"], "incomplete", "not promoted to closed")
    eq(rec["record_complete"], False, "incomplete evidence")
    beat = r["heartbeats"][0]
    eq(beat["trade_state_save_attempted"], False,
       "the loop never reached the save")
    eq(beat["trade_state_save_completed"], False, "nothing was persisted")
    eq(beat["coverage_complete"], False, "coverage refused")


@control("a failure AFTER a successful save retracting the save, which would "
         "report an exit as unpersisted when it is already on disk")
def control_62_failure_after_save_does_not_retract_it():
    r = run("promote_raises_after_save")
    if r["code"] == 0:
        raise AssertionError("exit 0 while promote_queue raised")
    if "promote_queue failed" not in r["err"]:
        raise AssertionError(f"exception not propagated:\n{r['err'][-500:]}")
    beat = r["heartbeats"][0]
    eq(beat["trade_state_save_attempted"], True, "save attempted")
    eq(beat["trade_state_save_completed"], True,
       "a later failure must not retract a completed save")
    rec = r["observations"][0]
    eq(rec["decision"], "closed", "the close completed and was persisted")
    eq(rec["record_complete"], True, f"violations: {rec['contract_violations']}")
    # The cycle still did not complete, so it cannot claim coverage.
    eq(beat["monitor_completed"], False, "monitor_completed")
    eq(beat["coverage_complete"], False, "coverage refused")


@control("tests writing into the production logs directory, which would "
         "corrupt the very evidence under review")
def control_57_production_logs_are_untouched():
    prod = ROOT / "logs"
    watched = ["virtual_trades.json", "tracker_parity_v1.jsonl",
               "monitor_observation_v1.jsonl", "monitor_heartbeat_v1.jsonl",
               "monitor_health_v1.jsonl"]
    before = {n: _digest(prod / n) if (prod / n).exists() else None
              for n in watched}
    for mode in ("normal_hold", "load_raises", "recorder_import_fails",
                 "recorder_cannot_write", "closes_position", "save_raises",
                 "serialization_fails", "health_unwritable",
                 "health_unwritable_and_raises",
                 "close_raises_after_mutation", "promote_raises_after_save"):
        run(mode)
    after = {n: _digest(prod / n) if (prod / n).exists() else None
             for n in watched}
    for name in watched:
        eq(after[name], before[name], f"production artefact {name} changed")


def main():
    width = max(len(n) for n, _, _ in RESULTS)
    failed = []
    for name, why, fn in RESULTS:
        try:
            fn()
            print(f"  PASS  {name.ljust(width)}  {why}")
        except Exception as exc:                          # noqa: BLE001
            import traceback
            failed.append((name, traceback.format_exc()))
            print(f"  FAIL  {name.ljust(width)}  {why}")
            print(f"        -> {type(exc).__name__}: {exc}")
    print()
    print(f"  {len(RESULTS) - len(failed)}/{len(RESULTS)} containment "
          f"controls passed")
    for name, tb in failed:
        print(f"--- {name} ---\n{tb}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
