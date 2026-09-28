"""Phase 4 module_eval — the canonical exit evaluation, for parity observation.

Ported from the PROVEN Phase 0 call sequence (Athena run_tracker_equivalence.py
step_module), which reproduced tracker.py across 4,730 bars with zero
decision-changing mismatches. It is not re-derived here: the ordering below is
that sequence, and any change to it invalidates the Phase 0 evidence.

MEASUREMENT ONLY. Dormant. Nothing imports this as of this commit.

OWNS ONLY
    - the canonical exit_policy call sequence
    - tracker_v3_2dp compatibility projection
    - a structured parity payload

DOES NOT
    load market data, recompute an indicator frame, save or close a position,
    write trade logs, decide commissions, submit orders, alert, touch
    psi_state.json or the queue, or mutate its input. The caller supplies the
    bar; this module never fetches one.

PRICE DETERMINISM — ENFORCED, NOT ASSUMED
    tracker.py line 819 does `live = get_live_price(...)` then
    `current_price = live if live else daily_price`. get_live_price returns a
    LIVE IBKR 1-min bar close, which varies between calls seconds apart. If a
    live price were ever in play, re-deriving the price here would produce
    mismatches caused by market movement rather than by the migration.

    Verified 2026-09-28: no IB Gateway is reachable on the VPS (127.0.0.1:4002
    refused), so get_live_price returns None and the daily Close is always used.
    That is also the condition under which Phase 0 was run -- its harness forced
    get_live_price to None -- so the zero-mismatch result transfers only for the
    daily-Close path.

    A gateway could be started later without anyone revisiting this file, so the
    premise is checked per bar rather than trusted: a bar whose price_source is
    not "daily" is refused, and the caller classifies it
    PRICE_SOURCE_NONDETERMINISTIC instead of comparing it.
"""

import copy

from engine import exit_policy
from engine.tracker_compat import (CONTRACT_VERSION, apply_tracker_v3_2dp,
                                   tracker_v3_2dp_fill)


class NonDeterministicPrice(RuntimeError):
    """The bar did not come from the deterministic daily Close."""


def to_module_pos(trade, params=None):
    """Map a tracker-stored trade onto the canonical module's schema.

    Returns (pos, seeding_notes). `trade` is never mutated.

    tracker stores a single `scaled_out` bool; the module counts `tiers_hit` and,
    once a tier fires, ratchets against `peak_after_first_tier`. tracker has no
    such field, so for a position ALREADY scaled out when parity first observes
    it there is nothing to map from and the stored `peak_price` is the only
    available proxy. That is a seeding assumption, not an equivalence, and it is
    reported so such comparisons can be discounted rather than silently trusted.

    Currently unexercised: all five open positions are unscaled
    (ABM, SDGR, TMO, WBD, SECZ, all momentum_breakout, scaled_out False).
    """
    seeding = []
    sl = trade["stop_loss"]
    entry = trade["entry_price"]
    ts = trade.get("trailing_stop", sl)
    peak = trade.get("peak_price", entry)
    scaled = bool(trade.get("scaled_out", False))

    if scaled:
        seeding.append("peak_after_first_tier seeded from stored peak_price; "
                       "tracker does not persist a post-tier peak")

    pos = {
        "symbol": trade.get("symbol"),
        "strategy": trade.get("strategy"),
        "entry_date": trade.get("entry_date"),
        "entry_price": entry,
        "shares": trade["shares"],
        "original_shares": trade.get("original_shares", trade["shares"]),
        "stop_loss": sl,
        "initial_stop": trade.get("initial_stop", sl),
        "take_profit": trade.get("take_profit"),
        "trailing_stop": ts,
        "peak_price": peak,
        "trough_price": trade.get("trough_price", entry),
        "highest_trailing_stop": max(ts, sl),
        "trail_activated": ts > sl,
        "tiers_hit": 1 if scaled else 0,
        "scaled_out": scaled,
        "scale_out_date": trade.get("scale_out_date"),
        "scale_out_price": trade.get("scale_out_price") or 0.0,
        "scale_out_shares": trade.get("scale_out_shares") or 0.0,
        "scale_out_proceeds": trade.get("scale_out_proceeds") or 0.0,
        "peak_before_first_tier": peak if not scaled else entry,
        "peak_after_first_tier": peak if scaled else None,
        "entry_commission": trade.get("entry_commission", 1.0),
        "commission_paid": trade.get("total_commission",
                                     trade.get("entry_commission", 1.0)),
    }
    return pos, seeding


def evaluate(trade, params, bar):
    """Evaluate ONE bar and return the tracker-shaped post-state.

    This is the module_eval handed to parity_runner.observe_cycle. It mirrors
    tracker.check_open_trades's per-trade body at the call sites the eventual
    swap will use, then projects the result back through tracker_v3_2dp so the
    comparison is against tracker's own precision contract rather than full
    precision.

    `bar` must carry: price, rsi, bearish_div, date, price_source.
    """
    if bar.get("price_source") != "daily":
        raise NonDeterministicPrice(
            f"price_source={bar.get('price_source')!r}; parity requires the "
            "deterministic daily Close (see module docstring)")

    out = copy.deepcopy(trade)
    pos, seeding = to_module_pos(trade, params)
    out["_parity_seeding"] = seeding
    out["_parity_contract"] = CONTRACT_VERSION

    price = float(bar["price"])
    day = bar["date"]

    # tracker skips the entry bar entirely (`if today == trade['entry_date']:
    # continue`), so no evaluation happens and state is unchanged. Mirror that
    # rather than evaluating a bar tracker never evaluated.
    if day == trade.get("entry_date"):
        out["_parity_action"] = "skipped_entry_day"
        return out

    rsi = bar.get("rsi")
    rsi = 50.0 if rsi is None else float(rsi)
    div = bool(bar.get("bearish_div", False))

    # --- the proven sequence; ordering is Phase 0's, do not reorder -----------
    exit_policy.update_peak(pos, price, params)
    eff = exit_policy.effective_stop(pos, params)

    # tracker persists the ratcheted peak and trail on EVERY bar, including an
    # exit bar, because the assignment precedes the exit check. Precision is
    # applied by the adapter, never by hand here -- that separation is the point
    # of tracker_compat, and duplicating round() calls would let the two drift.
    out = apply_tracker_v3_2dp(out, {"peak_price": pos["peak_price"],
                                     "trailing_stop": pos["trailing_stop"]})
    out["_parity_effective_stop"] = eff
    out["effective_stop"] = eff

    slip = params.get("slippage_pct", 0.001)
    comm = params.get("commission_per_trade", 1.00)

    if params.get("scale_out", False):
        tier = exit_policy.decide_scale_out(pos, price, params)
        if tier and not pos["scaled_out"]:
            # Same-bar fill, then short-circuit: tracker's `continue`.
            fill = price * (1 - slip)
            sell = pos["original_shares"] * tier["fraction"]
            entry = trade["entry_price"]
            # Both P&L fields use the UNROUNDED fill, matching tracker exactly.
            out = apply_tracker_v3_2dp(out, {
                "shares": trade["shares"] - sell,
                "scale_out_price": fill,
                "scale_out_shares": sell,
                "scale_out_pnl": (fill - entry) * sell,
                "scale_out_pnl_pct": (fill - entry) / entry * 100,
            })
            out["scale_out_date"] = day
            # Unrounded size retained for assertion only; tracker computes P&L
            # from this, not from the 2dp stored field.
            out["_parity_sold_shares"] = sell
            out["scale_out_commission"] = comm
            out["total_commission"] = trade.get("total_commission", 1.0) + comm
            out["scaled_out"] = True
            out["_parity_action"] = "scale_out"
            return out

    reason, px = exit_policy.decide_exit(pos, price, rsi, div, params,
                                         holding_days=None)
    if reason:
        out["status"] = "closed"
        out["exit_date"] = day
        out["exit_reason"] = reason
        # decide_exit returns the DECISION price; the fill model is tracker's.
        out["exit_price"] = tracker_v3_2dp_fill(px, slip, side="exit")
        out["_parity_action"] = "exit"
        return out

    out["_parity_action"] = "hold"
    return out


def make_bar(row, price_source="daily"):
    """Build a bar from an indicator-augmented frame's last row.

    Mirrors tracker's reads exactly: Close, rsi, bearish_div, and the index date
    truncated to 10 chars. Kept here so the caller does not have to reimplement
    those reads and drift from tracker.
    """
    import pandas as pd
    rsi = row.get("rsi")
    return {
        "price": float(row["Close"]),
        "rsi": None if rsi is None or pd.isna(rsi) else float(rsi),
        "bearish_div": bool(row.get("bearish_div", False)),
        "date": str(row.name)[:10],
        "price_source": price_source,
    }
