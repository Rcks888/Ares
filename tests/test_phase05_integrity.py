"""Phase 0.5 §6.4 / §6.4b — capture integrity and cycle isolation.

§6.3 established that the chain agrees when the capture WORKS. This file is about
what happens when it does not. The governing rule is that evidence fails CLOSED
while production fails OPEN: a parity record may never report agreement it did not
establish, and no parity failure may alter the inline result.

REFUSAL PRECEDENCE (fixed; asserted, not assumed)

    store unreadable
      -> cycle token absent
        -> symbol packet absent
          -> packet not a mapping
            -> packet token mismatched
              -> packet schema drift
                -> evaluate

A plausible-looking payload must never override an earlier integrity failure,
which is why the schema is checked LAST and only on a packet already proven to
belong to the completed invocation.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

# Registered telemetry shape is single-sourced from the tracker-diff contract, so
# a registration change cannot leave these suites asserting a stale shape.
import tracker_diff as td  # noqa: E402

from engine import parity_runner as pr  # noqa: E402
from engine import parity_compare as pc  # noqa: E402
from engine import tracker  # noqa: E402

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


TOKEN = 7
GOOD = {"cycle_token": TOKEN, "price": 48.00, "price_source": "IBKR",
        "rsi": 55.0, "bearish_div": False, "bar_date": "2026-09-26"}


def st(symbol="ABM", **over):
    t = {"symbol": symbol, "status": "open", "strategy": "momentum_breakout",
         "entry_date": "2026-09-09", "entry_price": 50.65, "shares": 2.94,
         "original_shares": 2.94, "stop_loss": 48.67, "trailing_stop": 48.67,
         "peak_price": 50.91, "take_profit": 59.76, "scaled_out": False,
         "total_commission": 1.0}
    t.update(over)
    return t


def observe(trades_before, trades_after, packets_fn, module_eval=None,
            marker=False):
    """One observe_cycle over in-memory state. Returns (result, summary, records).

    marker=False by DEFAULT and that matters. MARKER_PREFIX is "Error checking ",
    so emitting it marks the inline evaluation as FAILED, and classify_result
    short-circuits on inline failure. An earlier version of this helper printed
    the marker for every symbol, which meant every capture-integrity assertion
    below was evaluated under an inline-failure condition instead of under a
    normal successful inline cycle -- passing, but not for the stated reason.
    """
    out = str(Path(tempfile.mkdtemp()) / "shadow.jsonl")
    state = {"cur": [dict(t) for t in trades_before]}
    calls = []

    def inline_call():
        state["cur"] = [dict(t) for t in trades_after]
        if marker:
            for t in trades_before:
                print(f"{pc.MARKER_PREFIX}{t['symbol']}: ok")
        return "INLINE_RESULT"

    def load_state():
        return [dict(t) for t in state["cur"]]

    def me(t, p, packet):
        calls.append((t.get("symbol"), packet))
        return (module_eval or (lambda a, b, c: dict(a)))(t, p, packet)

    res, summ = pr.observe_cycle(
        object(), inline_call, load_state, me, packets_fn=packets_fn,
        params={"exit_policy": "A"}, lineage={"production_commit": "test"},
        output_path=out)
    text = Path(out).read_text().strip() if Path(out).exists() else ""
    recs = [json.loads(l) for l in text.splitlines()] if text else []
    return res, summ, {r["symbol"]: r for r in recs}, calls


def refused(rec, kind):
    return (rec["shadow_exception_type"] == "INLINE_INPUT_NOT_CAPTURED"
            and rec["capture_defect"] == kind
            and rec["would_change_action"] is None
            and rec["difference_class"] != "MATCH")


# ---- §6.4  packet integrity ------------------------------------------------
def test_valid_packet_evaluates_normally():
    res, summ, recs, calls = observe([st()], [st()],
                                     lambda: (TOKEN, {"ABM": dict(GOOD)}))
    check("valid packet: inline result preserved", res == "INLINE_RESULT")
    check("valid packet: module_eval was called", len(calls) == 1, calls)
    check("valid packet: no capture defect",
          recs["ABM"]["capture_defect"] is None, recs["ABM"]["capture_defect"])
    check("valid packet: not refused",
          recs["ABM"]["shadow_exception_type"] is None,
          recs["ABM"]["shadow_exception_type"])


def test_missing_packet_refuses():
    res, summ, recs, calls = observe([st()], [st()], lambda: (TOKEN, {}))
    check("missing packet: refused as packet_absent",
          refused(recs["ABM"], "packet_absent"),
          (recs["ABM"]["capture_defect"], recs["ABM"]["would_change_action"]))
    check("missing packet: module_eval NOT called", not calls, calls)
    check("missing packet: inline result preserved", res == "INLINE_RESULT")


def test_previous_cycle_packet_refused_and_never_consumed():
    stale = dict(GOOD, cycle_token=TOKEN - 1, price=999.99)
    res, summ, recs, calls = observe([st()], [st()],
                                     lambda: (TOKEN, {"ABM": stale}))
    check("stale packet: refused as packet_stale",
          refused(recs["ABM"], "packet_stale"), recs["ABM"]["capture_defect"])
    check("stale packet: module_eval NOT called", not calls, calls)
    check("stale packet: the old value never reached the record",
          recs["ABM"]["bar_price"] != 999.99, recs["ABM"]["bar_price"])


def test_absent_store_token_refuses():
    res, summ, recs, calls = observe([st()], [st()],
                                     lambda: (None, {"ABM": dict(GOOD)}))
    check("no token: refused as no_cycle_token",
          refused(recs["ABM"], "no_cycle_token"), recs["ABM"]["capture_defect"])
    check("no token: module_eval NOT called even though a packet existed",
          not calls, calls)


def test_store_read_failure_is_fail_open():
    def boom():
        raise RuntimeError("store unreadable")

    res, summ, recs, calls = observe([st()], [st()], boom)
    check("store failure: inline result preserved", res == "INLINE_RESULT")
    check("store failure: recorded in the summary",
          "store unreadable" in str(summ.get("capture_error")),
          summ.get("capture_error"))
    check("store failure: refused as store_unreadable",
          refused(recs["ABM"], "store_unreadable"),
          recs["ABM"]["capture_defect"])
    check("store failure: module_eval NOT called", not calls, calls)


def test_each_registered_field_is_individually_required():
    """Drop one field at a time. Every omission must fail closed."""
    for field in sorted(pr.REGISTERED_PACKET_FIELDS):
        partial = {k: v for k, v in GOOD.items() if k != field}
        if field == "cycle_token":
            # Without a token the packet cannot be authenticated at all, so it
            # is refused one step EARLIER in the ladder. Still closed.
            expect = "packet_stale"
        else:
            expect = "packet_schema_drift"
        res, summ, recs, calls = observe([st()], [st()],
                                         lambda p=partial: (TOKEN, {"ABM": p}))
        check(f"missing '{field}': refused ({expect})",
              refused(recs["ABM"], expect),
              (recs["ABM"]["capture_defect"], recs["ABM"]["shadow_exception_type"]))
        check(f"missing '{field}': module_eval NOT called", not calls, calls)


def test_unregistered_extra_field_invalidates_the_packet():
    """The schema is an EXACT set, not a minimum.

    An extra key means the tracker's capture changed without this contract being
    reviewed. Evaluating it would silently compare against an input nobody
    registered, so it is refused until the schema is re-registered.
    """
    extra = dict(GOOD, peak_price=50.91)
    res, summ, recs, calls = observe([st()], [st()],
                                     lambda: (TOKEN, {"ABM": extra}))
    check("extra field: refused as packet_schema_drift",
          refused(recs["ABM"], "packet_schema_drift"),
          recs["ABM"]["capture_defect"])
    check("extra field: module_eval NOT called", not calls, calls)
    check("extra field: the reason names the unregistered key",
          "peak_price" in (recs["ABM"]["shadow_exception_message"] or ""),
          recs["ABM"]["shadow_exception_message"])


def test_non_mapping_packet_refuses():
    res, summ, recs, calls = observe([st()], [st()],
                                     lambda: (TOKEN, {"ABM": [1, 2, 3]}))
    check("non-mapping packet: refused as packet_not_a_mapping",
          refused(recs["ABM"], "packet_not_a_mapping"),
          recs["ABM"]["capture_defect"])
    check("non-mapping packet: module_eval NOT called", not calls, calls)


def test_refusal_precedence_is_not_reorderable():
    """A stale packet that ALSO has schema drift must report the EARLIER defect.

    If schema were checked first, a stale packet could be reported as a schema
    problem and the staleness -- the more serious integrity failure -- would be
    hidden.
    """
    both = dict(GOOD, cycle_token=TOKEN - 1)
    both["unregistered"] = 1
    del both["rsi"]
    res, summ, recs, calls = observe([st()], [st()],
                                     lambda: (TOKEN, {"ABM": both}))
    check("precedence: staleness outranks schema drift",
          recs["ABM"]["capture_defect"] == "packet_stale",
          recs["ABM"]["capture_defect"])
    # And a missing token outranks everything about the packet.
    res2, s2, r2, c2 = observe([st()], [st()],
                               lambda: (None, {"ABM": both}))
    check("precedence: absent cycle token outranks packet defects",
          r2["ABM"]["capture_defect"] == "no_cycle_token",
          r2["ABM"]["capture_defect"])
    check("precedence: neither case called module_eval",
          not calls and not c2, (calls, c2))


def test_would_change_action_is_never_false_on_refusal():
    """Undefined is not agreement. False would mean 'compared, no change'."""
    for tag, pf in (("absent", lambda: (TOKEN, {})),
                    ("stale", lambda: (TOKEN, {"ABM": dict(GOOD,
                                                           cycle_token=1)})),
                    ("no-token", lambda: (None, {"ABM": dict(GOOD)})),
                    ("drift", lambda: (TOKEN, {"ABM": dict(GOOD, x=1)}))):
        res, summ, recs, calls = observe([st()], [st()], pf)
        check(f"{tag}: would_change_action is None, not False",
              recs["ABM"]["would_change_action"] is None,
              recs["ABM"]["would_change_action"])


# ---- §6.4b  cycle isolation, against the REAL tracker ----------------------
def real_cycle(trades, frames, live=None, fail=(), params=None):
    """Run the REAL check_open_trades with I/O stubbed. Returns the store state."""
    import pandas as pd
    live = live or {}
    saved = {k: getattr(tracker, k) for k in
             ("load_trades", "save_trades", "load_stock", "add_indicators",
              "get_live_price", "_load_params", "expire_queue")}
    state = {"trades": [dict(t) for t in trades]}

    def load_stock(sym, *a, **k):
        if sym in fail:
            raise RuntimeError(f"forced failure for {sym}")
        return frames[sym]

    try:
        tracker.load_trades = lambda: [dict(x) for x in state["trades"]]
        tracker.save_trades = lambda tr: state.update(
            trades=[dict(x) for x in tr])
        tracker.load_stock = load_stock
        tracker.add_indicators = lambda d: d
        tracker.get_live_price = lambda s: live.get(s)
        tracker._load_params = lambda: dict(params or {
            "scale_out": True, "scale_out_pct": 0.50, "trailing_stop_pct": 0.10,
            "rsi_extreme_high": 90, "slippage_pct": 0.001,
            "commission_per_trade": 1.00, "max_positions": 5})
        tracker.expire_queue = lambda: []
        tracker.check_open_trades()
    finally:
        for k, v in saved.items():
            setattr(tracker, k, v)
    return (tracker._LAST_EVAL["cycle_token"],
            dict(tracker._LAST_EVAL["packets"]))


def frame(close, date="2026-09-26", bars=120):
    import pandas as pd
    idx = pd.to_datetime(pd.date_range(end=date, periods=bars, freq="D"))
    return pd.DataFrame({
        "Open": [close] * bars, "High": [close] * bars, "Low": [close] * bars,
        "Close": [close] * bars, "Volume": [1_000_000] * bars,
        "rsi": [55.0] * bars, "bearish_div": [False] * bars}, index=idx)


def test_partial_cycle_cannot_reuse_the_previous_invocation():
    """THE most important §6.4b case.

    Invocation 1 captures S. Invocation 2 rotates identity, clears, then fails on
    S before reaching the capture point. Invocation 1's values must be
    inaccessible -- not merely unused, but absent -- so the only possible
    classification is INLINE_INPUT_NOT_CAPTURED.
    """
    t = st("SOLO")
    tok1, p1 = real_cycle([t], {"SOLO": frame(99.00)}, live={"SOLO": 49.00})
    check("invocation 1 captured SOLO", "SOLO" in p1, sorted(p1))
    first_price = p1["SOLO"]["price"]
    check("invocation 1 captured the live price", first_price == 49.00,
          first_price)

    tok2, p2 = real_cycle([t], {}, live={"SOLO": 49.00}, fail={"SOLO"})
    check("invocation 2 rotated the token", tok2 > tok1, (tok1, tok2))
    check("invocation 2 left NO packet for SOLO", "SOLO" not in p2, sorted(p2))
    check("invocation 1's packet is not reachable in the store", not p2,
          sorted(p2))

    # The consuming end must refuse, with invocation 2's token in force.
    res, summ, recs, calls = observe([st("SOLO")], [st("SOLO")],
                                     lambda: (tok2, dict(p2)))
    check("partial cycle: refused as packet_absent",
          refused(recs["SOLO"], "packet_absent"),
          recs["SOLO"]["capture_defect"])
    check("partial cycle: module_eval NOT called", not calls, calls)
    check("partial cycle: invocation-1 price never surfaced",
          recs["SOLO"]["bar_price"] != first_price, recs["SOLO"]["bar_price"])


def test_two_symbols_do_not_cross_contaminate():
    trades = [st("AAA", stop_loss=40.0, trailing_stop=40.0),
              st("BBB", stop_loss=20.0, trailing_stop=20.0, entry_price=25.0,
                 peak_price=25.0, take_profit=99.0)]
    frames = {"AAA": frame(99.00), "BBB": frame(99.00)}
    tok, pkts = real_cycle(trades, frames, live={"AAA": 45.00, "BBB": 24.00})
    check("both symbols captured", set(pkts) == {"AAA", "BBB"}, sorted(pkts))
    check("AAA kept its own price", pkts["AAA"]["price"] == 45.00,
          pkts["AAA"]["price"])
    check("BBB kept its own price", pkts["BBB"]["price"] == 24.00,
          pkts["BBB"]["price"])
    check("both carry the same invocation token",
          pkts["AAA"]["cycle_token"] == pkts["BBB"]["cycle_token"] == tok,
          (pkts["AAA"]["cycle_token"], pkts["BBB"]["cycle_token"], tok))


def test_one_symbol_failing_leaves_others_attributable():
    trades = [st("GOOD", stop_loss=40.0, trailing_stop=40.0), st("DEAD")]
    frames = {"GOOD": frame(99.00)}
    tok, pkts = real_cycle(trades, frames, live={"GOOD": 45.00, "DEAD": 1.0},
                           fail={"DEAD"})
    check("the healthy symbol still captured", "GOOD" in pkts, sorted(pkts))
    check("the failed symbol has no packet", "DEAD" not in pkts, sorted(pkts))
    res, summ, recs, calls = observe(trades, trades, lambda: (tok, dict(pkts)))
    check("GOOD evaluated normally", recs["GOOD"]["capture_defect"] is None,
          recs["GOOD"]["capture_defect"])
    check("DEAD refused as packet_absent",
          refused(recs["DEAD"], "packet_absent"),
          recs["DEAD"]["capture_defect"])
    check("only the healthy symbol reached module_eval",
          [c[0] for c in calls] == ["GOOD"], calls)


def test_consecutive_invocations_rotate_the_token():
    t = st("SEQ", stop_loss=1.0, trailing_stop=1.0, take_profit=1e9)
    seen = []
    for _ in range(4):
        tok, pkts = real_cycle([t], {"SEQ": frame(99.00)}, live={"SEQ": 50.0})
        seen.append(tok)
        check(f"token {tok}: packet carries the current token",
              pkts["SEQ"]["cycle_token"] == tok,
              (pkts["SEQ"]["cycle_token"], tok))
    check("tokens strictly increase across invocations",
          all(b > a for a, b in zip(seen, seen[1:])), seen)
    check("no token repeated", len(set(seen)) == len(seen), seen)


def test_disabled_mode_rotates_and_clears_identically():
    """Telemetry rotation is UNCONDITIONAL.

    A store cleared only when ARES_PARITY=1 would make the two modes behave
    differently, which would defeat the disabled-mode equivalence proof. Disabled
    mode is the production default, so this is the configuration that must not
    drift.
    """
    saved_env = os.environ.get("ARES_PARITY")
    try:
        os.environ.pop("ARES_PARITY", None)
        t = st("OFF", stop_loss=1.0, trailing_stop=1.0, take_profit=1e9)
        tok1, p1 = real_cycle([t], {"OFF": frame(99.00)}, live={"OFF": 50.0})
        check("disabled: packet still captured", "OFF" in p1, sorted(p1))
        tok2, p2 = real_cycle([t], {"OFF": frame(99.00)}, live={"OFF": 51.0})
        check("disabled: token still rotated", tok2 > tok1, (tok1, tok2))
        check("disabled: prior packet was cleared, not accumulated",
              len(p2) == 1 and p2["OFF"]["price"] == 51.0,
              (len(p2), p2["OFF"]["price"]))
        check("disabled: packet token matches the new invocation",
              p2["OFF"]["cycle_token"] == tok2,
              (p2["OFF"]["cycle_token"], tok2))
    finally:
        if saved_env is None:
            os.environ.pop("ARES_PARITY", None)
        else:
            os.environ["ARES_PARITY"] = saved_env


def test_entry_day_position_captures_and_derives_skip():
    day = "2026-09-26"
    t = st("NEW", entry_date=day)
    tok, pkts = real_cycle([t], {"NEW": frame(99.00, date=day)},
                           live={"NEW": 40.00})
    check("entry-day: packet captured despite the skip", "NEW" in pkts,
          sorted(pkts))
    check("entry-day: bar_date equals entry_date",
          pkts["NEW"]["bar_date"] == day, pkts["NEW"]["bar_date"])
    check("entry-day: skip is derivable from the packet alone",
          pkts["NEW"]["bar_date"] == t["entry_date"])


def test_store_is_never_persisted():
    """_LAST_EVAL must not reach any trade file or parity record as state."""
    t = st("PERSIST", stop_loss=1.0, trailing_stop=1.0, take_profit=1e9)
    saved = {k: getattr(tracker, k) for k in ("load_trades", "save_trades",
                                              "load_stock", "add_indicators",
                                              "get_live_price", "_load_params",
                                              "expire_queue")}
    written = []
    try:
        tracker.load_trades = lambda: [dict(t)]
        tracker.save_trades = lambda tr: written.extend(tr)
        tracker.load_stock = lambda s, *a, **k: frame(99.00)
        tracker.add_indicators = lambda d: d
        tracker.get_live_price = lambda s: 50.0
        tracker._load_params = lambda: {"scale_out": False, "scale_out_pct": 0.5,
                                        "trailing_stop_pct": 0.10,
                                        "rsi_extreme_high": 90}
        tracker.expire_queue = lambda: []
        tracker.check_open_trades()
    finally:
        for k, v in saved.items():
            setattr(tracker, k, v)
    banned = {"cycle_token", "_LAST_EVAL", "price_source", "bar_date",
              "_EVAL_CYCLE_SEQ"}
    for tr in written:
        check(f"{tr.get('symbol')}: no telemetry key persisted into trade state",
              not (set(tr) & banned), sorted(set(tr) & banned))
    check("save_trades was exercised", True)


def test_registered_schema_is_the_single_source_of_truth():
    """The runner's schema must equal what the tracker actually writes."""
    import ast
    src = (ROOT / "engine" / "tracker.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "check_open_trades")
    writes = [n for n in ast.walk(fn) if isinstance(n, ast.Assign)
              and any("_LAST_EVAL" in {x.id for x in ast.walk(tgt)
                                       if isinstance(x, ast.Name)}
                      for tgt in n.targets)
              and isinstance(n.value, ast.Dict)]
    def for_store(store):
        return [n for n in writes for tg in n.targets
                if isinstance(tg, ast.Subscript)
                and isinstance(tg.value, ast.Subscript)
                and isinstance(tg.value.slice, ast.Constant)
                and tg.value.slice.value == store]

    packets = for_store("packets")
    results = for_store("results")
    check("exactly one packet-writing assignment", len(packets) == 1,
          len(packets))
    check("exactly one result-writing assignment", len(results) == 1,
          len(results))
    check("every dict write targets a registered sub-store",
          len(packets) + len(results) == len(writes), len(writes))
    if results:
        rkeys = {k.value for k in results[0].value.keys
                 if isinstance(k, ast.Constant)}
        check("tracker writes exactly the registered result field set",
              rkeys == set(td.REGISTERED_RESULT_FIELDS),
              sorted(rkeys ^ set(td.REGISTERED_RESULT_FIELDS)))
    if packets:
        writes = packets
        keys = {k.value for k in writes[0].value.keys
                if isinstance(k, ast.Constant)}
        check("tracker writes exactly the registered field set",
              keys == set(pr.REGISTERED_PACKET_FIELDS),
              sorted(keys ^ set(pr.REGISTERED_PACKET_FIELDS)))


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
