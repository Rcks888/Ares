import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from engine import sample

MYT = timezone(timedelta(hours=8))

LOGS_DIR = Path(__file__).parent / "logs"
PSI_STATE = LOGS_DIR / "psi_state.json"

STALL_WARN_MS = 1000      # any full-stall second between runs is notable
PAGES_OUT_WARN = 25600    # ~100 MB at 4 KiB pages — active paging, not drift
PAGES_PER_MB = 256

def memory_pressure():
    """Deltas in memory-stall time and pages swapped out since the last run.

    Swap occupancy is a cumulative high-water mark, not a pressure signal. At
    the default vm.swappiness the kernel evicts idle anonymous pages even with
    gigabytes free, and those pages are never faulted back in. Equilibrium
    restoration after a swap clear and a genuine RAM peak produce the same
    occupancy curve, so occupancy alone cannot tell them apart.

    PSI 'full' total is cumulative stall time, which is the property that
    matters: a delta between two samples still captures a spike that began and
    ended between them — something a point-in-time MemAvailable read
    structurally cannot do.

    Requires kernel 4.20+ with CONFIG_PSI. Returns None if unavailable so the
    dashboard degrades to a no-op rather than failing.
    """
    import json
    now = {}
    try:
        with open('/proc/pressure/memory') as f:
            for line in f:
                if line.startswith('full'):
                    for tok in line.split():
                        if tok.startswith('total='):
                            now['stall_us'] = int(tok.split('=', 1)[1])
    except Exception:
        pass
    try:
        with open('/proc/vmstat') as f:
            for line in f:
                if line.startswith('pswpout '):
                    now['pswpout'] = int(line.split()[1])
                    break
    except Exception:
        pass

    if not now:
        return None

    prev = {}
    try:
        prev = json.loads(PSI_STATE.read_text())
    except Exception:
        pass

    now['ts'] = datetime.now(MYT).isoformat(timespec='seconds')
    try:
        PSI_STATE.parent.mkdir(parents=True, exist_ok=True)
        PSI_STATE.write_text(json.dumps(now))
    except Exception:
        pass

    if not prev:
        return {'first_run': True}

    out = {'first_run': False, 'window_h': None}
    if 'stall_us' in now and 'stall_us' in prev:
        out['stall_ms'] = max(0, (now['stall_us'] - prev['stall_us']) // 1000)
    if 'pswpout' in now and 'pswpout' in prev:
        out['pages_out'] = max(0, now['pswpout'] - prev['pswpout'])
    try:
        elapsed = datetime.now(MYT) - datetime.fromisoformat(prev['ts'])
        out['window_h'] = round(elapsed.total_seconds() / 3600, 1)
    except Exception:
        pass
    return out

def system_health():
    """RAM / swap / disk / cache summary. Returns list of display lines."""
    lines = []
    try:
        mem = {}
        with open('/proc/meminfo') as f:
            for line in f:
                k, v = line.split(':', 1)
                mem[k] = int(v.strip().split()[0]) // 1024   # MiB

        total = mem.get('MemTotal', 0)
        avail = mem.get('MemAvailable', 0)
        used = total - avail
        swap_used = mem.get('SwapTotal', 0) - mem.get('SwapFree', 0)

        psi = memory_pressure()
        pages_out = (psi or {}).get('pages_out')
        stall_ms = (psi or {}).get('stall_ms')
        win = (psi or {}).get('window_h')
        win_txt = f" in {win}h" if win else ""

        # Real pressure is a compound condition. Swap high with RAM free is
        # inert eviction history; swap high with RAM low is actual distress.
        pressure = swap_used > 200 and avail < 400

        ram_icon = "⚠️" if pressure else "✅"
        lines.append(f"  {ram_icon} RAM: {used}/{total} MB ({avail} free)")

        if pages_out is None:
            swap_icon = "⚠️" if pressure else "✅"
            lines.append(f"  {swap_icon} Swap: {swap_used} MB used")
        elif pages_out > PAGES_OUT_WARN:
            lines.append(f"  ⚠️ Swap: {swap_used} MB "
                         f"(+{pages_out // PAGES_PER_MB} MB paged out{win_txt})")
        else:
            moved = pages_out // PAGES_PER_MB
            lines.append(f"  ✅ Swap: {swap_used} MB "
                         f"(inert — {moved} MB paged out{win_txt})")

        if stall_ms is not None:
            if stall_ms > STALL_WARN_MS:
                lines.append(f"  ⚠️ Stall: {stall_ms} ms{win_txt} — memory pressure event")
            else:
                lines.append(f"  ✅ Stall: {stall_ms} ms{win_txt}")
        elif psi and psi.get('first_run'):
            lines.append("  ✅ Stall: baseline recorded")
    except Exception:
        pass

    try:
        import shutil
        du = shutil.disk_usage("/")
        gb = 1024 ** 3
        pct = du.used / du.total * 100
        disk_icon = "⚠️" if pct > 80 else "✅"
        lines.append(f"  {disk_icon} Disk: {du.used // gb}/{du.total // gb} GB ({pct:.0f}%)")
    except Exception:
        pass

    try:
        cache = Path(__file__).parent / "data" / "ohlcv"
        n = len(list(cache.glob("*.csv"))) if cache.exists() else 0
        cache_icon = "⚠️" if n > 2000 else "✅"
        lines.append(f"  {cache_icon} Cache: {n} symbols")
    except Exception:
        pass

    try:
        log = Path("/root/ares/cron.log")
        if log.exists():
            mb = log.stat().st_size / (1024 ** 2)
            log_icon = "⚠️" if mb > 20 else "✅"
            lines.append(f"  {log_icon} cron.log: {mb:.1f} MB")
    except Exception:
        pass

    return lines

def load_json(path):
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)

def build_dashboard():
    now = datetime.now(MYT).strftime("%b %d, %Y | %I:%M %p MYT")
    trades = load_json(LOGS_DIR / "virtual_trades.json")
    pending = load_json(LOGS_DIR / "pending_signals.json")
    queue = load_json(LOGS_DIR / "signal_queue.json")
    config_path = Path(__file__).parent / "config" / "strategy_params.json"
    with open(config_path) as f:
        params = json.load(f)

    def calc_days(entry_date_str):
        try:
            entry = datetime.strptime(entry_date_str, "%Y-%m-%d")
            return (datetime.now() - entry).days
        except Exception:
            return 0

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
            days = calc_days(t.get('entry_date', ''))
            scaled = " [50% sold]" if t.get('scaled_out') else ""
            sl = t.get('stop_loss', 0)
            ts = t.get('trailing_stop', sl)
            tp = t.get('take_profit', 0)
            shares = t.get('shares', 0)
            peak = t.get('peak_price', entry)
            unreal_pct = (peak - entry) / entry * 100 if entry else 0
            arrow = "+" if unreal_pct >= 0 else ""
            lines.append(f"  {sym} peak {arrow}{unreal_pct:.1f}% | {days}d{scaled}")
            lines.append(f"    Entry: ${entry:.2f} ({shares:.1f} shares)")
            lines.append(f"    SL: ${sl:.2f} | TS: ${ts:.2f} | TP: ${tp:.2f}")
    else:
        lines.append("  Empty")

    if pending:
        lines.append(f"\n⏳ PENDING ({len(pending)})")
        for p in pending:
            lines.append(f"  {p['symbol']} -> next open")

    ranked = load_json(LOGS_DIR / "queue_ranked.json")
    if ranked:
        lines.append(f"\n📋 QUEUED ({len(ranked)}) — ranked")
        for i, q in enumerate(ranked, 1):
            drift = q.get('drift_pct')
            drift_s = f"{drift:+.1f}%" if drift is not None else "?"
            lines.append(f"  {i}. {q['symbol']} conf{q.get('confluence', '?')} | drift {drift_s}")
    elif queue:
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

    # Official scoreboard is the clean sample only. Pre-clean history is
    # reported separately and labelled, never blended into these figures.
    clean_closed = sample.clean(closed_trades)
    excl_closed = sample.excluded(closed_trades)
    cm = sample.metrics(clean_closed)

    lines.append(f"\n🏛️ OFFICIAL — CLEAN SAMPLE")
    if cm is None:
        lines.append(f"  No clean closed trades yet")
        lines.append(f"  Edge measurement starts {sample.CLEAN_FROM.isoformat()}")
    else:
        lines.append(f"  Closed: {cm['n']} (W: {cm['wins']}/{cm['n']}, "
                     f"{cm['win_rate']:.0f}%)")
        lines.append(f"  Realized: ${cm['realized']:+.2f}")
        lines.append(f"  Expectancy: ${cm['expectancy']:+.2f}/trade")
        if cm['profit_factor'] is not None:
            lines.append(f"  Profit factor: {cm['profit_factor']:.2f}")
        lines.append(f"  Progress: {cm['n']}/40 trades")
    if excl_closed:
        em = sample.metrics(excl_closed)
        lines.append(f"\n📦 Pre-clean history (excluded from above)")
        lines.append(f"  {len(excl_closed)} closed | Realized: "
                     f"${em['realized']:+.2f} | W/L: {em['wins']}/{em['losses']}")
        lines.append(f"  Process validation only — not an edge measurement")

    total_closed = len(closed_trades)
    def _net(t):
        return sample.net_pnl(t)
    wins = len([t for t in closed_trades if _net(t) > 0])
    wr = f"{wins}/{total_closed}" if total_closed > 0 else "0"
    lines.append(f"\n🗂️ All history combined: {total_closed} (W: {wr})")
    if total_closed > 0:
        total_pnl = sum(t.get('pnl_after_costs', t.get('pnl', 0)) or 0 for t in closed_trades)
        lines.append(f"  Realized: ${total_pnl:+.2f}")

        recent = sorted(closed_trades, key=lambda t: t.get('exit_date', ''), reverse=True)[:3]
        lines.append(f"\n📋 RECENT CLOSES")
        for t in recent:
            pm = t.get('post_mortem', {})
            pnl_pct = t.get('pnl_pct', 0) or 0
            icon = "✅" if _net(t) > 0 else "❌"
            lines.append(f"  {icon} {t['symbol']} {pnl_pct:+.1f}% ({t.get('exit_reason', '?')})")
            if pm.get('verdict'):
                lines.append(f"     {pm['verdict']} | peak +{pm.get('max_favorable_excursion_pct', 0)}%")

    health = system_health()
    if health:
        lines.append(f"\n🖥️ SYSTEM")
        lines.extend(health)

    print('\n'.join(lines))

if __name__ == "__main__":
    build_dashboard()