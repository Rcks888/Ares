"""Phase 0.5 §6.3 — captured LIVE-price equivalence, end to end.

THE GAP THIS CLOSES
-------------------
Phase 0's 4,730-bar replay forced get_live_price to None, so it proved the
daily-Close path only. §6.1 re-ran that same population against the instrumented
tracker -- still daily-only. §6.2 never loaded the Ares tracker at all. A
production measurement (/tmp/ares_output.txt) showed all five open positions were
decided on IBKR live prices, so the path that actually runs in production had
ZERO differential coverage until this file.

WHAT IS EXERCISED
-----------------
The real chain, not a mock of it:

    injected live price X
      -> tracker.check_open_trades uses X
      -> tracker._LAST_EVAL captures X under the current invocation token
      -> parity_hook._packets reads the store AFTER inline returns
      -> parity_runner validates the token and calls parity_eval.evaluate
      -> tracker_v3_2dp projection
      -> inline vs module decisions compared
      -> record written

Every fixture runs observed_check_open_trades() with ARES_PARITY=1. Nothing here
stubs the tracker's decision logic, the packet, the runner or the evaluator.

NETWORK
-------
get_live_price / load_stock / add_indicators are counted. The inline path is
allowed exactly the calls it already makes; the parity branch must add ZERO.
§6.5 is the formal gate, but a secondary acquisition introduced here would
invalidate every result in this file, so it is asserted per fixture.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import parity_compare as sc  # noqa: E402
from engine import parity_hook, parity_runner, tracker  # noqa: E402

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


def make_df(close, rsi=55.0, bearish=False, date="2026-09-26", bars=120):
    """A real DataFrame, so .iloc[-1] / ['Close'] / .name behave faithfully."""
    idx = pd.to_datetime(pd.date_range(end=date, periods=bars, freq="D"))
    return pd.DataFrame({
        "Open": [close] * bars, "High": [close] * bars,
        "Low": [close] * bars, "Close": [close] * bars,
        "Volume": [1_000_000] * bars, "rsi": [rsi] * bars,
        "bearish_div": [False] * (bars - 1) + [bearish],
    }, index=idx)


PARAMS = {"scale_out": True, "scale_out_pct": 0.50, "trailing_stop_pct": 0.10,
          "rsi_extreme_high": 90, "slippage_pct": 0.001,
          "commission_per_trade": 1.00, "max_positions": 5, "exit_policy": "A"}


def trade(symbol="ABM", **over):
    t = {"symbol": symbol, "status": "open", "strategy": "momentum_breakout",
         "entry_date": "2026-09-09", "entry_price": 50.65, "shares": 2.94,
         "original_shares": 2.94, "stop_loss": 48.67, "trailing_stop": 48.67,
         "peak_price": 50.91, "take_profit": 59.76, "total_commission": 1.0,
         "entry_commission": 1.0, "scaled_out": False, "signal_date": "2026-09-09",
         "rsi_at_entry": 50.0, "stdev_20": 0.02}
    t.update(over)
    return t


NET_NAMES = ("get_live_price", "load_stock", "add_indicators")


def run_cycle(trades, frames, live=None, params=None, bar_date="2026-09-26"):
    """Drive observed_check_open_trades() with ARES_PARITY=1. Returns records.

    Restores every patched name and the env var. Counts market-data calls.
    """
    live = live or {}
    out = str(Path(tempfile.mkdtemp()) / "shadow.jsonl")
    calls = {n: 0 for n in NET_NAMES}
    saved = {k: getattr(tracker, k) for k in
             ("load_trades", "save_trades", "load_stock", "add_indicators",
              "get_live_price", "_load_params", "expire_queue")}
    saved_env = os.environ.get("ARES_PARITY")
    saved_default = parity_runner.DEFAULT_OUTPUT
    persisted = []

    def counted_live(sym):
        calls["get_live_price"] += 1
        return live.get(sym)

    def counted_stock(sym, *a, **k):
        calls["load_stock"] += 1
        return frames[sym]

    def counted_ind(df):
        calls["add_indicators"] += 1
        return df

    # Production load_trades() reads the file save_trades() writes, so after the
    # inline call it reflects the new state. A stub that always returns the
    # pristine input makes the runner compare the module's post-state against a
    # PRE-state snapshot, which reports DECISION_CHANGING_MISMATCH on every
    # correct exit. Keep this stateful.
    state = {"trades": [dict(x) for x in trades]}

    def load_trades():
        return [dict(x) for x in state["trades"]]

    def save_trades(tr):
        state["trades"] = [dict(x) for x in tr]
        persisted.append([dict(x) for x in tr])

    try:
        tracker.load_trades = load_trades
        tracker.save_trades = save_trades
        tracker.load_stock = counted_stock
        tracker.add_indicators = counted_ind
        tracker.get_live_price = counted_live
        tracker._load_params = lambda: dict(params or PARAMS)
        tracker.expire_queue = lambda: []
        os.environ["ARES_PARITY"] = "1"
        # Redirect writes away from the real logs/tracker_parity_v1.jsonl.
        #
        # This works only because observe_cycle now resolves output_path at
        # INVOCATION time. While the signature bound DEFAULT_OUTPUT as a default
        # argument value (evaluated once at definition), this reassignment had no
        # effect and these fixtures would have appended to the production
        # evidence file while appearing to pass. parity_hook calls observe_cycle
        # WITHOUT an explicit path, so the runtime default is the only seam --
        # and redirecting it exercises the real append_record write path rather
        # than replacing it.
        parity_runner.DEFAULT_OUTPUT = out
        parity_hook.observed_check_open_trades()
    finally:
        for k, v in saved.items():
            setattr(tracker, k, v)
        parity_runner.DEFAULT_OUTPUT = saved_default
        if saved_env is None:
            os.environ.pop("ARES_PARITY", None)
        else:
            os.environ["ARES_PARITY"] = saved_env

    text = Path(out).read_text().strip() if Path(out).exists() else ""
    recs = [json.loads(l) for l in text.splitlines()] if text else []
    return {"records": {r["symbol"]: r for r in recs}, "all": recs,
            "calls": calls, "persisted": persisted,
            "token": tracker._LAST_EVAL["cycle_token"],
            "packets": dict(tracker._LAST_EVAL["packets"])}


REGISTERED = {"cycle_token", "price", "price_source", "rsi", "bearish_div",
              "bar_date"}
PREV_TOKEN = {"v": None}


def assert_chain(tag, res, symbol, price, source, rsi=55.0, bearish=False,
                 bar_date="2026-09-26", expect_capture=True):
    """The token/packet/identity/network assertions every §6.3 fixture owes."""
    pkts, tok = res["packets"], res["token"]

    check(f"{tag}: store cycle_token is not None", tok is not None, tok)
    check(f"{tag}: token rotated since the previous invocation",
          PREV_TOKEN["v"] is None or tok > PREV_TOKEN["v"],
          f"{PREV_TOKEN['v']} -> {tok}")
    PREV_TOKEN["v"] = tok

    if not expect_capture:
        check(f"{tag}: no packet captured", symbol not in pkts, sorted(pkts))
        return None

    check(f"{tag}: packet exists for the evaluated symbol", symbol in pkts,
          sorted(pkts))
    if symbol not in pkts:
        return None
    p = pkts[symbol]
    check(f"{tag}: packet token == store token", p["cycle_token"] == tok,
          f"{p['cycle_token']} vs {tok}")
    check(f"{tag}: packet field set is exactly the registered six",
          set(p) == REGISTERED, sorted(set(p) ^ REGISTERED))
    # Identity, not tolerance. get_live_price already returns a 2dp float.
    check(f"{tag}: packet price is IDENTICAL to the injected price",
          p["price"] == price and type(p["price"]) is float,
          f"{p['price']!r} vs {price!r}")
    check(f"{tag}: price_source == {source}", p["price_source"] == source,
          p["price_source"])
    check(f"{tag}: packet rsi is identical to the inline rsi", p["rsi"] == rsi,
          f"{p['rsi']!r} vs {rsi!r}")
    check(f"{tag}: bearish_div uses identical truth semantics",
          p["bearish_div"] is bool(bearish), p["bearish_div"])
    check(f"{tag}: bar_date is identical to inline today",
          p["bar_date"] == bar_date, p["bar_date"])

    # The parity branch must add no market access. The inline path calls
    # get_live_price once per evaluated position and load_stock once.
    check(f"{tag}: parity added no extra get_live_price call",
          res["calls"]["get_live_price"] <= 1, res["calls"])
    check(f"{tag}: parity added no extra load_stock call",
          res["calls"]["load_stock"] <= 1, res["calls"])

    rec = res["records"].get(symbol)
    check(f"{tag}: a parity record was written", rec is not None,
          sorted(res["records"]))
    if rec is None:
        return None
    check(f"{tag}: packet was accepted, not refused",
          rec["shadow_exception_type"] is None, rec["shadow_exception_type"])
    check(f"{tag}: record carries the live price source",
          rec["price_source"] == source, rec["price_source"])
    check(f"{tag}: no decision-changing mismatch",
          rec["difference_class"] != "DECISION_CHANGING_MISMATCH",
          (rec["difference_class"], rec.get("decision_diffs")))
    check(f"{tag}: would_change_action is not True",
          rec["would_change_action"] is not True, rec["would_change_action"])
    check(f"{tag}: parity mutated no production state",
          rec.get("production_state_hash_before")
          == rec.get("production_state_hash_after"),
          (rec.get("production_state_hash_before"),
           rec.get("production_state_hash_after")))
    return rec


def inline_exit(res, symbol):
    """(status, exit_reason, exit_price) the INLINE tracker actually produced."""
    if not res["persisted"]:
        return (None, None, None)
    for t in res["persisted"][-1]:
        if t["symbol"] == symbol:
            return (t.get("status"), t.get("exit_reason"), t.get("exit_price"))
    return (None, None, None)


# ---- 1. effective-stop boundary, on LIVE prices ---------------------------
def test_effective_stop_boundary_live():
    """effective_stop = max(stop_loss, trailing_stop). Probe -1c / at / +1c.

    At the stop the inline rule is `<=`, so AT the boundary it exits. Whatever
    inline does, the module must agree -- that is the only claim under test; this
    file does not assert which side is correct.
    """
    eff = 48.67
    for tag, px, want_exit in (("stop-1c", 48.66, True),
                               ("stop-at", 48.67, True),
                               ("stop+1c", 48.68, False)):
        t = trade(stop_loss=48.67, trailing_stop=48.67)
        res = run_cycle([t], {"ABM": make_df(99.00)}, live={"ABM": px})
        rec = assert_chain(f"eff-stop {tag}", res, "ABM", px, "IBKR")
        status, reason, _ = inline_exit(res, "ABM")
        check(f"eff-stop {tag}: inline {'closed' if want_exit else 'held'}",
              (status == "closed") is want_exit, (status, reason, px, eff))
        if rec:
            check(f"eff-stop {tag}: inline and module agree on exit_reason",
                  rec["inline_exit_reason"] == rec["shadow_exit_reason"],
                  (rec["inline_exit_reason"], rec["shadow_exit_reason"]))
            check(f"eff-stop {tag}: inline and module agree on decision",
                  rec["inline_decision"] == rec["shadow_decision"],
                  (rec["inline_decision"], rec["shadow_decision"]))


def test_sub_half_cent_ratchet_defeats_pre_post_derivation():
    """THE load-bearing case for capturing instead of reconstructing.

    A ratchet that moves the local by less than half a cent rounds back to the
    SAME stored value, so the persisted state is identical before and after:

        pre  stored trailing_stop   48.67
        new  unrounded local        48.674...   <- what the decision uses
        post stored trailing_stop   round(...) == 48.67

    Every candidate reconstruction is a function of stored state, so all of them
    return 48.67 and none can see 48.674. The trail-advance discriminator
    (post != pre) also reports "no ratchet" here, which is precisely how it was
    defeated. This test asserts BOTH halves: derivation is wrong, capture is
    right. If it ever passes with derivation, the capture is unnecessary.
    """
    pct = 0.10
    pre_trail = 48.67
    # The tracker sets peak_price = current_price on a new high, so the ratchet
    # derives from the PRICE. Choose the price whose ratchet lands a sub-half-cent
    # above the stored value. Not rounded: rounding the input would destroy the
    # very precision under test.
    px = (pre_trail + 0.004) / (1 - pct)
    expected = px * (1 - pct)            # the exact unrounded local
    t = trade(stop_loss=40.00, trailing_stop=pre_trail, peak_price=50.00,
              take_profit=None)
    res = run_cycle([t], {"ABM": make_df(99.00)}, live={"ABM": px})
    rec = assert_chain("subcent", res, "ABM", px, "IBKR")

    check("fixture is genuinely sub-half-cent (rounds back to the same value)",
          round(expected, 2) == pre_trail, (expected, round(expected, 2)))
    check("fixture ratchet is real (local exceeds the stored value)",
          expected > pre_trail, (expected, pre_trail))
    if rec:
        post = rec["inline_persistent_state"]
        check("stored state is UNCHANGED across the ratchet",
              post["trailing_stop"] == pre_trail, post["trailing_stop"])
        # Derivation attempts, all of which must fail.
        derived_pre = max(40.00, pre_trail)
        derived_post = max(post["stop_loss"], post["trailing_stop"])
        check("pre-state derivation gives the WRONG value",
              derived_pre != expected, (derived_pre, expected))
        check("post-state derivation gives the WRONG value",
              derived_post != expected, (derived_post, expected))
        check("trail-advance discriminator reports no ratchet (it is defeated)",
              post["trailing_stop"] == pre_trail)
        # Capture succeeds where all derivation fails.
        check("captured inline effective stop is EXACT",
              rec["inline_effective_stop"] == expected,
              (rec["inline_effective_stop"], expected))
        check("captured value carries sub-cent precision",
              round(rec["inline_effective_stop"], 2)
              != rec["inline_effective_stop"], rec["inline_effective_stop"])
        check("basis is captured_inline_local",
              rec["inline_effective_stop_basis"] == sc.BASIS_CAPTURED,
              rec["inline_effective_stop_basis"])
        check("canonical module agrees exactly, no 2dp tolerance",
              rec["shadow_effective_stop"] == rec["inline_effective_stop"],
              (rec["shadow_effective_stop"], rec["inline_effective_stop"]))
        check("no decision change", rec["would_change_action"] is False,
              rec["would_change_action"])


def test_material_ratchet_still_needs_the_captured_local():
    """A ratchet that DOES move the stored 2dp value still loses precision.

    Complements the sub-half-cent case. There the persisted state hides that a
    ratchet happened at all; here it reveals it, yet the exact value is still
    unrecoverable because only round(x, 2) survives. Together they show capture
    is required on EVERY ratcheting bar, not just fractional-cent ones.
    """
    pct = 0.10
    pre_trail = 48.67
    px = (pre_trail + 0.014) / (1 - pct)
    expected = px * (1 - pct)             # ~48.684, the exact local
    t = trade(stop_loss=40.00, trailing_stop=pre_trail, peak_price=50.00,
              take_profit=None)
    res = run_cycle([t], {"ABM": make_df(99.00)}, live={"ABM": px})
    rec = assert_chain("material", res, "ABM", px, "IBKR")

    check("fixture ratchet IS visible in stored state",
          round(expected, 2) != pre_trail, (expected, round(expected, 2)))
    if rec:
        post = rec["inline_persistent_state"]
        check("stored trail advanced to the rounded value",
              post["trailing_stop"] == round(expected, 2),
              post["trailing_stop"])
        check("stored value is NOT the value the decision used",
              post["trailing_stop"] != expected,
              (post["trailing_stop"], expected))
        check("post-state derivation is still WRONG",
              max(post["stop_loss"], post["trailing_stop"]) != expected,
              (max(post["stop_loss"], post["trailing_stop"]), expected))
        check("captured inline effective stop is EXACT",
              rec["inline_effective_stop"] == expected,
              (rec["inline_effective_stop"], expected))
        check("basis is captured_inline_local",
              rec["inline_effective_stop_basis"] == sc.BASIS_CAPTURED)
        # Compared against the canonical value, never against the rounded trail.
        check("canonical agrees exactly with the captured local",
              rec["shadow_effective_stop"] == rec["inline_effective_stop"],
              (rec["shadow_effective_stop"], rec["inline_effective_stop"]))
        check("no decision change", rec["would_change_action"] is False)


def test_result_store_does_not_leak_across_invocations():
    """Invocation 2 must never be able to consume invocation 1's result."""
    t1 = trade(symbol="SOLO", stop_loss=40.00, trailing_stop=48.67,
               peak_price=50.00, take_profit=None)
    px = (48.67 + 0.004) / 0.9
    r1 = run_cycle([t1], {"SOLO": make_df(99.00)}, live={"SOLO": px})
    rec1 = r1["records"].get("SOLO")
    check("invocation 1 captured a value", rec1
          and rec1["inline_effective_stop"] is not None,
          rec1 and rec1["inline_effective_stop"])
    first_value = rec1["inline_effective_stop"] if rec1 else None
    tok1 = r1["token"]

    # Invocation 2: the store is cleared and the token rotates, so invocation 1's
    # number is physically gone rather than merely superseded.
    leftover = dict(tracker._LAST_EVAL.get("results") or {})
    t2 = trade(symbol="SOLO", stop_loss=40.00, trailing_stop=48.67,
               peak_price=50.00, take_profit=None)
    r2 = run_cycle([t2], {"SOLO": make_df(99.00)}, live={"SOLO": px})
    check("token rotated between invocations", r2["token"] != tok1,
          (tok1, r2["token"]))
    check("invocation 1 results are not still resident under the new token",
          all(e.get("cycle_token") != r2["token"]
              for e in leftover.values()) or not leftover, leftover)

    # A deliberately stale entry must be treated as ABSENT, never consumed.
    stale = {"SOLO": {"cycle_token": tok1,
                      "inline_effective_stop": first_value}}
    val, basis = sc.effective_stop_evidence(
        stale, "SOLO", r2["token"], {"bar_date": "2026-09-26"},
        "2026-09-09", False)
    check("stale result entry yields NO value", val is None, val)
    check("stale result entry yields NO guessed basis", basis is None, basis)
    check("the stale value is specifically not consumed",
          val != first_value or first_value is None, (val, first_value))


def test_entry_day_skip_has_no_result_and_an_honest_basis():
    """The tracker CONTINUEs before computing effective_stop."""
    t = trade(entry_date="2026-09-26")
    res = run_cycle([t], {"ABM": make_df(99.00)}, live={"ABM": 50.00},
                    bar_date="2026-09-26")
    rec = res["records"].get("ABM")
    check("entry-day-skip record exists", rec is not None)
    if rec:
        check("entry-day: no captured value",
              rec["inline_effective_stop"] is None,
              rec["inline_effective_stop"])
        check("entry-day: basis is the entry-day skip",
              rec["inline_effective_stop_basis"] == sc.BASIS_ENTRY_DAY,
              rec["inline_effective_stop_basis"])


def test_abm_equality_boundary_attribution_live():
    """stop_loss == trailing_stop. Attribution must reproduce tracker's rule:

        'trailing_stop' if trailing_stop > stop_loss else 'stop_loss'

    so EQUALITY must label stop_loss, not trailing_stop. ABM's real live state
    (entry 50.65, SL 48.67, TS 48.67) sits exactly on this boundary, which is
    why it is the registered first-cycle acceptance case.
    """
    for tag, sl, ts, want in (("equal", 48.67, 48.67, "stop_loss"),
                              ("trail+1c", 48.67, 48.68, "trailing_stop"),
                              ("trail-1c", 48.67, 48.66, "stop_loss")):
        eff = max(sl, ts)
        px = round(eff - 0.01, 2)
        t = trade(stop_loss=sl, trailing_stop=ts)
        res = run_cycle([t], {"ABM": make_df(99.00)}, live={"ABM": px})
        rec = assert_chain(f"abm {tag}", res, "ABM", px, "IBKR")
        status, reason, _ = inline_exit(res, "ABM")
        check(f"abm {tag}: inline closed below effective stop {eff}",
              status == "closed", (status, px, eff))
        check(f"abm {tag}: inline attribution is {want}", reason == want, reason)
        if rec:
            check(f"abm {tag}: module reproduces the attribution",
                  rec["shadow_exit_reason"] == want, rec["shadow_exit_reason"])
            # Schema v2: the inline effective stop is now CAPTURED from the
            # tracker's own local, so the two sides are directly comparable.
            # Under v1 this assertion said "inline_effective_stop is None on
            # every record by construction" -- true, but it documented the gap
            # rather than closing it, and it is the reason a decisive stop bar
            # could not be verified end to end.
            check(f"abm {tag}: inline effective_stop captured exactly = {eff}",
                  rec["inline_effective_stop"] == eff,
                  rec["inline_effective_stop"])
            check(f"abm {tag}: basis is captured_inline_local",
                  rec["inline_effective_stop_basis"] == sc.BASIS_CAPTURED,
                  rec["inline_effective_stop_basis"])
            check(f"abm {tag}: inline and module effective stops agree",
                  rec["inline_effective_stop"] == rec["shadow_effective_stop"],
                  (rec["inline_effective_stop"], rec["shadow_effective_stop"]))
            check(f"abm {tag}: module effective_stop == max(sl, ts) = {eff}",
                  rec["shadow_effective_stop"] == eff,
                  (rec["shadow_effective_stop"], eff))


def test_sdgr_dead_band_live():
    """SDGR's CORRECTED real state: entry 29.35, SL 25.06, TS 28.23.

    An earlier note in this migration recorded SDGR's stop_loss as 27.60. That
    was wrong. The label conclusion is unaffected -- max(25.06, 28.23) = 28.23
    so attribution is trailing_stop either way -- but a fixture that does not
    match production is not a fixture, so the real values are used.
    """
    for tag, px, want_exit in (("28.22", 28.22, True),
                               ("28.23", 28.23, True),
                               ("28.24", 28.24, False)):
        t = trade("SDGR", strategy="mean_reversion", entry_price=29.35,
                  stop_loss=25.06, trailing_stop=28.23, peak_price=31.37,
                  take_profit=34.63, shares=5.11, original_shares=5.11)
        res = run_cycle([t], {"SDGR": make_df(99.00)}, live={"SDGR": px})
        rec = assert_chain(f"sdgr {tag}", res, "SDGR", px, "IBKR")
        status, reason, _ = inline_exit(res, "SDGR")
        check(f"sdgr {tag}: effective_stop is the trailing stop 28.23",
              max(25.06, 28.23) == 28.23)
        check(f"sdgr {tag}: inline {'closed' if want_exit else 'held'}",
              (status == "closed") is want_exit, (status, reason, px))
        if want_exit:
            check(f"sdgr {tag}: attribution is trailing_stop",
                  reason == "trailing_stop", reason)
        if rec:
            check(f"sdgr {tag}: inline and module agree on exit_reason",
                  rec["inline_exit_reason"] == rec["shadow_exit_reason"],
                  (rec["inline_exit_reason"], rec["shadow_exit_reason"]))


# ---- 2. strategy-specific scale-out thresholds ----------------------------
def test_scale_out_thresholds_live():
    """Momentum fires at +18%, mean reversion at +10%.

    The B/D defect found in Block A was exactly a strategy-conditional
    scale-out gate, so both strategies are probed independently at -1c / at /
    +1c around their own threshold.
    """
    # IMPORTANT CONTRACT POINT, found while building this fixture.
    # tracker's inline scale-out gate is `current_price >= take_profit` -- it does
    # NOT recompute a +18%/+10% threshold. The strategy-specific percentages live
    # in exit_policy's TIERS. The two coincide only because take_profit is SET at
    # entry from the strategy's percentage. A fixture that prices to +18% while
    # leaving take_profit at +50% therefore correctly does not scale out, and an
    # earlier version of this test asserted otherwise. The boundary under test is
    # take_profit, seeded from the strategy percentage so both sides align.
    cases = (("momentum_breakout", 0.18, "MOM"),
             ("mean_reversion", 0.10, "REV"))
    for strategy, pct, sym in cases:
        entry = 50.00
        thresh = round(entry * (1 + pct), 2)
        for tag, px, want in ((f"{sym}-1c", round(thresh - 0.01, 2), False),
                              (f"{sym}-at", thresh, True),
                              (f"{sym}+1c", round(thresh + 0.01, 2), True)):
            t = trade(sym, strategy=strategy, entry_price=entry,
                      stop_loss=45.00, trailing_stop=45.00, peak_price=entry,
                      take_profit=thresh, shares=10.0, original_shares=10.0)
            res = run_cycle([t], {sym: make_df(99.00)}, live={sym: px})
            rec = assert_chain(f"scaleout {tag}", res, sym, px, "IBKR")
            persisted = res["persisted"][-1][0] if res["persisted"] else {}
            check(f"scaleout {tag}: inline scaled_out is {want} (gate=take_profit)",
                  bool(persisted.get("scaled_out")) is want,
                  (persisted.get("scaled_out"), px, thresh))
            if want and persisted.get("scaled_out"):
                check(f"scaleout {tag}: shares reduced from 10.0",
                      persisted.get("shares", 10.0) < 10.0,
                      persisted.get("shares"))
                check(f"scaleout {tag}: scale_out_date recorded",
                      persisted.get("scale_out_date") is not None,
                      persisted.get("scale_out_date"))
            if rec:
                check(f"scaleout {tag}: module agrees on scaled_out",
                      rec["inline_scaled_out"] == rec["shadow_scaled_out"],
                      (rec["inline_scaled_out"], rec["shadow_scaled_out"]))
                check(f"scaleout {tag}: module agrees on persistent state",
                      rec["difference_class"] in ("MATCH",
                                                  "PRECISION_ONLY_DIFFERENCE"),
                      (rec["difference_class"], rec.get("state_diffs")))


def test_take_profit_is_the_inline_scale_out_gate_not_a_percentage():
    """Documents the contract the previous fixture version got wrong.

    Both strategies are priced at exactly +10% with take_profit left at +50%.
    NEITHER scales out, because tracker gates on take_profit alone and is
    strategy-agnostic at this point in the chain. The strategy-specific tier
    percentages are exit_policy's, applied when take_profit is SET at entry.

    This is asserted rather than assumed so a future change that makes tracker's
    inline gate strategy-conditional -- the shape of the Block A B/D defect --
    fails here instead of silently altering live exits.
    """
    entry, px = 50.00, 55.00          # exactly +10%
    got = {}
    for strategy in ("momentum_breakout", "mean_reversion"):
        t = trade("XX", strategy=strategy, entry_price=entry, stop_loss=45.00,
                  trailing_stop=45.00, peak_price=entry, take_profit=75.0,
                  shares=10.0, original_shares=10.0)
        res = run_cycle([t], {"XX": make_df(99.00)}, live={"XX": px})
        assert_chain(f"tp-gate {strategy}", res, "XX", px, "IBKR")
        p = res["persisted"][-1][0] if res["persisted"] else {}
        got[strategy] = bool(p.get("scaled_out"))
    check("below take_profit: mean_reversion does NOT scale out",
          got["mean_reversion"] is False, got)
    check("below take_profit: momentum does NOT scale out",
          got["momentum_breakout"] is False, got)
    check("the inline gate is strategy-agnostic at this point",
          got["mean_reversion"] == got["momentum_breakout"], got)


# ---- 3. entry-day end to end: closes the gap §6.1 could not cover ---------
def test_entry_day_packet_exists_before_skip():
    """§6.1's 4,730-bar population contained ZERO entry-day bars.

    The harness steps from the bar after entry, so the skip branch never ran.
    This is the end-to-end proof, and it must show the packet is PRESENT -- not
    merely that the position was skipped. That is the whole point of capturing
    before the skip: absence of a packet then means exactly one thing (capture
    was not reached), so it cannot be confused with an entry-day skip.
    """
    day = "2026-09-26"
    t = trade(entry_date=day)
    res = run_cycle([t], {"ABM": make_df(99.00, date=day)}, live={"ABM": 48.00})
    # 48.00 is BELOW the 48.67 stop: without the skip this would exit.
    rec = assert_chain("entry-day", res, "ABM", 48.00, "IBKR", bar_date=day)
    status, reason, _ = inline_exit(res, "ABM")
    check("entry-day: inline did NOT exit despite price below the stop",
          status != "closed", (status, reason))
    p = res["packets"]["ABM"]
    check("entry-day: bar_date equals entry_date, so the skip is DERIVABLE",
          p["bar_date"] == t["entry_date"] == day, (p["bar_date"], t["entry_date"]))
    if rec:
        check("entry-day: module DERIVED skipped_entry_day from bar_date",
              rec.get("parity_action") == "skipped_entry_day",
              rec.get("parity_action"))
        check("entry-day: no exit reason on either side",
              rec["inline_exit_reason"] is None
              and rec["shadow_exit_reason"] is None,
              (rec["inline_exit_reason"], rec["shadow_exit_reason"]))
        check("entry-day: inline and module agree",
              rec["inline_decision"] == rec["shadow_decision"],
              (rec["inline_decision"], rec["shadow_decision"]))
        check("entry-day: not a decision-changing mismatch",
              rec["difference_class"] != "DECISION_CHANGING_MISMATCH",
              rec["difference_class"])


# ---- 4. both price sources -------------------------------------------------
def test_daily_price_stays_unrounded_in_the_packet():
    """With no live price, current_price is float(latest['Close']) -- full
    precision. Rounding it in the packet would manufacture a difference against
    the unrounded local the inline decision actually used.
    """
    ugly = 48.6749999999
    t = trade(stop_loss=40.00, trailing_stop=40.00)
    res = run_cycle([t], {"ABM": make_df(ugly)}, live={})
    rec = assert_chain("daily-precision", res, "ABM", ugly, "daily")
    p = res["packets"]["ABM"]
    check("daily: packet price is the FULL-precision Close, not 2dp",
          p["price"] == ugly and p["price"] != round(ugly, 2),
          (p["price"], round(ugly, 2)))
    if rec:
        check("daily: full-precision input still produces agreement",
              rec["difference_class"] != "DECISION_CHANGING_MISMATCH",
              rec["difference_class"])


def test_live_price_two_decimals_is_bit_identical():
    """get_live_price returns round(float(close), 2), so comparison is identity."""
    # Stops must be representable in tracker's 2dp state contract. An earlier
    # version of this fixture used stop_loss=0.005: round(0.005, 2) == 0.01, so
    # the tracker_v3_2dp projection reported a state_update the tracker never
    # made, and the failure looked like a decision divergence when it was an
    # unrepresentable fixture value. Real stops are 2dp.
    for px in (48.00, 49.78, 15.78, 675.00, 0.01):
        stop = round(px * 0.90, 2)
        t = trade(stop_loss=stop, trailing_stop=stop, take_profit=1e9,
                  entry_price=px, peak_price=px)
        res = run_cycle([t], {"ABM": make_df(99.00)}, live={"ABM": px})
        assert_chain(f"live-identity {px}", res, "ABM", px, "IBKR")


def test_both_sources_on_the_same_boundary_agree():
    """The same stop boundary, once on a live price and once on a daily Close."""
    outcomes = {}
    for tag, live, close in (("IBKR", 48.00, 99.00), ("daily", None, 48.00)):
        t = trade()
        res = run_cycle([t], {"ABM": make_df(close)},
                        live=({"ABM": live} if live else {}))
        assert_chain(f"both-sources {tag}", res, "ABM", 48.00, tag)
        outcomes[tag] = inline_exit(res, "ABM")[:2]
    check("the same price exits identically regardless of source",
          outcomes["IBKR"] == outcomes["daily"], outcomes)
    check("both sources produced a stop exit",
          outcomes["IBKR"][0] == "closed", outcomes)


# ---- 5. the refusal path has no fallback ----------------------------------
def test_capture_failure_refuses_and_never_evaluates():
    """A symbol whose inline evaluation dies before the capture point.

    load_stock raises, so the tracker's per-trade `except` swallows it and no
    packet is written. The record must refuse, not silently report agreement.
    """
    t = trade("BAD")
    frames = {}

    def boom(sym, *a, **k):
        raise RuntimeError("forced data failure")

    saved = {k: getattr(tracker, k) for k in
             ("load_trades", "save_trades", "load_stock", "add_indicators",
              "get_live_price", "_load_params", "expire_queue")}
    saved_env = os.environ.get("ARES_PARITY")
    saved_default = parity_runner.DEFAULT_OUTPUT
    out = str(Path(tempfile.mkdtemp()) / "shadow.jsonl")
    try:
        tracker.load_trades = lambda: [dict(t)]
        tracker.save_trades = lambda tr: None
        tracker.load_stock = boom
        tracker.add_indicators = lambda d: d
        tracker.get_live_price = lambda s: 1.0
        tracker._load_params = lambda: dict(PARAMS)
        tracker.expire_queue = lambda: []
        os.environ["ARES_PARITY"] = "1"
        parity_runner.DEFAULT_OUTPUT = out
        parity_hook.observed_check_open_trades()
    finally:
        for k, v in saved.items():
            setattr(tracker, k, v)
        parity_runner.DEFAULT_OUTPUT = saved_default
        if saved_env is None:
            os.environ.pop("ARES_PARITY", None)
        else:
            os.environ["ARES_PARITY"] = saved_env

    check("capture-failure: no packet was written for the symbol",
          "BAD" not in tracker._LAST_EVAL["packets"],
          sorted(tracker._LAST_EVAL["packets"]))
    text = Path(out).read_text().strip() if Path(out).exists() else ""
    recs = [json.loads(l) for l in text.splitlines()] if text else []
    rec = next((r for r in recs if r["symbol"] == "BAD"), None)
    check("capture-failure: a record was still written", rec is not None, recs)
    if rec:
        check("capture-failure: classified INLINE_INPUT_NOT_CAPTURED",
              rec["shadow_exception_type"] == "INLINE_INPUT_NOT_CAPTURED",
              rec["shadow_exception_type"])
        check("capture-failure: would_change_action is None, not False",
              rec["would_change_action"] is None, rec["would_change_action"])
        check("capture-failure: never reported as a MATCH",
              rec["difference_class"] != "MATCH", rec["difference_class"])


# ---- 6. parity-side data acquisition is prohibited -------------------------
def test_parity_branch_makes_no_secondary_acquisition():
    """Counts market-data calls across a multi-position cycle.

    The inline path calls get_live_price and load_stock once per position. If the
    parity branch acquired its own price the counts would exceed the position
    count -- which is precisely the print_scorecard mistake this design avoids
    (that function makes a SECOND get_live_price call minutes after the decision,
    so parsing its output would have handed parity a price the inline path never
    evaluated).
    """
    trades = [trade("ABM", stop_loss=40.0, trailing_stop=40.0),
              trade("TMO", stop_loss=600.0, trailing_stop=600.0,
                    entry_price=654.54, peak_price=675.0, take_profit=772.36),
              trade("WBD", stop_loss=29.31, trailing_stop=29.31,
                    entry_price=30.87, peak_price=30.9, take_profit=36.42)]
    frames = {s["symbol"]: make_df(99.00) for s in trades}
    live = {"ABM": 49.78, "TMO": 675.00, "WBD": 30.85}
    res = run_cycle(trades, frames, live=live)
    n = len(trades)
    check("get_live_price called exactly once per position",
          res["calls"]["get_live_price"] == n, res["calls"])
    check("load_stock called exactly once per position",
          res["calls"]["load_stock"] == n, res["calls"])
    check("every position captured a packet", len(res["packets"]) == n,
          sorted(res["packets"]))
    check("every position produced a record", len(res["records"]) == n,
          sorted(res["records"]))
    for sym, px in live.items():
        p = res["packets"].get(sym, {})
        check(f"multi: {sym} packet price is identical to its injected price",
              p.get("price") == px, (p.get("price"), px))
        check(f"multi: {sym} packet carries the current token",
              p.get("cycle_token") == res["token"],
              (p.get("cycle_token"), res["token"]))
    for sym, rec in res["records"].items():
        check(f"multi: {sym} was not refused",
              rec["shadow_exception_type"] is None, rec["shadow_exception_type"])
        check(f"multi: {sym} no decision-changing mismatch",
              rec["difference_class"] != "DECISION_CHANGING_MISMATCH",
              rec["difference_class"])


def test_parity_stack_never_imports_market_data():
    """Structural: no parity module may reference a data-acquisition symbol."""
    import ast as _ast
    banned = {"get_live_price", "load_stock", "download_stock", "add_indicators",
              "_get_ib", "_ib_connection", "yfinance", "requests"}
    for mod in ("parity_runner", "parity_eval", "parity_compare",
                "tracker_compat"):
        src = (ROOT / "engine" / f"{mod}.py").read_text()
        tree = _ast.parse(src)
        found = {n.id for n in _ast.walk(tree) if isinstance(n, _ast.Name)} \
            | {n.attr for n in _ast.walk(tree) if isinstance(n, _ast.Attribute)}
        check(f"{mod} references no market-data symbol", not (found & banned),
              sorted(found & banned))
    hook = (ROOT / "engine" / "parity_hook.py").read_text()
    htree = _ast.parse(hook)
    hnames = {n.id for n in _ast.walk(htree) if isinstance(n, _ast.Name)} \
        | {n.attr for n in _ast.walk(htree) if isinstance(n, _ast.Attribute)}
    check("parity_hook references no market-data symbol",
          not (hnames & banned), sorted(hnames & banned))
    check("parity_hook imports no data_feed", "data_feed" not in hnames,
          "data_feed present")


def test_coverage_summary():
    """Report §6.3 coverage by dimension so gaps are visible, not assumed."""
    print("\n  --- §6.3 coverage ---")
    print("    IBKR live-price fixtures : effective-stop x3, ABM equality x3,")
    print("                               SDGR dead band x3, scale-out x6,")
    print("                               threshold-isolation x2, identity x5,")
    print("                               entry-day x1, multi-position x3")
    print("    daily-price fixtures     : full-precision x1, both-sources x1")
    print("    entry-day fixtures       : 1 end-to-end (closes the §6.1 gap)")
    print("    scale-out fixtures       : momentum +18% x3, reversion +10% x3")
    print("    stop-boundary fixtures   : effective-stop x3, ABM x3, SDGR x3")
    print("    refusal fixtures         : capture-failure x1")
    check("coverage summary emitted", True)


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
