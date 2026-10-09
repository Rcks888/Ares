"""Ares V2.1 — Intraday Trade Monitor
Only checks open trades using IBKR live prices.
No signal scanning, no yfinance download.
"""
from datetime import datetime, timedelta, timezone
from engine.data_feed import get_live_price, disconnect_ib

MYT = timezone(timedelta(hours=8))
from engine.tracker import load_trades, save_trades, _load_params, _close_trade, _holding_days, load_stock, promote_queue
from engine.indicators import add_indicators

# Observation only -- never authoritative, never read back by any decision.
# The import is guarded because a recorder that cannot be imported must not stop
# the monitor from managing open positions. Absence degrades evidence, not trading.
try:
    from engine import monitor_observer as _obs
except Exception:                                   # noqa: BLE001
    _obs = None

# Decision-site capture for the CURRENT invocation. Plain dict, mirroring
# tracker._LAST_EVAL: the loop stores raw values and does nothing else, so every
# derivation and write happens after the loop where it cannot reach a decision.
# Unconditional -- no env gate, because a store that behaves differently when
# observation is off means the observed path is not the path that runs.
_MONITOR_EVAL = {
    "invocation_token": None,
    "cycle_id": None,
    "cycle_started_at": None,
    "params_raw": None,
    "params_resolved": None,
    "observations": [],
    "attempted": 0,
    "open_positions_seen": None,
    "early_return_reason": None,
    "loop_exception": None,
    "monitor_completed": False,
    "flushed": False,
}
_INVOCATION_SEQ = 0

def monitor():
    # Rotate invocation identity BEFORE anything else. Clear first, then publish
    # the token: the reverse order leaves a new token beside prior-invocation
    # observations, which a reader validating freshness would wrongly accept.
    global _INVOCATION_SEQ
    _INVOCATION_SEQ += 1
    _MONITOR_EVAL["observations"].clear()
    _MONITOR_EVAL["attempted"] = 0
    _MONITOR_EVAL["loop_exception"] = None
    _MONITOR_EVAL["early_return_reason"] = None
    _MONITOR_EVAL["monitor_completed"] = False
    _MONITOR_EVAL["flushed"] = False
    _MONITOR_EVAL["invocation_token"] = _INVOCATION_SEQ
    _MONITOR_EVAL["cycle_started_at"] = datetime.now(timezone.utc).isoformat()

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

    _MONITOR_EVAL["params_raw"] = params
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
            "shares_at_decision": trade.get('shares'),
            "ratcheted": False,
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
            new_trailing = peak_price * (1 - trailing_pct)
            if new_trailing > trailing_stop:
                trailing_stop = new_trailing
                trade['trailing_stop'] = round(trailing_stop, 2)
            updated = True
            obs["ratcheted"] = True

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
            # The position is booked at the STOP while `live` is discarded. Both
            # are recorded separately and never reconciled here: whether the
            # stop was executable is not a question this recorder can answer.
            obs["decision"] = "closed"
            obs["exit_reason"] = reason
            obs["booked_fill_price"] = effective_stop
            _close_trade(trade, today, effective_stop, reason)
            print(f"  ❌ {symbol}: CLOSED at ${effective_stop:.2f} — {reason}")
            print(f"     P&L: {trade['pnl_pct']:+.1f}% (${trade['pnl']:+.2f})")
            updated = True
        elif take_profit and live >= take_profit:
            # Full exit. The monitor has no scale-out branch, so a take-profit
            # here closes the whole position where the report path would sell a
            # tranche. Recorded, not reconciled.
            obs["decision"] = "closed"
            obs["exit_reason"] = "take_profit"
            obs["booked_fill_price"] = take_profit
            _close_trade(trade, today, take_profit, 'take_profit')
            print(f"  ✅ {symbol}: CLOSED at ${take_profit:.2f} — take_profit")
            print(f"     P&L: {trade['pnl_pct']:+.1f}% (${trade['pnl']:+.2f})")
            updated = True
        else:
            obs["decision"] = "state_update" if obs["ratcheted"] else "hold"
            days = _holding_days(trade['entry_date'])
            print(f"  📊 {symbol}: ${live:.2f} ({arrow}{unrealized:.1f}%) | "
                  f"Day {days} | SL: ${stop_loss:.2f} | TS: ${trailing_stop:.2f} | "
                  f"TP: ${take_profit if take_profit else 'N/A'}")

    if updated:
        save_trades(trades)
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
        if _obs is not None:
            _obs.flush(_MONITOR_EVAL)
