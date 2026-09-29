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
    decision-input packet; this module never fetches or derives one.

PRICE PROVENANCE — SUPPLIED, NOT RECONSTRUCTED (Phase 0.5)
    This module does NOT derive a price. It consumes the decision-input packet
    the inline tracker captured for that invocation (tracker._LAST_EVAL), so the
    canonical module is evaluated against the exact values the authoritative
    decision used.

    This replaces the Phase 4 reconstruction approach, which was withdrawn after
    a production-time measurement: tracker.py:819 does
    `current_price = live if live else daily_price`, and the gateway IS up during
    scheduled cycles (restart_gateway.sh at 13:00/16:00/17:25, run_ares.sh at
    13:30/21:00 — the last real cycle reported "(live)" for all five positions).
    A live IBKR 1-min close is not reproducible after the fact, so any
    re-derivation would have produced mismatches caused by market movement rather
    than by the migration.

    Consequence: live and daily prices are now equally comparable, and
    `price_source` is a COVERAGE DIMENSION recorded on every record rather than a
    refusal reason. Refusal moved upstream: a missing or stale packet is
    INLINE_INPUT_NOT_CAPTURED, decided by the runner before this module is called.
"""

import copy

from engine import exit_policy
from engine.tracker_compat import (CONTRACT_VERSION, apply_tracker_v3_2dp,
                                   tracker_v3_2dp_fill)


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


def evaluate(trade, params, packet):
    """Evaluate ONE bar and return the tracker-shaped post-state.

    This is the module_eval handed to parity_runner.observe_cycle. It mirrors
    tracker.check_open_trades's per-trade body at the call sites the eventual
    swap will use, then projects the result back through tracker_v3_2dp so the
    comparison is against tracker's own precision contract rather than full
    precision.

    `packet` is the tracker's captured decision-input packet and must carry:
    price, price_source, rsi, bearish_div, bar_date. Its validity (presence and
    cycle-token match) is established by the runner BEFORE this is called; this
    function assumes a verified current-invocation packet.
    """
    out = copy.deepcopy(trade)
    pos, seeding = to_module_pos(trade, params)
    out["_parity_seeding"] = seeding
    out["_parity_contract"] = CONTRACT_VERSION

    price = float(packet["price"])
    day = packet["bar_date"]
    out["_parity_price_source"] = packet.get("price_source")

    # tracker skips the entry bar entirely (`if today == trade['entry_date']:
    # continue`), so no evaluation happens and state is unchanged. Mirror that
    # rather than evaluating a bar tracker never evaluated.
    if day == trade.get("entry_date"):
        out["_parity_action"] = "skipped_entry_day"
        return out

    rsi = packet.get("rsi")
    rsi = 50.0 if rsi is None else float(rsi)
    # Already a bool in the packet: the tracker captured its own truth-value
    # interpretation, which must not be re-derived or normalised here.
    div = bool(packet.get("bearish_div", False))

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


# make_bar() was REMOVED in Phase 0.5. It reconstructed a bar from the daily
# cache, which is no longer how parity obtains inputs and would now be a second,
# non-authoritative source competing with the tracker's captured packet. Deleted
# rather than left dormant so no future reader mistakes it for the live path.
