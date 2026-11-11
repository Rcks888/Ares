#!/usr/bin/env python3
"""Negative controls for the writer inventory and save data flow
(design §6.2 extension 3, §3.5, §7 "Verifier").

    python3 tests/run_writer_inventory_controls.py

Each control copies the REAL production files (ROOT/*.py, ROOT/engine/*.py)
into a temporary tree, applies one mutation, and runs
tools/verify_writer_inventory.py on that tree. Control 00 proves the
unmutated tree passes with the exact counts and the thirteen flows; every
other control must be rejected with the named problem, except the boundary
controls 25-27, which must pass.

Each verifier mutant disables one rule in a copy of the real verifier; the
named control must then fail.

ARES_WRCTL_VERIFIER replaces the verifier path, so a verifier that cannot
fail can be shown to fail this suite. ARES_WRCTL_ONLY runs one control.
"""

import glob
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REAL_VERIFIER = os.path.join(ROOT, "tools", "verify_writer_inventory.py")
VERIFIER = os.environ.get("ARES_WRCTL_VERIFIER") or REAL_VERIFIER

TRACKER = "engine/tracker.py"
MONITOR = "monitor_trades.py"
DASHBOARD = "build_dashboard.py"

OPEN_TRADE_LOAD = ("    # Save as pending — will execute at next day's open price\n"
                   "    pending = load_pending()\n")
EXPIRE_LOAD = "    queue = load_queue()\n    today = date.today()\n"
EXPIRE_SAVE = "        save_queue(active)\n"
QUEUE_SIGNAL_LOAD = ("    max_size = params.get('queue_max_size', 10)\n"
                     "    queue = load_queue()\n")
CARRIER = '    _ADMISSION_EVAL["records"].append(mq)\n    queue = expire_queue()\n'
CHECK_OPEN_SAVE = "    if updated:\n        save_trades(trades)\n\n    # Check queue"
SHADOW_LOAD = ('    """Track price movement for 30 days after trade closes."""\n'
               '    trades = load_trades()\n')
MONITOR_LOAD = ("    trades = load_trades()\n"
                "    open_trades = [t for t in trades if t['status'] == 'open']\n")

FLOWS = (
    "save_trades(trades)   [engine/tracker.py <module>/def execute_pending_signals()] <- load load_trades",
    "save_pending(pending)   [engine/tracker.py <module>/def execute_pending_signals()] <- load load_pending",
    "save_queue(queue)   [engine/tracker.py <module>/def queue_signal()] <- load load_queue",
    "save_queue(active)   [engine/tracker.py <module>/def expire_queue()] <- empty | load load_queue",
    "save_ranked(survivors)   [engine/tracker.py <module>/def maintain_queue()] <- carrier maintain_queue.queue = expire_queue() | empty",
    "for q in survivors + unverified])   [engine/tracker.py <module>/def maintain_queue()] <- carrier maintain_queue.queue = expire_queue() | empty",
    "save_ranked([])   [engine/tracker.py <module>/def maintain_queue()] <- empty",
    "save_ranked([q for q in ranked if q['symbol'] not in removed])   [engine/tracker.py <module>/def promote_queue()] <- load load_queue | load load_ranked",
    "save_queue([q for q in load_queue() if q['symbol'] not in removed])   [engine/tracker.py <module>/def promote_queue()] <- load load_queue",
    "save_pending(pending)   [engine/tracker.py <module>/def open_trade()] <- load load_pending",
    "save_trades(trades)   [engine/tracker.py <module>/def check_open_trades()] <- load load_trades",
    "save_trades(trades)   [engine/tracker.py <module>/def check_shadow_trades()] <- load load_trades",
    "save_trades(trades)   [monitor_trades.py <module>/def monitor()] <- load load_trades",
)


def _run(mutate=None):
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "engine"))
        for f in glob.glob(os.path.join(ROOT, "*.py")):
            shutil.copy(f, tmp)
        for f in glob.glob(os.path.join(ROOT, "engine", "*.py")):
            shutil.copy(f, os.path.join(tmp, "engine"))
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


def control_00_real_tree_passes():
    code, out = _run()
    assert code == 0, f"the real tree must pass, got {code}:\n{out}"
    for line in ("save sites  : 13 found, 13 registered",
                 "writers     : 20 found, 20 registered",
                 "save funcs  : 4 registered",
                 "carriers    : 1 registered",
                 "trades muts : 1 found, 1 registered"):
        assert line in out, f"missing {line!r}:\n{out}"
    for flow in FLOWS:
        assert flow in out, f"missing flow {flow!r}:\n{out}"
    assert out.count("    flow     : ") == 13, out


def control_01_new_save_queue_call():
    code, out = _run(lambda t: _append(
        t, TRACKER, "\n\ndef _requeue():\n    save_queue(load_queue())\n"))
    _rejected(out, code, "UNREGISTERED SAVE SITE engine/tracker.py "
                         "[<module>/def _requeue()]")


def control_02_save_argument_from_a_parameter():
    code, out = _run(lambda t: _replace_once(
        t, TRACKER, OPEN_TRADE_LOAD,
        OPEN_TRADE_LOAD.replace("load_pending()", "signal['pending']")))
    _rejected(out, code, "SAVE ARGUMENT NOT FROM A LOAD IN THE SAME FUNCTION "
                         "engine/tracker.py [<module>/def open_trade()]")
    assert "<- parameter signal" in out, out
    assert "UNREGISTERED SAVE SITE" not in out, out


def control_03_new_carrier_through_a_return_value():
    code, out = _run(lambda t: _replace_once(
        t, TRACKER, QUEUE_SIGNAL_LOAD,
        QUEUE_SIGNAL_LOAD.replace("load_queue()", "expire_queue()")))
    _rejected(out, code, "SAVE ARGUMENT NOT FROM A LOAD IN THE SAME FUNCTION "
                         "engine/tracker.py [<module>/def queue_signal()]")
    assert "<- return value of expire_queue()" in out, out


def control_04_try_around_the_carrier_owner_save():
    code, out = _run(lambda t: _replace_once(
        t, TRACKER, EXPIRE_SAVE,
        "        try:\n            save_queue(active)\n"
        "        except Exception:\n            pass\n"))
    _rejected(out, code, "CARRIER OWNER SAVE INSIDE TRY engine/tracker.py "
                         "[<module>/def expire_queue()]")


def control_05_with_around_the_carrier_owner_save():
    code, out = _run(lambda t: _replace_once(
        t, TRACKER, EXPIRE_SAVE,
        "        with _suppress(Exception):\n            save_queue(active)\n"))
    _rejected(out, code, "CARRIER OWNER SAVE INSIDE WITH engine/tracker.py")


def control_06_try_around_the_carrier_edge():
    code, out = _run(lambda t: _replace_once(
        t, TRACKER, CARRIER,
        '    _ADMISSION_EVAL["records"].append(mq)\n    try:\n'
        '        queue = expire_queue()\n    except Exception:\n'
        '        queue = []\n'))
    _rejected(out, code, "CARRIER EDGE INSIDE TRY engine/tracker.py "
                         "[<module>/def maintain_queue()]")


def control_07_registered_carrier_edge_missing():
    code, out = _run(lambda t: _replace_once(
        t, TRACKER, CARRIER, CARRIER.replace("expire_queue()", "load_queue()")))
    _rejected(out, code, "REGISTERED CARRIER EDGE FOUND 0x, EXPECTED 1x")


def control_08_trades_append_outside_execute_pending_signals():
    code, out = _run(lambda t: _replace_once(
        t, TRACKER, CHECK_OPEN_SAVE,
        "    if updated:\n        trades.append({})\n        save_trades(trades)"
        "\n\n    # Check queue"))
    _rejected(out, code, "UNREGISTERED TRADES LIST MUTATION engine/tracker.py "
                         "[<module>/def check_open_trades()]")


def control_09_trades_list_rebound_before_its_save():
    code, out = _run(lambda t: _replace_once(
        t, TRACKER, SHADOW_LOAD,
        SHADOW_LOAD + "    trades = [t for t in trades if t.get('symbol')]\n"))
    _rejected(out, code, "TRADES LIST REBOUND engine/tracker.py "
                         "[<module>/def check_shadow_trades()]")
    assert "SAVE ARGUMENT NOT FROM A LOAD" not in out, out


def control_10_trades_list_aliased():
    code, out = _run(lambda t: _replace_once(
        t, MONITOR, MONITOR_LOAD, MONITOR_LOAD + "    kept = trades\n"))
    _rejected(out, code, "TRADES LIST ESCAPES OR IS ALIASED monitor_trades.py "
                         "[<module>/def monitor()]")


def control_11_trades_list_passed_to_a_helper():
    code, out = _run(lambda t: _replace_once(
        t, MONITOR, MONITOR_LOAD, MONITOR_LOAD + "    _prune(trades)\n"))
    _rejected(out, code, "TRADES LIST ESCAPES OR IS ALIASED monitor_trades.py")


def control_12_trades_list_item_deleted():
    code, out = _run(lambda t: _replace_once(
        t, MONITOR, MONITOR_LOAD, MONITOR_LOAD + "    del trades[0]\n"))
    _rejected(out, code, "TRADES LIST ITEM STORE OR DEL monitor_trades.py")


def control_13_trades_list_augmented():
    code, out = _run(lambda t: _replace_once(
        t, MONITOR, MONITOR_LOAD, MONITOR_LOAD + "    trades += []\n"))
    _rejected(out, code, "TRADES LIST AUGMENTED monitor_trades.py")


def control_14_trades_list_captured_by_a_lambda():
    code, out = _run(lambda t: _replace_once(
        t, MONITOR, MONITOR_LOAD,
        MONITOR_LOAD + "    _drop = lambda: trades.pop()\n"))
    _rejected(out, code, "TRADES LIST CAPTURED BY A NESTED SCOPE "
                         "monitor_trades.py")


def control_15_save_function_body_changed():
    code, out = _run(lambda t: _replace_once(
        t, TRACKER, "    with open(QUEUE_FILE, 'w') as f:\n"
                    "        json.dump(queue, f, indent=2)",
        "    with open(QUEUE_FILE, 'w') as f:\n"
        "        json.dump(load_queue() + queue, f, indent=2)"))
    _rejected(out, code, "SAVE FUNCTION DIFFERS FROM REGISTERED SOURCE "
                         "engine/tracker.py save_queue")


def control_16_state_file_written_outside_its_save_function():
    code, out = _run(lambda t: _append(
        t, DASHBOARD,
        "\n\ndef _reset_trades():\n"
        "    with open(LOGS_DIR / 'virtual_trades.json', 'w') as fh:\n"
        "        fh.write('[]')\n"))
    _rejected(out, code, "STATE FILE OPENED FOR WRITING OUTSIDE save_trades()")


def control_17_new_file_writer():
    code, out = _run(lambda t: _append(
        t, "engine/screener.py",
        "\n\ndef _dump(rows):\n    with open('screen.txt', 'w') as fh:\n"
        "        fh.write(str(rows))\n"))
    _rejected(out, code, "UNREGISTERED FILE WRITER engine/screener.py")


def control_18_new_writer_through_an_aliased_os_module():
    code, out = _run(lambda t: _append(
        t, MONITOR, "\n\ndef _swap(a, b):\n    _os.replace(a, b)\n"))
    _rejected(out, code, "UNREGISTERED FILE WRITER monitor_trades.py "
                         "[<module>/def _swap()]")


def control_19_save_name_aliased():
    code, out = _run(lambda t: _append(t, TRACKER, "\n_sq = save_queue\n"))
    _rejected(out, code, "non-call reference: save_queue (Load)")


def control_20_renaming_import_of_a_load():
    code, out = _run(lambda t: _append(
        t, DASHBOARD, "\nfrom engine.tracker import load_queue as _lq\n"))
    _rejected(out, code, "renaming import: load_queue as _lq")


def control_21_load_redefined_in_another_file():
    code, out = _run(lambda t: _append(
        t, DASHBOARD, "\n\ndef load_trades():\n    return []\n"))
    _rejected(out, code, "DEFINITION OF load_trades NOT EXACTLY ONCE")


def control_22_save_of_a_non_empty_literal():
    code, out = _run(lambda t: _replace_once(
        t, TRACKER, "        save_ranked([])\n", "        save_ranked([{}])\n"))
    _rejected(out, code, "SAVE ARGUMENT NOT FROM A LOAD IN THE SAME FUNCTION "
                         "engine/tracker.py [<module>/def maintain_queue()]")
    assert "<- non-empty literal" in out, out


def control_23_save_from_the_wrong_load():
    code, out = _run(lambda t: _replace_once(
        t, TRACKER, EXPIRE_LOAD,
        EXPIRE_LOAD.replace("load_queue()", "load_pending()")))
    _rejected(out, code, "SAVE ARGUMENT NOT FROM A LOAD IN THE SAME FUNCTION "
                         "engine/tracker.py [<module>/def expire_queue()]")
    assert "<- load load_pending" in out, out


def control_24_fresh_list_filled_from_a_parameter():
    code, out = _run(lambda t: _replace_once(
        t, TRACKER, OPEN_TRADE_LOAD,
        OPEN_TRADE_LOAD.replace("load_pending()", "[]")))
    _rejected(out, code, "SAVE ARGUMENT NOT FROM A LOAD IN THE SAME FUNCTION "
                         "engine/tracker.py [<module>/def open_trade()]")
    assert "Dict expression" in out, out


def control_25_read_of_a_state_file_outside_tracker_passes():
    code, out = _run(lambda t: _append(
        t, DASHBOARD,
        "\n\ndef _peek():\n"
        "    with open(LOGS_DIR / 'virtual_trades.json') as fh:\n"
        "        return fh.read()\n"))
    assert code == 0, f"a read is not a writer, got {code}:\n{out}"


def control_26_non_production_file_is_outside_the_inventory():
    def mutate(t):
        os.makedirs(os.path.join(t, "tests"))
        _append(t, "tests/probe.py",
                "from engine.tracker import save_queue\nsave_queue([1])\n"
                "open('logs/virtual_trades.json', 'w')\n")
    code, out = _run(mutate)
    assert code == 0, f"tests/ is not production, got {code}:\n{out}"


def control_27_trades_list_read_uses_pass():
    code, out = _run(lambda t: _replace_once(
        t, MONITOR, MONITOR_LOAD,
        MONITOR_LOAD + "    _n = len(trades) if trades else sum(1 for _ in "
                       "trades)\n    print(f'{trades!r}' if not trades else "
                       "trades.count(None))\n"))
    assert code == 0, f"reads of the trades list must pass, got {code}:\n{out}"


def control_28_writer_imported_by_name():
    code, out = _run(lambda t: _append(
        t, DASHBOARD, "\nfrom shutil import copyfile as _cp\n\n\n"
                      "def _backup(a, b):\n    _cp(a, b)\n"))
    _rejected(out, code, "UNREGISTERED FILE WRITER build_dashboard.py "
                         "[<module>/def _backup()]")


CONTROLS = [v for k, v in sorted(globals().items()) if k.startswith("control_")]

MUTANTS = (
    ("save provenance",
     "            bad = sorted(tags - allowed)",
     "            bad = []",
     "control_02_save_argument_from_a_parameter"),
    ("fresh list appends",
     "            if n in self.fresh:",
     "            if False:",
     "control_24_fresh_list_filled_from_a_parameter"),
    ("load kind",
     '            allowed = {f"load {x}" for x in SAVE_LOADS[save]} | {"empty"}',
     '            allowed = {f"load {x}" for x in LOADS} | {"empty"}',
     "control_23_save_from_the_wrong_load"),
    ("try guard",
     "        if isinstance(cur, (ast.Try, ast.With, ast.AsyncWith)) or (",
     "        if isinstance(cur, (ast.With, ast.AsyncWith)) or (",
     "control_04_try_around_the_carrier_owner_save"),
    ("with guard",
     "        if isinstance(cur, (ast.Try, ast.With, ast.AsyncWith)) or (",
     "        if isinstance(cur, (ast.Try,)) or (",
     "control_05_with_around_the_carrier_owner_save"),
    ("carrier edge count",
     "        if n != 1:",
     "        if False:",
     "control_07_registered_carrier_edge_missing"),
    ("trades rebinding",
     "                if not (kind == \"value\" and isinstance(e, ast.Call)",
     "                if False and not (kind == \"value\" and isinstance(e, ast.Call)",
     "control_09_trades_list_rebound_before_its_save"),
    ("trades escape",
     "                problems.append(f\"TRADES LIST ESCAPES OR IS ALIASED {where}\\n\"",
     "                (lambda *a: None)(f\"TRADES LIST ESCAPES OR IS ALIASED {where}\\n\"",
     "control_11_trades_list_passed_to_a_helper"),
    ("nested capture",
     "        for node in _nested_nodes(fn):",
     "        for node in ():",
     "control_14_trades_list_captured_by_a_lambda"),
    ("save function source",
     "        if res[\"save_defs\"].get(name) != save_functions.get(name):",
     "        if False:",
     "control_15_save_function_body_changed"),
    ("state open policy",
     "            if state and kind == \"open\" and not (",
     "            if False and not (",
     "control_16_state_file_written_outside_its_save_function"),
    ("module alias resolution",
     "                self.modules[a.asname or a.name.split(\".\")[0]] = a.name",
     "                self.modules[a.name.split(\".\")[0]] = a.name",
     "control_18_new_writer_through_an_aliased_os_module"),
)


def _run_mutants():
    with open(REAL_VERIFIER, encoding="utf-8") as fh:
        source = fh.read()
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy(os.path.join(ROOT, "tools", "admission_writer_inventory.py"),
                    tmp)
        for i, (label, old, new, must_fail) in enumerate(MUTANTS, 1):
            name = f"mutant_{i:02d}_{label.replace(' ', '_')}"
            if source.count(old) != 1:
                results.append((name, False,
                                f"anchor appears {source.count(old)} times"))
                continue
            path = os.path.join(tmp, "verify_writer_inventory.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(source.replace(old, new))
            env = dict(os.environ, ARES_WRCTL_VERIFIER=path,
                       ARES_WRCTL_ONLY=must_fail)
            proc = subprocess.run([sys.executable, os.path.abspath(__file__)],
                                  capture_output=True, text=True, env=env)
            out = proc.stdout + proc.stderr
            ok = proc.returncode == 1 and f"FAIL  {must_fail}" in out
            results.append((name, ok, f"{must_fail} must fail; got exit "
                            f"{proc.returncode}\n{out[-400:]}"))
    return results


def main():
    only = os.environ.get("ARES_WRCTL_ONLY")
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
    print(f"\n  {total - len(failures)}/{total} writer inventory checks "
          f"passed ({len(controls)} controls"
          + ("" if only else f", {len(MUTANTS)} verifier mutants") + ")")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
