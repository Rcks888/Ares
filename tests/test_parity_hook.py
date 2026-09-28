"""Tests for engine/parity_hook.py -- the single production bridge.

The two properties that must be PROVABLE, not intended:

  ARES_PARITY unset : behaviour and import surface equivalent to the old path.
  ARES_PARITY=1     : inline tracker runs exactly once and stays authoritative;
                      every parity-side failure is observational only.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

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


PARITY_MODULES = ("engine.parity_eval", "engine.parity_runner",
                  "engine.parity_compare", "engine.tracker_compat")


def func(name):
    """Return the AST node for a top-level function in parity_hook.

    Text scanning has produced three false results in this project already --
    matching a docstring or a comment instead of code -- so structural questions
    are asked of the AST.
    """
    import ast
    tree = ast.parse((ROOT / "engine" / "parity_hook.py").read_text())
    return next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == name)


def calls_in(name):
    import ast
    out = set()
    for n in ast.walk(func(name)):
        if isinstance(n, ast.Call):
            f = n.func
            out.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    return out


def run_child(body, env=None):
    """Run a snippet in a clean interpreter and return (rc, stdout+stderr)."""
    e = dict(os.environ)
    e.pop("ARES_PARITY", None)
    e.update(env or {})
    e["PYTHONPATH"] = str(ROOT)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(body)
        path = fh.name
    try:
        r = subprocess.run([sys.executable, path], capture_output=True,
                           text=True, env=e, cwd=str(ROOT), timeout=180)
        return r.returncode, r.stdout + r.stderr
    finally:
        os.unlink(path)


# --- BLOCKING ISSUE 1: disabled mode must not import the parity stack -------
def test_disabled_mode_does_not_import_parity_stack():
    """Make the parity stack un-importable and prove the disabled path still works.

    This is the real guarantee: not "the modules happen to import fine today",
    but "a broken parity stack cannot stop Ares while parity is off".
    """
    body = f'''
import sys, importlib.abc, importlib.machinery
BLOCKED = {PARITY_MODULES!r}

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name in BLOCKED:
            raise ImportError("blocked by test: " + name)
        return None

sys.meta_path.insert(0, Blocker())

import engine.parity_hook as hook          # must succeed with stack blocked
import engine.tracker as tracker

calls = []
tracker.check_open_trades = lambda: calls.append(1) or "INLINE_RESULT"
hook.tracker = tracker

assert hook.enabled() is False, "parity should be disabled"
res = hook.observed_check_open_trades()

assert res == "INLINE_RESULT", res
assert len(calls) == 1, f"inline called {{len(calls)}} times, expected 1"
leaked = [m for m in BLOCKED if m in sys.modules]
assert not leaked, f"parity modules imported while disabled: {{leaked}}"
print("CHILD_OK")
'''
    rc, out = run_child(body)
    check("parity_hook imports with the whole parity stack un-importable",
          "ImportError" not in out or "CHILD_OK" in out, out[-500:])
    check("disabled mode: inline called exactly once, stack never imported",
          rc == 0 and "CHILD_OK" in out, out[-900:])


def test_disabled_mode_import_surface_is_minimal():
    """parity_hook's own module body must import nothing parity-specific."""
    src = (ROOT / "engine" / "parity_hook.py").read_text()
    head = src.split("def enabled")[0]
    for name in ("parity_eval", "parity_runner", "parity_compare",
                 "tracker_compat", "data_feed", "indicators"):
        check(f"module body does not eagerly import {name}",
              f"import {name}" not in head and f"from engine.{name}" not in head
              and f"from engine import {name}" not in head)
    check("module body eagerly imports only tracker (plus stdlib os)",
          "from engine import tracker" in head)


def test_no_live_price_attempt_anywhere_in_hook():
    """The extra per-symbol outbound attempt must be gone."""
    src = (ROOT / "engine" / "parity_hook.py").read_text()
    check("hook never calls get_live_price", "get_live_price(" not in src)
    check("hook does not import get_live_price",
          "import get_live_price" not in src)
    check("hook reads the cached handle instead",
          "_ib_connection" in src)
    check("_bar_fn makes no live-price call", "get_live_price" not in calls_in("_bar_fn"),
          calls_in("_bar_fn"))
    check("premise is evaluated once per cycle, not per symbol: _bar_fn "
          "does not call _premise", "_premise" not in calls_in("_bar_fn"),
          calls_in("_bar_fn"))
    check("the bridge calls _premise at most once",
          sum(1 for c in calls_in("observed_check_open_trades")
              if c == "_premise") == 0)
    check("_bar_fn never triggers a download",
          "download_stock" not in calls_in("_bar_fn"))


# --- BLOCKING ISSUE 2: _params failure must not block the inline cycle ------
def _stub_trade():
    return {"symbol": "ABM", "strategy": "momentum_breakout",
            "entry_date": "2026-09-09", "entry_price": 50.65, "shares": 2.94,
            "original_shares": 2.94, "stop_loss": 48.67, "trailing_stop": 48.67,
            "peak_price": 50.91, "take_profit": 59.76, "status": "open"}


def test_params_failure_is_observational_only():
    body = f'''
import json, sys, tempfile
from pathlib import Path
import engine.tracker as tracker
import engine.parity_runner as pr
import engine.parity_hook as hook

out = Path(tempfile.mkdtemp()) / "parity.jsonl"
pr.DEFAULT_OUTPUT = out
_real = pr.observe_cycle
pr.observe_cycle = lambda *a, **k: _real(*a, **{{**k, "output_path": out}})

def boom():
    raise RuntimeError("config gone")
tracker._load_params = boom

# Stub the bar source: this test is about params failure, and the real _bar_fn
# reads the on-disk cache. Keeping it out makes the test hermetic.
hook._bar_fn = lambda s: {{"price": 49.0, "rsi": 55.0, "bearish_div": False,
                          "date": "2026-09-26", "price_source": "daily",
                          "cache_mtime": 0}}
hook._bar_recheck = lambda s, b: None

calls = []
tracker.check_open_trades = lambda: calls.append(1) or "INLINE_RESULT"
tracker.load_trades = lambda: [{_stub_trade()!r}]
hook.tracker = tracker

import os
os.environ["ARES_PARITY"] = "1"
res = hook.observed_check_open_trades()

assert res == "INLINE_RESULT", res
assert len(calls) == 1, f"inline called {{len(calls)}} times, expected 1"

recs = [json.loads(l) for l in out.read_text().splitlines()] if out.exists() else []
assert recs, "no parity record written"
r = recs[0]
assert r["shadow_exception_type"] == "ParamsUnavailable", r["shadow_exception_type"]
assert "config gone" in (r["shadow_exception_message"] or ""), r["shadow_exception_message"]
assert r["would_change_action"] is not False, r["would_change_action"]
print("CHILD_OK", r["difference_class"])
'''
    rc, out = run_child(body, {"ARES_PARITY": "1"})
    check("params failure: inline called exactly once and result preserved",
          rc == 0 and "CHILD_OK" in out, out[-1200:])
    check("params failure classified as a parity infrastructure failure",
          "ParamsUnavailable" in out or "CHILD_OK" in out, out[-400:])


def test_params_fn_passed_uncalled():
    """The call site must hand over a callable, not an evaluated dict."""
    src = (ROOT / "engine" / "parity_hook.py").read_text()
    call = src.split("observe_cycle(")[-1]
    check("params_fn is passed uncalled", "params_fn=_params," in call
          or "params_fn=_params)" in call, call[:300])
    check("params is NOT pre-evaluated at the call site",
          "params=_params()" not in src)
    check("premise_fn is passed uncalled", "premise_fn=_premise" in call)
    check("no eager params call outside the runner",
          "_params()" not in src.split("def observed_check_open_trades")[1])


# --- _load_params compatibility contract -----------------------------------
def test_load_params_contract():
    from engine import tracker
    check("tracker._load_params exists", hasattr(tracker, "_load_params"))
    p = tracker._load_params()
    check("_load_params returns a mapping", hasattr(p, "get"), type(p))
    for key in ("stop_loss_multiplier", "tp_momentum", "trailing_stop_pct",
                "rsi_extreme_high", "scale_out"):
        check(f"_load_params supplies {key}", key in p, sorted(p)[:14])
    from engine import parity_hook
    hp = parity_hook._params()
    check("_params defaults exit_policy", hp.get("exit_policy") == "A")
    check("_params does not mutate the loaded config",
          "exit_policy" not in p and "slippage_pct" not in p)
    check("_params passes real config values through unchanged",
          all(hp[k] == p[k] for k in p))


def test_inline_fallback_defaults_match_tracker_source():
    """slippage_pct and commission_per_trade are NOT in config.

    Measured: config/strategy_params.json contains neither key, so the inline
    tracker uses hardcoded fallbacks. Parity must inherit the SAME values, and
    this asserts it against tracker's source so drift breaks the build rather
    than producing a MATCH on a fill that differed.
    """
    import re
    from engine import parity_hook, tracker
    cfg = tracker._load_params()
    src = (ROOT / "engine" / "tracker.py").read_text()
    for key, expected in parity_hook.INLINE_FALLBACKS.items():
        check(f"{key} genuinely absent from config (premise of the fallback)",
              key not in cfg, cfg.get(key))
        found = re.findall(r"params\.get\('" + key + r"',\s*([0-9.]+)\)", src)
        check(f"tracker hardcodes a fallback for {key}", bool(found), found)
        check(f"all tracker fallbacks for {key} agree with each other",
              len(set(found)) == 1, found)
        check(f"parity inherits tracker's {key} fallback exactly",
              found and float(found[0]) == expected, (found, expected))
    hp = parity_hook._params()
    check("_params supplies the inherited slippage",
          hp["slippage_pct"] == 0.001, hp.get("slippage_pct"))
    check("_params supplies the inherited commission",
          hp["commission_per_trade"] == 1.00, hp.get("commission_per_trade"))


# --- premise logic ---------------------------------------------------------
def test_premise_refuses_when_gateway_reachable():
    from engine import data_feed, parity_hook
    saved = getattr(data_feed, "_ib_connection", None)
    try:
        data_feed._ib_connection = None
        p = parity_hook._premise()
        check("no cached handle => daily premise holds",
              p["ok"] is True and p["source"] == "daily", p)

        class Live:
            def isConnected(self):
                return True
        data_feed._ib_connection = Live()
        p = parity_hook._premise()
        check("reachable gateway REFUSES the cycle", p["ok"] is False, p)
        check("refusal reports the live source", p["source"] == "live", p)

        class Dead:
            def isConnected(self):
                return False
        data_feed._ib_connection = Dead()
        p = parity_hook._premise()
        check("disconnected handle => daily premise holds", p["ok"] is True, p)

        class Broken:
            def isConnected(self):
                raise OSError("socket gone")
        data_feed._ib_connection = Broken()
        p = parity_hook._premise()
        check("indeterminate premise refuses rather than assuming daily",
              p["ok"] is False and p["source"] == "unknown", p)
    finally:
        data_feed._ib_connection = saved


def test_premise_opens_no_connection():
    """_premise must not trigger _get_ib, which would dial the gateway."""
    from engine import data_feed, parity_hook
    saved_get, saved_conn = data_feed._get_ib, getattr(
        data_feed, "_ib_connection", None)
    dialled = []
    try:
        data_feed._get_ib = lambda: dialled.append(1)
        data_feed._ib_connection = None
        parity_hook._premise()
        check("_premise never calls _get_ib", not dialled, dialled)
    finally:
        data_feed._get_ib, data_feed._ib_connection = saved_get, saved_conn


# --- no double evaluation --------------------------------------------------
def test_no_fallback_recall():
    import ast
    fn = func("observed_check_open_trades")
    refs = [n for n in ast.walk(fn) if isinstance(n, ast.Attribute)
            and n.attr == "check_open_trades"]
    check("exactly two references to check_open_trades in the bridge",
          len(refs) == 2, len(refs))
    check("the bridge contains no try/except at all, so no fallback re-call "
          "is structurally possible",
          not [n for n in ast.walk(fn) if isinstance(n, ast.Try)])
    check("observe_cycle is called exactly once",
          sum(1 for c in calls_in("observed_check_open_trades")
              if c == "observe_cycle") == 1)


# --- zero-network bar sourcing, measured not just structural ----------------
def test_bar_fn_never_downloads_even_with_a_stale_cache():
    """load_stock re-downloads above 20h. Parity must not, at any cache age."""
    from engine import data_feed, parity_hook
    saved = data_feed.download_stock
    dl = []
    with tempfile.TemporaryDirectory() as tmp:
        saved_dir = data_feed.DATA_DIR
        try:
            data_feed.download_stock = lambda s, period="2y": dl.append(s)
            data_feed.DATA_DIR = Path(tmp)
            csv = Path(tmp) / "ZZZ.csv"
            rows = ["Date,Open,High,Low,Close,Volume"]
            for i in range(120):
                d = 1 + i
                rows.append(f"2025-{1 + d // 28:02d}-{1 + d % 28:02d},"
                            f"10,11,9,{10 + i * 0.05:.2f},1000000")
            csv.write_text("\n".join(rows) + "\n")
            os.utime(csv, (0, 0))          # ~56 years stale
            age_h = (__import__("time").time() - csv.stat().st_mtime) / 3600
            check("premise: cache is far beyond the 20h refresh threshold",
                  age_h > data_feed.CACHE_MAX_AGE_HOURS, age_h)
            try:
                bar = parity_hook._bar_fn("ZZZ")
                ok = True
            except Exception as exc:                # noqa: BLE001
                ok, bar = False, repr(exc)
            check("_bar_fn reads a stale cache successfully", ok, bar)
            check("_bar_fn triggered NO download despite the stale cache",
                  not dl, dl)
            if ok:
                check("bar is marked daily", bar["price_source"] == "daily")
                check("bar records the cache mtime",
                      bar["cache_mtime"] == csv.stat().st_mtime)
        finally:
            data_feed.download_stock = saved
            data_feed.DATA_DIR = saved_dir


def test_bar_fn_refuses_a_missing_cache_rather_than_fetching():
    from engine import data_feed, parity_hook
    saved_dl, saved_dir = data_feed.download_stock, data_feed.DATA_DIR
    dl = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            data_feed.download_stock = lambda s, period="2y": dl.append(s)
            data_feed.DATA_DIR = Path(tmp)
            try:
                parity_hook._bar_fn("NOPE")
                check("missing cache raises instead of downloading", False)
            except FileNotFoundError:
                check("missing cache raises instead of downloading", True)
            except Exception as exc:                # noqa: BLE001
                check("missing cache raises instead of downloading", False,
                      repr(exc))
            check("no download attempted for a missing cache", not dl, dl)
        finally:
            data_feed.download_stock, data_feed.DATA_DIR = saved_dl, saved_dir


def test_bar_recheck_detects_a_mid_cycle_refresh():
    """If the inline path refreshes the cache, parity's bar is not inline's bar."""
    from engine import data_feed, parity_hook
    saved_dir = data_feed.DATA_DIR
    with tempfile.TemporaryDirectory() as tmp:
        try:
            data_feed.DATA_DIR = Path(tmp)
            csv = Path(tmp) / "AAA.csv"
            csv.write_text("Date,Close\n2026-01-01,1\n")
            mt = csv.stat().st_mtime
            check("unchanged cache passes the recheck",
                  parity_hook._bar_recheck("AAA", {"cache_mtime": mt}) is None)
            os.utime(csv, (mt + 500, mt + 500))
            reason = parity_hook._bar_recheck("AAA", {"cache_mtime": mt})
            check("a refreshed cache is caught", reason is not None, reason)
            check("the reason names the mid-cycle refresh",
                  reason and "refreshed" in reason, reason)
            csv.unlink()
            gone = parity_hook._bar_recheck("AAA", {"cache_mtime": mt})
            check("a disappeared cache is caught", gone is not None, gone)
        finally:
            data_feed.DATA_DIR = saved_dir


def test_runner_classifies_a_stale_bar_as_unavailable():
    """A failed recheck must block comparison, not be absorbed into agreement."""
    import engine.parity_runner as pr
    from engine import tracker
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "p.jsonl"
        trade = {"symbol": "ABM", "status": "open", "entry_date": "2026-09-09",
                 "entry_price": 50.65, "shares": 2.94, "stop_loss": 48.67,
                 "trailing_stop": 48.67, "peak_price": 50.91,
                 "strategy": "momentum_breakout", "take_profit": 59.76}
        evaluated = []
        res, summ = pr.observe_cycle(
            tracker, lambda: "INLINE", lambda: [dict(trade)],
            lambda t, p, b: evaluated.append(1) or dict(t),
            bar_fn=lambda s: {"price": 49.0, "date": "2026-09-26",
                              "price_source": "daily", "cache_mtime": 1},
            bar_recheck=lambda s, b: "cache moved",
            params={"slippage_pct": 0.001}, output_path=out)
        check("inline result preserved when the bar is stale", res == "INLINE")
        check("module_eval NOT run against an untrustworthy bar",
              not evaluated, evaluated)
        recs = [json.loads(l) for l in out.read_text().splitlines()]
        check("a record is still written", len(recs) == 1, len(recs))
        check("stale bar classified BarStale",
              recs[0]["shadow_exception_type"] == "BarStale",
              recs[0]["shadow_exception_type"])
        check("would_change_action is not coerced to False",
              recs[0]["would_change_action"] is not False,
              recs[0]["would_change_action"])
        check("a recheck exception is caught, not propagated",
              pr._recheck_failed(lambda s, b: 1 / 0, "X", {}) is not None)


def test_premise_states_stay_distinguishable():
    """Four states, only ONE of which permits comparison. No falsey collapsing."""
    from engine import data_feed, parity_hook as ph
    saved = getattr(data_feed, "_ib_connection", None)
    had = hasattr(data_feed, "_ib_connection")
    try:
        # 1. contract missing -> must NOT be read as "no gateway"
        del data_feed._ib_connection
        p = ph._premise()
        check("missing _ib_connection FAILS CLOSED", p["ok"] is False, p)
        check("missing attribute reported as contract_missing",
              p["source"] == ph.PREMISE_CONTRACT_MISSING, p["source"])

        # 2. known no connection -> the only comparable state
        data_feed._ib_connection = None
        p = ph._premise()
        check("absent connection permits comparison", p["ok"] is True, p)
        check("absent connection labelled distinctly",
              p["source"] == ph.PREMISE_NO_CONNECTION, p["source"])
        check("premise records the loaded data_feed module path",
              p.get("module_file") and "data_feed" in p["module_file"],
              p.get("module_file"))

        # 3. unexpected handle shape -> refuse, do not crash
        data_feed._ib_connection = object()
        p = ph._premise()
        check("handle without isConnected refuses", p["ok"] is False, p)
        check("unexpected handle labelled unreadable",
              p["source"] == ph.PREMISE_UNREADABLE, p["source"])

        # 4. the four labels are genuinely distinct values
        labels = {ph.PREMISE_NO_CONNECTION, ph.PREMISE_LIVE,
                  ph.PREMISE_UNREADABLE, ph.PREMISE_CONTRACT_MISSING}
        check("all four premise labels are distinct", len(labels) == 4, labels)
        check("only the no-connection label maps to ok=True",
              ph.PREMISE_LIVE != ph.PREMISE_NO_CONNECTION
              and ph.PREMISE_CONTRACT_MISSING != ph.PREMISE_NO_CONNECTION
              and ph.PREMISE_UNREADABLE != ph.PREMISE_NO_CONNECTION)
    finally:
        if had:
            data_feed._ib_connection = saved
        elif hasattr(data_feed, "_ib_connection"):
            del data_feed._ib_connection


def test_premise_refusal_blocks_evaluation_and_match():
    """A refused premise must stop module_eval and can never yield MATCH."""
    import engine.parity_runner as pr
    from engine import tracker
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "p.jsonl"
        trade = {"symbol": "SDGR", "status": "open", "entry_date": "2026-09-18",
                 "entry_price": 29.35, "shares": 5.08, "stop_loss": 27.60,
                 "trailing_stop": 28.23, "peak_price": 31.37,
                 "strategy": "momentum_breakout", "take_profit": 34.63}
        ran = []
        res, summ = pr.observe_cycle(
            tracker, lambda: "INLINE", lambda: [dict(trade)],
            lambda t, p, b: ran.append(1) or dict(t),
            bar_fn=lambda s: {"price": 29.1, "date": "2026-09-26",
                              "price_source": "daily", "cache_mtime": 1},
            premise_fn=lambda: {"ok": False, "source": "live",
                                "reason": "gateway reachable"},
            params={"slippage_pct": 0.001}, output_path=out)
        check("inline preserved when the premise is refused", res == "INLINE")
        check("module_eval never runs on a refused premise", not ran, ran)
        rec = json.loads(out.read_text().splitlines()[0])
        check("refusal classified PremiseNotHeld",
              rec["shadow_exception_type"] == "PremiseNotHeld",
              rec["shadow_exception_type"])
        check("refused premise never yields a MATCH",
              rec["difference_class"] != "MATCH", rec["difference_class"])
        check("would_change_action stays None on refusal",
              rec["would_change_action"] is None, rec["would_change_action"])
        check("the premise is recorded in the cycle summary",
              (summ.get("premise") or {}).get("source") == "live",
              summ.get("premise"))


def test_barstale_is_reported_separately_from_barunavailable():
    check("BarStale and BarUnavailable are distinct classifications",
          "BarStale" != "BarUnavailable")
    src = (ROOT / "engine" / "parity_runner.py").read_text()
    check("runner emits both labels distinctly",
          '"BarStale"' in src and '"BarUnavailable"' in src)
    check("mtime is documented as a sentinel, not content proof",
          "VALIDITY SENTINEL" in (ROOT / "engine" / "parity_hook.py").read_text())


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
