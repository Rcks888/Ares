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

QUEUE_FILE = LOGS_DIR / "signal_queue.json"

def load_queue():
    """Load queued signals from disk."""
    if not QUEUE_FILE.exists():
        return []
    with open(QUEUE_FILE) as f:
        return json.load(f)

def save_queue(queue):
    """Save queued signals to disk."""
    with open(QUEUE_FILE, 'w') as f:
        json.dump(queue, f, indent=2)

def queue_signal(signal):
    """Add a signal to the watchlist queue."""
    queue = load_queue()
    for q in queue:
        if q['symbol'] == signal['symbol']:
            return
    queue.append({
        'symbol': signal['symbol'],
        'strategy': signal['strategy'],
        'trigger': signal.get('trigger', 'unknown'),
        'confluence': signal.get('confluence', 1),
        'price_at_signal': signal['price'],
        'rsi_at_signal': signal['rsi'],
        'date_added': signal['date'],
        'screens': signal.get('screens', [])
    })
    save_queue(queue)
    print(f"    [Queued — slots full] {signal['symbol']} ({signal['strategy']})")

def expire_queue():
    """Remove expired queue entries (older than queue_max_age_days)."""
    params = _load_params()
    max_age = params.get('queue_max_age_days', 5)
    queue = load_queue()
    today = date.today()
    active = []
    for q in queue:
        try:
            added = datetime.strptime(q['date_added'], "%Y-%m-%d").date()
            if (today - added).days <= max_age:
                active.append(q)
        except Exception:
            pass
    if len(active) != len(queue):
        save_queue(active)
    return active

def open_trade(signal, from_queue=False):
    """Record a new virtual trade from a signal. Ares V3."""
    trades = load_trades()
    params = _load_params()

    open_trades = [t for t in trades if t['status'] == 'open']
    max_positions = params.get('max_positions', 5)

    for t in open_trades:
        if t['symbol'] == signal['symbol']:
            return 'duplicate'

    if len(open_trades) >= max_positions:
        queue_signal(signal)
        return 'queued'

    slippage_pct = params.get('slippage_pct', 0.001)
    commission = params.get('commission_per_trade', 1.00)
    cash_reserve_pct = params.get('cash_reserve_pct', 0.25)

    raw_price = signal['price']
    entry_price = raw_price * (1 + slippage_pct)

    portfolio = params.get('starting_capital', 1000)
    available_capital = portfolio * (1 - cash_reserve_pct)
    position_size = (available_capital / max_positions) - commission
    shares = position_size / entry_price
    stop_loss = entry_price - (entry_price * signal['stdev_20'] * 2)

    strategy = signal['strategy']
    if strategy in ('momentum_breakout', 'trend_continuation'):
        tp_pct = params.get('tp_momentum', 0.18)
    else:
        tp_pct = params.get('tp_reversal', 0.10)

    take_profit = entry_price * (1 + tp_pct)
    trade = {
        'symbol': signal['symbol'],
        'strategy': strategy,
        'trigger': signal.get('trigger', 'unknown'),
        'regime': signal.get('regime', 'unknown'),
        'category': signal.get('category', 'unknown'),
        'confluence': signal.get('confluence', 1),
        'signal_date': signal['date'],
        'signal_price': round(raw_price, 2),
        'entry_date': signal['date'] + ' (next-bar)',
        'entry_price': round(entry_price, 2),
        'entry_slippage': round(entry_price - raw_price, 4),
        'entry_commission': commission,
        'shares': round(shares, 2),
        'original_shares': round(shares, 2),
        'position_size': round(position_size, 2),
        'stop_loss': round(stop_loss, 2),
        'take_profit': round(take_profit, 2),
        'trailing_stop': round(stop_loss, 2),
        'peak_price': entry_price,
        'rsi_at_entry': signal['rsi'],
        'vol_at_entry': signal['vol_ratio'],
        'strength': signal['strength'],
        'scaled_out': False,
        'scale_out_price': None,
        'scale_out_date': None,
        'from_queue': from_queue,
        'status': 'open',
        'exit_date': None,
        'exit_price': None,
        'exit_reason': None,
        'exit_slippage': None,
        'exit_commission': None,
        'total_commission': commission,
        'pnl': None,
        'pnl_pct': None,
        'pnl_after_costs': None,
        'version': '3.0'
    }

    trades.append(trade)
    save_trades(trades)
    return 'opened'

def _close_trade(trade, today, exit_price, reason):
    """Helper to close a trade with given reason. Applies slippage and commission."""
    params = _load_params()
    slippage_pct = params.get('slippage_pct', 0.001)
    commission = params.get('commission_per_trade', 1.00)

    raw_exit = exit_price
    exit_price_after_slippage = raw_exit * (1 - slippage_pct)

    trade['status'] = 'closed'
    trade['exit_date'] = today
    trade['exit_price'] = round(exit_price_after_slippage, 2)
    trade['exit_reason'] = reason
    trade['exit_slippage'] = round(raw_exit - exit_price_after_slippage, 4)
    trade['exit_commission'] = commission
    trade['total_commission'] = trade.get('entry_commission', commission) + commission

    entry_date = trade.get('signal_date', trade['entry_date'])
    if ' (next-bar)' in str(entry_date):
        entry_date = entry_date.replace(' (next-bar)', '')
    trade['holding_days'] = _holding_days(entry_date)

    pnl_raw = (exit_price_after_slippage - trade['entry_price']) * trade['shares']
    trade['pnl'] = round(pnl_raw, 2)
    trade['pnl_pct'] = round(
        (exit_price_after_slippage - trade['entry_price'])
        / trade['entry_price'] * 100, 2)
    trade['pnl_after_costs'] = round(pnl_raw - trade['total_commission'], 2)
    trade['shadow'] = {
        'active': True,
        'days_tracked': 0,
        'max_days': 30,
        'peak_after_exit': exit_price,
        'trough_after_exit': exit_price,
        'peak_date': today,
        'trough_date': today,
        'missed_upside_pct': 0,
        'avoided_downside_pct': 0,
        'latest_price': exit_price
    }

def check_open_trades():
    """Check all open trades. Ares V3 exit rules with scale-out."""
    trades = load_trades()
    params = _load_params()
    updated = False
    scale_out_enabled = params.get('scale_out', False)
    scale_out_pct = params.get('scale_out_pct', 0.50)

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

            trailing_pct = params.get('trailing_stop_pct', 0.10)
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

            # Scale-out: sell 50% at TP, let rest ride
            if scale_out_enabled and not trade.get('scaled_out', False):
                if take_profit and current_price >= take_profit:
                    slippage_pct = params.get('slippage_pct', 0.001)
                    scale_commission = params.get('commission_per_trade', 1.00)
                    scale_price = current_price * (1 - slippage_pct)
                    original = trade.get('original_shares', trade['shares'])
                    sell_shares = original * scale_out_pct
                    trade['shares'] = round(trade['shares'] - sell_shares, 2)
                    trade['scaled_out'] = True
                    trade['scale_out_price'] = round(scale_price, 2)
                    trade['scale_out_date'] = today
                    trade['scale_out_commission'] = scale_commission
                    trade['total_commission'] = trade.get('total_commission', 1.0) + scale_commission
                    pnl_pct = (scale_price - trade['entry_price']) / trade['entry_price'] * 100
                    print(f"  📈 {trade['symbol']}: SCALED OUT 50% at ${scale_price:.2f} (+{pnl_pct:.1f}%) [comm: ${scale_commission}]")
                    updated = True
                    continue

            if current_price <= effective_stop:
                exit_price = effective_stop
                reason = 'trailing_stop' if trailing_stop > trade['stop_loss'] else 'stop_loss'
                _close_trade(trade, today, exit_price, reason)
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

        except Exception as e:
            print(f"  Error checking {trade['symbol']}: {e}")

    if updated:
        save_trades(trades)

    # Check queue for entries if slot opened
    open_count = len([t for t in trades if t['status'] == 'open'])
    max_positions = params.get('max_positions', 5)
    if open_count < max_positions:
        queue = expire_queue()
        if queue:
            print(f"\n  QUEUE CHECK: {len(queue)} signals waiting, {max_positions - open_count} slot(s) open")

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

    params = _load_params()
    max_pos = params.get('max_positions', 5)
    queue = load_queue()

    if open_trades:
        print(f"\n  OPEN POSITIONS ({len(open_trades)}/{max_pos} slots):")
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
                scaled = " [50% sold]" if t.get('scaled_out') else ""
                print(f"    {t['symbol']}: entry ${t['entry_price']} -> "
                      f"now ${current:.2f} ({src}) {arrow}{abs(unrealized):.1f}% | "
                      f"Day {days} | SL: ${t['stop_loss']} | TS: ${ts} | TP: ${tp}{scaled}")
            except Exception:
                tp = t.get('take_profit', 'N/A')
                print(f"    {t['symbol']}: entry ${t['entry_price']} | "
                      f"stop: ${t['stop_loss']} | TP: ${tp}")

    if queue:
        print(f"\n  QUEUED SIGNALS ({len(queue)}):")
        for q in queue:
            age = _holding_days(q['date_added'])
            print(f"    {q['symbol']}: {q['strategy']} | confluence {q['confluence']} | "
                  f"${q['price_at_signal']:.2f} | queued {age}d ago")

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

def check_shadow_trades():
    """Track price movement for 30 days after trade closes."""
    trades = load_trades()
    updated = False
    shadow_trades = [t for t in trades if t['status'] == 'closed'
                     and t.get('shadow', {}).get('active', False)]

    if not shadow_trades:
        return

    print(f"\n  SHADOW TRACKING ({len(shadow_trades)} trades):")
    for trade in shadow_trades:
        symbol = trade['symbol']
        shadow = trade['shadow']

        try:
            df = load_stock(symbol)
            current_price = float(df.iloc[-1]['Close'])
            shadow['latest_price'] = round(current_price, 2)
            shadow['days_tracked'] = _holding_days(trade['exit_date'])

            if current_price > shadow['peak_after_exit']:
                shadow['peak_after_exit'] = round(current_price, 2)
                shadow['peak_date'] = str(df.iloc[-1].name)[:10]

            if current_price < shadow['trough_after_exit']:
                shadow['trough_after_exit'] = round(current_price, 2)
                shadow['trough_date'] = str(df.iloc[-1].name)[:10]

            exit_price = trade['exit_price']
            shadow['missed_upside_pct'] = round(
                (shadow['peak_after_exit'] - exit_price) / exit_price * 100, 2)
            shadow['avoided_downside_pct'] = round(
                (exit_price - shadow['trough_after_exit']) / exit_price * 100, 2)

            if shadow['days_tracked'] >= shadow['max_days']:
                shadow['active'] = False
                verdict = "✅ Good exit" if shadow['missed_upside_pct'] < 5 else "⚠️ Left money on table"
                print(f"    {symbol}: SHADOW COMPLETE ({shadow['max_days']}d) | {verdict}")
                print(f"      Peak after exit: ${shadow['peak_after_exit']} (+{shadow['missed_upside_pct']}%)")
                print(f"      Trough after exit: ${shadow['trough_after_exit']} (-{shadow['avoided_downside_pct']}%)")
            else:
                days_left = shadow['max_days'] - shadow['days_tracked']
                print(f"    {symbol}: Day {shadow['days_tracked']}/{shadow['max_days']} | "
                      f"Now ${current_price:.2f} | "
                      f"Peak +{shadow['missed_upside_pct']}% | "
                      f"Trough -{shadow['avoided_downside_pct']}% | "
                      f"{days_left}d left")

            updated = True
        except Exception as e:
            print(f"    {symbol}: Shadow error — {e}")

    if updated:
        save_trades(trades)

def export_csv():
    """Export all trades to CSV for easy viewing in Excel."""
    trades = load_trades()
    if not trades:
        print("  No trades to export.")
        return

    csv_path = LOGS_DIR / "trades_report.csv"
    columns = [
        'symbol', 'strategy', 'trigger', 'regime', 'category', 'confluence',
        'signal_date', 'signal_price', 'entry_date', 'entry_price',
        'entry_slippage', 'entry_commission',
        'shares', 'original_shares', 'position_size',
        'stop_loss', 'trailing_stop', 'take_profit', 'peak_price',
        'rsi_at_entry', 'vol_at_entry', 'strength',
        'scaled_out', 'scale_out_price', 'scale_out_date',
        'from_queue',
        'status', 'exit_date', 'exit_price', 'exit_reason',
        'exit_slippage', 'exit_commission', 'total_commission',
        'holding_days', 'pnl', 'pnl_pct', 'pnl_after_costs', 'version',
        'shadow_days', 'shadow_peak', 'shadow_missed_pct',
        'shadow_trough', 'shadow_avoided_pct', 'shadow_verdict'
    ]

    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for t in trades:
            row = {col: t.get(col, '') for col in columns}
            shadow = t.get('shadow', {})
            if shadow:
                row['shadow_days'] = shadow.get('days_tracked', '')
                row['shadow_peak'] = shadow.get('peak_after_exit', '')
                row['shadow_missed_pct'] = shadow.get('missed_upside_pct', '')
                row['shadow_trough'] = shadow.get('trough_after_exit', '')
                row['shadow_avoided_pct'] = shadow.get('avoided_downside_pct', '')
                missed = shadow.get('missed_upside_pct', 0)
                row['shadow_verdict'] = 'Good exit' if missed < 5 else 'Left money on table'
            writer.writerow(row)

    print(f"  CSV exported: logs/trades_report.csv")