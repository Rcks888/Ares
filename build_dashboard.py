import json
from datetime import datetime
from pathlib import Path

LOGS_DIR = Path(__file__).parent / "logs"

def load_json(path):
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)

def build_dashboard():
    now = datetime.now().strftime("%b %d, %Y | %I:%M %p MYT")
    trades = load_json(LOGS_DIR / "virtual_trades.json")
    pending = load_json(LOGS_DIR / "pending_signals.json")
    queue = load_json(LOGS_DIR / "signal_queue.json")
    config_path = Path(__file__).parent / "config" / "strategy_params.json"
    with open(config_path) as f:
        params = json.load(f)

    open_trades = [t for t in trades if t['status'] == 'open']
    closed_trades = [t for t in trades if t['status'] == 'closed']
    max_pos = params.get('max_positions', 5)

    lines = []
    lines.append(f"📊 ARES V3 Dashboard")
    lines.append(f"{now}")

    lines.append(f"\n💼 PORTFOLIO ({len(open_trades)}/{max_pos} slots)")
    if open_trades:
        for t in open_trades:
            sym = t['symbol']
            entry = t['entry_price']
            days = t.get('holding_days', 0)
            scaled = " [50% sold]" if t.get('scaled_out') else ""
            pnl_pct = t.get('pnl_pct', 0) or 0
            arrow = "+" if pnl_pct >= 0 else ""
            sl = t.get('stop_loss', 0)
            ts = t.get('trailing_stop', sl)
            tp = t.get('take_profit', 0)
            shares = t.get('shares', 0)
            lines.append(f"  {sym} {arrow}{pnl_pct:.1f}% | {days}d{scaled}")
            lines.append(f"    Entry: ${entry:.2f} ({shares:.1f} shares)")
            lines.append(f"    SL: ${sl:.2f} | TS: ${ts:.2f} | TP: ${tp:.2f}")
    else:
        lines.append("  Empty")

    if pending:
        lines.append(f"\n⏳ PENDING ({len(pending)})")
        for p in pending:
            lines.append(f"  {p['symbol']} -> next open")

    if queue:
        lines.append(f"\n📋 QUEUED ({len(queue)})")
        for q in queue:
            lines.append(f"  {q['symbol']}")

    summary = LOGS_DIR / "last_scan_summary.txt"
    if summary.exists():
        txt = summary.read_text()
        cands = sigs = 0
        sig_details = ""
        for line in txt.split('\n'):
            if 'candidates_screened:' in line:
                cands = line.split(':')[1].strip()
            elif 'signals_found:' in line:
                sigs = line.split(':')[1].strip()
            elif 'signal_details:' in line:
                sig_details = line.split(':', 1)[1].strip()
        lines.append(f"\n📡 SCAN: {cands} screened, {sigs} signals")
        if sig_details:
            for detail in sig_details.split('|||'):
                lines.append(f"  {detail.strip()}")

    total_closed = len(closed_trades)
    wins = len([t for t in closed_trades if (t.get('pnl', 0) or 0) > 0])
    wr = f"{wins}/{total_closed}" if total_closed > 0 else "0"
    lines.append(f"\n🏛️ Trades completed: {total_closed} (W: {wr})")
    if total_closed > 0:
        total_pnl = sum(t.get('pnl_after_costs', t.get('pnl', 0)) or 0 for t in closed_trades)
        lines.append(f"  Realized: ${total_pnl:+.2f}")

    print('\n'.join(lines))

if __name__ == "__main__":
    build_dashboard()