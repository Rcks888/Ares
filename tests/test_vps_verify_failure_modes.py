"""Failure-mode controls for tools/vps_phase3_verify.sh.

WHY
---
The verifier printed every gate green, then raised KeyError in the cross-host
comparison, then exited 0. Three independent defects combined:

  1. EXPECT was built with bare subscripts outside the try/except, so a renamed
     baseline field raised instead of reporting.
  2. An unparseable snapshot did sys.exit(0).
  3. The comparison block's exit code was DISCARDED -- the script ended with echo
     statements, so it exited 0 even when differences were reported.

Any one of these lets a deployment gate pass without verifying anything. These
controls extract the comparison block and prove each failure mode now exits
non-zero, and that a skipped comparison is distinguishable from a passing one.
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "vps_phase3_verify.sh"
SNAPSHOT = ROOT / "tools" / "pre_parity_snapshot.py"

FAILS = []
COUNT = 0


def check(label, cond, got=None):
    global COUNT
    COUNT += 1
    if cond:
        print(f"  pass  {label}")
    else:
        print(f"  FAIL  {label}  {got if got is not None else ''}")
        FAILS.append(label)


def comparison_block():
    """The embedded python heredoc that performs the cross-host comparison."""
    src = SCRIPT.read_text()
    m = re.search(r"python3 - \"\$OUT_A\".*?<<'PY'\n(.*?)\nPY\n", src, re.S)
    assert m, "could not extract the comparison heredoc"
    return m.group(1)


BLOCK = comparison_block()


def run_block(snapshot_obj, baseline_obj):
    """Run the real comparison block against controlled inputs."""
    d = Path(tempfile.mkdtemp())
    snap = d / "snap.json"
    tools = d / "tools"
    tools.mkdir()
    if isinstance(snapshot_obj, str):
        snap.write_text(snapshot_obj)
    else:
        snap.write_text(json.dumps(snapshot_obj))
    if isinstance(baseline_obj, str):
        (tools / "parity_baseline.json").write_text(baseline_obj)
    else:
        (tools / "parity_baseline.json").write_text(json.dumps(baseline_obj))
    script = d / "block.py"
    script.write_text(BLOCK)
    # argv[2] must be the REAL snapshot tool: the display block imports the
    # state contract from it. Pointing at a non-existent copy in the temp dir
    # made the contract load fail, which silently skipped every state check.
    (tools / "pre_parity_snapshot.py").write_text(SNAPSHOT.read_text())
    r = subprocess.run([sys.executable, str(script), str(snap),
                        str(tools / "pre_parity_snapshot.py")],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def good_baseline():
    return json.loads((ROOT / "tools" / "parity_baseline.json").read_text())


def good_snapshot():
    b = good_baseline()
    return {
        "lineage": {"exit_policy_md5": b["exit_policy_md5"],
                    "tracker_md5": b["tracker_raw_bytes_md5"]},
        "marker_contract": {"tracker_source_hash": b["tracker_loaded_source_md5"]},
        "runtime": {"rounding_fingerprint": b["rounding_fingerprint"],
                    "python": "3.12.3", "pandas": "3.0.5", "numpy": "2.2.6",
                    "pandas_ta": "0.4.71b0"},
        # 'passed', 'path', 'failures' and 'tail' are required by the display
        # code that runs BEFORE the comparison. Omitting them made the block die
        # early, so every "exits non-zero" control passed on a traceback rather
        # than on detection -- a false pass in the controls themselves.
        "test_suites": {n: {"assertions": a, "passed": True,
                            "path": f"tests/test_{n}.py", "failures": [],
                            "tail": "ALL PASS"}
                        for n, a in b["suite_assertions"].items()},
        "assertion_total": b["assertion_total"],
        "tracker_diff": {"verdict": "PASS", "normalized_ast_identical": True,
                         "rollback_commit": b[
                             "tracker_diff_is_registered_phase05_only"][
                                 "rollback_commit"]},
        "phase3_gate": {}, "baseline_validity": {}, "live_logs": {},
        "parity_output": {
            "state": "NOT_DECLARED_ABSENT", "valid": True,
            "collection_declared": False, "records": 0, "failures": [],
            "notes": ["Phase 0.5: collection disabled, no evidence file"],
            "declaration": {"declared": False, "valid": True,
                            "export_lines": [], "entry_point_line": 8,
                            "value": None, "failures": []}},
        "parity_output_tracking": {"ignored": False, "trackable": True},
    }


# ---- positive control -----------------------------------------------------
def test_matching_inputs_pass_with_full_accounting():
    rc, out = run_block(good_snapshot(), good_baseline())
    check("matching inputs exit 0", rc == 0, f"rc={rc}\n{out[-700:]}")
    check("reports 9/9 PASS", "CROSS-HOST COMPARISON: 9/9 PASS" in out, out[-400:])
    check("reports FULL VPS VERIFICATION: PASS",
          "FULL VPS VERIFICATION: PASS" in out)
    check("expected comparisons stated as 9", "expected comparisons   : 9" in out)
    check("executed comparisons stated as 9", "executed comparisons   : 9" in out)
    check("successful comparisons stated as 9",
          "successful comparisons : 9" in out)


# ---- 1. missing baseline field -------------------------------------------
def test_missing_baseline_field_fails():
    b = good_baseline()
    del b["tracker_loaded_source_md5"]
    del b["tracker_raw_bytes_md5"]
    rc, out = run_block(good_snapshot(), b)
    check("missing baseline field exits non-zero", rc != 0, rc)
    check("names the absent field(s)", "BASELINE FIELD(S) ABSENT" in out,
          out[-500:])
    check("states the comparison proves nothing",
          "proves nothing" in out.lower(), out[-500:])
    check("does not report PASS", "FULL VPS VERIFICATION: PASS" not in out)


def test_renamed_field_falls_back_compatibly():
    """Old name still accepted, so an older baseline does not hard-fail."""
    b = good_baseline()
    b["tracker_source_hash"] = b.pop("tracker_loaded_source_md5")
    rc, out = run_block(good_snapshot(), b)
    check("legacy tracker_source_hash accepted", rc == 0, f"rc={rc}\n{out[-500:]}")
    check("still reports 9/9", "9/9 PASS" in out)


# ---- 2-6. wrong values ---------------------------------------------------
def test_wrong_tracker_hash_fails():
    s = good_snapshot()
    s["lineage"]["tracker_md5"] = "0" * 32
    rc, out = run_block(s, good_baseline())
    check("wrong raw-bytes hash exits non-zero", rc != 0, rc)
    check("flagged as a DIFF on the git-content hash",
          "DIFF tracker_raw_bytes_md5" in out, out[-600:])


def test_wrong_loaded_source_hash_fails():
    s = good_snapshot()
    s["marker_contract"]["tracker_source_hash"] = "0" * 32
    rc, out = run_block(s, good_baseline())
    check("wrong getsource hash exits non-zero", rc != 0, rc)
    check("flagged on the getsource basis specifically",
          "DIFF tracker_loaded_source_md5" in out, out[-600:])


def test_wrong_assertion_total_fails():
    s = good_snapshot()
    s["assertion_total"] = 1344
    rc, out = run_block(s, good_baseline())
    check("wrong assertion_total exits non-zero", rc != 0, rc)
    check("flagged as assertion_total", "DIFF assertion_total" in out, out[-500:])


def test_tracker_diff_verdict_not_pass_fails():
    s = good_snapshot()
    s["tracker_diff"]["verdict"] = "FAIL"
    rc, out = run_block(s, good_baseline())
    check("verdict FAIL exits non-zero", rc != 0, rc)
    check("flagged as tracker_diff verdict",
          "DIFF tracker_diff verdict" in out, out[-500:])


def test_normalized_ast_false_fails():
    s = good_snapshot()
    s["tracker_diff"]["normalized_ast_identical"] = False
    rc, out = run_block(s, good_baseline())
    check("normalized_ast_identical False exits non-zero", rc != 0, rc)
    check("flagged as normalized_ast_identical",
          "DIFF normalized_ast_identical" in out, out[-500:])


def test_wrong_rollback_commit_fails():
    s = good_snapshot()
    s["tracker_diff"]["rollback_commit"] = "deadbeefdeadbeef"
    rc, out = run_block(s, good_baseline())
    check("wrong rollback commit exits non-zero", rc != 0, rc)
    check("flagged as rollback commit", "DIFF rollback commit" in out, out[-500:])


def test_rounding_fingerprint_difference_fails_loudly():
    s = good_snapshot()
    s["runtime"]["rounding_fingerprint"] = {"2.675": 2.68}
    rc, out = run_block(s, good_baseline())
    check("fingerprint difference exits non-zero", rc != 0, rc)
    check("explains it breaks the tracker_v3_2dp contract",
          "tracker_v3_2dp contract does not hold" in out, out[-600:])


# ---- 7. malformed baseline / snapshot ------------------------------------
def test_malformed_baseline_json_fails():
    rc, out = run_block(good_snapshot(), "{ this is not json")
    check("malformed baseline exits non-zero", rc != 0, rc)
    check("reports the baseline as unreadable",
          "BASELINE MISSING OR UNREADABLE" in out, out[-400:])


def test_unparseable_snapshot_fails_instead_of_exiting_zero():
    """This path previously did sys.exit(0)."""
    rc, out = run_block("{ broken", good_baseline())
    check("unparseable snapshot exits non-zero", rc != 0, rc)
    check("states the comparison was NOT EXECUTED",
          "CROSS-HOST COMPARISON: NOT EXECUTED" in out, out[-400:])
    check("reports FULL VPS VERIFICATION: FAIL",
          "FULL VPS VERIFICATION: FAIL" in out)


# ---- 8-9. incomplete execution ------------------------------------------
def test_fewer_comparisons_than_expected_is_not_a_pass():
    """Simulate the original defect: the block raises partway through."""
    block = BLOCK.replace('cmp("exit_policy_md5"',
                          'raise KeyError("simulated mid-block failure")\ncmp("exit_policy_md5"')
    d = Path(tempfile.mkdtemp())
    (d / "tools").mkdir()
    (d / "snap.json").write_text(json.dumps(good_snapshot()))
    (d / "tools" / "parity_baseline.json").write_text(json.dumps(good_baseline()))
    (d / "b.py").write_text(block)
    r = subprocess.run([sys.executable, str(d / "b.py"), str(d / "snap.json"),
                        str(d / "tools" / "pre_parity_snapshot.py")],
                       capture_output=True, text=True)
    check("a mid-block exception exits non-zero", r.returncode != 0, r.returncode)
    check("does NOT print a PASS verdict",
          "FULL VPS VERIFICATION: PASS" not in r.stdout, r.stdout[-300:])
    check("does NOT print 9/9", "9/9 PASS" not in r.stdout)


def test_expected_comparison_count_matches_actual_cmp_calls():
    """Guard against adding a cmp() without updating EXPECTED_COMPARISONS."""
    n = len(re.findall(r"^cmp\(", BLOCK, re.M))
    m = re.search(r"EXPECTED_COMPARISONS = (\d+)", BLOCK)
    check("EXPECTED_COMPARISONS is declared", bool(m))
    if m:
        check(f"declared {m.group(1)} matches {n} cmp() call sites",
              int(m.group(1)) == n, f"declared={m.group(1)} actual={n}")


# ---- display contract ----------------------------------------------------
def test_display_shows_the_parity_state():
    """The gate must SHOW the state, not just a derived boolean.

    The verifier's closing prose referred to "the parity_output state above"
    while printing only parity_output_state_valid, so the checklist items
    state == ARMED_NOT_STARTED and state == ACTIVE_VALID were unverifiable from
    a normal run.
    """
    s = good_snapshot()
    rc, out = run_block(s, good_baseline())
    check("state is printed", "state               = NOT_DECLARED_ABSENT" in out,
          out[-900:])
    check("collection_declared is printed", "collection_declared = False" in out)
    check("records is printed", "records             = 0" in out)
    check("entry_point_line is printed", "entry_point_line    =" in out)
    check("declared value is printed", "declared value      =" in out)
    check("DISPLAY CONTRACT: PASS", "DISPLAY CONTRACT: PASS" in out, out[-500:])
    check("display contract does not disturb 9/9", "9/9 PASS" in out)
    check("exit still 0", rc == 0, rc)


def test_missing_parity_output_block_fails():
    """Would previously have rendered as state = None inside a green run."""
    s = good_snapshot()
    del s["parity_output"]
    rc, out = run_block(s, good_baseline())
    check("absent parity_output exits non-zero", rc != 0, rc)
    check("names the absent block",
          "parity_output block absent" in out, out[-600:])
    check("DISPLAY CONTRACT: FAIL", "DISPLAY CONTRACT: FAIL" in out)
    check("does not report overall PASS",
          "FULL VPS VERIFICATION: PASS" not in out)


def test_unrecognised_state_fails():
    s = good_snapshot()
    s["parity_output"]["state"] = "TOTALLY_NEW_STATE"
    rc, out = run_block(s, good_baseline())
    check("unrecognised state exits non-zero", rc != 0, rc)
    check("names it unrecognised",
          "is not a recognised state" in out, out[-600:])


def test_none_valued_fields_fail_rather_than_print():
    for field, msg in (("valid", "valid is not a bool"),
                       ("collection_declared",
                        "collection_declared is not a bool"),
                       ("records", "records is not a non-negative integer")):
        s = good_snapshot()
        s["parity_output"][field] = None
        rc, out = run_block(s, good_baseline())
        check(f"{field}=None exits non-zero", rc != 0, rc)
        check(f"{field}=None names the contract breach", msg in out, out[-500:])


def test_negative_record_count_fails():
    s = good_snapshot()
    s["parity_output"]["records"] = -1
    rc, out = run_block(s, good_baseline())
    check("negative records exits non-zero", rc != 0, rc)


def test_missing_declaration_block_fails():
    s = good_snapshot()
    del s["parity_output"]["declaration"]
    rc, out = run_block(s, good_baseline())
    check("absent declaration block exits non-zero", rc != 0, rc)
    check("names the declaration block",
          "declaration block absent" in out, out[-500:])


def test_append_only_required_only_for_active_valid():
    """State-aware: no file exists before the first cycle."""
    s = good_snapshot()
    s["parity_output"].update({"state": "ARMED_NOT_STARTED", "records": 0,
                               "collection_declared": True})
    s["parity_output"].pop("append_only", None)
    rc, out = run_block(s, good_baseline())
    check("ARMED_NOT_STARTED without append_only is fine", rc == 0,
          f"rc={rc}\n{out[-600:]}")
    check("ARMED_NOT_STARTED displayed",
          "state               = ARMED_NOT_STARTED" in out)

    s2 = good_snapshot()
    s2["parity_output"].update({"state": "ACTIVE_VALID", "records": 3,
                                "collection_declared": True})
    s2["parity_output"].pop("append_only", None)
    rc2, out2 = run_block(s2, good_baseline())
    check("ACTIVE_VALID without append_only fails", rc2 != 0, rc2)
    check("names the required block",
          "append_only block required" in out2, out2[-500:])


def test_append_only_block_is_displayed_when_present():
    s = good_snapshot()
    s["parity_output"].update({
        "state": "ACTIVE_VALID", "records": 3, "collection_declared": True,
        "append_only": {"prefix_preserved": True, "history_append_only": True,
                        "committed_records": 2, "working_tree_records": 3,
                        "new_records": 1}})
    rc, out = run_block(s, good_baseline())
    check("append_only line printed", "append_only         =" in out, out[-700:])
    check("prefix_preserved shown", "prefix_preserved=True" in out)
    check("new count shown", "new=1" in out)
    check("ACTIVE_VALID with append_only passes", rc == 0, rc)


def test_parity_failures_and_notes_are_surfaced():
    s = good_snapshot()
    s["parity_output"]["failures"] = ["record 2 ABM"]
    s["parity_output"]["state"] = "DECISION_CHANGING_MISMATCH_PRESENT"
    s["parity_output"]["valid"] = False
    rc, out = run_block(s, good_baseline())
    check("parity failure text surfaced", "FAILURE: record 2 ABM" in out,
          out[-600:])


def test_display_states_come_from_the_single_contract():
    src = (ROOT / "tools" / "vps_phase3_verify.sh").read_text()
    check("display imports the state contract",
          "PARITY_OUTPUT_STATES" in src)
    check("display has no hardcoded state list",
          '"ARMED_NOT_STARTED",' not in src)
    import importlib.util
    spec = importlib.util.spec_from_file_location("pps", SNAPSHOT)
    pps = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pps)
    for st in ("NOT_DECLARED_ABSENT", "ARMED_NOT_STARTED", "ACTIVE_VALID",
               "UNDECLARED_OUTPUT_PRESENT", "UNPARSEABLE_JSONL", "SCHEMA_DRIFT",
               "DECISION_CHANGING_MISMATCH_PRESENT", "EVIDENCE_TRUNCATED",
               "EVIDENCE_REWRITTEN", "LINEAGE_INCOMPLETE"):
        check(f"contract contains {st}", st in pps.PARITY_OUTPUT_STATES)
    check("append-only requirement is state-scoped",
          pps.PARITY_STATES_REQUIRING_APPEND_ONLY == ("ACTIVE_VALID",),
          pps.PARITY_STATES_REQUIRING_APPEND_ONLY)


def test_display_failure_does_not_corrupt_comparison_count():
    """A display failure must not be reported as a failed comparison."""
    s = good_snapshot()
    del s["parity_output"]
    rc, out = run_block(s, good_baseline())
    check("still reports 9 executed comparisons",
          "executed comparisons   : 9" in out, out[-700:])
    check("still reports 9 successful comparisons",
          "successful comparisons : 9" in out, out[-700:])
    check("but overall verification fails", rc != 0, rc)


# ---- shell-level wiring -------------------------------------------------
def test_script_exits_with_the_comparison_status():
    src = SCRIPT.read_text()
    check("comparison exit code is captured", "CMP_RC=$?" in src)
    check("final status considers both gates",
          '"$RC" -ne 0 ] || [ "$CMP_RC" -ne 0' in src)
    check("script ends with an explicit exit", 'exit "$FINAL"' in src)
    check("additive section can report NOT EVALUATED",
          "NOT EVALUATED" in src)
    check("a --pre-pull-commit option exists",
          "--pre-pull-commit" in src)
    check("shell syntax is valid",
          subprocess.run(["bash", "-n", str(SCRIPT)],
                         capture_output=True).returncode == 0)


if __name__ == "__main__":
    for fn in sorted([v for k, v in list(globals().items())
                      if k.startswith("test_")], key=lambda f: f.__name__):
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{COUNT} assertions")
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
        sys.exit(1)
    print("ALL PASS")
