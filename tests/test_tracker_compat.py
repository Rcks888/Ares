"""Unit tests for the tracker_v3_2dp compatibility adapter.

No pytest in this environment, so these are plain asserts with a runner.
    python3 tests/test_tracker_compat.py

These gate Phase 1 "implemented". They test the ADAPTER only — not policy, not
the tracker, not the migration. Includes a repository-level dormancy check
asserting nothing in production imports the adapter yet; that check is expected
to be updated deliberately in the Phase 5 wiring commit.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.tracker_compat import (  # noqa: E402
    CONTRACT_VERSION,
    STORED_2DP,
    apply_tracker_v3_2dp,
    to_canonical_state,
    tracker_v3_2dp_fill,
    tracker_v3_2dp_slippage,
)

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


def base_trade(**over):
    t = {
        "symbol": "TEST", "entry_price": 100.0, "stop_loss": 90.0,
        "shares": 10.0, "status": "open", "entry_date": "2026-01-05",
        "strategy": "momentum", "take_profit": 118.0,
    }
    t.update(over)
    return t


# --- defaults, which are silent when wrong -----------------------------------
def test_defaults():
    c = to_canonical_state(base_trade())
    check("default trailing_stop == stop_loss", c["trailing_stop"] == 90.0, c)
    check("default peak_price == entry_price", c["peak_price"] == 100.0, c)
    check("default original_shares == shares", c["original_shares"] == 10.0, c)
    check("default scaled_out is False", c["scaled_out"] is False, c)
    check("contract stamped", c["state_precision_contract"] == CONTRACT_VERSION)
    c2 = to_canonical_state(base_trade(trailing_stop=95.5, peak_price=106.1,
                                       original_shares=20.0, scaled_out=True))
    check("stored values win over defaults",
          (c2["trailing_stop"], c2["peak_price"], c2["original_shares"],
           c2["scaled_out"]) == (95.5, 106.1, 20.0, True), c2)
    check("default trailing_pct 0.10", c2["trailing_pct"] == 0.10)
    check("trailing_pct honours params",
          to_canonical_state(base_trade(), {"trailing_stop_pct": 0.08})["trailing_pct"] == 0.08)


# --- half-cent boundaries, below / at / above --------------------------------
def test_half_cent_boundaries():
    # Literals verified against the interpreter, not assumed. An earlier draft
    # of this test asserted 100.015 -> 100.01 and failed: its float is
    # 100.015000000000000568, ABOVE the midpoint, so it rounds up. Only 0.125 in
    # this set is exactly representable, so the tie rule is rarely what decides
    # the result -- the float representation is. Hand-reasoned expectations
    # about 2dp money rounding are unreliable; these are measured.
    cases = [
        (109.9849, 109.98),   # below
        (109.9850, 109.98),   # float is 109.98499999999999943, below midpoint
        (109.9851, 109.99),   # above
        (100.005, 100.0),     # float 100.00499999999999545 -> down
        (100.015, 100.02),    # float 100.01500000000000057 -> up
        (2.675, 2.67),        # float 2.67499999999999982 -> down
        (0.125, 0.12),        # exactly representable: banker's rounding to even
        (0.135, 0.14),        # float 0.13500000000000001 -> up
    ]
    for raw, want in cases:
        got = apply_tracker_v3_2dp(base_trade(), {"peak_price": raw})["peak_price"]
        check(f"round2({raw}) == {want}", got == want, f"got {got}")
    # The adapter must reproduce Python's round(), which is what the live
    # tracker calls. It must not "correct" it to half-up.
    for raw in (2.675, 0.125, 100.015, 109.985):
        check(f"matches tracker's own round() at {raw}",
              apply_tracker_v3_2dp(base_trade(), {"peak_price": raw})["peak_price"]
              == round(raw, 2))


# --- which fields round, and which must not ---------------------------------
def test_only_stored_fields_round():
    canon = {"peak_price": 110.0119, "trailing_stop": 99.00999,
             "shares": 5.004999, "scale_out_price": 117.88999,
             "scale_out_shares": 5.0049, "scale_out_pnl": 12.3456,
             "scale_out_pnl_pct": 10.4999,
             "entry_price": 100.123456, "stop_loss": 90.987654}
    out = apply_tracker_v3_2dp(base_trade(), canon)
    for k in STORED_2DP:
        check(f"{k} rounded to 2dp", out[k] == round(canon[k], 2), f"got {out[k]}")
    check("entry_price NOT rounded", out["entry_price"] == 100.123456, out["entry_price"])
    check("stop_loss NOT rounded", out["stop_loss"] == 90.987654, out["stop_loss"])


def test_unknown_keys_not_invented():
    out = apply_tracker_v3_2dp(base_trade(), {"peak_price": 105.0})
    check("absent canonical keys are not written", "scale_out_price" not in out,
          sorted(out))
    check("unrelated stored keys preserved", out["symbol"] == "TEST")


# --- fill model -------------------------------------------------------------
def test_fill():
    check("exit fill 90.0 -> 89.91", tracker_v3_2dp_fill(90.0, 0.001) == 89.91)
    check("exit fill 110.0 -> 109.89", tracker_v3_2dp_fill(110.0, 0.001) == 109.89)
    check("exit fill is ONE round after slippage",
          tracker_v3_2dp_fill(161.475, 0.001) == round(161.475 * 0.999, 2))
    check("entry fill raises price", tracker_v3_2dp_fill(100.0, 0.001, "entry") == 100.1)
    check("exit_slippage is 4dp", tracker_v3_2dp_slippage(90.0, 0.001) == 0.09)
    try:
        tracker_v3_2dp_fill(100.0, 0.001, "sideways")
        check("bad side rejected", False, "no raise")
    except ValueError:
        check("bad side rejected", True)


# --- the two observed rounding classes --------------------------------------
def test_observed_rounding_classes():
    # Class 1, tracker_2dp_rounding: round(module_value, 2) == tracker_value.
    for module_val, tracker_val in [(109.989, 109.99), (110.011, 110.01),
                                    (117.9882, 117.99), (106.18938, 106.19)]:
        got = apply_tracker_v3_2dp(base_trade(), {"peak_price": module_val})["peak_price"]
        check(f"class1 round({module_val},2)=={tracker_val}", got == tracker_val, got)

    # Class 2, tracker_rounding_compounded, reproduced from the real mechanism:
    # the trail is persisted at 2dp on bar N and read back on bar N+1, so the
    # fill derives from the ROUNDED carry-forward, not from a double round.
    for module_eff, tracker_exit in [(161.31480546 / 0.999, 161.32),
                                     (22.41456327 / 0.999, 22.42)]:
        stored = apply_tracker_v3_2dp(base_trade(), {"trailing_stop": module_eff})
        carried = stored["trailing_stop"]              # bar N persists 2dp
        got = tracker_v3_2dp_fill(carried, 0.001)      # bar N+1 fills from it
        check(f"class2 carry-forward fill -> {tracker_exit}", got == tracker_exit,
              f"eff={module_eff} carried={carried} got={got}")


# --- stop vs trail equality, the labelling boundary -------------------------
def test_stop_trail_equality():
    c = to_canonical_state(base_trade(trailing_stop=90.0))
    check("trail == stop is representable",
          c["trailing_stop"] == c["stop_loss"] == 90.0, c)
    # The adapter must not nudge equality either way; ABM/TMO/WBD live on it.
    out = apply_tracker_v3_2dp(base_trade(), {"trailing_stop": 90.0})
    check("equality preserved exactly", out["trailing_stop"] == out["stop_loss"])


# --- scale-out quantities and signed P&L ------------------------------------
def test_scale_out_and_pnl_signs():
    out = apply_tracker_v3_2dp(base_trade(shares=10.0), {
        "shares": 5.0, "scale_out_shares": 5.0,
        "scale_out_price": 117.882, "scale_out_pnl": 89.41,
    })
    check("shares halved and stored", out["shares"] == 5.0, out["shares"])
    check("scale_out_price 2dp", out["scale_out_price"] == 117.88)
    for pnl in (-8.865, 9.774, 0.0, -0.004):
        got = apply_tracker_v3_2dp(base_trade(), {"scale_out_pnl": pnl})["scale_out_pnl"]
        check(f"signed pnl {pnl} -> {round(pnl,2)}", got == round(pnl, 2), got)
    check("fractional shares survive (0.23 TMO-like)",
          apply_tracker_v3_2dp(base_trade(), {"shares": 0.229999})["shares"] == 0.23)


# --- mutation, determinism, JSON ---------------------------------------------
def test_no_mutation_and_determinism():
    t = base_trade()
    snapshot = json.dumps(t, sort_keys=True)
    to_canonical_state(t)
    apply_tracker_v3_2dp(t, {"peak_price": 123.456, "shares": 1.239})
    check("to_canonical/apply do not mutate input",
          json.dumps(t, sort_keys=True) == snapshot)
    out = apply_tracker_v3_2dp(t, {"peak_price": 123.456}, in_place=True)
    check("in_place=True mutates, opt-in only", t["peak_price"] == 123.46 and out is t)

    t2 = base_trade()
    a = apply_tracker_v3_2dp(t2, {"peak_price": 110.005, "shares": 3.335})
    b = apply_tracker_v3_2dp(t2, {"peak_price": 110.005, "shares": 3.335})
    check("repeated calls deterministic", a == b, (a, b))
    check("idempotent on already-rounded state",
          apply_tracker_v3_2dp(a, {"peak_price": a["peak_price"]})["peak_price"]
          == a["peak_price"])
    check("JSON round-trips", json.loads(json.dumps(a)) == a)
    check("stored values are float/int, not numpy",
          all(type(a[k]) in (float, int) for k in STORED_2DP if k in a),
          {k: type(a[k]).__name__ for k in STORED_2DP if k in a})


def test_none_and_missing_passthrough():
    out = apply_tracker_v3_2dp(base_trade(), {"scale_out_price": None,
                                              "take_profit": None})
    check("None passes through unrounded", out["scale_out_price"] is None)
    check("take_profit None preserved", out["take_profit"] is None)
    c = to_canonical_state(base_trade(take_profit=None))
    check("missing take_profit tolerated", c["take_profit"] is None)


# --- repository-level dormancy ----------------------------------------------
def test_dormant_in_production():
    hits = subprocess.run(
        ["grep", "-rn", "--include=*.py", "-e", "tracker_compat",
         "-e", "apply_tracker_v3_2dp", str(ROOT)],
        capture_output=True, text=True).stdout.strip().splitlines()
    offenders = [h for h in hits
                 if "tests/test_tracker_compat.py" not in h
                 and "engine/tracker_compat.py" not in h]
    check("nothing in production imports the adapter", not offenders,
          "\n        " + "\n        ".join(offenders))
    tracker = (ROOT / "engine" / "tracker.py").read_text(errors="replace")
    check("tracker.py does not import tracker_compat",
          "tracker_compat" not in tracker)
    check("tracker.py still does not import exit_policy",
          "exit_policy" not in tracker)
    check("engine/__init__.py does not auto-import it",
          "tracker_compat" not in (ROOT / "engine" / "__init__.py").read_text())


def main():
    for fn in (test_defaults, test_half_cent_boundaries,
               test_only_stored_fields_round, test_unknown_keys_not_invented,
               test_fill, test_observed_rounding_classes,
               test_stop_trail_equality, test_scale_out_and_pnl_signs,
               test_no_mutation_and_determinism,
               test_none_and_missing_passthrough, test_dormant_in_production):
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{'FAILED: ' + ', '.join(FAILURES) if FAILURES else 'ALL PASS'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
