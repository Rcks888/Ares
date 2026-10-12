"""Ares V2.1 — Intraday Trade Monitor
Only checks open trades using IBKR live prices.
No signal scanning, no yfinance download.
"""
import json as _json
import os as _os
import sys as _sys
from datetime import datetime, timedelta, timezone
from engine.data_feed import get_live_price, disconnect_ib

MYT = timezone(timedelta(hours=8))
from engine.tracker import load_trades, save_trades, _load_params, _close_trade, _holding_days, load_stock, promote_queue
from engine.indicators import add_indicators

# Observation only -- never authoritative, never read back by any decision.
# The import is guarded because a recorder that cannot be imported must not stop
# the monitor from managing open positions. Absence degrades evidence, not
# trading -- but it must not look like healthy silence either, so the failure is
# recorded and emitted through the health path below.
try:
    from engine import monitor_observer as _obs
    _OBS_IMPORT_ERROR = None
except Exception as _exc:                           # noqa: BLE001
    _obs = None
    _OBS_IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"

# Decision-site capture for the CURRENT invocation. Plain dict, mirroring
# tracker._LAST_EVAL: the loop stores raw values and does nothing else, so every
# derivation and write happens after the loop where it cannot reach a decision.
# Unconditional -- no env gate, because a store that behaves differently when
# observation is off means the observed path is not the path that runs.
_INVOCATION_SEQ = 0


def _new_invocation_store(token):
    """THE registered initialisation contract. Every invocation-owned field is
    defined here and nowhere else.

    Returned whole and swapped in, rather than selectively cleared. Selective
    clearing left cycle_id, params_raw, params_resolved and open_positions_seen
    holding the PREVIOUS invocation's values, so a cycle that failed early or
    returned with zero positions could publish a fresh token above stale
    population and configuration. A new token does not make retained metadata
    current.
    """
    return {
        "invocation_token": token,
        # Left None deliberately: the serialiser mints one per invocation, so a
        # retained identity cannot survive into a later cycle.
        "cycle_id": None,
        "cycle_started_at": datetime.now(timezone.utc).isoformat(),
        "params_raw": None,
        "params_resolved": None,
        "observations": [],
        "attempted": 0,
        "open_positions_seen": None,
        "early_return_reason": None,
        "loop_exception": None,
        "monitor_completed": False,
        "trade_state_save_attempted": False,
        "trade_state_save_completed": False,
        "flushed": False,
    }


_MONITOR_EVAL = _new_invocation_store(None)


def _emit_observation_health_failure(reason, detail):
    """Record that OBSERVATION failed, on a path that needs no recorder.

    Reached when the recorder could not be imported, or when its own flush could
    not write. Uses nothing but builtins and the stdlib imported here, because
    the condition it reports may be that `engine` is unimportable.

    Distinguishes three states that are otherwise identical silence:
      a health line and no heartbeat  -> recorder unavailable or unable to write
      a heartbeat                     -> recorder invoked
      neither                         -> the monitor itself never ran

    Never raises. A diagnostic that can fail the cycle it describes is worse
    than no diagnostic.
    """
    try:
        path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                             "logs", "monitor_health_v1.jsonl")
        _os.makedirs(_os.path.dirname(path), exist_ok=True)
        line = _json.dumps({
            "health_schema_version": 1,
            "decision_path": "monitor",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "invocation_token": _MONITOR_EVAL.get("invocation_token"),
            "observation_failed": True,
            "reason": reason,
            "detail": detail,
            "monitor_completed": _MONITOR_EVAL.get("monitor_completed"),
            "attempted": _MONITOR_EVAL.get("attempted"),
        }, sort_keys=True, default=str)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
        # Distinct from ordinary trading output, and on stderr so a stdout
        # scraper cannot mistake it for a position line.
        print(f"OBSERVATION-HEALTH: {reason}: {detail}", file=_sys.stderr)
    except BaseException:                           # noqa: BLE001
        pass

def monitor():
    # Rotate invocation identity BEFORE anything else. Clear first, then publish
    # the token: the reverse order leaves a new token beside prior-invocation
    # observations, which a reader validating freshness would wrongly accept.
    global _INVOCATION_SEQ
    _INVOCATION_SEQ += 1
    _MONITOR_EVAL.clear()
    _MONITOR_EVAL.update(_new_invocation_store(_INVOCATION_SEQ))

    today_str = datetime.now(MYT).strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*50}")
    print(f"  ARES V3 TRADE MONITOR — {today_str}")
    print(f"  Mode: IBKR 1-min Bar Price Check")
    print(f"{'='*50}\n")

    trades = load_trades()
    open_trades = [t for t in trades if t['status'] == 'open']

    _MONITOR_EVAL["open_positions_seen"] = len(open_trades)

    if not open_trades:
        # Named so that zero records is not ambiguous. A cycle with nothing to
        # monitor and a cycle where the recorder never ran are otherwise
        # identical silence.
        _MONITOR_EVAL["early_return_reason"] = "no_open_positions"
        _MONITOR_EVAL["monitor_completed"] = True
        print("  No open positions to monitor.")
        disconnect_ib()
        return

    print(f"  Monitoring {len(open_trades)} open position(s)...\n")
    params = _load_params()
    trailing_pct = params.get('trailing_stop_pct', 0.08)
    rsi_extreme = params.get('rsi_extreme_high', 90)
    updated = False

    # A COPY. Retaining the live mapping would let a later mutation rewrite the
    # snapshot that was supposedly recorded at this point in the cycle.
    _MONITOR_EVAL["params_raw"] = dict(params)
    if _obs is not None:
        try:
            _MONITOR_EVAL["params_resolved"] = _obs.effective_config(params)
        except Exception:                           # noqa: BLE001
            _MONITOR_EVAL["params_resolved"] = None

    for trade in trades:
        if trade['status'] != 'open':
            continue

        symbol = trade['symbol']
        live = get_live_price(symbol)
        entry = trade['entry_price']
        stop_loss = trade['stop_loss']
        trailing_stop = trade.get('trailing_stop', stop_loss)
        peak_price = trade.get('peak_price', entry)
        take_profit = trade.get('take_profit')

        # Appended BEFORE any branch, then mutated in place. If the loop raises
        # mid-trade -- and the monitor has no per-trade try/except, so it will
        # abort the whole loop -- the partial record is already in the store and
        # surfaces as record_complete=false. An absent record would be
        # indistinguishable from a position that was never held.
        obs = {
            "symbol": symbol,
            "entry_date": trade.get('entry_date'),
            "holding_basis": "entry_date_string",
            "observed_live": live,
            "entry_price": entry,
            "stop_loss": stop_loss,
            "trailing_stop_before": trailing_stop,
            "peak_price_before": peak_price,
            "take_profit": take_profit,
            # The REMAINING quantity at decision time. For a scaled-out
            # position this is not the original size, which is why the name says
            # at_decision.
            "shares_at_decision": trade.get('shares'),
            "peak_advanced": False,
            "trail_advanced": False,
            "close_requested": False,
        }
        _MONITOR_EVAL["observations"].append(obs)
        _MONITOR_EVAL["attempted"] += 1

        if not live:
            days = _holding_days(trade['entry_date'])
            tp_str = f"${take_profit:.2f}" if take_profit else "N/A"
            print(f"  📊 {symbol}: No live price | "
                  f"entry ${entry:.2f} | Day {days} | "
                  f"SL: ${stop_loss:.2f} | TS: ${trailing_stop:.2f} | TP: {tp_str}")
            # No stop was evaluated at all on this symbol this cycle. The cause
            # is unrecoverable: get_live_price returns None for a dead gateway,
            # a timeout, an unqualified contract and genuinely no bars alike.
            obs["skip_reason"] = "live_price_unavailable_cause_unknown"
            obs["decision"] = "skipped"
            continue

        unrealized = (live - entry) / entry * 100
        arrow = "+" if unrealized > 0 else ""

        if live > peak_price:
            peak_price = live
            trade['peak_price'] = round(peak_price, 2)
            obs["peak_advanced"] = True
            new_trailing = peak_price * (1 - trailing_pct)
            if new_trailing > trailing_stop:
                trailing_stop = new_trailing
                trade['trailing_stop'] = round(trailing_stop, 2)
                # A new peak does not imply the trail moved. Collapsing these
                # into one flag reported a ratchet that never advanced a stop.
                obs["trail_advanced"] = True
            updated = True

        effective_stop = max(stop_loss, trailing_stop)
        today = datetime.now().strftime("%Y-%m-%d")

        # The exact values the comparison below uses. trailing_stop may be an
        # UNROUNDED ratchet while only round(x, 2) is persisted, so these cannot
        # be recovered from stored state afterwards.
        obs["peak_price_after"] = peak_price
        obs["trailing_stop_after_unrounded"] = trailing_stop
        obs["effective_stop_unrounded"] = effective_stop
        obs["unrealized_pct_at_decision"] = unrealized
        obs["exit_date_recorded"] = today

        if live <= effective_stop:
            reason = 'trailing_stop' if trailing_stop > stop_loss else 'stop_loss'
            # Four separate facts, in the order they become true. The ARGUMENT
            # is not the booked price: _close_trade applies slippage and rounds,
            # and it can raise after partially mutating the trade. So the
            # decision is recorded as REQUESTED here, and only promoted to a
            # completed close once the function has returned and the booked
            # price can be read back from the trade it wrote.
            obs["close_requested"] = True
            obs["exit_reason"] = reason
            obs["close_price_argument"] = effective_stop
            _close_trade(trade, today, effective_stop, reason)
            obs["close_function_completed"] = True
            obs["booked_exit_price"] = trade.get('exit_price')
            obs["decision"] = "closed"
            print(f"  ❌ {symbol}: CLOSED at ${effective_stop:.2f} — {reason}")
            print(f"     P&L: {trade['pnl_pct']:+.1f}% (${trade['pnl']:+.2f})")
            updated = True
        elif take_profit and live >= take_profit:
            # Full exit. The monitor has no scale-out branch, so a take-profit
            # here closes the whole position where the report path would sell a
            # tranche. Recorded, not reconciled.
            obs["close_requested"] = True
            obs["exit_reason"] = "take_profit"
            obs["close_price_argument"] = take_profit
            _close_trade(trade, today, take_profit, 'take_profit')
            obs["close_function_completed"] = True
            obs["booked_exit_price"] = trade.get('exit_price')
            obs["decision"] = "closed"
            print(f"  ✅ {symbol}: CLOSED at ${take_profit:.2f} — take_profit")
            print(f"     P&L: {trade['pnl_pct']:+.1f}% (${trade['pnl']:+.2f})")
            updated = True
        else:
            obs["decision"] = "state_update" if obs["peak_advanced"] else "hold"
            days = _holding_days(trade['entry_date'])
            print(f"  📊 {symbol}: ${live:.2f} ({arrow}{unrealized:.1f}%) | "
                  f"Day {days} | SL: ${stop_loss:.2f} | TS: ${trailing_stop:.2f} | "
                  f"TP: ${take_profit if take_profit else 'N/A'}")

    if updated:
        # Persistence recorded as attempted-then-completed, so a save that
        # raised is distinguishable from a save that never ran. promote_queue
        # failing AFTERWARDS does not retract the save.
        _MONITOR_EVAL["trade_state_save_attempted"] = True
        save_trades(trades)
        _MONITOR_EVAL["trade_state_save_completed"] = True
        # A close freed a slot — promote from the ranked queue rather than
        # leaving it idle until the next scan. No rescanning here.
        promote_queue(source="monitor", use_live=True)

    disconnect_ib()
    _MONITOR_EVAL["monitor_completed"] = True
    print(f"\n{'='*50}\n")

if __name__ == "__main__":
    # The flush lives in a finally so a monitor that raised mid-loop still
    # publishes the observations it had already captured, plus a heartbeat
    # recording that the cycle ran and did not complete. The original exception
    # is preserved: the bare raise re-raises it unchanged, and the flush cannot
    # replace it because flush() never raises.
    try:
        monitor()
    except BaseException as exc:
        _MONITOR_EVAL["loop_exception"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if _obs is None:
            _emit_observation_health_failure("recorder_import_failed",
                                            _OBS_IMPORT_ERROR)
        else:
            _summary = _obs.flush(_MONITOR_EVAL)
            # A recorder that ran but could not write its evidence must not be
            # indistinguishable from one that wrote successfully. Checked here
            # rather than inside flush, because flush must stay incapable of
            # raising into this finally.
            if (not _summary.get("heartbeat_written")
                    or _summary.get("write_failures")
                    or _summary.get("flush_error")):
                _emit_observation_health_failure(
                    "recorder_write_failed", _json.dumps(_summary,
                                                         sort_keys=True,
                                                         default=str))
