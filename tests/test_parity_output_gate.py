"""Controls for the phase-aware parity-evidence gate.

WHY
---
parity_output_absent was a bare .exists() encoding "Phase 4 has not started".
The first parity write would have made the verifier exit non-zero on every later
run: a permanent known-false gate requiring manual interpretation, which is the
condition that replacing logs_unchanged_since_tag was meant to end.

Three near-misses this suite exists to keep closed:

1. The draft gate detected `export ARES_PARITY=1` ANYWHERE in run_ares.sh. The
   entire purpose is proving it appears BEFORE the daily_report.py invocation --
   /root/ares/.env is sourced afterwards, so a later declaration reads as
   configured while the trading process runs with parity off.
2. The draft checked r["verdict"]. The frozen field is difference_class.
   'verdict' and 'result_class' both return None, so every real
   DECISION_CHANGING_MISMATCH would have passed silently.
3. The draft assumed `git add logs/` stages the evidence. .gitignore has
   `logs/*`, so it does not. Allow-listing a path does not override an ignore
   rule, and the evidence would have had no off-host backup while the allow-list
   implied archiving.
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import importlib.util
spec = importlib.util.spec_from_file_location(
    "pps", ROOT / "tools" / "pre_parity_snapshot.py")
pps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pps)
from engine import parity_compare as sc

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


# ---------------------------------------------------------------- declaration
REAL = (ROOT / "run_ares.sh").read_text()


def declared_with(script):
    """Run the real parser against a substituted run_ares.sh."""
    d = Path(tempfile.mkdtemp())
    (d / "run_ares.sh").write_text(script)
    (d / "engine").mkdir()
    orig = pps.ROOT
    try:
        pps.ROOT = d
        return pps._collection_declared()
    finally:
        pps.ROOT = orig


BASE = """#!/bin/bash
export ARES_SCHEDULED=1
cd /root/ares/Ares
{before}
python daily_report.py 2>&1 | tee /tmp/ares_output.txt
{after}
set -a
source /root/ares/.env
set +a
"""


def test_export_before_python_is_declared():
    r = declared_with(BASE.format(before="export ARES_PARITY=1", after=""))
    check("export before python -> declared", r["declared"] is True, r)
    check("export before python -> valid", r["valid"] is True, r["failures"])


def test_export_after_python_fails():
    r = declared_with(BASE.format(before="", after="export ARES_PARITY=1"))
    check("export AFTER python -> not declared", r["declared"] is False, r)
    check("export AFTER python -> invalid", r["valid"] is False, r)
    check("names the placement problem",
          any("AFTER" in f for f in r["failures"]), r["failures"])


def test_commented_export_is_not_declared():
    r = declared_with(BASE.format(before="# export ARES_PARITY=1", after=""))
    check("commented export -> not declared", r["declared"] is False, r)
    check("commented export -> still valid (nothing wrong)",
          r["valid"] is True, r["failures"])


def test_value_zero_is_not_declared():
    r = declared_with(BASE.format(before="export ARES_PARITY=0", after=""))
    check("ARES_PARITY=0 -> not declared", r["declared"] is False, r)


def test_two_conflicting_exports_fail():
    r = declared_with(BASE.format(
        before="export ARES_PARITY=1\nexport ARES_PARITY=0", after=""))
    check("two pre-invocation assignments -> invalid", r["valid"] is False, r)
    check("names the count", any("assignments" in f for f in r["failures"]),
          r["failures"])


def test_unset_after_export_before_python_fails():
    r = declared_with(BASE.format(
        before="export ARES_PARITY=1\nunset ARES_PARITY", after=""))
    check("unset before invocation -> not declared", r["declared"] is False, r)
    check("unset before invocation -> invalid", r["valid"] is False, r)
    check("names the no-effect condition",
          any("no effect" in f for f in r["failures"]), r["failures"])


def test_env_only_declaration_is_not_declared():
    """.env is sourced after the invocation, so it can never take effect."""
    r = declared_with(BASE.format(before="", after=""))
    check(".env-only (absent from run_ares.sh) -> not declared",
          r["declared"] is False, r)
    check(".env-only -> valid, nothing malformed", r["valid"] is True, r)


def test_missing_entry_point_is_invalid():
    r = declared_with("#!/bin/bash\nexport ARES_PARITY=1\n")
    check("no daily_report.py invocation -> invalid", r["valid"] is False, r)
    check("refuses to confirm placement",
          any("placement" in f for f in r["failures"]), r["failures"])


def test_real_script_declaration_is_well_formed():
    """Phase-aware, not phase-hardcoded.

    This previously asserted the real run_ares.sh was NOT declared, which was
    true only before activation. Committing the declaration turned it into a
    permanent false failure -- the same known-false-gate shape that replacing
    logs_unchanged_since_tag and parity_output_absent was meant to end. The
    invariant that actually holds in both phases is that the declaration is
    well-formed and that the parser agrees with an independent naive scan.
    """
    r = pps._collection_declared()
    check("real run_ares.sh declaration is well-formed", r["valid"] is True, r)
    check("real run_ares.sh entry point found",
          r["entry_point_line"] is not None, r)

    # Independent derivation: a deliberately naive scan, so agreement is
    # meaningful rather than the parser confirming itself.
    lines = [l.strip() for l in (ROOT / "run_ares.sh").read_text().splitlines()]
    entry = next(i for i, l in enumerate(lines)
                 if not l.startswith("#") and "daily_report.py" in l)
    naive = any(l.startswith("export ARES_PARITY=1")
                for l in lines[:entry] if not l.startswith("#"))
    check("parser agrees with an independent scan on declared",
          r["declared"] is naive, (r["declared"], naive))

    # Expectations vary by phase; the NUMBER of assertions must not. Conditional
    # execution made the count 83 undeclared and 85 declared, so during a staged
    # rollout -- when the laptop and VPS are briefly in different phases -- the
    # cross-host suite-count comparison would have failed for no real reason.
    check("value is '1' exactly when declared",
          (r["value"] == "1") == r["declared"], (r["value"], r["declared"]))
    check("no value-1 export sits after the entry point",
          all(not e["after_entry_point"] for e in r["export_lines"]
              if e["value"] == "1"), r["export_lines"])


# --------------------------------------------------------------- schema source
def test_schema_is_derived_not_hardcoded():
    f = pps._frozen_record_fields()
    check("45 frozen fields derived", len(f) == 45, len(f))
    check("difference_class is in the derived set", "difference_class" in f)
    check("'verdict' is NOT a real field", "verdict" not in f)
    check("'result_class' is NOT a real field", "result_class" not in f)
    src = (ROOT / "tools" / "pre_parity_snapshot.py").read_text()
    check("gate uses the classifier constant, not a literal string",
          "sc.DECISION_CHANGING_MISMATCH" in src)
    check("no duplicated mismatch literal in the gate",
          '"DECISION_CHANGING_MISMATCH"' not in src
          or src.count('"DECISION_CHANGING_MISMATCH"') <= 1)


def test_schema_underivable_is_a_failure():
    d = Path(tempfile.mkdtemp())
    (d / "engine").mkdir()
    (d / "engine" / "parity_compare.py").write_text("def other(): pass\n")
    orig = pps.ROOT
    # Recorded in a flag rather than the paired check(False)/check(True) raises
    # idiom. That idiom is sound, but it is indistinguishable by inspection from a
    # vacuous check(..., True), so it defeated the guard below. One unconditional
    # assertion on a boolean is equivalent and stays machine-checkable.
    raised = False
    try:
        pps.ROOT = d
        try:
            pps._frozen_record_fields()
        except Exception:
            raised = True
    finally:
        pps.ROOT = orig
    check("underivable schema raises", raised, raised)


# ----------------------------------------------------------------- gate states
def good_record(**over):
    r = {k: "x" for k in pps._frozen_record_fields()}
    r.update({"record_schema_version": sc.RECORD_SCHEMA_VERSION,
              "difference_class": sc.MATCH, "symbol": "ABM",
              "cycle_id": "c1", "timestamp": "2026-09-29T13:30:00Z",
              "production_commit": "abc1234",
              "exit_policy_md5": "d00e621da121dc19320c739bc6b81f84",
              "compatibility_contract": "tracker_v3_2dp",
              "tracker_source_hash": "12bd27d3582dc60f5dd3110b9692d458"})
    r.update(over)
    return r


def gate_with(script, lines, git=False):
    """Run the real gate in a scratch repo."""
    d = Path(tempfile.mkdtemp())
    (d / "run_ares.sh").write_text(script)
    (d / "logs").mkdir()
    (d / "engine").mkdir()
    (d / "engine" / "parity_compare.py").write_text(
        (ROOT / "engine" / "parity_compare.py").read_text())
    if lines is not None:
        (d / pps.PARITY_OUTPUT).write_text(
            "".join(json.dumps(r) + "\n" for r in lines))
    if git:
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    orig = pps.ROOT
    try:
        pps.ROOT = d
        return pps._parity_output_state()
    finally:
        pps.ROOT = orig


ON = BASE.format(before="export ARES_PARITY=1", after="")
OFF = BASE.format(before="", after="")


def test_state_not_declared_absent():
    r = gate_with(OFF, None)
    check("undeclared + absent -> NOT_DECLARED_ABSENT",
          r["state"] == "NOT_DECLARED_ABSENT", r["state"])
    check("undeclared + absent -> valid", r["valid"] is True, r)


def test_state_undeclared_output_present_fails():
    r = gate_with(OFF, [good_record()])
    check("undeclared + present -> UNDECLARED_OUTPUT_PRESENT",
          r["state"] == "UNDECLARED_OUTPUT_PRESENT", r["state"])
    check("undeclared + present -> invalid", r["valid"] is False, r)


def test_state_armed_not_started_is_a_pass_with_status():
    r = gate_with(ON, None)
    check("declared + absent -> ARMED_NOT_STARTED",
          r["state"] == "ARMED_NOT_STARTED", r["state"])
    check("ARMED_NOT_STARTED is valid", r["valid"] is True, r)
    check("ARMED_NOT_STARTED has no failures", r["failures"] == [], r)
    check("status is a note, not buried in failures",
          r["notes"] and "declared" in r["notes"][0], r["notes"])
    check("records reported as 0", r["records"] == 0, r)


def test_state_active_valid():
    r = gate_with(ON, [good_record(), good_record(symbol="SDGR")], git=True)
    check("declared + valid records -> ACTIVE_VALID",
          r["state"] == "ACTIVE_VALID", (r["state"], r["failures"]))
    check("ACTIVE_VALID is valid", r["valid"] is True, r)
    check("counts both records", r["records"] == 2, r)


def test_schema_valid_mismatch_record_is_rejected():
    """The control the 'verdict' bug would have passed."""
    bad = good_record(symbol="ABM",
                      difference_class=sc.DECISION_CHANGING_MISMATCH)
    check("mismatch record is schema-complete",
          set(bad) == pps._frozen_record_fields())
    r = gate_with(ON, [good_record(symbol="SDGR"), bad], git=True)
    check("schema-valid mismatch -> DECISION_CHANGING_MISMATCH_PRESENT",
          r["state"] == "DECISION_CHANGING_MISMATCH_PRESENT", r["state"])
    check("mismatch -> invalid", r["valid"] is False, r)
    check("names the symbol", any("ABM" in f for f in r["failures"]),
          r["failures"])


def test_mismatch_is_not_masked_by_unrelated_drift():
    """Branch selection would have reported only drift, hiding the mismatch."""
    bad = good_record(symbol="ABM",
                      difference_class=sc.DECISION_CHANGING_MISMATCH)
    drifted = good_record(symbol="SDGR", surprise=1)
    r = gate_with(ON, [drifted, bad], git=True)
    check("mismatch outranks drift in the state",
          r["state"] == "DECISION_CHANGING_MISMATCH_PRESENT", r["state"])
    check("mismatch still reported in failures",
          any("ABM" in f for f in r["failures"]), r["failures"])
    check("drift ALSO reported, not discarded",
          any("unregistered" in f for f in r["failures"]), r["failures"])


def test_unparseable_jsonl_fails():
    d = Path(tempfile.mkdtemp())
    (d / "run_ares.sh").write_text(ON)
    (d / "logs").mkdir()
    (d / "engine").mkdir()
    (d / "engine" / "parity_compare.py").write_text(
        (ROOT / "engine" / "parity_compare.py").read_text())
    (d / pps.PARITY_OUTPUT).write_text(
        json.dumps(good_record()) + "\n{ truncated\n")
    orig = pps.ROOT
    try:
        pps.ROOT = d
        r = pps._parity_output_state()
    finally:
        pps.ROOT = orig
    check("unparseable line -> UNPARSEABLE_JSONL",
          r["state"] == "UNPARSEABLE_JSONL", r["state"])
    check("unparseable -> invalid", r["valid"] is False, r)


def test_missing_field_is_schema_drift():
    bad = good_record()
    del bad["bar_rsi"]
    r = gate_with(ON, [bad], git=True)
    check("missing field -> SCHEMA_DRIFT", r["state"] == "SCHEMA_DRIFT",
          r["state"])
    check("names the missing field", any("bar_rsi" in f for f in r["failures"]),
          r["failures"])


def test_unregistered_extra_field_is_schema_drift():
    r = gate_with(ON, [good_record(surprise=1)], git=True)
    check("extra field -> SCHEMA_DRIFT", r["state"] == "SCHEMA_DRIFT", r["state"])
    check("names it unregistered",
          any("unregistered" in f for f in r["failures"]), r["failures"])


def test_wrong_schema_version_is_drift():
    r = gate_with(ON, [good_record(record_schema_version=99)], git=True)
    check("wrong schema_version -> SCHEMA_DRIFT",
          r["state"] == "SCHEMA_DRIFT", r["state"])


def test_incomplete_lineage_fails():
    r = gate_with(ON, [good_record(production_commit=None)], git=True)
    check("missing production_commit -> LINEAGE_INCOMPLETE",
          r["state"] == "LINEAGE_INCOMPLETE", r["state"])
    check("lineage -> invalid", r["valid"] is False, r)


def test_duplicate_cycle_symbol_identity_is_drift():
    r = gate_with(ON, [good_record(), good_record()], git=True)
    check("duplicate cycle/symbol -> SCHEMA_DRIFT",
          r["state"] == "SCHEMA_DRIFT", r["state"])
    check("names the duplicate", any("duplicate" in f for f in r["failures"]),
          r["failures"])


def test_declaration_malformed_short_circuits():
    r = gate_with(BASE.format(before="", after="export ARES_PARITY=1"), None)
    check("bad placement -> DECLARATION_MALFORMED",
          r["state"] == "DECLARATION_MALFORMED", r["state"])
    check("malformed -> invalid", r["valid"] is False, r)


# ------------------------------------------------------------- append-only
def git_repo_with_history(versions):
    """Commit each version in turn; return the repo path."""
    d = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
    (d / "logs").mkdir()
    for v in versions:
        (d / pps.PARITY_OUTPUT).write_text(v)
        subprocess.run(["git", "add", "-f", pps.PARITY_OUTPUT], cwd=d, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=d, check=True)
    return d


def hist(versions, working=None):
    d = git_repo_with_history(versions)
    if working is not None:
        (d / pps.PARITY_OUTPUT).write_text(working)
    orig = pps.ROOT
    try:
        pps.ROOT = d
        return pps._append_only_history()
    finally:
        pps.ROOT = orig


A = json.dumps(good_record()) + "\n"
B = json.dumps(good_record(symbol="SDGR")) + "\n"
C = json.dumps(good_record(symbol="SECZ")) + "\n"


def test_append_only_clean_history():
    r = hist([A, A + B], working=A + B + C)
    check("clean append history -> append_only True",
          r["history_append_only"] is True, r)
    check("working tree is a prefix-extension",
          r["prefix_preserved"] is True, r)
    check("counts committed records", r["committed_records"] == 2, r)
    check("counts working records", r["working_tree_records"] == 3, r)
    check("new_records = 1", r["new_records"] == 1, r)


def test_truncated_working_tree_detected():
    r = hist([A, A + B], working=A)
    check("truncation detected", r["prefix_preserved"] is False, r)
    check("names truncation/rewrite",
          any("truncated" in v or "rewritten" in v for v in r["violations"]),
          r["violations"])


def test_rewritten_old_record_detected_at_same_line_count():
    """Line count is unchanged; only the content of an old record differs."""
    tampered = B + C
    r = hist([A, A + B], working=tampered)
    check("same line count but rewritten prefix is detected",
          r["prefix_preserved"] is False, r)
    check("line count alone would NOT have caught it",
          r["working_tree_records"] == r["committed_records"],
          (r["working_tree_records"], r["committed_records"]))


def test_reordering_detected():
    r = hist([A, A + B], working=B + A)
    check("reordering detected", r["prefix_preserved"] is False, r)


def test_mid_file_insertion_detected():
    r = hist([A + B], working=A + C + B)
    check("mid-file insertion detected", r["prefix_preserved"] is False, r)


def test_history_rewrite_between_commits_detected():
    r = hist([A + B, C], working=C)
    check("non-extending commit detected",
          r["history_append_only"] is False, r)
    check("names the offending commit",
          any("byte-extension of its parent" in v for v in r["violations"]),
          r["violations"])


def test_first_cycle_has_empty_expected_prefix():
    d = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    (d / "logs").mkdir()
    (d / pps.PARITY_OUTPUT).write_text(A)
    orig = pps.ROOT
    try:
        pps.ROOT = d
        r = pps._append_only_history()
    finally:
        pps.ROOT = orig
    check("no committed parent -> prefix preserved", r["prefix_preserved"] is True, r)
    check("committed_records = 0", r["committed_records"] == 0, r)
    check("new_records equals all records", r["new_records"] == 1, r)


# ------------------------------------------------------------- trackability
def test_parity_output_is_actually_trackable():
    r = pps._parity_output_trackable()
    check("parity output is NOT ignored", r["ignored"] is False, r)
    check("parity output is trackable", r["trackable"] is True, r)


def test_ordinary_git_add_logs_stages_the_evidence():
    """The assumption that was wrong. Proven, not assumed.

    Runs against a SCRATCH repo carrying the real .gitignore, for three reasons.

    The earlier version probed the live repository and skipped itself when the
    production evidence file existed, via `check("SKIPPED: ...", True)`. That
    assertion always passed while testing nothing -- it inflated the count with a
    vacuous pass -- and it made the count phase-dependent: 2 assertions before
    the first cycle, 1 after. That is what moved parity_output 90 -> 89.

    It also mutated the live working tree (write_text, `git add logs/`, `git
    reset`) during an active migration, where a mistimed reset could unstage real
    evidence. The subject under test is .gitignore's treatment of the path, which
    a scratch repo reproduces exactly and without that risk.
    """
    d = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    (d / ".gitignore").write_text((ROOT / ".gitignore").read_text())
    (d / "logs").mkdir()
    # Every allow-listed runtime log, so a negation that accidentally unignores a
    # sibling is caught rather than assumed absent.
    for rel in pps.ALLOWED_RUNTIME_LOGS:
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")
    subprocess.run(["git", "add", "logs/"], cwd=d, capture_output=True)
    out = subprocess.run(["git", "status", "--short", "--", pps.PARITY_OUTPUT],
                         cwd=d, capture_output=True, text=True).stdout
    check("ordinary `git add logs/` stages the evidence",
          out.strip().startswith("A"), repr(out))
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"],
                            cwd=d, capture_output=True, text=True).stdout.split()
    unregistered = [f for f in staged if f not in pps.ALLOWED_RUNTIME_LOGS]
    check("no unregistered log became trackable", unregistered == [],
          unregistered)


def test_allowlist_contains_the_evidence_path():
    check("evidence path is allow-listed",
          pps.PARITY_OUTPUT in pps.ALLOWED_RUNTIME_LOGS,
          pps.ALLOWED_RUNTIME_LOGS)


def test_no_assertion_in_this_suite_is_conditionally_executed():
    """The suite count must not depend on repository or migration phase.

    Originally scoped to the declaration test only. It was then a DIFFERENT test
    -- the git-add trackability probe -- that carried a conditional
    `check("SKIPPED", True)` and moved the count 90 -> 89 the moment the first
    cycle created the evidence file. A guard covering one function could not see
    it, so it now covers every test in the file.

    A phase-dependent count breaks the cross-host suite comparison during a
    staged rollout, when the laptop and VPS are legitimately in different phases,
    and a conditional check() that asserts True is a vacuous pass inflating the
    total while testing nothing.
    """
    import ast as _ast
    tree = _ast.parse(Path(__file__).read_text())
    offenders = []
    for fn in [n for n in _ast.walk(tree) if isinstance(n, _ast.FunctionDef)
               and n.name.startswith("test_")]:
        for node in _ast.walk(fn):
            if isinstance(node, (_ast.If, _ast.For, _ast.While)):
                for inner in _ast.walk(node):
                    if (isinstance(inner, _ast.Call)
                            and getattr(inner.func, "id", None) == "check"):
                        offenders.append(f"{fn.name}:{inner.lineno}")
    check("no conditionally-executed check() anywhere in this suite",
          offenders == [], offenders)

    # A conditional check() is the mechanism; a check(..., True) literal is the
    # other half -- it cannot fail, so it measures nothing regardless of nesting.
    literal = []
    for fn in [n for n in _ast.walk(tree) if isinstance(n, _ast.FunctionDef)
               and n.name.startswith("test_")]:
        for inner in _ast.walk(fn):
            if (isinstance(inner, _ast.Call)
                    and getattr(inner.func, "id", None) == "check"
                    and len(inner.args) >= 2
                    and isinstance(inner.args[1], _ast.Constant)
                    and inner.args[1].value is True):
                literal.append(f"{fn.name}:{inner.lineno}")
    check("no check(..., True) literal that cannot fail", literal == [], literal)


def test_baseline_parity_state_is_informational_only():
    """It must not become a compared field.

    The state advances ARMED_NOT_STARTED -> ACTIVE_VALID at the first cycle and
    records only grow. Comparing either to a later run would manufacture a
    failure on correct behaviour -- the same mistake as parity_output_absent.
    """
    src = (ROOT / "tools" / "pre_parity_snapshot.py").read_text()
    check("baseline records the state for provenance",
          "parity_collection_at_generation" in src)
    check("recorded with a not-compared note",
          "never compared" in src)
    vsrc = (ROOT / "tools" / "vps_phase3_verify.sh").read_text()
    check("verifier does NOT compare the recorded state",
          "parity_collection_at_generation" not in vsrc)
    import re
    n = len(re.findall(r"^cmp\(", re.search(
        r"python3 - \"\$OUT_A\".*?<<'PY'\n(.*?)\nPY\n", vsrc, re.S).group(1),
        re.M))
    check("comparison count still 9", n == 9, n)


def test_gate_key_renamed_and_wired():
    src = (ROOT / "tools" / "pre_parity_snapshot.py").read_text()
    check("parity_output_state_valid is a gate key",
          '"parity_output_state_valid"' in src)
    check("parity_output_trackable is a gate key",
          '"parity_output_trackable"' in src)
    check("bare .exists() gate key is gone",
          '"parity_output_absent":' not in src)


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
