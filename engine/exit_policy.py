"""Canonical exit policy — the single implementation of Ares' exit rules.

Block A of the V4 pre-registration (ROADMAP.md) requires one exit implementation
called by Ares paper operation, Athena's replay, label generation, and the final
portfolio simulation. Entries are already pinned: Athena imports live signals.py
and asserts its md5. Exits were a parallel reimplementation until this module.

Design constraints:

- **Pure.** No I/O, no price fetching, no params loading. Callers pass state and a
  bar; the module returns decisions. Ares supplies live IBKR prices, Athena
  supplies daily bars, and neither source is baked in. That price-source
  difference is irreducible and this module does not close it — it only stops the
  two from *also* differing in rule logic.
- **Policy A is byte-faithful to tracker.py as of 2026-09-25**, including its
  defects, because step 2 validates the module by reproducing Run A' exactly. A
  module that "fixed" things while being validated would prove nothing.

POLICY_A_REPRODUCED_DEFECTS documents what is deliberately preserved.
"""

EXIT_POLICY_MODULE_VERSION = "exit_policy_v1"

# Deliberately reproduced from tracker.py. Repairing any of these changes the
# trade population and belongs at a V4 boundary, measured against the bar Run B
# failed — not assumed beneficial.
POLICY_A_REPRODUCED_DEFECTS = {
    "divergence_elif_swallows_mean_reversion": (
        "tracker.py:885-892 — when bearish_div is True on a mean_reversion trade "
        "the elif branch is entered but its body does nothing, so the "
        "mean_reversion_complete check below is unreachable on that bar. Inert "
        "today because all four divergence columns are permanently False in "
        "production; repairing the divergence feed would ACTIVATE this bug."
    ),
    "scale_out_skips_stop_check": (
        "tracker.py:872 — the scale-out branch continues, so no stop, RSI, or "
        "strategy exit is evaluated on the bar a tranche is sold. A gap down "
        "through the stop on a take-profit bar is not acted on until the next bar."
    ),
    "trail_seeded_at_entry": (
        "peak_price seeds at entry_price and the trail is live from entry, so a "
        "trail exit below entry is labelled trailing_stop rather than stop_loss "
        "whenever peak rose enough to lift the trail above the initial stop. This "
        "produces the documented dead band: peak +6.7% to +11.1% exits at a loss "
        "labelled trailing_stop."
    ),
}

# Pre-registered replay policies. Frozen 2026-09-25 before any replay code.
# tiers: list of (peak_gain_pct, fraction_of_original_shares) take-profit tiers.
# trail_activation: 'entry' | 'first_tier' | float (peak gain pct threshold).
POLICIES = {
    "A": {  # control — current production behaviour
        "tiers": [(0.18, 0.50)],
        "trail_activation": "entry",
    },
    "B": {  # trail activates only after the first take-profit tier
        "tiers": [(0.18, 0.50)],
        "trail_activation": "first_tier",
    },
    "C": {  # pre-specified tiered take-profit
        "tiers": [(0.15, 0.25), (0.30, 0.25), (0.50, 0.25)],
        "trail_activation": "first_tier",
    },
    "D": {  # minimal structural correction — activation derived from trail geometry
        "tiers": [(0.18, 0.50)],
        # trail = peak * 0.90 >= entry requires peak >= +11.11%; the minimum peak
        # at which the trail can lock any gross profit. Not fitted.
        "trail_activation": 0.112,
    },
}

MAX_HOLD_DAYS = 170  # registered: binds 4.1% of Universe A, 2.0% of Universe B


def _policy(params):
    return POLICIES[params.get("exit_policy", "A")]


def trail_is_active(pos, params):
    """Whether the trailing stop may control the exit on this bar."""
    activation = _policy(params)["trail_activation"]
    if activation == "entry":
        return True
    if activation == "first_tier":
        return pos.get("tiers_hit", 0) > 0 or bool(pos.get("scaled_out"))
    gain = (pos["peak_price"] - pos["entry_price"]) / pos["entry_price"]
    return gain >= float(activation)


def update_peak(pos, price, params):
    """Ratchet peak_price and trailing_stop. Mutates pos. Returns True if changed.

    Faithful to tracker.py: the trail only ever ratchets upward, and peak_price
    seeds at entry_price so the trail is live from entry under Policy A.
    """
    changed = False
    if price > pos["peak_price"]:
        pos["peak_price"] = price
        pos["peak_date"] = pos.get("_bar_date")
        new_trail = price * (1 - params.get("trailing_stop_pct", 0.10))
        if new_trail > pos["trailing_stop"]:
            pos["trailing_stop"] = new_trail
            if not pos.get("trail_activated") and trail_is_active(pos, params):
                pos["trail_activated"] = True
                pos["trail_activation_date"] = pos.get("_bar_date")
        pos["highest_trailing_stop"] = max(
            pos.get("highest_trailing_stop", pos["trailing_stop"]), pos["trailing_stop"]
        )
        changed = True
    if price < pos.get("trough_price", pos["entry_price"]):
        pos["trough_price"] = price
        pos["trough_date"] = pos.get("_bar_date")
        changed = True
    return changed


def effective_stop(pos, params):
    """The stop actually controlling this bar."""
    if trail_is_active(pos, params):
        return max(pos["stop_loss"], pos["trailing_stop"])
    return pos["stop_loss"]


def decide_scale_out(pos, price, params):
    """Next unmet take-profit tier, or None.

    Returns {'tier_index', 'tier_gain_pct', 'fraction'}. Callers book the tranche;
    this module does not compute proceeds or commissions.
    """
    if not params.get("scale_out", False):
        return None
    tiers = _policy(params)["tiers"]
    hit = pos.get("tiers_hit", 0)
    if hit >= len(tiers):
        return None
    gain_pct, fraction = tiers[hit]
    # Policy A preserves tracker.py's absolute take_profit field when present, so
    # the module reproduces stored values rather than recomputing from entry.
    if pos.get("take_profit") and hit == 0 and params.get("exit_policy", "A") == "A":
        threshold = pos["take_profit"]
    else:
        threshold = pos["entry_price"] * (1 + gain_pct)
    if threshold <= 0 or price < threshold:
        return None
    return {"tier_index": hit, "tier_gain_pct": gain_pct, "fraction": fraction}


def decide_exit(pos, price, rsi, bearish_div, params, holding_days=None):
    """Return (reason, exit_price) or (None, None).

    Evaluation order is tracker.py's exactly. Callers MUST check decide_scale_out
    first and skip this function when a tier fires, reproducing the continue at
    tracker.py:872 (see POLICY_A_REPRODUCED_DEFECTS).
    """
    stop = effective_stop(pos, params)
    strategy = pos.get("strategy")

    if price <= stop:
        trail_controls = (
            trail_is_active(pos, params) and pos["trailing_stop"] > pos["stop_loss"]
        )
        return ("trailing_stop" if trail_controls else "stop_loss"), stop

    if rsi is not None and rsi > params.get("rsi_extreme_high", 90):
        return "emotional_extreme", price

    # Faithful reproduction: this elif is ENTERED for any strategy when
    # bearish_div is True, but only acts on momentum strategies. For
    # mean_reversion it therefore swallows the check below on that bar.
    if bearish_div:
        if strategy in ("momentum_breakout", "trend_continuation"):
            return "bearish_divergence", price
        return None, None

    if strategy == "mean_reversion" and rsi is not None and rsi > 70:
        return "mean_reversion_complete", price

    if holding_days is not None and holding_days >= MAX_HOLD_DAYS:
        return "time_stop", price

    return None, None


def new_position_state(entry_price, entry_date, stop_loss, take_profit, strategy):
    """Instrumented position state. Fields the pre-registration requires but which
    Athena's saved output currently lacks: peak_price, peak_date, trough_price,
    mae, trail activation, tier counts, time_stop_bound.
    """
    return {
        "entry_price": entry_price,
        "entry_date": entry_date,
        "initial_stop": stop_loss,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "strategy": strategy,
        "trailing_stop": stop_loss,
        "peak_price": entry_price,
        "peak_date": entry_date,
        "trough_price": entry_price,
        "trough_date": entry_date,
        "highest_trailing_stop": stop_loss,
        "trail_activated": False,
        "trail_activation_date": None,
        "tiers_hit": 0,
        "scaled_out": False,
        "peak_before_first_tier": entry_price,
        "peak_after_first_tier": None,
        "time_stop_bound": False,
    }


def instrument_close(pos, exit_price, gross_price_return_pct, net_return_pct):
    """MFE/MAE/giveback block for the post-mortem.

    Giveback is split because mfe_pct is price-path based while net_return_pct
    includes commissions. A single metric would make a policy with identical exit
    prices but more partial-exit commissions look like it had worse trailing
    behaviour.
    """
    entry = pos["entry_price"]
    mfe_pct = (pos["peak_price"] - entry) / entry * 100
    mae_pct = (pos["trough_price"] - entry) / entry * 100
    return {
        "mfe_pct": round(mfe_pct, 4),
        "mae_pct": round(mae_pct, 4),
        "peak_price": round(pos["peak_price"], 6),
        "peak_date": pos.get("peak_date"),
        "trough_price": round(pos["trough_price"], 6),
        "trough_date": pos.get("trough_date"),
        "mfe_before_first_tier_pct": round(
            (pos["peak_before_first_tier"] - entry) / entry * 100, 4
        ),
        "mfe_after_first_tier_pct": (
            None if pos.get("peak_after_first_tier") is None
            else round((pos["peak_after_first_tier"] - entry) / entry * 100, 4)
        ),
        "price_giveback_pct": round(max(0.0, mfe_pct - gross_price_return_pct), 4),
        "economic_giveback_pct": round(max(0.0, mfe_pct - net_return_pct), 4),
        "trail_activated": pos.get("trail_activated", False),
        "trail_activation_date": pos.get("trail_activation_date"),
        "highest_trailing_stop": round(pos.get("highest_trailing_stop", 0.0), 6),
        "tiers_hit": pos.get("tiers_hit", 0),
        "time_stop_bound": pos.get("time_stop_bound", False),
        "exit_policy_version": EXIT_POLICY_MODULE_VERSION,
    }


def assert_invariants(closed_trade):
    """Structural checks that must hold for every closed trade.

    The scaled_out invariant is impossible to violate by construction: reaching a
    tier means peak >= entry * (1 + tier), so trail = peak * 0.90 sits above any
    sub-entry stop. It is asserted anyway because it is free, and because it is
    what resolved the 70/70 and 73/73 exit-reason symmetry as coincidence on a
    sound mechanism rather than a labelling defect.
    """
    errors = []
    if closed_trade.get("scaled_out") and closed_trade.get("exit_reason") == "stop_loss":
        errors.append(
            "scaled_out trade exited as stop_loss — structurally impossible: "
            "peak >= 1.18x entry puts the trail above any sub-entry stop"
        )
    mfe = closed_trade.get("mfe_pct")
    if mfe is not None and mfe < -1e-9:
        errors.append(f"mfe_pct negative ({mfe}) — peak seeds at entry, floor is 0")
    for k in ("price_giveback_pct", "economic_giveback_pct"):
        v = closed_trade.get(k)
        if v is not None and v < -1e-9:
            errors.append(f"{k} negative ({v}) — max(0, ...) floor breached")
    return errors
