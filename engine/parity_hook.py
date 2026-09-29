"""Phase 4 call-site wiring. The ONE place production touches the parity stack.

OFF BY DEFAULT. Unless ARES_PARITY=1, observed_check_open_trades() delegates
straight to tracker.check_open_trades() and the parity stack is never even
IMPORTED. Only engine.tracker is imported eagerly here, so a syntax error,
missing dependency or import-time fault anywhere in the parity stack cannot
prevent Ares from starting while parity is off. Asserted by
tests/test_parity_hook.py, not merely intended.

When enabled, the inline tracker remains the SOLE decision authority. The
observer records a comparison and returns the inline result unchanged.

NO OUTBOUND CALLS AT ALL (Phase 0.5)
    This module reads no market data. Inputs come from tracker._LAST_EVAL, the
    decision-input packet the inline tracker captured for the invocation that
    just completed, so parity adds zero IBKR connections, zero gateway log
    entries, zero yfinance requests and zero cache reads.

    The Phase 4 reconstruction path (_bar_fn, _bar_recheck, cache-mtime
    sentinels, the _premise gateway check and the data_feed._ib_connection
    dependency) was DELETED, not disabled. A production-time measurement showed
    the gateway is up during scheduled cycles, so reconstruction could not have
    matched the inline price anyway, and leaving it dormant would confuse which
    input is authoritative. See TRACKER_MIGRATION_PLAN_PHASE_0_5.md.

    Private dependencies are now: tracker._load_params (same effective policy
    config) and tracker._LAST_EVAL (registered Phase 0.5 capture contract).
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


def _packets():
    """Read the completed invocation's decision-input capture.

    Returns (cycle_token, packets). Called AFTER the inline tracker: for a
    tracker-owned token, the token for a call does not exist until that call
    runs. Read-only -- the bridge never clears, writes or reorders the store.

    A missing store attribute returns (None, {}), which the runner classifies
    INLINE_INPUT_NOT_CAPTURED for every symbol. It must NOT be read as "no
    difference found": absence of evidence is not evidence of agreement.
    """
    store = getattr(tracker, "_LAST_EVAL", None)
    if not isinstance(store, dict):
        return None, {}
    return store.get("cycle_token"), dict(store.get("packets") or {})


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
    # params_fn/_packets are passed UNCALLED: evaluating them here would put them
    # outside the fail-open boundary.
    result, summary = parity_runner.observe_cycle(
        tracker, tracker.check_open_trades, tracker.load_trades,
        parity_eval.evaluate, packets_fn=_packets, params_fn=_params,
        lineage=_lineage())
    att, wrt = summary.get("attempted", 0), summary.get("written", 0)
    if att != wrt or summary.get("shadow_system_error") or summary.get("capture_error"):
        print(f"  [parity] attempted={att} written={wrt} "
              f"token={summary.get('capture_cycle_token')} "
              f"capture_err={summary.get('capture_error')} "
              f"err={summary.get('shadow_system_error')}")
    return result
