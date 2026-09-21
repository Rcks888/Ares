#!/usr/bin/env python3
"""Re-base OPEN positions whose entry_price came from a stale cached bar.

Before the fill-order fix, execute_pending_signals() could read the bar cached
by a previous scan, so entry_price was sometimes the *previous* session's open.
Every field derived from entry_price inherited the error:

    shares      = position_size / entry_price
    stop_loss   = entry_price - entry_price * stdev_20 * sl_mult
    take_profit = entry_price * (1 + tp_pct)
    peak_price  initialised to entry_price

The stop_loss consequence is the reason this script exists. A stop derived from
a too-low entry sits far below where it belongs, so the position silently
carries more risk than the strategy intended. That is live behaviour, not
reporting, and it keeps mattering for as long as the trade stays open.

Closed trades are deliberately left alone: their exits already happened at
prices the wrong stops produced, and rewriting them would invent history that
never occurred. They stay contaminated and excluded from analysis.

Corrected trades are tagged entry_corrected/contaminated so they can still be
filtered out of any performance or ML dataset.

Dry run by default. Pass --apply to write, which backs up first.
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent
TRADES = ROOT / "logs" / "virtual_trades.json"
PARAMS = ROOT / "config" / "strategy_params.json"
TOL_PCT = 0.15


def main():
    apply = '--apply' in sys.argv
    if not TRADES.exists():
        print(f"no trades file at {TRADES}")
        return 1

    import yfinance as yf
    params = json.loads(PARAMS.read_text()) if PARAMS.exists() else {}
    slippage = params.get('slippage_pct', 0.001)
    trail_pct = params.get('trailing_stop_pct', 0.10)

    trades = json.loads(TRADES.read_text())
    changed, warnings = 0, []

    for t in trades:
        if t.get('status') != 'open' or t.get('entry_corrected'):
            continue
        sym = t.get('symbol')
        entry_date = str(t.get('entry_date', '')).replace(' (next-bar)', '')
        old_entry = t.get('entry_price')
        if not (sym and entry_date and old_entry):
            continue

        try:
            df = yf.download(sym, start=entry_date, progress=False,
                             auto_adjust=False)
            if hasattr(df.columns, 'get_level_values'):
                df.columns = df.columns.get_level_values(0)
            df = df[df['Open'].notna()]
            true_open = float(df.iloc[0]['Open'])
            current = float(df.iloc[-1]['Close'])
        except Exception as e:
            print(f"  ! {sym}: could not fetch ({e}), skipped")
            continue

        new_entry = round(true_open * (1 + slippage), 2)
        err_pct = (old_entry - new_entry) / new_entry * 100
        if abs(err_pct) <= TOL_PCT:
            continue

        # Preserve the ratios the original sizing produced, so the corrected
        # stop keeps the same volatility-scaled distance it was meant to have.
        sl_frac = t['stop_loss'] / old_entry
        tp_frac = (t.get('take_profit') or old_entry) / old_entry
        pos_size = t.get('position_size') or (old_entry * t['shares'])

        new_sl = round(new_entry * sl_frac, 2)
        new_tp = round(new_entry * tp_frac, 2)
        new_shares = round(pos_size / new_entry, 2)
        new_peak = max(t.get('peak_price', new_entry), new_entry)
        new_trail = round(max(new_sl, new_peak * (1 - trail_pct)), 2)

        old_unreal = (current - old_entry) / old_entry * 100
        new_unreal = (current - new_entry) / new_entry * 100

        print(f"\n  {sym}  entry_date {entry_date}")
        print(f"    entry        {old_entry:>8.2f}  ->  {new_entry:>8.2f}   "
              f"(true open {true_open:.2f}, recorded was {err_pct:+.2f}%)")
        print(f"    shares       {t['shares']:>8.2f}  ->  {new_shares:>8.2f}")
        print(f"    stop_loss    {t['stop_loss']:>8.2f}  ->  {new_sl:>8.2f}   "
              f"({sl_frac * 100 - 100:+.2f}% from entry, preserved)")
        print(f"    take_profit  {(t.get('take_profit') or 0):>8.2f}  ->  {new_tp:>8.2f}")
        print(f"    trailing     {t.get('trailing_stop', 0):>8.2f}  ->  {new_trail:>8.2f}")
        print(f"    unrealized   {old_unreal:>+7.1f}%  ->  {new_unreal:>+7.1f}%   "
              f"(at ${current:.2f})")

        if old_unreal > 0 >= new_unreal:
            warnings.append(f"{sym} was shown as a winner ({old_unreal:+.1f}%) "
                            f"but is actually underwater ({new_unreal:+.1f}%)")
        effective = max(new_sl, new_trail)
        if current <= effective:
            warnings.append(f"{sym} current ${current:.2f} is at or below its "
                            f"corrected stop ${effective:.2f} — the next monitor "
                            f"run will close it")
        elif current <= effective * 1.02:
            warnings.append(f"{sym} current ${current:.2f} is within 2% of its "
                            f"corrected stop ${effective:.2f}")

        t['entry_price_original'] = old_entry
        t['entry_true_open'] = round(true_open, 2)
        t['entry_error_pct'] = round(err_pct, 2)
        t['entry_price'] = new_entry
        t['entry_slippage'] = round(new_entry - true_open, 4)
        t['shares'] = new_shares
        t['original_shares'] = new_shares
        t['stop_loss'] = new_sl
        t['take_profit'] = new_tp
        t['trailing_stop'] = new_trail
        t['peak_price'] = round(new_peak, 2)
        t['entry_corrected'] = True
        t['contaminated'] = True
        t['contamination_reason'] = 'stale_cached_bar_at_fill'
        changed += 1

    if not changed:
        print("no open positions need re-basing")
        return 0

    if warnings:
        print("\n  ATTENTION")
        for wmsg in warnings:
            print(f"    - {wmsg}")

    print(f"\n  {changed} open position(s) re-based.")
    print("  Tagged contaminated=True — exclude these from any performance or")
    print("  ML dataset. Closed trades were left untouched on purpose.")

    if apply:
        backup = TRADES.with_suffix('.json.stale-bak')
        shutil.copy2(TRADES, backup)
        TRADES.write_text(json.dumps(trades, indent=2))
        print(f"  written. backup at {backup}")
    else:
        print("  DRY RUN — re-run with --apply to write")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())