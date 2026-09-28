"""Compatibility contract: tracker_v3_2dp

Preserves the state precision and round-fill-round behaviour of the current
inline tracker during canonical exit-module migration.

This adapter is a production-parity requirement, not a strategy rule.
Full-precision state is deferred to a separate controlled change.

This module has no production effect unless explicitly imported and wired by
tracker.py. As of this commit nothing imports it.

Scope, deliberately narrow. This adapter ONLY:
  1. converts tracker-compatible stored state into canonical input state
  2. converts canonical output back into tracker_v3_2dp stored state

It does not and must not: retrieve market data, add indicators, decide
commissions, write logs, save positions, send alerts, execute closes, or mutate
its input. It is not a second tracker.

Explicitly out of scope, belonging to Phase 0.5 operational hardening:
minimum-history handling, blanket-exception observability, alert escalation.
Also out of scope: full-precision state, new exit parameters, tracker
refactoring, and any Policy B or D logic.
"""

CONTRACT_VERSION = "tracker_v3_2dp"

# Fields the live tracker persists at 2dp. Sourced from tracker.check_open_trades
# and tracker._close_trade; see PRECISION_NOTE for why this matters across bars.
STORED_2DP = (
    "peak_price",
    "trailing_stop",
    "shares",
    "scale_out_price",
    "scale_out_shares",
    "scale_out_pnl",
    "scale_out_pnl_pct",
)

PRECISION_NOTE = """\
Within a single bar the live tracker computes on FULL precision locals: it
derives effective_stop from the unrounded new trailing stop, so the fill on a
same-bar stop is round(unrounded_eff * (1 - slippage), 2).

The rounding bites ACROSS bars. peak_price and trailing_stop are persisted at
2dp, and the next bar reads those rounded values back as its starting state.
That is the entire mechanism behind the observed exit_price differences on
FDX 2022-06-21 and NFLX 2022-07-22: the canonical module carried full-precision
trail forward while the tracker carried a 2dp-rounded one.

So the fill rule itself is a single round. It is state persistence, not the fill,
that compounds. Anyone tempted to "fix" the fill into a double round has
misread the mechanism.
"""


def _round2(value):
    """Round to 2dp, passing None and non-numerics through untouched."""
    if value is None or isinstance(value, bool):
        return value
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return value


def to_canonical_state(trade, params=None):
    """Build canonical input state from a tracker-stored trade record.

    Returns a NEW dict. `trade` is never mutated.

    Defaults replicate tracker.check_open_trades exactly:
      trailing_stop   -> stop_loss
      peak_price      -> entry_price
      original_shares -> shares
      scaled_out      -> False
    Getting these wrong is silent, so they are asserted in the unit tests
    rather than trusted.
    """
    params = params or {}
    stop_loss = trade["stop_loss"]
    return {
        "entry_price": trade["entry_price"],
        "stop_loss": stop_loss,
        "shares": trade["shares"],
        "original_shares": trade.get("original_shares", trade["shares"]),
        "trailing_stop": trade.get("trailing_stop", stop_loss),
        "peak_price": trade.get("peak_price", trade["entry_price"]),
        "scaled_out": trade.get("scaled_out", False),
        "take_profit": trade.get("take_profit"),
        "strategy": trade.get("strategy"),
        "trailing_pct": params.get("trailing_stop_pct", 0.10),
        "state_precision_contract": CONTRACT_VERSION,
    }


def apply_tracker_v3_2dp(trade, canonical, in_place=False):
    """Write canonical output back into tracker_v3_2dp stored representation.

    Only keys present in `canonical` are written, so a caller cannot
    accidentally introduce fields the live tracker never stored.

    Returns the updated record. Default is a copy; `in_place=True` is opt-in and
    explicit, because silent mutation of a live position record is exactly the
    class of bug this migration must not introduce.
    """
    out = trade if in_place else dict(trade)
    for key, value in canonical.items():
        if key == "state_precision_contract":
            continue
        out[key] = _round2(value) if key in STORED_2DP else value
    return out


def tracker_v3_2dp_fill(raw_price, slippage_pct, side="exit"):
    """Fill price as the live tracker computes it: one round, after slippage.

    Exit fills are reduced by slippage (tracker._close_trade); entry fills are
    increased. Returns 2dp, matching trade['exit_price'].
    """
    if side == "exit":
        return round(float(raw_price) * (1 - slippage_pct), 2)
    if side == "entry":
        return round(float(raw_price) * (1 + slippage_pct), 2)
    raise ValueError(f"side must be 'entry' or 'exit', got {side!r}")


def tracker_v3_2dp_slippage(raw_price, slippage_pct):
    """trade['exit_slippage'] — 4dp, not 2dp. Matches tracker._close_trade."""
    raw = float(raw_price)
    return round(raw - raw * (1 - slippage_pct), 4)
