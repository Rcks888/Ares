"""Which trades are allowed to count toward edge measurement.

Weeks 1-3 were retained as process-validation history. Official edge
measurement begins from the first clean scheduled fill after the Sep 18-21
fixes. Contaminated trades stay labelled and excluded from edge/ML metrics.

Kept in one module on purpose. The scorecard, the dashboard, the CSV export and
any future ML training must all agree on what "clean" means, and three copies of
the predicate would eventually disagree without anyone noticing.

A trade counts as clean only when all four hold:
  1. Filled by the scheduled production path, not a manual off-schedule run
  2. Entered after the Sep 18-21 fixes
  3. Not marked contaminated
  4. Uses current accounting end to end
"""
import os
import sys
from datetime import date

# First date on which the production path was correct end to end: fill
# ordering, queue validation, scale-out P&L booking, commission accumulation.
#
# Deliberately the day AFTER the fixes landed. A trade whose entry_date is the
# fix day itself cannot be proven to have filled after deployment, because the
# record stores a date and not a time. Unprovable is treated as contaminated.
CLEAN_FROM = date(2026, 9, 19)

CLEAN_PHASE = "clean_v3"
PRE_PHASE = "pre_clean"

# The queue path failed to carry stdev_20, so open_trade fell back to 0.05.
# With stop_loss_multiplier 2.0 that lands the stop at exactly 10.00% below
# entry -- a value real volatility virtually never produces, which makes the
# bug detectable after the fact rather than merely suspected.
FALLBACK_STOP_FRAC = 0.10
FALLBACK_TOL = 0.0008

STALE_ENTRY_TOL_PCT = 0.15


def fill_context():
    """How this process was started: ('scheduled'|'manual', detection method).

    ARES_SCHEDULED is authoritative when set. Otherwise fall back to whether
    stdin is a TTY: cron has none, an interactive shell does. The method is
    recorded alongside the verdict so an inferred classification is never
    mistaken for a verified one.
    """
    env = os.environ.get('ARES_SCHEDULED')
    if env is not None:
        return ('scheduled' if env.strip() == '1' else 'manual'), 'env'
    try:
        interactive = sys.stdin.isatty()
    except Exception:
        interactive = False
    return ('manual' if interactive else 'scheduled'), 'tty_inferred'


def entry_date(trade):
    """Parse entry_date, tolerating the ' (next-bar)' suffix. None if unusable."""
    raw = str(trade.get('entry_date') or '').replace(' (next-bar)', '')
    try:
        return date.fromisoformat(raw[:10])
    except (ValueError, TypeError):
        return None


def stop_frac(trade):
    """Stop distance as a fraction of entry, or None if not derivable."""
    entry = trade.get('entry_price_original') or trade.get('entry_price')
    sl = trade.get('stop_loss')
    if not entry or sl is None:
        return None
    return (entry - sl) / entry


def used_fallback_stdev(trade):
    """True when the stop distance carries the stdev_20 fallback signature."""
    if not trade.get('from_queue'):
        return False
    frac = stop_frac(trade)
    if frac is None:
        return False
    return abs(frac - FALLBACK_STOP_FRAC) <= FALLBACK_TOL


def classify(trade, stale_entry=None):
    """Return (sample_phase, contaminated, reasons). Never mutates the trade.

    stale_entry may be passed by a caller that has fetched the true open. When
    None, a previously recorded entry_error_pct or entry_corrected flag is used
    instead, so re-running never loses an earlier finding.
    """
    reasons = []
    ed = entry_date(trade)

    if stale_entry is None:
        err = trade.get('entry_error_pct')
        stale_entry = bool(trade.get('entry_corrected')) or (
            err is not None and abs(err) > STALE_ENTRY_TOL_PCT)
    if stale_entry:
        reasons.append('stale_entry')

    # Every stale fill in the audit came from a manual off-schedule run, and
    # pre-fix records carry no fill_source to prove otherwise.
    src = trade.get('fill_source')
    if src == 'manual':
        reasons.append('manual_fill')
    elif src is None and (ed is None or ed < CLEAN_FROM):
        reasons.append('unverified_fill_path')

    if used_fallback_stdev(trade):
        reasons.append('queue_stdev_fallback')

    if trade.get('scaled_out') and ed is not None and ed < CLEAN_FROM:
        reasons.append('scaleout_pnl_pre_fix')

    if ed is None:
        reasons.append('unparseable_entry_date')
    elif ed < CLEAN_FROM:
        reasons.append('pre_fix_accounting')

    reasons = sorted(set(reasons))
    contaminated = bool(reasons)
    phase = PRE_PHASE if contaminated else CLEAN_PHASE
    return phase, contaminated, reasons


def is_clean(trade):
    """The single predicate. Reads stored labels; does not re-derive them."""
    return (trade.get('sample_phase') == CLEAN_PHASE
            and not trade.get('contaminated', False))


def clean(trades):
    return [t for t in trades if is_clean(t)]


def excluded(trades):
    return [t for t in trades if not is_clean(t)]


def net_pnl(trade):
    """Net P&L. A gain smaller than its commissions is not a win."""
    v = trade.get('pnl_after_costs')
    return (v if v is not None else trade.get('pnl', 0)) or 0


def metrics(trades):
    """Edge metrics over whatever list is handed in. Caller does the filtering.

    Returns None when there is nothing closed to measure, rather than emitting
    zeros that would read as a real result.
    """
    closed = [t for t in trades if t.get('status') == 'closed']
    if not closed:
        return None
    wins = [t for t in closed if net_pnl(t) > 0]
    losses = [t for t in closed if net_pnl(t) <= 0]
    gross_win = sum(net_pnl(t) for t in wins)
    gross_loss = abs(sum(net_pnl(t) for t in losses))
    n = len(closed)
    return {
        'n': n,
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': len(wins) / n * 100,
        'realized': sum(net_pnl(t) for t in closed),
        'avg_win': (gross_win / len(wins)) if wins else None,
        'avg_loss': (gross_loss / len(losses)) if losses else None,
        # Undefined rather than infinite when nothing has lost yet.
        'profit_factor': (gross_win / gross_loss) if gross_loss > 0 else None,
        'expectancy': sum(net_pnl(t) for t in closed) / n,
    }