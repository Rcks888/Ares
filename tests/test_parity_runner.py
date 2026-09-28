"""Unit tests for the Phase 4 shadow wiring layer.

    python3 tests/test_parity_runner.py

Central properties: the inline result is returned unchanged no matter what the
shadow path does, and no shadow failure mode can raise into the live cycle.
"""
import json
import subprocess
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import parity_compare as sc          # noqa: E402
from engine import parity_runner as sr           # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


def unwritable_output():
    """A path that cannot be created by ANY user, including root.

    An earlier version used "/nonexistent-root-xyz/...", assuming mkdir at / would
    fail. That holds for an unprivileged user but NOT for root, so the test
    passed on the laptop (uid 1000) and failed on the VPS (uid 0) -- and worse,
    it CREATED a stray directory at the VPS filesystem root.

    Making the parent a FILE instead yields NotADirectoryError regardless of
    privilege, and writes only inside a temp dir.
    """
    blocker = Path(tempfile.mkdtemp()) / "blocker"
    blocker.write_text("not a directory")
    return str(blocker / "sub" / "out.jsonl")


def st(**over):
    s = {"symbol": "ABM", "status": "open", "entry_date": "2026-09-09",
         "entry_price": 50.65, "stop_loss": 48.67, "shares": 2.94,
         "original_shares": 2.94, "trailing_stop": 48.67, "peak_price": 50.91,
         "scaled_out": False}
    s.update(over)
    return s


def fake_tracker(marker=True):
    m = types.ModuleType("fake_tracker")
    body = "def check_open_trades():\n    pass\n"
    if marker:
        body += f'#  {sc.MARKER_CONTRACT}\n'
    src = body
    m.__file__ = "/fake/tracker.py"
    import inspect
    # inspect.getsource needs a real file; write one.
    tmp = Path(tempfile.mkdtemp()) / "fake_tracker.py"
    tmp.write_text(src)
    m.__file__ = str(tmp)
    m.__loader__ = None
    sys.modules.setdefault("fake_tracker", m)
    return m, tmp


def harness(trades_before, trades_after, stdout="", module_eval=None,
            marker=True, output=None, params=None):
    """Build a one-shot observe_cycle invocation over in-memory state."""
    mod, _ = fake_tracker(marker)
    state = {"cur": [dict(t) for t in trades_before]}

    def inline_call():
        print(stdout, end="")
        state["cur"] = [dict(t) for t in trades_after]
        return "INLINE_RESULT"

    def load_state():
        return [dict(t) for t in state["cur"]]

    me = module_eval or (lambda t, p: dict(t))
    out = output or str(Path(tempfile.mkdtemp()) / "shadow.jsonl")
    res, summ = sr.observe_cycle(mod, inline_call, load_state, me,
                                 params=params or {},
                                 lineage={"production_commit": "01617b7"},
                                 output_path=out)
    return res, summ, out


# --- the inline path is untouchable -----------------------------------------
def test_inline_result_returned_unchanged():
    res, summ, _ = harness([st()], [st()])
    check("inline result passed through", res == "INLINE_RESULT", res)
    check("one symbol attempted", summ["attempted"] == 1, summ)
    check("record written", summ["written"] == 1, summ)


def test_shadow_exception_cannot_break_cycle():
    def boom(t, p):
        raise RuntimeError("shadow exploded")
    res, summ, out = harness([st()], [st()], module_eval=boom)
    check("inline result survives shadow exception", res == "INLINE_RESULT")
    rec = json.loads(Path(out).read_text().strip())
    check("classified as shadow failure",
          rec["difference_class"] == sc.SHADOW_EVALUATION_FAILURE,
          rec["difference_class"])
    check("shadow exception type recorded",
          rec["shadow_exception_type"] == "RuntimeError")
    check("shadow exception message recorded",
          "exploded" in rec["shadow_exception_message"])
    check("would_change_action is None", rec["would_change_action"] is None)


def test_unwritable_path_premise_holds_for_any_user():
    """The write-failure tests are vacuous if their path is actually writable."""
    import os
    target = unwritable_output()
    ok = False
    try:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    except (NotADirectoryError, FileExistsError, PermissionError):
        ok = True
    check(f"unwritable-path premise holds (euid={os.geteuid()})", ok,
          f"{target} was creatable -- write-failure tests would be vacuous")
    check("append_record reports failure for it",
          sr.append_record({"x": 1}, target) is False)


def test_write_failure_is_visible_not_silent():
    res, summ, _ = harness([st()], [st()],
                           output=unwritable_output())
    check("inline result survives write failure", res == "INLINE_RESULT")
    check("write failure counted", summ["write_failures"] == 1, summ)
    check("attempted still counted", summ["attempted"] == 1)
    check("written is zero", summ["written"] == 0)
    check("fail-open is not fail-silent: attempted != written",
          summ["attempted"] != summ["written"])


def test_load_state_failure_does_not_break_cycle():
    mod, _ = fake_tracker()
    def inline_call():
        return "OK"
    def bad_load():
        raise IOError("disk gone")
    res, summ = sr.observe_cycle(mod, inline_call, bad_load,
                                 lambda t, p: dict(t))
    check("inline result survives load_state failure", res == "OK")
    check("shadow system error recorded",
          summ["shadow_system_error"] is not None, summ)


def test_inline_raise_propagates():
    """An inline raise past tracker's own handlers is a production event."""
    mod, _ = fake_tracker()
    def inline_call():
        raise KeyError("production fault")
    try:
        sr.observe_cycle(mod, inline_call, lambda: [st()],
                         lambda t, p: dict(t))
        check("inline raise propagates", False, "was swallowed")
    except KeyError:
        check("inline raise propagates, not masked by shadow layer", True)


def test_warn_called_on_degradation():
    seen = []
    mod, _ = fake_tracker()
    sr.observe_cycle(mod, lambda: "OK", lambda: [st()],
                     lambda t, p: dict(t),
                     output_path=unwritable_output(),
                     warn=seen.append)
    check("warn invoked on write failure", len(seen) == 1, seen)
    check("warn message names the cycle", "shadow system degraded" in seen[0])

    def bad_warn(msg):
        raise RuntimeError("telegram down")
    res, _ = sr.observe_cycle(mod, lambda: "OK", lambda: [st()],
                              lambda t, p: dict(t),
                              output_path=unwritable_output(),
                              warn=bad_warn)
    check("a failing warn cannot break the cycle", res == "OK")


# --- inline status derived from the marker, not from silence -----------------
def test_inline_failure_detected_from_stdout():
    out_line = "  Error checking ABM: 'NoneType' object has no attribute 'iloc'\n"
    res, summ, out = harness([st()], [st()], stdout=out_line)
    rec = json.loads(Path(out).read_text().strip())
    check("inline status FAILED from marker",
          rec["inline_evaluation_status"] == sc.FAILED,
          rec["inline_evaluation_status"])
    check("not classified as a match",
          rec["difference_class"] == sc.INLINE_EVALUATION_FAILURE)
    # MEASURED: tracker prints str(e), so the class name is absent from the
    # line entirely. The type is not recoverable and must be None rather than
    # guessed. Detection is unaffected -- it keys off the marker's presence.
    check("exception type is None, not guessed",
          rec["inline_exception_type"] is None, rec["inline_exception_type"])
    check("message captured verbatim",
          "NoneType" in rec["inline_exception_message"])
    check("type IS recovered when the message happens to name it",
          sr._marker_exc("X", "  Error checking X: KeyError: 'rsi'\n") == "KeyError")


def test_unchanged_state_without_marker_is_no_action():
    res, summ, out = harness([st()], [st()])
    rec = json.loads(Path(out).read_text().strip())
    check("no marker + no change -> SUCCEEDED_NO_ACTION",
          rec["inline_evaluation_status"] == sc.SUCCEEDED_NO_ACTION)
    check("clean match", rec["difference_class"] == sc.MATCH,
          rec["difference_class"])


def test_marker_is_per_symbol_across_positions():
    a, b = st(symbol="ABM"), st(symbol="SDGR", entry_date="2026-09-18",
                                entry_price=29.35, stop_loss=25.06,
                                trailing_stop=28.23, peak_price=31.37)
    res, summ, out = harness([a, b], [a, b],
                             stdout="  Error checking ABM: boom\n")
    recs = {json.loads(l)["symbol"]: json.loads(l)
            for l in Path(out).read_text().strip().splitlines()}
    check("two records written", len(recs) == 2, list(recs))
    check("ABM failed", recs["ABM"]["inline_evaluation_status"] == sc.FAILED)
    check("SDGR unaffected by ABM's failure",
          recs["SDGR"]["inline_evaluation_status"] == sc.SUCCEEDED_NO_ACTION)


# --- contract verification against the LOADED module ------------------------
def test_broken_marker_contract_voids_coverage():
    res, summ, out = harness([st()], [st()], marker=False)
    check("inline result still returned", res == "INLINE_RESULT")
    rec = json.loads(Path(out).read_text().strip())
    check("coverage voided", rec["difference_class"] == sc.SHADOW_CONTRACT_INVALID,
          rec["difference_class"])
    check("contract_valid False in summary", summ["contract_valid"] is False)
    check("marker mismatch flagged", rec["marker_contract_match"] is False)
    check("loaded file recorded", rec["tracker_file"] is not None)
    check("source hash recorded", rec["tracker_source_hash"] is not None)


def test_contract_records_loaded_source_not_worktree():
    mod, tmp = fake_tracker()
    info = sc.verify_marker_contract(mod)
    check("records the loaded module's file", info["tracker_file"] == str(tmp))
    check("valid when marker present", info["valid"] is True)


# --- production state mutation evidence -------------------------------------
def test_shadow_mutation_is_caught():
    """A module_eval that writes through to live state must void the record."""
    live = {"cur": [st()]}

    def inline_call():
        return "OK"

    def load_state():
        return live["cur"]          # deliberately NOT a copy

    def leaky_eval(t, p):
        live["cur"][0]["peak_price"] = 99.99    # mutate production state
        return dict(t)

    mod, _ = fake_tracker()
    out = str(Path(tempfile.mkdtemp()) / "s.jsonl")
    res, summ = sr.observe_cycle(mod, inline_call, load_state, leaky_eval,
                                 output_path=out)
    check("inline result still returned", res == "OK")
    rec = json.loads(Path(out).read_text().strip())
    check("state hashes differ", rec["production_state_hash_before"]
          != rec["production_state_hash_after"])
    check("record voided as INVALID_SHADOW_RECORD",
          rec["difference_class"] == sc.INVALID_SHADOW_RECORD,
          rec["difference_class"])
    check("mutation named", "<production_state_mutated>" in rec["differing_fields"])


def test_clean_run_reports_state_unchanged():
    res, summ, out = harness([st()], [st()])
    check("production_state_unchanged True", summ["production_state_unchanged"])
    rec = json.loads(Path(out).read_text().strip())
    check("hashes equal", rec["production_state_hash_before"]
          == rec["production_state_hash_after"])


# --- schema and boundary payloads -------------------------------------------
def test_schema_and_boundaries():
    a = st(symbol="ABM")
    sd = st(symbol="SDGR", entry_date="2026-09-18", entry_price=29.35,
            stop_loss=25.06, trailing_stop=28.23, peak_price=31.37)
    res, summ, out = harness([a, sd], [a, sd])
    recs = {json.loads(l)["symbol"]: json.loads(l)
            for l in Path(out).read_text().strip().splitlines()}
    for f in ("record_schema_version", "cycle_id", "tracker_source_hash",
              "production_state_hash_before", "production_state_hash_after",
              "shadow_exception_type", "inline_exception_type"):
        check(f"schema has {f}", f in recs["ABM"])
    check("schema version 1", recs["ABM"]["record_schema_version"] == 1)
    check("cycle_id shared across records",
          recs["ABM"]["cycle_id"] == recs["SDGR"]["cycle_id"])
    ab = recs["ABM"]["abm_equality_boundary"]
    check("ABM boundary captured", ab and ab["equal"] is True, ab)
    check("ABM labels stop_loss", ab["labels_stop_loss_if_stopped"] is True)
    check("ABM has no SDGR block", recs["ABM"]["sdgr_dead_band_state"] is None)
    dd = recs["SDGR"]["sdgr_dead_band_state"]
    check("SDGR dead band captured", dd and dd["in_dead_band"] is True, dd)
    check("SDGR gap below entry ~-3.8%",
          abs(dd["gap_below_entry_pct"] + 3.8194) < 0.01, dd)
    check("SDGR has no ABM block", recs["SDGR"]["abm_equality_boundary"] is None)


def test_closed_and_scaled_positions():
    before = st()
    after = st(status="closed", exit_reason="stop_loss", exit_price=48.62)
    res, summ, out = harness([before], [after],
                             module_eval=lambda t, p: dict(after))
    rec = json.loads(Path(out).read_text().strip())
    check("closure -> SUCCEEDED_ACTION",
          rec["inline_evaluation_status"] == sc.SUCCEEDED_ACTION)
    check("agreeing closure is a MATCH",
          rec["difference_class"] == sc.MATCH, rec["difference_class"])
    check("decision labelled", rec["inline_decision"] == "close:stop_loss")
    # Disagreeing closure
    res2, _, out2 = harness([before], [after],
                            module_eval=lambda t, p: dict(t))
    rec2 = json.loads(Path(out2).read_text().strip())
    check("inline closed, shadow held -> DECISION_CHANGING_MISMATCH",
          rec2["difference_class"] == sc.DECISION_CHANGING_MISMATCH)
    check("would_change_action True", rec2["would_change_action"] is True)


def test_non_open_positions_skipped():
    closed = st(status="closed")
    res, summ, _ = harness([closed], [closed])
    check("closed positions not attempted", summ["attempted"] == 0, summ)


def test_jsonl_is_append_only():
    out = str(Path(tempfile.mkdtemp()) / "s.jsonl")
    harness([st()], [st()], output=out)
    harness([st()], [st()], output=out)
    lines = Path(out).read_text().strip().splitlines()
    check("appends rather than truncating", len(lines) == 2, len(lines))
    check("each line valid JSON", all(json.loads(l) for l in lines))
    ids = {json.loads(l)["cycle_id"] for l in lines}
    check("distinct cycle ids", len(ids) == 2, ids)


# --- INSISTED CHECK 1: module_eval receives PRE-inline state ----------------
def deep(d):
    import copy as _c
    return _c.deepcopy(d)


def test_module_eval_receives_pre_inline_state():
    """The clone must be taken BEFORE the inline call mutates the position.

    Starting from post-inline state would make this a second evaluation of an
    already-mutated position rather than differential equivalence.
    """
    seen = []
    pre = st(peak_price=50.91, trailing_stop=48.67, scaled_out=False,
             shares=2.94)
    post = st(peak_price=52.40, trailing_stop=47.16, scaled_out=True,
              shares=1.47)

    def spy(trade, params):
        seen.append(deep(trade))
        return dict(trade)

    harness([pre], [post], module_eval=spy)
    check("module_eval called once", len(seen) == 1, len(seen))
    got = seen[0]
    check("PRE-inline peak_price", got["peak_price"] == 50.91, got["peak_price"])
    check("PRE-inline trailing_stop", got["trailing_stop"] == 48.67,
          got["trailing_stop"])
    check("PRE-inline scaled_out", got["scaled_out"] is False, got["scaled_out"])
    check("PRE-inline shares", got["shares"] == 2.94, got["shares"])
    check("did NOT receive the post-inline mutation",
          got["peak_price"] != 52.40 and got["shares"] != 1.47)


def test_module_eval_gets_an_isolated_clone():
    """Mutating the handed-in trade must not reach production state."""
    live = {"cur": [st()]}

    def vandal(trade, params):
        trade["peak_price"] = 1234.56
        trade["stop_loss"] = 0.01
        return dict(trade)

    mod, _ = fake_tracker()
    out = str(Path(tempfile.mkdtemp()) / "p.jsonl")
    sr.observe_cycle(mod, lambda: "OK", lambda: live["cur"], vandal,
                     output_path=out)
    check("production peak_price untouched", live["cur"][0]["peak_price"] == 50.91,
          live["cur"][0]["peak_price"])
    check("production stop_loss untouched", live["cur"][0]["stop_loss"] == 48.67)
    rec = json.loads(Path(out).read_text().strip())
    check("state hashes equal", rec["production_state_hash_before"]
          == rec["production_state_hash_after"])


# --- INSISTED CHECK 2: stdout preserved, not swallowed ----------------------
def test_stdout_is_preserved_for_operators():
    """Capture must tee, not intercept. VPS log output must be unchanged."""
    import io as _io
    from contextlib import redirect_stdout as _rs
    outer = _io.StringIO()
    mod, _ = fake_tracker()

    def inline_call():
        print("  ABM: holding, trail 48.67")
        return "OK"

    with _rs(outer):
        sr.observe_cycle(mod, inline_call, lambda: [st()],
                         lambda t, p: dict(t),
                         output_path=str(Path(tempfile.mkdtemp()) / "p.jsonl"))
    check("operator output still reaches the real stream",
          "ABM: holding, trail 48.67" in outer.getvalue(),
          repr(outer.getvalue()[:120]))


def test_stdout_preserved_even_when_marker_present():
    import io as _io
    from contextlib import redirect_stdout as _rs
    outer = _io.StringIO()
    mod, _ = fake_tracker()

    def inline_call():
        print("  Error checking ABM: boom")
        print("  SDGR: holding")
        return "OK"

    with _rs(outer):
        sr.observe_cycle(mod, inline_call, lambda: [st()],
                         lambda t, p: dict(t),
                         output_path=str(Path(tempfile.mkdtemp()) / "p.jsonl"))
    txt = outer.getvalue()
    check("error line preserved for operators", "Error checking ABM" in txt)
    check("normal line preserved", "SDGR: holding" in txt)


def test_tee_survives_a_broken_downstream_stream():
    import io as _io

    class Broken:
        def write(self, d):
            raise IOError("pipe closed")

        def flush(self):
            raise IOError("pipe closed")

    t = sr._Tee(_io.StringIO(), Broken())
    try:
        t.write("x")
        t.flush()
        check("tee tolerates a broken downstream stream", True)
    except Exception as exc:                        # noqa: BLE001
        check("tee tolerates a broken downstream stream", False, repr(exc))


def test_concurrency_assumption_recorded():
    """stdout redirection is process-global, so this must stay sequential."""
    src = (ROOT / "engine" / "parity_runner.py").read_text().lower()
    check("concurrency assumption recorded in the module",
          "sequential" in src and "process-global" in src)


# --- dormancy and isolation -------------------------------------------------
def test_dormancy_and_isolation():
    hits = subprocess.run(
        ["grep", "-rn", "--include=*.py", "-e", "parity_runner",
         "-e", "parity_compare", "-e", "tracker_compat", str(ROOT)],
        capture_output=True, text=True).stdout.strip().splitlines()
    allowed = ("engine/tracker_compat.py", "engine/parity_compare.py",
               "engine/parity_runner.py", "tests/test_tracker_compat.py",
               "tests/test_parity_compare.py", "tests/test_parity_runner.py",
               "tools/pre_parity_snapshot.py")
    offenders = [h for h in hits if not any(a in h for a in allowed)]
    check("no production module references the shadow stack", not offenders,
          "\n        " + "\n        ".join(offenders))
    tracker = (ROOT / "engine" / "tracker.py").read_text(errors="replace")
    for n in ("parity_runner", "parity_compare", "tracker_compat",
              "exit_policy"):
        check(f"tracker.py does not import {n}", n not in tracker)
    # Scan CODE, not prose. The module docstring legitimately names psi_state
    # while promising not to touch it; grepping raw text flagged that as a
    # violation. Strip the docstring before scanning.
    import ast
    raw = (ROOT / "engine" / "parity_runner.py").read_text()
    tree = ast.parse(raw)
    doc = ast.get_docstring(tree) or ""
    code = raw.replace(doc, "", 1)
    for forbidden in ("save_trades", "send_telegram", "psi_state",
                      "place_order", "requests.", "virtual_trades"):
        check(f"parity_runner code does not touch {forbidden}",
              forbidden not in code)
    check("writes only to its dedicated output",
          "tracker_parity_v1.jsonl" in code)


def main():
    for fn in (test_unwritable_path_premise_holds_for_any_user,
               test_inline_result_returned_unchanged,
               test_shadow_exception_cannot_break_cycle,
               test_write_failure_is_visible_not_silent,
               test_load_state_failure_does_not_break_cycle,
               test_inline_raise_propagates, test_warn_called_on_degradation,
               test_inline_failure_detected_from_stdout,
               test_unchanged_state_without_marker_is_no_action,
               test_marker_is_per_symbol_across_positions,
               test_broken_marker_contract_voids_coverage,
               test_contract_records_loaded_source_not_worktree,
               test_shadow_mutation_is_caught,
               test_clean_run_reports_state_unchanged,
               test_schema_and_boundaries, test_closed_and_scaled_positions,
               test_non_open_positions_skipped, test_jsonl_is_append_only,
               test_module_eval_receives_pre_inline_state,
               test_module_eval_gets_an_isolated_clone,
               test_stdout_is_preserved_for_operators,
               test_stdout_preserved_even_when_marker_present,
               test_tee_survives_a_broken_downstream_stream,
               test_concurrency_assumption_recorded,
               test_dormancy_and_isolation):
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{'FAILED: ' + ', '.join(FAILURES) if FAILURES else 'ALL PASS'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
