"""Tests for engine/parity_eval.py -- the Phase 4 module_eval.

Dependency-free, plain asserts, __main__ runner, matching the other suites.

What these can and cannot establish
-----------------------------------
These verify the EVALUATOR: the call ordering, the tracker-shaped projection,
the price-determinism refusal, the entry-day skip and the seeding report. They
do NOT re-establish equivalence with tracker.py -- that is Phase 0's job, done
over 4,730 bars against the real tracker under I/O injection. A green run here
means the port is faithful to the sequence Phase 0 proved, not that the sequence
is itself correct.
"""

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from engine import parity_eval as pe          # noqa: E402
from engine.exit_policy import POLICIES        # noqa: E402

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


def params(**over):
    p = {"exit_policy": "A", "slippage_pct": 0.001,
         "commission_per_trade": 1.00, "rsi_extreme_high": 90,
         "scale_out": False, "scale_out_pct": 0.50}
    p.update(over)
    return p


def abm():
    """The real live ABM position: trailing_stop == stop_loss, never ratcheted."""
    return {"symbol": "ABM", "strategy": "momentum_breakout",
            "entry_date": "2026-09-09", "entry_price": 50.65, "shares": 2.94,
            "original_shares": 2.94, "stop_loss": 48.67, "trailing_stop": 48.67,
            "peak_price": 50.91, "take_profit": 59.76, "status": "open",
            "scaled_out": False, "total_commission": 1.0}


def sdgr():
    """The real live SDGR position: active giveback dead band, trail < entry."""
    return {"symbol": "SDGR", "strategy": "momentum_breakout",
            "entry_date": "2026-09-18", "entry_price": 29.35, "shares": 5.08,
            "original_shares": 5.08, "stop_loss": 27.60, "trailing_stop": 28.23,
            "peak_price": 31.37, "take_profit": 34.63, "status": "open",
            "scaled_out": False, "total_commission": 1.0}


def bar(price, rsi=55.0, div=False, date="2026-09-26", src="daily"):
    """A Phase 0.5 decision-input packet, as tracker._LAST_EVAL would supply it."""
    return {"price": price, "rsi": rsi, "bearish_div": div, "bar_date": date,
            "price_source": src, "cycle_token": 1}


# --- Phase 0.5: live and daily are equally comparable -----------------------
def test_both_price_sources_are_comparable():
    """The Phase 4 refusal is withdrawn: parity now gets the price inline used.

    Reconstruction had to refuse live prices because a re-derived price could
    differ through market movement. The packet removes the re-derivation, so the
    live path -- which is the ONLY path production actually takes during
    scheduled cycles -- becomes comparable rather than refused.
    """
    for src in ("daily", "IBKR"):
        out = pe.evaluate(abm(), params(), bar(49.57, src=src))
        check(f"price_source={src!r} evaluates instead of raising",
              out["_parity_action"] == "hold", out.get("_parity_action"))
        check(f"price_source={src!r} recorded as a coverage dimension",
              out["_parity_price_source"] == src, out.get("_parity_price_source"))
    check("the withdrawn refusal no longer exists",
          not hasattr(pe, "NonDeterministicPrice"))
    check("make_bar was deleted, not left dormant", not hasattr(pe, "make_bar"))
    live = pe.evaluate(abm(), params(), bar(48.00, src="IBKR"))
    check("a live price can drive a real exit",
          live["status"] == "closed" and live["exit_reason"] == "stop_loss",
          (live["status"], live.get("exit_reason")))


# --- no mutation of the supplied state ---------------------------------------
def test_no_mutation():
    t = abm()
    frozen = copy.deepcopy(t)
    pe.evaluate(t, params(), bar(55.00))
    check("input trade not mutated on ratchet", t == frozen)
    t2 = sdgr()
    frozen2 = copy.deepcopy(t2)
    pe.evaluate(t2, params(), bar(20.00))       # forces an exit
    check("input trade not mutated on exit", t2 == frozen2)


# --- tracker's entry-bar skip ------------------------------------------------
def test_entry_day_skipped():
    t = abm()
    out = pe.evaluate(t, params(), bar(99.00, rsi=99.0, date="2026-09-09"))
    check("entry bar skipped", out["_parity_action"] == "skipped_entry_day",
          out["_parity_action"])
    check("entry-bar skip leaves status open", out["status"] == "open")
    check("entry-bar skip leaves peak untouched", out["peak_price"] == 50.91)
    check("entry-bar skip cannot exit", out.get("exit_reason") is None)


# --- ABM equality boundary: trail == stop must label stop_loss --------------
def test_abm_equality_boundary_labels_stop_loss():
    out = pe.evaluate(abm(), params(), bar(48.00))
    check("ABM exits at/below stop", out["status"] == "closed")
    check("ABM labels stop_loss not trailing_stop",
          out["exit_reason"] == "stop_loss", out["exit_reason"])


# --- SDGR dead band: trail ratcheted but below entry ------------------------
def test_sdgr_dead_band_labels_trailing_stop():
    out = pe.evaluate(sdgr(), params(), bar(28.00))
    check("SDGR exits at/below trail", out["status"] == "closed")
    check("SDGR labels trailing_stop (trail > stop)",
          out["exit_reason"] == "trailing_stop", out["exit_reason"])


# --- the fill is a SINGLE round of the decision price -----------------------
def test_fill_is_single_round():
    out = pe.evaluate(sdgr(), params(), bar(28.00))
    eff = 28.23                                  # effective_stop = trailing_stop
    check("exit_price is one round of raw*(1-slip)",
          out["exit_price"] == round(eff * (1 - 0.001), 2), out["exit_price"])
    check("exit_price is NOT a double round",
          out["exit_price"] == round(28.23 * 0.999, 2))


# --- peak and trail are persisted on every bar, including an exit bar -------
def test_state_persisted_on_exit_bar():
    t = sdgr()
    out = pe.evaluate(t, params(), bar(35.00))    # new high, above take_profit
    check("peak ratchets to the new high", out["peak_price"] == 35.00,
          out["peak_price"])
    check("trail ratchets with the peak", out["trailing_stop"] > 28.23,
          out["trailing_stop"])
    check("stored trail is 2dp",
          round(out["trailing_stop"], 2) == out["trailing_stop"])
    check("stored peak is 2dp",
          round(out["peak_price"], 2) == out["peak_price"])


# --- rsi handling: None is equivalent to a non-triggering value ------------
def test_rsi_none_is_non_triggering():
    a = pe.evaluate(abm(), params(), bar(52.00, rsi=None))
    check("rsi None does not exit", a.get("exit_reason") is None)
    b = pe.evaluate(abm(), params(), bar(52.00, rsi=95.0))
    check("rsi above extreme exits", b["exit_reason"] == "emotional_extreme",
          b.get("exit_reason"))
    check("emotional_extreme fills off the bar price",
          b["exit_price"] == round(52.00 * 0.999, 2))


# --- bearish divergence: acts on momentum, swallowed for mean_reversion ----
def test_bearish_divergence_strategy_gate():
    m = pe.evaluate(abm(), params(), bar(52.00, div=True))
    check("momentum exits on bearish divergence",
          m["exit_reason"] == "bearish_divergence", m.get("exit_reason"))
    mr = abm()
    mr["strategy"] = "mean_reversion"
    r = pe.evaluate(mr, params(), bar(52.00, rsi=75.0, div=True))
    check("mean_reversion divergence swallows the rsi>70 check on that bar",
          r.get("exit_reason") is None, r.get("exit_reason"))
    r2 = pe.evaluate(mr, params(), bar(52.00, rsi=75.0, div=False))
    check("mean_reversion_complete fires without divergence",
          r2["exit_reason"] == "mean_reversion_complete", r2.get("exit_reason"))


# --- scale-out short-circuits the exit check (tracker's `continue`) --------
def test_scale_out_short_circuits():
    t = abm()
    p = params(scale_out=True)
    tiers = POLICIES["A"].get("scale_out_tiers") or POLICIES["A"].get("tiers")
    if not tiers:
        check("policy A exposes scale-out tiers", False, POLICIES["A"].keys())
        return
    check("policy A exposes scale-out tiers", True)
    gain = tiers[0][0] if isinstance(tiers[0], (list, tuple)) else tiers[0]
    price = t["entry_price"] * (1 + float(gain)) + 0.50
    out = pe.evaluate(t, p, bar(price, rsi=95.0, div=True))
    check("tier fires", out["_parity_action"] == "scale_out",
          out["_parity_action"])
    check("scale-out sets scaled_out", out["scaled_out"] is True)
    check("scale-out short-circuits: no exit despite rsi 95 and divergence",
          out.get("exit_reason") is None and out["status"] == "open",
          out.get("exit_reason"))
    check("scale_out_date recorded", out["scale_out_date"] == "2026-09-26")
    fill = price * 0.999
    check("scale_out_price is the rounded fill",
          out["scale_out_price"] == round(fill, 2), out["scale_out_price"])
    sold = out["scale_out_shares"]
    # Exact, not approximate. An abs() tolerance here would hide precisely the
    # rounding divergence this suite exists to detect.
    sold_raw = out["_parity_sold_shares"]
    check("scale_out_pnl uses the UNROUNDED fill and unrounded shares",
          out["scale_out_pnl"] == round((fill - 50.65) * sold_raw, 2),
          (out["scale_out_pnl"], round((fill - 50.65) * sold_raw, 2)))
    check("scale_out_pnl_pct uses the UNROUNDED fill",
          out["scale_out_pnl_pct"] == round((fill - 50.65) / 50.65 * 100, 2),
          out["scale_out_pnl_pct"])
    check("stored scale_out_shares is the 2dp projection of the raw size",
          sold == round(sold_raw, 2), (sold, sold_raw))
    check("shares reduced", out["shares"] < 2.94, out["shares"])
    check("commission added",
          out["total_commission"] == 2.0, out["total_commission"])


# --- schema mapping ---------------------------------------------------------
def test_to_module_pos_mapping():
    pos, seed = pe.to_module_pos(abm(), params())
    check("tiers_hit 0 when unscaled", pos["tiers_hit"] == 0)
    check("peak_after_first_tier None when unscaled",
          pos["peak_after_first_tier"] is None)
    check("no seeding note for an unscaled position", seed == [], seed)
    check("initial_stop defaults to stop_loss", pos["initial_stop"] == 48.67)
    check("trail_activated False when trail == stop",
          pos["trail_activated"] is False)
    pos2, _ = pe.to_module_pos(sdgr(), params())
    check("trail_activated True when trail > stop",
          pos2["trail_activated"] is True)

    bare = {"entry_price": 10.0, "stop_loss": 9.0, "shares": 5.0,
            "strategy": "momentum_breakout", "entry_date": "2026-01-01"}
    pos3, _ = pe.to_module_pos(bare, params())
    check("trailing_stop defaults to stop_loss", pos3["trailing_stop"] == 9.0)
    check("peak_price defaults to entry_price", pos3["peak_price"] == 10.0)
    check("original_shares defaults to shares", pos3["original_shares"] == 5.0)


def test_scaled_position_seeding_is_reported():
    t = abm()
    t["scaled_out"] = True
    pos, seed = pe.to_module_pos(t, params())
    check("tiers_hit 1 when scaled", pos["tiers_hit"] == 1)
    check("peak_after_first_tier seeded from stored peak",
          pos["peak_after_first_tier"] == 50.91)
    check("seeding assumption is REPORTED, not silent", len(seed) == 1, seed)
    out = pe.evaluate(t, params(), bar(52.00))
    check("seeding surfaces in the evaluated payload",
          out["_parity_seeding"] and "peak_after_first_tier" in
          out["_parity_seeding"][0])
    check("unscaled evaluation carries no seeding note",
          pe.evaluate(abm(), params(), bar(52.00))["_parity_seeding"] == [])


def test_contract_recorded():
    out = pe.evaluate(abm(), params(), bar(52.00))
    check("adapter contract recorded on the payload",
          out["_parity_contract"] == "tracker_v3_2dp", out["_parity_contract"])
    check("effective_stop reported", out["effective_stop"] == 48.67,
          out.get("effective_stop"))


# --- the packet is consumed verbatim, never re-derived ---------------------
def test_packet_values_are_used_verbatim():
    """No rounding, no normalising, no recomputation of the captured inputs."""
    p = bar(52.123456789, rsi=61.4, div=False)
    out = pe.evaluate(abm(), params(), p)
    check("full-precision packet price drives the peak ratchet",
          out["peak_price"] == 52.12, out["peak_price"])
    check("evaluator does not mutate the packet", p["price"] == 52.123456789)
    # bearish_div arrives already interpreted by tracker; do not re-derive it.
    truthy = pe.evaluate(abm(), params(), bar(52.00, div=True))
    check("packet bearish_div True is honoured",
          truthy["exit_reason"] == "bearish_divergence",
          truthy.get("exit_reason"))
    out2 = pe.evaluate(abm(), params(), bar(52.00, rsi=None))
    check("absent rsi is non-triggering, not an error",
          out2.get("exit_reason") is None)
    check("bar_date is read from the packet's bar_date field",
          pe.evaluate(abm(), params(), bar(48.00))["exit_date"] == "2026-09-26")


# --- dormancy --------------------------------------------------------------
def test_dormant():

    # Delegated to tests/blast_radius.py -- the ONE structural contract.
    # Previously a grep with a locally duplicated allow-list: it needed an edit
    # per new test file (four during Phase 0.5) and once failed on a COMMENT that
    # merely named a module. Structural questions now come from AST nodes.
    import blast_radius as br
    _, bad = br.audit()
    check("no production module references parity_eval", not bad,
          "\n" + br.describe(bad))
    tsrc = (ROOT / "engine" / "tracker.py").read_text()
    check("tracker.py does not import parity_eval", "parity_eval" not in tsrc)


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
