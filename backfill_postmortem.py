"""One-time script to backfill post_mortem on already-closed trades."""
from engine.tracker import load_trades, save_trades, _build_post_mortem

def backfill():
    trades = load_trades()
    updated = 0
    for t in trades:
        if t['status'] == 'closed' and 'post_mortem' not in t:
            t['post_mortem'] = _build_post_mortem(t, t.get('exit_reason', 'unknown'))
            print(f"  {t['symbol']}: {t['post_mortem']['verdict']}")
            print(f"    {t['post_mortem']['analysis']}")
            updated += 1
    if updated:
        save_trades(trades)
        print(f"\nBackfilled {updated} trade(s).")
    else:
        print("No trades need backfilling.")

if __name__ == "__main__":
    backfill()