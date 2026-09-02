import json
import csv
from datetime import datetime
from pathlib import Path
from engine.data_feed import load_stock
from engine.indicators import add_indicators

LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
TRADES_FILE = LOGS_DIR / "virtual_trades.json"

def load_trades():
    """Load all virtual trades from disk."""
    if not TRADES_FILE.exists():
        return []
    with open(TRADES_FILE) as f:
        return json.load(f)
    
def save_trades(trades):
    """Save all virtual trades to disk."""
    with open(TRADES_FILE, 'w') as f:
        json.dump(trades, f, indent=2)

def open_trade(signal):
    """Record a new virtual trade from a signal."""
    trades = load_trades()
    for t in trades:
        if t['symbol'] == signal['symbol'] and t['status'] == 'open':
            return
        
    portfolio = 10000
    position_size = portfolio * 0.10
    shares = position_size / signal['price']
    stop_loss = signal['price'] - (signal['price'] * signal['stdev_20'] * 2)
    tp_pct = 0.12 if signal['strategy'] == 'momentum_breakout' else 0.08
    take_profit = signal['price'] * (1 + tp_pct)
    trade = {
        'symbol': signal['symbol'],
        'strategy': signal['strategy'],
        'trigger': signal.get('trigger', 'unknown'),
        'category': signal.get('category', 'unknown'),
        'entry_date': signal['date'],
        'entry_price': signal['price'],
        'shares': round(shares, 2),
        'position_size': round(position_size, 2),
        'stop_loss': round(stop_loss, 2),
        'take_profit': round(take_profit, 2),
        'rsi_at_entry': signal['rsi'],
        'vol_at_entry': signal['vol_ratio'],
        'strength': signal['strength'],
        'status': 'open',
        'exit_date': None,
        'exit_price': None,
        'exit_reason': None,
        'pnl': None,
        'pnl_pct': None
    }

    trades.append(trade)
    save_trades(trades)

def check_open_trades():
    """Check all open trades for stop-loss or take-profit."""
    trades = load_trades()
    updated = False

    for trade in trades:
        if trade['status'] != 'open':
            continue

        try:
            df = load_stock(trade['symbol'])
            df = add_indicators(df)
            latest = df.iloc[-1]

            current_price = float(latest['Close'])
            current_rsi = float(latest['rsi'])
            today = str(latest.name)[:10]

            take_profit = trade.get('take_profit')

            if current_price <= trade['stop_loss']:
                trade['status'] = 'closed'
                trade['exit_date'] = today
                trade['exit_price'] = trade['stop_loss']
                trade['exit_reason'] = 'stop_loss'
                pnl = (trade['stop_loss'] - trade['entry_price']) * trade['shares']
                trade['pnl'] = round(pnl, 2)
                trade['pnl_pct'] = round(
                    (trade['stop_loss'] - trade['entry_price'])
                    / trade['entry_price'] * 100, 2)
                updated = True

            elif take_profit and current_price >= take_profit and today != trade['entry_date']:
                trade['status'] = 'closed'
                trade['exit_date'] = today
                trade['exit_price'] = round(take_profit, 2)
                trade['exit_reason'] = 'take_profit'
                pnl = (take_profit - trade['entry_price']) * trade['shares']
                trade['pnl'] = round(pnl, 2)
                trade['pnl_pct'] = round(
                    (take_profit - trade['entry_price'])
                    / trade['entry_price'] * 100, 2)
                updated = True

            elif trade['strategy'] == 'momentum_breakout' and current_rsi > 75 and today != trade['entry_date']:
                # Momentum breakout: exit when overbought (RSI > 75)
                trade['status'] = 'closed'
                trade['exit_date'] = today
                trade['exit_price'] = round(current_price, 2)
                trade['exit_reason'] = 'target_reached'
                pnl = (current_price - trade['entry_price']) * trade['shares']
                trade['pnl'] = round(pnl, 2)
                trade['pnl_pct'] = round(
                    (current_price - trade['entry_price'])
                    / trade['entry_price'] * 100, 2)
                updated = True

            elif trade['strategy'] == 'rsi_reversal' and current_rsi > 50 and today != trade['entry_date']:
                # RSI reversal: exit when mean reversion complete (RSI > 50)
                trade['status'] = 'closed'
                trade['exit_date'] = today
                trade['exit_price'] = round(current_price, 2)
                trade['exit_reason'] = 'target_reached'
                pnl = (current_price - trade['entry_price']) * trade['shares']
                trade['pnl'] = round(pnl, 2)
                trade['pnl_pct'] = round(
                    (current_price - trade['entry_price'])
                    / trade['entry_price'] * 100, 2)
                updated = True

        except Exception as e:
            print(f"  Error checking {trade['symbol']}: {e}")

    if updated:
        save_trades(trades)

    return trades

def print_scorecard():
    """Print running performance scorecard."""
    trades = load_trades()
    closed = [t for t in trades if t['status'] == 'closed']
    open_trades = [t for t in trades if t['status'] == 'open']

    print(f"\n[5] SCORECARD")
    print("-" * 40)

    if not closed and not open_trades:
        print("  No trades recorded yet.")
        return

    if open_trades:
        print(f"\n  OPEN POSITIONS ({len(open_trades)}):")
        for t in open_trades:
            try:
                df = load_stock(t['symbol'])
                current = float(df.iloc[-1]['Close'])
                unrealized = (current - t['entry_price']) / t['entry_price'] * 100
                arrow = "+" if unrealized > 0 else "-"
                tp = t.get('take_profit', 'N/A')
                print(f"    {t['symbol']}: entry ${t['entry_price']} -> "
                      f"now ${current:.2f} {arrow}{abs(unrealized):.1f}% | "
                      f"stop: ${t['stop_loss']} | TP: ${tp}")
            except Exception:
                tp = t.get('take_profit', 'N/A')
                print(f"    {t['symbol']}: entry ${t['entry_price']} | "
                      f"stop: ${t['stop_loss']} | TP: ${tp}")

    if closed:
        wins = [t for t in closed if t['pnl'] > 0]
        losses = [t for t in closed if t['pnl'] <= 0]
        total_pnl = sum(t['pnl'] for t in closed)
        avg_win = (sum(t['pnl_pct'] for t in wins) / len(wins)
                   if wins else 0)
        avg_loss = (sum(t['pnl_pct'] for t in losses) / len(losses)
                    if losses else 0)

        print(f"\n  CLOSED TRADES ({len(closed)}):")
        print(f"    Win rate:   {len(wins)}/{len(closed)} "
              f"({len(wins)/len(closed)*100:.0f}%)")
        print(f"    Total P&L:  ${total_pnl:+.2f}")
        print(f"    Avg win:    {avg_win:+.1f}%")
        print(f"    Avg loss:   {avg_loss:+.1f}%")

        print(f"\n  RECENT TRADES:")
        for t in closed[-5:]:
            icon = "W" if t['pnl'] > 0 else "L"
            print(f"    [{icon}] {t['symbol']} | "
                  f"{t['entry_date']} -> {t['exit_date']} | "
                  f"{t['pnl_pct']:+.1f}% | {t['exit_reason']}")
        strategies = {}
        for t in closed:
            s = t['strategy']
            if s not in strategies:
                strategies[s] = {'wins': 0, 'total': 0}
            strategies[s]['total'] += 1
            if t['pnl'] > 0:
                strategies[s]['wins'] += 1

        print(f"\n  BY STRATEGY:")
        for s, data in strategies.items():
            wr = data['wins'] / data['total'] * 100
            print(f"    {s}: {data['wins']}/{data['total']} wins ({wr:.0f}%)")

def export_csv():
    """Export all trades to CSV for easy viewing in Excel."""
    trades = load_trades()
    if not trades:
        print("  No trades to export.")
        return

    csv_path = LOGS_DIR / "trades_report.csv"
    columns = [
        'symbol', 'strategy', 'trigger', 'category', 'entry_date', 'entry_price', 'shares',
        'position_size', 'stop_loss', 'take_profit', 'rsi_at_entry', 'vol_at_entry',
        'strength', 'status', 'exit_date', 'exit_price', 'exit_reason',
        'pnl', 'pnl_pct'
    ]

    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for t in trades:
            row = {col: t.get(col, '') for col in columns}
            writer.writerow(row)

    print(f"  CSV exported: logs/trades_report.csv")