#!/usr/bin/env python3
"""Negative controls for the isolation verifier.

    python3 tests/run_verifier_negative_controls.py

Each control mutates the REAL deployed monitor_trades.py in memory and requires
the verifier to REJECT it. A verifier that cannot fail proves nothing, and this
suite exists because three defective candidates were independently reproduced as
false passes against the previous name-based normaliser:

    A  production mutation chained onto an observation assignment
    B  production mutation hidden inside the registered configuration block
    C  production exception replaced, behind the word "flush"

Controls 1-3 reproduce exactly those. Controls 4-9 cover the remaining
rejections required by review. Control 0 proves the harness itself can fail.

Mutations are applied to the deployed file rather than to a copied replica,
because the defect that motivated control 28 in the observer suite was a control
that tested a replica and passed a mutation the real file would have failed.
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEPLOYED = os.path.join(ROOT, "monitor_trades.py")
REFERENCE = os.path.join(HERE, "fixtures", "monitor_trades_preactivation.py")
VERIFIER = os.path.join(ROOT, "tools", "verify_recorder_isolation.py")


def _run_verifier(source_text):
    """Return (exit_code, output) for the verifier on this candidate source."""
    with tempfile.TemporaryDirectory() as tmp:
        candidate = os.path.join(tmp, "monitor_trades.py")
        with open(candidate, "w", encoding="utf-8") as fh:
            fh.write(source_text)
        proc = subprocess.run(
            [sys.executable, VERIFIER, REFERENCE, candidate],
            capture_output=True, text=True)
        return proc.returncode, proc.stdout + proc.stderr


def _deployed():
    with open(DEPLOYED, encoding="utf-8") as fh:
        return fh.read()


def _replace_once(text, old, new, label):
    if text.count(old) != 1:
        raise AssertionError(
            f"{label}: anchor appears {text.count(old)} times, expected 1. "
            f"The deployed file moved; this control is no longer testing what "
            f"it claims to test.")
    return text.replace(old, new)


# --------------------------------------------------------------------------
# Controls
# --------------------------------------------------------------------------

def control_00_harness_detects_a_passing_candidate():
    """The unmutated deployed file must PASS, or every rejection below is
    meaningless -- a verifier that rejects everything is not a control."""
    code, out = _run_verifier(_deployed())
    assert code == 0, f"the approved file must pass, got {code}:\n{out}"
    assert "only registered observation additions" in out, out


def control_01_chained_production_target_on_observation_assignment():
    """Candidate A. The first target is `obs`, so a name-based rule strips the
    statement and never sees that the second target writes a stop loss."""
    src = _replace_once(_deployed(),
                        "        obs = {\n",
                        "        obs = trade['stop_loss'] = {\n",
                        "A")
    code, out = _run_verifier(src)
    assert code != 0, f"chained production target accepted:\n{out}"
    assert "UNREGISTERED ADDITION" in out, out


def control_02_production_mutation_inside_configuration_block():
    """Candidate B. The whole `if _obs is not None:` block was removed without
    inspecting its contents, so anything inside it was invisible."""
    src = _replace_once(_deployed(),
                        "    if _obs is not None:\n        try:\n",
                        "    if _obs is not None:\n        trade_stop_override = 0\n        try:\n",
                        "B")
    code, out = _run_verifier(src)
    assert code != 0, f"production mutation inside the config block accepted:\n{out}"
    assert "UNREGISTERED ADDITION" in out, out


def control_03_exception_replaced_behind_the_word_flush():
    """Candidate C. The old check asked whether the unparsed finally body
    CONTAINED the text "flush". A string literal satisfied that while the body
    replaced the production exception."""
    src = _replace_once(
        _deployed(),
        "        if _obs is None:\n            _emit_observation_health_failure(\"recorder_import_failed\",",
        "        if _obs is not None:\n            return_value = \"flush\"\n            raise RuntimeError(return_value)\n        if _obs is None:\n            _emit_observation_health_failure(\"recorder_import_failed\",",
        "C")
    code, out = _run_verifier(src)
    assert code != 0, f"exception replacement accepted:\n{out}"
    assert "UNREGISTERED ADDITION" in out, out


def control_04_persistence_call_inside_observation_assignment():
    """An observation assignment whose VALUE performs persistence."""
    src = _replace_once(_deployed(),
                        "        obs[\"exit_date_recorded\"] = today",
                        "        obs[\"exit_date_recorded\"] = save_trades(trades)",
                        "persistence-in-observation")
    code, out = _run_verifier(src)
    assert code != 0, f"persistence call inside observation accepted:\n{out}"


def control_05_acquisition_call_inside_observation_assignment():
    """An observation assignment that acquires market data, which would make
    the recorder a second consumer of the feed and change request counts."""
    src = _replace_once(_deployed(),
                        "        obs[\"peak_price_after\"] = peak_price",
                        "        obs[\"peak_price_after\"] = get_live_price(symbol)",
                        "acquisition-in-observation")
    code, out = _run_verifier(src)
    assert code != 0, f"acquisition call inside observation accepted:\n{out}"


def control_06_return_in_finally_swallows_exception():
    """A `return` in the finally block discards the in-flight exception. The
    handler still re-raises, so any check that only inspects the handler passes
    this."""
    src = _replace_once(
        _deployed(),
        "            _summary = _obs.flush(_MONITOR_EVAL)",
        "            _summary = _obs.flush(_MONITOR_EVAL)\n            return _summary",
        "return-in-finally")
    code, out = _run_verifier(src)
    assert code != 0, f"return in finally accepted:\n{out}"


def control_07_additional_handler_swallows_original_exception():
    """An extra handler placed BEFORE the registered one catches first and does
    not re-raise, while the registered bare `raise` remains present in the file
    for any descendant-search check to find."""
    src = _replace_once(
        _deployed(),
        "    except BaseException as exc:\n        _MONITOR_EVAL[\"loop_exception\"]",
        "    except Exception:\n        pass\n    except BaseException as exc:\n        _MONITOR_EVAL[\"loop_exception\"]",
        "extra-handler")
    code, out = _run_verifier(src)
    assert code != 0, f"exception-swallowing handler accepted:\n{out}"


def control_08_decision_statement_adjacent_to_registered_observation():
    """A production statement placed next to a legitimate observation line, so
    it sits inside the scope the verifier is most permissive about."""
    src = _replace_once(
        _deployed(),
        "        _MONITOR_EVAL[\"attempted\"] += 1",
        "        _MONITOR_EVAL[\"attempted\"] += 1\n        trade['trailing_stop'] = 0.0",
        "adjacent-decision")
    code, out = _run_verifier(src)
    assert code != 0, f"adjacent production statement accepted:\n{out}"


def control_09_removed_decision_logic_is_detected():
    """Deleting production logic must fail as an unregistered REMOVAL, not pass
    because only additions were being examined."""
    src = _replace_once(
        _deployed(),
        "            trade['peak_price'] = round(peak_price, 2)",
        "            pass",
        "removed-decision")
    code, out = _run_verifier(src)
    assert code != 0, f"removed decision logic accepted:\n{out}"
    assert "UNREGISTERED REMOVAL" in out or "UNREGISTERED ADDITION" in out, out


def control_10_second_entrypoint_wrapper_is_rejected():
    """Exactly one entry-point wrapper is permitted. A second invocation of
    monitor() would double-run the cycle."""
    src = _replace_once(
        _deployed(),
        "    try:\n        monitor()",
        "    try:\n        monitor()\n        monitor()",
        "double-monitor")
    code, out = _run_verifier(src)
    assert code != 0, f"duplicated monitor() call accepted:\n{out}"


def control_12_duplicated_registered_statement_is_rejected():
    """Registration must pin the OCCURRENCE COUNT, not just the shape.

    Review predicted this. Doubling `_MONITOR_EVAL["attempted"] += 1` -- which
    double-counts the population and breaks reconciliation -- passed a registry
    that recorded only shapes, because both occurrences matched one key."""
    src = _replace_once(
        _deployed(),
        "        _MONITOR_EVAL[\"attempted\"] += 1",
        "        _MONITOR_EVAL[\"attempted\"] += 1\n"
        "        _MONITOR_EVAL[\"attempted\"] += 1",
        "duplicated-registered-statement")
    code, out = _run_verifier(src)
    assert code != 0, f"duplicated registered statement accepted:\n{out}"
    assert "OCCURRENCE COUNT MISMATCH" in out, out


def control_13_missing_registered_statement_is_rejected():
    """The converse: removing a registered observation line must also fail, so
    the registry describes the candidate exactly rather than permissively."""
    src = _replace_once(
        _deployed(),
        "        _MONITOR_EVAL[\"attempted\"] += 1\n",
        "",
        "removed-registered-statement")
    code, out = _run_verifier(src)
    assert code != 0, f"missing registered statement accepted:\n{out}"
    assert "OCCURRENCE COUNT MISMATCH" in out, out


def control_14_missing_registration_table_fails_closed():
    """With nothing registered, nothing can be accepted. The verifier must
    refuse rather than treat an empty registry as 'no violations found'."""
    with tempfile.TemporaryDirectory() as tmp:
        shim = os.path.join(tmp, "verify.py")
        with open(VERIFIER, encoding="utf-8") as fh:
            text = fh.read()
        # Break only the registry import, leaving the analysis intact.
        text = text.replace("from recorder_registration import",
                            "from recorder_registration_absent import")
        with open(shim, "w", encoding="utf-8") as fh:
            fh.write(text)
        proc = subprocess.run(
            [sys.executable, shim, REFERENCE, DEPLOYED],
            capture_output=True, text=True, cwd=tmp)
        out = proc.stdout + proc.stderr
        assert proc.returncode != 0, f"empty registry passed:\n{out}"
        assert "nothing is registered" in out, out


# --------------------------------------------------------------------------
# Admission capture v1: the two further pairs, each with its own table
# (archive/DESIGN_admission_capture_v1.md §6.2). Same verifier, same rules.
# --------------------------------------------------------------------------

PAIRS = {
    "tracker": ("admission_registration_tracker",
                os.path.join(HERE, "fixtures", "tracker_preadmission.py"),
                os.path.join(ROOT, "engine", "tracker.py")),
    "daily_report": ("admission_registration_daily_report",
                     os.path.join(HERE, "fixtures",
                                  "daily_report_preadmission.py"),
                     os.path.join(ROOT, "daily_report.py")),
}


def _run_pair(pair, source_text=None, registry=None):
    reg, before, after = PAIRS[pair]
    with tempfile.TemporaryDirectory() as tmp:
        candidate = os.path.join(tmp, os.path.basename(after))
        if source_text is None:
            with open(after, encoding="utf-8") as fh:
                source_text = fh.read()
        with open(candidate, "w", encoding="utf-8") as fh:
            fh.write(source_text)
        proc = subprocess.run(
            [sys.executable, VERIFIER, "--registry", registry or reg, before,
             candidate], capture_output=True, text=True)
        return proc.returncode, proc.stdout + proc.stderr


def _deployed_pair(pair):
    with open(PAIRS[pair][2], encoding="utf-8") as fh:
        return fh.read()


def control_15_admission_pairs_pass_unmutated():
    """Harness check for both new pairs, as control 0 is for the monitor."""
    for pair in PAIRS:
        code, out = _run_pair(pair)
        assert code == 0, f"{pair}: approved file must pass, got {code}:\n{out}"
        assert "only registered observation additions" in out, out


def control_16_tracker_admission_decision_change_is_rejected():
    """>= to > in the open_trade capacity test, next to a capture line."""
    src = _replace_once(
        _deployed_pair("tracker"),
        "    if len(open_trades) + len(pending) >= max_positions:\n",
        "    if len(open_trades) + len(pending) > max_positions:\n",
        "tracker-capacity")
    code, out = _run_pair("tracker", src)
    assert code != 0, f"tracker decision change accepted:\n{out}"
    assert "UNREGISTERED" in out, out


def control_17_tracker_chained_production_target_is_rejected():
    """Candidate A again, on the admission capture: a second target writing a
    production signal field behind a registered capture assignment."""
    src = _replace_once(
        _deployed_pair("tracker"),
        '        adm["branch"] = "queued"\n',
        '        adm["branch"] = signal["confluence"] = "queued"\n',
        "tracker-chained")
    code, out = _run_pair("tracker", src)
    assert code != 0, f"chained production target accepted:\n{out}"
    assert "UNREGISTERED" in out, out


def control_18_report_swallowed_exception_is_rejected():
    """The report's __main__ must re-raise. Replacing the bare raise with pass
    changes the registered wrapper digest."""
    src = _replace_once(
        _deployed_pair("daily_report"),
        "        raise\n",
        "        pass\n",
        "report-raise")
    code, out = _run_pair("daily_report", src)
    assert code != 0, f"swallowed report exception accepted:\n{out}"
    assert "UNREGISTERED" in out, out


def control_19_unknown_registry_fails_closed():
    """A misspelt --registry must not fall back to an empty or default table."""
    code, out = _run_pair("tracker", registry="admission_registration_absent")
    assert code != 0, f"unknown registry passed:\n{out}"
    assert "nothing is registered" in out, out


CONTROLS = [v for k, v in sorted(globals().items()) if k.startswith("control_")]


def main():
    failures = []
    for fn in CONTROLS:
        name = fn.__name__
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            failures.append(name)
            print(f"  FAIL  {name}\n        {exc}")
        except Exception as exc:                      # noqa: BLE001
            failures.append(name)
            print(f"  ERROR {name}\n        {type(exc).__name__}: {exc}")
    print(f"\n  {len(CONTROLS) - len(failures)}/{len(CONTROLS)} verifier "
          f"negative controls passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
