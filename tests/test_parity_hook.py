"""Tests for engine/parity_hook.py -- the single production bridge.

Two properties must be PROVABLE, not intended:

  ARES_PARITY unset : behaviour and import surface equivalent to the old path.
  ARES_PARITY=1     : inline tracker runs exactly once and stays authoritative;
                      every parity-side failure is observational only.

Phase 0.5: the bridge no longer reconstructs inputs. It reads the tracker's
decision-input capture after the inline call. The Phase 4 reconstruction tests
(_bar_fn, _bar_recheck, cache mtime, _premise, _ib_connection) were removed
because the code they covered was deleted, not disabled.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

# Registered telemetry shape is single-sourced from the tracker-diff contract, so
# a registration change cannot leave these suites asserting a stale shape.
import tracker_diff as td  # noqa: E402

FAILS = []
COUNT = 0

PARITY_MODULES = ("engine.parity_eval", "engine.parity_runner",
                  "engine.parity_compare", "engine.tracker_compat")


def check(label, cond, got=None):
    global COUNT
    COUNT += 1
    if cond:
        print(f"  pass  {label}")
    else:
        print(f"  FAIL  {label}  {got if got is not None else ''}")
        FAILS.append(label)


def func(name):
    """AST node for a top-level function in parity_hook.

    Text scanning has produced five false results in this project -- matching a
    docstring, a comment, a prose mention -- so structural questions go to the AST.
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


def stub_trade():
    return {"symbol": "ABM", "strategy": "momentum_breakout", "status": "open",
            "entry_date": "2026-09-09", "entry_price": 50.65, "shares": 2.94,
            "original_shares": 2.94, "stop_loss": 48.67, "trailing_stop": 48.67,
            "peak_price": 50.91, "take_profit": 59.76}


# --- disabled mode must not import the parity stack -------------------------
def test_disabled_mode_does_not_import_parity_stack():
    body = f'''
import sys, importlib.abc
BLOCKED = {PARITY_MODULES!r}

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name in BLOCKED:
            raise ImportError("blocked by test: " + name)
        return None

sys.meta_path.insert(0, Blocker())

import engine.parity_hook as hook
import engine.tracker as tracker

calls = []
tracker.check_open_trades = lambda: calls.append(1) or "INLINE_RESULT"
hook.tracker = tracker

assert hook.enabled() is False
res = hook.observed_check_open_trades()
assert res == "INLINE_RESULT", res
assert len(calls) == 1, f"inline called {{len(calls)}} times"
leaked = [m for m in BLOCKED if m in sys.modules]
assert not leaked, f"parity modules imported while disabled: {{leaked}}"
print("CHILD_OK")
'''
    rc, out = run_child(body)
    check("parity_hook imports with the whole parity stack un-importable",
          "CHILD_OK" in out, out[-400:])
    check("disabled mode: inline called exactly once, stack never imported",
          rc == 0 and "CHILD_OK" in out, out[-900:])


def test_disabled_mode_import_surface_is_minimal():
    src = (ROOT / "engine" / "parity_hook.py").read_text()
    head = src.split("def enabled")[0]
    for name in ("parity_eval", "parity_runner", "parity_compare",
                 "tracker_compat", "data_feed", "indicators"):
        check(f"module body does not eagerly import {name}",
              f"import {name}" not in head
              and f"from engine.{name}" not in head
              and f"from engine import {name}" not in head)
    check("module body eagerly imports only tracker (plus stdlib os)",
          "from engine import tracker" in head)


# --- Phase 0.5: zero outbound calls, zero reconstruction --------------------
def test_hook_performs_no_market_access_at_all():
    src = (ROOT / "engine" / "parity_hook.py").read_text()
    code = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith("#"))
    body = code.split('"""', 2)[-1] if code.count('"""') >= 2 else code
    for banned in ("get_live_price", "load_stock", "download_stock",
                   "_get_ib", "yfinance", "add_indicators"):
        check(f"hook never calls {banned}", f"{banned}(" not in body, banned)
    check("the reconstruction helpers are GONE, not dormant",
          all(f"def {n}" not in src for n in
              ("_bar_fn", "_bar_recheck", "_cache_file", "_premise")))
    from engine import parity_hook
    for gone in ("_bar_fn", "_bar_recheck", "_cache_file", "_premise",
                 "PREMISE_LIVE", "PREMISE_NO_CONNECTION"):
        check(f"{gone} no longer exists on the module",
              not hasattr(parity_hook, gone))


def test_packets_reads_the_store_read_only():
    from engine import parity_hook, tracker
    saved = tracker._LAST_EVAL
    try:
        tracker._LAST_EVAL = {"cycle_token": 7,
                              "packets": {"ABM": {"cycle_token": 7,
                                                  "price": 49.78}}}
        tok, pk = parity_hook._packets()
        check("token read from the store", tok == 7, tok)
        check("packets read from the store", pk["ABM"]["price"] == 49.78)
        pk["ABM"]["price"] = 0.0
        pk["INJECTED"] = {}
        check("bridge returns a copy: store packets dict not extended",
              "INJECTED" not in tracker._LAST_EVAL["packets"])
        check("bridge never clears the store",
              "ABM" in tracker._LAST_EVAL["packets"])
        check("bridge does not write a token",
              tracker._LAST_EVAL["cycle_token"] == 7)
    finally:
        tracker._LAST_EVAL = saved


def test_packets_fails_closed_on_a_missing_store():
    """A renamed or absent store must not read as 'no difference found'."""
    from engine import parity_hook, tracker
    saved = tracker._LAST_EVAL
    try:
        del tracker._LAST_EVAL
        tok, pk = parity_hook._packets()
        check("absent store yields no token", tok is None, tok)
        check("absent store yields no packets", pk == {}, pk)
        tracker._LAST_EVAL = "not-a-dict"
        tok, pk = parity_hook._packets()
        check("malformed store yields no token", tok is None, tok)
        check("malformed store yields no packets", pk == {}, pk)
    finally:
        tracker._LAST_EVAL = saved


def test_packets_read_after_inline_not_before():
    src = (ROOT / "engine" / "parity_hook.py").read_text()
    call = src.split("observe_cycle(")[-1]
    check("packets_fn is passed UNCALLED", "packets_fn=_packets" in call, call[:200])
    check("_packets is not invoked in the bridge body",
          "_packets()" not in src.split("def observed_check_open_trades")[1])
    check("docstring states the post-inline ordering requirement",
          "does not exist until that call runs" in src
          or "Called AFTER the inline tracker" in src)


# --- params failure must not block the inline cycle ------------------------
def test_params_failure_is_observational_only():
    body = f'''
import json, tempfile
from pathlib import Path
import engine.tracker as tracker
import engine.parity_runner as pr
import engine.parity_hook as hook

out = Path(tempfile.mkdtemp()) / "parity.jsonl"
_real = pr.observe_cycle
pr.observe_cycle = lambda *a, **k: _real(*a, **{{**k, "output_path": out}})

def boom():
    raise RuntimeError("config gone")
tracker._load_params = boom

calls = []
tracker.check_open_trades = lambda: calls.append(1) or "INLINE_RESULT"
tracker.load_trades = lambda: [{stub_trade()!r}]
tracker._LAST_EVAL = {{"cycle_token": 3,
                      "packets": {{"ABM": {{"cycle_token": 3, "price": 49.0,
                                          "price_source": "IBKR", "rsi": 55.0,
                                          "bearish_div": False,
                                          "bar_date": "2026-09-26"}}}}}}
hook.tracker = tracker

import os
os.environ["ARES_PARITY"] = "1"
res = hook.observed_check_open_trades()

assert res == "INLINE_RESULT", res
assert len(calls) == 1, f"inline called {{len(calls)}} times"
recs = [json.loads(l) for l in out.read_text().splitlines()] if out.exists() else []
assert recs, "no parity record written"
r = recs[0]
assert r["shadow_exception_type"] == "ParamsUnavailable", r["shadow_exception_type"]
assert "config gone" in (r["shadow_exception_message"] or "")
assert r["would_change_action"] is not False, r["would_change_action"]
print("CHILD_OK", r["difference_class"])
'''
    rc, out = run_child(body, {"ARES_PARITY": "1"})
    check("params failure: inline called exactly once, result preserved",
          rc == 0 and "CHILD_OK" in out, out[-1200:])
    check("params failure classified ParamsUnavailable, not a match",
          "CHILD_OK" in out and "MATCH" not in out.split("CHILD_OK")[-1],
          out[-200:])


def test_params_fn_passed_uncalled():
    src = (ROOT / "engine" / "parity_hook.py").read_text()
    call = src.split("observe_cycle(")[-1]
    check("params_fn is passed uncalled", "params_fn=_params" in call, call[:300])
    check("params is NOT pre-evaluated at the call site",
          "params=_params()" not in src)
    check("no eager params call in the bridge body",
          "_params()" not in src.split("def observed_check_open_trades")[1])


# --- private-dependency contracts ------------------------------------------
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


def test_last_eval_contract():
    """The second registered private dependency, per amendment dbffb87."""
    from engine import tracker
    check("tracker._LAST_EVAL exists", hasattr(tracker, "_LAST_EVAL"))
    check("_LAST_EVAL has the registered shape",
          set(tracker._LAST_EVAL) == set(td.REGISTERED_STORE_KEYS),
          sorted(tracker._LAST_EVAL))
    check("tracker._EVAL_CYCLE_SEQ exists",
          isinstance(getattr(tracker, "_EVAL_CYCLE_SEQ", None), int))
    check("data_feed._ib_connection is NO LONGER a parity dependency",
          "_ib_connection" not in
          (ROOT / "engine" / "parity_hook.py").read_text().split('"""', 2)[-1])


def test_inline_fallback_defaults_match_tracker_source():
    import re
    from engine import parity_hook, tracker
    cfg = tracker._load_params()
    src = (ROOT / "engine" / "tracker.py").read_text()
    for key, expected in parity_hook.INLINE_FALLBACKS.items():
        check(f"{key} genuinely absent from config (premise of the fallback)",
              key not in cfg, cfg.get(key))
        found = re.findall(r"params\.get\('" + key + r"',\s*([0-9.]+)\)", src)
        check(f"tracker hardcodes a fallback for {key}", bool(found), found)
        check(f"all tracker fallbacks for {key} agree", len(set(found)) == 1, found)
        check(f"parity inherits tracker's {key} fallback exactly",
              found and float(found[0]) == expected, (found, expected))
    hp = parity_hook._params()
    check("_params supplies the inherited slippage", hp["slippage_pct"] == 0.001)
    check("_params supplies the inherited commission",
          hp["commission_per_trade"] == 1.00)


# --- no double evaluation --------------------------------------------------
def test_no_fallback_recall():
    import ast
    fn = func("observed_check_open_trades")
    refs = [n for n in ast.walk(fn) if isinstance(n, ast.Attribute)
            and n.attr == "check_open_trades"]
    check("exactly two references to check_open_trades in the bridge",
          len(refs) == 2, len(refs))
    check("the bridge contains no try/except, so no fallback re-call is "
          "structurally possible",
          not [n for n in ast.walk(fn) if isinstance(n, ast.Try)])
    check("observe_cycle is called exactly once",
          sum(1 for c in calls_in("observed_check_open_trades")
              if c == "observe_cycle") == 1)


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
