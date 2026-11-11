#!/usr/bin/env python3
"""Negative controls for the call-site inventory (design §6.2, extension 2).

    python3 tests/run_call_inventory_controls.py

Each control copies the REAL production files (ROOT/*.py, ROOT/engine/*.py)
into a temporary tree, applies one mutation, and runs
tools/verify_call_inventory.py on that tree. Control 00 proves the unmutated
tree passes; every other control must be rejected with the named problem.
Controls 12 and 27 mark boundaries: a call outside production files, and a
plain attribute read on the tracker module, are not part of the inventory.

Controls 13 to 26 cover indirect routes (M3 review round 1, finding 1): a
namespace lookup, a name built at run time, reflective builtins and modules,
sys.modules, subprocess and os.system, and changes to a registered dynamic
site. Each MUTANT disables one rule in a copy of the verifier and names the
control that must then fail.

ARES_INVCTL_VERIFIER replaces the verifier path, so a verifier that cannot
fail can be shown to fail this suite. ARES_INVCTL_ONLY runs one control.
"""

import glob
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VERIFIER = (os.environ.get("ARES_INVCTL_VERIFIER")
            or os.path.join(ROOT, "tools", "verify_call_inventory.py"))


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


def _append(tmp, rel, text):
    with open(os.path.join(tmp, rel), "a", encoding="utf-8") as fh:
        fh.write(text)


def _rejected(out, code, problem):
    assert code == 1, f"expected exit 1, got {code}:\n{out}"
    assert problem in out, f"expected {problem!r}:\n{out}"


def control_00_real_tree_passes():
    code, out = _run()
    assert code == 0, f"the real tree must pass, got {code}:\n{out}"
    assert "call sites : 9 found, 9 registered" in out, out
    assert "dynamic    : 11 found, 11 registered" in out, out


def control_01_new_caller_in_another_production_file():
    code, out = _run(lambda t: _append(
        t, "build_dashboard.py",
        "\n\ndef _extra(signal):\n    return open_trade(signal)\n"))
    _rejected(out, code, "UNREGISTERED CALL SITE build_dashboard.py")


def control_02_monitor_promotion_argument_changed():
    code, out = _run(lambda t: _replace_once(
        t, "monitor_trades.py",
        'promote_queue(source="monitor", use_live=True)',
        'promote_queue(source="monitor", use_live=False)'))
    _rejected(out, code, "UNREGISTERED CALL SITE monitor_trades.py")
    assert "REGISTERED CALL SITE MISSING monitor_trades.py" in out, out


def control_03_r1_admission_gains_explicit_from_queue():
    code, out = _run(lambda t: _replace_once(
        t, "daily_report.py", "result = open_trade(s)",
        "result = open_trade(s, from_queue=True)"))
    _rejected(out, code, "UNREGISTERED CALL SITE daily_report.py")


def control_04_registered_caller_removed():
    code, out = _run(lambda t: _replace_once(
        t, "daily_report.py", "    execute_pending_signals()\n", "    pass\n"))
    _rejected(out, code, "REGISTERED CALL SITE MISSING daily_report.py")


def control_05_registered_call_duplicated_in_its_scope():
    code, out = _run(lambda t: _replace_once(
        t, "daily_report.py", '    promote_queue(source="scan")\n',
        '    promote_queue(source="scan")\n    promote_queue(source="scan")\n'))
    _rejected(out, code, "OCCURRENCE COUNT MISMATCH daily_report.py")


def control_06_registered_text_in_a_new_scope():
    code, out = _run(lambda t: _append(
        t, "engine/tracker.py",
        "\n\ndef _second_promotion(signal):\n"
        "    return open_trade(signal, from_queue=True)\n"))
    _rejected(out, code, "UNREGISTERED CALL SITE engine/tracker.py")


def control_07_alias_assignment():
    code, out = _run(lambda t: _append(
        t, "engine/tracker.py", "\n_ot = open_trade\n"))
    _rejected(out, code, "non-call reference: open_trade (Load)")


def control_08_getattr_by_name():
    code, out = _run(lambda t: _append(
        t, "engine/screener.py",
        "\n\ndef _via(module):\n    return getattr(module, 'open_trade')\n"))
    _rejected(out, code, "target name as a string constant: 'open_trade'")


def control_09_renaming_import():
    code, out = _run(lambda t: _append(
        t, "build_dashboard.py",
        "\nfrom engine.tracker import queue_signal as _qs\n"))
    _rejected(out, code, "renaming import: queue_signal as _qs")


def control_10_new_production_file():
    code, out = _run(lambda t: _append(
        t, "engine/extra.py",
        "from engine import tracker\n\n\ndef run():\n"
        "    tracker.expire_queue()\n"))
    _rejected(out, code, "UNREGISTERED CALL SITE engine/extra.py")


def control_11_call_through_module_attribute():
    code, out = _run(lambda t: _replace_once(
        t, "daily_report.py", "result = open_trade(s)",
        "result = tracker.open_trade(s)"))
    _rejected(out, code, "UNREGISTERED CALL SITE daily_report.py")


def control_12_non_production_file_is_outside_the_inventory():
    def mutate(t):
        os.makedirs(os.path.join(t, "tests"))
        _append(t, "tests/probe.py",
                "from engine.tracker import open_trade\nopen_trade({})\n")
    code, out = _run(mutate)
    assert code == 0, f"tests/ is not production, got {code}:\n{out}"


def control_13_review_probe_namespace_lookup_in_a_new_file():
    code, out = _run(lambda t: _append(
        t, "engine/probe_dict.py",
        "from engine import tracker\n"
        "_f = tracker.__dict__[\"open_trade\"]\n_f({})\n"))
    _rejected(out, code, "UNREGISTERED DYNAMIC ACCESS engine/probe_dict.py")
    _rejected(out, code, "target name as a string constant: 'open_trade'")


def control_14_name_built_at_run_time():
    code, out = _run(lambda t: _append(
        t, "build_dashboard.py",
        "\nfrom engine import tracker as _t\n\n\ndef _late(sig):\n"
        "    return _t.__dict__['open_' + 'trade'](sig)\n"))
    _rejected(out, code, "UNREGISTERED DYNAMIC ACCESS build_dashboard.py")
    assert "string constant" not in out, out


def control_15_vars_lookup():
    code, out = _run(lambda t: _append(
        t, "engine/screener.py",
        "\n\ndef _v(m, n):\n    return vars(m)[n]\n"))
    _rejected(out, code, "UNREGISTERED DYNAMIC ACCESS engine/screener.py")


def control_16_registered_reflective_import_new_member():
    code, out = _run(lambda t: _append(
        t, "engine/parity_compare.py",
        "\n\ndef _members(m, n):\n"
        "    return dict(inspect.getmembers(m))[n]\n"))
    _rejected(out, code, "inspect.getmembers(m)")


def control_17_sys_modules():
    code, out = _run(lambda t: _append(
        t, "monitor_trades.py",
        "\n\ndef _mod():\n    return _sys.modules['engine.tracker']\n"))
    _rejected(out, code, "_sys.modules")


def control_18_reflective_from_import():
    code, out = _run(lambda t: _append(
        t, "daily_report.py",
        "\nfrom operator import methodcaller as _mc\n"))
    _rejected(out, code, "from operator import methodcaller as _mc")


def control_19_importlib():
    code, out = _run(lambda t: _append(
        t, "build_dashboard.py",
        "\nimport importlib as _il\n\n\ndef _load():\n"
        "    return _il.import_module('engine.tracker')\n"))
    _rejected(out, code, "import importlib as _il")


def control_20_os_system_through_an_existing_os_import():
    code, out = _run(lambda t: _append(
        t, "monitor_trades.py",
        "\n\ndef _shell():\n    _os.system('python3 -c pass')\n"))
    _rejected(out, code, "_os.system('python3 -c pass')")


def control_21_new_subprocess_call_with_registered_import():
    code, out = _run(lambda t: _append(
        t, "engine/admission_observer.py",
        "\n\ndef _spawn():\n    subprocess.run(['python3', '-c', 'pass'])\n"))
    _rejected(out, code, "subprocess.run(['python3', '-c', 'pass'])")


def control_22_registered_dynamic_site_changed():
    code, out = _run(lambda t: _replace_once(
        t, "engine/parity_hook.py",
        'store = getattr(tracker, "_LAST_EVAL", None) or {}',
        'store = getattr(tracker, "_LAST_EVAL", {}) or {}'))
    _rejected(out, code, "UNREGISTERED DYNAMIC ACCESS engine/parity_hook.py")
    _rejected(out, code, "REGISTERED DYNAMIC ACCESS MISSING")


def control_23_target_name_as_a_string_constant():
    code, out = _run(lambda t: _append(
        t, "build_dashboard.py", "\n_NAMES = ('expire_queue',)\n"))
    _rejected(out, code, "target name as a string constant: 'expire_queue'")


def control_24_pandas_query_resolves_caller_names():
    code, out = _run(lambda t: _append(
        t, "engine/screener.py",
        "\n\ndef _q(df):\n    return df.query('@x > 1')\n"))
    _rejected(out, code, "df.query('@x > 1')")


def control_25_star_import():
    code, out = _run(lambda t: _append(
        t, "build_dashboard.py", "\nfrom engine.tracker import *\n"))
    _rejected(out, code, "from engine.tracker import *")


def control_26_registered_dynamic_site_duplicated():
    code, out = _run(lambda t: _replace_once(
        t, "engine/parity_compare.py",
        "src = inspect.getsource(tracker_module)",
        "src = inspect.getsource(tracker_module)\n"
        "        inspect.getsource(tracker_module)"))
    _rejected(out, code, "OCCURRENCE COUNT MISMATCH engine/parity_compare.py")


def control_27_plain_module_attribute_read_is_outside():
    code, out = _run(lambda t: _append(
        t, "build_dashboard.py",
        "\nfrom engine import tracker as _t2\n"
        "_LIMIT = _t2.MAX_FILL_ATTEMPTS\n"))
    assert code == 0, f"a plain attribute read is not dynamic, got {code}:\n{out}"


CONTROLS = [v for k, v in sorted(globals().items()) if k.startswith("control_")]

MUTANTS = (
    ("string constant rule",
     "        if isinstance(node.value, str) and node.value in TARGETS:",
     "        if False:",
     "control_23_target_name_as_a_string_constant"),
    ("dynamic table comparison",
     '                + _compare(dynamic, dyn_inventory, "DYNAMIC ACCESS"))',
     "                + [])",
     "control_14_name_built_at_run_time"),
    ("reflective attributes",
     "            if f.attr in REFLECTIVE_ATTRS:",
     "            if False:",
     "control_17_sys_modules"),
    ("reflective module members",
     "                if f.value.id in self.reflective_aliases:",
     "                if False:",
     "control_16_registered_reflective_import_new_member"),
    ("os process calls",
     "                if f.value.id in self.os_aliases and _os_process(f.attr):",
     "                if False:",
     "control_20_os_system_through_an_existing_os_import"),
    ("reflective builtins",
     "REFLECTIVE_BUILTINS = frozenset((",
     "REFLECTIVE_BUILTINS = frozenset(() and (",
     "control_15_vars_lookup"),
    ("dynamic occurrence count",
     "        elif want != n:",
     "        elif False:",
     "control_26_registered_dynamic_site_duplicated"),
)


def _run_mutants():
    with open(VERIFIER, encoding="utf-8") as fh:
        source = fh.read()
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy(os.path.join(ROOT, "tools", "admission_call_inventory.py"),
                    tmp)
        for i, (label, old, new, must_fail) in enumerate(MUTANTS, 1):
            name = f"mutant_{i:02d}_{label.replace(' ', '_')}"
            if source.count(old) != 1:
                results.append((name, False,
                                f"anchor appears {source.count(old)} times"))
                continue
            path = os.path.join(tmp, "verify_call_inventory.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(source.replace(old, new))
            env = dict(os.environ, ARES_INVCTL_VERIFIER=path,
                       ARES_INVCTL_ONLY=must_fail)
            proc = subprocess.run([sys.executable, os.path.abspath(__file__)],
                                  capture_output=True, text=True, env=env)
            out = proc.stdout + proc.stderr
            ok = proc.returncode == 1 and f"FAIL  {must_fail}" in out
            results.append((name, ok, f"{must_fail} must fail; got exit "
                            f"{proc.returncode}\n{out[-400:]}"))
    return results


def main():
    only = os.environ.get("ARES_INVCTL_ONLY")
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
    print(f"\n  {total - len(failures)}/{total} call-site inventory checks "
          f"passed ({len(controls)} controls"
          + ("" if only else f", {len(MUTANTS)} verifier mutants") + ")")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
