#!/usr/bin/env python3
"""Label every existing trade with its sample phase and contamination reasons.

Splits the dataset without wiping it. Weeks 1-3 are retained as
process-validation history; official edge measurement begins from the first
clean scheduled fill after the Sep 18-21 fixes.

This script labels only. It never rewrites a price, a share count, a stop or a
P&L figure on a closed trade -- old statistics are not made to look better than
they were, and the original record stays auditable. The one exception is that
where repair_stale_entries.py already re-based an open position, its
entry_price_original is preserved and reported here.

Detection, in order of confidence:
  stale_entry           recorded entry vs the true open for that date
  queue_stdev_fallback  from_queue and a stop at exactly 10.00% of entry, the
                        signature of the missing stdev_20
  scaleout_pnl_pre_fix  scaled out before the booking fix
  unverified_fill_path  no fill_source recorded, so a scheduled fill cannot be
                        proven -- treated as contaminated rather than assumed
  pre_fix_accounting    entered before the fix date

Dry run by default. Pass --apply to write, which backs up first.
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from engine import sample

TRADES = Path(__file__).parent / "logs" / "virtual_trades.json"


def main():
    apply = '--apply' in sys.argv
    offline = '--offline' in sys.argv
    if not TRADES.exists():
        print(f"no trades file at {TRADES}")
        return 1

    trades = json.loads(TRADES.read_text())
    stale_map = {}

    if not offline:
        try:
            import yfinance as yf
            for t in trades:
                sym = t.get('symbol')
                ed = sample.entry_date(t)
                recorded = t.get('entry_price_original') or t.get('entry_price')
                if not (sym and ed and recorded) or t.get('entry_corrected'):
                    continue
                try:
                    df = yf.download(sym, start=ed.isoformat(), progress=False,
                                     auto_adjust=False)
                    if hasattr(df.columns, 'get_level_values'):
                        df.columns = df.columns.get_level_values(0)
                    df = df[df['Open'].notna()]
                    true_open = float(df.iloc[0]['Open'])
                    err = (recorded - true_open * 1.001) / (true_open * 1.001) * 100
                    stale_map[id(t)] = (abs(err) > sample.STALE_ENTRY_TOL_PCT, err)
                except Exception:
                    pass
        except ImportError:
            print("  yfinance unavailable — running offline, stale_entry taken")
            print("  from previously recorded entry_error_pct only")

    print(f"\n  {'Symbol':<7} {'Entry':<11} {'Status':<7} {'Phase':<10} Reasons")
    print("  " + "-" * 74)

    changed = 0
    for t in trades:
        stale, err = stale_map.get(id(t), (None, None))
        phase, contaminated, reasons = sample.classify(t, stale_entry=stale)

        was = (t.get('sample_phase'), t.get('contaminated'),
               tuple(t.get('contamination_reasons') or ()))
        now = (phase, contaminated, tuple(reasons))
        if was != now:
            changed += 1
        if err is not None and t.get('entry_error_pct') is None:
            t['entry_error_pct'] = round(err, 2)

        t['sample_phase'] = phase
        t['contaminated'] = contaminated
        t['contamination_reasons'] = reasons

        mark = "  " if phase == sample.CLEAN_PHASE else "! "
        print(f"{mark}{t.get('symbol', '?'):<7} "
              f"{str(t.get('entry_date', '?'))[:10]:<11} "
              f"{t.get('status', '?'):<7} {phase:<10} "
              f"{', '.join(reasons) if reasons else '-'}")

    clean_n = len(sample.clean(trades))
    excl_n = len(trades) - clean_n
    clean_closed = len([t for t in sample.clean(trades)
                        if t.get('status') == 'closed'])

    print(f"\n  clean_v3: {clean_n}  ({clean_closed} closed)     "
          f"pre_clean: {excl_n}")
    print(f"  labels changed on {changed} trade(s)")

    tally = {}
    for t in sample.excluded(trades):
        for r in t.get('contamination_reasons') or ['unlabelled']:
            tally[r] = tally.get(r, 0) + 1
    if tally:
        print(f"\n  Exclusion reasons")
        for k, v in sorted(tally.items(), key=lambda kv: -kv[1]):
            print(f"    {k:<24} {v}")

    if clean_closed == 0:
        print(f"\n  No clean closed trades. Official counters start from the")
        print(f"  first scheduled fill on or after "
              f"{sample.CLEAN_FROM.isoformat()}.")

    print(f"\n  No price, share, stop or P&L value was modified.")
    if apply:
        backup = TRADES.with_suffix('.json.prelabel-bak')
        shutil.copy2(TRADES, backup)
        TRADES.write_text(json.dumps(trades, indent=2))
        print(f"  written. backup at {backup}")
    else:
        print(f"  DRY RUN — re-run with --apply to write")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())