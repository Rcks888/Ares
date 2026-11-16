#!/usr/bin/env python3
"""Negative controls for the statement whitelist (design §3.2, §6.2 ext. 1).

    python3 tests/run_statement_whitelist_controls.py

Each control copies the three REAL admission files and their pinned bases into
a temporary tree, applies one mutation, and runs
tools/verify_statement_whitelist.py on that tree. Control 00 proves the
unmutated tree passes with the exact form counts; every other control must be
rejected with the named problem.

ARES_WLCTL_VERIFIER replaces the verifier path, so a verifier that cannot
fail can be shown to fail this suite.
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VERIFIER = (os.environ.get("ARES_WLCTL_VERIFIER")
            or os.path.join(ROOT, "tools", "verify_statement_whitelist.py"))

FILES = ("engine/tracker.py", "daily_report.py", "monitor_trades.py",
         "tests/fixtures/tracker_preadmission.py",
         "tests/fixtures/daily_report_preadmission.py",
         "tests/fixtures/monitor_trades_preadmission.py")

TRACKER = "engine/tracker.py"
REPORT = "daily_report.py"
MONITOR = "monitor_trades.py"


def _run(mutate=None):
    with tempfile.TemporaryDirectory() as tmp:
        for rel in FILES:
            os.makedirs(os.path.dirname(os.path.join(tmp, rel)),
                        exist_ok=True)
            shutil.copy(os.path.join(ROOT, rel), os.path.join(tmp, rel))
        if mutate:
            mutate(tmp)
        proc = subprocess.run([sys.executable, VERIFIER, tmp],
                              capture_output=True, text=True)
        return proc.returncode, proc.stdout + proc.stderr


def _replace_once(tmp, rel, old, new):
    path = os.path.join(tmp, rel)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if text.count(old) != 1:
        raise AssertionError(
            f"{rel}: anchor appears {text.count(old)} times, expected 1. The "
            f"file moved; this control no longer tests what it claims.")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace(old, new))


def _after(tmp, rel, anchor, added):
    _replace_once(tmp, rel, anchor, anchor + added)


def _append(tmp, rel, text):
    with open(os.path.join(tmp, rel), "a", encoding="utf-8") as fh:
        fh.write(text)


def _rejected(out, code, problem):
    assert code == 1, f"expected exit 1, got {code}:\n{out}"
    assert problem in out, f"expected {problem!r}:\n{out}"


FILLED = '            fe["outcome"] = "filled"\n'
PENDING_SEEN = '    fp["pending_seen"] = len(pending)\n'


def control_00_real_tree_passes():
    code, out = _run()
    assert code == 0, f"the real tree must pass, got {code}:\n{out}"
    for line in (
            "engine/tracker.py: 142 additions, 0 deletions",
            "forms    : builtin read 11, clock read 23, module addition 3, "
            "record append 12, record creation 8, store assignment 119",
            "records  : adm, cand, ex, fe, fp, mq, pp, qi",
            "daily_report.py: 19 additions, 1 deletions",
            "forms    : clock read 2, counter 1, entry-point flush wrapper 1, "
            "module addition 7, reset 2, store assignment 6, token sequence 2",
            "monitor_trades.py: 10 additions, 0 deletions",
            "forms    : entry-point flush wrapper 1, module addition 3, "
            "reset 2, store assignment 4",
            "infrastructure : 15 found, 15 registered"):
        assert line in out, f"missing {line!r}:\n{out}"


def control_01_other_call_in_a_value():
    code, out = _run(lambda t: _replace_once(
        t, TRACKER, FILLED, '            fe["outcome"] = repr(sig)\n'))
    _rejected(out, code, "call 'repr' is not permitted")


def control_02_comparison_in_a_value():
    code, out = _run(lambda t: _replace_once(
        t, TRACKER, PENDING_SEEN,
        '    fp["pending_seen"] = len(pending) > 0\n'))
    _rejected(out, code, "Compare is not a permitted value")


def control_03_branch_added():
    code, out = _run(lambda t: _after(
        t, TRACKER, PENDING_SEEN,
        '    if open_count:\n        fp["probe"] = 1\n'))
    _rejected(out, code, "If is not a permitted capture statement")


def control_04_method_call_on_a_production_object():
    code, out = _run(lambda t: _after(
        t, TRACKER, PENDING_SEEN, "    pending.sort(key=len)\n"))
    _rejected(out, code, "call 'pending.sort' is not permitted")


def control_05_chained_assignment_defect_a():
    code, out = _run(lambda t: _after(
        t, TRACKER, PENDING_SEEN,
        '    fp["probe"] = pending[0]["status"] = 1\n'))
    _rejected(out, code, "more than one assignment target")


def control_06_depth_two_target_reaches_a_production_object():
    code, out = _run(lambda t: _after(
        t, TRACKER, FILLED, '            fe["sig"]["probe"] = True\n'))
    _rejected(out, code, "assignment target is not root['key']")


def control_07_target_rooted_at_a_production_name():
    code, out = _run(lambda t: _after(
        t, TRACKER, FILLED, '            sig["probe"] = True\n'))
    _rejected(out, code, "assignment target is not root['key']")


def control_08_record_name_used_by_the_base_function():
    code, out = _run(lambda t: _after(
        t, TRACKER, PENDING_SEEN, '    trades = {"kind": "probe"}\n'))
    _rejected(out, code, "record name 'trades' is used by the base function")


def control_09_formatting_in_a_value():
    code, out = _run(lambda t: _replace_once(
        t, TRACKER, '            fe["exception"] = e\n',
        '            fe["exception"] = f"{e}"\n'))
    _rejected(out, code, "JoinedStr is not a permitted value")


def control_10_other_clock_call():
    code, out = _run(lambda t: _replace_once(
        t, TRACKER, FILLED, '            fe["outcome"] = _time.sleep(0)\n'))
    _rejected(out, code, "call '_time.sleep' is not permitted")


def control_11_counter_by_a_non_constant():
    code, out = _run(lambda t: _replace_once(
        t, REPORT, '_ADMISSION_EVAL["scan_open_trade_expected"] += 1',
        '_ADMISSION_EVAL["scan_open_trade_expected"] += len(s)'))
    _rejected(out, code, "counter must add an int constant")


def control_12_append_key_rebound_to_a_production_list():
    code, out = _run(lambda t: _replace_once(
        t, TRACKER, '            qi["evicted"].append(e)\n',
        '            qi["evicted"] = evicted\n'
        '            qi["evicted"].append(e)\n'))
    _rejected(out, code, "qi['evicted'] is also assigned by a capture "
              "statement")


def control_13_counter_key_missing_from_the_fallback_store():
    code, out = _run(lambda t: _replace_once(
        t, REPORT,
        'return {"invocation_token": token, "scan_open_trade_expected": 0}',
        'return {"invocation_token": token}'))
    _rejected(out, code, "_ADMISSION_EVAL['scan_open_trade_expected'] is not "
              "initialised as an int by every store factory")


def control_14_reset_outside_an_entry_function():
    code, out = _run(lambda t: _after(
        t, TRACKER, PENDING_SEEN, "    _ADMISSION_EVAL.clear()\n"))
    _rejected(out, code, "reset outside an entry function")


def control_15_production_statement_before_the_reset():
    today = '    today = datetime.now(MYT).strftime("%Y-%m-%d %H:%M")\n'
    reset = ("    _ADMISSION_EVAL.clear()\n"
             "    _ADMISSION_EVAL.update(_new_admission_store(_ADMISSION_SEQ))\n"
             "\n")
    code, out = _run(lambda t: _replace_once(
        t, REPORT, reset + today, today + reset))
    _rejected(out, code, "precedes the reset")


def control_16_module_level_public_name():
    code, out = _run(lambda t: _append(t, TRACKER, "\nADMISSION_DEBUG = True\n"))
    _rejected(out, code, "binds public name 'ADMISSION_DEBUG'")


def control_17_module_level_rebinds_a_base_name():
    code, out = _run(lambda t: _append(t, TRACKER, "\n_pending_age_days = None\n"))
    _rejected(out, code, "binds '_pending_age_days', which the base file "
              "already uses")


def control_18_module_level_expression():
    code, out = _run(lambda t: _append(t, TRACKER, "\nprint('probe')\n"))
    _rejected(out, code, "non-permitted kind (Expr)")


def control_19_second_clock_import():
    code, out = _run(lambda t: _append(t, TRACKER, "\nimport time as _time\n"))
    _rejected(out, code, "'_time' bound by 2 module-level additions")


def control_20_base_digest_differs():
    code, out = _run(lambda t: _append(
        t, "tests/fixtures/monitor_trades_preadmission.py", "\n# edited\n"))
    assert code == 2, f"expected exit 2, got {code}:\n{out}"
    assert "pinned" in out, out


def control_21_builtin_read_of_an_expression():
    code, out = _run(lambda t: _replace_once(
        t, TRACKER, PENDING_SEEN,
        '    fp["pending_seen"] = len(pending[1:])\n'))
    _rejected(out, code, "len() argument is not a name or constant-key "
              "subscript")


def control_22_unbound_name():
    code, out = _run(lambda t: _replace_once(
        t, TRACKER, FILLED, '            fe["outcome"] = probe_value\n'))
    _rejected(out, code, "name 'probe_value' is not definitely bound here")


def control_23_second_statement_in_the_entry_point_block():
    code, out = _run(lambda t: _append(
        t, MONITOR, "        print('after the admission flush')\n"))
    _rejected(out, code, "more than one non-capture addition in the "
              "entry-point block")


def control_24_token_sequence_outside_an_entry_function():
    code, out = _run(lambda t: _after(
        t, TRACKER, PENDING_SEEN, "    _ADMISSION_SEQ += 1\n"))
    _rejected(out, code, "counter on a name is permitted only for a new "
              "private token name in an entry function")


POST_LOOP = '    fp["pending_save_attempted"] = True\n'


def control_25_review_probe_read_before_binding():
    code, out = _run(lambda t: _after(
        t, TRACKER, '        fp["pending_seen"] = 0\n',
        '        fp["probe"] = shares\n'))
    _rejected(out, code, "name 'shares' is not definitely bound here")


def control_26_handler_reads_a_name_bound_in_the_try_body():
    code, out = _run(lambda t: _after(
        t, TRACKER, '            fe["exception"] = e\n',
        '            fe["entry_seen"] = entry_price\n'))
    _rejected(out, code, "name 'entry_price' is not definitely bound here")


def control_27_name_bound_on_both_branches_is_accepted():
    code, out = _run(lambda t: _after(
        t, TRACKER, '            take_profit = entry_price * (1 + tp_pct)\n',
        '            fe["tp_pct"] = tp_pct\n'))
    assert code == 0, f"tp_pct is bound on both branches, got {code}:\n{out}"


def control_28_loop_variable_read_after_the_loop():
    code, out = _run(lambda t: _replace_once(
        t, TRACKER, POST_LOOP, '    fp["last_symbol"] = sig\n' + POST_LOOP))
    _rejected(out, code, "name 'sig' is not definitely bound here")


def control_29_record_root_not_bound_after_the_loop():
    code, out = _run(lambda t: _replace_once(
        t, TRACKER, POST_LOOP, '    fe["late"] = 1\n' + POST_LOOP))
    _rejected(out, code, "root 'fe' is not definitely bound here")


def control_30_entry_block_reads_a_name_bound_later():
    code, out = _run(lambda t: _after(
        t, REPORT, 'if __name__ == "__main__":\n',
        '    _ADMISSION_EVAL["early_summary"] = _adm_summary\n'))
    _rejected(out, code, "name '_adm_summary' is not definitely bound here")


def control_31_review_probe_module_level_side_effect():
    code, out = _run(lambda t: _append(
        t, TRACKER, "\n_private_probe = save_trades([])\n"))
    _rejected(out, code, "UNREGISTERED INFRASTRUCTURE engine/tracker.py")


def control_32_registered_flush_wrapper_changed():
    code, out = _run(lambda t: _replace_once(
        t, REPORT, 'decision_path="report",', 'decision_path="report2",'))
    _rejected(out, code, "UNREGISTERED INFRASTRUCTURE daily_report.py "
                         "[<module>/if __name__ == '__main__']")
    _rejected(out, code, "REGISTERED INFRASTRUCTURE MISSING daily_report.py")


def control_33_registered_module_addition_changed():
    code, out = _run(lambda t: _replace_once(
        t, REPORT, "_ADMISSION_SEQ = 0", "_ADMISSION_SEQ = 1"))
    _rejected(out, code, "UNREGISTERED INFRASTRUCTURE daily_report.py "
                         "[<module>]")
    _rejected(out, code, "REGISTERED INFRASTRUCTURE MISSING daily_report.py")


def control_34_builtin_read_shadowed_by_a_local():
    code, out = _run(lambda t: _replace_once(
        t, TRACKER, POST_LOOP, '    len = list\n' + POST_LOOP))
    _rejected(out, code, "len is local to this function")


CONTROLS = [v for k, v in sorted(globals().items()) if k.startswith("control_")]

# Verifier mutants: each disables one rule in a copy of the real verifier, and
# the named control must then FAIL. A rule whose removal no control notices
# is untested.
MUTANTS = (
    ("depth-1 target rule",
     "    if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)",
     "    while (isinstance(node, ast.Subscript)\n"
     "           and isinstance(node.value, ast.Subscript)):\n"
     "        node = node.value\n"
     "    if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)",
     "control_06_depth_two_target_reaches_a_production_object"),
    ("record name freshness",
     "            if base_fn is None or name not in _identifiers(base_fn):",
     "            if True:",
     "control_08_record_name_used_by_the_base_function"),
    ("container initialisation",
     "            if not inits or any(i.get(key) != kind for i in inits):",
     "            if not inits:",
     "control_13_counter_key_missing_from_the_fallback_store"),
    ("container rebinding",
     "            if rebound:",
     "            if False:",
     "control_12_append_key_rebound_to_a_production_list"),
    ("reset prologue",
     "        for s in body[:idx[0]]:",
     "        for s in []:",
     "control_15_production_statement_before_the_reset"),
    ("module name absent from base",
     "            elif name in self.base_names:",
     "            elif False:",
     "control_17_module_level_rebinds_a_base_name"),
    ("pinned base digest",
     "    if digest != pinned:",
     "    if False:",
     "control_20_base_digest_differs"),
    ("single assignment target",
     "            if len(stmt.targets) != 1:",
     "            if False:",
     "control_05_chained_assignment_defect_a"),
    ("one entry-point wrapper",
     "    if len(entry_other) > 1:",
     "    if False:",
     "control_23_second_statement_in_the_entry_point_block"),
    ("definite binding in functions",
     "        return (d & local) | (outer - local), local",
     "        return local | outer, local",
     "control_25_review_probe_read_before_binding"),
    ("handler starts before the try body",
     "                o, hlive = self.block(h.body, d | named)",
     "                o, hlive = self.block(h.body, body | named)",
     "control_26_handler_reads_a_name_bound_in_the_try_body"),
    ("loop body may not run",
     "            self.block(s.body, d | _targets(s.target))\n"
     "            self.block(s.orelse, d)\n"
     "            return d, True",
     "            d2, _l = self.block(s.body, d | _targets(s.target))\n"
     "            self.block(s.orelse, d)\n"
     "            return d2, True",
     "control_28_loop_variable_read_after_the_loop"),
    ("record root bound",
     "                return\n            if rk[0] not in bound:",
     "                return\n            if False:",
     "control_29_record_root_not_bound_after_the_loop"),
    ("module flow in the entry block",
     "            return d | (self.builtins - mod_locals), mod_locals",
     "            return d | mod_locals | self.builtins, mod_locals",
     "control_30_entry_block_reads_a_name_bound_later"),
    ("infrastructure registration",
     "        total_problems.extend(check_infrastructure(checks, infra))",
     "        total_problems.extend([])",
     "control_31_review_probe_module_level_side_effect"),
    ("local shadowing of builtin reads",
     "            if f.id in self.shadowing:",
     "            if False:",
     "control_34_builtin_read_shadowed_by_a_local"),
)


def _run_mutants():
    real = os.path.join(ROOT, "tools", "verify_statement_whitelist.py")
    with open(real, encoding="utf-8") as fh:
        source = fh.read()
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for helper in ("verify_recorder_isolation.py",
                       "admission_whitelist_infrastructure.py"):
            shutil.copy(os.path.join(ROOT, "tools", helper), tmp)
        for i, (label, old, new, must_fail) in enumerate(MUTANTS, 1):
            name = f"mutant_{i:02d}_{label.replace(' ', '_')}"
            if source.count(old) != 1:
                results.append((name, False,
                                f"anchor appears {source.count(old)} times"))
                continue
            path = os.path.join(tmp, "verify_statement_whitelist.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(source.replace(old, new))
            env = dict(os.environ, ARES_WLCTL_VERIFIER=path,
                       ARES_WLCTL_ONLY=must_fail)
            proc = subprocess.run([sys.executable, os.path.abspath(__file__)],
                                  capture_output=True, text=True, env=env)
            out = proc.stdout + proc.stderr
            ok = proc.returncode == 1 and f"FAIL  {must_fail}" in out
            results.append((name, ok, f"{must_fail} must fail; got exit "
                            f"{proc.returncode}\n{out[-400:]}"))
    return results


def main():
    only = os.environ.get("ARES_WLCTL_ONLY")
    controls = [c for c in CONTROLS if not only or c.__name__ == only]
    if only and not controls:
        print(f"  no control named {only}")
        return 2
    failures = []
    for fn in controls:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as exc:
            failures.append(fn.__name__)
            print(f"  FAIL  {fn.__name__}\n        {exc}")
        except Exception as exc:                      # noqa: BLE001
            failures.append(fn.__name__)
            print(f"  ERROR {fn.__name__}\n        {type(exc).__name__}: {exc}")
    total = len(controls)
    if not only:
        for name, ok, detail in _run_mutants():
            total += 1
            if ok:
                print(f"  PASS  {name}")
            else:
                failures.append(name)
                print(f"  FAIL  {name}\n        {detail}")
    print(f"\n  {total - len(failures)}/{total} statement whitelist checks "
          f"passed ({len(controls)} controls"
          + ("" if only else f", {len(MUTANTS)} verifier mutants") + ")")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
