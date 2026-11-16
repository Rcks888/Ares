#!/usr/bin/env python3
"""End-to-end run of the real daily_report.py entry point (admission capture
v1, M2; design §7 *Report entrypoint*, §10 step 3).

    python3 tests/run_report_e2e_smoke.py

Two variants, each in a fresh subprocess and its own scratch copy of the
repository code (engine/, config/, the entry points; no logs, no .git):

    A  tests/fixtures/daily_report_preadmission.py + tracker_preadmission.py
       (production before capture; engine/admission_observer.py removed)
    B  daily_report.py + engine/tracker.py + engine/admission_observer.py

Each copy's daily_report.py is executed as __main__ via runpy. Only the
network-facing modules are stubbed: engine.data_feed (deterministic bars,
no live prices), engine.screener and engine.signals (fixed candidates and
signals). engine.tracker, engine.sample, engine.parity_hook and, when
pandas_ta imports, engine.indicators are the real copies. Both variants run
from the same seeded logs/ state.

Required: identical operator stdout and exit status; identical data-feed
call sequence; every file A writes is written identically by B; B writes
exactly the admission record and heartbeat files besides, with one report
heartbeat, records carrying its cycle_id, no contract violation outside the
expected ones, no admission health line.

The fill window is the real, clock-dependent `_fill_window_ok`; both
variants run seconds apart and see the same verdict, which is printed.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIX = os.path.join(HERE, "fixtures")
ADM_FILES = {"admission_v1.jsonl", "admission_heartbeat_v1.jsonl"}
STAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?"
                   r"(\+00:00)?")
FEED_CALLS = []


# --------------------------------------------------------------------------
# Driver (subprocess)
# --------------------------------------------------------------------------

def _last_weekday(d):
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _frame(symbol, last_day):
    import pandas as pd
    days = pd.bdate_range(end=last_day, periods=80)
    base = 20.0 + (sum(map(ord, symbol)) % 40)
    close = [round(base * (1 + 0.002 * i), 4) for i in range(80)]
    return pd.DataFrame({
        "Open": [round(c * 0.995, 4) for c in close],
        "High": [round(c * 1.01, 4) for c in close],
        "Low": [round(c * 0.99, 4) for c in close], "Close": close,
        "Volume": [1_000_000 + 1000 * i for i in range(80)]}, index=days)


def _sig(symbol, d, price, conf=3, strategy="mean_reversion"):
    return {"symbol": symbol, "date": d, "price": price, "strategy": strategy,
            "trigger": "t", "regime": "r", "category": "c",
            "confluence": conf, "strength": "strong", "stdev_20": 0.02,
            "rsi": 40.0, "vol_ratio": 1.2, "screens": ["s1"]}


def _install_stubs(src):
    today = _last_weekday(datetime.now(timezone.utc).date())
    iso = today.isoformat()

    def mod(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m
        return m

    def load_stock(symbol, max_age_hours=None):
        FEED_CALLS.append(["load_stock", symbol, max_age_hours])
        return None if symbol == "NOD" else _frame(symbol, today)

    def get_live_price(symbol):
        FEED_CALLS.append(["get_live_price", symbol])
        return None

    mod("engine.data_feed", load_stock=load_stock,
        get_live_price=get_live_price,
        refresh_watchlist=lambda syms: FEED_CALLS.append(
            ["refresh_watchlist", sorted(syms)]),
        prune_cache=lambda keep: FEED_CALLS.append(
            ["prune_cache", sorted(keep)]),
        disconnect_ib=lambda: FEED_CALLS.append(["disconnect_ib"]))
    universe = ["AAA", "KKK", "MMM", "GGG"]
    mod("engine.screener", get_screened_symbols=lambda: (
        list(universe), {s: ["s1"] for s in universe}))

    def load_strategy_params():
        with open(os.path.join(src, "config", "strategy_params.json")) as fh:
            return json.load(fh)

    def scan_universe(symbols, screen_data=None):
        px = lambda s: round(float(_frame(s, today)["Close"].iloc[-1]), 2)
        return [_sig("AAA", iso, px("AAA")), _sig("KKK", iso, px("KKK")),
                _sig("MMM", iso, px("MMM"), conf=1,
                     strategy="momentum_breakout")]

    mod("engine.signals", scan_universe=scan_universe,
        load_watchlist=lambda: {"categories": {}},
        get_all_symbols=lambda wl: [],
        load_strategy_params=load_strategy_params)
    try:
        import pandas_ta  # noqa: F401
        mode = "real"
    except Exception:                                     # noqa: BLE001
        mod("engine.indicators", add_indicators=lambda df: df)
        mode = "stubbed"
    return mode, today


def _seed(logs, today):
    iso = today.isoformat()
    old = (today - timedelta(days=12)).isoformat()
    price = lambda s: round(float(_frame(s, today)["Close"].iloc[-1]), 4)

    def w(name, obj):
        with open(os.path.join(logs, name), "w") as fh:
            json.dump(obj, fh, indent=2)

    trades = []
    for s in ("AAA", "BBB"):
        p = price(s)
        trades.append({"symbol": s, "status": "open", "entry_date": old,
                       "entry_price": p, "shares": 3.0, "position_size": 3 * p,
                       "stop_loss": round(p * 0.9, 2),
                       "take_profit": round(p * 1.1, 2),
                       "trailing_stop": round(p * 0.9, 2), "peak_price": p,
                       "strategy": "mean_reversion", "confluence": 3})
    trades.append({"symbol": "ZZZ", "status": "closed", "entry_date": old,
                   "exit_date": old, "entry_price": 10.0, "exit_price": 9.0,
                   "shares": 3.0, "pnl": -3.0, "pnl_pct": -10.0,
                   "exit_reason": "stop_loss", "strategy": "mean_reversion"})
    w("virtual_trades.json", trades)
    pend = _sig("CCC", iso, price("CCC"))
    w("pending_signals.json", [pend, dict(_sig("NOD", iso, 50.0),
                                          fill_attempts=2)])
    w("signal_queue.json", [{
        "symbol": "GGG", "strategy": "mean_reversion",
        "price_at_signal": price("GGG"), "confluence": 3, "date_added": iso,
        "rsi_at_signal": 40.0, "stdev_20": 0.02}])


def drive(src):
    os.environ["ARES_SCHEDULED"] = "1"
    os.environ.pop("ARES_PARITY", None)
    sys.path.insert(0, src)
    mode, today = _install_stubs(src)
    _seed(os.path.join(src, "logs"), today)
    import runpy
    try:
        runpy.run_path(os.path.join(src, "daily_report.py"),
                       run_name="__main__")
    finally:
        from engine import tracker
        ok, why = tracker._fill_window_ok(tracker._load_params())
        sys.stderr.write("FILLWINDOW " + json.dumps([ok, why]) + "\n")
        sys.stderr.write("INDICATORS " + mode + "\n")
        sys.stderr.write("FEEDCALLS " + json.dumps(FEED_CALLS) + "\n")


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------

def _prepare(variant, tmp):
    src = os.path.join(tmp, variant)
    shutil.copytree(os.path.join(ROOT, "engine"), os.path.join(src, "engine"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(os.path.join(ROOT, "config"), os.path.join(src, "config"))
    for f in ("daily_report.py", "monitor_trades.py"):
        shutil.copyfile(os.path.join(ROOT, f), os.path.join(src, f))
    os.makedirs(os.path.join(src, "logs"))
    if variant == "A":
        shutil.copyfile(os.path.join(FIX, "daily_report_preadmission.py"),
                        os.path.join(src, "daily_report.py"))
        shutil.copyfile(os.path.join(FIX, "tracker_preadmission.py"),
                        os.path.join(src, "engine", "tracker.py"))
        os.remove(os.path.join(src, "engine", "admission_observer.py"))
    return src


def _run(variant, tmp):
    src = _prepare(variant, tmp)
    p = subprocess.run([sys.executable, os.path.abspath(__file__), "--drive",
                        src], capture_output=True, text=True, cwd=src,
                       timeout=300)
    files = {}
    logs = os.path.join(src, "logs")
    for name in sorted(os.listdir(logs)):
        with open(os.path.join(logs, name), encoding="utf-8") as fh:
            files[name] = fh.read()
    tagged = {}
    for line in p.stderr.splitlines():
        for tag in ("FILLWINDOW", "INDICATORS", "FEEDCALLS"):
            if line.startswith(tag + " "):
                raw = line.split(" ", 1)[1]
                tagged[tag] = raw if tag == "INDICATORS" else json.loads(raw)
    return p, files, tagged


def main():
    fails = []

    def check(label, cond, detail=""):
        print(f"  {'PASS' if cond else 'FAIL'}  {label}"
              + (f"\n        {detail}" if (detail and not cond) else ""))
        if not cond:
            fails.append(label)

    mask = lambda t: STAMP.sub("<ts>", t)
    with tempfile.TemporaryDirectory() as tmp:
        pa, fa, ta = _run("A", tmp)
        pb, fb, tb = _run("B", tmp)
        print(f"  indicators: {tb.get('INDICATORS')}; fill window: "
              f"{tb.get('FILLWINDOW')}")
        check("A exit 0", pa.returncode == 0, pa.stderr[-2500:])
        check("B exit 0", pb.returncode == 0, pb.stderr[-2500:])
        check("fill-window verdict identical", ta.get("FILLWINDOW")
              == tb.get("FILLWINDOW"), f"{ta.get('FILLWINDOW')} vs "
              f"{tb.get('FILLWINDOW')}")
        check("operator stdout identical", mask(pa.stdout) == mask(pb.stdout),
              "\n".join(__import__("difflib").unified_diff(
                  mask(pa.stdout).splitlines(), mask(pb.stdout).splitlines(),
                  lineterm=""))[:3000])
        check("no ADMISSION-HEALTH line", "ADMISSION-HEALTH" not in pb.stderr,
              pb.stderr[-1500:])
        check("data-feed calls identical", ta.get("FEEDCALLS") is not None
              and ta.get("FEEDCALLS") == tb.get("FEEDCALLS"),
              f"A {len(ta.get('FEEDCALLS') or [])} B "
              f"{len(tb.get('FEEDCALLS') or [])}")
        check("B writes A's files plus exactly the admission files",
              set(fb) - set(fa) == ADM_FILES and set(fa) <= set(fb),
              f"A {sorted(fa)}; B {sorted(fb)}")
        for name in sorted(fa):
            check(f"file identical: {name}",
                  mask(fa[name]) == mask(fb.get(name, "")))
        print(f"        files compared: {sorted(fa)}")
        beats = [json.loads(l) for l in fb.get(
            "admission_heartbeat_v1.jsonl", "").splitlines() if l]
        recs = [json.loads(l) for l in fb.get(
            "admission_v1.jsonl", "").splitlines() if l]
        check("one report heartbeat, report_completed",
              len(beats) == 1 and beats[0].get("decision_path") == "report"
              and beats[0].get("report_completed") is True,
              json.dumps(beats)[:1500])
        if beats:
            b = beats[0]
            print(f"        call sites: {json.dumps(b.get('call_sites'), sort_keys=True)}")
            check("no call site FAIL", all(
                v.get("verdict") != "FAIL" for v in b["call_sites"].values()),
                b.get("call_sites"))
            check("no peer on the report path",
                  b.get("peer_status") == "not_applicable"
                  and b.get("peer_cycle_started_at") is None)
            check("records carry the heartbeat's cycle_id",
                  recs and all(r.get("cycle_id") == b.get("cycle_id")
                               for r in recs))
        kinds = {}
        for r in recs:
            kinds[r.get("kind")] = kinds.get(r.get("kind"), 0) + 1
        print(f"        record kinds: {dict(sorted(kinds.items()))}")
        check("fill, admission and promotion records present",
              {"fill", "admission", "promotion_pass"} <= set(kinds), kinds)
        viol = sorted({v for r in recs for v in r.get("contract_violations", [])})
        check("no contract violation", viol == [], viol)
        bad = [(r.get("kind"), r.get("seq"), r.get("missing_fields"))
               for r in recs if r.get("missing_fields") != []
               or r.get("record_complete") is not True]
        check("every record complete, no missing field", recs and not bad, bad)
        if beats:
            check("heartbeat records_incomplete 0",
                  beats[0].get("records_incomplete") == 0,
                  beats[0].get("records_incomplete"))
    print(f"\n  {'ALL PASS' if not fails else f'{len(fails)} FAILED: {fails}'}")
    return 1 if fails else 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--drive":
        drive(sys.argv[2])
    else:
        sys.exit(main())
