"""Ares V2.1 — Intraday Trade Monitor
Only checks open trades using IBKR live prices.
No signal scanning, no yfinance download.
"""
from datetime import datetime
from engine.data_feed import get_live_price, disconnect_ib
from engine.tracker import load_trades, save_trades, _load_params, _close_trade, load_stock
from engine.indicators import add_indicators

def monitor():
    today_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*50}")
    print(f"  ARES V2.1 TRADE MONITOR — {today_str}")
    print(f"  Mode: IBKR Live Price Check")
    print(f"{'='*50}\n")

    trades = load_trades()
    open_trades = [t for t in trades if t['status'] == 'open']

    if not open_trades:
        print("  No open positions to monitor.")
        disconnect_ib()
        return

    print(f"  Monitoring {len(open_trades)} open position(s)...\n")
    params = _load_params()
    trailing_pct = params.get('trailing_stop_pct', 0.08)
    rsi_extreme = params.get('rsi_extreme_high', 90)
    updated = False

    for trade in trades:
        if trade['status'] != 'open':
            continue

        symbol = trade['symbol']
        live = get_live_price(symbol)

        if not live:
            print(f"  {symbol}: No live price available (market closed?)")
            continue

        entry = trade['entry_price']
        stop_loss = trade['stop_loss']
        trailing_stop = trade.get('trailing_stop', stop_loss)
        peak_price = trade.get('peak_price', entry)
        take_profit = trade.get('take_profit')
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

        effective_stop = max(stop_loss, trailing_stop)
        today = datetime.now().strftime("%Y-%m-%d")

        if live <= effective_stop:
            reason = 'trailing_stop' if trailing_stop > stop_loss else 'stop_loss'
            _close_trade(trade, today, effective_stop, reason)
            print(f"  ❌ {symbol}: CLOSED at ${effective_stop:.2f} — {reason}")
            print(f"     P&L: {trade['pnl_pct']:+.1f}% (${trade['pnl']:+.2f})")
            updated = True
        elif take_profit and live >= take_profit:
            _close_trade(trade, today, take_profit, 'take_profit')
            print(f"  ✅ {symbol}: CLOSED at ${take_profit:.2f} — take_profit")
            print(f"     P&L: {trade['pnl_pct']:+.1f}% (${trade['pnl']:+.2f})")
            updated = True
        else:
            print(f"  📊 {symbol}: ${live:.2f} ({arrow}{unrealized:.1f}%) | "
                  f"SL: ${stop_loss:.2f} | TS: ${trailing_stop:.2f} | "
                  f"TP: ${take_profit if take_profit else 'N/A'}")

    if updated:
        save_trades(trades)

    disconnect_ib()
    print(f"\n{'='*50}\n")

if __name__ == "__main__":
    monitor()
