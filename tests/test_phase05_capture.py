"""Phase 0.5 — decision-input capture contract. Amendment dbffb87.

Proves the tracker-side half: invocation identity, stale-packet refusal,
write-only direction, capture placement, and bearish_div truth semantics.

Structural questions are asked of the AST. Text scanning has produced five false
results in this project (matching a docstring, a comment, a prose mention) and is
not acceptable for questions about code structure.
"""

import ast
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

# Registered telemetry shape is single-sourced from the tracker-diff contract, so
# a registration change cannot leave these suites asserting a stale shape.
import tracker_diff as td  # noqa: E402

from engine import tracker  # noqa: E402

FAILS = []
COUNT = 0
TSRC = (ROOT / "engine" / "tracker.py").read_text()
TREE = ast.parse(TSRC)


def check(label, cond, got=None):
    global COUNT
    COUNT += 1
    if cond:
        print(f"  pass  {label}")
    else:
        print(f"  FAIL  {label}  {got if got is not None else ''}")
        FAILS.append(label)


def fn_node(name):
    return next(n for n in ast.walk(TREE)
                if isinstance(n, ast.FunctionDef) and n.name == name)


def names_in(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


# ---------------------------------------------------------------- harness ----
def make_df(close, rsi=55.0, bearish=False, date="2026-09-26", bars=120):
    idx = pd.to_datetime(pd.date_range(end=date, periods=bars, freq="D"))
    df = pd.DataFrame({
        "Open": [close] * bars, "High": [close] * bars,
        "Low": [close] * bars, "Close": [close] * bars,
        "Volume": [1_000_000] * bars,
        "rsi": [rsi] * bars,
        "bearish_div": [False] * (bars - 1) + [bearish],
    }, index=idx)
    return df


def trade(symbol="ABM", **over):
    t = {"symbol": symbol, "status": "open", "strategy": "momentum_breakout",
         "entry_date": "2026-09-09", "entry_price": 50.65, "shares": 2.94,
         "original_shares": 2.94, "stop_loss": 48.67, "trailing_stop": 48.67,
         "peak_price": 50.91, "take_profit": 59.76, "total_commission": 1.0}
    t.update(over)
    return t


class Harness:
    """Run the REAL check_open_trades with I/O stubbed. Restores everything."""

    def __init__(self, trades, frames, live=None, params=None):
        self.trades, self.frames, self.live = trades, frames, live or {}
        self.params = params or {"scale_out": False, "scale_out_pct": 0.50,
                                 "trailing_stop_pct": 0.10,
                                 "rsi_extreme_high": 90,
                                 "slippage_pct": 0.001,
                                 "commission_per_trade": 1.00,
                                 "max_positions": 5}
        self.saved_calls = []
        self.fail_symbols = set()

    def __enter__(self):
        t = tracker
        self._orig = {k: getattr(t, k) for k in
                      ("load_trades", "save_trades", "load_stock",
                       "add_indicators", "get_live_price", "_load_params",
                       "expire_queue")}

        def load_stock(sym, *a, **k):
            if sym in self.fail_symbols:
                raise RuntimeError(f"forced data failure for {sym}")
            return self.frames[sym]

        t.load_trades = lambda: [dict(x) for x in self.trades]
        t.save_trades = lambda tr: self.saved_calls.append(tr)
        t.load_stock = load_stock
        t.add_indicators = lambda df: df
        t.get_live_price = lambda sym: self.live.get(sym)
        t._load_params = lambda: dict(self.params)
        t.expire_queue = lambda: []
        return self

    def __exit__(self, *exc):
        for k, v in self._orig.items():
            setattr(tracker, k, v)
        return False

    def run(self):
        return tracker.check_open_trades()


def packets():
    return tracker._LAST_EVAL["packets"]


def token():
    return tracker._LAST_EVAL["cycle_token"]


# ------------------------------------------------- 1. structural invariants --
def test_store_shape_and_single_write_site():
    check("_LAST_EVAL exists with the registered shape",
          set(tracker._LAST_EVAL) == set(td.REGISTERED_STORE_KEYS),
          sorted(tracker._LAST_EVAL))
    check("_EVAL_CYCLE_SEQ exists and is an int",
          isinstance(tracker._EVAL_CYCLE_SEQ, int))
    writes = [n for n in ast.walk(TREE) if isinstance(n, ast.Assign)
              for tg in n.targets
              if isinstance(tg, ast.Subscript)
              and isinstance(tg.value, ast.Subscript)
              and isinstance(tg.value.value, ast.Name)
              and tg.value.value.id == "_LAST_EVAL"]

    def sub_writes(store):
        return [n for n in writes for tg in n.targets
                if isinstance(tg.value.slice, ast.Constant)
                and tg.value.slice.value == store]

    # Counted PER STORE. The original predicate matched any _LAST_EVAL[x][y]
    # assignment, so 2B's result store made it read 2 and the assertion could
    # only have been "fixed" by loosening it to 2 -- which would then accept a
    # third, unregistered sub-store silently.
    check("exactly one packet write site in tracker.py",
          len(sub_writes("packets")) == 1, len(sub_writes("packets")))
    check("exactly one result write site in tracker.py",
          len(sub_writes("results")) == 1, len(sub_writes("results")))
    check("no write site targets an unregistered sub-store",
          len(sub_writes("packets")) + len(sub_writes("results")) == len(writes),
          len(writes))


def test_tracker_never_reads_telemetry_into_a_decision():
    """_LAST_EVAL must not reach any branch, comparison, return or call arg."""
    for kind, cls in (("if", ast.If), ("compare", ast.Compare),
                      ("return", ast.Return), ("while", ast.While),
                      ("boolop", ast.BoolOp), ("assert", ast.Assert)):
        offenders = [getattr(n, "lineno", "?") for n in ast.walk(TREE)
                     if isinstance(n, cls) and "_LAST_EVAL" in names_in(n)]
        check(f"no {kind} in tracker.py references _LAST_EVAL",
              not offenders, offenders)
    bad_args = []
    for n in ast.walk(TREE):
        if isinstance(n, ast.Call):
            for a in list(n.args) + [k.value for k in n.keywords]:
                if "_LAST_EVAL" in names_in(a):
                    bad_args.append(getattr(n, "lineno", "?"))
    check("_LAST_EVAL is never passed as a call argument", not bad_args,
          bad_args)
    persist = []
    for n in ast.walk(TREE):
        if isinstance(n, ast.Call):
            tgt = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
            if tgt in ("dump", "dumps", "write", "save_trades", "writer",
                       "writerow") and "_LAST_EVAL" in names_in(n):
                persist.append(getattr(n, "lineno", "?"))
    check("_LAST_EVAL never reaches a serialiser or file write", not persist,
          persist)
    outside = [n.name for n in ast.walk(TREE)
               if isinstance(n, ast.FunctionDef)
               and "_LAST_EVAL" in names_in(n)
               and n.name != "check_open_trades"]
    check("only check_open_trades touches the store", not outside, outside)


def test_clear_precedes_publish_structurally():
    fn = fn_node("check_open_trades")
    clear_ln = publish_ln = None
    for n in ast.walk(fn):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "clear" and "_LAST_EVAL" in names_in(n)):
            clear_ln = n.lineno
        if isinstance(n, ast.Assign):
            for tg in n.targets:
                if (isinstance(tg, ast.Subscript) and isinstance(tg.value, ast.Name)
                        and tg.value.id == "_LAST_EVAL"
                        and getattr(tg.slice, "value", None) == "cycle_token"):
                    publish_ln = n.lineno
    check("packets.clear() is present", clear_ln is not None)
    check("cycle_token publish is present", publish_ln is not None)
    check("clear() PRECEDES the cycle_token publish "
          "(reverse order exposes new token + stale packets)",
          clear_ln is not None and publish_ln is not None
          and clear_ln < publish_ln, (clear_ln, publish_ln))


def test_capture_precedes_entry_day_skip_and_all_decisions():
    fn = fn_node("check_open_trades")
    cap_ln = next(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Assign)
                  for tg in n.targets
                  if isinstance(tg, ast.Subscript)
                  and isinstance(tg.value, ast.Subscript)
                  and isinstance(tg.value.value, ast.Name)
                  and tg.value.value.id == "_LAST_EVAL")
    src = TSRC.splitlines()

    def line_of(frag, after=0):
        return next(i + 1 for i, l in enumerate(src)
                    if frag in l and i + 1 > after)

    entry_skip = line_of("if today == trade['entry_date']")
    peak = line_of("if current_price > peak_price")
    stop = line_of("if current_price <= effective_stop")
    rsi = line_of("elif current_rsi > rsi_extreme")
    div = line_of("elif bool(latest.get('bearish_div'")
    check("capture precedes the entry-day skip", cap_ln < entry_skip,
          (cap_ln, entry_skip))
    check("capture precedes the peak ratchet", cap_ln < peak, (cap_ln, peak))
    check("capture precedes the stop decision", cap_ln < stop, (cap_ln, stop))
    check("capture precedes the rsi branch", cap_ln < rsi, (cap_ln, rsi))
    check("capture precedes the divergence branch", cap_ln < div, (cap_ln, div))
    for needed in ("current_price", "price_source", "current_rsi", "today"):
        check(f"{needed} is resolved before capture",
              line_of(f"{needed} = ") < cap_ln)


# ------------------------------------------------- 2. runtime token contract --
def test_tokens_rotate_and_clear_prior_packets():
    with Harness([trade()], {"ABM": make_df(52.00)}) as h:
        h.run()
        t1, p1 = token(), dict(packets())
        check("invocation 1 captured a packet", "ABM" in p1)
        check("invocation 1 token is set", t1 is not None)
        h.run()
        t2 = token()
        check("invocation 2 token differs from invocation 1", t2 != t1, (t1, t2))
        check("token is monotonic", t2 > t1, (t1, t2))
        check("packet token matches the enclosing store token",
              packets()["ABM"]["cycle_token"] == t2)
        check("no invocation-1 packet object survives",
              packets()["ABM"] is not p1["ABM"])


def test_partial_cycle_cannot_reuse_an_earlier_packet():
    """THE critical test. Invocation 2 fails on S before capture."""
    with Harness([trade("ABM"), trade("SDGR", entry_price=29.35,
                                      stop_loss=25.06, trailing_stop=28.23,
                                      peak_price=31.37, entry_date="2026-09-18")],
                 {"ABM": make_df(52.00), "SDGR": make_df(29.10)}) as h:
        h.run()
        t1 = token()
        check("both symbols captured in invocation 1",
              set(packets()) == {"ABM", "SDGR"}, sorted(packets()))
        sdgr_v1 = dict(packets()["SDGR"])

        h.fail_symbols = {"SDGR"}
        h.run()
        t2 = token()
        check("SDGR ABSENT after failing before capture",
              "SDGR" not in packets(), sorted(packets()))
        check("ABM still captured in invocation 2", "ABM" in packets())
        check("invocation-1 SDGR values are inaccessible",
              not any(p == sdgr_v1 for p in packets().values()))
        check("surviving packet carries the CURRENT token",
              packets()["ABM"]["cycle_token"] == t2 != t1)


def test_no_new_token_beside_prior_packets():
    """No observable state may pair a new token with prior-cycle packets."""
    with Harness([trade()], {"ABM": make_df(52.00)}) as h:
        h.run()
        t1 = token()
        stale = dict(packets())
        h.run()
        check("after init, token is new AND packets are current",
              token() != t1 and packets()["ABM"]["cycle_token"] == token())
        check("no packet in the store carries a superseded token",
              all(p["cycle_token"] == token() for p in packets().values()),
              [(k, v["cycle_token"]) for k, v in packets().items()])
        check("prior-cycle packet contents are not retained",
              packets()["ABM"] != stale["ABM"] or t1 == token())


def test_store_rotates_identically_with_parity_disabled():
    import os
    saved = os.environ.pop("ARES_PARITY", None)
    try:
        with Harness([trade()], {"ABM": make_df(52.00)}) as h:
            h.run()
            t1 = token()
            h.run()
            check("telemetry rotates with ARES_PARITY unset", token() != t1)
            check("telemetry still captures with ARES_PARITY unset",
                  "ABM" in packets())
    finally:
        if saved is not None:
            os.environ["ARES_PARITY"] = saved


def test_no_cross_contamination_between_symbols():
    with Harness([trade("ABM"), trade("TMO", entry_price=654.54,
                                     stop_loss=634.25, trailing_stop=634.25,
                                     peak_price=675.0, take_profit=772.36,
                                     entry_date="2026-09-22")],
                 {"ABM": make_df(52.00, rsi=55.0),
                  "TMO": make_df(675.00, rsi=61.0)}) as h:
        h.run()
        check("ABM packet holds ABM's price", packets()["ABM"]["price"] == 52.00,
              packets()["ABM"]["price"])
        check("TMO packet holds TMO's price", packets()["TMO"]["price"] == 675.00,
              packets()["TMO"]["price"])
        check("ABM packet holds ABM's rsi", packets()["ABM"]["rsi"] == 55.0)
        check("TMO packet holds TMO's rsi", packets()["TMO"]["rsi"] == 61.0)


# --------------------------------------------- 3. packet content correctness --
def test_packet_captures_the_exact_live_price():
    with Harness([trade()], {"ABM": make_df(52.00)},
                 live={"ABM": 49.78}) as h:
        h.run()
        p = packets()["ABM"]
        check("live price captured, not the daily Close", p["price"] == 49.78,
              p["price"])
        check("price_source records IBKR", p["price_source"] == "IBKR",
              p["price_source"])


def test_packet_captures_unrounded_daily_close():
    with Harness([trade()], {"ABM": make_df(52.123456789)}) as h:
        h.run()
        p = packets()["ABM"]
        check("daily price captured at FULL precision, not 2dp",
              p["price"] == 52.123456789, p["price"])
        check("price_source records daily", p["price_source"] == "daily")


def test_packet_present_for_entry_day_skip():
    """Entry-day symbols must still have a packet, so absence means failure."""
    with Harness([trade(entry_date="2026-09-26")],
                 {"ABM": make_df(52.00, date="2026-09-26")}) as h:
        h.run()
        check("entry-day symbol still captured", "ABM" in packets())
        check("bar_date lets parity derive skipped_entry_day",
              packets()["ABM"]["bar_date"] == "2026-09-26",
              packets()["ABM"]["bar_date"])


def test_packet_matches_the_price_the_decision_used():
    """An exit must fill from the same price the packet recorded."""
    with Harness([trade()], {"ABM": make_df(48.00)}) as h:
        res = h.run()
        p = packets()["ABM"]
        closed = [t for t in res if t["symbol"] == "ABM"][0]
        check("position closed at/below stop", closed["status"] == "closed")
        check("exit labelled stop_loss at the equality boundary",
              closed["exit_reason"] == "stop_loss", closed.get("exit_reason"))
        check("packet price equals the price the decision saw",
              p["price"] == 48.00, p["price"])
        check("packet captured despite the position closing", "ABM" in packets())


# ------------------------------------------- 4. bearish_div truth semantics --
def test_bearish_div_truth_semantics():
    """Capture must equal the inline branch's Boolean interpretation."""
    import numpy as np
    cases = [("False", False, False), ("True", True, True),
             ("0", 0, False), ("1", 1, True),
             ("np.bool_(False)", np.bool_(False), False),
             ("np.bool_(True)", np.bool_(True), True),
             ("None", None, False),
             ('float("nan")', float("nan"), True)]
    for label, raw, want in cases:
        df = make_df(52.00)
        df["bearish_div"] = df["bearish_div"].astype(object)
        df.iloc[-1, df.columns.get_loc("bearish_div")] = raw
        with Harness([trade(strategy="mean_reversion")], {"ABM": df}) as h:
            h.run()
            got = packets()["ABM"]["bearish_div"]
        inline = bool(df.iloc[-1].get("bearish_div", False))
        check(f"bearish_div {label}: captured == inline interpretation",
              got == inline, (got, inline))
        check(f"bearish_div {label}: value is {want}", got == want, got)
    check("NaN is truthy -- documented trap, currently unreachable because "
          "detect_bearish_divergence returns a bool Series",
          bool(float("nan")) is True)


def test_bearish_div_missing_column_defaults_false():
    df = make_df(52.00).drop(columns=["bearish_div"])
    with Harness([trade()], {"ABM": df}) as h:
        h.run()
        check("absent bearish_div column captured as False",
              packets()["ABM"]["bearish_div"] is False,
              packets()["ABM"]["bearish_div"])


# ------------------------------------------------------- 5. non-persistence --
def test_telemetry_never_persisted():
    saved = []
    with Harness([trade()], {"ABM": make_df(48.00)}) as h:
        h.run()
        saved = h.saved_calls
    check("save_trades was called (the position closed)", len(saved) == 1)
    if saved:
        blob = repr(saved[0])
        for key in ("cycle_token", "price_source", "bar_date"):
            check(f"{key} absent from persisted trade state", key not in blob)


def test_telemetry_footprint_is_exactly_four_statements():
    """The instrumentation's whole call surface, enumerated.

    §6.1 must be able to claim the telemetry adds no market access and no
    mutation. That is asserted here structurally rather than inferred from the
    replay, because the replay injects tracker's I/O boundary and therefore
    could not observe a real network call even if one were added.

    NOTE on a mistake worth keeping: ast.walk(root) YIELDS THE ROOT, so an
    earlier version of this check treated the whole check_open_trades body as a
    "telemetry statement" and reported load_stock/get_live_price as reachable.
    Hence the explicit `n is not fn`.
    """
    fn = next(n for n in ast.walk(TREE)
              if isinstance(n, ast.FunctionDef) and n.name == "check_open_trades")

    def names(n):
        return {x.id for x in ast.walk(n) if isinstance(x, ast.Name)}

    stmts = [n for n in ast.walk(fn)
             if isinstance(n, ast.stmt) and n is not fn
             and (names(n) & {"_LAST_EVAL", "_EVAL_CYCLE_SEQ"})
             and not isinstance(n, (ast.For, ast.If, ast.Try, ast.While,
                                    ast.With, ast.FunctionDef))]
    # Derived from the registration table, not pinned to a count. The literal 4
    # was correct for Phase 0.5 and wrong the moment 2B registered two more
    # statements; repinning it to 6 would just defer the same breakage. The
    # invariant is that EVERY telemetry statement in the function is a
    # registered node, and that every in-function registered kind appears once.
    in_fn_kinds = sorted(set(n for n, _ in td.REGISTERED)
                         - {"module_store_decl", "module_seq_decl",
                            "global_decl"})
    kinds = [td.classify(s) for s in stmts]
    check("every telemetry statement is a registered node",
          all(k is not None for k in kinds),
          [(type(s).__name__, s.lineno) for s, k in zip(stmts, kinds)
           if k is None])
    check("telemetry statements are exactly the in-function registered kinds",
          sorted(k for k in kinds if k) == in_fn_kinds,
          sorted(k for k in kinds if k))

    calls = []
    for s in stmts:
        for c in ast.walk(s):
            if isinstance(c, ast.Call):
                f = c.func
                calls.append(f.attr if isinstance(f, ast.Attribute)
                             else getattr(f, "id", "?"))
    check("only clear/bool/get are called",
          sorted(set(calls)) == ["bool", "clear", "get"], sorted(set(calls)))

    reach = set()
    for s in stmts:
        reach |= names(s)
        reach |= {x.attr for x in ast.walk(s) if isinstance(x, ast.Attribute)}
    banned = {"get_live_price", "load_stock", "download_stock", "add_indicators",
              "requests", "urlopen", "reqHistoricalData", "save_trades", "open",
              "print", "_close_trade", "write"}
    check("no network, IO, persistence or exit call is reachable",
          not (reach & banned), sorted(reach & banned))
    check("telemetry touches no position-state field",
          not (reach & {"stop_loss", "trailing_stop", "peak_price", "shares",
                        "take_profit", "scaled_out", "status", "exit_reason"}),
          sorted(reach))


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
