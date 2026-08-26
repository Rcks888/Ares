import json
from datetime import datetime
from pathlib import Path
from engine.data_feed import refresh_watchlist, load_stock
from engine.indicators import add_indicators
from engine.signals import scan_universe
from engine.tracker import open_trade, check_open_trades, print_scorecard, export_csv

def generate_report():
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*50}")
    print(f"  ARES DAILY REPORT — {today}")
    print(f"{'='*50}\n")

    with open("config/watchlist.json") as f:
        watchlist = json.load(f)

    print("[1] Refreshing data...")
    refresh_watchlist(watchlist["symbols"])

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
        
    print(f"\n[4] SIGNAL SCAN")
    print("-" * 40)
    signals = scan_universe()

    if signals:
        for s in signals:
            print(f"\n  * SIGNAL: {s['symbol']}")
            print(f"    Strategy: {s['strategy']}")
            print(f"    Price:    ${s['price']}")
            print(f"    RSI:      {s['rsi']}")
            print(f"    Volume:   {s['vol_ratio']}x average")
            print(f"    Strength: {s['strength']}")

            portfolio = 10000
            position_size = portfolio * 0.10
            shares = position_size / s['price']
            stop_loss = s['price'] - (s['price'] * s['stdev_20'] * 2)
            print(f"\n    --- WHAT TO DO ---")
            print(f"    Buy:       ${position_size:.0f} worth "
                  f"({shares:.1f} shares)")
            print(f"    Stop-loss: ${stop_loss:.2f}")
            print(f"    Exit when: RSI > 50")

            open_trade(s)
            print(f"    [Recorded as virtual trade]")
    else:
        print("  No signals today. Do nothing.")

    print_scorecard()
    export_csv()

    print(f"\n[6] WATCHLIST STATUS")
    print("-" * 40)
    print(f"  {'Symbol':<8}{'Price':<10}{'RSI':<8}{'Vol':<8}{'Note'}")
    print(f"  {'------':<8}{'-----':<10}{'---':<8}{'---':<8}{'----'}")

    for sym in watchlist["symbols"]:
        try:
            df = load_stock(sym)
            df = add_indicators(df)
            latest = df.iloc[-1]
            rsi = float(latest['rsi'])
            vol = float(latest['vol_ratio'])
            price = float(latest['Close'])

            note = ""
            if rsi < 35:
                note = "<- near oversold"
            elif rsi > 65:
                note = "<- overbought"
            if vol > 1.8:
                note += " HIGH VOL"

            print(f"  {sym:<8}${price:<9.2f}{rsi:<8.0f}{vol:<8.1f}{note}")
        except Exception as e:
            print(f"  {sym:<8} ERROR: {e}")

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