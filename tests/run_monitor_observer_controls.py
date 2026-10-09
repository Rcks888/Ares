#!/usr/bin/env python3
"""Standalone negative-control runner for the monitor-path recorder.

Stdlib only. Deliberately does NOT require pytest, which is absent from the VPS
venv -- installing it would change the production environment to test an
observation-only module, which is the wrong trade.

Mirrors tests/test_monitor_observer.py control for control, so the two cannot
drift: if you add a control there, add it here.

Imports engine.monitor_observer ONLY. It never imports monitor_trades, which
would pull in ib_insync and a live gateway connection. Every control redirects
the log directory to a fresh temp dir, so production evidence is never touched.

    python3 tests/run_monitor_observer_controls.py

Exit status 0 if every control passes, 1 otherwise.
"""

import json
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine import monitor_observer as mo

RESULTS = []


def control(why):
    """Register one control. `why` states the failure it exists to catch."""
    def wrap(fn):
        RESULTS.append((fn.__name__, why, fn))
        return fn
    return wrap


def isolate():
    """Fresh temp log dir. Redirects BOTH outputs through one seam.

    Giving the heartbeat an independent default is how parity fixtures once kept
    appending to the real production log while appearing isolated.
    """
    d = Path(tempfile.mkdtemp())
    mo.LOGS_DIR = d
    return d


def store(**kw):
    base = {
        "invocation_token": 1,
        "cycle_id": "test0001",
        "cycle_started_at": "2026-10-09T00:00:00+00:00",
        "params_raw": {"trailing_stop_pct": 0.10, "rsi_extreme_high": 90},
        "observations": [],
        "attempted": 0,
        "open_positions_seen": 0,
        "early_return_reason": None,
        "loop_exception": None,
        "monitor_completed": True,
        "flushed": False,
    }
    base.update(kw)
    base["params_resolved"] = mo.effective_config(base["params_raw"])
    return base


def lines(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def eq(got, want, label):
    if got != want:
        raise AssertionError(f"{label}: got {got!r}, want {want!r}")


def truthy(got, label):
    if not got:
        raise AssertionError(f"{label}: expected truthy, got {got!r}")


# --- harness self-test -----------------------------------------------------

@control("a harness that cannot report a failure proves nothing")
def control_00_harness_detects_failure():
    try:
        eq(1, 2, "deliberate")
    except AssertionError:
        return
    raise AssertionError("harness did not raise on a false assertion")


# --- observation isolation -------------------------------------------------

@control("a recorder exception escaping into the monitor")
def control_01_flush_survives_unserialisable_value():
    isolate()

    class Hostile:
        def __repr__(self):
            raise RuntimeError("boom")

    s = store(observations=[{"symbol": "X", "observed_live": Hostile()}],
              attempted=1)
    summary = mo.flush(s)
    # Must return, not raise. Either it serialised or it counted a failure.
    truthy(summary["written"] + summary["write_failures"] == 1,
           "one record accounted for")


@control("a disk or permission failure becoming a trading failure")
def control_02_flush_survives_unwritable_directory():
    mo.LOGS_DIR = Path("/proc/nonexistent/forbidden")
    s = store(observations=[{"symbol": "X", "observed_live": 10.0}], attempted=1)
    summary = mo.flush(s)
    eq(summary["written"], 0, "written")
    eq(summary["write_failures"], 1, "write_failures")
    eq(summary["heartbeat_written"], False, "heartbeat_written")


@control("the finally block duplicating every record the normal flush wrote")
def control_03_flush_is_idempotent():
    isolate()
    s = store(observations=[{"symbol": "X", "observed_live": 10.0}], attempted=1)
    first = mo.flush(s)
    second = mo.flush(s)
    eq(first["written"], 1, "first write")
    eq(second["skipped_already_flushed"], True, "second skipped")
    eq(len(lines(mo.observation_path())), 1, "records on disk")


# --- coverage and freshness are PATH-SCOPED --------------------------------

@control("a report-path heartbeat satisfying a monitor freshness check")
def control_04_heartbeat_is_path_scoped():
    isolate()
    mo.flush(store())
    beat = lines(mo.heartbeat_path())[-1]
    eq(beat["decision_path"], "monitor", "decision_path")
    truthy(mo.heartbeat_path().name != "parity_heartbeat_v1.jsonl",
           "heartbeat file is distinct")
    truthy(mo.observation_path().name != "tracker_parity_v1.jsonl",
           "evidence file is distinct")


@control("zero records reading as 'no positions' when the recorder never ran")
def control_05_empty_cycle_is_not_silence():
    isolate()
    mo.flush(store(open_positions_seen=0,
                   early_return_reason="no_open_positions"))
    beat = lines(mo.heartbeat_path())[-1]
    eq(beat["open_positions_seen"], 0, "open_positions_seen")
    eq(beat["early_return_reason"], "no_open_positions", "early_return_reason")
    eq(beat["attempted"], 0, "attempted")


@control("the monitor's missing per-trade try/except hiding unevaluated "
         "positions")
def control_06_aborted_loop_is_visible():
    isolate()
    s = store(observations=[{"symbol": "A", "observed_live": 1.0,
                             "decision": "hold"},
                            {"symbol": "B", "observed_live": 2.0}],
              attempted=3, open_positions_seen=3, monitor_completed=False,
              loop_exception="AttributeError: 'NoneType' has no attribute")
    summary = mo.flush(s)
    beat = lines(mo.heartbeat_path())[-1]
    eq(beat["monitor_completed"], False, "monitor_completed")
    truthy(beat["loop_exception"].startswith("AttributeError"), "loop_exception")
    eq(beat["attempted"], 3, "attempted")
    eq(summary["written"], 2, "written")        # attempted != written, visibly


@control("a trade that failed mid-evaluation vanishing from evidence")
def control_07_partial_record_is_incomplete_not_absent():
    isolate()
    mo.flush(store(observations=[{"symbol": "B", "observed_live": 2.0}],
                   attempted=1))
    rec = lines(mo.observation_path())[-1]
    eq(rec["record_complete"], False, "record_complete")
    truthy("effective_stop_unrounded" in rec["missing_fields"],
           "stop named as missing")
    eq(rec["decision"], "incomplete", "decision")


@control("a legitimately skipped symbol reporting incomplete, making every "
         "WBD cycle look like a loop that died")
def control_08_skip_is_complete_evidence():
    isolate()
    mo.flush(store(observations=[{
        "symbol": "WBD", "entry_date": "2026-09-22", "entry_price": 30.87,
        "stop_loss": 29.31, "trailing_stop_before": 29.31,
        "peak_price_before": 30.87, "take_profit": 36.42,
        "shares_at_decision": 4.8, "ratcheted": False,
        "holding_basis": "entry_date_string", "observed_live": None,
        "skip_reason": "live_price_unavailable_cause_unknown",
        "decision": "skipped"}], attempted=1))
    rec = lines(mo.observation_path())[-1]
    eq(rec["decision"], "skipped", "decision")
    eq(rec["record_complete"], True, "record_complete")
    eq(rec["missing_fields"], [], "missing_fields")
    eq(rec["effective_stop_unrounded"], None, "no stop was evaluated")


@control("REGRESSION: an envelope pass filling absent keys before completeness "
         "is judged, so a partial record reports complete")
def control_09_derive_is_not_applied_twice():
    isolate()
    mo.flush(store(observations=[{"symbol": "B", "observed_live": 2.0}],
                   attempted=1))
    rec = lines(mo.observation_path())[-1]
    eq(rec["record_complete"], False, "record_complete")
    truthy(len(rec["missing_fields"]) > 5, "several fields named missing")
    # Every registered key is still PRESENT, so no consumer has to handle an
    # absent key.
    eq(set(rec), set(mo.REGISTERED_OBSERVATION_FIELDS), "key set")


# --- no comparator ---------------------------------------------------------

@control("a MATCH label implying an agreed reference policy exists")
def control_10_schema_has_no_comparison_fields():
    isolate()
    mo.flush(store(observations=[{"symbol": "X", "observed_live": 10.0,
                                  "decision": "hold"}], attempted=1))
    rec = lines(mo.observation_path())[-1]
    forbidden = ("difference_class", "would_change_action", "match",
                 "shadow_status", "shadow_after", "inline_status")
    bad = [k for k in rec if any(f in k.lower() for f in forbidden)]
    eq(bad, [], "comparison fields present")


@control("collapsing the observed price into the booked price, which is why "
         "PCVX's real gap is permanently unanswerable")
def control_11_live_and_booked_fill_are_separate():
    isolate()
    mo.flush(store(observations=[{
        "symbol": "P", "observed_live": 60.10, "stop_loss": 65.86,
        "effective_stop_unrounded": 65.86, "booked_fill_price": 65.86,
        "decision": "closed", "exit_reason": "stop_loss"}], attempted=1))
    rec = lines(mo.observation_path())[-1]
    eq(rec["observed_live"], 60.10, "observed_live")
    eq(rec["booked_fill_price"], 65.86, "booked_fill_price")


# --- evidence identity -----------------------------------------------------

@control("a manifest reporting a clean fingerprint for an incomplete set, as "
         "config/params.json did while never existing")
def control_12_manifest_fails_on_missing_path():
    original = mo.DECISION_MANIFEST
    try:
        mo.DECISION_MANIFEST = ("monitor_trades.py", "config/params.json")
        man = mo.manifests()["decision"]
        eq(man["complete"], False, "complete")
        truthy(any("config/params.json" in m for m in man["missing"]),
               "missing path named")
        eq(man["digests"]["config/params.json"], None, "digest")
    finally:
        mo.DECISION_MANIFEST = original


@control("a manifest entry renamed or deleted without review")
def control_13_real_manifests_are_complete():
    for name, man in mo.manifests().items():
        truthy(man["complete"], f"{name} manifest complete {man['missing']}")


@control("losing the unrounded value the decision used: a ratchet to 11.6640 "
         "persists 11.66 and leaves pre == post")
def control_14_both_stop_precisions_preserved():
    isolate()
    mo.flush(store(observations=[{
        "symbol": "E", "observed_live": 12.96,
        "trailing_stop_after_unrounded": 11.664,
        "effective_stop_unrounded": 11.664,
        "decision": "state_update"}], attempted=1))
    rec = lines(mo.observation_path())[-1]
    eq(rec["effective_stop_unrounded"], 11.664, "unrounded")
    eq(rec["effective_stop_persisted_as"], 11.66, "persisted")
    eq(rec["trailing_stop_persisted"], 11.66, "trailing persisted")


@control("comparing a 2dp peak against an unrounded one as a real difference")
def control_15_peak_precision_basis_declared():
    isolate()
    mo.flush(store(observations=[
        {"symbol": "ITUB", "observed_live": 9.92,
         "peak_price_before": 10.120109656333922, "decision": "hold"},
        {"symbol": "EROC", "observed_live": 13.07,
         "peak_price_before": 13.07, "decision": "state_update"}],
        attempted=2))
    recs = {r["symbol"]: r for r in lines(mo.observation_path())}
    eq(recs["ITUB"]["peak_price_precision_basis"],
       "unrounded_initialisation_value", "ITUB basis")
    eq(recs["EROC"]["peak_price_precision_basis"],
       "two_dp_or_equal", "EROC basis")


@control("silently comparing the monitor's local civil date against the report "
         "path's bar date")
def control_16_exit_date_basis_is_named():
    isolate()
    mo.flush(store(observations=[{"symbol": "X", "observed_live": 1.0,
                                  "decision": "hold"}], attempted=1))
    rec = lines(mo.observation_path())[-1]
    eq(rec["exit_date_basis"], "process_local_naive_civil_date", "basis")


@control("attributing a None price to a specific cause when get_live_price "
         "returns None for four distinct causes")
def control_17_unavailable_price_cause_is_unknown():
    isolate()
    mo.flush(store(observations=[{"symbol": "WBD", "observed_live": None}],
                   attempted=1))
    rec = lines(mo.observation_path())[-1]
    eq(rec["live_available"], False, "live_available")
    eq(rec["skip_reason"], "live_price_unavailable_cause_unknown", "skip_reason")
    eq(rec["decision"], "skipped", "decision")
    eq(rec["effective_stop_unrounded"], None, "no stop evaluated")


# --- the masked default divergence ----------------------------------------

@control("losing the 2026-10-07 reading, where the monitor ratcheted at 10% so "
         "config was read and neither default fired")
def control_18_masked_divergence_recorded_while_masked():
    cfg = mo.effective_config({"trailing_stop_pct": 0.10})["trailing_stop_pct"]
    eq(cfg["resolved_value"], 0.10, "resolved_value")
    eq(cfg["source"], "config", "source")
    eq(cfg["defaults_agree"], False, "defaults_agree")
    eq(cfg["masked_divergence"], True, "masked_divergence")
    eq(cfg["currently_active"], False, "currently_active")


@control("CONTROL 27: a config deletion silently putting the monitor on 8% "
         "while the report path stays on 10%")
def control_19_removing_key_exposes_disagreement():
    cfg = mo.effective_config({})["trailing_stop_pct"]
    eq(cfg["resolved_value"], 0.08, "monitor default, not the report path's")
    eq(cfg["source"], "code_default", "source")
    eq(cfg["default_at_other_site"], 0.10, "other site")
    eq(cfg["masked_divergence"], False, "no longer masked")
    eq(cfg["currently_active"], True, "currently_active")
    gate = mo.config_review_gate({})
    truthy(any(f["key"] == "trailing_stop_pct"
               and f["condition"] == "divergent_defaults_now_active"
               for f in gate), "review gate fires")


@control("treating slippage_pct and commission_per_trade as configured when "
         "they are absent from config entirely and set every booked price")
def control_20_code_only_defaults_flagged():
    cfg = mo.effective_config({})
    for key, expected in (("slippage_pct", 0.001),
                          ("commission_per_trade", 1.00)):
        eq(cfg[key]["source"], "code_default", f"{key} source")
        eq(cfg[key]["resolved_value"], expected, f"{key} value")


@control("a gate that fires constantly and is therefore ignored")
def control_21_review_gate_quiet_when_config_supplies_value():
    eq(mo.config_review_gate({"trailing_stop_pct": 0.10}), [], "gate output")


def main():
    width = max(len(n) for n, _, _ in RESULTS)
    failed = []
    for name, why, fn in RESULTS:
        try:
            fn()
            print(f"  PASS  {name.ljust(width)}  {why}")
        except Exception as exc:                          # noqa: BLE001
            failed.append((name, exc, traceback.format_exc()))
            print(f"  FAIL  {name.ljust(width)}  {why}")
            print(f"        -> {type(exc).__name__}: {exc}")
    print()
    print(f"  {len(RESULTS) - len(failed)}/{len(RESULTS)} controls passed")
    if failed:
        print()
        for name, _, tb in failed:
            print(f"--- {name} ---")
            print(tb)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
