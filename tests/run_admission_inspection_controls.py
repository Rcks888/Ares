#!/usr/bin/env python3
"""Controls for tools/inspect_admission_cycles.py (design §5, §7
*Inspection*; step 3 M4), mutation-tested like v7.

    python3 tests/run_admission_inspection_controls.py

Real chains. Each chain is a scratch git repository holding a copy of the
code (run_report_e2e_smoke._prepare "B") and seeded logs/. The real
daily_report.py and monitor_trades.py run as __main__ in a subprocess, and
each run's logs/ is committed with the cron wrapper's subject, so the tool
inspects exactly what it would see on the VPS. Stubbed: the network modules
(data feed, screener, signals), indicators (identity), the queue validator
and the fill-window clock (both deterministic per run). Tracker, observer,
monitor recorder and the entry points are the real copies.

  chain A  queue-heavy: a pre-activation pending filled, a demotion, an
           expiry, a maintenance drop, a scan admission queued, a monitor
           close and promotion, the promoted pending filled next report.
  chain B  scan-direct: two scan admissions, one filled next report, one
           retained for missing data.

Negative controls derive a commit from a real one (same parent, one file
changed, the cron subject) and name the rule that must fire. Unit controls
call the tool's functions on records taken from the chains.

Meta: every source mutant of the tool must make its named control fail, and
two stub tools (always PASS, always FAIL) must make the suite fail.

Env: ARES_INSPECT_TOOL replaces the tool path, ARES_INSPECT_ONLY runs one
control, ARES_INSPECT_META=0 skips mutants and stubs, ARES_INSPECT_CHAINS
reuses chains built by a parent run (JSON {name: dir}).
"""
import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL = (os.environ.get("ARES_INSPECT_TOOL")
        or os.path.join(ROOT, "tools", "inspect_admission_cycles.py"))
PERSIST = os.path.join(ROOT, "tools", "admission_persistence.py")

PENDING = "logs/pending_signals.json"
TRADES = "logs/virtual_trades.json"
QUEUE = "logs/signal_queue.json"
RANKED = "logs/queue_ranked.json"
RECORDS = "logs/admission_v1.jsonl"
HEARTBEAT = "logs/admission_heartbeat_v1.jsonl"
HEALTH = "logs/admission_health_v1.jsonl"
MON_HB = "logs/monitor_heartbeat_v1.jsonl"


# ---------------------------------------------------------------------------
# Driver (subprocess): one real entry point run
# ---------------------------------------------------------------------------

def _today():
    return datetime.now(timezone.utc).date()


def _frame(symbol):
    import pandas as pd
    days = pd.date_range(end=_today(), periods=80, freq="D")
    base = 20.0 + (sum(map(ord, symbol)) % 40)
    close = [round(base * (1 + 0.002 * i), 4) for i in range(80)]
    return pd.DataFrame({
        "Open": [round(c * 0.995, 4) for c in close],
        "High": [round(c * 1.01, 4) for c in close],
        "Low": [round(c * 0.99, 4) for c in close], "Close": close,
        "Volume": [1_000_000 + 1000 * i for i in range(80)]}, index=days)


def price(symbol):
    return round(float(_frame(symbol)["Close"].iloc[-1]), 2)


def signal(symbol, conf=3, strategy="mean_reversion"):
    return {"symbol": symbol, "date": _today().isoformat(),
            "price": price(symbol), "strategy": strategy, "trigger": "t",
            "regime": "r", "category": "c", "confluence": conf,
            "strength": "strong", "stdev_20": 0.02, "rsi": 40.0,
            "vol_ratio": 1.2, "screens": ["s1"]}


def _install(src, opts):
    def mod(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m
        return m

    no_data = set(opts.get("no_data", ()))
    live = opts.get("live", {})
    mod("engine.data_feed",
        load_stock=lambda s, max_age_hours=None: (None if s in no_data
                                                  else _frame(s)),
        get_live_price=lambda s: live.get(s),
        refresh_watchlist=lambda syms: None, prune_cache=lambda keep: None,
        disconnect_ib=lambda: None)
    sigs = opts.get("signals", [])
    universe = [s for s in sigs] or ["NONE"]
    mod("engine.screener", get_screened_symbols=lambda: (
        list(universe), {s: ["s1"] for s in universe}))

    def load_strategy_params():
        with open(os.path.join(src, "config", "strategy_params.json")) as fh:
            return json.load(fh)

    mod("engine.signals",
        scan_universe=lambda symbols, screen_data=None: [signal(s)
                                                         for s in sigs],
        load_watchlist=lambda: {"categories": {}},
        get_all_symbols=lambda wl: [],
        load_strategy_params=load_strategy_params)
    mod("engine.indicators", add_indicators=lambda df: df)


def _validator(opts):
    verdict = opts.get("validate", {})

    def validate(symbol, q, params, use_live=False):
        p = q.get("price_at_signal") or 30.0
        checks = {"drift_pct": 0.5, "rsi": 45.0, "ema20_ok": True,
                  "live": round(p * 1.005, 2)}
        kind = verdict.get(symbol, "valid")
        if kind == "valid":
            return True, "valid", checks
        if kind == "transient":
            return False, "transient: control", checks
        return False, "control: invalid", checks
    return validate


def drive(entry, src, opts_json):
    opts = json.loads(opts_json)
    os.environ["ARES_SCHEDULED"] = "1"
    os.environ.pop("ARES_PARITY", None)
    sys.path.insert(0, src)
    _install(src, opts)
    from engine import tracker
    tracker._fill_window_ok = lambda params: (opts.get("window", True),
                                              "control")
    tracker._validate_queued = _validator(opts)
    ctx = contextlib.nullcontext()
    if opts.get("fail"):
        sys.path.insert(0, HERE)
        import run_persistence_controls as pc
        target, point, nth = opts["fail"]
        ctx = pc.failat(tracker, os.path.join(src, target), point, nth)
    import runpy
    with ctx:
        runpy.run_path(os.path.join(src, entry), run_name="__main__")


# ---------------------------------------------------------------------------
# Chains
# ---------------------------------------------------------------------------

def _git(cwd, *args, env=None, data=None):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                       env=env, input=data)
    if r.returncode != 0:
        raise RuntimeError(f"git {args}: {r.stderr.decode()}")
    return r.stdout.decode().strip()


class Chain:
    def __init__(self, path):
        self.dir = path
        self.refs = {}
        meta = Path(path, ".chain.json")
        if meta.exists():
            self.refs = json.loads(meta.read_text())

    @classmethod
    def create(cls, tmp, name, seed):
        sys.path.insert(0, HERE)
        import run_report_e2e_smoke as e2e
        os.makedirs(os.path.join(tmp, name))
        src = e2e._prepare("B", os.path.join(tmp, name))
        for path, obj in seed.items():
            Path(src, path).write_text(json.dumps(obj, indent=2))
        _git(src, "init", "-q")
        for k, v in (("user.email", "controls@localhost"),
                     ("user.name", "inspection controls"),
                     ("commit.gpgsign", "false")):
            _git(src, "config", k, v)
        Path(src, ".gitignore").write_text("__pycache__/\n.chain.json\n")
        _git(src, "add", "-A")
        _git(src, "commit", "-q", "-m", "seed")
        c = cls(src)
        c.refs["R"] = _git(src, "rev-parse", "HEAD")
        return c

    def save(self):
        Path(self.dir, ".chain.json").write_text(json.dumps(self.refs))

    def run(self, label, entry, opts, commit=True):
        p = subprocess.run([sys.executable, os.path.abspath(__file__),
                            "--drive", entry, self.dir, json.dumps(opts)],
                           capture_output=True, text=True, cwd=self.dir,
                           timeout=300)
        if not commit:
            return p
        kind = "report" if entry == "daily_report.py" else "monitor"
        _git(self.dir, "add", "-A", "logs")
        stamp = datetime.now().strftime("%Y-%m-%d_%H:%M:%S")
        _git(self.dir, "commit", "-q", "--allow-empty", "-m",
             f"Ares V3 {kind} {stamp}")
        self.refs[label] = _git(self.dir, "rev-parse", "HEAD")
        self.refs[label + ".rc"] = p.returncode
        self.refs[label + ".err"] = p.stderr[-1500:]
        return p

    def show(self, ref, path):
        r = subprocess.run(["git", "show", f"{self.refs.get(ref, ref)}:{path}"],
                           cwd=self.dir, capture_output=True)
        return r.stdout if r.returncode == 0 else None

    def lines(self, ref, path):
        return [json.loads(x) for x in (self.show(ref, path) or b"")
                .decode().splitlines() if x]

    def derive(self, ref, changes, subject=None, parent=None):
        """A commit with `ref`'s tree, some files replaced (bytes) or
        removed (None), on `parent` (default: ref's parent)."""
        sha = self.refs.get(ref, ref)
        parent = self.refs.get(parent, parent) if parent else \
            _git(self.dir, "rev-parse", f"{sha}^")
        subject = subject or _git(self.dir, "log", "-1", "--format=%s", sha)
        idx = os.path.join(self.dir, ".git", "derive_index")
        env = dict(os.environ, GIT_INDEX_FILE=idx)
        _git(self.dir, "read-tree", sha, env=env)
        for path, data in changes.items():
            if data is None:
                _git(self.dir, "update-index", "--force-remove", path,
                     env=env)
                continue
            blob = _git(self.dir, "hash-object", "-w", "--stdin",
                        data=data)
            _git(self.dir, "update-index", "--add", "--cacheinfo",
                 f"100644,{blob},{path}", env=env)
        tree = _git(self.dir, "write-tree", env=env)
        os.remove(idx)
        return _git(self.dir, "commit-tree", tree, "-p", parent, "-m",
                    subject)

    def inspect(self, *refs, tool=None):
        shas = [self.refs.get(r, r) for r in refs]
        p = subprocess.run([sys.executable, tool or TOOL, *shas],
                           capture_output=True, text=True, cwd=self.dir,
                           timeout=300)
        return p.returncode, p.stdout + p.stderr


def _open_trade(symbol, days_ago=12):
    p = price(symbol)
    d = (_today() - timedelta(days=days_ago)).isoformat()
    return {"symbol": symbol, "status": "open", "entry_date": d,
            "signal_date": d, "entry_price": p, "shares": 3.0,
            "position_size": 3 * p, "stop_loss": round(p * 0.9, 2),
            "take_profit": round(p * 1.1, 2),
            "trailing_stop": round(p * 0.9, 2), "peak_price": p,
            "strategy": "mean_reversion", "confluence": 3}


def _qentry(symbol, days_ago=0):
    return {"symbol": symbol, "strategy": "mean_reversion",
            "price_at_signal": price(symbol), "confluence": 3,
            "date_added": (_today() - timedelta(days=days_ago)).isoformat(),
            "rsi_at_signal": 40.0, "stdev_20": 0.02}


def _pending(symbol):
    s = signal(symbol)
    s.pop("screens")
    return s


def build_a(tmp):
    """Queue-heavy chain: R -> rep1 -> mon1 -> rep2."""
    c = Chain.create(tmp, "A", {
        TRADES: [_open_trade(s) for s in ("OPA", "OPB", "OPC", "OPD")],
        PENDING: [_pending("PRE"), _pending("PRT")],
        QUEUE: [_qentry("QAA"), _qentry("QBB"), _qentry("QIN"),
                _qentry("QOLD", days_ago=12)],
        RANKED: []})
    c.run("rep1", "daily_report.py", {"signals": ["SCA"],
                                      "validate": {"QIN": "invalid"}})
    c.run("mon1", "monitor_trades.py", {"live": {
        "OPA": round(price("OPA") * 1.2, 2), "OPB": price("OPB"),
        "OPC": price("OPC"), "OPD": price("OPD"), "PRE": price("PRE")}})
    c.run("rep2", "daily_report.py", {"signals": []})
    c.save()
    return c


def build_b(tmp):
    """Scan-direct chain: R -> rep1 -> rep2."""
    c = Chain.create(tmp, "B", {TRADES: [], PENDING: [], QUEUE: [],
                                RANKED: []})
    c.run("rep1", "daily_report.py", {"signals": ["SDA", "SDB"]})
    c.run("rep2", "daily_report.py", {"signals": [], "no_data": ["SDB"]})
    c.run("rep3a", "daily_report.py", {"signals": [], "no_data": ["SDB"]},
          commit=False)
    c.run("rep3", "daily_report.py", {"signals": [], "no_data": ["SDB"]})
    c.save()
    return c


def build_c(tmp):
    """Failure chain: R -> rep1, whose first pending save (QOK's promotion)
    fails at open; the queue holds an entry with a malformed date_added."""
    bad = _qentry("QBAD")
    bad["date_added"] = "not-a-date"
    c = Chain.create(tmp, "C", {TRADES: [], PENDING: [],
                                QUEUE: [bad, _qentry("QOK")], RANKED: []})
    c.run("rep1", "daily_report.py", {"signals": ["SCX"],
                                      "fail": [PENDING, "open", 1]})
    c.save()
    return c


def build_a_tail(a):
    """Chain A, then a report whose maintenance save leaves a partial
    queue file."""
    a.run("rep3", "daily_report.py", {"signals": [],
                                      "fail": [QUEUE, "partial", 1]})
    a.save()


def chains():
    """Build every chain once, or reuse a parent run's (meta subprocesses)."""
    given = os.environ.get("ARES_INSPECT_CHAINS")
    if given:
        return {k: Chain(v) for k, v in json.loads(given).items()}, None
    tmp = tempfile.mkdtemp(prefix="adm_inspect_")
    out = {"A": build_a(tmp), "B": build_b(tmp), "C": build_c(tmp)}
    build_a_tail(out["A"])
    return out, tmp


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

CONTROLS = []


def control(fn):
    CONTROLS.append(fn)
    return fn


class Check:
    def __init__(self):
        self.fails = []

    def __call__(self, cond, msg):
        if not cond:
            self.fails.append(msg)


def jl(objs):
    return "".join(json.dumps(o, sort_keys=True) + "\n"
                   for o in objs).encode()


def parent(ch, ref):
    return _git(ch.dir, "rev-parse", f"{ch.refs.get(ref, ref)}^")


def edit_appended(ch, ref, path, fn):
    """`path` at `ref` with its appended lines passed through fn; the
    parent's bytes are kept exactly, so the change is append-only."""
    old = ch.show(parent(ch, ref), path) or b""
    new = ch.lines(ref, path)[len(ch.lines(parent(ch, ref), path)):]
    res = fn(new)
    return old + jl(new if res is None else res)


def edit_state(ch, ref, path, fn):
    obj = json.loads(ch.show(ref, path))
    res = fn(obj)
    return json.dumps(obj if res is None else res, indent=2).encode()


def expect(c, ch, refs, code, *subs, absent=()):
    rc, out = ch.inspect(*refs)
    c(rc == code, f"exit {rc}, want {code}: {out[-900:]}")
    for s in subs:
        c(s in out, f"missing {s!r}: {out[-900:]}")
    for s in absent:
        c(s not in out, f"unexpected {s!r}: {out[-900:]}")
    return out


def derive_expect(c, ch, ref, changes, refs, code, *subs, subject=None):
    x = ch.derive(ref, changes, subject=subject)
    i = refs.index(ref)
    return expect(c, ch, refs[:i] + [x], code, *subs)


def _kind(kind):
    return lambda r: r.get("kind") == kind


def _set(pred, **kv):
    def fn(lines):
        hit = [r for r in lines if pred(r)]
        assert hit, "no line to edit"
        hit[0].update(kv)
    return fn


def load_tool():
    spec = importlib.util.spec_from_file_location("inspect_tool", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TODAY = _today().isoformat()
A_REFS = ["R", "rep1", "mon1", "rep2"]
B_REFS = ["R", "rep1", "rep2"]


# ---------------------------- positive -------------------------------------

@control
def p01_chain_a_reconciles(c, ch):
    """Pre-activation fill, demotion, expiry, maintenance drop, scan queued,
    monitor close and promotion, the promoted pending filled."""
    a = ch["A"]
    c(all(a.refs.get(k + ".rc") == 0 for k in A_REFS[1:]),
      f"a run failed: {[a.refs.get(k + '.err') for k in A_REFS[1:]]}")
    out = expect(c, a, ["--verbose"] + A_REFS, 0, "RESULT: PASS",
                 "fill filled ('PRE'", f"unlinked_preactivation ('PRE', "
                 f"'{TODAY}')", "fill removed ('PRT'",
                 "queue consistent PRT (insert)",
                 "queue consistent QOLD (expired)",
                 "queue consistent QIN (maintenance)",
                 "queue consistent SCA (insert)",
                 "queue consistent QAA (candidate)",
                 f"episode opened ('QAA', '{TODAY}') (queue_monitor)",
                 "fill filled ('QAA'", "A2 QAA", "open episodes: 0")
    ev = a.lines("rep1", HEARTBEAT)[-1]["queue_events"]
    c(["QIN", "dropped", True] in ev and ["QOLD", "expired", True] in ev,
      f"chain A rep1 events {ev}")
    c("a2_observation unknown" not in out.split("A2 QAA")[1].split("\n")[0],
      "A2 QAA observation not derived from the candidate bracket")


@control
def p02_chain_b_reconciles(c, ch):
    b = ch["B"]
    expect(c, b, ["--verbose"] + B_REFS, 0, "RESULT: PASS",
           f"pending admitted ('SDA', '{TODAY}')",
           f"fill filled ('SDA', '{TODAY}')",
           f"fill retained ('SDB', '{TODAY}') (retained_no_data)",
           "A2 SDA", "episode admitted", "open episodes: 1")


@control
def p03_history_episode_carried(c, ch):
    """Inspection restarted at a later base: the pending's admission is in
    the record history, so its episode is not pre-activation."""
    expect(c, ch["B"], ["rep1", "rep2"], 0, "RESULT: PASS",
           "episode history", absent=("unlinked_preactivation",))


@control
def p04_a2_classifier_pinned(c, ch):
    """The tool's own A2 classifier equals the observer's over a grid that
    straddles the open, both DST regimes, open brackets and bar dates
    before, on and after the event session."""
    sys.path.insert(0, ROOT)
    from engine import admission_observer as ao
    t = load_tool()
    n = 0
    for day in ("2026-10-30", "2026-11-02", "2026-11-16", "2026-03-09"):
        o = ao.session_open_utc(day)
        for ds in (-7200, -1, 0, 1, 3600, 6 * 3600 + 31 * 60):
            for width in (None, 0, 1, 2, 600, 90000):
                s = o + timedelta(seconds=ds)
                e = None if width is None else s + timedelta(seconds=width)
                for bar_shift in (-1, 0, 1, 3):
                    bar = (datetime.fromisoformat(day).date()
                           + timedelta(days=bar_shift)).isoformat()
                    want = ao.classify_a2_checked(s, e, bar)
                    got = t.classify_a2(s, e, bar)
                    n += 1
                    c(tuple(got) == (want[0], list(want[1])),
                      f"{day} {ds} {width} {bar}: {got} != {want}")
        c(t.session_open_utc(day) == o, f"session open {day}")
    c(n == 576, f"grid {n}")


@control
def p05_frozen_copy_pinned(c, ch):
    """The tool's frozen vocabulary equals the observer's, and every real
    record and heartbeat in the chains fits the tool's key sets exactly."""
    sys.path.insert(0, ROOT)
    from engine import admission_observer as ao
    t = load_tool()
    for name in ("FILL_OUTCOMES", "FILL_REMOVES", "CANDIDATE_OUTCOMES",
                 "PROMOTION_REMOVES", "PASS_RETURNS"):
        c(getattr(t, name) == frozenset(getattr(ao, name)), name)
    c(t.MANIFEST_PATHS == {
        "decision_manifest": frozenset(ao.DECISION_MANIFEST),
        "classification_manifest": frozenset(ao.CLASSIFICATION_MANIFEST),
        "observation_manifest": frozenset(ao.OBSERVATION_MANIFEST)},
        "manifest paths")
    c(t.RECORD_SCHEMA_VERSION == ao.RECORD_SCHEMA_VERSION and
      t.HEARTBEAT_SCHEMA_VERSION == ao.HEARTBEAT_SCHEMA_VERSION, "versions")
    c(t.HEARTBEAT.endswith(ao.HEARTBEAT_NAME) and
      t.RECORDS.endswith(ao.RECORDS_NAME), "file names")
    kinds = set()
    for chn, tip in (("A", "rep2"), ("B", "rep3"), ("C", "rep1")):
        for hb in ch[chn].lines(tip, HEARTBEAT):
            c(set(hb) == t.HEARTBEAT_FIELDS,
              f"heartbeat keys {set(hb) ^ t.HEARTBEAT_FIELDS}")
        for r in ch[chn].lines(tip, RECORDS):
            kinds.add(r["kind"])
            req, opt = t.BODY[r["kind"]]
            keys = set(r) - t.ENVELOPE - t.COMMON
            c(req <= keys <= req | opt,
              f"{r['kind']} keys {sorted(keys ^ req)}")
            for cand in r.get("candidates", []):
                ck = set(cand)
                c(t.CANDIDATE[0] <= ck <= t.CANDIDATE[0] | t.CANDIDATE[1],
                  f"candidate keys {sorted(ck ^ t.CANDIDATE[0])}")
    c(kinds == set(t.BODY), f"kinds seen {sorted(kinds)}")


# ---------------------------- structural FAIL ------------------------------

@control
def f01_non_bot_commit(c, ch):
    b = ch["B"]
    derive_expect(c, b, "rep1", {}, B_REFS, 1,
                  "not a bot report or monitor commit", subject="manual fix")


@control
def f02_chain_must_be_direct(c, ch):
    expect(c, ch["A"], ["R", "mon1"], 1, "do not form a direct chain")
    expect(c, ch["A"], ["R", "rep1", "rep1"], 1, "given twice")


@control
def f03_append_only(c, ch):
    a = ch["A"]
    data = a.show("mon1", RECORDS).replace(b'"kind": "fill"',
                                           b'"kind": "fill" ', 1)
    derive_expect(c, a, "mon1", {RECORDS: data}, A_REFS, 1,
                  "is not append-only")


@control
def f04_record_key_set(c, ch):
    def drop(lines):
        r = [x for x in lines if x["kind"] == "admission"][0]
        del r["route"]
    b = ch["B"]
    derive_expect(c, b, "rep1", {RECORDS: edit_appended(b, "rep1", RECORDS,
                                                        drop)},
                  B_REFS, 1, "key set")


@control
def f05_non_finite_numbers(c, ch):
    b = ch["B"]
    for token in (b"NaN", b"1e999"):
        data = edit_appended(b, "rep2", RECORDS, _set(
            lambda r: r["kind"] == "fill" and r["outcome"] == "filled",
            entry_price="@X@")).replace(b'"@X@"', token)
        derive_expect(c, b, "rep2", {RECORDS: data}, B_REFS, 1,
                      "non-finite")


@control
def f06_contract_violation_or_missing_field(c, ch):
    b = ch["B"]
    for kv, sub in (({"contract_violations": ["x"]}, "contract_violations"),
                    ({"missing_fields": ["t_enter"]}, "missing_fields"),
                    ({"record_complete": False},
                     "record_complete disagrees")):
        data = edit_appended(b, "rep1", RECORDS, _set(_kind("admission"),
                                                       **kv))
        derive_expect(c, b, "rep1", {RECORDS: data}, B_REFS, 1, sub)


@control
def f07_heartbeat_recount(c, ch):
    b = ch["B"]

    def bump(lines):
        lines[-1]["records_attempted"] += 1

    def sites(lines):
        lines[-1]["call_sites"]["fill_pass"]["count"] = 2
    for fn, sub in ((bump, "records attempted"), (sites, "fill_pass count")):
        derive_expect(c, b, "rep2", {HEARTBEAT: edit_appended(
            b, "rep2", HEARTBEAT, fn)}, B_REFS, 1, sub)


@control
def f08_lineage(c, ch):
    b = ch["B"]

    def commit(lines):
        lines[-1]["repository_commit"] = "deadbee"
        for r in lines:
            r["repository_commit"] = "deadbee"

    def digest(lines):
        lines[-1]["decision_manifest"]["engine/tracker.py"] = "0" * 32
    derive_expect(c, b, "rep2", {
        HEARTBEAT: edit_appended(b, "rep2", HEARTBEAT, commit),
        RECORDS: edit_appended(b, "rep2", RECORDS, commit)},
        B_REFS, 1, "is not the commit the run started on")
    derive_expect(c, b, "rep2", {HEARTBEAT: edit_appended(
        b, "rep2", HEARTBEAT, digest)}, B_REFS, 1, "digest differs")


@control
def f09_time_window(c, ch):
    b = ch["B"]
    derive_expect(c, b, "rep2", {HEARTBEAT: edit_appended(
        b, "rep2", HEARTBEAT, _set(lambda h: True,
                                   started="2000-01-01T00:00:00+00:00"))},
        B_REFS, 1, "started is earlier than the parent commit")
    derive_expect(c, b, "rep2", {RECORDS: edit_appended(
        b, "rep2", RECORDS, _set(_kind("fill_pass"),
                                 t_exit="2099-01-01T00:00:00+00:00"))},
        B_REFS, 1, "outside [started, flushed_at]")


@control
def f10_cycle_identity(c, ch):
    b = ch["B"]
    old = b.lines("rep1", HEARTBEAT)[-1]["cycle_id"]

    def same(lines):
        for r in lines:
            r["cycle_id"] = old
    derive_expect(c, b, "rep2", {
        HEARTBEAT: edit_appended(b, "rep2", HEARTBEAT, same),
        RECORDS: edit_appended(b, "rep2", RECORDS, same)},
        B_REFS, 1, "repeats an earlier run")


@control
def f11_evidence_presence(c, ch):
    b = ch["B"]
    hb0 = b.show(parent(b, "rep2"), HEARTBEAT)
    rec0 = b.show(parent(b, "rep2"), RECORDS)
    derive_expect(c, b, "rep2", {HEARTBEAT: hb0}, B_REFS, 1,
                  "records without a heartbeat")
    derive_expect(c, b, "rep2", {HEARTBEAT: hb0, RECORDS: rec0}, B_REFS, 1,
                  "no admission heartbeat and no health line")
    derive_expect(c, b, "rep2", {HEALTH: jl([{"reason": "x"}])}, B_REFS, 1,
                  "partial flush")

    def gap(lines):
        return [r for r in lines if r["seq"] != 1]
    derive_expect(c, b, "rep2", {RECORDS: edit_appended(b, "rep2", RECORDS,
                                                        gap)},
                  B_REFS, 1, "record seq values")


# ---------------------------- state-diff reconciliation --------------------

@control
def r01_unrecorded_pending_added(c, ch):
    b = ch["B"]
    derive_expect(c, b, "rep2", {PENDING: edit_state(
        b, "rep2", PENDING, lambda st: st.append(_pending("ZZZ")))},
        B_REFS, 1, "unrecorded_pending_added ('ZZZ'",
        "rule 7: pending without an episode ('ZZZ'")


@control
def r02_unrecorded_pending_removed(c, ch):
    """SDB's retained entry with its pending gone from the file: the state
    diff and the §3.5 check both fail."""
    b = ch["B"]
    derive_expect(c, b, "rep2", {PENDING: edit_state(
        b, "rep2", PENDING, lambda st: [p for p in st
                                        if p["symbol"] != "SDB"])},
        B_REFS, 1, "unrecorded_pending_removed ('SDB'",
        "['pending_no_dump_matches']")


@control
def r03_unrecorded_trade(c, ch):
    b = ch["B"]

    def add(st):
        t = _open_trade("XYZ", 0)
        st.append(t)
    derive_expect(c, b, "rep2", {TRADES: edit_state(b, "rep2", TRADES, add)},
                  B_REFS, 1, "unrecorded_trade ('XYZ'", "rule 4: trade")


@control
def r04_existing_trade_key_fields(c, ch):
    a = ch["A"]

    def change(st):
        st[0]["entry_date"] = "2000-01-03"
    derive_expect(c, a, "rep2", {TRADES: edit_state(a, "rep2", TRADES,
                                                    change)},
                  A_REFS, 1, "trade_key_changed at 0")
    derive_expect(c, a, "rep2", {TRADES: edit_state(
        a, "rep2", TRADES, lambda st: st[:4])}, A_REFS, 1, "trades_removed")


@control
def r05_unrecorded_queue_change(c, ch):
    a = ch["A"]
    derive_expect(c, a, "mon1", {QUEUE: edit_state(
        a, "mon1", QUEUE, lambda st: st.append(_qentry("QNEW")))},
        A_REFS, 1, "unrecorded_queue_added QNEW")
    derive_expect(c, a, "mon1", {QUEUE: edit_state(
        a, "mon1", QUEUE, lambda st: [q for q in st
                                      if q["symbol"] != "QBB"])},
        A_REFS, 1, "unrecorded_queue_removed ['QBB']")


# ---------------------------- §3.5 per-file check on real runs -------------

@control
def r06_fill_contradicts_completed_save(c, ch):
    a = ch["A"]
    derive_expect(c, a, "rep1", {PENDING: edit_state(
        a, "rep1", PENDING, lambda st: st.append(_pending("PRE")))},
        A_REFS, 1, "pending_contradicts_completed_save")


@control
def r07_trade_no_dump_matches(c, ch):
    b = ch["B"]

    def dup(st):
        st.append(dict([t for t in st if t["symbol"] == "SDA"][0]))
    derive_expect(c, b, "rep2", {TRADES: edit_state(b, "rep2", TRADES, dup)},
                  B_REFS, 1, "trade_no_dump_matches")


@control
def r08_maintenance_and_expiry_removals(c, ch):
    """QIN (maintenance drop, :485) and QOLD (expiry, :379 carried by
    :485) kept in the committed queue: each contradicts a completed save."""
    a = ch["A"]
    for sym, days in (("QIN", 0), ("QOLD", 12)):
        derive_expect(c, a, "rep1", {QUEUE: edit_state(
            a, "rep1", QUEUE, lambda st: st.append(_qentry(sym, days)))},
            A_REFS, 1, f"queue_contradicts_completed_save {sym}")


@control
def r09_aborted_run_partial_queue(c, ch):
    """C-HANDOFF on the real entry point: maintain_queue's save (:485)
    leaves a partial queue file and the report aborts."""
    a = ch["A"]
    c(a.refs.get("rep3.rc", 0) != 0, "rep3 did not abort")
    expect(c, a, A_REFS + ["rep3"], 2, "aborted report run",
           "queue_unreadable", "RESULT: INCONCLUSIVE")


@control
def r10_admission_not_persisted(c, ch):
    """QOK's promotion: open_trade's save_pending raises before writing.
    The admission is an incident, the aborted run INCONCLUSIVE, the
    malformed queue entry's silent removal named."""
    cc = ch["C"]
    c(cc.refs.get("rep1.rc", 0) != 0, "C rep1 did not abort")
    expect(c, cc, ["R", "rep1"], 2,
           f"pending_not_persisted ('QOK', '{TODAY}')",
           "expiry_parse_failed_removal ['QBAD']", "aborted report run",
           absent=("episode opened",))


@control
def r11_concurrent_runs(c, ch):
    """Two report runs committed together: every key both touched is
    INCONCLUSIVE."""
    b = ch["B"]
    c(len(b.lines("rep3", HEARTBEAT)) - len(b.lines("rep2", HEARTBEAT)) == 2,
      "rep3 does not hold two heartbeats")
    expect(c, b, B_REFS + ["rep3"], 2, "2 runs in one commit",
           f"concurrent_runs pending ('SDB', '{TODAY}')")


# ---------------------------- cross-checks ---------------------------------

@control
def r12_monitor_pairing_and_save(c, ch):
    a = ch["A"]
    derive_expect(c, a, "mon1", {MON_HB: edit_appended(
        a, "mon1", MON_HB, _set(lambda h: True,
                                cycle_started_at="2000-01-01T00:00:00+00:00"))},
        A_REFS, 2, "peer_unpaired")
    derive_expect(c, a, "mon1", {MON_HB: edit_appended(
        a, "mon1", MON_HB, _set(lambda h: True,
                                trade_state_save_completed=False))},
        A_REFS, 1, "trade_state_save_completed False != promotion_expected")


@control
def r13_promoted_candidate_agrees(c, ch):
    a = ch["A"]

    def price_of(lines):
        pp = [r for r in lines if r["kind"] == "promotion_pass"][0]
        pp["candidates"][0]["promotion_price"] += 1
    derive_expect(c, a, "mon1", {RECORDS: edit_appended(a, "mon1", RECORDS,
                                                        price_of)},
                  A_REFS, 1, "disagrees with its admission record")


@control
def r14_recorder_call_site_verdict(c, ch):
    a = ch["A"]

    def bad(lines):
        lines[-1]["call_sites"]["scan_admissions"]["verdict"] = "FAIL"
    derive_expect(c, a, "rep1", {HEARTBEAT: edit_appended(
        a, "rep1", HEARTBEAT, bad)}, A_REFS, 1, "call site scan_admissions")


@control
def r15_a2_bar_before_event(c, ch):
    b = ch["B"]
    derive_expect(c, b, "rep2", {RECORDS: edit_appended(
        b, "rep2", RECORDS, _set(lambda r: r["kind"] == "fill" and
                                 r["outcome"] == "filled",
                                 bar_date="2000-01-03"))},
        B_REFS, 1, "a2_bar_before_event_session SDA")


@control
def r16_health_only_commit(c, ch):
    a = ch["A"]
    p = parent(a, "mon1")
    x = a.derive("mon1", {HEARTBEAT: a.show(p, HEARTBEAT),
                          RECORDS: a.show(p, RECORDS),
                          HEALTH: jl([{"reason": "recorder_write_failed"}])})
    expect(c, a, ["R", "rep1", x], 2, "admission evidence unavailable",
           f"pending added without evidence ('QAA', '{TODAY}')")


# ---------------------------- episode rules on real records ----------------
# The tool's reconcile() on a real cycle's records and states, with one
# fact changed that no real run can produce.

@contextlib.contextmanager
def at(ch):
    old = os.getcwd()
    os.chdir(ch.dir)
    try:
        yield
    finally:
        os.chdir(old)


def cycle(t, ch, ref):
    b = ch.refs[ref]
    a = parent(ch, ref)
    groups = t._groups(t.appended(a, b, HEARTBEAT, "u"),
                       t.appended(a, b, RECORDS, "u"), "u")
    base, obs = t._states(a, b)
    return groups, base, obs, t.appended(a, b, MON_HB, "u")


def episodes(t, ch, ref):
    sha = ch.refs[ref]
    return t.Episodes(sha, t.parse_lines(t.blob(sha, RECORDS) or b"", "h"),
                      t.parse_lines(t.blob(sha, HEARTBEAT) or b"", "h"))


def run_unit(ch, ref, prior, change):
    """reconcile() on `ref` after change(t, eps, groups, base, obs)."""
    t = load_tool()
    with at(ch):
        eps = episodes(t, ch, prior)
        groups, base, obs, mon = cycle(t, ch, ref)
        change(t, eps, groups, base, obs)
        V, notes = [], []
        t.reconcile(eps, groups, base, obs, mon, V, notes)
    return t, eps, V, notes


def has(c, V, verdict, sub):
    c(any(v == verdict and sub in r for v, r in V),
      f"no {verdict} {sub!r} in {V}")


@control
def u01_unchanged_cycle_is_consistent(c, ch):
    """The unit harness itself: B rep2 unchanged reconciles clean."""
    t, eps, V, notes = run_unit(ch["B"], "rep2", "rep1", lambda *a: None)
    c(t.cycle_code(V) == 0, f"{V}")
    c(any("fill filled ('SDA'" in r for _, r in V), f"{V}")


@control
def u02_rule1_admission_with_open_episode(c, ch):
    def change(t, eps, groups, base, obs):
        k = ("SDA", "2000-01-03")
        eps.open[k] = t.Episode(k, "preactivation")
        obs["pending"].append(_pending("SDA") | {"date": "2000-01-03"})
    t, eps, V, notes = run_unit(ch["B"], "rep1", "R", change)
    has(c, V, t.FAIL, f"rule 1: admission_with_open_episode ('SDA', "
                      f"'{TODAY}')")


@control
def u03_rule5_second_link(c, ch):
    def change(t, eps, groups, base, obs):
        eps.open[("SDA", TODAY)].link = ("SDA", TODAY, "2000-01-03")
    t, eps, V, notes = run_unit(ch["B"], "rep2", "rep1", change)
    has(c, V, t.FAIL, f"rule 5: second_trade_link ('SDA', '{TODAY}')")


def _over_existing(t, groups, base, obs, symbol):
    """SDB's fill turned into discarded_already_open over a trade with the
    same (symbol, signal_date) that was already in the file."""
    for r in groups[0][1]:
        if r["kind"] == "fill" and r["symbol"] == symbol:
            r.update(outcome="discarded_already_open", executed_marked=True)
    t0 = _open_trade(symbol, 3) | {"signal_date": TODAY}
    base["trades"].append(t0)
    obs["trades"].insert(len(base["trades"]) - 1, dict(t0))
    obs["pending"][:] = [p for p in obs["pending"] if p["symbol"] != symbol]
    return t0


@control
def u04_unowned_existing_trade(c, ch):
    def change(t, eps, groups, base, obs):
        _over_existing(t, groups, base, obs, "SDB")
    t, eps, V, notes = run_unit(ch["B"], "rep2", "rep1", change)
    has(c, V, t.FAIL, f"unowned_existing_trade ('SDB', '{TODAY}')")


@control
def u05_linked_existing_trade_closes(c, ch):
    """The same removal when the episode owns that trade: no FAIL, the
    episode closes linked."""
    def change(t, eps, groups, base, obs):
        t0 = _over_existing(t, groups, base, obs, "SDB")
        eps.open[("SDB", TODAY)].link = t.trade_key(t0)
    t, eps, V, notes = run_unit(ch["B"], "rep2", "rep1", change)
    c(t.cycle_code(V) == 0, f"{V}")
    c(("SDB", TODAY) not in eps.open, "SDB episode still open")


@control
def u06_rule7_episode_without_pending(c, ch):
    def change(t, eps, groups, base, obs):
        eps.open[("GHOST", TODAY)] = t.Episode(("GHOST", TODAY),
                                               "preactivation")
    t, eps, V, notes = run_unit(ch["B"], "rep2", "rep1", change)
    has(c, V, t.FAIL, f"rule 7: episode open without a pending ('GHOST'")


@control
def u07_rule8_key_reused_in_run(c, ch):
    """SDA filled, then admitted again by a later record in the same run:
    the fill's removal cannot be shown by the commit."""
    def change(t, eps, groups, base, obs):
        recs = groups[0][1]
        adm = [r for r in ch["B"].lines("rep1", RECORDS)
               if r["kind"] == "admission" and r["symbol"] == "SDA"][0]
        recs.append(dict(adm, seq=len(recs), parent_seq=None,
                         cycle_id=recs[0]["cycle_id"]))
        obs["pending"].append(_pending("SDA"))
    t, eps, V, notes = run_unit(ch["B"], "rep2", "rep1", change)
    has(c, V, t.INCONCLUSIVE, f"rule 8: key_reused_in_run ('SDA'")
    c(not any(v == t.FAIL for v, _ in V), f"{V}")


@control
def u08_baseline_ambiguous_after_raise(c, ch):
    """PRT's demotion insert save raised: maintenance's later queue claims
    have no baseline."""
    def change(t, eps, groups, base, obs):
        for r in groups[0][1]:
            if r["kind"] == "queue_insert" and r["symbol"] == "PRT":
                r["queue_save"] = "raised"
    t, eps, V, notes = run_unit(ch["A"], "rep1", "R", change)
    has(c, V, t.INCONCLUSIVE, "queue_baseline_ambiguous")


@control
def u09_carrier_after_raise(c, ch):
    """The expiry's save raised but the maintenance save that carries it
    completed: impossible in the code, so FAIL."""
    def change(t, eps, groups, base, obs):
        for r in groups[0][1]:
            if r["kind"] == "queue_expiry":
                r["queue_save"] = "raised"
    t, eps, V, notes = run_unit(ch["A"], "rep1", "R", change)
    has(c, V, t.FAIL, "carrier_after_raise QOLD")


@control
def u10_unknown_episode_is_inconclusive(c, ch):
    def change(t, eps, groups, base, obs):
        eps.forget(("SDA", TODAY))
    t, eps, V, notes = run_unit(ch["B"], "rep2", "rep1", change)
    has(c, V, t.INCONCLUSIVE, f"episode_unknown ('SDA', '{TODAY}')")
    c(not any(v == t.FAIL for v, _ in V), f"{V}")


@control
def u11_rule2_fill_without_episode(c, ch):
    def change(t, eps, groups, base, obs):
        eps.open.pop(("SDA", TODAY))
    t, eps, V, notes = run_unit(ch["B"], "rep2", "rep1", change)
    has(c, V, t.FAIL, f"rule 2: fill_without_episode ('SDA', '{TODAY}')")


@control
def u12_a2_brackets_per_route(c, ch):
    """A2 from the episode's own brackets: admission from t_enter/t_return,
    observation from the report's scan bracket (scan_direct) or the
    promoted candidate's validation bracket (queue routes). The brackets
    are moved across the open so the two values differ."""
    def at_open(t, secs):
        return (t.session_open_utc(TODAY) + timedelta(seconds=secs)
                ).isoformat()

    def scan(t, eps, groups, base, obs):
        ep = eps.open[("SDA", TODAY)]
        ep.adm = dict(ep.adm, t_enter=at_open(t, -120),
                      t_return=at_open(t, -60))
        ep.hb = dict(ep.hb, t_scan_start=at_open(t, 60),
                     t_scan_end=at_open(t, 120))

    def queue(t, eps, groups, base, obs):
        ep = eps.open[("QAA", TODAY)]
        ep.adm = dict(ep.adm, t_enter=at_open(t, 60),
                      t_return=at_open(t, 120))
        ep.cand = dict(ep.cand, t_validate_start=at_open(t, -120),
                       t_validate_end=at_open(t, -60))
    for chn, ref, prior, fn, sym, adm, obs_v in (
            ("B", "rep2", "rep1", scan, "SDA", "before_open", "after_open"),
            ("A", "rep2", "mon1", queue, "QAA", "after_open", "before_open")):
        t, eps, V, notes = run_unit(ch[chn], ref, prior, fn)
        want = (f"A2 {sym} bar {TODAY}: a2_admission {adm} "
                f"(after_open_seconds {-120 if adm == 'before_open' else 60})"
                f", a2_observation {obs_v}")
        c(any(n.startswith(want) for n in notes), f"{want!r} not in {notes}")
        c(t.cycle_code(V) == 0, f"{V}")


# ---------------------------------------------------------------------------
# Meta: mutants of the tool, stub tools
# ---------------------------------------------------------------------------

MUTANTS = (
    ("append-only check dropped",
     "    if not new.startswith(old):", "    if False:",
     "f03_append_only"),
    ("non-finite numbers accepted",
     "        return math.isfinite(obj)", "        return True",
     "f05_non_finite_numbers"),
    ("repository_commit not checked",
     '    if not a.startswith(hb["repository_commit"]):', "    if False:",
     "f08_lineage"),
    ("run window not checked",
     "    if start < commit_time(a) - CLOCK_SLACK:", "    if False:",
     "f09_time_window"),
    ("identity repeats accepted",
     'if hb["cycle_id"] in seen_ids or hb["started"] in seen_starts:',
     "if False:", "f10_cycle_identity"),
    ("trade key fields not compared",
     "            if trade_key(a[i]) != trade_key(b[i]):",
     "            if False:",
     "r04_existing_trade_key_fields"),
    ("silent queue removals within budget forever",
     "        if len(silent) > parse_failed:",
     "        if len(silent) > parse_failed + 1:",
     "r05_unrecorded_queue_change"),
    ("rule 1 dropped",
     "        if eps.symbol_busy(key[0]):", "        if False:",
     "u02_rule1_admission_with_open_episode"),
    ("second link allowed",
     "        if ep.link is not None:", "        if False:",
     "u03_rule5_second_link"),
    ("unowned existing trade accepted",
     '            if ep.link is None and ep.origin != "preactivation":',
     "            if False:", "u04_unowned_existing_trade"),
    ("rule 7 dropped",
     "    for k in sorted(set(eps.open) - held, key=str):",
     "    for k in []:", "u06_rule7_episode_without_pending"),
    ("reused key checked as if final",
     "            elif m.intent != out[id(m)][0]:", "            elif False:",
     "u07_rule8_key_reused_in_run"),
    ("raised save leaves the baseline usable",
     "    if baseline == AMBIGUOUS:", "    if False:",
     "u08_baseline_ambiguous_after_raise"),
    ("carrier problem ignored",
     "    if m.problem:", "    if False:", "u09_carrier_after_raise"),
    ("concurrent runs ignored",
     "    concurrent = {fk for fk, n in touched.items() if n > 1}",
     "    concurrent = set()", "r11_concurrent_runs"),
    ("aborted run accepted",
     '    if hb[f"{path}_completed"] is not True:', "    if False:",
     "r10_admission_not_persisted"),
    ("A2 bracket end ignored",
     "    if bar_date > session or end < o:", "    if bar_date > session:",
     "p04_a2_classifier_pinned"),
    ("frozen admission body drifted",
     '"signal_fields_absent", "from_queue", "route",',
     '"signal_fields_absent", "from_queue",',
     "p05_frozen_copy_pinned"),
    ("monitor save vs promotion not compared",
     '    if saved is not hb["promotion_expected"]:', "    if False:", "r12_monitor_pairing_and_save"),
    ("promoted candidate not compared",
     '            if (a["signal_date"], a["decision_price"]) != (',
     '            if False and (a["signal_date"], a["decision_price"]) != (',
     "r13_promoted_candidate_agrees"),
    ("state diff ignores added pendings",
     '                out.append((FAIL, f"unrecorded_pending_added {k}"))',
     "                pass", "r01_unrecorded_pending_added"),
    ("rule 2 dropped",
     '        V.append((FAIL, f"rule 2: fill_without_episode {key}"))',
     "        return", "u11_rule2_fill_without_episode"),
    ("rule 4 dropped",
     "            if added[k] != links[k]:", "            if False:",
     "r03_unrecorded_trade"),
    ("unknown episodes treated as missing",
     "    if key in eps.unknown:", "    if False:",
     "u10_unknown_episode_is_inconclusive"),
    ("health gap accepts added pendings",
     '        V.append((INCONCLUSIVE, f"pending added without evidence {k}"))',
     "        pass", "r16_health_only_commit"),
    ("scan_direct observation from the candidate bracket",
     '    elif ep.adm.get("route") == "scan_direct":',
     '    elif ep.adm.get("route") != "scan_direct":',
     "u12_a2_brackets_per_route"),
    ("preactivation fills reported as linked coverage",
     '        if ep.origin == "preactivation":\n            V.append((INCIDENT',
     '        if False:\n            V.append((INCIDENT',
     "p01_chain_a_reconciles"),
)

STUBS = (
    ("always-PASS tool",
     "import sys\nprint('RESULT: PASS')\nsys.exit(0)\n"),
    ("always-FAIL tool",
     "import sys\nprint('RESULT: FAIL')\nsys.exit(1)\n"),
)


def _subrun(tool, chain_dirs, only=None):
    env = dict(os.environ, ARES_INSPECT_TOOL=tool, ARES_INSPECT_META="0",
               ARES_INSPECT_CHAINS=json.dumps(chain_dirs))
    if only:
        env["ARES_INSPECT_ONLY"] = only
    return subprocess.run([sys.executable, os.path.abspath(__file__)],
                          capture_output=True, text=True, env=env,
                          timeout=1800)


def _run_meta(chain_dirs):
    real = Path(TOOL).read_text()
    names = {f.__name__ for f in CONTROLS}
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy(PERSIST, tmp)
        for i, (label, old, new, must_fail) in enumerate(MUTANTS, 1):
            name = f"mutant {i:02d} ({label}) fails {must_fail}"
            if real.count(old) != 1 or must_fail not in names:
                out.append((name, False, "anchor not found exactly once"
                            if real.count(old) != 1 else "no such control"))
                continue
            path = os.path.join(tmp, f"mutant_{i:02d}.py")
            Path(path).write_text(real.replace(old, new))
            p = _subrun(path, chain_dirs, must_fail)
            out.append((name, p.returncode == 1 and
                        f"FAIL  {must_fail}" in p.stdout,
                        p.stdout[-400:]))
        for label, text in STUBS:
            path = os.path.join(tmp, "stub.py")
            Path(path).write_text(text)
            p = _subrun(path, chain_dirs)
            out.append((f"stub ({label}) makes the suite fail",
                        p.returncode == 1, p.stdout[-400:]))
    return out


def main():
    ch, tmp = chains()
    only = os.environ.get("ARES_INSPECT_ONLY")
    failed = 0
    ran = 0
    for fn in CONTROLS:
        if only and fn.__name__ != only:
            continue
        ran += 1
        c = Check()
        try:
            fn(c, ch)
        except (Exception, SystemExit) as e:
            c.fails.append(f"{type(e).__name__}: {e}")
        if c.fails:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            for f in c.fails[:6]:
                print(f"      {f}")
        else:
            print(f"ok    {fn.__name__}")
    total, meta = ran, []
    if os.environ.get("ARES_INSPECT_META", "1") != "0" and not only:
        meta = _run_meta({k: v.dir for k, v in ch.items()})
        for name, ok, detail in meta:
            total += 1
            if ok:
                print(f"ok    {name}")
            else:
                failed += 1
                print(f"FAIL  {name}\n      {detail}")
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    nm = sum(1 for name, _, _ in meta if name.startswith("mutant"))
    print(f"  {total - failed}/{total} admission inspection checks passed "
          f"({ran} controls, {nm} tool mutants, {len(meta) - nm} stub tools)")
    return 1 if failed or not ran else 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--drive":
        drive(*sys.argv[2:5])
        sys.exit(0)
    sys.exit(main())
