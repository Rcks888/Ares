import json
from datetime import datetime, timedelta, timezone

MYT = timezone(timedelta(hours=8))
from pathlib import Path
from engine.data_feed import refresh_watchlist, load_stock, disconnect_ib, prune_cache
from engine.indicators import add_indicators
from engine.signals import scan_universe, load_watchlist, get_all_symbols
from engine.tracker import open_trade, print_scorecard, export_csv, check_shadow_trades, execute_pending_signals, maintain_queue, promote_queue, load_trades, load_queue
from engine.parity_hook import observed_check_open_trades
import os as _os
import sys as _sys
import time as _time

# Admission capture v1 -- observation only. Both imports are guarded: a
# recorder that cannot be imported, or a tracker deployed without the store,
# must not stop the report. The fallback store is a plain dict, so every
# capture statement below still succeeds and records into nothing.
try:
    from engine.tracker import _ADMISSION_EVAL, _new_admission_store
    _ADM_STORE_ERROR = None
except Exception as _exc:                           # noqa: BLE001
    _ADMISSION_EVAL = {}

    def _new_admission_store(token):
        return {"invocation_token": token, "scan_open_trade_expected": 0}

    _ADM_STORE_ERROR = f"{type(_exc).__name__}: {_exc}"
try:
    from engine import admission_observer as _adm_obs
    _ADM_OBS_IMPORT_ERROR = None
except Exception as _exc:                           # noqa: BLE001
    _adm_obs = None
    _ADM_OBS_IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"

_ADMISSION_SEQ = 0


def _emit_admission_health_failure(reason, detail):
    """Record that ADMISSION OBSERVATION failed, on a path needing no recorder.

    Same contract as monitor_trades._emit_observation_health_failure: stdlib
    only, never raises, announced on stderr so a stdout scraper cannot mistake
    it for trading output.
    """
    try:
        path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                             "logs", "admission_health_v1.jsonl")
        _os.makedirs(_os.path.dirname(path), exist_ok=True)
        line = json.dumps({
            "health_schema_version": 1,
            "decision_path": "report",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "invocation_token": _ADMISSION_EVAL.get("invocation_token"),
            "observation_failed": True,
            "reason": reason,
            "detail": detail,
            "report_completed": _ADMISSION_EVAL.get("report_completed"),
        }, sort_keys=True, default=str)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
        print(f"ADMISSION-HEALTH: {reason}: {detail}", file=_sys.stderr)
    except BaseException:                           # noqa: BLE001
        pass


USE_SCREENER = True

def generate_report():
    global _ADMISSION_SEQ
    _ADMISSION_SEQ += 1
    _ADMISSION_EVAL.clear()
    _ADMISSION_EVAL.update(_new_admission_store(_ADMISSION_SEQ))

    today = datetime.now(MYT).strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*50}")
    print(f"  ARES V3 DAILY REPORT — {today}")
    print(f"  RSI: 21-period OHLC4 | Regime-Aware | Scale-Out")
    print(f"  Mode: {'Dynamic Screener' if USE_SCREENER else 'Fixed Watchlist'}")
    print(f"{'='*50}\n")

    screen_data = {}
    if USE_SCREENER:
        print("[0] FINVIZ MARKET SCREENING")
        print("-" * 40)
        try:
            from engine.screener import get_screened_symbols
            all_symbols, screen_data = get_screened_symbols()
            print(f"  Screened {len(all_symbols)} candidates from entire market")
        except Exception as e:
            print(f"  Screener error: {e}")
            print(f"  Falling back to fixed watchlist")
            watchlist = load_watchlist()
            all_symbols = get_all_symbols(watchlist)
    else:
        watchlist = load_watchlist()
        all_symbols = get_all_symbols(watchlist)

    market_syms = ["SPY", "QQQ", "IWM"]
    for sym in market_syms:
        if sym not in all_symbols:
            all_symbols.append(sym)

    print(f"\n[1] Refreshing data ({len(all_symbols)} stocks)...")
    refresh_watchlist(all_symbols)

    held = {t['symbol'] for t in load_trades()}
    queued = {q['symbol'] for q in load_queue()}
    prune_cache(set(all_symbols) | held | queued)

    # Must run AFTER refresh_watchlist: fills read today's open price, and
    # load_stock()'s 20h staleness window is longer than the 7.5h/16.5h gaps
    # between scans, so a pre-refresh fill silently used yesterday's bar.
    _ADMISSION_EVAL["fill_pass_expected"] = True
    execute_pending_signals()

    print("\n[2] Checking open positions...")
    # Phase 4: identical to check_open_trades() unless ARES_PARITY=1. The inline
    # tracker remains the sole decision authority either way.
    observed_check_open_trades()

    print("\n[2b] SIGNAL QUEUE")
    print("-" * 40)
    maintain_queue()
    _ADMISSION_EVAL["promotion_expected"] = True
    promote_queue(source="scan")

    print("\n[3] MARKET OVERVIEW")
    print("-" * 40)
    for sym in market_syms:
        try:
            df = load_stock(sym)
            df = add_indicators(df)
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            change_pct = (float(latest['Close']) - float(prev['Close'])) / float(prev['Close']) * 100
            direction = "+" if change_pct > 0 else "-"
            print(f"  {sym}: ${float(latest['Close']):.2f} "
                  f"{direction}{abs(change_pct):.2f}% | "
                  f"RSI: {float(latest['rsi']):.0f}")
        except Exception:
            pass

    print(f"\n[4] SIGNAL SCAN ({len(all_symbols)} stocks)")
    print("-" * 40)
    _ADMISSION_EVAL["t_scan_start"] = _time.time()
    signals = scan_universe(all_symbols, screen_data)
    _ADMISSION_EVAL["t_scan_end"] = _time.time()
    _ADMISSION_EVAL["signals"] = signals

    if signals:
        for s in signals:
            print(f"\n  * SIGNAL: {s['symbol']} [{s['category']}]")
            print(f"    Strategy:   {s['strategy']}")
            print(f"    Trigger:    {s['trigger']}")
            print(f"    Regime:     {s.get('regime', 'N/A')}")
            print(f"    Confluence: {s.get('confluence', 'N/A')} signals")
            print(f"    Price:      ${s['price']}")
            print(f"    RSI:        {s['rsi']}")
            print(f"    Volume:     {s['vol_ratio']}x average")
            print(f"    Strength:   {s['strength']}")
            if s.get('screens'):
                print(f"    Screens:    {', '.join(s['screens'])}")

            from engine.signals import load_strategy_params
            _params = load_strategy_params()
            max_pos = _params.get('max_positions', 5)
            portfolio = _params.get('starting_capital', 1000)
            cash_reserve = _params.get('cash_reserve_pct', 0.25)
            available = portfolio * (1 - cash_reserve)
            position_size = available / max_pos
            shares = position_size / s['price']
            stop_loss = s['price'] - (s['price'] * s['stdev_20'] * 2)
            tp_pct = _params.get('tp_momentum', 0.18) if s['strategy'] == 'momentum_breakout' else _params.get('tp_reversal', 0.10)
            take_profit = s['price'] * (1 + tp_pct)
            trailing_pct = _params.get('trailing_stop_pct', 0.10)
            print(f"\n    --- WHAT TO DO ---")
            print(f"    Buy:          ${position_size:.0f} worth "
                  f"({shares:.1f} shares)")
            print(f"    Stop-loss:    ${stop_loss:.2f}")
            print(f"    Take-profit:  ${take_profit:.2f} (+{tp_pct*100:.0f}%) — scale out 50%")
            print(f"    Trailing:     {trailing_pct*100:.0f}% from peak (remaining 50%)")

            _ADMISSION_EVAL["scan_open_trade_expected"] += 1
            result = open_trade(s)
            if result == 'pending':
                print(f"    [PENDING — will execute at next day's open]")
            elif result == 'queued':
                print(f"    [QUEUED — slots full, waiting for opening]")
            elif result == 'duplicate':
                print(f"    [SKIP — already open or pending]")
    else:
        print("  No signals today. Do nothing.")

    summary_path = Path(__file__).parent / "logs" / "last_scan_summary.txt"
    signal_details = []
    for s in signals:
        _params = load_strategy_params()
        max_pos = _params.get('max_positions', 5)
        cash_reserve = _params.get('cash_reserve_pct', 0.25)
        portfolio = _params.get('starting_capital', 1000)
        available = portfolio * (1 - cash_reserve)
        pos_size = available / max_pos
        shares = pos_size / s['price']
        stdev = s.get('stdev_20', s['price'] * 0.05)
        sl = s['price'] - (s['price'] * stdev * 2)
        tp_pct = _params.get('tp_momentum', 0.18) if s['strategy'] == 'momentum_breakout' else _params.get('tp_reversal', 0.10)
        tp = s['price'] * (1 + tp_pct)
        ts_pct = _params.get('trailing_stop_pct', 0.10)
        detail = (f"{s['symbol']} | {s['strategy']} | conf{s.get('confluence',1)} | {s.get('regime','?')}\n"
                  f"  Price: ${s['price']:.2f} | RSI: {s['rsi']:.1f} | Vol: {s['vol_ratio']}x\n"
                  f"  Buy: ${pos_size:.0f} ({shares:.1f} shares)\n"
                  f"  SL: ${sl:.2f} | TP: ${tp:.2f} (+{tp_pct*100:.0f}%) | TS: {ts_pct*100:.0f}%")
        signal_details.append(detail)

    with open(summary_path, 'w') as f:
        f.write(f"candidates_screened:{len(all_symbols)}\n")
        f.write(f"signals_found:{len(signals)}\n")
        f.write(f"signal_details:{'|||'.join(signal_details)}\n")

    print_scorecard()
    check_shadow_trades()
    export_csv()

    print(f"\n[6] SCREEN OVERVIEW")
    print("-" * 40)
    if screen_data:
        screen_counts = {}
        for sym, tags in screen_data.items():
            for tag in tags:
                screen_counts[tag] = screen_counts.get(tag, 0) + 1
        for screen_name, count in sorted(screen_counts.items(), key=lambda x: -x[1]):
            print(f"  {screen_name:<20} {count} stocks")
    else:
        watchlist = load_watchlist()
        for cat_name, cat_data in watchlist['categories'].items():
            rsi_values = []
            for sym in cat_data['symbols']:
                try:
                    df = load_stock(sym)
                    df = add_indicators(df)
                    rsi_values.append(float(df.iloc[-1]['rsi']))
                except:
                    pass
            if rsi_values:
                avg_rsi = sum(rsi_values) / len(rsi_values)
                low_rsi = min(rsi_values)
                print(f"  {cat_name:<16} Avg RSI: {avg_rsi:.0f} | "
                      f"Lowest: {low_rsi:.0f} | "
                      f"Stocks: {len(cat_data['symbols'])}")

    print(f"\n{'='*50}")
    print("  V3 RULES — OBSERVATION PHASE:")
    print("  - Max 5 positions open ($150 each)")
    print("  - Keep 25% cash reserve")
    print("  - 10% trailing stop — no exceptions")
    print("  - Scale out 50% at TP, ride rest")
    print("  - Do NOT change parameters")
    print("  - Do NOT override signals manually")
    print(f"{'='*50}\n")
    _ADMISSION_EVAL["report_completed"] = True

if __name__ == "__main__":
    # Same shape as monitor_trades.py: the flush is in a finally so a report
    # that raised still publishes what it captured; the bare raise preserves
    # the original exception, and flush() never raises. disconnect_ib() stays
    # outside, exactly where it was: a raising report still skips it.
    try:
        generate_report()
    except BaseException as exc:
        _ADMISSION_EVAL["loop_exception"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if _adm_obs is None or _ADM_STORE_ERROR is not None:
            _emit_admission_health_failure(
                "recorder_import_failed",
                _ADM_OBS_IMPORT_ERROR or _ADM_STORE_ERROR)
        else:
            _adm_summary = _adm_obs.flush(_ADMISSION_EVAL, decision_path="report",
                                          peer=None)
            if (not _adm_summary.get("heartbeat_written")
                    or _adm_summary.get("write_failures")
                    or _adm_summary.get("flush_error")):
                _emit_admission_health_failure(
                    "recorder_write_failed", json.dumps(_adm_summary,
                                                        sort_keys=True,
                                                        default=str))
    disconnect_ib()
