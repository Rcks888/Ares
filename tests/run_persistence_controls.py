#!/usr/bin/env python3
"""Admission capture v1, milestone M3: persistence, save-chain and C-ENUM
controls (design §3.5, §4.2, §4.4, §7).

    python3 tests/run_persistence_controls.py

Four groups.

  *C-ENUM* (e01-e07): the functions in tools/admission_persistence.py are
  called on every tuple of their domains: the §3.5 table on 36 membership
  tuples and 48 trade-count tuples, the queue check on 36, the save chain on
  9, the fill composition on all 864. Each input must match exactly one table
  row and equal the expected table written by hand below. A table with one row
  removed, or with two rows overlapping, must be detected (e06).

  *Ten save sites x three failure points* (p01-p30): the REAL tracker and
  observer run in one process against a scratch git repository whose logs/
  is committed before and after the run, as the cron wrappers do. One save is
  made to fail at `open`, after a partial dump, or at close after a complete
  dump. Baseline is read from the parent commit, observed from the run's
  commit, intent and save fact from the derived records; the persistence
  function's verdict must be the one the design names.

  *Save chains and the expiry carrier, C-HANDOFF* (h01-h10): expiry `:379`
  failing at each point, in-process and through the real daily_report.py
  entry point (subprocess, scratch copy of the code); no expiry then `:485`;
  expiry completed then `:485` failing at each point; a synthetic
  `carrier_after_raise`; two expiries in one report; a malformed date_added.

  *Other persistence controls* (x01-x02): a file edited between the parent
  commit and the run to neither dump; the committed-state readers.

Only market data and indicators are stubbed (the capture controls' stubs).
Failure points shadow `open` / `json` in the tracker module's globals for one
call; production code is not edited.

Not here (M4, the inspection tool): concurrent_runs and baseline_ambiguous,
which need commits grouped by heartbeat; trade links over existing membership
across two report runs, which need episodes (§5); naming an
expiry_parse_failed removal in the inspection output.

ARES_PERSIST_MODULE replaces tools/admission_persistence.py, so a module that
cannot fail can be shown to fail this suite. ARES_PERSIST_ONLY runs one
control.
"""

import builtins
import contextlib
import importlib.util
import io
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MODULE = (os.environ.get("ARES_PERSIST_MODULE")
          or os.path.join(ROOT, "tools", "admission_persistence.py"))
MARK = "PersistenceControlMarker"


class Marker(RuntimeError):
    """Injected production failure."""


def _load_module(path):
    spec = importlib.util.spec_from_file_location("admission_persistence",
                                                  path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# Failure points: nth write-open of one file, in one module's globals
# --------------------------------------------------------------------------

class _CloseFails:
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


_ABSENT = object()


@contextlib.contextmanager
def failat(module, target, point, nth=1):
    """The nth `open(target, 'w')` in `module` fails at `point`: 'open'
    raises; 'partial' lets the dump write half its text, then raises;
    'close' raises after the complete dump."""
    target = Path(target)
    real_open = builtins.open
    st = {"writes": 0, "fired": False, "armed": None}

    def fake_open(path, mode="r", *a, **k):
        if Path(path) == target and "w" in mode:
            st["writes"] += 1
            if st["writes"] == nth:
                st["fired"] = True
                if point == "open":
                    raise Marker(f"{MARK}: open failed")
                fh = real_open(path, mode, *a, **k)
                if point == "close":
                    return _CloseFails(fh)
                st["armed"] = fh
                return fh
        return real_open(path, mode, *a, **k)

    class _Json:
        def dump(self, obj, fp, **kw):
            if st["armed"] is not None and fp is st["armed"]:
                st["armed"] = None
                text = json.dumps(obj, **kw)
                fp.write(text[: max(1, len(text) // 2)])
                fp.flush()
                raise Marker(f"{MARK}: dump failed after a partial write")
            return json.dump(obj, fp, **kw)

        def __getattr__(self, name):
            return getattr(json, name)

    saved = {k: module.__dict__.get(k, _ABSENT) for k in ("open", "json")}
    module.open = fake_open
    module.json = _Json()
    try:
        yield st
    finally:
        for k, v in saved.items():
            if v is _ABSENT:
                delattr(module, k)
            else:
                setattr(module, k, v)


# --------------------------------------------------------------------------
# Expected tables, written by hand (C-ENUM)
# --------------------------------------------------------------------------
# §3.5 on membership: intent baseline observed save -> verdict reason.
# P present, A absent, U unreadable; n not_attempted, c completed, r raised.
# "x" stands for the file name in the reason.

STATE_EXPECTED = """
P P P n consistent -
P P P c consistent -
P P P r consistent -
P P A n FAIL x_no_dump_matches
P P A c FAIL x_no_dump_matches
P P A r FAIL x_no_dump_matches
P P U n INCONCLUSIVE x_unreadable
P P U c INCONCLUSIVE x_unreadable
P P U r INCONCLUSIVE x_unreadable
A A A n consistent -
A A A c consistent -
A A A r consistent -
A A P n FAIL x_no_dump_matches
A A P c FAIL x_no_dump_matches
A A P r FAIL x_no_dump_matches
A A U n INCONCLUSIVE x_unreadable
A A U c INCONCLUSIVE x_unreadable
A A U r INCONCLUSIVE x_unreadable
P A P n FAIL x_changed_without_save
P A P c consistent -
P A P r consistent -
P A A n incident x_not_persisted
P A A c FAIL x_contradicts_completed_save
P A A r incident x_not_persisted
P A U n INCONCLUSIVE x_unreadable
P A U c INCONCLUSIVE x_unreadable
P A U r INCONCLUSIVE x_unreadable
A P A n FAIL x_changed_without_save
A P A c consistent -
A P A r consistent -
A P P n incident x_not_persisted
A P P c FAIL x_contradicts_completed_save
A P P r incident x_not_persisted
A P U n INCONCLUSIVE x_unreadable
A P U c INCONCLUSIVE x_unreadable
A P U r INCONCLUSIVE x_unreadable
"""

# §3.5 on trade counts (r5): baseline n, trade_appended, observed m, save.
COUNT_EXPECTED = """
0 F 0 n consistent -
0 F 0 c consistent -
0 F 0 r consistent -
0 F 1 n FAIL trade_no_dump_matches
0 F 1 c FAIL trade_no_dump_matches
0 F 1 r FAIL trade_no_dump_matches
0 F 2 n FAIL trade_no_dump_matches
0 F 2 c FAIL trade_no_dump_matches
0 F 2 r FAIL trade_no_dump_matches
0 F U n INCONCLUSIVE trade_unreadable
0 F U c INCONCLUSIVE trade_unreadable
0 F U r INCONCLUSIVE trade_unreadable
1 F 0 n FAIL trade_no_dump_matches
1 F 0 c FAIL trade_no_dump_matches
1 F 0 r FAIL trade_no_dump_matches
1 F 1 n consistent -
1 F 1 c consistent -
1 F 1 r consistent -
1 F 2 n FAIL trade_no_dump_matches
1 F 2 c FAIL trade_no_dump_matches
1 F 2 r FAIL trade_no_dump_matches
1 F U n INCONCLUSIVE trade_unreadable
1 F U c INCONCLUSIVE trade_unreadable
1 F U r INCONCLUSIVE trade_unreadable
0 T 0 n incident trade_not_persisted
0 T 0 c FAIL trade_contradicts_completed_save
0 T 0 r incident trade_not_persisted
0 T 1 n FAIL trade_changed_without_save
0 T 1 c consistent -
0 T 1 r consistent -
0 T 2 n FAIL trade_no_dump_matches
0 T 2 c FAIL trade_no_dump_matches
0 T 2 r FAIL trade_no_dump_matches
0 T U n INCONCLUSIVE trade_unreadable
0 T U c INCONCLUSIVE trade_unreadable
0 T U r INCONCLUSIVE trade_unreadable
1 T 0 n FAIL trade_no_dump_matches
1 T 0 c FAIL trade_no_dump_matches
1 T 0 r FAIL trade_no_dump_matches
1 T 1 n incident trade_not_persisted
1 T 1 c FAIL trade_contradicts_completed_save
1 T 1 r incident trade_not_persisted
1 T 2 n FAIL trade_changed_without_save
1 T 2 c consistent -
1 T 2 r consistent -
1 T U n INCONCLUSIVE trade_unreadable
1 T U c INCONCLUSIVE trade_unreadable
1 T U r INCONCLUSIVE trade_unreadable
"""

# §4.2 queue check: queue_memory observed queue_save -> verdict reason.
# K kept, E inserted_then_evicted, Q already_queued; "-" save is None.
QUEUE_EXPECTED = """
K P n FAIL queue_changed_without_save
K P c consistent -
K P r consistent -
K P - FAIL queue_save_fact_missing
K A n incident queue_not_persisted
K A c FAIL queue_contradicts_completed_save
K A r incident queue_not_persisted
K A - FAIL queue_save_fact_missing
K U n INCONCLUSIVE queue_unreadable
K U c INCONCLUSIVE queue_unreadable
K U r INCONCLUSIVE queue_unreadable
K U - FAIL queue_save_fact_missing
E P n FAIL queue_no_dump_matches
E P c FAIL queue_no_dump_matches
E P r FAIL queue_no_dump_matches
E P - FAIL queue_save_fact_missing
E A n consistent -
E A c consistent -
E A r consistent -
E A - FAIL queue_save_fact_missing
E U n INCONCLUSIVE queue_unreadable
E U c INCONCLUSIVE queue_unreadable
E U r INCONCLUSIVE queue_unreadable
E U - FAIL queue_save_fact_missing
Q P n FAIL queue_save_without_mutation
Q P c FAIL queue_save_without_mutation
Q P r FAIL queue_save_without_mutation
Q P - consistent -
Q A n FAIL queue_save_without_mutation
Q A c FAIL queue_save_without_mutation
Q A r FAIL queue_save_without_mutation
Q A - FAIL queue_no_dump_matches
Q U n FAIL queue_save_without_mutation
Q U c FAIL queue_save_without_mutation
Q U r FAIL queue_save_without_mutation
Q U - INCONCLUSIVE queue_unreadable
"""

# Save chain, owner and one carrier -> value or problem.
CHAIN_EXPECTED = """
n n not_attempted
n c completed
n r raised
c n completed
c c completed
c r completed
r n raised
r c carrier_after_raise
r r carrier_after_raise
"""

# §4.4 named combinations: executed_marked trade_appended pending_observed
# m-n n>=1 -> name. "*" in the n>=1 column matches any n.
FILL_NAMES_EXPECTED = """
Y Y A 1 * filled
Y Y A 0 * vanished_fill
Y Y P 0 * fill_not_persisted
N Y P 1 * booked_pending_retained
N Y P 0 * trade_not_persisted
Y N A 0 N removed
Y N A 0 Y removed_over_existing_trade
Y N P 0 * removal_not_persisted
N N P 0 * retained
"""

SAVE = {"n": "not_attempted", "c": "completed", "r": "raised", "-": None}
MEMB = {"P": "present", "A": "absent", "U": "unreadable"}
QMEM = {"K": "kept", "E": "inserted_then_evicted", "Q": "already_queued"}


def _rows(text):
    return [line.split() for line in text.strip().splitlines()]


def _res(verdict, reason):
    return (verdict, None if reason == "-" else reason)


def state_expected():
    return {(MEMB[i], MEMB[b], MEMB[o], SAVE[s]): _res(v, r)
            for i, b, o, s, v, r in _rows(STATE_EXPECTED)}


def count_expected():
    return {(int(n), a == "T", "unreadable" if m == "U" else int(m), SAVE[s]):
            _res(v, r) for n, a, m, s, v, r in _rows(COUNT_EXPECTED)}


def queue_expected():
    return {(QMEM[q], MEMB[o], SAVE[s]): _res(v, r)
            for q, o, s, v, r in _rows(QUEUE_EXPECTED)}


def chain_expected():
    return {(SAVE[o], SAVE[c]): v for o, c, v in _rows(CHAIN_EXPECTED)}


def names_expected():
    return [((e == "Y", a == "Y", MEMB[p], int(d),
              None if x == "*" else x == "Y"), name)
            for e, a, p, d, x, name in _rows(FILL_NAMES_EXPECTED)]


_RANK = {"consistent": 0, "incident": 1, "INCONCLUSIVE": 2, "FAIL": 3}


def fill_expected(e, a, po, n, m, ps, ts):
    """Oracle for the fill composition, built only from the hand tables."""
    st, ct = state_expected(), count_expected()
    pv = st[("absent" if e else "present", "present", po, ps)]
    tv = ct[(n, a, m, ts)]
    order = ts != "not_attempted" and ps != "completed"
    verdict = max([pv[0], tv[0]] + (["FAIL"] if order else []),
                  key=lambda v: _RANK[v])
    name = None
    if verdict in ("consistent", "incident"):
        hit = [nm for (k, nm) in names_expected()
               if isinstance(m, int) and k[:4] == (e, a, po, m - n)
               and (k[4] is None or k[4] == (n >= 1))]
        if len(hit) == 1:
            name = hit[0]
        else:
            verdict = "FAIL"
    link = (verdict in ("consistent", "incident") and tv[0] == "consistent"
            and isinstance(m, int) and m - n == 1)
    return verdict, name, link


SAVE3 = ("not_attempted", "completed", "raised")
OBS3 = ("present", "absent", "unreadable")
FILL_DOMAIN = list(itertools.product(
    (False, True), (False, True), OBS3, (0, 1), (0, 1, 2, "unreadable"),
    SAVE3, SAVE3))


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


def _first(items, n=4):
    return items[:n] + ([f"... {len(items) - n} more"] if len(items) > n
                        else [])


# --------------------------------------------------------------------------
# C-ENUM
# --------------------------------------------------------------------------

def enum_state(pm, table=None):
    """Mismatches of the §3.5 table against STATE_EXPECTED, plus any input
    (including invalid save values) that matches other than one row."""
    bad = []
    exp = state_expected()
    for key in itertools.product(("present", "absent"), ("present", "absent"),
                                 OBS3, SAVE3 + (None, "bogus")):
        rows = pm.state_rows(*key, table=table)
        if len(rows) != 1:
            bad.append(f"{key}: rows {rows}")
            continue
        got = pm.check_membership("x", *key, table=table)
        want = exp.get(key, ("FAIL", "x_save_fact_invalid"))
        if got != want:
            bad.append(f"{key}: {got} != {want}")
    return bad, len(exp)


def enum_count(pm, table=None):
    bad = []
    exp = count_expected()
    for key in itertools.product((0, 1), (False, True),
                                 (0, 1, 2, "unreadable"), SAVE3):
        n, a, m, s = key
        intent = n + 1 if a else n
        rows = pm.state_rows(intent, n, m, s, table=table)
        if len(rows) != 1:
            bad.append(f"{key}: rows {rows}")
            continue
        got = pm.check_trade_count(*key, table=table)
        if got != exp[key]:
            bad.append(f"{key}: {got} != {exp[key]}")
    return bad, len(exp)


def enum_fill(pm, table=None, names=None):
    bad, named = [], {}
    for key in FILL_DOMAIN:
        e, a, po, n, m, ps, ts = key
        try:
            got = pm.check_fill(e, a, po, n, m, ps, ts, table=table,
                                names=names)
        except Exception as exc:                          # noqa: BLE001
            bad.append(f"{key}: raised {type(exc).__name__}: {exc}")
            continue
        want = fill_expected(e, a, po, n, m, ps, ts)
        have = (got.get("verdict"), got.get("name"), got.get("new_link"))
        if have != want:
            bad.append(f"{key}: {have} != {want}")
        if got.get("verdict") in ("consistent", "incident"):
            hits = pm.fill_names(e, a, po, n, m, names=names)
            if len(hits) != 1:
                bad.append(f"{key}: names {hits}")
        if got.get("name"):
            named[got["name"]] = named.get(got["name"], 0) + 1
    return bad, named


@control
def e01_state_table_36_tuples(pm):
    """§3.5 membership: 2 x 2 x 3 x 3 = 36 tuples, each one row, each equal
    to STATE_EXPECTED; an invalid save value is one row too (FAIL)."""
    c = Check()
    bad, n = enum_state(pm)
    c(n == 36, f"expected table has {n} rows")
    c(not bad, f"mismatches {_first(bad)}")
    return c.fails


@control
def e02_trade_count_table_48_tuples(pm):
    """§3.5 on counts (r5): 2 x 2 x 4 x 3 = 48 tuples."""
    c = Check()
    bad, n = enum_count(pm)
    c(n == 48, f"expected table has {n} rows")
    c(not bad, f"mismatches {_first(bad)}")
    c(pm.check_trade_count(-1, False, 0, "completed")[0] == "FAIL",
      "negative count accepted")
    c(pm.check_trade_count(0, True, True, "completed")[0] == "FAIL",
      "bool count accepted")
    return c.fails


@control
def e03_queue_check_36_tuples(pm):
    """§4.2: queue_memory x observed x queue_save (with None) = 36 tuples;
    a baseline that contradicts the memory facts is baseline_memory_mismatch;
    an unknown memory value is FAIL."""
    c = Check()
    exp = queue_expected()
    c(len(exp) == 36, f"expected table has {len(exp)} rows")
    bad = []
    for key, want in exp.items():
        got = pm.check_queue(*key)
        if got != want:
            bad.append(f"{key}: {got} != {want}")
    c(not bad, f"mismatches {_first(bad)}")
    for mem, base in (("kept", "present"), ("inserted_then_evicted",
                                             "present"),
                      ("already_queued", "absent")):
        c(pm.check_queue(mem, "present", None if mem == "already_queued"
                         else "completed", baseline=base)
          == ("FAIL", "baseline_memory_mismatch"), f"{mem} baseline {base}")
    c(pm.check_queue("kept", "present", "completed", baseline="absent")
      == ("consistent", None), "agreeing baseline rejected")
    c(pm.check_queue(None, "present", "completed")
      == ("FAIL", "queue_memory_unknown"), "unknown memory")
    return c.fails


@control
def e04_save_chain_9_tuples(pm):
    """§3.5 chain reduction for an owner and one carrier, including
    carrier_after_raise; an invalid save value is save_fact_invalid."""
    c = Check()
    exp = chain_expected()
    c(len(exp) == 9, f"expected table has {len(exp)} rows")
    for (o, k), want in exp.items():
        v, problem = pm.chain_save(o, (k,))
        c((problem or v) == want and (v is None) == (problem is not None),
          f"chain ({o}, {k}): {(v, problem)} != {want}")
    c(pm.chain_save("completed") == ("completed", None), "owner alone")
    c(pm.chain_save("raised", ("not_attempted", "not_attempted"))
      == ("raised", None), "two idle carriers")
    c(pm.chain_save(None, ())[1] == "save_fact_invalid", "invalid owner")
    return c.fails


@control
def e05_fill_composition_864_tuples(pm):
    """§4.4: 2 x 2 x 3 x 2 x 4 x 3 x 3 = 864 tuples. Each returns a verdict
    (never raises), equals the oracle built from the hand tables, and a
    consistent or incident verdict has exactly one name. Every name in the
    §4.4 table is produced; booked_with_drop_event comes from `outcome`."""
    c = Check()
    c(len(FILL_DOMAIN) == 864, f"domain {len(FILL_DOMAIN)}")
    bad, named = enum_fill(pm)
    c(not bad, f"mismatches {_first(bad)}")
    want = {nm for _, nm in names_expected()}
    c(set(named) == want, f"names produced {sorted(named)}")
    r = pm.check_fill(True, True, "absent", 0, 1, "completed", "completed",
                      outcome="dropped_exception")
    c(r["name"] == "booked_with_drop_event" and r["new_link"],
      f"booked_with_drop_event {r}")
    r = pm.check_fill(True, True, "absent", 0, 1, "completed", "completed",
                      pending_baseline="absent")
    c(r["verdict"] == "FAIL" and "baseline_memory_mismatch" in r["reasons"],
      f"pending baseline absent {r}")
    r = pm.check_fill(True, True, "present", 0, 1, "raised", "completed")
    c(r["verdict"] == "FAIL" and
      "trades_save_before_pending_save_completed" in r["reasons"],
      f"source-impossible order {r}")
    print(f"        named: {dict(sorted(named.items()))}")
    return c.fails


def _without(rows, i):
    return rows[:i] + rows[i + 1:]


@control
def e06_table_mutations_are_detected(pm):
    """An observer with one row removed, or two rows overlapping, fails
    C-ENUM: every single-row deletion of the §3.5 table and of the §4.4 name
    table, and one overlapping row added to each."""
    c = Check()
    rows = tuple(pm.STATE_ROWS)
    for i, r in enumerate(rows):
        t = _without(rows, i)
        hit = (enum_state(pm, t)[0] or enum_count(pm, t)[0])
        c(bool(hit), f"§3.5 row {r[0]} removed: not detected")
    overlap = rows + (("overlap_unreadable",
                       lambda f, s: not f["readable"], "FAIL", "x"),)
    c(bool(enum_state(pm, overlap)[0]), "§3.5 overlap not detected")
    overlap2 = rows + (("overlap_eq_intent",
                        lambda f, s: f["readable"] and f["eq_intent"],
                        "consistent", None),)
    c(bool(enum_count(pm, overlap2)[0]), "§3.5 eq_intent overlap not detected")
    names = tuple(pm.FILL_NAMES)
    for i, (k, nm) in enumerate(names):
        c(bool(enum_fill(pm, names=_without(names, i))[0]),
          f"name row {nm} removed: not detected")
    dup = names + (((True, False, "absent", 0, None), "removed_any"),)
    c(bool(enum_fill(pm, names=dup)[0]), "name overlap not detected")
    return c.fails


@control
def e07_unknown_inputs_return_a_verdict(pm):
    """Total functions: malformed inputs give FAIL, never an exception."""
    c = Check()
    cases = [
        lambda: pm.check_membership("x", "maybe", "absent", "absent",
                                    "completed"),
        lambda: pm.check_membership("x", "present", "absent", "gone",
                                    "completed"),
        lambda: pm.check_trade_count(None, False, 0, "completed"),
        lambda: pm.check_queue("kept", "present", "bogus"),
        lambda: (pm.check_fill(None, True, "absent", 0, 1, "completed",
                               "completed")["verdict"], None),
        lambda: (pm.check_fill(True, True, "absent", 0, "x", "completed",
                               "completed")["verdict"], None),
    ]
    for i, fn in enumerate(cases):
        try:
            v = fn()
            c(v[0] == "FAIL", f"case {i}: {v}")
        except Exception as exc:                          # noqa: BLE001
            c(False, f"case {i} raised {type(exc).__name__}: {exc}")
    return c.fails


# --------------------------------------------------------------------------
# Real runs: scratch git repository, committed before and after each run
# --------------------------------------------------------------------------

_CAP = []


def _cap():
    """The capture controls' module: real tracker and observer, stubbed
    market data and indicators. Imported only for the real-run controls."""
    if not _CAP:
        sys.path.insert(0, HERE)
        import run_admission_capture_controls as cap
        _CAP.append(cap)
    return _CAP[0]


PENDING = "pending_signals.json"
TRADES = "virtual_trades.json"
QUEUE = "signal_queue.json"
RANKED = "queue_ranked.json"


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True)


def _git_init(path):
    _git(path, "init", "-q")
    for k, v in (("user.email", "controls@localhost"),
                 ("user.name", "persistence controls"),
                 ("commit.gpgsign", "false")):
        _git(path, "config", k, v)


def _commit(path, msg, what=("-A", "logs")):
    _git(path, "add", *what)
    r = _git(path, "commit", "-q", "--allow-empty", "-m", msg)
    if r.returncode != 0:
        raise RuntimeError(f"git commit failed: {r.stderr}")


def _show(path, rev, name):
    r = _git(path, "show", f"{rev}:logs/{name}")
    return r.stdout if r.returncode == 0 else None


class Repo:
    """logs/ in a scratch git repository; the tracker's paths point at it."""

    def __init__(self, cap):
        self.dir = Path(tempfile.mkdtemp(prefix="adm_persist_"))
        self.logs = self.dir / "logs"
        self.logs.mkdir()
        _git_init(self.dir)
        tr = cap.tr
        tr.LOGS_DIR = self.logs
        tr.TRADES_FILE = self.logs / TRADES
        tr.PENDING_FILE = self.logs / PENDING
        tr.QUEUE_FILE = self.logs / QUEUE
        tr.RANKED_FILE = self.logs / RANKED
        tr.QUEUE_EVENTS_FILE = self.logs / "queue_events.jsonl"

    def write(self, name, obj):
        (self.logs / name).write_text(json.dumps(obj, indent=2))

    def seed(self, pending=(), trades=(), queue=(), ranked=()):
        """Write the four state files and commit them as the parent."""
        for name, obj in ((PENDING, pending), (TRADES, trades),
                          (QUEUE, queue), (RANKED, ranked)):
            self.write(name, list(obj))
        _commit(self.dir, "parent")

    def states(self, pm, name):
        """(baseline, observed): the parent commit's file and the run's."""
        base_text = _show(self.dir, "HEAD~1", name)
        return (pm.parse_state(base_text, False),
                pm.parse_state(_show(self.dir, "HEAD", name),
                               base_text is not None))

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def _run(cap, repo, fn, target=None, point=None, nth=1, window=None):
    """One call of real code with one failure point; then the wrapper's
    commit of logs/ and the derived records."""
    store = cap._reset_store()
    patches = {"_load_params": cap._params()}
    if window is not None:
        patches["_fill_window_ok"] = cap._window(window)
    raised, st = None, {}
    with cap.patched(**patches), contextlib.redirect_stdout(io.StringIO()):
        ctx = (failat(cap.tr, repo.logs / target, point, nth) if point
               else contextlib.nullcontext({}))
        with ctx as st:
            try:
                fn()
            except Marker as exc:
                raised = exc
    _commit(repo.dir, "run")
    _, out = cap.ao.derive_all(store)
    return {"out": out, "raised": raised, "fired": st.get("fired"),
            "store": store}


def _one(c, out, kind):
    recs = [r for r in out if r["kind"] == kind]
    c(len(recs) == 1, f"{kind} records {len(recs)}")
    return recs[0] if len(recs) == 1 else None


def _syms(state):
    return state if state == "unreadable" else [x.get("symbol")
                                                for x in state]


def _left(c, point, observed, old, new, label):
    """The file each failure point leaves: the old dump, a prefix, the new."""
    want = {"open": old, "partial": "unreadable", "close": new}[point]
    c(_syms(observed) == want, f"{label} left {_syms(observed)} != {want}")


def _expect(c, got, want, label):
    c(tuple(got) == tuple(want), f"{label}: {tuple(got)} != {tuple(want)}")


def _fired(c, r, point):
    c(r["fired"] is True, f"failure point {point} not reached")
    c(r["raised"] is not None, "injected failure did not propagate")


INC = ("INCONCLUSIVE",)


# ---------------------------- the ten sites --------------------------------

def site_open_trade_pending(c, pm, cap, repo, point):
    """open_trade's save_pending: a new pending admission."""
    repo.seed()
    sig = cap._sig("NEW")
    r = _run(cap, repo, lambda: cap.tr.open_trade(sig), PENDING, point)
    _fired(c, r, point)
    adm = _one(c, r["out"], "admission")
    if not adm:
        return
    c(adm["branch"] == "pending" and adm["pending_appended"],
      f"branch {adm['branch']}")
    c(adm["pending_save"] == "raised", f"pending_save {adm['pending_save']}")
    c(adm["call_state"] == "raised_in_pending_save",
      f"call_state {adm['call_state']}")
    base, obs = repo.states(pm, PENDING)
    _left(c, point, obs, [], ["NEW"], "pending")
    b = pm.pending_membership(base, "NEW", cap.TODAY)
    o = pm.pending_membership(obs, "NEW", cap.TODAY)
    c(b == "absent", f"baseline {b}")
    _expect(c, pm.check_membership("pending", "present", b, o,
                                   adm["pending_save"]),
            {"open": ("incident", "pending_not_persisted"),
             "partial": ("INCONCLUSIVE", "pending_unreadable"),
             "close": ("consistent", None)}[point], "pending check")


def _fill_site(c, pm, cap, repo, point, target, saves, want):
    repo.seed(pending=[cap._sig("FIL")])
    r = _run(cap, repo, cap.tr.execute_pending_signals, target, point)
    _fired(c, r, point)
    p = _one(c, r["out"], "fill_pass")
    f = _one(c, r["out"], "fill")
    if not (p and f):
        return
    c(f["outcome"] == "filled" and f["executed_marked"]
      and f["trade_appended"], f"fill {f['outcome']}")
    c((p["pending_save"], p["trades_save"]) == saves,
      f"saves {(p['pending_save'], p['trades_save'])} != {saves}")
    bp, op = repo.states(pm, PENDING)
    bt, ot = repo.states(pm, TRADES)
    ed = f.get("entry_date")
    res = pm.check_fill(
        f["executed_marked"], f["trade_appended"],
        pm.pending_membership(op, "FIL", cap.TODAY),
        pm.trade_count(bt, "FIL", cap.TODAY, ed),
        pm.trade_count(ot, "FIL", cap.TODAY, ed),
        p["pending_save"], p["trades_save"], outcome=f["outcome"],
        pending_baseline=pm.pending_membership(bp, "FIL", cap.TODAY))
    _expect(c, (res["verdict"], res["name"], res["new_link"]), want[point],
            f"fill check {res.get('reasons')}")


def site_fill_pass_pending(c, pm, cap, repo, point):
    """execute_pending_signals' save_pending: trades_save never runs, so a
    close failure after the pending dump is the vanished fill (§2.2.1)."""
    _fill_site(c, pm, cap, repo, point, PENDING,
               ("raised", "not_attempted"),
               {"open": ("incident", "fill_not_persisted", False),
                "partial": ("INCONCLUSIVE", None, False),
                "close": ("incident", "vanished_fill", False)})


def site_fill_pass_trades(c, pm, cap, repo, point):
    """execute_pending_signals' save_trades after a completed save_pending:
    the vanished fill (§2.2.1) at open, a booked trade at close."""
    _fill_site(c, pm, cap, repo, point, TRADES,
               ("completed", "raised"),
               {"open": ("incident", "vanished_fill", False),
                "partial": ("INCONCLUSIVE", None, False),
                "close": ("consistent", "filled", True)})


def site_queue_signal(c, pm, cap, repo, point):
    """queue_signal's save_queue: a queued admission, slots full."""
    repo.seed(trades=cap.FULL)
    sig = cap._sig("QUE")
    r = _run(cap, repo, lambda: cap.tr.open_trade(sig), QUEUE, point)
    _fired(c, r, point)
    adm = _one(c, r["out"], "admission")
    if not adm:
        return
    q = adm.get("queue") or {}
    c(adm["branch"] == "queued" and q.get("queue_memory") == "kept",
      f"branch {adm['branch']} memory {q.get('queue_memory')}")
    c(q.get("queue_save") == "raised", f"queue_save {q.get('queue_save')}")
    c(adm["call_state"] == "raised_in_queue_save",
      f"call_state {adm['call_state']}")
    base, obs = repo.states(pm, QUEUE)
    _left(c, point, obs, [], ["QUE"], "queue")
    _expect(c, pm.check_queue(q.get("queue_memory"),
                              pm.queue_membership(obs, "QUE"),
                              q.get("queue_save"),
                              baseline=pm.queue_membership(base, "QUE")),
            {"open": ("incident", "queue_not_persisted"),
             "partial": ("INCONCLUSIVE", "queue_unreadable"),
             "close": ("consistent", None)}[point], "queue check")


def _maintenance(c, out):
    ex = [r for r in out if r["kind"] == "queue_expiry"]
    mq = _one(c, out, "queue_maintenance")
    c(len(ex) == 1, f"expiry records {len(ex)}")
    return (ex[0] if len(ex) == 1 else None), mq


def site_expire_queue(c, pm, cap, repo, point):
    """C-HANDOFF: expire_queue's save_queue (:379) fails inside
    maintain_queue. The exception leaves both functions, :485 and :488 have
    no attempted marker, and the committed queue is the old file, a prefix
    or the expiry dump: one §3.5 row each."""
    repo.seed(queue=[cap._qentry("OLDQ", added=cap.OLD), cap._qentry("KEP")])
    r = _run(cap, repo, cap.tr.maintain_queue, QUEUE, point)
    _fired(c, r, point)
    ex, mq = _maintenance(c, r["out"])
    if not (ex and mq):
        return
    c([x.get("symbol") for x in ex["removals"]] == ["OLDQ"],
      f"removals {ex['removals']}")
    c(ex["queue_save"] == "raised" and not ex["returned"],
      f"expiry {ex['queue_save']} returned {ex['returned']}")
    c(mq["queue_save"] == "not_attempted" and mq["ranked_save"] ==
      "not_attempted" and not mq["returned"],
      f"maintenance {mq['queue_save']}/{mq['ranked_save']}/{mq['returned']}")
    chain = pm.chain_save(ex["queue_save"], (mq["queue_save"],))
    c(chain == ("raised", None), f"chain {chain}")
    base, obs = repo.states(pm, QUEUE)
    _left(c, point, obs, ["OLDQ", "KEP"], ["KEP"], "queue")
    c(_show(repo.dir, "HEAD", RANKED) == _show(repo.dir, "HEAD~1", RANKED),
      "ranked file changed")
    _expect(c, pm.check_membership("queue", "absent",
                                   pm.queue_membership(base, "OLDQ"),
                                   pm.queue_membership(obs, "OLDQ"),
                                   chain[0]),
            {"open": ("incident", "queue_not_persisted"),
             "partial": ("INCONCLUSIVE", "queue_unreadable"),
             "close": ("consistent", None)}[point], "expiry check")


def site_maintain_queue(c, pm, cap, repo, point):
    """maintain_queue's save_queue (:485) with no expiry: its own drop."""
    repo.seed(queue=[cap._qentry("KEP"), cap._qentry("INV", price=40.0)])
    r = _run(cap, repo, cap.tr.maintain_queue, QUEUE, point)
    _fired(c, r, point)
    ex, mq = _maintenance(c, r["out"])
    if not (ex and mq):
        return
    c(ex["removals"] == [] and ex["queue_save"] == "not_attempted",
      f"expiry {ex}")
    c(mq["queue_save"] == "raised" and mq["ranked_save"] == "not_attempted",
      f"maintenance {mq['queue_save']}/{mq['ranked_save']}")
    base, obs = repo.states(pm, QUEUE)
    _left(c, point, obs, ["KEP", "INV"], ["KEP"], "queue")
    save = pm.chain_save(mq["queue_save"])[0]
    _expect(c, pm.check_membership("queue", "absent",
                                   pm.queue_membership(base, "INV"),
                                   pm.queue_membership(obs, "INV"), save),
            {"open": ("incident", "queue_not_persisted"),
             "partial": ("INCONCLUSIVE", "queue_unreadable"),
             "close": ("consistent", None)}[point], "drop check")
    _expect(c, pm.check_membership("queue", "present",
                                   pm.queue_membership(base, "KEP"),
                                   pm.queue_membership(obs, "KEP"), save),
            INC + ("queue_unreadable",) if point == "partial"
            else ("consistent", None), "survivor check")


def site_maintain_ranked_empty(c, pm, cap, repo, point):
    """maintain_queue's save_ranked([]) (:436). Ranked only orders
    promotion: no membership claim, the save fact and file are recorded."""
    repo.seed(ranked=[cap._qentry("RNK")])
    r = _run(cap, repo, cap.tr.maintain_queue, RANKED, point)
    _fired(c, r, point)
    _, mq = _maintenance(c, r["out"])
    if not mq:
        return
    c(mq["ranked_save_site"] == "empty" and mq["ranked_save"] == "raised"
      and mq["queue_save"] == "not_attempted",
      f"maintenance {mq['ranked_save_site']}/{mq['ranked_save']}")
    _, obs = repo.states(pm, RANKED)
    _left(c, point, obs, ["RNK"], [], "ranked")


def site_maintain_ranked_survivors(c, pm, cap, repo, point):
    """maintain_queue's save_ranked(survivors) (:488) after a completed
    :485; the queue keeps its survivor in every case."""
    repo.seed(queue=[cap._qentry("KEP")])
    r = _run(cap, repo, cap.tr.maintain_queue, RANKED, point)
    _fired(c, r, point)
    _, mq = _maintenance(c, r["out"])
    if not mq:
        return
    c(mq["ranked_save_site"] == "survivors" and mq["queue_save"] ==
      "completed" and mq["ranked_save"] == "raised",
      f"maintenance {mq['queue_save']}/{mq['ranked_save']}")
    _, obs = repo.states(pm, RANKED)
    _left(c, point, obs, [], ["KEP"], "ranked")
    base, qobs = repo.states(pm, QUEUE)
    _expect(c, pm.check_membership("queue", "present",
                                   pm.queue_membership(base, "KEP"),
                                   pm.queue_membership(qobs, "KEP"),
                                   mq["queue_save"]),
            ("consistent", None), "survivor check")


def _promotion_site(c, pm, cap, repo, point, target):
    repo.seed(queue=[cap._qentry("PRM")], ranked=[cap._qentry("PRM")])
    r = _run(cap, repo, lambda: cap.tr.promote_queue("scan"), target, point,
             window=True)
    _fired(c, r, point)
    pp = _one(c, r["out"], "promotion_pass")
    adm = _one(c, r["out"], "admission")
    if not (pp and adm):
        return None
    c([x["outcome"] for x in pp["candidates"]] == ["promoted"],
      f"candidates {[x['outcome'] for x in pp['candidates']]}")
    c(adm["branch"] == "pending" and adm["pending_save"] == "completed",
      f"admission {adm['branch']}/{adm['pending_save']}")
    bp, op = repo.states(pm, PENDING)
    pv = pm.check_membership("pending", "present",
                             pm.pending_membership(bp, "PRM", cap.TODAY),
                             pm.pending_membership(op, "PRM", cap.TODAY),
                             adm["pending_save"])
    _expect(c, pv, ("consistent", None),
            "completed pending save retracted by a later failure")
    bq, oq = repo.states(pm, QUEUE)
    qv = pm.check_membership("queue", "absent",
                             pm.queue_membership(bq, "PRM"),
                             pm.queue_membership(oq, "PRM"), pp["queue_save"])
    return pp, qv, op, oq


def site_promote_queue(c, pm, cap, repo, point):
    """promote_queue's save_queue (:609) after open_trade's completed
    save_pending: at `open` the symbol is both pending and queued (§7)."""
    got = _promotion_site(c, pm, cap, repo, point, QUEUE)
    if not got:
        return
    pp, qv, op, oq = got
    c(pp["queue_save"] == "raised" and pp["ranked_save"] == "not_attempted",
      f"pass {pp['queue_save']}/{pp['ranked_save']}")
    _left(c, point, oq, ["PRM"], [], "queue")
    if point == "open":
        c(pm.pending_membership(op, "PRM", cap.TODAY) == "present"
          and pm.queue_membership(oq, "PRM") == "present",
          "not both pending and queued")
    _expect(c, qv, {"open": ("incident", "queue_not_persisted"),
                    "partial": ("INCONCLUSIVE", "queue_unreadable"),
                    "close": ("consistent", None)}[point], "promotion check")


def site_promote_ranked(c, pm, cap, repo, point):
    """promote_queue's save_ranked (:610) after a completed :609."""
    got = _promotion_site(c, pm, cap, repo, point, RANKED)
    if not got:
        return
    pp, qv, _, _ = got
    c(pp["queue_save"] == "completed" and pp["ranked_save"] == "raised",
      f"pass {pp['queue_save']}/{pp['ranked_save']}")
    _expect(c, qv, ("consistent", None), "promotion check")
    _, obs = repo.states(pm, RANKED)
    _left(c, point, obs, ["PRM"], [], "ranked")


SITES = (
    ("open_trade_pending", site_open_trade_pending),
    ("fill_pass_pending", site_fill_pass_pending),
    ("fill_pass_trades", site_fill_pass_trades),
    ("queue_signal", site_queue_signal),
    ("expire_queue", site_expire_queue),
    ("maintain_queue", site_maintain_queue),
    ("maintain_ranked_empty", site_maintain_ranked_empty),
    ("maintain_ranked_survivors", site_maintain_ranked_survivors),
    ("promote_queue", site_promote_queue),
    ("promote_ranked", site_promote_ranked),
)
POINTS = ("open", "partial", "close")


def _real(body):
    """A control over real code in a fresh scratch repository."""
    def run(pm):
        cap = _cap()
        cap.FEED.reset()
        repo = Repo(cap)
        c = Check()
        try:
            body(c, pm, cap, repo)
        finally:
            repo.close()
        return c.fails
    return run


def _register_sites():
    i = 0
    for name, fn in SITES:
        for point in POINTS:
            i += 1
            ctl = _real(lambda c, pm, cap, repo, fn=fn, point=point:
                        fn(c, pm, cap, repo, point))
            ctl.__name__ = f"p{i:02d}_{name}_{point}"
            ctl.__doc__ = fn.__doc__
            CONTROLS.append(ctl)


_register_sites()


# ---------------------------- save chains, C-HANDOFF -----------------------

def h01_no_expiry_then_maintenance_saves(c, pm, cap, repo):
    """No expiry, then :485 saves: the chain is not_attempted -> completed;
    expiry has no removal (intent = baseline); the drop is checked
    against :485."""
    repo.seed(queue=[cap._qentry("KEP"), cap._qentry("INV", price=40.0)])
    r = _run(cap, repo, cap.tr.maintain_queue)
    ex, mq = _maintenance(c, r["out"])
    if not (ex and mq):
        return
    c(ex["removals"] == [] and ex["queue_save"] == "not_attempted",
      f"expiry {ex}")
    c(pm.chain_save(ex["queue_save"], (mq["queue_save"],))
      == ("completed", None), "chain not completed")
    base, obs = repo.states(pm, QUEUE)
    _expect(c, pm.check_membership("queue", "absent",
                                   pm.queue_membership(base, "INV"),
                                   pm.queue_membership(obs, "INV"),
                                   mq["queue_save"]),
            ("consistent", None), "drop check")


def _expiry_then_carrier_fails(c, pm, cap, repo, point):
    repo.seed(queue=[cap._qentry("OLDQ", added=cap.OLD), cap._qentry("KEP"),
                     cap._qentry("INV", price=40.0)])
    r = _run(cap, repo, cap.tr.maintain_queue, QUEUE, point, nth=2)
    _fired(c, r, point)
    ex, mq = _maintenance(c, r["out"])
    if not (ex and mq):
        return
    c(ex["queue_save"] == "completed" and mq["queue_save"] == "raised"
      and mq["ranked_save"] == "not_attempted",
      f"saves {ex['queue_save']}/{mq['queue_save']}/{mq['ranked_save']}")
    base, obs = repo.states(pm, QUEUE)
    _left(c, point, obs, ["KEP", "INV"], ["KEP"], "queue")
    chain = pm.chain_save(ex["queue_save"], (mq["queue_save"],))
    c(chain == ("completed", None), f"expiry chain {chain}")
    _expect(c, pm.check_membership("queue", "absent",
                                   pm.queue_membership(base, "OLDQ"),
                                   pm.queue_membership(obs, "OLDQ"),
                                   chain[0]),
            INC + ("queue_unreadable",) if point == "partial"
            else ("consistent", None), "expiry check")
    _expect(c, pm.check_membership("queue", "absent",
                                   pm.queue_membership(base, "INV"),
                                   pm.queue_membership(obs, "INV"),
                                   mq["queue_save"]),
            {"open": ("incident", "queue_not_persisted"),
             "partial": ("INCONCLUSIVE", "queue_unreadable"),
             "close": ("consistent", None)}[point], "drop check")


def h02_expiry_completed_then_485_open_fails(c, pm, cap, repo):
    """Expiry completed, :485 fails at open: the file is the expiry dump;
    the expiry is consistent, maintain_queue's drop = baseline."""
    _expiry_then_carrier_fails(c, pm, cap, repo, "open")


def h03_expiry_completed_then_485_partial_dump(c, pm, cap, repo):
    """Expiry completed, :485 leaves a prefix: every key is INCONCLUSIVE
    (§3.5 row 1), the expiry included."""
    _expiry_then_carrier_fails(c, pm, cap, repo, "partial")


def h04_expiry_completed_then_485_close_fails(c, pm, cap, repo):
    """Expiry completed, :485 fails after a complete dump: both consistent."""
    _expiry_then_carrier_fails(c, pm, cap, repo, "close")


def h05_synthetic_carrier_after_raise(c, pm, cap, repo):
    """Recorder input with :485 attempted after :379 raised: FAIL
    carrier_after_raise, whether or not the carrier completed."""
    repo.seed(queue=[cap._qentry("OLDQ", added=cap.OLD), cap._qentry("KEP")])
    r = _run(cap, repo, cap.tr.maintain_queue, QUEUE, "open")
    raw = [x for x in r["store"]["records"]
           if x.get("kind") == "queue_maintenance"]
    c(len(raw) == 1, f"raw maintenance records {len(raw)}")
    if len(raw) != 1:
        return
    for completed in (False, True):
        raw[0]["queue_save_attempted"] = True
        raw[0]["queue_save_completed"] = completed
        _, out = cap.ao.derive_all(r["store"])
        ex, mq = _maintenance(c, out)
        if ex and mq:
            c(ex["queue_save"] == "raised", f"expiry {ex['queue_save']}")
            got = pm.chain_save(ex["queue_save"], (mq["queue_save"],))
            c(got == (None, "carrier_after_raise"),
              f"carrier {mq['queue_save']}: {got}")


def h06_two_expiries_in_one_report(c, pm, cap, repo):
    """A free slot: check_open_trades expires first (it only prints the
    result, so it is not a carrier), maintain_queue reloads and expires
    nothing. Both expiries are recorded in order; the first one's chain
    explains the removal."""
    repo.seed(queue=[cap._qentry("OLDQ", added=cap.OLD), cap._qentry("KEP")])

    def report_order():
        cap.tr.check_open_trades()
        cap.tr.maintain_queue()
    r = _run(cap, repo, report_order)
    ex = [x for x in r["out"] if x["kind"] == "queue_expiry"]
    mq = _one(c, r["out"], "queue_maintenance")
    c(len(ex) == 2, f"expiry records {len(ex)}")
    if len(ex) != 2 or not mq:
        return
    c(ex[0]["seq"] < mq["seq"] and ex[1]["seq"] == mq["seq"] + 1,
      f"order {ex[0]['seq']}, {mq['seq']}, {ex[1]['seq']}")
    c([x.get("symbol") for x in ex[0]["removals"]] == ["OLDQ"]
      and ex[0]["queue_save"] == "completed", f"first expiry {ex[0]}")
    c(ex[1]["removals"] == [] and ex[1]["queue_save"] == "not_attempted",
      f"second expiry {ex[1]}")
    base, obs = repo.states(pm, QUEUE)
    c(_syms(obs) == ["KEP"], f"queue {_syms(obs)}")
    _expect(c, pm.check_membership("queue", "absent",
                                   pm.queue_membership(base, "OLDQ"),
                                   pm.queue_membership(obs, "OLDQ"),
                                   pm.chain_save(ex[0]["queue_save"])[0]),
            ("consistent", None), "first expiry check")


def h07_malformed_date_added(c, pm, cap, repo):
    """Probe H2: one expiry_parse_failed capture (no symbol, the active
    length at the failure), the committed queue lacks the entry, and the
    state difference is exactly that entry."""
    repo.seed(queue=[cap._qentry("BAD", added="not-a-date"),
                     cap._qentry("KEP")])
    r = _run(cap, repo, cap.tr.maintain_queue)
    ex, _ = _maintenance(c, r["out"])
    if not ex:
        return
    c(ex["removals"] == [{"action": "expiry_parse_failed", "active_len": 0}],
      f"removals {ex['removals']}")
    c(ex["queue_save"] == "completed", f"expiry save {ex['queue_save']}")
    base, obs = repo.states(pm, QUEUE)
    c(set(_syms(base)) - set(_syms(obs)) == {"BAD"},
      f"removed {set(_syms(base)) - set(_syms(obs))}")
    _expect(c, pm.check_membership("queue", "absent",
                                   pm.queue_membership(base, "BAD"),
                                   pm.queue_membership(obs, "BAD"),
                                   ex["queue_save"]),
            ("consistent", None), "parse-failure removal check")


for _fn in (h01_no_expiry_then_maintenance_saves,
            h02_expiry_completed_then_485_open_fails,
            h03_expiry_completed_then_485_partial_dump,
            h04_expiry_completed_then_485_close_fails,
            h05_synthetic_carrier_after_raise,
            h06_two_expiries_in_one_report,
            h07_malformed_date_added):
    _ctl = _real(_fn)
    _ctl.__name__, _ctl.__doc__ = _fn.__name__, _fn.__doc__
    CONTROLS.append(_ctl)


# ---------------------------- C-HANDOFF through the report entry point -----

def _report_trade(e2e, symbol, today, old):
    p = round(float(e2e._frame(symbol, today)["Close"].iloc[-1]), 4)
    return {"symbol": symbol, "status": "open", "entry_date": old,
            "entry_price": p, "shares": 3.0, "position_size": 3 * p,
            "stop_loss": round(p * 0.9, 2), "take_profit": round(p * 1.1, 2),
            "trailing_stop": round(p * 0.9, 2), "peak_price": p,
            "strategy": "mean_reversion", "confluence": 3}


def drive_report(src, point):
    """Subprocess body: the real daily_report.py as __main__ in a scratch
    copy, the e2e smoke's network stubs, :379 failing at `point`.
    Indicators are the identity, as in the capture controls: with real
    indicators the steadily rising stub series closes every seeded trade
    (emotional_extreme), the slot frees, and check_open_trades' expiry
    (:953) runs first instead of the one inside maintain_queue."""
    os.environ["ARES_SCHEDULED"] = "1"
    os.environ.pop("ARES_PARITY", None)
    sys.path.insert(0, HERE)
    import run_report_e2e_smoke as e2e
    sys.path.insert(0, src)
    e2e._install_stubs(src)
    import types
    ind = types.ModuleType("engine.indicators")
    ind.add_indicators = lambda df: df
    sys.modules["engine.indicators"] = ind
    from engine import tracker
    import runpy
    with failat(tracker, os.path.join(src, "logs", QUEUE), point, 1):
        runpy.run_path(os.path.join(src, "daily_report.py"),
                       run_name="__main__")


def _report_handoff(pm, point):
    c = Check()
    sys.path.insert(0, HERE)
    import run_report_e2e_smoke as e2e
    with tempfile.TemporaryDirectory() as tmp:
        src = e2e._prepare("B", tmp)
        today = e2e._last_weekday(datetime.now().astimezone().date())
        old = (today - timedelta(days=12)).isoformat()
        logs = os.path.join(src, "logs")
        seed = {
            TRADES: [_report_trade(e2e, s, today, old)
                     for s in ("AAA", "BBB", "CCC", "DDD", "EEE")],
            PENDING: [],
            QUEUE: [{"symbol": s, "strategy": "mean_reversion",
                     "price_at_signal": 30.0, "confluence": 3,
                     "date_added": d, "rsi_at_signal": 40.0,
                     "stdev_20": 0.02}
                    for s, d in (("OLDQ", old), ("GGG", today.isoformat()))],
            RANKED: [],
        }
        for name, obj in seed.items():
            with open(os.path.join(logs, name), "w") as fh:
                json.dump(obj, fh, indent=2)
        _git_init(src)
        _commit(src, "parent", ("-A",))
        p = subprocess.run([sys.executable, os.path.abspath(__file__),
                            "--drive-report", src, point],
                           capture_output=True, text=True, cwd=src,
                           timeout=300)
        _commit(src, "run")
        c(p.returncode != 0 and MARK in p.stderr,
          f"exit {p.returncode}: {p.stderr[-800:]}")
        c("ADMISSION-HEALTH" not in p.stderr, "admission health line")
        beats = [json.loads(x) for x in (_show(
            src, "HEAD", "admission_heartbeat_v1.jsonl") or "").splitlines()
            if x]
        recs = [json.loads(x) for x in (_show(
            src, "HEAD", "admission_v1.jsonl") or "").splitlines() if x]
        c(len(beats) == 1, f"heartbeats {len(beats)}")
        if beats:
            b = beats[0]
            c(b["decision_path"] == "report" and b["report_completed"] is False
              and MARK in (b.get("loop_exception") or ""),
              f"heartbeat {b.get('report_completed')} "
              f"{b.get('loop_exception')}")
            ps = b["call_sites"].get("promotion_scan", {})
            c(ps.get("state") == "not_expected" and ps.get("reason") ==
              "aborted_before", f"promotion_scan {ps}")
        ex = [r for r in recs if r["kind"] == "queue_expiry"]
        mq = [r for r in recs if r["kind"] == "queue_maintenance"]
        c(len(ex) == 1 and len(mq) == 1, f"records {len(ex)}/{len(mq)}")
        if len(ex) == 1 and len(mq) == 1:
            ex, mq = ex[0], mq[0]
            c(ex["queue_save"] == "raised"
              and [x.get("symbol") for x in ex["removals"]] == ["OLDQ"],
              f"expiry {ex['queue_save']} {ex['removals']}")
            c(mq["queue_save"] == "not_attempted" and mq["ranked_save"] ==
              "not_attempted" and mq["returned"] is False,
              f"maintenance {mq['queue_save']}/{mq['ranked_save']}")
            base_text = _show(src, "HEAD~1", QUEUE)
            base = pm.parse_state(base_text, False)
            obs = pm.parse_state(_show(src, "HEAD", QUEUE),
                                 base_text is not None)
            _left(c, point, obs, ["OLDQ", "GGG"], ["GGG"], "queue")
            chain = pm.chain_save(ex["queue_save"], (mq["queue_save"],))
            _expect(c, pm.check_membership(
                "queue", "absent", pm.queue_membership(base, "OLDQ"),
                pm.queue_membership(obs, "OLDQ"), chain[0]),
                {"open": ("incident", "queue_not_persisted"),
                 "partial": ("INCONCLUSIVE", "queue_unreadable"),
                 "close": ("consistent", None)}[point], "expiry check")
    return c.fails


def _register_report():
    for i, point in enumerate(POINTS, 8):
        def ctl(pm, point=point):
            return _report_handoff(pm, point)
        ctl.__name__ = f"h{i:02d}_report_entrypoint_379_{point}"
        ctl.__doc__ = ("C-HANDOFF through the real daily_report.py entry "
                       f"point, :379 failing at {point}: the report aborts, "
                       "its heartbeat records the abort, :485/:488 are not "
                       "attempted, and the committed queue gives one §3.5 "
                       "row.")
        CONTROLS.append(ctl)


_register_report()


# ---------------------------- other persistence controls -------------------

def x01_file_edited_between_commit_and_run(c, pm, cap, repo):
    """The trades file is edited after the parent commit to hold the key
    the run will book. The committed count is neither dump's: FAIL, because
    the baseline is the parent commit (r5), never the file the run read."""
    repo.seed(pending=[cap._sig("FIL")])
    entry = datetime.now().strftime("%Y-%m-%d")
    repo.write(TRADES, [{"symbol": "FIL", "signal_date": cap.TODAY,
                         "entry_date": entry, "status": "closed"}])
    r = _run(cap, repo, cap.tr.execute_pending_signals)
    p = _one(c, r["out"], "fill_pass")
    f = _one(c, r["out"], "fill")
    if not (p and f):
        return
    bt, ot = repo.states(pm, TRADES)
    bp, op = repo.states(pm, PENDING)
    n = pm.trade_count(bt, "FIL", cap.TODAY, f.get("entry_date"))
    m = pm.trade_count(ot, "FIL", cap.TODAY, f.get("entry_date"))
    c((n, m) == (0, 2), f"counts n={n} m={m}")
    res = pm.check_fill(f["executed_marked"], f["trade_appended"],
                        pm.pending_membership(op, "FIL", cap.TODAY), n, m,
                        p["pending_save"], p["trades_save"],
                        pending_baseline=pm.pending_membership(
                            bp, "FIL", cap.TODAY))
    c(res["verdict"] == "FAIL" and "trade_no_dump_matches" in res["reasons"]
      and not res["new_link"], f"fill check {res}")


_ctl = _real(x01_file_edited_between_commit_and_run)
_ctl.__name__ = x01_file_edited_between_commit_and_run.__name__
_ctl.__doc__ = x01_file_edited_between_commit_and_run.__doc__
CONTROLS.append(_ctl)


@control
def x02_committed_state_readers(pm):
    """Observed state (§3.5): a prefix, a non-list, a list of non-dicts and
    a file missing where the parent had it are unreadable; missing in both
    is empty. Keys: pending (symbol, date), queue symbol, trade count over
    (symbol, signal_date, entry_date)."""
    c = Check()
    U = "unreadable"
    for text, parent, want in (
            (None, True, U), (None, False, []), ("[\n  {", True, U),
            ('{"a": 1}', True, U), ("[1]", True, U), ("[]", True, []),
            ('[{"symbol": "A"}]', False, [{"symbol": "A"}])):
        got = pm.parse_state(text, parent)
        c(got == want, f"parse {text!r} parent={parent}: {got}")
    trades = [{"symbol": "A", "signal_date": "d1", "entry_date": "d2"},
              {"symbol": "A", "signal_date": "d1", "entry_date": "d3"},
              {"symbol": "A", "signal_date": "d0", "entry_date": "d2"}]
    c(pm.trade_count(trades, "A", "d1", "d2") == 1, "trade count key")
    c(pm.trade_count(U, "A", "d1", "d2") == U, "trade count unreadable")
    pend = [{"symbol": "A", "date": "d1"}]
    c(pm.pending_membership(pend, "A", "d1") == "present", "pending key")
    c(pm.pending_membership(pend, "A", "d2") == "absent", "pending date")
    c(pm.queue_membership([{"symbol": "A"}], "B") == "absent", "queue key")
    c(pm.queue_membership(U, "A") == U, "queue unreadable")
    return c.fails


# --------------------------------------------------------------------------
# Anti-vacuity: module mutants and stub modules
# --------------------------------------------------------------------------

MUTANTS = (
    ("not_persisted read as consistent",
     'INCIDENT, "{file}_not_persisted"),',
     'CONSISTENT, "{file}_not_persisted"),',
     "e01_state_table_36_tuples"),
    ("unreadable row without its save guard (overlap)",
     'lambda f, s: s in SAVE_VALUES and not f["readable"],',
     'lambda f, s: not f["readable"],',
     "e01_state_table_36_tuples"),
    ("already_queued accepts a save",
     '            return FAIL, "queue_save_without_mutation"',
     '            pass',
     "e03_queue_check_36_tuples"),
    ("kept implies a present baseline",
     '"kept": ("present", "absent"),',
     '"kept": ("present", "present"),',
     "p10_queue_signal_open"),
    ("carrier after a raised owner accepted",
     '    if owner == "raised" and any(s != "not_attempted" for s in '
     'carriers):',
     '    if False:',
     "h05_synthetic_carrier_after_raise"),
    ("save order rule dropped",
     '        reasons.append("trades_save_before_pending_save_completed")',
     '        pass',
     "e05_fill_composition_864_tuples"),
    ("removed ignores an existing trade (name overlap)",
     '((True, False, "absent", 0, False), "removed"),',
     '((True, False, "absent", 0, None), "removed"),',
     "e05_fill_composition_864_tuples"),
    ("INCONCLUSIVE ranked above FAIL",
     "_RANK = {CONSISTENT: 0, INCIDENT: 1, INCONCLUSIVE: 2, FAIL: 3}",
     "_RANK = {CONSISTENT: 0, INCIDENT: 1, INCONCLUSIVE: 3, FAIL: 2}",
     "e05_fill_composition_864_tuples"),
    ("missing committed file read as empty",
     "return UNREADABLE if parent_had_file else []",
     "return []",
     "x02_committed_state_readers"),
    ("trade count ignores entry_date",
     '               and t.get("entry_date") == entry_date)',
     '               )',
     "x02_committed_state_readers"),
)

STUBS = (
    ("always-consistent module",
     "\ndef check_state(*a, **k):\n    return CONSISTENT, None\n\n\n"
     "def chain_save(owner, carriers=()):\n    return 'completed', None\n"),
    ("always-FAIL module",
     "\ndef check_state(*a, **k):\n    return FAIL, 'stub'\n\n\n"
     "def chain_save(owner, carriers=()):\n    return None, 'stub'\n"),
)


def _subrun(path, only=None):
    env = dict(os.environ, ARES_PERSIST_MODULE=path, ARES_PERSIST_META="0")
    if only:
        env["ARES_PERSIST_ONLY"] = only
    return subprocess.run([sys.executable, os.path.abspath(__file__)],
                          capture_output=True, text=True, env=env,
                          timeout=900)


def _run_meta():
    real = Path(MODULE).read_text()
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, (label, old, new, must_fail) in enumerate(MUTANTS, 1):
            name = f"mutant {i:02d} ({label}) fails {must_fail}"
            if real.count(old) != 1:
                out.append((name, False, "anchor not found exactly once"))
                continue
            path = os.path.join(tmp, f"mutant_{i:02d}.py")
            Path(path).write_text(real.replace(old, new))
            p = _subrun(path, must_fail)
            out.append((name, p.returncode == 1 and
                        f"FAIL  {must_fail}" in p.stdout,
                        f"exit {p.returncode}: {p.stdout[-400:]}"))
        for i, (label, extra) in enumerate(STUBS, 1):
            path = os.path.join(tmp, f"stub_{i}.py")
            Path(path).write_text(real + extra)
            p = _subrun(path)
            tail = (p.stdout.strip().splitlines() or [""])[-1].strip()
            out.append((f"stub {i} ({label}) fails the suite: {tail}",
                        p.returncode == 1, f"exit {p.returncode}: {tail}"))
    return out


def main():
    pm = _load_module(MODULE)
    only = os.environ.get("ARES_PERSIST_ONLY")
    meta = os.environ.get("ARES_PERSIST_META", "1") == "1" and not only
    controls = [c for c in CONTROLS if not only or c.__name__ == only]
    if only and not controls:
        print(f"  no control named {only}")
        return 2
    failures = []
    for fn in controls:
        try:
            fails = fn(pm)
        except Exception as exc:                          # noqa: BLE001
            fails = [f"ERROR {type(exc).__name__}: {exc}"]
        if fails:
            failures.append(fn.__name__)
            print(f"  FAIL  {fn.__name__}")
            for f in _first(fails, 6):
                print(f"        {f}")
        else:
            print(f"  PASS  {fn.__name__}")
    total = len(controls)
    if meta:
        for name, ok, detail in _run_meta():
            total += 1
            print(f"  {'PASS' if ok else 'FAIL'}  {name}"
                  + ("" if ok else f"\n        {detail}"))
            if not ok:
                failures.append(name)
    print(f"\n  {total - len(failures)}/{total} persistence checks passed "
          f"({len(controls)} controls"
          + (f", {len(MUTANTS)} module mutants, {len(STUBS)} stub modules"
             if meta else "") + ")")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--drive-report":
        drive_report(sys.argv[2], sys.argv[3])
    else:
        sys.exit(main())
