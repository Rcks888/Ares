#!/usr/bin/env python3
"""One-off repair: rebook closed trades whose scale-out gain was never recorded.

The scale-out handler reduced trade['shares'] and stored scale_out_price, but
computed the realised gain on the sold tranche into a local variable that was
printed and discarded. _close_trade then measured P&L on the remaining shares
only, so every scaled-out winner was understated by its locked-in profit.

It also overwrote total_commission with entry+exit, dropping the scale-out
commission.

Both are recoverable from stored fields:
    sell_shares    = original_shares - shares
    scale_out_pnl  = (scale_out_price - entry_price) * sell_shares

Dry run by default. Pass --apply to write, which backs up first.
"""
import json
import shutil
import sys
from pathlib import Path

TRADES = Path(__file__).parent / "logs" / "virtual_trades.json"
COMMISSION = 1.00


def repair(trade):
    """Return (changed, before, after) for one trade."""
    if trade.get('status') != 'closed' or not trade.get('scaled_out'):
        return False, None, None
    if trade.get('scale_out_pnl') is not None:
        return False, None, None          # already repaired

    entry = trade.get('entry_price')
    exit_p = trade.get('exit_price')
    original = trade.get('original_shares')
    remaining = trade.get('shares')
    scale_price = trade.get('scale_out_price')
    if None in (entry, exit_p, original, remaining, scale_price):
        print(f"  ! {trade.get('symbol')}: missing fields, skipped")
        return False, None, None

    before = {
        'pnl': trade.get('pnl'),
        'pnl_pct': trade.get('pnl_pct'),
        'pnl_after_costs': trade.get('pnl_after_costs'),
        'total_commission': trade.get('total_commission'),
    }

    sell_shares = round(original - remaining, 2)
    scale_pnl = round((scale_price - entry) * sell_shares, 2)
    remaining_pnl = round((exit_p - entry) * remaining, 2)
    pnl = round(scale_pnl + remaining_pnl, 2)
    # entry + scale-out + exit
    total_commission = round(COMMISSION * 3, 2)
    cost_basis = entry * original

    trade['scale_out_shares'] = sell_shares
    trade['scale_out_pnl'] = scale_pnl
    trade['scale_out_pnl_pct'] = round((scale_price - entry) / entry * 100, 2)
    trade['pnl_remaining'] = remaining_pnl
    trade['pnl'] = pnl
    trade['pnl_pct'] = round(pnl / cost_basis * 100, 2) if cost_basis else 0.0
    trade['total_commission'] = total_commission
    trade['pnl_after_costs'] = round(pnl - total_commission, 2)

    after = {
        'pnl': trade['pnl'],
        'pnl_pct': trade['pnl_pct'],
        'pnl_after_costs': trade['pnl_after_costs'],
        'total_commission': trade['total_commission'],
    }
    return True, before, after


def main():
    apply = '--apply' in sys.argv
    if not TRADES.exists():
        print(f"no trades file at {TRADES}")
        return 1

    trades = json.loads(TRADES.read_text())
    changed = 0
    for t in trades:
        did, before, after = repair(t)
        if not did:
            continue
        changed += 1
        print(f"\n  {t['symbol']} ({t.get('exit_reason')})")
        print(f"    scale-out: {t['scale_out_shares']} sh @ ${t['scale_out_price']:.2f} "
              f"= +${t['scale_out_pnl']:.2f} locked (was unbooked)")
        print(f"    pnl              {before['pnl']:+.2f}  ->  {after['pnl']:+.2f}")
        print(f"    pnl_pct          {before['pnl_pct']:+.2f}% ->  {after['pnl_pct']:+.2f}%")
        print(f"    commission       {before['total_commission']:.2f}   ->  "
              f"{after['total_commission']:.2f}")
        print(f"    pnl_after_costs  {before['pnl_after_costs']:+.2f}  ->  "
              f"{after['pnl_after_costs']:+.2f}")

    if not changed:
        print("nothing to repair")
        return 0

    net = sum((t.get('pnl_after_costs') or 0) for t in trades if t.get('status') == 'closed')
    print(f"\n  {changed} trade(s) repaired. Realised total would become ${net:+.2f}")

    if apply:
        backup = TRADES.with_suffix('.json.bak')
        shutil.copy2(TRADES, backup)
        TRADES.write_text(json.dumps(trades, indent=2))
        print(f"  written. backup at {backup}")
    else:
        print("  DRY RUN — re-run with --apply to write")
    return 0


if __name__ == '__main__':
    sys.exit(main())