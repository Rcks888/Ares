import json
from datetime import datetime
from pathlib import Path
from engine.data_feed import refresh_watchlist, load_stock, disconnect_ib
from engine.indicators import add_indicators
from engine.signals import scan_universe, load_watchlist, get_all_symbols
from engine.tracker import open_trade, check_open_trades, print_scorecard, export_csv

def generate_report():
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*50}")
    print(f"  ARES V2 DAILY REPORT — {today}")
    print(f"  RSI: 21-period OHLC4 | Regime-Aware")
    print(f"{'='*50}\n")

    watchlist = load_watchlist()
    all_symbols = get_all_symbols(watchlist)

    print(f"[1] Refreshing data ({len(all_symbols)} stocks)...")
    refresh_watchlist(all_symbols)

    print("\n[2] Checking open positions...")
    check_open_trades()

    print("\n[3] MARKET OVERVIEW")
    print("-" * 40)
    for sym in ["SPY", "QQQ", "IWM"]:
        df = load_stock(sym)
        df = add_indicators(df)
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        change_pct = (float(latest['Close']) - float(prev['Close'])) / float(prev['Close']) * 100
        direction = "+" if change_pct > 0 else "-"
        print(f"  {sym}: ${float(latest['Close']):.2f} "
              f"{direction}{abs(change_pct):.2f}% | "
              f"RSI: {float(latest['rsi']):.0f}")

    print(f"\n[4] SIGNAL SCAN ({len(all_symbols)} stocks)")
    print("-" * 40)
    signals = scan_universe()

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

            portfolio = 10000
            position_size = portfolio * 0.10
            shares = position_size / s['price']
            stop_loss = s['price'] - (s['price'] * s['stdev_20'] * 2)
            from engine.signals import load_strategy_params
            _params = load_strategy_params()
            tp_pct = _params.get('tp_momentum', 0.12) if s['strategy'] in ('momentum_breakout', 'trend_continuation') else _params.get('tp_reversal', 0.08)
            take_profit = s['price'] * (1 + tp_pct)
            print(f"\n    --- WHAT TO DO ---")
            print(f"    Buy:          ${position_size:.0f} worth "
                  f"({shares:.1f} shares)")
            print(f"    Stop-loss:    ${stop_loss:.2f}")
            print(f"    Take-profit:  ${take_profit:.2f} (+{tp_pct*100:.0f}%)")
            print(f"    Trailing:     8% from peak")

            open_trade(s)
            print(f"    [Recorded as virtual trade]")
    else:
        print("  No signals today. Do nothing.")

    print_scorecard()
    export_csv()

    print(f"\n[6] CATEGORY OVERVIEW")
    print("-" * 40)
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
    print("  RULES REMINDER:")
    print("  - Max 10% of portfolio per trade")
    print("  - Keep 30% cash at all times")
    print("  - Sell at stop-loss — no exceptions")
    print("  - Max 8 positions open")
    print("  - Stop trading if down 5% this week")
    print(f"{'='*50}\n")

if __name__ == "__main__":
    generate_report()
    disconnect_ib()
