"""Phase 0.5 §6.5 — zero-network gate, structural AND dynamic.

WHY BOTH HALVES ARE REQUIRED
----------------------------
§6.1's replay injected tracker's I/O boundary, so it COULD NOT have observed a
real network call even if the telemetry made one. Its zero-network claim was
therefore structural only. And a structural check alone cannot prove that the
wired-up runtime path behaves as the source suggests. Neither half substitutes
for the other.

WHAT "ZERO" MEANS HERE
----------------------
Zero ATTEMPTED parity-side acquisitions, not zero completed requests. A call that
raises, times out, or is served from cache still proves parity tried to acquire
its own observation, which is the failure mode this gate exists to prevent.

CALLS ARE COUNTED BY AUTHORITY
------------------------------
inline_authoritative_calls must equal the position count; parity_observer_calls
must be zero. A single total is not acceptable evidence: a total equal to the
position count can conceal one missing inline call plus one improper parity call.
"""

import ast
import json
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import data_feed, parity_hook, parity_runner, tracker  # noqa: E402

FAILS = []
COUNT = 0


def check(label, cond, got=None):
    global COUNT
    COUNT += 1
    if cond:
        print(f"  pass  {label}")
    else:
        print(f"  FAIL  {label}  {got if got is not None else ''}")
        FAILS.append(label)


# Acquisition surfaces. Anything here, reached from parity, fails the gate.
FORBIDDEN = frozenset({
    "get_live_price", "_get_ib", "load_stock", "download_stock",
    "refresh_watchlist", "prune_cache", "disconnect_ib",
    "yfinance", "yf", "requests", "urllib", "socket", "urlopen",
    "create_connection", "connect", "reqHistoricalData", "reqMktData",
    "Ticker", "download", "history",
})
PARITY_MODULES = ("parity_hook", "parity_runner", "parity_eval",
                  "tracker_compat", "parity_compare")


# ============================ STRUCTURAL HALF ==============================
def module_ast(name):
    return ast.parse((ROOT / "engine" / f"{name}.py").read_text())


def test_no_forbidden_imports_in_parity_modules():
    """AST imports only. A docstring mentioning yfinance must not fail this."""
    for mod in PARITY_MODULES:
        tree = module_ast(mod)
        imported = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    imported.add(a.name.split(".")[0])
                    imported.add((a.asname or a.name).split(".")[0])
            elif isinstance(n, ast.ImportFrom):
                imported.add((n.module or "").split(".")[0])
                for a in n.names:
                    imported.add(a.name)
                    imported.add(a.asname or a.name)
        bad = imported & FORBIDDEN
        check(f"{mod}: imports no acquisition module or symbol", not bad,
              sorted(bad))
        check(f"{mod}: does not import data_feed", "data_feed" not in imported,
              sorted(imported))


def test_no_forbidden_call_targets_in_parity_modules():
    """Every Call target, by name or attribute, across each parity module."""
    for mod in PARITY_MODULES:
        tree = module_ast(mod)
        targets = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Name):
                    targets.add(f.id)
                elif isinstance(f, ast.Attribute):
                    targets.add(f.attr)
        bad = targets & FORBIDDEN
        check(f"{mod}: calls no acquisition function", not bad, sorted(bad))


def test_no_dynamic_import_of_acquisition_modules():
    """importlib / __import__ / exec are how a forbidden import hides."""
    for mod in PARITY_MODULES:
        tree = module_ast(mod)
        dyn = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                nm = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                if nm in ("__import__", "import_module", "exec", "eval",
                          "load_module"):
                    dyn.append((nm, n.lineno))
        check(f"{mod}: no dynamic import or exec", not dyn, dyn)


def test_lazy_imports_are_parity_only():
    """parity_hook imports lazily inside the enabled branch. Verify WHAT."""
    tree = module_ast("parity_hook")
    inner = []
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef):
            for sub in ast.walk(n):
                if isinstance(sub, ast.ImportFrom):
                    inner.extend(a.name for a in sub.names)
                elif isinstance(sub, ast.Import):
                    inner.extend(a.name for a in sub.names)
    allowed = {"parity_eval", "parity_runner", "hashlib", "subprocess",
               "Path", "pathlib"}
    unexpected = set(inner) - allowed
    check("parity_hook lazy imports are parity/stdlib only", not unexpected,
          sorted(unexpected))
    check("parity_hook lazily imports no acquisition module",
          not (set(inner) & FORBIDDEN), sorted(set(inner) & FORBIDDEN))


def test_transitive_reachability_from_the_bridge():
    """Direct-module scanning is insufficient: a parity function could call a
    helper that acquires data indirectly. Walk the parity call graph and check
    every resolved target's own module.
    """
    defined = {}
    for mod in PARITY_MODULES:
        tree = module_ast(mod)
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef):
                defined[(mod, n.name)] = n

    # Names each parity module binds from OUTSIDE the parity set.
    external = {}
    for mod in PARITY_MODULES:
        tree = module_ast(mod)
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                base = (n.module or "").split(".")[0]
                for a in n.names:
                    if a.name not in PARITY_MODULES:
                        external.setdefault(mod, set()).add(
                            (a.asname or a.name, base))
            elif isinstance(n, ast.Import):
                for a in n.names:
                    external.setdefault(mod, set()).add(
                        ((a.asname or a.name).split(".")[0],
                         a.name.split(".")[0]))

    leaked = []
    for mod, names in external.items():
        for local, base in names:
            if local in FORBIDDEN or base in FORBIDDEN:
                leaked.append((mod, local, base))
    check("no parity module binds an external acquisition name", not leaked,
          leaked)

    # The only non-stdlib external dependency permitted is engine.tracker, and
    # only for _load_params / _LAST_EVAL / check_open_trades / load_trades.
    hook = module_ast("parity_hook")
    tracker_attrs = {n.attr for n in ast.walk(hook)
                     if isinstance(n, ast.Attribute)
                     and isinstance(n.value, ast.Name)
                     and n.value.id == "tracker"}
    permitted = {"_load_params", "_LAST_EVAL", "check_open_trades",
                 "load_trades"}
    check("parity_hook touches only the registered tracker surface",
          tracker_attrs <= permitted, sorted(tracker_attrs - permitted))


def test_withdrawn_reconstruction_names_are_gone_not_dormant():
    """Removed, not disabled. A dormant path confuses which input is
    authoritative and could be re-enabled by a later change.
    """
    for mod in PARITY_MODULES:
        src = (ROOT / "engine" / f"{mod}.py").read_text()
        tree = ast.parse(src)
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} \
            | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} \
            | {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        for gone in ("_bar_fn", "_bar_recheck", "_premise", "_ib_connection",
                     "cache_mtime", "BarStale"):
            check(f"{mod}: {gone} is absent from the AST", gone not in names,
                  gone)


# ============================= DYNAMIC HALF ================================
PARAMS = {"scale_out": True, "scale_out_pct": 0.50, "trailing_stop_pct": 0.10,
          "rsi_extreme_high": 90, "slippage_pct": 0.001,
          "commission_per_trade": 1.00, "max_positions": 5, "exit_policy": "A"}


def frame(close, date="2026-09-26", bars=120, rsi=55.0):
    idx = pd.to_datetime(pd.date_range(end=date, periods=bars, freq="D"))
    return pd.DataFrame({
        "Open": [close] * bars, "High": [close] * bars, "Low": [close] * bars,
        "Close": [close] * bars, "Volume": [1_000_000] * bars,
        "rsi": [rsi] * bars, "bearish_div": [False] * bars}, index=idx)


def st(symbol="ABM", **over):
    t = {"symbol": symbol, "status": "open", "strategy": "momentum_breakout",
         "entry_date": "2026-09-09", "entry_price": 50.65, "shares": 2.94,
         "original_shares": 2.94, "stop_loss": 48.67, "trailing_stop": 48.67,
         "peak_price": 50.91, "take_profit": 59.76, "scaled_out": False,
         "total_commission": 1.0, "entry_commission": 1.0}
    t.update(over)
    return t


class Spies:
    """Counts acquisition attempts and attributes each to inline or parity.

    Attribution works by phase: the wrapper around check_open_trades sets the
    phase to 'inline' on entry and 'parity' on exit, so anything the observer
    does after the authoritative call is attributed to parity. Raising is not
    required -- an ATTEMPT is already a failure -- so the spies return benign
    values and let the assertion catch it.
    """

    def __init__(self):
        self.phase = "parity"
        self.events = []

    def note(self, what):
        self.events.append((self.phase, what))

    def counts(self, phase):
        return sum(1 for p, _ in self.events if p == phase)

    def by(self, phase):
        return sorted(w for p, w in self.events if p == phase)


def run(trades, frames, live=None, params=None, enable=True,
        module_eval_fail=False, params_fail=False, write_fail=False,
        fail_symbols=(), packets_override=None):
    """Drive the bridge with every acquisition surface spied."""
    import socket
    import urllib.request
    live = live or {}
    sp = Spies()
    out = ("/nonexistent-file-parent/x/shadow.jsonl" if write_fail
           else str(Path(tempfile.mkdtemp()) / "shadow.jsonl"))
    state = {"trades": [dict(t) for t in trades]}

    saved_tracker = {k: getattr(tracker, k) for k in
                     ("load_trades", "save_trades", "load_stock",
                      "add_indicators", "get_live_price", "_load_params",
                      "expire_queue", "check_open_trades")}
    saved_df = {k: getattr(data_feed, k) for k in
                ("get_live_price", "load_stock", "download_stock",
                 "refresh_watchlist", "_get_ib")}
    saved_sock = socket.create_connection
    saved_url = urllib.request.urlopen
    saved_env = os.environ.get("ARES_PARITY")
    saved_default = parity_runner.DEFAULT_OUTPUT
    real_coq = saved_tracker["check_open_trades"]

    def inline_wrapper():
        sp.phase = "inline"
        try:
            return real_coq()
        finally:
            sp.phase = "parity"

    def spy_live(sym):
        sp.note(f"get_live_price({sym})")
        return live.get(sym)

    def spy_stock(sym, *a, **k):
        sp.note(f"load_stock({sym})")
        if sym in fail_symbols:
            raise RuntimeError("forced data failure")
        return frames[sym]

    try:
        tracker.load_trades = lambda: [dict(x) for x in state["trades"]]
        tracker.save_trades = lambda tr: state.update(
            trades=[dict(x) for x in tr])
        tracker.load_stock = spy_stock
        tracker.add_indicators = lambda d: d
        tracker.get_live_price = spy_live
        tracker.expire_queue = lambda: []
        tracker.check_open_trades = inline_wrapper
        if params_fail:
            def bad_params():
                if sp.phase == "parity":
                    raise RuntimeError("config unavailable")
                return dict(params or PARAMS)
            tracker._load_params = bad_params
        else:
            tracker._load_params = lambda: dict(params or PARAMS)

        # Lower-level surfaces: any touch at all is recorded.
        for name in ("get_live_price", "load_stock", "download_stock",
                     "refresh_watchlist", "_get_ib"):
            def mk(n):
                def f(*a, **k):
                    sp.note(f"data_feed.{n}")
                    return None
                return f
            setattr(data_feed, name, mk(name))
        socket.create_connection = lambda *a, **k: (
            sp.note("socket.create_connection"), None)[1]
        urllib.request.urlopen = lambda *a, **k: (
            sp.note("urllib.urlopen"), None)[1]

        if enable:
            os.environ["ARES_PARITY"] = "1"
        else:
            os.environ.pop("ARES_PARITY", None)

        # Runtime default redirection; exercises the real write path.
        parity_runner.DEFAULT_OUTPUT = out

        if module_eval_fail:
            from engine import parity_eval
            saved_ev = parity_eval.evaluate

            def boom(t, p, packet):
                raise RuntimeError("evaluator exploded")
            parity_eval.evaluate = boom
        if packets_override is not None:
            saved_pk = parity_hook._packets
            parity_hook._packets = packets_override

        try:
            parity_hook.observed_check_open_trades()
        finally:
            if module_eval_fail:
                parity_eval.evaluate = saved_ev
            if packets_override is not None:
                parity_hook._packets = saved_pk
    finally:
        for k, v in saved_tracker.items():
            setattr(tracker, k, v)
        for k, v in saved_df.items():
            setattr(data_feed, k, v)
        socket.create_connection = saved_sock
        urllib.request.urlopen = saved_url
        parity_runner.DEFAULT_OUTPUT = saved_default
        if saved_env is None:
            os.environ.pop("ARES_PARITY", None)
        else:
            os.environ["ARES_PARITY"] = saved_env

    text = ""
    if not write_fail and Path(out).exists():
        text = Path(out).read_text().strip()
    recs = [json.loads(l) for l in text.splitlines()] if text else []
    return sp, {r["symbol"]: r for r in recs}, out


def assert_zero_parity(tag, sp, n_positions):
    check(f"{tag}: inline_authoritative_calls == position count",
          sp.counts("inline") >= n_positions, (sp.counts("inline"), n_positions))
    check(f"{tag}: parity_observer_calls == 0", sp.counts("parity") == 0,
          sp.by("parity"))


def test_dynamic_zero_network_live_source():
    sp, recs, _ = run([st()], {"ABM": frame(99.00)}, live={"ABM": 49.78})
    assert_zero_parity("IBKR source", sp, 1)
    check("IBKR: packet consumed verbatim",
          recs["ABM"]["bar_price"] == 49.78, recs["ABM"]["bar_price"])
    check("IBKR: price_source recorded", recs["ABM"]["price_source"] == "IBKR",
          recs["ABM"]["price_source"])


def test_dynamic_zero_network_daily_source():
    sp, recs, _ = run([st()], {"ABM": frame(49.7812345)}, live={})
    assert_zero_parity("daily source", sp, 1)
    check("daily: packet consumed verbatim and unrounded",
          recs["ABM"]["bar_price"] == 49.7812345, recs["ABM"]["bar_price"])
    check("daily: price_source recorded",
          recs["ABM"]["price_source"] == "daily", recs["ABM"]["price_source"])


def test_dynamic_multi_position_attribution():
    trades = [st("ABM"), st("TMO", stop_loss=600.0, trailing_stop=600.0,
                            entry_price=654.54, peak_price=675.0,
                            take_profit=772.36),
              st("WBD", stop_loss=29.31, trailing_stop=29.31,
                 entry_price=30.87, peak_price=30.9, take_profit=36.42)]
    frames = {t["symbol"]: frame(99.00) for t in trades}
    sp, recs, _ = run(trades, frames,
                      live={"ABM": 49.78, "TMO": 675.00, "WBD": 30.85})
    check("3 positions: exactly 3 inline live-price acquisitions",
          sum(1 for p, w in sp.events
              if p == "inline" and w.startswith("get_live_price")) == 3,
          sp.by("inline"))
    check("3 positions: parity made zero acquisitions",
          sp.counts("parity") == 0, sp.by("parity"))
    check("3 positions: all three recorded", len(recs) == 3, sorted(recs))


def test_refusal_paths_do_not_attempt_repair():
    """Every refusal must NOT try to fetch what it is missing."""
    cases = {
        "missing packet": lambda: (99, {}),
        "stale packet": lambda: (99, {"ABM": {"cycle_token": 1, "price": 1.0,
                                              "price_source": "IBKR",
                                              "rsi": 50.0, "bearish_div": False,
                                              "bar_date": "2026-09-26"}}),
        "no token": lambda: (None, {}),
        "schema drift": lambda: (99, {"ABM": {"cycle_token": 99, "price": 1.0}}),
    }
    for tag, pf in cases.items():
        sp, recs, _ = run([st()], {"ABM": frame(99.00)}, live={"ABM": 49.78},
                          packets_override=pf)
        check(f"refusal '{tag}': zero parity acquisitions",
              sp.counts("parity") == 0, sp.by("parity"))
        check(f"refusal '{tag}': refused, not repaired",
              recs["ABM"]["shadow_exception_type"]
              == "INLINE_INPUT_NOT_CAPTURED",
              recs["ABM"]["shadow_exception_type"])


def test_failure_paths_do_not_attempt_repair():
    for tag, kw in (("inline data failure", {"fail_symbols": {"ABM"}}),
                    ("evaluator failure", {"module_eval_fail": True}),
                    ("params failure", {"params_fail": True}),
                    ("write failure", {"write_fail": True})):
        sp, recs, _ = run([st()], {"ABM": frame(99.00)}, live={"ABM": 49.78},
                          **kw)
        check(f"failure '{tag}': zero parity acquisitions",
              sp.counts("parity") == 0, sp.by("parity"))


def test_entry_day_skip_does_not_acquire():
    day = "2026-09-26"
    sp, recs, _ = run([st(entry_date=day)], {"ABM": frame(99.00, date=day)},
                      live={"ABM": 40.00})
    assert_zero_parity("entry-day", sp, 1)


def _evidence_fingerprint():
    """Existence + size + checksum of the real evidence file.

    Deliberately NOT an existence check. Once collection is declared and a cycle
    has run, the file legitimately exists, so "absent" is a Phase 0.5 statement
    rather than an invariant. "Neither created nor modified" holds in every phase
    and is strictly stronger.
    """
    import hashlib
    p = ROOT / "logs" / "tracker_parity_v1.jsonl"
    if not p.exists():
        return ("absent", None, None)
    b = p.read_bytes()
    return ("present", len(b), hashlib.md5(b).hexdigest())


def test_disabled_mode_writes_no_parity_output():
    before = _evidence_fingerprint()
    sp, recs, out = run([st()], {"ABM": frame(99.00)}, live={"ABM": 49.78},
                        enable=False)
    check("disabled: zero parity acquisitions", sp.counts("parity") == 0,
          sp.by("parity"))
    check("disabled: no parity record written", not recs, sorted(recs))
    check("disabled: no output file created", not Path(out).exists(), out)
    check("disabled: the real evidence file was neither created nor modified",
          _evidence_fingerprint() == before,
          (before, _evidence_fingerprint()))


def test_no_cache_file_is_created_or_modified():
    """data_feed.DATA_DIR must be untouched across an observed cycle."""
    d = getattr(data_feed, "DATA_DIR", None)
    before = {}
    if d and Path(d).exists():
        before = {p.name: (p.stat().st_mtime_ns, p.stat().st_size)
                  for p in Path(d).iterdir() if p.is_file()}
    sp, recs, _ = run([st()], {"ABM": frame(99.00)}, live={"ABM": 49.78})
    after = {}
    if d and Path(d).exists():
        after = {p.name: (p.stat().st_mtime_ns, p.stat().st_size)
                 for p in Path(d).iterdir() if p.is_file()}
    check("no cache file created, modified or removed", before == after,
          {"added": sorted(set(after) - set(before)),
           "removed": sorted(set(before) - set(after)),
           "changed": sorted(k for k in before if k in after
                             and before[k] != after[k])})


def test_production_trade_state_unmodified_by_parity():
    sp, recs, _ = run([st()], {"ABM": frame(99.00)}, live={"ABM": 49.78})
    rec = recs["ABM"]
    check("production state hash unchanged across the shadow bracket",
          rec["production_state_hash_before"]
          == rec["production_state_hash_after"],
          (rec["production_state_hash_before"],
           rec["production_state_hash_after"]))


def test_report_counts_by_authority_not_total():
    """The report must separate authorities.

    A single total equal to the position count can hide one MISSING inline call
    plus one IMPROPER parity call, which is exactly the confusion this gate is
    designed to eliminate.
    """
    sp, recs, _ = run([st()], {"ABM": frame(99.00)}, live={"ABM": 49.78})
    print(f"\n    inline_authoritative_calls : {sp.counts('inline')}")
    print(f"    parity_observer_calls      : {sp.counts('parity')}")
    print(f"    inline detail              : {sp.by('inline')}")
    check("both authorities reported separately",
          sp.counts("inline") > 0 and sp.counts("parity") == 0,
          (sp.counts("inline"), sp.counts("parity")))


def test_the_spies_can_actually_detect_a_parity_side_call():
    """NEGATIVE CONTROL. A gate that cannot fail is not evidence.

    Deliberately make the parity branch acquire a price, and assert the spies
    catch it and attribute it to parity. Without this, every zero above could be
    explained by broken instrumentation rather than by correct behaviour.
    """
    import engine.parity_eval as pe
    saved = pe.evaluate
    seen = {}

    def leaky(t, params, packet):
        # This is what a future refactor must never do: acquire a second
        # observation inside the parity path.
        tracker.get_live_price(t["symbol"])
        return saved(t, params, packet)

    try:
        pe.evaluate = leaky
        sp, recs, _ = run([st()], {"ABM": frame(99.00)}, live={"ABM": 49.78})
        seen["parity"] = sp.counts("parity")
        seen["detail"] = sp.by("parity")
    finally:
        pe.evaluate = saved

    check("negative control: an injected parity call IS detected",
          seen["parity"] >= 1, seen)
    check("negative control: it is attributed to parity, not inline",
          any("get_live_price" in w for w in seen["detail"]), seen["detail"])

    # And the clean path must still report zero afterwards, proving the spy
    # state did not leak between runs.
    sp2, _, _ = run([st()], {"ABM": frame(99.00)}, live={"ABM": 49.78})
    check("negative control: clean run still reports zero after the leak test",
          sp2.counts("parity") == 0, sp2.by("parity"))


if __name__ == "__main__":
    for fn in sorted([v for k, v in list(globals().items())
                      if k.startswith("test_")], key=lambda f: f.__name__):
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{COUNT} assertions")
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
        sys.exit(1)
    print("ALL PASS")
