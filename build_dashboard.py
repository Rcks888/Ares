import json
from datetime import datetime
from pathlib import Path
from engine.tracker import load_trades, load_pending, load_queue
from engine.data_feed import load_stock

LOGS_DIR = Path(__file__).parent / "logs"

def load_params():
    config_path = Path(__file__).parent / "config" / "strategy_params.json"
    with open(config_path) as f:
        return json.load(f)

def build_dashboard():
    now = datetime.now().strftime("%b %d, %Y | %I:%M %p MYT")
    trades = load_trades()
    pending = load_pending()
    queue = load_queue()
    params = load_params()

    open_trades = [t for t in trades if t['status'] == 'open']
    closed_trades = [t for t in trades if t['status'] == 'closed']
    max_pos = params.get('max_positions', 5)

    lines = []
    lines.append(f"📊 <b>ARES V3 — Daily Dashboard</b>")
    lines.append(f"{now}")
    lines.append("")

    lines.append(f"💼 <b>PORTFOLIO ({len(open_trades)}/{max_pos} slots)</b>")
    if open_trades:
        for t in open_trades:
            sym = t['symbol']
            entry = t['entry_price']
            days = t.get('holding_days', 0)
            scaled = " [50% sold]" if t.get('scaled_out') else ""
            try:
                df = load_stock(sym)
                if df is not None and len(df) > 0:
                    current = float(df.iloc[-1]['Close'])
                    pnl_pct = (current - entry) / entry * 100
                    arrow = "📈" if pnl_pct >= 0 else "📉"
                    lines.append(f"  {sym}  {pnl_pct:+.1f}%  {arrow} {days}d  ${current:.2f}{scaled}")
                else:
                    lines.append(f"  {sym}  ?%  {days}d  entry ${entry:.2f}{scaled}")
            except Exception:
                lines.append(f"  {sym}  ?%  {days}d  entry ${entry:.2f}{scaled}")
    else:
        lines.append("  No open positions")
    lines.append("")

    if pending:
        lines.append(f"⏳ <b>PENDING ({len(pending)})</b>")
        for p in pending:
            lines.append(f"  {p['symbol']} — executes tomorrow at open")
        lines.append("")

    if queue:
        lines.append(f"📋 <b>QUEUED ({len(queue)})</b>")
        for q in queue:
            queued_date = q.get('queued_date', q.get('date', ''))
            lines.append(f"  {q['symbol']} — queued {queued_date}")
        lines.append("")

    if open_trades:
        total_pnl = 0
        total_comm = 0
        for t in open_trades:
            try:
                df = load_stock(t['symbol'])
                if df is not None and len(df) > 0:
                    current = float(df.iloc[-1]['Close'])
                    pnl = (current - t['entry_price']) * t['shares']
                    total_pnl += pnl
            except Exception:
                pass
            total_comm += t.get('total_commission', 1.0)
        lines.append(f"💰 <b>ESTIMATED P&L</b>")
        lines.append(f"  Open trades: ${total_pnl:+.2f}")
        lines.append(f"  Commissions: -${total_comm:.2f}")
        lines.append(f"  Net: ${total_pnl - total_comm:+.2f}")
        lines.append("")

    output_file = LOGS_DIR / "last_scan_summary.txt"
    scan_candidates = 0
    scan_signals = 0
    new_signal_names = []
    if output_file.exists():
        txt = output_file.read_text()
        for line in txt.split('\n'):
            if 'candidates_screened:' in line:
                scan_candidates = int(line.split(':')[1].strip())
            elif 'signals_found:' in line:
                scan_signals = int(line.split(':')[1].strip())
            elif 'signal_names:' in line:
                names = line.split(':')[1].strip()
                if names:
                    new_signal_names = names.split(',')

    lines.append(f"📡 <b>TODAY'S SCAN</b>")
    lines.append(f"  Candidates screened: {scan_candidates}")
    lines.append(f"  New signals: {scan_signals}")
    if new_signal_names:
        for name in new_signal_names:
            lines.append(f"  → {name.strip()}")
    lines.append("")

    total_closed = len(closed_trades)
    wins = len([t for t in closed_trades if (t.get('pnl', 0) or 0) > 0])
    losses = total_closed - wins
    win_rate = (wins / total_closed * 100) if total_closed > 0 else 0

    lines.append(f"🏛️ <b>OBSERVATION PHASE</b>")
    lines.append(f"  Total trades completed: {total_closed}")
    if total_closed > 0:
        lines.append(f"  Win/Loss: {wins}W / {losses}L ({win_rate:.0f}%)")
        total_pnl_closed = sum(t.get('pnl_after_costs', t.get('pnl', 0)) or 0 for t in closed_trades)
        lines.append(f"  Realized P&L: ${total_pnl_closed:+.2f}")

    print('\n'.join(lines))

if __name__ == "__main__":
    build_dashboard()