import json
import csv
from datetime import datetime, date
from pathlib import Path
from engine.data_feed import load_stock, get_live_price
from engine.indicators import add_indicators

def _holding_days(entry_date_str):
    """Calculate number of days held from entry date to today."""
    try:
        entry = datetime.strptime(entry_date_str, "%Y-%m-%d").date()
        return (date.today() - entry).days
    except Exception:
        return 0

def _load_params():
    config_path = Path(__file__).parent.parent / "config" / "strategy_params.json"
    with open(config_path) as f:
        return json.load(f)

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
    """Save all virtual trades to disk. Updates holding_days for open trades."""
    for t in trades:
        if t['status'] == 'open':
            t['holding_days'] = _holding_days(t['entry_date'])
    with open(TRADES_FILE, 'w') as f:
        json.dump(trades, f, indent=2)

def open_trade(signal):
    """Record a new virtual trade from a signal. Ares V2."""
    trades = load_trades()
    for t in trades:
        if t['symbol'] == signal['symbol'] and t['status'] == 'open':
            return

    portfolio = 10000
    position_size = portfolio * 0.10
    shares = position_size / signal['price']
    stop_loss = signal['price'] - (signal['price'] * signal['stdev_20'] * 2)

    params = _load_params()
    strategy = signal['strategy']
    if strategy in ('momentum_breakout', 'trend_continuation'):
        tp_pct = params.get('tp_momentum', 0.12)
    else:
        tp_pct = params.get('tp_reversal', 0.08)

    take_profit = signal['price'] * (1 + tp_pct)
    trade = {
        'symbol': signal['symbol'],
        'strategy': strategy,
        'trigger': signal.get('trigger', 'unknown'),
        'regime': signal.get('regime', 'unknown'),
        'category': signal.get('category', 'unknown'),
        'confluence': signal.get('confluence', 1),
        'entry_date': signal['date'],
        'entry_price': signal['price'],
        'shares': round(shares, 2),
        'position_size': round(position_size, 2),
        'stop_loss': round(stop_loss, 2),
        'take_profit': round(take_profit, 2),
        'trailing_stop': round(stop_loss, 2),
        'peak_price': signal['price'],
        'rsi_at_entry': signal['rsi'],
        'vol_at_entry': signal['vol_ratio'],
        'strength': signal['strength'],
        'status': 'open',
        'exit_date': None,
        'exit_price': None,
        'exit_reason': None,
        'pnl': None,
        'pnl_pct': None,
        'version': '2.0'
    }

    trades.append(trade)
    save_trades(trades)

def _close_trade(trade, today, exit_price, reason):
    """Helper to close a trade with given reason."""
    trade['status'] = 'closed'
    trade['exit_date'] = today
    trade['exit_price'] = round(exit_price, 2)
    trade['exit_reason'] = reason
    trade['holding_days'] = _holding_days(trade['entry_date'])
    pnl = (exit_price - trade['entry_price']) * trade['shares']
    trade['pnl'] = round(pnl, 2)
    trade['pnl_pct'] = round(
        (exit_price - trade['entry_price'])
        / trade['entry_price'] * 100, 2)

def check_open_trades():
    """Check all open trades. Ares V2 exit rules."""
    trades = load_trades()
    updated = False

    for trade in trades:
        if trade['status'] != 'open':
            continue

        try:
            df = load_stock(trade['symbol'])
            df = add_indicators(df)
            latest = df.iloc[-1]

            live = get_live_price(trade['symbol'])
            daily_price = float(latest['Close'])
            current_price = live if live else daily_price
            price_source = "IBKR" if live else "daily"
            current_rsi = float(latest['rsi'])
            today = str(latest.name)[:10]
            if today == trade['entry_date']:
                continue

            strategy = trade['strategy']
            take_profit = trade.get('take_profit')
            trailing_stop = trade.get('trailing_stop', trade['stop_loss'])
            peak_price = trade.get('peak_price', trade['entry_price'])

            params = _load_params()
            trailing_pct = params.get('trailing_stop_pct', 0.08)
            rsi_extreme = params.get('rsi_extreme_high', 90)

            if current_price > peak_price:
                peak_price = current_price
                trade['peak_price'] = round(peak_price, 2)
                new_trailing = peak_price * (1 - trailing_pct)
                if new_trailing > trailing_stop:
                    trailing_stop = new_trailing
                    trade['trailing_stop'] = round(trailing_stop, 2)
                updated = True

            effective_stop = max(trade['stop_loss'], trailing_stop)

            if current_price <= effective_stop:
                exit_price = effective_stop
                reason = 'trailing_stop' if trailing_stop > trade['stop_loss'] else 'stop_loss'
                _close_trade(trade, today, exit_price, reason)
                updated = True

            elif take_profit and current_price >= take_profit:
                _close_trade(trade, today, take_profit, 'take_profit')
                updated = True

            elif current_rsi > rsi_extreme:
                _close_trade(trade, today, current_price, 'emotional_extreme')
                updated = True

            elif bool(latest.get('bearish_div', False)):
                if strategy in ('momentum_breakout', 'trend_continuation'):
                    _close_trade(trade, today, current_price, 'bearish_divergence')
                    updated = True

            elif strategy == 'mean_reversion' and current_rsi > 70:
                _close_trade(trade, today, current_price, 'mean_reversion_complete')
                updated = True

            # V1 backward compatibility
            elif strategy == 'rsi_reversal' and current_rsi > 50:
                _close_trade(trade, today, current_price, 'target_reached')
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
                live = get_live_price(t['symbol'])
                df = load_stock(t['symbol'])
                daily = float(df.iloc[-1]['Close'])
                current = live if live else daily
                src = "live" if live else "daily"
                unrealized = (current - t['entry_price']) / t['entry_price'] * 100
                arrow = "+" if unrealized > 0 else "-"
                tp = t.get('take_profit', 'N/A')
                ts = t.get('trailing_stop', t['stop_loss'])
                days = _holding_days(t['entry_date'])
                print(f"    {t['symbol']}: entry ${t['entry_price']} -> "
                      f"now ${current:.2f} ({src}) {arrow}{abs(unrealized):.1f}% | "
                      f"Day {days} | SL: ${t['stop_loss']} | TS: ${ts} | TP: ${tp}")
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
        hold_days = [t.get('holding_days', 0) for t in closed if t.get('holding_days')]
        if hold_days:
            print(f"    Avg hold:   {sum(hold_days)/len(hold_days):.0f} days")

        print(f"\n  RECENT TRADES:")
        for t in closed[-5:]:
            icon = "W" if t['pnl'] > 0 else "L"
            days = t.get('holding_days', '?')
            print(f"    [{icon}] {t['symbol']} | "
                  f"{t['entry_date']} -> {t['exit_date']} ({days}d) | "
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
        'symbol', 'strategy', 'trigger', 'regime', 'category', 'confluence',
        'entry_date', 'entry_price', 'shares', 'position_size',
        'stop_loss', 'trailing_stop', 'take_profit', 'peak_price',
        'rsi_at_entry', 'vol_at_entry', 'strength',
        'status', 'exit_date', 'exit_price', 'exit_reason',
        'holding_days', 'pnl', 'pnl_pct', 'version'
    ]

    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for t in trades:
            row = {col: t.get(col, '') for col in columns}
            writer.writerow(row)

    print(f"  CSV exported: logs/trades_report.csv")