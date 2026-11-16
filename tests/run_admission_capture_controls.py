#!/usr/bin/env python3
"""Admission capture v1, milestone M2: capture controls (design §7).

    python3 tests/run_admission_capture_controls.py

Two groups:

  *Capture: one control per outcome* -- every admission branch (pending,
  queued/inserted, queued/already_queued, duplicate_open, duplicate_pending),
  queue event written vs write failed, every promotion early return,
  `unranked_fallback`, every candidate outcome including an unexamined tail
  after `break`, and all nine fill outcomes plus the empty fill pass.

  *Completion and partial outcomes* -- `save_pending` raising inside
  `open_trade`; a queue insertion that evicts the new candidate, with
  `save_queue` failing at each of the three points (open, partial dump, close
  after a complete dump); a fill exception after `trades.append` (the
  reviewer's EXECUTED-print probe), below and at MAX_FILL_ATTEMPTS;
  `save_trades` failing after `save_pending` (the vanished fill); and the
  reviewer's r3 combination (post-append exception, then `save_trades`
  failing).

The REAL engine/tracker.py and engine/admission_observer.py are used, in one
process, against a fresh scratch logs directory per control. Only market data
(`engine.data_feed`), indicators (identity) and, where a control says so, the
fill window are stubbed. Failure points are injected by shadowing `open`,
`json` or `print` in the tracker module's globals for the duration of one
call; production code is not edited.

These controls check what the CAPTURE records and what the scratch state files
hold afterwards. The verdict on the disk effect (design §3.5: consistent,
`<file>_not_persisted`, INCONCLUSIVE) belongs to the inspection tool (M3/M4);
here the file left by each failure point is asserted directly, so those later
controls have a known input.

  *Queue upkeep and raises before a return* -- maintain_queue with an
  expiry and with an empty queue; open_trade, promote_queue and a fill
  demotion each raising before they return.

Completeness contract (design §4.1, G2-C1), checked over every store the
controls derived: no real record names a missing field and record_complete
is exactly "returned, nothing missing, no violation" (k01); nulling the raw
source of each field `REQUIRED_FIELDS` lists for the record's state names
that field in `missing_fields`, and every table entry is exercised (k02);
each save that is unconditional on a returned path, marked raised, gives
`<save>_not_completed_at_return` (k03); the review v3 probe (k04).

Anti-vacuity: every control asserts a specific outcome value, and the final
coverage checks require that the controls together produced all nine fill
outcomes, all four admission branches, all five candidate outcomes and all
four promotion return reasons. A harness whose stimulus did nothing fails.
Mutants of tracker.py (a capture statement removed) and of the observer
(the pre-C1 completeness rule restored, the save rule dropped) must each
make their named checks fail.
"""

import builtins
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import traceback
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ["ARES_SCHEDULED"] = "1"

MARK = "AdmissionControlMarker"


class Marker(RuntimeError):
    """Injected production failure, identifiable in records and tracebacks."""


# --------------------------------------------------------------------------
# Stubbed market data. Behaviour per symbol is set by each control.
# --------------------------------------------------------------------------

class Feed:
    def __init__(self):
        self.mode = {}
        self.price = {}
        self.calls = {"load_stock": 0, "get_live_price": 0}

    def reset(self):
        self.mode.clear()
        self.price.clear()

    def frame(self, symbol, last_day):
        import pandas as pd
        p = self.price.get(symbol, 50.0)
        days = pd.date_range(end=pd.Timestamp(last_day), periods=30, freq="D")
        return pd.DataFrame({"Open": [p] * 30, "High": [p * 1.01] * 30,
                             "Low": [p * 0.99] * 30, "Close": [p] * 30,
                             "Volume": [1_000_000] * 30}, index=days)

    def load_stock(self, symbol, max_age_hours=None):
        self.calls["load_stock"] += 1
        m = self.mode.get(symbol, "ok")
        if m == "none":
            return None
        if m == "raise":
            raise Marker(f"{MARK}: load_stock({symbol})")
        today = datetime.now(timezone.utc).date()
        last = today - timedelta(days=1) if m == "yesterday" else today
        return self.frame(symbol, last)

    def get_live_price(self, symbol):
        self.calls["get_live_price"] += 1
        return None


FEED = Feed()


def _install_stubs():
    feed = types.ModuleType("engine.data_feed")
    feed.load_stock = FEED.load_stock
    feed.get_live_price = FEED.get_live_price
    feed.disconnect_ib = lambda: None
    sys.modules["engine.data_feed"] = feed
    ind = types.ModuleType("engine.indicators")
    ind.add_indicators = lambda df: df
    sys.modules["engine.indicators"] = ind


def _load_tracker():
    """The real engine/tracker.py, or the mutant named by
    ARES_CAPTURE_TRACKER (anti-vacuity runs only)."""
    path = os.environ.get("ARES_CAPTURE_TRACKER")
    if not path:
        from engine import tracker
        return tracker
    import importlib.util
    import engine
    spec = importlib.util.spec_from_file_location("engine.tracker", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["engine.tracker"] = mod
    engine.tracker = mod
    spec.loader.exec_module(mod)
    return mod


def _load_observer():
    """The real engine/admission_observer.py, or the mutant named by
    ARES_CAPTURE_OBSERVER (anti-vacuity runs only)."""
    path = os.environ.get("ARES_CAPTURE_OBSERVER")
    if not path:
        from engine import admission_observer
        return admission_observer
    import importlib.util
    import engine
    spec = importlib.util.spec_from_file_location("engine.admission_observer",
                                                  path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["engine.admission_observer"] = mod
    engine.admission_observer = mod
    spec.loader.exec_module(mod)
    return mod


_install_stubs()
tr = _load_tracker()
ao = _load_observer()

BASE_PARAMS = tr._load_params()
REAL_FILL_WINDOW = tr._fill_window_ok
TODAY = date.today().isoformat()
OLD = (date.today() - timedelta(days=10)).isoformat()

SEEN = {"fill": set(), "branch": set(), "candidate": set(),
        "pass_return": set()}


# --------------------------------------------------------------------------
# Scratch state, store reset and failure injection
# --------------------------------------------------------------------------

class Scratch:
    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="adm_capture_"))
        tr.LOGS_DIR = self.dir
        tr.TRADES_FILE = self.dir / "virtual_trades.json"
        tr.PENDING_FILE = self.dir / "pending_signals.json"
        tr.QUEUE_FILE = self.dir / "signal_queue.json"
        tr.RANKED_FILE = self.dir / "queue_ranked.json"
        tr.QUEUE_EVENTS_FILE = self.dir / "queue_events.jsonl"

    def write(self, name, obj):
        (self.dir / name).write_text(json.dumps(obj, indent=2))

    def read(self, name):
        p = self.dir / name
        if not p.exists():
            return "absent"
        try:
            return json.loads(p.read_text())
        except ValueError:
            return "unreadable"

    def raw(self, name):
        p = self.dir / name
        return p.read_text() if p.exists() else None

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def _reset_store(token=1):
    tr._ADMISSION_EVAL.clear()
    tr._ADMISSION_EVAL.update(tr._new_admission_store(token))
    return tr._ADMISSION_EVAL


def _sig(symbol, d=None, price=50.0, conf=3, **kw):
    s = {"symbol": symbol, "date": d or TODAY, "price": price,
         "strategy": "mean_reversion", "trigger": "t", "regime": "r",
         "category": "c", "confluence": conf, "strength": "strong",
         "stdev_20": 0.02, "rsi": 40.0, "vol_ratio": 1.2}
    s.update(kw)
    return s


def _trade(symbol):
    return {"symbol": symbol, "status": "open", "entry_date": OLD}


def _qentry(symbol, price=50.0, conf=3, added=None):
    return {"symbol": symbol, "strategy": "mean_reversion",
            "price_at_signal": price, "confluence": conf,
            "date_added": added or TODAY, "rsi_at_signal": 40.0,
            "stdev_20": 0.02}


@contextlib.contextmanager
def patched(**attrs):
    """Shadow tracker globals for one call; restored afterwards."""
    saved = {}
    for k, v in attrs.items():
        saved[k] = tr.__dict__.get(k, _ABSENT)
        setattr(tr, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is _ABSENT:
                delattr(tr, k)
            else:
                setattr(tr, k, v)


_ABSENT = object()


def _params(**over):
    p = dict(BASE_PARAMS)
    p.update(over)
    return lambda: dict(p)


def _window(ok=True):
    reason = "test: window open" if ok else "test: window closed"
    return lambda params: (ok, reason)


class _CloseFails:
    """File whose close raises after the complete dump was written."""

    def __init__(self, fh):
        self._fh = fh

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        self._fh.close()
        if et is None:
            raise Marker(f"{MARK}: close failed after a complete dump")
        return False

    def __getattr__(self, name):
        return getattr(self._fh, name)


class _JsonShim:
    def __init__(self, target):
        self._target = target

    def dump(self, obj, fp, **kw):
        if Path(getattr(fp, "name", "")) == self._target:
            text = json.dumps(obj, **kw)
            fp.write(text[: max(1, len(text) // 2)])
            fp.flush()
            raise Marker(f"{MARK}: dump failed after a partial write")
        return json.dump(obj, fp, **kw)

    def __getattr__(self, name):
        return getattr(json, name)


def failpoint(target_path, point):
    """Three failure points per save (design §7, Persistence):
    'open' raises; 'partial' writes an unparseable prefix, then raises;
    'close' raises after a complete dump."""
    target = Path(target_path)
    real_open = builtins.open

    def fake_open(path, mode="r", *a, **k):
        if Path(path) == target and "w" in mode:
            if point == "open":
                raise Marker(f"{MARK}: open failed")
            fh = real_open(path, mode, *a, **k)
            return _CloseFails(fh) if point == "close" else fh
        return real_open(path, mode, *a, **k)

    attrs = {"open": fake_open}
    if point == "partial":
        attrs["json"] = _JsonShim(target)
    return patched(**attrs)


def print_raises_on(fragment):
    def fake_print(*args, **kw):
        if args and fragment in str(args[0]):
            raise Marker(f"{MARK}: stdout raised at the matching print")
        return builtins.print(*args, **kw)
    return patched(print=fake_print)


CORPUS = []


def _derive(store):
    """derive_all, and a snapshot of the store for the completeness checks.
    The records list is replaced (never cleared) at the next reset, so a
    shallow copy keeps this run's raw records."""
    raw, out = ao.derive_all(store)
    CORPUS.append((dict(store), out))
    return raw, out


def _of(out, kind):
    return [r for r in out if r["kind"] == kind]


def _note(out):
    for r in out:
        if r["kind"] == "fill" and r.get("outcome"):
            SEEN["fill"].add(r["outcome"])
        elif r["kind"] == "admission" and r.get("branch"):
            SEEN["branch"].add(r["branch"])
        elif r["kind"] == "promotion_pass":
            if r.get("return_reason"):
                SEEN["pass_return"].add(r["return_reason"])
            for c in r["candidates"]:
                if c.get("outcome"):
                    SEEN["candidate"].add(c["outcome"])


# --------------------------------------------------------------------------
# Control registry
# --------------------------------------------------------------------------

CONTROLS = []


def control(fn):
    CONTROLS.append(fn)
    return fn


class Check:
    def __init__(self):
        self.fails = []

    def __call__(self, cond, label):
        if not cond:
            self.fails.append(label)
        return cond


def _complete(c, recs, label):
    bad = [(r["seq"], r["kind"], r["contract_violations"]) for r in recs
           if r["contract_violations"]]
    c(not bad, f"{label}: contract violations {bad}")


# ---------------------------- fill outcomes --------------------------------

def _fill_run(s, pending, trades=(), params=None):
    s.write("pending_signals.json", list(pending))
    s.write("virtual_trades.json", list(trades))
    store = _reset_store()
    with patched(_load_params=params or _params()):
        tr.execute_pending_signals()
    raw, out = _derive(store)
    _note(out)
    return raw, out


def _one_fill(c, out, outcome, executed, appended):
    passes, fills = _of(out, "fill_pass"), _of(out, "fill")
    c(len(passes) == 1, f"fill passes {len(passes)}")
    c(len(fills) == 1, f"fill entries {len(fills)}")
    if not (passes and fills):
        return None
    f = fills[0]
    c(f["outcome"] == outcome, f"outcome {f['outcome']} != {outcome}")
    c(f["executed_marked"] is executed, f"executed_marked {f['executed_marked']}")
    c(f["trade_appended"] is appended, f"trade_appended {f['trade_appended']}")
    c(f["entry_state"] == "completed", f"entry_state {f['entry_state']}")
    c(f["parent_seq"] == passes[0]["seq"], "fill not linked to its pass")
    c(passes[0]["return_reason"] == "completed",
      f"pass return {passes[0]['return_reason']}")
    c(passes[0]["pending_save"] == "completed"
      and passes[0]["trades_save"] == "completed",
      f"pass saves {passes[0]['pending_save']}/{passes[0]['trades_save']}")
    return f


@control
def c01_fill_filled(s):
    c = Check()
    _, out = _fill_run(s, [_sig("FIL")])
    f = _one_fill(c, out, "filled", True, True)
    if f:
        for k in ("bar_date", "today_utc", "bar_open", "entry_price", "shares",
                  "stop_loss", "take_profit", "fill_source", "entry_date"):
            c(k in f, f"fill field {k} missing")
        c(f["fill_source"] == "scheduled", f"fill_source {f.get('fill_source')}")
        c(f["stdev_fallback"] is False, "stdev_fallback")
    c([t["symbol"] for t in s.read("virtual_trades.json")] == ["FIL"],
      "trade not booked")
    c(s.read("pending_signals.json") == [], "pending not removed")
    _complete(c, out, "c01")
    return c.fails


@control
def c02_fill_demoted_to_queue(s):
    c = Check()
    _, out = _fill_run(s, [_sig("DEM")],
                       [_trade(x) for x in ("T1", "T2", "T3", "T4", "T5")])
    f = _one_fill(c, out, "demoted_to_queue", True, False)
    qis = _of(out, "queue_insert")
    c(len(qis) == 1 and qis[0]["parent_seq"] == (f or {}).get("seq"),
      "demotion's queue_insert not linked to the fill entry")
    c(bool(f) and f.get("queue", {}).get("insert") == "inserted",
      "fill entry lacks its queue view")
    c([q["symbol"] for q in s.read("signal_queue.json")] == ["DEM"],
      "demoted signal not in queue")
    _complete(c, out, "c02")
    return c.fails


@control
def c03_fill_discarded_already_open(s):
    c = Check()
    _, out = _fill_run(s, [_sig("OPN")], [_trade("OPN")])
    _one_fill(c, out, "discarded_already_open", True, False)
    c(s.read("pending_signals.json") == [], "silent discard: pending kept")
    c(len(s.read("virtual_trades.json")) == 1, "trade count changed")
    _complete(c, out, "c03")
    return c.fails


@control
def c04_fill_dropped_aged(s):
    c = Check()
    _, out = _fill_run(s, [_sig("AGD", d=OLD)])
    f = _one_fill(c, out, "dropped_aged", True, False)
    c(bool(f) and f.get("pending_age_days") == 10,
      f"pending_age_days {(f or {}).get('pending_age_days')}")
    c(s.read("pending_signals.json") == [], "aged pending kept")
    _complete(c, out, "c04")
    return c.fails


@control
def c05_fill_retained_no_data(s):
    c = Check()
    FEED.mode["NDA"] = "none"
    _, out = _fill_run(s, [_sig("NDA")])
    f = _one_fill(c, out, "retained_no_data", False, False)
    c(bool(f) and f["fill_attempts"] == 1, "fill_attempts != 1")
    p = s.read("pending_signals.json")
    c([x["symbol"] for x in p] == ["NDA"] and p[0].get("fill_attempts") == 1,
      f"pending {p}")
    _complete(c, out, "c05")
    return c.fails


@control
def c06_fill_dropped_no_data(s):
    c = Check()
    FEED.mode["NDB"] = "none"
    _, out = _fill_run(s, [_sig("NDB", fill_attempts=2)])
    f = _one_fill(c, out, "dropped_no_data", True, False)
    c(bool(f) and f["fill_attempts"] == tr.MAX_FILL_ATTEMPTS,
      "fill_attempts != MAX")
    c(s.read("pending_signals.json") == [], "dropped pending kept")
    _complete(c, out, "c06")
    return c.fails


@control
def c07_fill_retained_bar_not_today(s):
    c = Check()
    FEED.mode["YST"] = "yesterday"
    _, out = _fill_run(s, [_sig("YST")])
    f = _one_fill(c, out, "retained_bar_not_today", False, False)
    if f:
        c(f["bar_date"] != f["today_utc"], "bar_date == today_utc")
    c([x["symbol"] for x in s.read("pending_signals.json")] == ["YST"],
      "pending not retained")
    _complete(c, out, "c07")
    return c.fails


@control
def c08_fill_retained_exception(s):
    c = Check()
    FEED.mode["EXA"] = "raise"
    _, out = _fill_run(s, [_sig("EXA")])
    f = _one_fill(c, out, "retained_exception", False, False)
    c(bool(f) and MARK in str(f["exception"]), "exception not captured")
    c(bool(f) and f["fill_attempts"] == 1, "fill_attempts != 1")
    _complete(c, out, "c08")
    return c.fails


@control
def c09_fill_dropped_exception(s):
    c = Check()
    FEED.mode["EXB"] = "raise"
    _, out = _fill_run(s, [_sig("EXB", fill_attempts=2)])
    f = _one_fill(c, out, "dropped_exception", True, False)
    c(bool(f) and MARK in str(f["exception"]), "exception not captured")
    c(s.read("pending_signals.json") == [], "dropped pending kept")
    _complete(c, out, "c09")
    return c.fails


@control
def c10_fill_pass_no_pending(s):
    c = Check()
    _, out = _fill_run(s, [])
    passes = _of(out, "fill_pass")
    c(len(passes) == 1 and passes[0]["return_reason"] == "no_pending"
      and passes[0]["pending_seen"] == 0, f"pass {passes}")
    c(not _of(out, "fill"), "fill entries on an empty pass")
    c(bool(passes) and passes[0]["pending_save"] == "not_attempted",
      "save marked on the early return")
    _complete(c, out, "c10")
    return c.fails


# ---------------------------- admissions -----------------------------------

def _admit(s, sig, trades=(), pending=(), queue=None, params=None,
           from_queue=False):
    s.write("virtual_trades.json", list(trades))
    s.write("pending_signals.json", list(pending))
    if queue is not None:
        s.write("signal_queue.json", list(queue))
    store = _reset_store()
    if not from_queue:
        store["signals"] = [sig]
        store["scan_open_trade_expected"] = 1
    with patched(_load_params=params or _params()):
        result = tr.open_trade(sig, from_queue=from_queue)
    raw, out = _derive(store)
    _note(out)
    return result, out


def _one_adm(c, out, branch, returned):
    adm = _of(out, "admission")
    c(len(adm) == 1, f"admission records {len(adm)}")
    if not adm:
        return None
    a = adm[0]
    c(a["branch"] == branch, f"branch {a['branch']} != {branch}")
    c(a["returned"] == returned, f"returned {a['returned']} != {returned}")
    c(a["call_state"] == "returned", f"call_state {a['call_state']}")
    c(a["t_enter"] is not None and a["t_return"] is not None, "bracket open")
    return a


FULL = [_trade(x) for x in ("T1", "T2", "T3", "T4", "T5")]


@control
def c20_admission_pending(s):
    c = Check()
    r, out = _admit(s, _sig("PND"), trades=[_trade("T1")])
    a = _one_adm(c, out, "pending", "pending")
    c(r == "pending", f"production returned {r}")
    if a:
        c(a["pending_appended"] and a["pending_save"] == "completed",
          "pending save facts")
        c(a["open_positions_before"] == 1 and a["pending_before"] == 0,
          f"counts {a['open_positions_before']}/{a['pending_before']}")
        c(a["max_positions"] == 5, "max_positions")
    c([p["symbol"] for p in s.read("pending_signals.json")] == ["PND"],
      "pending not written")
    _complete(c, out, "c20")
    return c.fails


@control
def c21_admission_queued_inserted(s):
    c = Check()
    r, out = _admit(s, _sig("QIN"), trades=FULL, queue=[])
    a = _one_adm(c, out, "queued", "queued")
    q = (a or {}).get("queue", {})
    c(q.get("insert") == "inserted" and q.get("queue_memory") == "kept",
      f"queue view {q}")
    c(q.get("queue_event_write") == "written", "queued event not written")
    c(q.get("queue_save") == "completed", "queue save")
    qis = _of(out, "queue_insert")
    c(len(qis) == 1 and qis[0]["parent_seq"] == (a or {}).get("seq"),
      "queue_insert not linked")
    c([x["symbol"] for x in s.read("signal_queue.json")] == ["QIN"],
      "queue not written")
    _complete(c, out, "c21")
    return c.fails


@control
def c22_admission_queued_already_queued(s):
    c = Check()
    before = [_qentry("QAL")]
    r, out = _admit(s, _sig("QAL"), trades=FULL, queue=before)
    a = _one_adm(c, out, "queued", "queued")
    q = (a or {}).get("queue", {})
    c(q.get("queue_memory") == "already_queued", f"queue view {q}")
    c(q.get("queue_save") is None and q.get("queue_event_write") is None,
      "already_queued claims a save or event")
    c(s.read("signal_queue.json") == before, "queue changed")
    _complete(c, out, "c22")
    return c.fails


@control
def c23_admission_duplicate_open(s):
    c = Check()
    r, out = _admit(s, _sig("DOP"), trades=[_trade("DOP")])
    _one_adm(c, out, "duplicate_open", "duplicate")
    c(r == "duplicate", f"production returned {r}")
    c(s.read("pending_signals.json") == [], "pending written")
    _complete(c, out, "c23")
    return c.fails


@control
def c24_admission_duplicate_pending(s):
    c = Check()
    r, out = _admit(s, _sig("DPN"), pending=[_sig("DPN")])
    a = _one_adm(c, out, "duplicate_pending", "duplicate")
    c(bool(a) and a["pending_save"] == "not_attempted", "save claimed")
    c(len(s.read("pending_signals.json")) == 1, "pending changed")
    _complete(c, out, "c24")
    return c.fails


@control
def c25_admission_queue_event_write_failed(s):
    """queue_events.jsonl unwritable: the event write fails, the insert and
    the queue save still complete, and the record says write_failed."""
    c = Check()
    with patched(QUEUE_EVENTS_FILE=s.dir / "no_such_dir" / "q.jsonl"):
        r, out = _admit(s, _sig("QEF"), trades=FULL, queue=[])
    a = _one_adm(c, out, "queued", "queued")
    q = (a or {}).get("queue", {})
    c(q.get("queue_event_write") == "write_failed", f"queue view {q}")
    c(q.get("queue_save") == "completed", "queue save")
    c([x["symbol"] for x in s.read("signal_queue.json")] == ["QEF"],
      "queue not written")
    _complete(c, out, "c25")
    return c.fails


# ---------------------------- promotion ------------------------------------

def _promote(s, trades=(), pending=(), queue=(), ranked=(), window=True,
             source="scan", params=None):
    s.write("virtual_trades.json", list(trades))
    s.write("pending_signals.json", list(pending))
    s.write("signal_queue.json", list(queue))
    s.write("queue_ranked.json", list(ranked))
    store = _reset_store()
    with patched(_load_params=params or _params(),
                 _fill_window_ok=_window(window)):
        tr.promote_queue(source)
    raw, out = _derive(store)
    _note(out)
    return out


def _one_pass(c, out, rr):
    pp = _of(out, "promotion_pass")
    c(len(pp) == 1, f"promotion passes {len(pp)}")
    if not pp:
        return None
    c(pp[0]["return_reason"] == rr, f"return {pp[0]['return_reason']} != {rr}")
    c(pp[0]["t_exit"] is not None, "pass not closed")
    return pp[0]


@control
def c30_promotion_no_free_slots(s):
    c = Check()
    out = _promote(s, trades=FULL, queue=[_qentry("NFS")],
                   ranked=[_qentry("NFS")])
    p = _one_pass(c, out, "no_free_slots")
    c(bool(p) and p["early_return_reason"] == "no_free_slots"
      and p["free_slots"] == 0 and p["candidates"] == [], f"pass {p}")
    c(bool(p) and p["queue_save"] == "not_attempted", "save claimed")
    _complete(c, out, "c30")
    return c.fails


@control
def c31_promotion_queue_empty(s):
    c = Check()
    out = _promote(s)
    p = _one_pass(c, out, "queue_empty")
    c(bool(p) and p["ranked_basis"] == "empty", f"basis {(p or {}).get('ranked_basis')}")
    _complete(c, out, "c31")
    return c.fails


@control
def c32_promotion_fill_window_closed(s):
    c = Check()
    out = _promote(s, queue=[_qentry("FWC")], ranked=[_qentry("FWC")],
                   window=False)
    p = _one_pass(c, out, "fill_window_closed")
    c(bool(p) and p["fill_window_ok"] is False
      and p["fill_window_reason"] == "test: window closed", f"pass {p}")
    c(bool(p) and p["candidates"] == [], "candidates on a closed window")
    c([q["symbol"] for q in s.read("signal_queue.json")] == ["FWC"],
      "queue changed")
    _complete(c, out, "c32")
    return c.fails


@control
def c33_promotion_unranked_fallback(s):
    c = Check()
    out = _promote(s, queue=[_qentry("URF")], ranked=[])
    p = _one_pass(c, out, "completed")
    c(bool(p) and p["ranked_basis"] == "unranked_fallback",
      f"basis {(p or {}).get('ranked_basis')}")
    c(bool(p) and [x["outcome"] for x in p["candidates"]] == ["promoted"],
      "fallback candidate not promoted")
    adm = _of(out, "admission")
    c(len(adm) == 1 and adm[0]["route"] == "queue_scan"
      and adm[0]["parent_seq"] == (p or {}).get("seq"),
      f"admission route {[(a['route'], a['parent_seq']) for a in adm]}")
    _complete(c, out, "c33")
    return c.fails


@control
def c34_promotion_every_candidate_outcome(s):
    """held_dropped, deferred_transient, dropped_invalid, promoted, and
    dropped_open_trade (a second ranked entry for the promoted symbol, which
    is not in the pass's held set, so open_trade returns 'duplicate')."""
    c = Check()
    FEED.mode["TRN"] = "none"
    ranked = [_qentry("HLD"), _qentry("TRN"), _qentry("INV", price=40.0),
              _qentry("PRM"), _qentry("PRM")]
    out = _promote(s, pending=[_sig("HLD")], queue=ranked, ranked=ranked)
    p = _one_pass(c, out, "completed")
    got = [(x["symbol"], x["outcome"]) for x in (p or {}).get("candidates", [])]
    want = [("HLD", "held_dropped"), ("TRN", "deferred_transient"),
            ("INV", "dropped_invalid"), ("PRM", "promoted"),
            ("PRM", "dropped_open_trade")]
    c(got == want, f"candidates {got}")
    if p and len(p["candidates"]) == 5:
        cs = p["candidates"]
        c(cs[0]["held"] and cs[0]["t_validate_start"] is None,
          "held candidate validated")
        c(cs[1]["valid"] is False and cs[1]["reason"].startswith("transient:"),
          "transient reason")
        c(cs[2]["valid"] is False and "drifted" in cs[2]["reason"],
          f"invalid reason {cs[2]['reason']}")
        c(cs[3]["open_trade_result"] == "pending"
          and cs[4]["open_trade_result"] == "duplicate", "open_trade results")
        c(all(x["open_trade_expected"] is (i >= 3) for i, x in enumerate(cs)),
          "open_trade_expected")
    adm = _of(out, "admission")
    c([(a["branch"], a["route"], a["parent_seq"]) for a in adm]
      == [("pending", "queue_scan", (p or {}).get("seq")),
          ("duplicate_pending", "queue_scan", (p or {}).get("seq"))],
      f"admissions {[(a['branch'], a['route'], a['parent_seq']) for a in adm]}")
    c(bool(p) and p["queue_save"] == "completed"
      and p["ranked_save"] == "completed", "pass saves")
    c(sorted(q["symbol"] for q in s.read("signal_queue.json")) == ["TRN"],
      f"queue {s.read('signal_queue.json')}")
    _complete(c, out, "c34")
    return c.fails


@control
def c35_promotion_unexamined_tail_after_break(s):
    c = Check()
    ranked = [_qentry("TA1"), _qentry("TA2"), _qentry("TA3")]
    out = _promote(s, trades=FULL[:4], queue=ranked, ranked=ranked)
    p = _one_pass(c, out, "completed")
    c(bool(p) and p["free_slots"] == 1 and p["ranked_count"] == 3
      and p["candidates_examined"] == 1, f"pass {p and (p['free_slots'], p['ranked_count'], p['candidates_examined'])}")
    c(bool(p) and [x["symbol"] for x in p["candidates"]] == ["TA1"],
      "examined set")
    c(sorted(q["symbol"] for q in s.read("signal_queue.json")) == ["TA2", "TA3"],
      "unexamined tail left the queue")
    _complete(c, out, "c35")
    return c.fails


# ---------------------- completion and partial outcomes --------------------

@control
def c40_save_pending_raises_in_open_trade(s):
    """The record is incomplete, not absent; the raise propagates; coverage
    is reached_not_completed, never completed."""
    c = Check()
    s.write("virtual_trades.json", [])
    s.write("pending_signals.json", [])
    store = _reset_store()
    sig = _sig("SPR")
    store["signals"] = [sig]
    store["scan_open_trade_expected"] = 1
    raised = None
    with patched(_load_params=_params()), failpoint(tr.PENDING_FILE, "open"):
        try:
            tr.open_trade(sig)
        except Marker as e:
            raised = e
    c(raised is not None, "open_trade did not propagate the failure")
    _, out = _derive(store)
    _note(out)
    adm = _of(out, "admission")
    c(len(adm) == 1, "admission record absent")
    if adm:
        a = adm[0]
        c(a["branch"] == "pending" and a["returned"] is None
          and a["t_return"] is None, f"admission {a['branch']}/{a['returned']}")
        c(a["call_state"] == "raised_in_pending_save", a["call_state"])
        c(a["pending_save"] == "raised", a["pending_save"])
    sites = ao.coverage(store, "report", *_derive(store))
    st = sites["scan_admissions"]
    c(st["state"] == "reached_not_completed" and st["verdict"] == "INCONCLUSIVE",
      f"coverage {st}")
    c(s.read("pending_signals.json") == [], "pending changed on open failure")
    return c.fails


def _evicting_insert(s, point):
    """One-slot queue holding a conf 3 entry; a conf 1 signal arrives with
    every slot full. queue_signal inserts it, then evicts it (the reviewer's
    probe). `point` is None or a save_queue failure point."""
    c = Check()
    keep = _qentry("KEP", conf=3)
    s.write("virtual_trades.json", FULL)
    s.write("pending_signals.json", [])
    s.write("signal_queue.json", [keep])
    store = _reset_store()
    sig = _sig("EVI", conf=1)
    store["signals"] = [sig]
    raised = None
    ctx = failpoint(tr.QUEUE_FILE, point) if point else contextlib.nullcontext()
    with patched(_load_params=_params(queue_max_size=1)), ctx:
        try:
            r = tr.open_trade(sig)
        except Marker as e:
            raised, r = e, None
    _, out = _derive(store)
    _note(out)
    adm, qis = _of(out, "admission"), _of(out, "queue_insert")
    c(len(adm) == 1 and len(qis) == 1, f"records {len(adm)}/{len(qis)}")
    if not (adm and qis):
        return c, None
    a, q = adm[0], qis[0]
    c(q["insert"] == "inserted" and q["evicted"] == ["EVI"]
      and q["queue_memory"] == "inserted_then_evicted", f"queue view {q}")
    if point is None:
        c(raised is None and r == "queued", f"returned {r}")
        c(q["queue_save"] == "completed" and a["call_state"] == "returned",
          "saves")
        c(s.read("signal_queue.json") == [keep], "committed queue")
        _complete(c, out, "c41")
    else:
        c(raised is not None, "save failure did not propagate")
        c(q["queue_save"] == "raised" and q["returned"] is False,
          f"queue save {q['queue_save']}")
        c(a["returned"] is None and a["call_state"] == "raised_in_queue_save",
          f"call_state {a['call_state']}")
    return c, s.read("signal_queue.json")


@control
def c41_insert_then_evict_new_candidate(s):
    c, _ = _evicting_insert(s, None)
    return c.fails


@control
def c42_insert_then_evict_save_open_fails(s):
    c, left = _evicting_insert(s, "open")
    c(left == [_qentry("KEP", conf=3)], f"file left {left}: old file expected")
    return c.fails


@control
def c43_insert_then_evict_save_partial_dump(s):
    c, left = _evicting_insert(s, "partial")
    c(left == "unreadable", f"file left {left}: unparseable prefix expected")
    return c.fails


@control
def c44_insert_then_evict_save_close_fails(s):
    c, left = _evicting_insert(s, "close")
    c(left == [_qentry("KEP", conf=3)],
      f"file left {left}: complete new dump expected")
    return c.fails


def _post_append(s, attempts, trades_point=None):
    s.write("pending_signals.json", [_sig("PAX", fill_attempts=attempts)]
            if attempts else [_sig("PAX")])
    s.write("virtual_trades.json", [_trade("T1")])
    store = _reset_store()
    raised = None
    ctx = (failpoint(tr.TRADES_FILE, trades_point) if trades_point
           else contextlib.nullcontext())
    with patched(_load_params=_params()), print_raises_on("EXECUTED"), ctx:
        try:
            tr.execute_pending_signals()
        except Marker as e:
            raised = e
    _, out = _derive(store)
    _note(out)
    return raised, out, store


@control
def c45_fill_exception_after_append_retained(s):
    """Reviewer's probe: stdout raises at the EXECUTED print. Trade booked,
    pending retained: retained_exception with trade_appended (n + 1)."""
    c = Check()
    raised, out, _ = _post_append(s, 0)
    c(raised is None, "pass did not contain the fill exception")
    f = _of(out, "fill")
    c(len(f) == 1 and f[0]["outcome"] == "retained_exception"
      and f[0]["trade_appended"] is True and f[0]["executed_marked"] is False
      and f[0]["fill_attempts"] == 1, f"fill {f}")
    c([t["symbol"] for t in s.read("virtual_trades.json")] == ["T1", "PAX"],
      "trade count not n + 1")
    c([p["symbol"] for p in s.read("pending_signals.json")] == ["PAX"],
      "pending not retained")
    _complete(c, out, "c45")
    return c.fails


@control
def c46_fill_exception_after_append_at_max(s):
    """The same at MAX_FILL_ATTEMPTS: dropped_exception, booked and removed
    (booked_with_drop_event)."""
    c = Check()
    raised, out, _ = _post_append(s, tr.MAX_FILL_ATTEMPTS - 1)
    f = _of(out, "fill")
    c(len(f) == 1 and f[0]["outcome"] == "dropped_exception"
      and f[0]["trade_appended"] is True and f[0]["executed_marked"] is True,
      f"fill {f}")
    c([t["symbol"] for t in s.read("virtual_trades.json")] == ["T1", "PAX"],
      "trade not booked")
    c(s.read("pending_signals.json") == [], "pending not removed")
    _complete(c, out, "c46")
    return c.fails


@control
def c47_save_trades_fails_after_save_pending(s):
    """The vanished fill (§2.2.1): pending saved without the filled signal,
    then save_trades fails at open; the trade is in neither file."""
    c = Check()
    s.write("pending_signals.json", [_sig("VAN")])
    s.write("virtual_trades.json", [_trade("T1")])
    store = _reset_store()
    raised = None
    with patched(_load_params=_params()), failpoint(tr.TRADES_FILE, "open"):
        try:
            tr.execute_pending_signals()
        except Marker as e:
            raised = e
    c(raised is not None, "save_trades failure did not propagate")
    raw, out = _derive(store)
    _note(out)
    fp, f = _of(out, "fill_pass"), _of(out, "fill")
    c(len(f) == 1 and f[0]["outcome"] == "filled", f"fill {f}")
    c(len(fp) == 1 and fp[0]["pending_save"] == "completed"
      and fp[0]["trades_save"] == "raised" and fp[0]["return_reason"] is None
      and fp[0]["t_exit"] is None, f"pass {fp}")
    c(s.read("pending_signals.json") == [], "pending still holds the fill")
    c([t["symbol"] for t in s.read("virtual_trades.json")] == ["T1"],
      "trade persisted")
    store["fill_pass_expected"] = True
    sites = ao.coverage(store, "report", raw, out)
    c(sites["fill_pass"]["state"] == "reached_not_completed",
      f"coverage {sites['fill_pass']}")
    return c.fails


def _r3(s, point, want_trades):
    c = Check()
    raised, out, _ = _post_append(s, 0, trades_point=point)
    c(raised is not None, "save_trades failure did not propagate")
    f, fp = _of(out, "fill"), _of(out, "fill_pass")
    c(len(f) == 1 and f[0]["outcome"] == "retained_exception"
      and f[0]["trade_appended"] is True, f"fill {f}")
    c(len(fp) == 1 and fp[0]["pending_save"] == "completed"
      and fp[0]["trades_save"] == "raised", f"pass {fp}")
    c([p["symbol"] for p in s.read("pending_signals.json")] == ["PAX"],
      "pending not retained")
    left = s.read("virtual_trades.json")
    got = left if isinstance(left, str) else [t["symbol"] for t in left]
    c(got == want_trades, f"trades file {got} != {want_trades}")
    return c.fails


@control
def c48_r3_post_append_then_save_trades_open_fails(s):
    """Pending present, trade m = n (trade_not_persisted)."""
    return _r3(s, "open", ["T1"])


@control
def c49_r3_post_append_then_save_trades_partial(s):
    """Pending present, trades unreadable (INCONCLUSIVE at inspection)."""
    return _r3(s, "partial", "unreadable")


# ------------------------ queue expiry and maintenance ---------------------

@control
def c50_queue_maintenance_and_expiry(s):
    """maintain_queue over one expired and one valid entry, then over an
    empty queue: both expiry shapes and both ranked-save sites, returned."""
    c = Check()
    s.write("signal_queue.json", [_qentry("OLDQ", added=OLD), _qentry("KEP")])
    store = _reset_store()
    with patched(_load_params=_params()):
        tr.maintain_queue()
        s.write("signal_queue.json", [])
        tr.maintain_queue()
    _, out = _derive(store)
    ex, mq = _of(out, "queue_expiry"), _of(out, "queue_maintenance")
    c(len(ex) == 2 and len(mq) == 2, f"records {len(ex)}/{len(mq)}")
    if len(ex) == 2 and len(mq) == 2:
        c([r["symbol"] for r in ex[0]["removals"]] == ["OLDQ"],
          f"removals {ex[0]['removals']}")
        c(ex[0]["queue_save"] == "completed", f"expiry save {ex[0]['queue_save']}")
        c(ex[1]["removals"] == [] and ex[1]["queue_save"] == "not_attempted",
          f"empty expiry {ex[1]}")
        c(mq[0]["ranked_save_site"] == "survivors"
          and mq[0]["queue_save"] == mq[0]["ranked_save"] == "completed",
          f"maintenance {mq[0]}")
        c(mq[1]["ranked_save_site"] == "empty"
          and mq[1]["ranked_save"] == "completed"
          and mq[1]["queue_save"] == "not_attempted", f"maintenance {mq[1]}")
    c(all(r["record_complete"] for r in out), "record not complete")
    c([q["symbol"] for q in s.read("queue_ranked.json")] == [],
      "ranked file after the empty pass")
    _complete(c, out, "c50")
    return c.fails


@control
def c51_admission_raises_before_branch(s):
    """open_trade raises while reading state: the record exists, has no
    branch, is not complete, and names nothing missing (t_enter only)."""
    c = Check()
    (s.dir / "virtual_trades.json").write_text("{")
    store = _reset_store()
    raised = None
    with patched(_load_params=_params()):
        try:
            tr.open_trade(_sig("RBB"))
        except ValueError as e:
            raised = e
    c(raised is not None, "the read failure did not propagate")
    _, out = _derive(store)
    adm = _of(out, "admission")
    c(len(adm) == 1, f"admission records {len(adm)}")
    if adm:
        a = adm[0]
        c(a["branch"] is None and a["call_state"] == "raised_elsewhere",
          f"{a['branch']}/{a['call_state']}")
        c(ao.contract_state(a) == "entered", ao.contract_state(a))
        c(a["missing_fields"] == [] and a["record_complete"] is False,
          f"{a['missing_fields']}/{a['record_complete']}")
    return c.fails


@control
def c52_fill_demotion_raises_in_queue_save(s):
    """A demotion whose queue_signal raises at save_queue open: the fill
    entry is reached_not_completed, its pass did not return, and neither
    record is complete or names anything missing."""
    c = Check()
    s.write("pending_signals.json", [_sig("DRQ")])
    s.write("virtual_trades.json", FULL)
    s.write("signal_queue.json", [])
    store = _reset_store()
    raised = None
    with patched(_load_params=_params()), failpoint(tr.QUEUE_FILE, "open"):
        try:
            tr.execute_pending_signals()
        except Marker as e:
            raised = e
    c(raised is not None, "save_queue failure did not propagate")
    _, out = _derive(store)
    f, fp, qi = _of(out, "fill"), _of(out, "fill_pass"), _of(out, "queue_insert")
    c(len(f) == 1 and f[0]["outcome"] == "demoted_to_queue"
      and f[0]["entry_state"] == "reached_not_completed", f"fill {f}")
    c(len(fp) == 1 and fp[0]["return_reason"] is None, f"pass {fp}")
    c(len(qi) == 1 and qi[0]["queue_save"] == "raised", f"insert {qi}")
    c(all(r["record_complete"] is False and r["missing_fields"] == []
          for r in out), [(r["kind"], r["record_complete"], r["missing_fields"])
                          for r in out])
    c(s.read("pending_signals.json") != "absent"
      and [p["symbol"] for p in s.read("pending_signals.json")] == ["DRQ"],
      "pending changed")
    return c.fails


@control
def c53_promotion_raises_reading_state(s):
    """promote_queue raises at load_trades: the pass record exists with its
    base fields, did not return, and is not complete."""
    c = Check()
    (s.dir / "virtual_trades.json").write_text("{")
    store = _reset_store()
    raised = None
    with patched(_load_params=_params()):
        try:
            tr.promote_queue(source="scan")
        except ValueError as e:
            raised = e
    c(raised is not None, "the read failure did not propagate")
    _, out = _derive(store)
    pp = _of(out, "promotion_pass")
    c(len(pp) == 1, f"passes {len(pp)}")
    if pp:
        p = pp[0]
        c(p["return_reason"] is None and p["free_slots"] is None,
          f"{p['return_reason']}/{p['free_slots']}")
        c(ao.contract_state(p) == "not_returned", ao.contract_state(p))
        c(p["missing_fields"] == [] and p["record_complete"] is False,
          f"{p['missing_fields']}/{p['record_complete']}")
    return c.fails


# --------------------------------------------------------------------------
# Completeness contract (design §4.1, G2-C1). Run after the controls, over
# every store they derived (CORPUS).
# --------------------------------------------------------------------------

RAW_KEY = {("admission", "open_positions_before"): "open_trades_seen",
           ("admission", "pending_before"): "pending_seen"}

KCHECKS = []


def kcheck(fn):
    KCHECKS.append(fn)
    return fn


def _ablated(store, seq, changes, cand=None):
    """A copy of `store` with raw `changes` applied to record `seq` (to its
    candidate `cand` when given). Signal and queue-entry objects are shared,
    so identity links are unchanged."""
    recs = [dict(r) for r in store["records"]]
    if cand is None:
        recs[seq].update(changes)
    else:
        cands = [dict(x) for x in recs[seq]["candidates"]]
        cands[cand].update(changes)
        recs[seq]["candidates"] = cands
    s2 = dict(store)
    s2["records"] = recs
    return ao.derive_all(s2)[1][seq]


def _req(kind, state):
    return ao.REQUIRED_FIELDS[(kind, state)]


@kcheck
def k01_real_records_carry_every_required_field(c):
    """No real capture lost a required field, and record_complete is exactly
    'returned, nothing missing, no violation'."""
    kinds = set()
    for _, out in CORPUS:
        for r in out:
            kinds.add(r["kind"])
            c(r["missing_fields"] == [],
              f"{r['kind']}#{r['seq']} missing {r['missing_fields']}")
            want = ao.call_returned(r) and not r["contract_violations"]
            c(r["record_complete"] is want,
              f"{r['kind']}#{r['seq']} record_complete {r['record_complete']}")
    want = {"admission", "queue_insert", "fill", "fill_pass", "promotion_pass",
            "queue_expiry", "queue_maintenance"}
    c(kinds == want, f"record kinds {sorted(kinds)}")


COVERED = set()


@kcheck
def k02_each_required_field_ablated_is_named_missing(c):
    """For every real record and every field its state requires: null the
    raw source, re-derive, and the field must be named in missing_fields
    with record_complete false. Every table entry must be exercised."""
    def probe(store, r, kind, fields, state_of, name, cand=None):
        """Ablate each field alone; when that moves the record to another
        state, also ablate each field of the new state alongside it."""
        st = state_of(r)
        for f in fields(st):
            first = {RAW_KEY.get((kind, f), f): None}
            r2 = _ablated(store, r["seq"], first, cand)
            ok = name(f) in r2["missing_fields"] and \
                r2["record_complete"] is False
            c(ok, f"{kind}/{st}: ablated {f} -> {r2['missing_fields']}")
            if not ok:
                continue
            COVERED.add((kind, st, f))
            st2 = state_of(r2)
            for g in fields(st2):
                both = dict(first)
                both[RAW_KEY.get((kind, g), g)] = None
                r3 = _ablated(store, r["seq"], both, cand)
                ok3 = name(g) in r3["missing_fields"] and \
                    r3["record_complete"] is False
                c(ok3, f"{kind}/{st2} (via {f}): ablated {g} -> "
                       f"{r3['missing_fields']}")
                if ok3:
                    COVERED.add((kind, st2, g))

    for store, out in CORPUS:
        for r in out:
            if r["missing_fields"]:
                continue
            kind = r["kind"]
            probe(store, r, kind, lambda st, k=kind: _req(k, st),
                  ao.contract_state, lambda f: f)
            if kind != "promotion_pass":
                continue
            done = ao.call_returned(r)
            for i in range(len(r["candidates"])):
                probe(store, r, "candidate",
                      lambda st: _req("candidate", st),
                      lambda rr, i=i: ao.candidate_state(rr["candidates"][i],
                                                         done),
                      lambda f, i=i: f"candidates[{i}].{f}", cand=i)
    table = {(k, st, f) for (k, st), fs in ao.REQUIRED_FIELDS.items()
             for f in fs}
    left = sorted(table - COVERED)
    c(not left, f"table entries never exercised: {left}")


@kcheck
def k03_save_not_completed_on_returned_call_is_a_violation(c):
    """Every save that is unconditional on a returned call: mark it raised
    in the raw record and the record gets `<save>_not_completed_at_return`."""
    seen = set()
    for store, out in CORPUS:
        for r in out:
            if not r["record_complete"]:
                continue
            for f in ao._required_saves(r):
                r2 = _ablated(store, r["seq"], {f + "_completed": False})
                v = f"{f}_not_completed_at_return"
                c(v in r2["contract_violations"]
                  and r2["record_complete"] is False,
                  f"{r['kind']}#{r['seq']} {f}: {r2['contract_violations']}")
                seen.add((r["kind"], f, r.get("ranked_save_site")))
    want = {("fill_pass", "pending_save", None),
            ("fill_pass", "trades_save", None),
            ("promotion_pass", "queue_save", None),
            ("promotion_pass", "ranked_save", None),
            ("queue_insert", "queue_save", None),
            ("queue_expiry", "queue_save", None),
            ("queue_maintenance", "queue_save", "survivors"),
            ("queue_maintenance", "ranked_save", "survivors"),
            ("queue_maintenance", "ranked_save", "empty")}
    c(seen == want, f"save rules exercised {sorted(seen, key=str)}")


@kcheck
def k04_reviewer_probe_returned_pending_without_brackets(c):
    """Review v3 probe: a returned pending admission whose save flags are
    set but whose timestamps, position counts and max_positions are absent
    must not be complete."""
    hit = None
    for store, out in CORPUS:
        for r in out:
            if r["kind"] == "admission" and r["branch"] == "pending" \
                    and r["record_complete"]:
                hit = (store, r)
                break
        if hit:
            break
    c(hit is not None, "no complete pending admission in the corpus")
    if not hit:
        return
    store, r = hit
    r2 = _ablated(store, r["seq"], {"t_enter": None, "t_return": None,
                                    "open_trades_seen": None,
                                    "pending_seen": None,
                                    "max_positions": None})
    c(r2["missing_fields"] == ["t_enter", "open_positions_before",
                               "pending_before", "max_positions", "t_return"],
      f"missing {r2['missing_fields']}")
    c(r2["contract_violations"] == [], f"violations {r2['contract_violations']}")
    c(r2["pending_save"] == "completed" and r2["call_state"] == "returned",
      f"{r2['pending_save']}/{r2['call_state']}")
    c(r2["record_complete"] is False, "record_complete true")


# --------------------------------------------------------------------------

def main():
    failed = []
    for fn in CONTROLS:
        FEED.reset()
        s = Scratch()
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                fails = fn(s)
        except Exception:                                  # noqa: BLE001
            fails = ["harness raised:\n" + traceback.format_exc()]
        finally:
            s.close()
        print(f"  {'PASS' if not fails else 'FAIL'}  {fn.__name__}")
        for x in fails:
            print(f"        {x}")
        if fails:
            print("        production stdout:\n" + "\n".join(
                "          " + l for l in buf.getvalue().splitlines()))
        if fails:
            failed.append(fn.__name__)
    print("\n  anti-vacuity: the controls together reached every value")
    cov = [("fill outcomes", SEEN["fill"], ao.FILL_OUTCOMES),
           ("admission branches", SEEN["branch"], ao.ADMISSION_BRANCHES),
           ("candidate outcomes", SEEN["candidate"], ao.CANDIDATE_OUTCOMES),
           ("promotion returns", SEEN["pass_return"], ao.PASS_RETURNS)]
    for label, seen, want in cov:
        ok = seen == set(want)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: {len(seen)}/{len(want)}"
              + ("" if ok else f" missing {sorted(set(want) - seen)}"))
        if not ok:
            failed.append(label)
    print("\n  completeness contract (G2-C1) over every record the controls "
          "derived")
    for fn in KCHECKS:
        c = Check()
        try:
            fn(c)
        except Exception:                                  # noqa: BLE001
            c.fails.append("harness raised:\n" + traceback.format_exc())
        print(f"  {'PASS' if not c.fails else 'FAIL'}  {fn.__name__}")
        for x in c.fails[:12]:
            print(f"        {x}")
        if len(c.fails) > 12:
            print(f"        ... {len(c.fails) - 12} more")
        if c.fails:
            failed.append(fn.__name__)
    n = len(CONTROLS) + len(cov) + len(KCHECKS)
    if not (os.environ.get("ARES_CAPTURE_TRACKER")
            or os.environ.get("ARES_CAPTURE_OBSERVER")):
        print("\n  anti-vacuity: mutants of engine/tracker.py and "
              "engine/admission_observer.py; each named check must FAIL on "
              "its mutant")
        for label, ok in _run_mutants():
            print(f"  {'PASS' if ok else 'FAIL'}  {label}")
            n += 1
            if not ok:
                failed.append(label)
    print(f"\n  {n - len(failed)}/{n} admission capture checks passed")
    return 1 if failed else 0


# One statement replaced per mutant (by `pass` unless a replacement is given,
# same indentation), and the checks that must all notice it. Tracker mutants
# remove a capture statement; observer mutants restore the pre-C1
# completeness rule or drop the save-contradiction rule.
_T, _O = "engine/tracker.py", "engine/admission_observer.py"
MUTANTS = [
    (_T, 'fe["outcome"] = "filled"', None, ("c01_fill_filled",)),
    (_T, 'qi["evicted"].append(e)', None,
     ("c41_insert_then_evict_new_candidate",)),
    (_T, 'adm["pending_save_completed"] = True', None,
     ("c20_admission_pending",)),
    (_T, 'cand["outcome"] = "dropped_open_trade"', None,
     ("c34_promotion_every_candidate_outcome",)),
    (_T, 'pp["ranked_basis"] = "unranked_fallback"', None,
     ("c33_promotion_unranked_fallback",)),
    (_T, '_ADMISSION_EVAL["queue_events"].append((symbol, action, False))',
     None, ("c25_admission_queue_event_write_failed",)),
    (_T, 'fp["trades_save_attempted"] = True', None,
     ("c47_save_trades_fails_after_save_pending",)),
    (_T, 'fe["trade_appended"] = True', None,
     ("c45_fill_exception_after_append_retained",)),
    (_T, 'fp["max_positions"] = max_positions', None,
     ("k01_real_records_carry_every_required_field",)),
    (_O, 'complete = call_returned(rec) and not missing and not violations',
     'complete = not violations',
     ("k01_real_records_carry_every_required_field",
      "k02_each_required_field_ablated_is_named_missing",
      "k04_reviewer_probe_returned_pending_without_brackets")),
    (_O, 'violations.append(f"{f}_not_completed_at_return")', None,
     ("k03_save_not_completed_on_returned_call_is_a_violation",)),
]
_MUTANT_ENV = {_T: "ARES_CAPTURE_TRACKER", _O: "ARES_CAPTURE_OBSERVER"}


def _run_mutants():
    import subprocess
    results = []
    tmp = tempfile.mkdtemp(prefix="adm_capture_mut_")
    try:
        os.makedirs(os.path.join(tmp, "engine"))
        os.makedirs(os.path.join(tmp, "config"))
        shutil.copyfile(os.path.join(ROOT, "config", "strategy_params.json"),
                        os.path.join(tmp, "config", "strategy_params.json"))
        for rel, stmt, repl, must_fail in MUTANTS:
            src = open(os.path.join(ROOT, rel), encoding="utf-8").read()
            lines = src.splitlines(keepends=True)
            hits = [i for i, l in enumerate(lines) if l.strip() == stmt]
            label = (f"mutant {os.path.basename(rel)} `{stmt}` -> "
                     f"{', '.join(must_fail)} fail")
            if len(hits) != 1:
                results.append((f"{label} (statement found {len(hits)}x)",
                                False))
                continue
            i = hits[0]
            indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
            lines[i] = indent + (repl or "pass") + "\n"
            path = os.path.join(tmp, rel)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("".join(lines))
            env = dict(os.environ)
            env[_MUTANT_ENV[rel]] = path
            p = subprocess.run([sys.executable, os.path.abspath(__file__)],
                               capture_output=True, text=True, env=env,
                               cwd=ROOT, timeout=300)
            failed = {l.split()[1] for l in p.stdout.splitlines()
                      if l.startswith("  FAIL  ")}
            results.append((label, set(must_fail) <= failed
                            and p.returncode == 1))
            os.remove(path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return results


if __name__ == "__main__":
    sys.exit(main())
