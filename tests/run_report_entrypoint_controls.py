#!/usr/bin/env python3
"""Containment controls for the daily_report.py entry point (admission capture v1).

    python3 tests/run_report_entrypoint_controls.py

The REAL daily_report.py is executed from a byte-identical copy in a scratch
directory, via runpy with __name__ == "__main__", with every engine dependency
stubbed in sys.modules. The admission observer is a stub whose flush can raise
on demand, so each control can place a failure inside the entry point's
finally while a report exception is, or is not, in flight.

Design: archive/DESIGN_admission_capture_v1.md §6.1 items 3 and 5. Gap G1 of
review_bundle_admission_impl_v1: flush() catches Exception only, so a
BaseException raised inside it must not replace the report's own exception.

The controls are also run against a mutant of the deployed file with the
finally guard removed, i.e. the entry point before the G1 fix: controls 71, 72
and 74 must FAIL there and the others pass, so they cannot pass vacuously.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEPLOYED = os.path.join(ROOT, "daily_report.py")
MARKER = "ReportMarkerError"


class ReportMarkerError(RuntimeError):
    """Unique production failure, so the propagated exception is identifiable."""


def _install_stubs(mode, work):
    def note(name, payload=""):
        with open(os.path.join(work, name), "a") as fh:
            fh.write(f"{payload}\n")

    def mod(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m
        return m

    engine = mod("engine")
    engine.__path__ = []

    def refresh_watchlist(symbols):
        if mode.startswith("raise"):
            raise ReportMarkerError("production failure inside generate_report")

    store = {}

    def _new_admission_store(token):
        return {"invocation_token": token, "scan_open_trade_expected": 0}

    mod("engine.data_feed", refresh_watchlist=refresh_watchlist,
        load_stock=lambda sym, **kw: None,
        disconnect_ib=lambda: note("disconnect.txt", "called"),
        prune_cache=lambda keep: None)
    mod("engine.indicators", add_indicators=lambda df: df)
    mod("engine.signals", scan_universe=lambda syms, data=None: [],
        load_watchlist=lambda: {"categories": {}}, get_all_symbols=lambda wl: [],
        load_strategy_params=lambda: {})
    mod("engine.screener", get_screened_symbols=lambda: ([], {}))
    mod("engine.tracker", open_trade=lambda s, **kw: "pending",
        print_scorecard=lambda *a, **kw: None, export_csv=lambda *a, **kw: None,
        check_shadow_trades=lambda *a, **kw: None,
        execute_pending_signals=lambda *a, **kw: None,
        maintain_queue=lambda *a, **kw: None,
        promote_queue=lambda *a, **kw: None, load_trades=lambda: [],
        load_queue=lambda: [], _ADMISSION_EVAL=store,
        _new_admission_store=_new_admission_store)
    mod("engine.parity_hook", observed_check_open_trades=lambda: None)
    for name in ("data_feed", "indicators", "signals", "screener", "tracker",
                 "parity_hook"):
        setattr(engine, name, sys.modules[f"engine.{name}"])
    if mode.endswith("observer_unimportable"):
        sys.modules["engine.admission_observer"] = None
        return

    def flush(st, decision_path, peer=None):
        note("flush_calls.txt", json.dumps(
            {"decision_path": decision_path, "peer": peer,
             "report_completed": st.get("report_completed"),
             "loop_exception": st.get("loop_exception")}))
        if mode.endswith("flush_keyboardinterrupt"):
            raise KeyboardInterrupt("raised inside admission flush")
        if mode.endswith("flush_systemexit"):
            raise SystemExit(7)
        return {"attempted": 0, "written": 0, "write_failures": 0,
                "heartbeat_written": True, "flush_error": None,
                "skipped_already_flushed": False}

    engine.admission_observer = mod("engine.admission_observer", flush=flush)


def drive(mode, work, copy):
    _install_stubs(mode, work)
    import runpy
    runpy.run_path(copy, run_name="__main__")


def run(mode, source):
    """Execute `source` (a daily_report.py) as __main__ in a scratch dir."""
    work = tempfile.mkdtemp(prefix="report_entry_")
    try:
        os.makedirs(os.path.join(work, "logs"))
        copy = os.path.join(work, "daily_report.py")
        shutil.copyfile(source, copy)
        with open(source, "rb") as a, open(copy, "rb") as b:
            assert hashlib.sha256(a.read()).digest() == \
                hashlib.sha256(b.read()).digest()
        p = subprocess.run([sys.executable, os.path.abspath(__file__),
                            "--drive", mode, work, copy],
                           capture_output=True, text=True, cwd=work,
                           timeout=120)

        def read(rel):
            path = os.path.join(work, rel)
            return open(path).read() if os.path.exists(path) else ""

        tail = [l for l in p.stderr.strip().splitlines() if l.strip()]
        return {"rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr,
                "last_err": tail[-1] if tail else "",
                "flush_calls": [json.loads(l) for l in
                                read("flush_calls.txt").splitlines() if l],
                "disconnect": read("disconnect.txt").count("called"),
                "health": [json.loads(l) for l in read(
                    "logs/admission_health_v1.jsonl").splitlines() if l]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


CONTROLS = []


def control(fn):
    CONTROLS.append(fn)
    return fn


def _check(cond, label, fails):
    if not cond:
        fails.append(label)


@control
def control_70_raising_report_propagates_its_exception(src):
    """Baseline: a report exception reaches the process exit; flush runs once
    with the exception recorded; disconnect_ib is skipped, as before capture."""
    r, f = run("raise", src), []
    _check(r["rc"] == 1, f"exit {r['rc']} != 1", f)
    _check(MARKER in r["last_err"], f"last stderr line: {r['last_err']!r}", f)
    _check(len(r["flush_calls"]) == 1, f"flush calls {len(r['flush_calls'])}", f)
    _check(bool(r["flush_calls"]) and MARKER in str(
        r["flush_calls"][0]["loop_exception"]), "loop_exception not recorded", f)
    _check(r["disconnect"] == 0, "disconnect_ib ran after a raising report", f)
    return f


@control
def control_71_keyboardinterrupt_in_flush_cannot_replace_report_exception(src):
    """G1: KeyboardInterrupt raised inside flush while the report exception is
    in flight. The process must still end on the report's own exception."""
    r, f = run("raise_flush_keyboardinterrupt", src), []
    _check(r["rc"] == 1, f"exit {r['rc']} != 1", f)
    _check(MARKER in r["last_err"], f"last stderr line: {r['last_err']!r}", f)
    _check(len(r["flush_calls"]) == 1, f"flush calls {len(r['flush_calls'])}", f)
    _check(r["disconnect"] == 0, "disconnect_ib ran after a raising report", f)
    return f


@control
def control_72_systemexit_in_flush_cannot_replace_report_exception(src):
    """G1: SystemExit(7) raised inside flush while the report exception is in
    flight. Exit status 7 would hide the failure from the cron wrapper."""
    r, f = run("raise_flush_systemexit", src), []
    _check(r["rc"] == 1, f"exit {r['rc']} != 1", f)
    _check(MARKER in r["last_err"], f"last stderr line: {r['last_err']!r}", f)
    return f


@control
def control_73_completed_report_flushes_and_disconnects(src):
    """Normal run: exit 0, one flush with decision_path report and no peer,
    report_completed true, disconnect_ib once, no health line."""
    r, f = run("ok", src), []
    _check(r["rc"] == 0, f"exit {r['rc']} != 0: {r['last_err']!r}", f)
    _check(len(r["flush_calls"]) == 1, f"flush calls {len(r['flush_calls'])}", f)
    if r["flush_calls"]:
        c = r["flush_calls"][0]
        _check(c["decision_path"] == "report" and c["peer"] is None,
               f"flush args {c}", f)
        _check(c["report_completed"] is True, "report_completed not set", f)
    _check(r["disconnect"] == 1, f"disconnect_ib ran {r['disconnect']}x", f)
    _check(r["health"] == [], f"unexpected health lines {r['health']}", f)
    return f


@control
def control_74_raising_flush_does_not_stop_a_completed_report(src):
    """KeyboardInterrupt inside flush after a completed report: contained, so
    disconnect_ib still runs and the exit status is 0, as without capture."""
    r, f = run("ok_flush_keyboardinterrupt", src), []
    _check(r["rc"] == 0, f"exit {r['rc']} != 0: {r['last_err']!r}", f)
    _check(r["disconnect"] == 1, f"disconnect_ib ran {r['disconnect']}x", f)
    return f


@control
def control_75_unimportable_observer_reports_health_and_trades_on(src):
    """engine.admission_observer unimportable: the report completes, exit 0,
    disconnect_ib runs, one recorder_import_failed health line, on stderr only."""
    r, f = run("ok_observer_unimportable", src), []
    _check(r["rc"] == 0, f"exit {r['rc']} != 0: {r['last_err']!r}", f)
    _check(r["disconnect"] == 1, f"disconnect_ib ran {r['disconnect']}x", f)
    _check([h.get("reason") for h in r["health"]] == ["recorder_import_failed"],
           f"health {r['health']}", f)
    _check("ADMISSION-HEALTH" in r["stderr"]
           and "ADMISSION-HEALTH" not in r["stdout"], "health not on stderr only", f)
    return f


def _unguarded_source(tmp):
    """Mutation of the deployed file: the guard around the finally body is
    removed (the `try/except BaseException: pass` is replaced by its own body),
    which is the entry point as it was before the G1 fix. Built from the
    deployed bytes, so it needs no git history."""
    import ast
    tree = ast.parse(open(DEPLOYED).read())
    mains = [n for n in tree.body if isinstance(n, ast.If)
             and ast.unparse(n.test) == "__name__ == '__main__'"]
    outer = [n for n in mains[0].body if isinstance(n, ast.Try)][0]
    guard = outer.finalbody
    if not (len(guard) == 1 and isinstance(guard[0], ast.Try)
            and len(guard[0].handlers) == 1
            and ast.unparse(guard[0].handlers[0].type) == "BaseException"):
        raise ValueError("finally body is not a single try/except BaseException")
    outer.finalbody = guard[0].body
    path = os.path.join(tmp, "daily_report_unguarded.py")
    with open(path, "w") as fh:
        fh.write(ast.unparse(tree) + "\n")
    return path


def main():
    failed = 0
    for fn in CONTROLS:
        fails = fn(DEPLOYED)
        failed += bool(fails)
        print(f"  {'PASS' if not fails else 'FAIL'}  {fn.__name__}")
        for x in fails:
            print(f"        {x}")
    print("\n  anti-vacuity: the same controls on a mutant with the finally "
          "guard removed (the entry point before the G1 fix)")
    tmp = tempfile.mkdtemp(prefix="report_mutant_")
    try:
        try:
            pre = _unguarded_source(tmp)
        except Exception as e:                       # noqa: BLE001
            pre = None
            print(f"  FAIL  mutant not built: {e}")
            failed += 1
        if pre:
            must_fail = {"control_71_keyboardinterrupt_in_flush_cannot_replace_report_exception",
                         "control_72_systemexit_in_flush_cannot_replace_report_exception",
                         "control_74_raising_flush_does_not_stop_a_completed_report"}
            for fn in CONTROLS:
                fails = fn(pre)
                want_fail = fn.__name__ in must_fail
                good = bool(fails) == want_fail
                failed += not good
                print(f"  {'PASS' if good else 'FAIL'}  {fn.__name__} "
                      f"{'fails' if fails else 'passes'} on mutant "
                      f"(expected {'fail' if want_fail else 'pass'})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    total = len(CONTROLS) * 2
    print(f"\n  {total - failed}/{total} report entrypoint checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--drive":
        drive(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        sys.exit(main())
