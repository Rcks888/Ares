import json
import csv
from datetime import datetime, date
from pathlib import Path
from engine.data_feed import load_stock, get_live_price
from engine.indicators import add_indicators
from engine import sample

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
PENDING_FILE = LOGS_DIR / "pending_signals.json"

def load_pending():
    """Load pending signals (awaiting next-bar execution)."""
    if not PENDING_FILE.exists():
        return []
    with open(PENDING_FILE) as f:
        return json.load(f)

def save_pending(pending):
    """Save pending signals to disk."""
    with open(PENDING_FILE, 'w') as f:
        json.dump(pending, f, indent=2)

MAX_FILL_ATTEMPTS = 3
MAX_CHECK_FAILURES = 3

def _pending_age_days(sig):
    """Calendar age of a pending signal, from its signal date."""
    try:
        return (date.today() - date.fromisoformat(sig['date'])).days
    except Exception:
        return 0

def execute_pending_signals():
    """Execute pending signals using today's open price.

    Called AFTER refresh_watchlist() so the fill reads a current bar.
    A pending is only discarded on a logged, explicit reason — never silently.
    """
    pending = load_pending()
    if not pending:
        return

    params = _load_params()
    slippage_pct = params.get('slippage_pct', 0.001)
    commission = params.get('commission_per_trade', 1.00)
    cash_reserve_pct = params.get('cash_reserve_pct', 0.25)
    max_positions = params.get('max_positions', 5)

    trades = load_trades()
    open_count = len([t for t in trades if t['status'] == 'open'])
    executed = []

    print(f"\n  PENDING SIGNALS: {len(pending)} awaiting execution")

    for sig in pending:
        if open_count >= max_positions:
            print(f"    {sig['symbol']}: Slots full — moving to queue")
            queue_signal(sig)
            executed.append(sig)
            continue

        if any(t['symbol'] == sig['symbol'] and t['status'] == 'open' for t in trades):
            print(f"    {sig['symbol']}: Already open — skipping")
            executed.append(sig)
            continue

        max_age_d = params.get('pending_max_age_days', 4)
        if _pending_age_days(sig) > max_age_d:
            print(f"    ✗ {sig['symbol']}: pending {_pending_age_days(sig)}d old "
                  f"(max {max_age_d}d) — dropped")
            _log_queue_event(sig['symbol'], 'fill_dropped',
                             reason=f"pending aged out ({_pending_age_days(sig)}d)")
            executed.append(sig)
            continue

        try:
            # max_age_hours=0 forces a fresh pull: a pending symbol may have
            # dropped out of the screener and so missed refresh_watchlist().
            df = load_stock(sig['symbol'], max_age_hours=0)
            if df is None or len(df) < 2:
                sig['fill_attempts'] = sig.get('fill_attempts', 0) + 1
                if sig['fill_attempts'] >= MAX_FILL_ATTEMPTS:
                    print(f"    ✗ {sig['symbol']}: no data after "
                          f"{sig['fill_attempts']} attempts — dropped")
                    _log_queue_event(sig['symbol'], 'fill_dropped',
                                     reason=f"no data after {sig['fill_attempts']} attempts")
                    executed.append(sig)
                else:
                    print(f"    ⏳ {sig['symbol']}: no data "
                          f"(attempt {sig['fill_attempts']}/{MAX_FILL_ATTEMPTS}) — retained")
                    _log_queue_event(sig['symbol'], 'fill_retry', reason="no data returned")
                continue

            bar_date = df.index[-1].date()
            today = datetime.utcnow().date()
            if bar_date != today:
                print(f"    ⏳ {sig['symbol']}: last bar {bar_date} != today {today} "
                      f"— no session yet, retained")
                _log_queue_event(sig['symbol'], 'fill_retry',
                                 reason=f"last bar {bar_date} != today {today}")
                continue

            today_open = float(df.iloc[-1]['Open'])
            entry_price = today_open * (1 + slippage_pct)

            portfolio = params.get('starting_capital', 1000)
            available_capital = portfolio * (1 - cash_reserve_pct)
            position_size = (available_capital / max_positions) - commission
            shares = position_size / entry_price

            # stdev_20 is a FRACTION of price, not a price. The old default of
            # entry_price*0.05 produced a negative stop_loss when absent.
            #
            # A missing stdev_20 is an absence, not a 5% volatility stock.
            # Substituting one silently yields a 10% stop where the real figure
            # would have given 4-5%, roughly doubling risk with no complaint
            # raised anywhere. The fallback still applies so a fill is never
            # lost over it, but the trade is now labelled so it cannot enter
            # the clean sample carrying a fabricated stop distance.
            stdev_20 = sig.get('stdev_20')
            stdev_missing = not stdev_20 or stdev_20 <= 0
            if stdev_missing:
                print(f"    ! {sig['symbol']}: stdev_20 absent — using 0.05 "
                      f"fallback, trade marked contaminated")
                stdev_20 = 0.05
            sl_mult = params.get('stop_loss_multiplier', 2.0)
            stop_loss = entry_price - (entry_price * stdev_20 * sl_mult)

            strategy = sig['strategy']
            if strategy in ('momentum_breakout', 'trend_continuation'):
                tp_pct = params.get('tp_momentum', 0.18)
            else:
                tp_pct = params.get('tp_reversal', 0.10)

            take_profit = entry_price * (1 + tp_pct)
            today_str = datetime.now().strftime("%Y-%m-%d")

            # Label the fill at the moment it happens. A manual off-schedule
            # run reads whatever state the schedule has not yet refreshed, so
            # it is marked contaminated on the spot rather than reconstructed
            # from timestamps later, which the audit showed is not possible.
            fill_source, fill_detect = sample.fill_context()
            fill_reasons = [] if fill_source == 'scheduled' else ['manual_fill']
            if stdev_missing:
                fill_reasons.append('stdev_fallback')
            phase = sample.PRE_PHASE if fill_reasons else sample.CLEAN_PHASE

            trade = {
                'symbol': sig['symbol'],
                'strategy': strategy,
                'trigger': sig.get('trigger', 'unknown'),
                'regime': sig.get('regime', 'unknown'),
                'category': sig.get('category', 'unknown'),
                'confluence': sig.get('confluence', 1),
                'signal_date': sig['date'],
                'signal_price': round(sig['price'], 2),
                'entry_date': today_str,
                'entry_price': round(entry_price, 2),
                'entry_slippage': round(entry_price - today_open, 4),
                'entry_commission': commission,
                'shares': round(shares, 2),
                'original_shares': round(shares, 2),
                'position_size': round(position_size, 2),
                'stop_loss': round(stop_loss, 2),
                'take_profit': round(take_profit, 2),
                'trailing_stop': round(stop_loss, 2),
                'peak_price': entry_price,
                'rsi_at_entry': sig.get('rsi', 0),
                'vol_at_entry': sig.get('vol_ratio', 0),
                'strength': sig.get('strength', 'unknown'),
                'scaled_out': False,
                'scale_out_price': None,
                'scale_out_date': None,
                'from_queue': sig.get('from_queue', False),
                'sample_phase': phase,
                'contaminated': bool(fill_reasons),
                'contamination_reasons': fill_reasons,
                'fill_source': fill_source,
                'fill_detect': fill_detect,
                'fill_run_ts': datetime.now().isoformat(timespec='seconds'),
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
            open_count += 1
            print(f"    ✅ {sig['symbol']}: EXECUTED at ${entry_price:.2f} (open) | "
                  f"Signal was ${sig['price']:.2f} (close) | "
                  f"Diff: {((entry_price - sig['price'])/sig['price']*100):+.2f}%")
            executed.append(sig)
        except Exception as e:
            sig['fill_attempts'] = sig.get('fill_attempts', 0) + 1
            if sig['fill_attempts'] >= MAX_FILL_ATTEMPTS:
                print(f"    ✗ {sig['symbol']}: fill failed "
                      f"{sig['fill_attempts']}x — dropped ({e})")
                _log_queue_event(sig['symbol'], 'fill_dropped',
                                 reason=f"exception after {sig['fill_attempts']} attempts: {e}")
                executed.append(sig)
            else:
                print(f"    ⏳ {sig['symbol']}: fill error "
                      f"(attempt {sig['fill_attempts']}/{MAX_FILL_ATTEMPTS}) — retained: {e}")
                _log_queue_event(sig['symbol'], 'fill_retry', reason=str(e))

    for sig in executed:
        pending.remove(sig)

    save_pending(pending)
    save_trades(trades)

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
RANKED_FILE = LOGS_DIR / "queue_ranked.json"
QUEUE_EVENTS_FILE = LOGS_DIR / "queue_events.jsonl"

def _log_queue_event(symbol, action, q=None, checks=None, reason=None):
    """Append-only audit log of every queue event.
    Actions: queued | kept | dropped | promoted | expired | evicted
    """
    event = {
        'timestamp': datetime.now().isoformat(timespec='seconds'),
        'symbol': symbol,
        'action': action,
        'queued_at': (q or {}).get('date_added'),
        'confluence': (q or {}).get('confluence'),
        'signal_price': (q or {}).get('price_at_signal'),
        'drift_pct': (checks or {}).get('drift_pct'),
        'rsi': (checks or {}).get('rsi'),
        'ema20_ok': (checks or {}).get('ema20_ok'),
        'live_price': (checks or {}).get('live'),
        'drop_reason': reason
    }
    try:
        with open(QUEUE_EVENTS_FILE, 'a') as f:
            f.write(json.dumps(event) + '\n')
    except Exception:
        pass

def load_ranked():
    """Load the last ranked queue snapshot written by maintain_queue()."""
    if not RANKED_FILE.exists():
        return []
    try:
        with open(RANKED_FILE) as f:
            return json.load(f)
    except Exception:
        return []

def save_ranked(ranked):
    with open(RANKED_FILE, 'w') as f:
        json.dump(ranked, f, indent=2)

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
    """Add a signal to the watchlist queue. Enforces queue_max_size with eviction."""
    params = _load_params()
    max_size = params.get('queue_max_size', 10)
    queue = load_queue()

    for q in queue:
        if q['symbol'] == signal['symbol']:
            return

    entry = {
        'symbol': signal['symbol'],
        'strategy': signal['strategy'],
        'trigger': signal.get('trigger', 'unknown'),
        'regime': signal.get('regime', 'unknown'),
        'category': signal.get('category', 'unknown'),
        'confluence': signal.get('confluence', 1),
        'price_at_signal': signal['price'],
        'rsi_at_signal': signal['rsi'],
        'stdev_20': signal.get('stdev_20'),
        'vol_ratio': signal.get('vol_ratio'),
        'date_added': signal['date'],
        'screens': signal.get('screens', [])
    }
    queue.append(entry)
    _log_queue_event(signal['symbol'], 'queued', entry)

    if len(queue) > max_size:
        # Keep higher confluence, then newer age. Evict the tail.
        queue.sort(key=lambda q: (-q.get('confluence', 1), q.get('date_added', '')), reverse=False)
        keep, evicted = queue[:max_size], queue[max_size:]
        for e in evicted:
            print(f"    [Evicted — queue full] {e['symbol']} (conf{e.get('confluence')}, added {e.get('date_added')})")
            _log_queue_event(e['symbol'], 'evicted', e,
                             reason=f"queue exceeded max_size {max_size}")
        queue = keep

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
            age = (today - added).days
            if age <= max_age:
                active.append(q)
            else:
                print(f"    [Expired] {q['symbol']} — {age}d old (max {max_age}d)")
                _log_queue_event(q['symbol'], 'expired', q,
                                 reason=f"age {age}d exceeded max {max_age}d")
        except Exception:
            pass
    if len(active) != len(queue):
        save_queue(active)
    return active

def _validate_queued(symbol, q, params, use_live=False):
    """Validate a queued signal against current data.
    Returns (valid, reason, checks_dict).
    use_live=True fetches an intraday price via IBKR instead of last daily close.
    """
    checks = {'drift_pct': None, 'rsi': None, 'ema20_ok': None, 'live': None}
    try:
        # 6h window: queued symbols may have left the screener and so miss
        # refresh_watchlist(). Scan gaps are 7.5h/16.5h, so 6h guarantees a
        # fresh pull each scan while letting maintain_queue and promote_queue
        # share one download within the same run.
        df = load_stock(symbol, max_age_hours=6)
        if df is None or df.empty:
            return False, "transient: no data", checks
        df = add_indicators(df)
        latest = df.iloc[-1]

        price = None
        if use_live:
            price = get_live_price(symbol)
        if price is None:
            price = float(latest['Close'])
        checks['live'] = round(price, 2)

        signal_price = q.get('price_at_signal') or price
        drift = (price - signal_price) / signal_price * 100 if signal_price else 0
        checks['drift_pct'] = round(drift, 2)

        rsi = float(latest.get('RSI', 50))
        checks['rsi'] = round(rsi, 1)

        ema20 = float(latest.get('EMA_20', 0))
        checks['ema20_ok'] = price >= ema20

        max_drift = params.get('queue_max_drift_pct', 5.0)
        if abs(drift) > max_drift:
            return False, f"drifted {drift:+.1f}% (max ±{max_drift}%)", checks
        if rsi > params.get('rsi_extreme_high', 90):
            return False, f"RSI {rsi:.1f} overbought", checks
        if not checks['ema20_ok']:
            return False, f"price ${price:.2f} below EMA20 ${ema20:.2f}", checks

        return True, "valid", checks
    except Exception as e:
        return False, f"transient: validation error: {e}", checks

def maintain_queue():
    """MAINTENANCE PASS — runs on every scan, regardless of slot availability.
    Validates all queued signals, drops stale ones with logged reason,
    re-ranks survivors on current data, persists to logs/queue_ranked.json.
    Does NOT promote. Promotion is a separate pass.
    """
    queue = expire_queue()
    if not queue:
        save_ranked([])
        return []

    params = _load_params()
    survivors, unverified = [], []

    print(f"  [Queue maintenance] validating {len(queue)} signal(s)")

    for q in queue:
        symbol = q['symbol']
        valid, reason, checks = _validate_queued(symbol, q, params)
        if valid:
            entry = dict(q)
            entry['checked_at'] = datetime.now().isoformat(timespec='seconds')
            entry['drift_pct'] = checks['drift_pct']
            entry['rsi'] = checks['rsi']
            entry['ema20_ok'] = checks['ema20_ok']
            entry['live_price'] = checks['live']
            entry.pop('check_failures', None)
            survivors.append(entry)
            _log_queue_event(symbol, 'kept', q, checks)
        elif reason.startswith('transient:'):
            # Could not evaluate — NOT the same as invalidated. Retain the
            # signal but keep it out of the ranked list so it is unpromotable
            # until a real check succeeds.
            entry = dict(q)
            entry['check_failures'] = q.get('check_failures', 0) + 1
            if entry['check_failures'] >= MAX_CHECK_FAILURES:
                print(f"    ✗ {symbol}: {reason} x{entry['check_failures']} — dropped")
                _log_queue_event(symbol, 'dropped', q, checks,
                                 f"{reason} after {entry['check_failures']} attempts")
            else:
                print(f"    ⏳ {symbol}: {reason} "
                      f"({entry['check_failures']}/{MAX_CHECK_FAILURES}) — retained, unranked")
                _log_queue_event(symbol, 'check_retry', q, checks, reason)
                unverified.append(entry)
        else:
            print(f"    ✗ {symbol}: {reason} — dropped")
            _log_queue_event(symbol, 'dropped', q, checks, reason)

    # Rank: confluence DESC, absolute drift ASC, age ASC
    survivors.sort(key=lambda q: (
        -q.get('confluence', 1),
        abs(q.get('drift_pct') or 0),
        q.get('date_added', '')
    ))

    # Queue keeps survivors AND unverified entries; only survivors are ranked,
    # so an unverified signal can never be promoted without a passing check.
    save_queue([{k: v for k, v in q.items()
                 if k not in ('checked_at', 'drift_pct', 'rsi', 'ema20_ok', 'live_price')}
                for q in survivors + unverified])
    save_ranked(survivors)

    if unverified:
        print(f"    {len(unverified)} unverified (retained, not promotable)")

    if survivors:
        top = survivors[0]
        print(f"    Ranked {len(survivors)}: top = {top['symbol']} "
              f"(conf{top.get('confluence')}, drift {top.get('drift_pct'):+.1f}%)")
    return survivors

def _fill_window_ok(params):
    """Weekend / stale-pending guard.
    A promotion creates a pending order filling at the next US regular session.
    Reject if that fill would sit across a long gap on stale validation.
    Server clock is UTC; US regular session is Mon-Fri.
    """
    from datetime import timedelta
    max_gap_h = params.get('pending_max_gap_hours', 48)
    now = datetime.utcnow()

    US_OPEN_H, US_OPEN_M = 13, 30   # 09:30 ET in UTC (EDT)
    US_CLOSE_H = 20                 # 16:00 ET in UTC (EDT)

    # Next regular-session open strictly after now, skipping weekends
    candidate = now.replace(hour=US_OPEN_H, minute=US_OPEN_M, second=0, microsecond=0)
    if now >= candidate.replace(hour=US_CLOSE_H, minute=0):
        candidate += timedelta(days=1)
    elif now >= candidate:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)

    gap_h = round((candidate - now).total_seconds() / 3600, 1)
    if gap_h > max_gap_h:
        return False, (f"next fill {candidate:%a %H:%M} UTC is {gap_h}h away "
                       f"(max {max_gap_h}h) — leaving in queue")
    return True, f"next fill {candidate:%a %H:%M} UTC in {gap_h}h"

def promote_queue(source="scan", use_live=False):
    """PROMOTION PASS — runs on scans and monitors.
    If slots are free, promote the top-ranked valid signal to pending.
    Always re-validates at execution time; never trusts a stale ranking.
    """
    params = _load_params()
    trades = load_trades()
    open_trades = [t for t in trades if t['status'] == 'open']
    pending = load_pending()
    max_positions = params.get('max_positions', 5)
    free_slots = max_positions - len(open_trades) - len(pending)

    if free_slots <= 0:
        return

    ranked = load_ranked()
    if not ranked:
        ranked = load_queue()
    if not ranked:
        return

    ok, why = _fill_window_ok(params)
    if not ok:
        print(f"  [Queue promotion] skipped — {why}")
        return

    print(f"  [Queue promotion / {source}] {free_slots} slot(s) free, {len(ranked)} ranked")

    held = {t['symbol'] for t in open_trades} | {p['symbol'] for p in pending}
    promoted, dropped = [], []

    for q in ranked:
        if free_slots <= 0:
            break
        symbol = q['symbol']
        if symbol in held:
            print(f"    - {symbol}: already held or pending — removed from queue")
            _log_queue_event(symbol, 'dropped', q, None, "already held or pending")
            dropped.append(symbol)
            continue

        # Mandatory execution-time re-validation
        valid, reason, checks = _validate_queued(symbol, q, params, use_live=use_live)
        if not valid:
            if reason.startswith('transient:'):
                print(f"    ⏳ {symbol}: {reason} — retained, promotion deferred")
                _log_queue_event(symbol, 'check_retry', q, checks,
                                 f"promotion-time: {reason}")
                continue
            print(f"    ✗ {symbol}: {reason} — dropped at promotion")
            _log_queue_event(symbol, 'dropped', q, checks, f"promotion-time: {reason}")
            dropped.append(symbol)
            continue

        signal = {
            'symbol': symbol,
            'strategy': q['strategy'],
            'trigger': q.get('trigger', 'unknown'),
            'regime': q.get('regime', 'unknown'),
            'category': q.get('category', 'unknown'),
            'confluence': q.get('confluence', 1),
            'date': date.today().isoformat(),
            'price': checks['live'],
            'rsi': q.get('rsi_at_signal'),
            'stdev_20': q.get('stdev_20'),
            'vol_ratio': q.get('vol_ratio'),
            'screens': q.get('screens', [])
        }
        result = open_trade(signal, from_queue=True)
        if result in ('pending', 'opened'):
            print(f"    ✓ {symbol}: promoted (conf{q.get('confluence')}, "
                  f"drift {checks['drift_pct']:+.1f}%) → {result}")
            _log_queue_event(symbol, 'promoted', q, checks)
            promoted.append(symbol)
            free_slots -= 1
        else:
            print(f"    ✗ {symbol}: open_trade returned '{result}'")
            _log_queue_event(symbol, 'dropped', q, checks, f"open_trade={result}")
            dropped.append(symbol)

    removed = set(promoted) | set(dropped)
    if removed:
        save_queue([q for q in load_queue() if q['symbol'] not in removed])
        save_ranked([q for q in ranked if q['symbol'] not in removed])

def open_trade(signal, from_queue=False):
    """Record a new virtual trade from a signal. Ares V3."""
    trades = load_trades()
    params = _load_params()

    open_trades = [t for t in trades if t['status'] == 'open']
    pending = load_pending()
    max_positions = params.get('max_positions', 5)

    for t in open_trades:
        if t['symbol'] == signal['symbol']:
            return 'duplicate'

    if len(open_trades) + len(pending) >= max_positions:
        queue_signal(signal)
        return 'queued'

    # Save as pending — will execute at next day's open price
    pending = load_pending()
    for p in pending:
        if p['symbol'] == signal['symbol']:
            return 'duplicate'

    pending.append({
        'symbol': signal['symbol'],
        'strategy': signal['strategy'],
        'trigger': signal.get('trigger', 'unknown'),
        'regime': signal.get('regime', 'unknown'),
        'category': signal.get('category', 'unknown'),
        'confluence': signal.get('confluence', 1),
        'date': signal['date'],
        'price': signal['price'],
        'rsi': signal.get('rsi', 0),
        'vol_ratio': signal.get('vol_ratio', 0),
        'stdev_20': signal.get('stdev_20', 0.05),
        'strength': signal.get('strength', 'unknown'),
        'screens': signal.get('screens', []),
        'from_queue': from_queue
    })
    save_pending(pending)
    return 'pending'

def _build_post_mortem(trade, reason):
    """Analyze why a trade ended the way it did. Auto-generated diagnostics."""
    entry = trade['entry_price']
    signal_price = trade.get('signal_price', entry)
    exit_price = trade.get('exit_price', entry)
    peak = trade.get('peak_price', entry)
    tp = trade.get('take_profit')
    sl = trade.get('stop_loss')
    scaled = trade.get('scaled_out', False)

    mfe_pct = round((peak - entry) / entry * 100, 2)
    entry_quality = round((signal_price - entry) / signal_price * 100, 2)

    pm = {
        'exit_reason': reason,
        'max_favorable_excursion_pct': mfe_pct,
        'entry_quality_pct': entry_quality,
        'scaled_out_before_exit': scaled,
        'holding_days': trade.get('holding_days', 0),
        'rsi_at_entry': trade.get('rsi_at_entry'),
        'regime_at_entry': trade.get('regime'),
        'confluence': trade.get('confluence'),
        'verdict': None,
        'analysis': None,
        'manual_note': ''
    }

    if reason == 'take_profit':
        pm['verdict'] = 'as_expected'
        pm['analysis'] = f"TP hit at ${tp}. Peak reached +{mfe_pct}%. Strategy worked as designed."
    elif reason == 'trailing_stop':
        if mfe_pct > 0:
            locked = round((exit_price - entry) / entry * 100, 2)
            gave_back = round(mfe_pct - locked, 2)
            pm['verdict'] = 'partial_win' if locked > 0 else 'reversal'
            pm['analysis'] = (f"Peaked +{mfe_pct}%, exited at {locked:+.2f}%. "
                              f"Gave back {gave_back}% from peak. "
                              f"{'Trailing stop protected profit.' if locked > 0 else 'Reversed before locking gains.'}")
        else:
            pm['verdict'] = 'immediate_reversal'
            pm['analysis'] = "Never moved favorably. Signal failed immediately."
    elif reason == 'stop_loss':
        if mfe_pct <= 0:
            pm['verdict'] = 'signal_failed'
            pm['analysis'] = (f"Never traded above entry. Signal was wrong from the start. "
                              f"RSI {trade.get('rsi_at_entry')}, regime {trade.get('regime')}, "
                              f"confluence {trade.get('confluence')}.")
        elif mfe_pct < 3:
            pm['verdict'] = 'weak_follow_through'
            pm['analysis'] = (f"Only reached +{mfe_pct}% before reversing to SL. "
                              f"Weak momentum — entry may have been too late.")
        else:
            distance_to_tp = round((tp - peak) / peak * 100, 2) if tp else None
            pm['verdict'] = 'reversal_after_gain'
            pm['analysis'] = (f"Peaked +{mfe_pct}% ({distance_to_tp}% short of TP) "
                              f"then reversed to SL. Consider tighter trailing stop or partial exit earlier.")
    elif reason == 'rsi_extreme':
        pm['verdict'] = 'emotional_exit'
        pm['analysis'] = f"RSI hit extreme (>90). Exited to avoid blow-off top. Peak +{mfe_pct}%."
    else:
        pm['verdict'] = 'other'
        pm['analysis'] = f"Closed via {reason}. Peak +{mfe_pct}%."

    return pm

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
    # Accumulate, never overwrite: overwriting dropped the scale-out commission.
    prior = trade.get('total_commission', trade.get('entry_commission', commission))
    trade['total_commission'] = round(prior + commission, 2)

    entry_date = trade.get('signal_date', trade['entry_date'])
    if ' (next-bar)' in str(entry_date):
        entry_date = entry_date.replace(' (next-bar)', '')
    trade['holding_days'] = _holding_days(entry_date)

    # Total P&L spans the whole original position: the tranche sold at the
    # scale-out plus whatever remains at exit. trade['shares'] holds only the
    # remainder, so using it alone understated every scaled-out winner.
    remaining_pnl = (exit_price_after_slippage - trade['entry_price']) * trade['shares']
    scale_pnl = trade.get('scale_out_pnl') or 0
    pnl_raw = remaining_pnl + scale_pnl
    trade['pnl'] = round(pnl_raw, 2)
    trade['pnl_remaining'] = round(remaining_pnl, 2)

    # Percentage is blended over the original cost basis so % and $ agree.
    # For a trade that never scaled out this is identical to the old formula.
    original = trade.get('original_shares') or trade['shares']
    cost_basis = trade['entry_price'] * original
    trade['pnl_pct'] = round(pnl_raw / cost_basis * 100, 2) if cost_basis else 0.0
    trade['pnl_after_costs'] = round(pnl_raw - trade['total_commission'], 2)
    trade['post_mortem'] = _build_post_mortem(trade, reason)
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
                    # Book the realised gain on the sold tranche. Previously this
                    # was computed into a local, printed, and discarded — so the
                    # locked-in profit never reached pnl at close.
                    trade['scale_out_shares'] = round(sell_shares, 2)
                    trade['scale_out_pnl'] = round(
                        (scale_price - trade['entry_price']) * sell_shares, 2)
                    pnl_pct = (scale_price - trade['entry_price']) / trade['entry_price'] * 100
                    trade['scale_out_pnl_pct'] = round(pnl_pct, 2)
                    print(f"  📈 {trade['symbol']}: SCALED OUT 50% at ${scale_price:.2f} "
                          f"(+{pnl_pct:.1f}%, +${trade['scale_out_pnl']:.2f} locked) "
                          f"[comm: ${scale_commission}]")
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
        # Official figures come from the clean sample only. All-history is
        # shown underneath for continuity, clearly marked as not an edge
        # measurement, so the two can never be read as the same number.
        clean_closed = sample.clean(closed)
        excl_closed = sample.excluded(closed)
        m = sample.metrics(clean_closed)

        print(f"\n  OFFICIAL — CLEAN SAMPLE ({sample.CLEAN_PHASE})")
        if m is None:
            print(f"    No clean closed trades yet.")
            print(f"    Edge measurement begins with the first scheduled fill")
            print(f"    on or after {sample.CLEAN_FROM.isoformat()}.")
        else:
            print(f"    Closed: {m['n']}   W/L: {m['wins']}/{m['losses']}   "
                  f"Win rate: {m['win_rate']:.0f}%")
            print(f"    Realized: ${m['realized']:+.2f}   "
                  f"Expectancy: ${m['expectancy']:+.2f}/trade")
            if m['avg_win'] is not None:
                print(f"    Avg win: ${m['avg_win']:+.2f}", end="")
                if m['avg_loss'] is not None:
                    print(f"   Avg loss: ${-m['avg_loss']:+.2f}", end="")
                print()
            pf = m['profit_factor']
            print(f"    Profit factor: "
                  f"{f'{pf:.2f}' if pf is not None else 'n/a (no losses yet)'}")
            target = 40
            print(f"    Progress to edge assessment: {m['n']}/{target} trades")

        if excl_closed:
            em = sample.metrics(excl_closed)
            print(f"\n  Pre-clean history ({len(excl_closed)} closed) — "
                  f"process validation only, NOT an edge measurement")
            print(f"    Realized: ${em['realized']:+.2f}   "
                  f"W/L: {em['wins']}/{em['losses']}")
            tally = {}
            for t in excl_closed:
                for r in t.get('contamination_reasons') or ['unlabelled']:
                    tally[r] = tally.get(r, 0) + 1
            print(f"    Excluded for: "
                  + ", ".join(f"{k} x{v}" for k, v in sorted(tally.items())))

        # Retained for the lines below, which report combined history.
        wins = [t for t in closed if sample.net_pnl(t) > 0]
        losses = [t for t in closed if sample.net_pnl(t) <= 0]
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