#!/usr/bin/env python3
"""Negative controls for the call-site inventory (design §6.2, extension 2).

    python3 tests/run_call_inventory_controls.py

Each control copies the REAL production files (ROOT/*.py, ROOT/engine/*.py)
into a temporary tree, applies one mutation, and runs
tools/verify_call_inventory.py on that tree. Control 00 proves the unmutated
tree passes; every other control must be rejected with the named problem.
Control 12 marks the boundary: a call outside production files is not part of
the inventory.

ARES_INVCTL_VERIFIER replaces the verifier path, so a verifier that cannot
fail can be shown to fail this suite.
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
    _rejected(out, code, "getattr by name")


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


CONTROLS = [v for k, v in sorted(globals().items()) if k.startswith("control_")]


def main():
    failures = []
    for fn in CONTROLS:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as exc:
            failures.append(fn.__name__)
            print(f"  FAIL  {fn.__name__}\n        {exc}")
        except Exception as exc:                      # noqa: BLE001
            failures.append(fn.__name__)
            print(f"  ERROR {fn.__name__}\n        {type(exc).__name__}: {exc}")
    print(f"\n  {len(CONTROLS) - len(failures)}/{len(CONTROLS)} call-site "
          f"inventory controls passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
