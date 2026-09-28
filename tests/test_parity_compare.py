"""Unit tests for the Phase 4 shadow comparison classifier.

    python3 tests/test_parity_compare.py

The central property under test: a missing or failed evaluation must NEVER be
classified as agreement. Everything else is secondary.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import parity_compare as sc  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


LINEAGE = {"production_commit": "cf072ba",
           "exit_policy_md5": "d00e621da121dc19320c739bc6b81f84",
           "compatibility_contract": "tracker_v3_2dp"}


def st(**over):
    s = {"status": "open", "entry_price": 50.65, "stop_loss": 48.67,
         "shares": 2.94, "original_shares": 2.94, "trailing_stop": 48.67,
         "peak_price": 50.91, "scaled_out": False}
    s.update(over)
    return s


# --- the marker contract, asserted against live tracker source --------------
def test_marker_contract_matches_tracker_source():
    src = (ROOT / "engine" / "tracker.py").read_text(errors="replace")
    check("tracker still prints the per-symbol error marker",
          sc.MARKER_CONTRACT in src,
          "tracker.py print format changed -- shadow status capture would "
          "silently reclassify failures as valid no-action")
    check("marker prefix appears in tracker source", sc.MARKER_PREFIX in src)


def test_marker_detection_is_per_symbol():
    out = "  Error checking GILD: 'NoneType' object has no attribute 'iloc'\n  ABM: ok\n"
    check("detects the failing symbol", sc.inline_failed_for("GILD", out))
    check("does not smear onto another symbol",
          not sc.inline_failed_for("ABM", out))
    check("empty stdout is not failure", not sc.inline_failed_for("ABM", ""))
    check("None stdout is not failure", not sc.inline_failed_for("ABM", None))
    check("substring symbols do not false-positive",
          not sc.inline_failed_for("ILD", out))


# --- status derivation ------------------------------------------------------
def test_status_derivation():
    before = st()
    check("no change -> SUCCEEDED_NO_ACTION",
          sc.classify_status(before, st(), False) == sc.SUCCEEDED_NO_ACTION)
    check("closure -> SUCCEEDED_ACTION",
          sc.classify_status(before, st(status="closed"), False) == sc.SUCCEEDED_ACTION)
    check("scale-out -> SUCCEEDED_ACTION",
          sc.classify_status(before, st(scaled_out=True), False) == sc.SUCCEEDED_ACTION)
    check("trail ratchet -> SUCCEEDED_ACTION",
          sc.classify_status(before, st(trailing_stop=49.10), False) == sc.SUCCEEDED_ACTION)
    check("failure wins over identical state",
          sc.classify_status(before, st(), True) == sc.FAILED)
    check("not attempted",
          sc.classify_status(before, st(), False, attempted=False) == sc.NOT_ATTEMPTED)
    check("missing after-state -> NOT_ATTEMPTED, not no-action",
          sc.classify_status(before, None, False) == sc.NOT_ATTEMPTED)


# --- THE core property: failure is never agreement --------------------------
def test_failure_is_never_a_match():
    # The exact unsafe record from the review.
    r = sc.build_record("ABM", "2026-09-09", "t", LINEAGE,
                        sc.FAILED, sc.SUCCEEDED_NO_ACTION,
                        st(), None, st())
    check("inline FAILED + shadow no-action is NOT a match",
          r["difference_class"] == sc.INLINE_EVALUATION_FAILURE, r["difference_class"])
    check("inline_decision is None on failure", r["inline_decision"] is None)
    check("would_change_action is None, not False, on failure",
          r["would_change_action"] is None, r["would_change_action"])

    r2 = sc.build_record("ABM", "2026-09-09", "t", LINEAGE,
                         sc.FAILED, sc.FAILED, st(), None, None)
    check("both failed is a shared failure, not equivalence",
          r2["difference_class"] == sc.BOTH_EVALUATIONS_FAILED)

    r3 = sc.build_record("ABM", "2026-09-09", "t", LINEAGE,
                         sc.SUCCEEDED_NO_ACTION, sc.FAILED, st(), st(), None)
    check("shadow failure classified separately",
          r3["difference_class"] == sc.SHADOW_EVALUATION_FAILURE)

    # Failure must be resolved BEFORE payload comparison, so identical
    # payloads cannot rescue it into MATCH.
    r4 = sc.build_record("ABM", "2026-09-09", "t", LINEAGE,
                         sc.FAILED, sc.SUCCEEDED_NO_ACTION, st(), st(), st())
    check("identical payloads cannot rescue a failure into MATCH",
          r4["difference_class"] == sc.INLINE_EVALUATION_FAILURE)

    for s in (sc.NOT_ATTEMPTED,):
        r5 = sc.build_record("ABM", "2026-09-09", "t", LINEAGE,
                             s, sc.SUCCEEDED_NO_ACTION, st(), None, st())
        check(f"{s} -> INVALID_SHADOW_RECORD",
              r5["difference_class"] == sc.INVALID_SHADOW_RECORD)
    check("missing status string is invalid, not a match",
          sc.classify_result("", sc.SUCCEEDED_NO_ACTION, st(), st())[0]
          == sc.INVALID_SHADOW_RECORD)


def test_only_two_pairings_can_match():
    ok = [(sc.SUCCEEDED_NO_ACTION, sc.SUCCEEDED_NO_ACTION),
          (sc.SUCCEEDED_ACTION, sc.SUCCEEDED_ACTION)]
    states = [sc.NOT_ATTEMPTED, sc.SUCCEEDED_NO_ACTION, sc.SUCCEEDED_ACTION,
              sc.FAILED]
    for a in states:
        for b in states:
            cls, _ = sc.classify_result(a, b, st(status="closed"),
                                        st(status="closed"))
            matched = cls == sc.MATCH
            expect = (a, b) in ok
            check(f"pairing {a}/{b} match={matched}", matched == expect, cls)


def test_action_vs_no_action_is_decision_changing():
    r = sc.build_record("SDGR", "2026-09-18", "t", LINEAGE,
                        sc.SUCCEEDED_ACTION, sc.SUCCEEDED_NO_ACTION,
                        st(), st(status="closed"), st())
    check("one acted, one held -> DECISION_CHANGING_MISMATCH",
          r["difference_class"] == sc.DECISION_CHANGING_MISMATCH)
    check("would_change_action True", r["would_change_action"] is True)
    check("flagged as action_produced difference",
          r["differing_fields"] == ["<action_produced>"])


# --- decision vs state-only differences -------------------------------------
def test_decision_vs_state_only():
    before = st()
    r = sc.build_record("ABM", "2026-09-09", "t", LINEAGE,
                        sc.SUCCEEDED_ACTION, sc.SUCCEEDED_ACTION, before,
                        st(status="closed", exit_reason="stop_loss"),
                        st(status="closed", exit_reason="trailing_stop"))
    check("differing exit_reason is decision-changing",
          r["difference_class"] == sc.DECISION_CHANGING_MISMATCH)
    check("names the field", r["differing_fields"] == ["exit_reason"])

    # Same decision, state differs only in 2dp carry-forward.
    r2 = sc.build_record("ABM", "2026-09-09", "t", LINEAGE,
                         sc.SUCCEEDED_ACTION, sc.SUCCEEDED_ACTION, before,
                         st(trailing_stop=49.10), st(trailing_stop=49.1042))
    check("state-only difference is NOT decision-changing",
          r2["difference_class"] == sc.NON_DECISION_STATE_DIFFERENCE,
          r2["difference_class"])
    check("state-only difference does not set would_change_action",
          r2["would_change_action"] is False)
    check("names the state field", r2["differing_fields"] == ["trailing_stop"])
    check("state-only counts as passing",
          sc.NON_DECISION_STATE_DIFFERENCE in sc.PASSING_CLASSES)


def test_clean_match():
    r = sc.build_record("WBD", "2026-09-22", "t", LINEAGE,
                        sc.SUCCEEDED_NO_ACTION, sc.SUCCEEDED_NO_ACTION,
                        st(), st(), st())
    check("identical hold -> MATCH", r["difference_class"] == sc.MATCH)
    check("decision label 'hold'", r["inline_decision"] == "hold")
    check("would_change_action False", r["would_change_action"] is False)


# --- ABM and SDGR boundary cases, the required coverage ---------------------
def test_abm_equality_boundary():
    """ABM: trailing_stop == stop_loss must survive adapter projection."""
    before = st(trailing_stop=48.67, stop_loss=48.67)
    r = sc.build_record("ABM", "2026-09-09", "t", LINEAGE,
                        sc.SUCCEEDED_NO_ACTION, sc.SUCCEEDED_NO_ACTION,
                        before, st(trailing_stop=48.67, stop_loss=48.67),
                        st(trailing_stop=48.67, stop_loss=48.67))
    check("equality preserved -> MATCH", r["difference_class"] == sc.MATCH)
    # A one-cent separation is exactly what must be caught, because it changes
    # the stop_loss/trailing_stop exit label.
    r2 = sc.build_record("ABM", "2026-09-09", "t", LINEAGE,
                         sc.SUCCEEDED_ACTION, sc.SUCCEEDED_ACTION, before,
                         st(status="closed", exit_reason="stop_loss"),
                         st(status="closed", exit_reason="trailing_stop"))
    check("one-cent separation flipping the label is caught",
          r2["difference_class"] == sc.DECISION_CHANGING_MISMATCH)


def test_sdgr_dead_band():
    """SDGR: active trail below entry. Exit must attribute identically."""
    before = st(entry_price=29.35, stop_loss=25.06, trailing_stop=28.23,
                peak_price=31.37, shares=5.08, original_shares=5.08)
    after = dict(before, status="closed", exit_reason="trailing_stop",
                 exit_price=28.20)
    r = sc.build_record("SDGR", "2026-09-18", "t", LINEAGE,
                        sc.SUCCEEDED_ACTION, sc.SUCCEEDED_ACTION,
                        before, after, dict(after))
    check("dead-band exit attributed identically -> MATCH",
          r["difference_class"] == sc.MATCH, r["difference_class"])
    check("decision label names the reason",
          r["inline_decision"] == "close:trailing_stop", r["inline_decision"])
    # trail below entry must not be silently normalised toward entry
    r2 = sc.build_record("SDGR", "2026-09-18", "t", LINEAGE,
                         sc.SUCCEEDED_NO_ACTION, sc.SUCCEEDED_NO_ACTION,
                         before, dict(before),
                         dict(before, trailing_stop=29.35))
    check("trail normalised to entry is caught as a state difference",
          r2["difference_class"] == sc.NON_DECISION_STATE_DIFFERENCE)


def test_scale_out_labelled():
    before = st()
    r = sc.build_record("DYN", "2026-09-01", "t", LINEAGE,
                        sc.SUCCEEDED_ACTION, sc.SUCCEEDED_ACTION, before,
                        st(scaled_out=True, shares=1.47),
                        st(scaled_out=True, shares=1.47))
    check("scale-out decision labelled", r["inline_decision"] == "scale_out",
          r["inline_decision"])
    check("scale-out match", r["difference_class"] == sc.MATCH)


# --- record shape, lineage, JSON --------------------------------------------
def test_record_shape():
    required = ["timestamp", "symbol", "entry_date", "production_commit",
                "exit_policy_md5", "compatibility_contract",
                "inline_evaluation_status", "shadow_evaluation_status",
                "inline_decision", "shadow_decision", "inline_exit_reason",
                "shadow_exit_reason", "inline_effective_stop",
                "shadow_effective_stop", "inline_scaled_out",
                "shadow_scaled_out", "inline_persistent_state",
                "shadow_compatible_state", "difference_class",
                "would_change_action", "exception_type", "exception_message"]
    r = sc.build_record("ABM", "2026-09-09", "2026-09-26T10:00:00+08:00",
                        LINEAGE, sc.SUCCEEDED_NO_ACTION,
                        sc.SUCCEEDED_NO_ACTION, st(), st(), st(),
                        available_history_bars=260)
    for f in required:
        check(f"record has {f}", f in r)
    check("lineage carried", r["production_commit"] == "cf072ba")
    check("contract carried", r["compatibility_contract"] == "tracker_v3_2dp")
    check("history bars carried", r["available_history_bars"] == 260)
    check("JSON serialisable", json.loads(json.dumps(r)) == r)


def test_failure_metadata():
    r = sc.build_record("GILD", "2021-11-01", "t", LINEAGE, sc.FAILED,
                        sc.SUCCEEDED_NO_ACTION, st(), None, st(),
                        available_history_bars=28,
                        exception_type="AttributeError",
                        exception_message="'NoneType' object has no attribute 'iloc'")
    check("exception type recorded", r["exception_type"] == "AttributeError")
    check("exception message recorded", "NoneType" in r["exception_message"])
    check("history bars recorded for diagnosis",
          r["available_history_bars"] == 28)


# --- summary ----------------------------------------------------------------
def test_summary():
    recs = [
        sc.build_record("ABM", "d", "t", LINEAGE, sc.SUCCEEDED_NO_ACTION,
                        sc.SUCCEEDED_NO_ACTION, st(), st(), st()),
        sc.build_record("SDGR", "d", "t", LINEAGE, sc.SUCCEEDED_ACTION,
                        sc.SUCCEEDED_ACTION, st(),
                        st(trailing_stop=49.10), st(trailing_stop=49.1042)),
    ]
    s = sc.summarize(recs)
    check("counts events", s["events"] == 2)
    check("clean when only passing classes", s["acceptance_clean"] is True, s)
    check("per-symbol breakdown", set(s["by_symbol"]) == {"ABM", "SDGR"})
    recs.append(sc.build_record("ABM", "d", "t", LINEAGE, sc.FAILED,
                                sc.SUCCEEDED_NO_ACTION, st(), None, st()))
    s2 = sc.summarize(recs)
    check("one failure blocks acceptance", s2["acceptance_clean"] is False)
    check("blocking class named",
          s2["blocking_classes"] == [sc.INLINE_EVALUATION_FAILURE])
    check("summary disclaims coverage conditions", "Coverage" in s2["note"]
          or "coverage" in s2["note"])


# --- dormancy, now an allowed-import boundary -------------------------------
def test_dormancy_boundary():
    hits = subprocess.run(
        ["grep", "-rn", "--include=*.py", "-e", "parity_compare",
         "-e", "tracker_compat", str(ROOT)],
        capture_output=True, text=True).stdout.strip().splitlines()
    allowed = ("engine/tracker_compat.py", "engine/parity_compare.py",
               "engine/parity_runner.py", "tests/test_tracker_compat.py",
               "tests/test_parity_compare.py", "tests/test_parity_runner.py",
               "tools/pre_parity_snapshot.py")
    offenders = [h for h in hits if not any(a in h for a in allowed)]
    check("only the adapter, shadow module and their tests reference them",
          not offenders, "\n        " + "\n        ".join(offenders))
    tracker = (ROOT / "engine" / "tracker.py").read_text(errors="replace")
    for name in ("parity_compare", "tracker_compat", "exit_policy"):
        check(f"tracker.py does not import {name}", name not in tracker)
    check("engine/__init__.py auto-imports neither",
          "parity_compare" not in (ROOT / "engine" / "__init__.py").read_text()
          and "tracker_compat" not in (ROOT / "engine" / "__init__.py").read_text())
    # json.dumps() is serialisation; json.dump() writes to a stream. Only the
    # latter is I/O, and the earlier predicate matched both.
    csrc = (ROOT / "engine" / "parity_compare.py").read_text()
    check("parity_compare performs no I/O",
          not any(t in csrc for t in ("open(", "save_trades", "requests.",
                                      "send_telegram", "json.dump(")),
          [t for t in ("open(", "save_trades", "requests.", "send_telegram",
                       "json.dump(") if t in csrc])


def main():
    for fn in (test_marker_contract_matches_tracker_source,
               test_marker_detection_is_per_symbol, test_status_derivation,
               test_failure_is_never_a_match, test_only_two_pairings_can_match,
               test_action_vs_no_action_is_decision_changing,
               test_decision_vs_state_only, test_clean_match,
               test_abm_equality_boundary, test_sdgr_dead_band,
               test_scale_out_labelled, test_record_shape,
               test_failure_metadata, test_summary, test_dormancy_boundary):
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{'FAILED: ' + ', '.join(FAILURES) if FAILURES else 'ALL PASS'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
