"""Phase 4 call-site wiring. The ONE place production touches the parity stack.

OFF BY DEFAULT. Unless ARES_PARITY=1, observed_check_open_trades() delegates
straight to tracker.check_open_trades() and the parity stack is never even
IMPORTED. Only engine.tracker is imported eagerly here, so a syntax error,
missing dependency or import-time fault anywhere in the parity stack cannot
prevent Ares from starting while parity is off. That is asserted by
tests/test_parity_hook.py, not merely intended.

When enabled, the inline tracker remains the SOLE decision authority. The
observer records a comparison and returns the inline result unchanged.

NO NEW OUTBOUND CALLS
    This module never calls get_live_price, and never triggers a download.
    load_stock() re-downloads whenever the cached CSV is older than 20h, so it is
    called with max_age_hours=inf and only for symbols whose cache file already
    exists -- parity therefore adds zero IBKR connections, zero gateway log
    entries and zero yfinance requests.

    The cost of reading the cache without refreshing it is that the inline path
    MIGHT refresh it during the same cycle, leaving parity on an older bar. That
    is checked rather than assumed: _bar_fn records the cache mtime pre-inline
    and _bar_recheck compares it post-inline, refusing that symbol if the file
    moved underneath.

    The determinism premise is instead verified from state the inline path itself
    produced: data_feed._ib_connection is a module-level cached handle, set to
    None whenever a connect fails. tracker.check_open_trades calls
    get_live_price per position, so AFTER the inline call that handle reports
    whether a live price was actually reachable during this cycle -- read without
    opening anything. Checked once per cycle, not once per symbol.
"""

import os

# Eager: production dependency only. Everything parity-specific is imported
# lazily inside the enabled branch -- see the module docstring.
from engine import tracker

ENABLE_ENV = "ARES_PARITY"

# Keys the inline tracker consumes but that config/strategy_params.json does NOT
# contain -- tracker falls back to these literals at engine/tracker.py:752,850
# (slippage_pct) and :753,851 (commission_per_trade). The parity evaluator needs
# the same values, and without this they would be two independent hardcoded
# copies: change tracker's default and parity would keep comparing against the
# old one while still reporting MATCH on a fill that actually differed.
#
# Declared here as an explicit inherited-default contract and asserted against
# tracker's own source by tests/test_parity_hook.py, so divergence breaks the
# build instead of silently corrupting the comparison.
INLINE_FALLBACKS = {"slippage_pct": 0.001, "commission_per_trade": 1.00}


def enabled():
    return os.environ.get(ENABLE_ENV) == "1"


def _cache_file(symbol):
    from engine.data_feed import DATA_DIR
    return DATA_DIR / f"{symbol}.csv"


def _bar_fn(symbol):
    """Build the bar from the on-disk daily cache, mirroring tracker's reads.

    Strictly read-only: no network access, no live-price attempt, no refresh.
    A missing cache file raises rather than downloading, so the runner records an
    unavailable bar instead of parity causing a download nothing asked for.

    With insufficient history add_indicators may raise while constructing the
    MACD-derived columns (ta.macd returns None, then the column access raises
    AttributeError) rather than returning cleanly. Either way the runner records
    the resulting unavailable-bar condition per symbol instead of treating a
    missing evaluation as agreement.
    """
    from engine import parity_eval
    from engine.data_feed import load_stock
    from engine.indicators import add_indicators
    path = _cache_file(symbol)
    if not path.exists():
        raise FileNotFoundError(
            f"no cached daily data for {symbol}; parity will not download")
    mtime = path.stat().st_mtime
    df = load_stock(symbol, max_age_hours=float("inf"))
    bar = parity_eval.make_bar(add_indicators(df).iloc[-1],
                               price_source="daily")
    bar["cache_mtime"] = mtime
    return bar


def _bar_recheck(symbol, bar):
    """Post-inline: did the inline path move the cache out from under this bar?

    mtime is a VALIDITY SENTINEL, not proof of content identity. Any observed
    change refuses the comparison, which is what Phase 4 needs. An UNCHANGED
    mtime must never later be reinterpreted as proof that two bars are
    mathematically identical. If that stronger evidence is ever required, add a
    content hash as a new schema revision -- not silently inside schema v1.
    """
    path = _cache_file(symbol)
    if not path.exists():
        return "daily cache disappeared during the cycle"
    if path.stat().st_mtime != bar.get("cache_mtime"):
        return ("daily cache was refreshed during the inline cycle; the "
                "pre-inline bar is not the bar the inline path evaluated")
    return None


# The four premise states are kept DISTINGUISHABLE on purpose. Collapsing them
# into one falsey test is what made the first version fail open: a missing
# attribute and an absent connection both read as None, so renaming
# _ib_connection upstream would have silently looked like "no gateway" and
# allowed comparison. Only ONE of these four permits a daily-cache comparison.
PREMISE_NO_CONNECTION = "daily"          # known: nothing connected  -> compare
PREMISE_LIVE = "live"                    # reachable gateway        -> refuse
PREMISE_UNREADABLE = "unknown"           # handle present, unusable -> refuse
PREMISE_CONTRACT_MISSING = "contract_missing"   # attr gone         -> refuse


def _premise():
    """Did the inline path run on the deterministic daily Close this cycle?

    Called after the inline tracker. Reads the cached IBKR handle only; opens no
    connection. Fails CLOSED: absence of trustworthy evidence never defaults to
    "daily".
    """
    try:
        from engine import data_feed
    except Exception as exc:                        # noqa: BLE001
        return {"ok": False, "source": PREMISE_UNREADABLE,
                "reason": f"data_feed unimportable: {type(exc).__name__}: {exc}"}

    info = {"module_file": getattr(data_feed, "__file__", None)}

    # Contract check FIRST. hasattr, not getattr-with-default: the attribute
    # going away is a broken compatibility contract, not an idle connection.
    if not hasattr(data_feed, "_ib_connection"):
        return {"ok": False, "source": PREMISE_CONTRACT_MISSING,
                "reason": "data_feed._ib_connection is absent; the premise "
                          "contract no longer holds and no daily-bar "
                          "comparison may be inferred from its absence",
                **info}

    conn = data_feed._ib_connection
    if conn is None:
        return {"ok": True, "source": PREMISE_NO_CONNECTION,
                "detail": "no cached IBKR connection after the inline cycle",
                **info}

    if not hasattr(conn, "isConnected"):
        return {"ok": False, "source": PREMISE_UNREADABLE,
                "reason": f"cached handle of unexpected type "
                          f"{type(conn).__name__} has no isConnected()",
                **info}
    try:
        connected = conn.isConnected()
    except Exception as exc:                        # noqa: BLE001
        return {"ok": False, "source": PREMISE_UNREADABLE,
                "reason": f"handle unreadable: {type(exc).__name__}: {exc}",
                **info}

    if connected:
        return {"ok": False, "source": PREMISE_LIVE,
                "reason": "IBKR gateway reachable during this cycle; the inline "
                          "path may have used a live 1-min close, which is not "
                          "reproducible",
                **info}
    return {"ok": True, "source": PREMISE_NO_CONNECTION,
            "detail": "cached IBKR handle present but disconnected", **info}


def _params():
    # tracker._load_params is the single source of live config. Reimplementing
    # the load here would create a second source that could drift from the one
    # the inline path actually used, which is the opposite of what parity needs.
    # Passed to the runner as a CALLABLE so a config-load failure degrades the
    # parity record instead of raising before the inline tracker can run.
    p = dict(tracker._load_params())
    p.setdefault("exit_policy", "A")
    for key, value in INLINE_FALLBACKS.items():
        p.setdefault(key, value)
    return p


def _lineage():
    import hashlib
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    out = {"production_commit": None, "exit_policy_md5": None}
    try:
        out["production_commit"] = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10).stdout.strip() or None
    except Exception:                               # noqa: BLE001
        pass
    try:
        out["exit_policy_md5"] = hashlib.md5(
            (root / "engine" / "exit_policy.py").read_bytes()).hexdigest()
    except Exception:                               # noqa: BLE001
        pass
    return out


def observed_check_open_trades():
    """Run the cycle, optionally under parity observation. Returns tracker's result."""
    if not enabled():
        return tracker.check_open_trades()

    from engine import parity_eval, parity_runner

    # Deliberately NOT wrapped in try/except calling check_open_trades again.
    # observe_cycle already invokes the inline tracker internally and is itself
    # fail-open for every parity-side failure, so a fallback call here would
    # re-run the cycle and evaluate every position TWICE. Anything that escapes
    # observe_cycle is a genuine inline exception and must propagate exactly as
    # it does today.
    #
    # Note params_fn/_lineage are passed UNCALLED: evaluating them here would put
    # them outside the fail-open boundary.
    result, summary = parity_runner.observe_cycle(
        tracker, tracker.check_open_trades, tracker.load_trades,
        parity_eval.evaluate, bar_fn=_bar_fn, bar_recheck=_bar_recheck,
        params_fn=_params,
        premise_fn=_premise, lineage=_lineage())
    att, wrt = summary.get("attempted", 0), summary.get("written", 0)
    premise = summary.get("premise") or {}
    if att != wrt or summary.get("shadow_system_error") or not premise.get("ok", True):
        print(f"  [parity] attempted={att} written={wrt} "
              f"premise={premise.get('source')} "
              f"err={summary.get('shadow_system_error')}")
    return result
