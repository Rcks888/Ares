#!/usr/bin/env python3
"""Admission capture v1 isolation smoke run (design §10 step 2, M1).

    python3 tests/run_admission_isolation_smoke.py

Drives the SAME scripted tracker sequence twice, each in a fresh subprocess
and a fresh scratch logs directory:

    A  tests/fixtures/tracker_preadmission.py   (production before capture)
    B  engine/tracker.py                        (production with capture)

and requires the operator stdout and every state file the sequence writes to
be identical between A and B, after masking wall-clock second stamps. B's
store is then flushed through engine/admission_observer.py into a SEPARATE
directory, and the records are checked for kinds and contract violations.

The sequence exercises: every fill outcome except the exception ones,
queue expiry, maintenance (kept / dropped / transient retry), a scan
promotion with no free slots, scan admissions (duplicate_open, queued with
eviction), a monitor promotion with free slots, and pending / duplicate_pending
admissions.

Market data is a deterministic stub; no network, no gateway, no production
state is read or written. This is evidence of identical behaviour on this
sequence only. The exhaustive argument is the isolation verifier.
"""

import json
import math
import os
import re
import subprocess
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VARIANTS = {
    "A": os.path.join(HERE, "fixtures", "tracker_preadmission.py"),
    "B": os.path.join(ROOT, "engine", "tracker.py"),
}
STATE_FILES = ("virtual_trades.json", "pending_signals.json",
               "signal_queue.json", "queue_ranked.json", "queue_events.jsonl")
STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


# --------------------------------------------------------------------------
# Driver (runs inside the subprocess)
# --------------------------------------------------------------------------

def _frame(symbol, last_day):
    import pandas as pd
    days = pd.bdate_range(end=pd.Timestamp(last_day), periods=80)
    seed = sum(ord(c) for c in symbol)
    close = [50 + 0.08 * i + 1.2 * math.sin(i / 3 + seed) for i in range(80)]
    if symbol == "JJJ":
        close[-1] *= 1.15
    return pd.DataFrame({
        "Open": [c * 0.995 for c in close], "High": [c * 1.01 for c in close],
        "Low": [c * 0.99 for c in close], "Close": close,
        "Volume": [1_000_000 + 1000 * i for i in range(80)]}, index=days)


def _install_feed(today):
    feed = types.ModuleType("engine.data_feed")

    def load_stock(symbol, max_age_hours=None):
        if symbol in ("EEE", "NOD"):
            return None
        last = today - timedelta(days=1) if symbol == "FFF" else today
        while last.weekday() >= 5:
            last -= timedelta(days=1)
        return _frame(symbol, last)

    feed.load_stock = load_stock
    feed.get_live_price = lambda symbol: None
    feed.disconnect_ib = lambda: None
    sys.modules["engine.data_feed"] = feed
    return load_stock


def _sig(symbol, d, price, conf=3, **kw):
    s = {"symbol": symbol, "date": d, "price": price,
         "strategy": "mean_reversion", "trigger": "t", "regime": "r",
         "category": "c", "confluence": conf, "strength": "strong",
         "stdev_20": 0.02, "rsi": 40.0, "vol_ratio": 1.2}
    s.update(kw)
    return s


def drive(variant_path, logs, flush):
    import importlib.util
    sys.path.insert(0, ROOT)
    os.environ["ARES_SCHEDULED"] = "1"
    today = datetime.now(timezone.utc).date()
    load_stock = _install_feed(today)
    import engine
    spec = importlib.util.spec_from_file_location("engine.tracker",
                                                  variant_path)
    tr = importlib.util.module_from_spec(spec)
    sys.modules["engine.tracker"] = tr
    engine.tracker = tr
    spec.loader.exec_module(tr)
    from pathlib import Path
    L = Path(logs)
    tr.LOGS_DIR = L
    tr.TRADES_FILE = L / "virtual_trades.json"
    tr.PENDING_FILE = L / "pending_signals.json"
    tr.QUEUE_FILE = L / "signal_queue.json"
    tr.RANKED_FILE = L / "queue_ranked.json"
    tr.QUEUE_EVENTS_FILE = L / "queue_events.jsonl"

    def last_close(sym):
        return round(float(load_stock(sym)["Close"].iloc[-1]), 4)

    iso = today.isoformat()
    old = (today - timedelta(days=10)).isoformat()
    tr.save_trades([{"symbol": "AAA", "status": "open", "entry_date": old},
                    {"symbol": "BBB", "status": "open", "entry_date": old}])
    tr.save_pending([
        _sig("CCC", iso, 50.0),
        _sig("AAA", iso, 50.0),
        _sig("DDD", old, 50.0),
        _sig("EEE", iso, 50.0),
        _sig("FFF", iso, 50.0),
        _sig("NOD", iso, 50.0, fill_attempts=2),
        _sig("CC2", iso, 50.0, stdev_20=None),
    ])
    q = []
    for sym, added, conf in (("GGG", iso, 3), ("HHH", iso, 2),
                             ("III", old, 3), ("JJJ", iso, 3),
                             ("EEE", iso, 1)):
        q.append({"symbol": sym, "strategy": "mean_reversion",
                  "price_at_signal": (last_close(sym) if sym != "EEE"
                                      else 50.0),
                  "confluence": conf, "date_added": added,
                  "rsi_at_signal": 40.0, "stdev_20": 0.02})
    tr.save_queue(q)

    store = getattr(tr, "_ADMISSION_EVAL", None)

    def reset(token):
        """What generate_report() / monitor() do at entry."""
        if store is not None:
            store.clear()
            store.update(tr._new_admission_store(token))

    def mark(key, value=True):
        if store is not None:
            store[key] = value

    # ---- run 1: a report (decision_path "report") ----
    reset(1)
    print("== fill pass")
    mark("fill_pass_expected")
    tr.execute_pending_signals()
    print("== maintenance")
    tr.maintain_queue()
    print("== scan promotion")
    mark("promotion_expected")
    tr.promote_queue("scan")
    signals = [_sig("AAA", iso, 51.0), _sig("KKK", iso, 52.0, conf=1)]
    mark("signals", signals)
    print("== scan admissions")
    for s in signals:
        if store is not None:
            store["scan_open_trade_expected"] += 1
        print("  ->", tr.open_trade(s))
    mark("report_completed")
    flush(store, "report", None)
    # ---- between runs: stop-outs free three slots ----
    trades = tr.load_trades()
    for t in trades:
        if t["symbol"] in ("BBB", "CCC", "CC2"):
            t["status"] = "closed"
    tr.save_trades(trades)
    tr.save_pending([p for p in tr.load_pending() if p["symbol"] != "FFF"])
    # ---- run 2: a monitor (decision_path "monitor") ----
    reset(2)
    print("== monitor promotion")
    mark("promotion_expected")
    tr.promote_queue("monitor", use_live=True)
    mark("monitor_completed")
    flush(store, "monitor", {"cycle_started_at": "2026-10-29T16:10:00+00:00",
                             "invocation_token": 2})
    # ---- run 3: a report with a duplicate pending and capacity left ----
    trades = tr.load_trades()
    for t in trades:
        if t["symbol"] in ("GGG", "JJJ"):
            t["status"] = "closed"
    tr.save_trades(trades)
    tr.save_pending([p for p in tr.load_pending()
                     if p["symbol"] not in ("GGG", "JJJ")])
    reset(3)
    mark("fill_pass_expected")
    mark("promotion_expected")
    signals = [_sig("EEE", iso, 50.0), _sig("MMM", iso, 53.0)]
    mark("signals", signals)
    print("== report 2 admissions")
    for s in signals:
        if store is not None:
            store["scan_open_trade_expected"] += 1
        print("  ->", tr.open_trade(s))
    flush(store, "report", None)
    return tr, store


def driver_main(variant_copy, logs, obs_logs):
    from pathlib import Path

    def flush(store, path, peer):
        """Variant B only: A has no store. Observer output goes to obs_logs,
        never beside the state files being compared."""
        if store is None:
            return
        from engine import admission_observer as ao
        ao.LOGS_DIR = Path(obs_logs)
        summary = ao.flush(store, decision_path=path, peer=peer)
        again = ao.flush(store, decision_path=path, peer=peer)
        sys.stderr.write(f"SUMMARY {path} " + json.dumps(summary) + "\n")
        sys.stderr.write(f"AGAIN {path} " + json.dumps(again) + "\n")

    drive(variant_copy, logs, flush)


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------

def _mask(text):
    return STAMP.sub("<ts>", text)


def _run(variant, tmp):
    """Each variant runs from a byte-identical copy at <tmp>/<V>/src/engine/,
    beside a copy of config/strategy_params.json, so both resolve config the
    same way and neither `__file__`-relative default reaches production."""
    import shutil
    logs = os.path.join(tmp, variant, "logs")
    obs = os.path.join(tmp, variant, "obs")
    src = os.path.join(tmp, variant, "src")
    for d in (logs, obs, os.path.join(src, "engine"),
              os.path.join(src, "config")):
        os.makedirs(d)
    copy = os.path.join(src, "engine", "tracker.py")
    shutil.copyfile(VARIANTS[variant], copy)
    shutil.copyfile(os.path.join(ROOT, "config", "strategy_params.json"),
                    os.path.join(src, "config", "strategy_params.json"))
    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "--drive", copy, logs,
         obs], capture_output=True, text=True, cwd=ROOT, timeout=300)
    files = {}
    for name in sorted(os.listdir(logs)):
        with open(os.path.join(logs, name), encoding="utf-8") as fh:
            files[name] = _mask(fh.read())
    return proc, files, obs


def main():
    fails = []

    def check(label, cond, detail=""):
        print(f"  {'PASS' if cond else 'FAIL'}  {label}"
              + (f"\n        {detail}" if (detail and not cond) else ""))
        if not cond:
            fails.append(label)

    with tempfile.TemporaryDirectory() as tmp:
        pa, fa, _ = _run("A", tmp)
        pb, fb, obs = _run("B", tmp)
        check("variant A ran", pa.returncode == 0, pa.stderr[-2000:])
        check("variant B ran", pb.returncode == 0, pb.stderr[-2000:])
        check("operator stdout identical",
              _mask(pa.stdout) == _mask(pb.stdout),
              "\n".join(__import__("difflib").unified_diff(
                  _mask(pa.stdout).splitlines(),
                  _mask(pb.stdout).splitlines(), lineterm=""))[:3000])
        check("same state files written", sorted(fa) == sorted(fb),
              f"{sorted(fa)} vs {sorted(fb)}")
        for name in STATE_FILES:
            check(f"state file identical: {name}",
                  fa.get(name) == fb.get(name) and name in fa,
                  f"present A={name in fa} B={name in fb}")
        summs, agains = [], []
        for line in pb.stderr.splitlines():
            if line.startswith("SUMMARY "):
                summs.append(json.loads(line.split(" ", 2)[2]))
            elif line.startswith("AGAIN "):
                agains.append(json.loads(line.split(" ", 2)[2]))
        check("three flush summaries returned", len(summs) == 3,
              pb.stderr[-1500:])
        check("flush: no error, all written, heartbeat",
              all(s["flush_error"] is None and s["write_failures"] == 0
                  and s["written"] == s["attempted"] > 0
                  and s["heartbeat_written"] for s in summs),
              json.dumps(summs))
        check("second flush skipped (idempotent)",
              len(agains) == 3 and all(a.get("skipped_already_flushed")
                                       is True for a in agains),
              json.dumps(agains))
        recs, beats = [], []
        rp = os.path.join(obs, "admission_v1.jsonl")
        hp = os.path.join(obs, "admission_heartbeat_v1.jsonl")
        if os.path.exists(rp):
            with open(rp, encoding="utf-8") as fh:
                recs = [json.loads(l) for l in fh]
        if os.path.exists(hp):
            with open(hp, encoding="utf-8") as fh:
                beats = [json.loads(l) for l in fh]
        check("one heartbeat per run",
              [b["decision_path"] for b in beats]
              == ["report", "monitor", "report"],
              [b.get("decision_path") for b in beats])
        check("records carry their run's cycle_id",
              {r["cycle_id"] for r in recs} <= {b["cycle_id"] for b in beats}
              and len({b["cycle_id"] for b in beats}) == 3)
        if len(beats) == 3:
            cs = [b["call_sites"] for b in beats]
            for i, c in enumerate(cs):
                print(f"        call sites run {i + 1}: "
                      f"{json.dumps(c, sort_keys=True)}")
            check("run 1 (complete report): every site EVALUATE",
                  all(v["verdict"] in ("EVALUATE", "PASS")
                      for v in cs[0].values()), cs[0])
            check("run 2 (monitor): promotion EVALUATE, admissions linked",
                  cs[1]["promotion_monitor"]["verdict"] == "EVALUATE"
                  and cs[1]["promotion_admissions"]["linked"] == 3, cs[1])
            check("run 3 (report without fill or promotion pass): FAIL",
                  cs[2]["fill_pass"]["state"] == "expected_not_reached"
                  and cs[2]["promotion_scan"]["state"]
                  == "expected_not_reached", cs[2])
            check("monitor heartbeat carries peer identity",
                  beats[1]["peer_status"] == "identified"
                  and beats[1]["peer_invocation_token"] == 2)
        kinds = {}
        for r in recs:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        print(f"        record kinds: {kinds}")
        for k in ("fill_pass", "fill", "queue_insert", "queue_expiry",
                  "queue_maintenance", "promotion_pass", "admission"):
            check(f"records include kind {k}", kinds.get(k, 0) > 0)
        bad = [(r["seq"], r["kind"], r["contract_violations"]) for r in recs
               if r["contract_violations"]]
        check("no contract violations", not bad, bad)
        fills = {r["symbol"]: r["outcome"] for r in recs if r["kind"] == "fill"}
        print(f"        fill outcomes: {fills}")
        want = {"CCC": "filled", "AAA": "discarded_already_open",
                "DDD": "dropped_aged", "EEE": "retained_no_data",
                "FFF": "retained_bar_not_today", "NOD": "dropped_no_data",
                "CC2": "filled"}
        check("fill outcomes as scripted", fills == want, fills)
        adm = [(r["symbol"], r["branch"], r["route"], r["call_state"])
               for r in recs if r["kind"] == "admission"]
        print(f"        admissions: {adm}")
        check("every admission returned",
              all(a[3] == "returned" for a in adm), adm)
        want_adm = [("AAA", "duplicate_open", "scan_direct"),
                    ("KKK", "queued", "scan_direct"),
                    ("GGG", "pending", "queue_monitor"),
                    ("JJJ", "pending", "queue_monitor"),
                    ("HHH", "pending", "queue_monitor"),
                    ("EEE", "duplicate_pending", "scan_direct"),
                    ("MMM", "pending", "scan_direct")]
        check("admission branches and routes as scripted",
              [a[:3] for a in adm] == want_adm, adm)
        qis = [(r["symbol"], r["insert"], r["queue_memory"],
                r["queue_event_write"]) for r in recs
               if r["kind"] == "queue_insert"]
        print(f"        queue inserts: {qis}")
        check("every queue insert has a parent",
              all(r["parent_seq"] is not None for r in recs
                  if r["kind"] == "queue_insert"))
        pps = [(r["source"], r["return_reason"],
                [(c["symbol"], c["outcome"]) for c in r["candidates"]])
               for r in recs if r["kind"] == "promotion_pass"]
        print(f"        promotion passes: {pps}")
        ex = [r["removals"] for r in recs if r["kind"] == "queue_expiry"]
        print(f"        expiry removals: {ex}")
        check("III expired", any(x.get("symbol") == "III"
                                 for e in ex for x in e), ex)
    n = len(fails)
    print(f"\n  {'ALL PASS' if not n else f'{n} FAILED: {fails}'}")
    return 1 if n else 0


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--drive":
        driver_main(sys.argv[2], sys.argv[3], sys.argv[4])
        sys.exit(0)
    sys.exit(main())
