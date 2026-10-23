# Ares: current status

Last updated: 2026-10-23 (weekly checkpoint). This page is the entry point. History is in
`Ares_Logbook.md`, full reference in `README.md`, finished investigations in
`archive/`. Everything removed in the cleanup is kept at tag
`checkpoint-2026-10-12`.

## State: FROZEN

The bot runs as is: paper trading only, V3.1, on the VPS (`/root/ares/Ares`).
No code changes without an explicit decision. Phase 5, policy unification and AI
modelling remain prohibited.

## Plan and timeline

The main flow (agreed Oct 9), and where things stand:

```
✔ 0. Recorder repairs accepted   (v7 accepted 2026-10-12, branch 28ae5f0)
✔ 1. Conditional recorder activation   (5fd13e9 on the VPS, 2026-10-13; rollback pre-tested)
✔ 2. First monitor-cycle inspection  → CHECKPOINT   (2026-10-15: both nights, 4 cycles, PASS)
▶ 3. Admission-source capture (separately reviewed, both routes)   ← HERE: design r5 accepted 2026-10-21; next: implementation on recorder/admission-v1
◻ 4. Evidence-informed decision on which exit policy is authoritative
```

By week (Mon–Fri). Update the "Actual" column at the end of each week, and say
why if it differs from the plan.

| Week | Planned | Actual | Change vs plan |
|---|---|---|---|
| Aug 27 – Sep 4 | V1, then V2/V2.1 | as planned | — |
| Sep 7 – Sep 25 | V3, data cleaning, parity build | as planned | — |
| Sep 28 – Oct 2 | parity (Phase 4) live; vacation from Oct 1 | parity started around Sep 29 | — |
| Oct 5 – Oct 9 | vacation; resume | incident (Oct 7), reconciliation, recorder designed, built and reviewed | unplanned: incident work |
| Oct 12 – Oct 16 | recorder activation, Mon 16:10 UTC | HOLD from review → repair round 2 → pause → checkpoint, audit, cleanup, WBD closed → v3 update → inspection-tool rounds v4–v7 → **accepted Mon** → **activated Tue 13 Oct** (`5fd13e9`) → **inspection Thu 15 Oct: PASS, both nights** → checkpoint | one day late against the original Mon date, a week early against the replan |
| Oct 19 – Oct 23 | step 0 review → steps 1–2 → checkpoint | (steps 0–2 done the week before) route audit, step 3 design r1–r5, **r5 accepted for implementation review Wed 21 Oct**. Monitor inspections Mon–Thu: 8 cycles, all PASS. Unplanned, Thu 22 Oct: eMudhra CA response, environment only (venv certifi 2026.10.19, emSign roots removed from the gateway JRE, DigitalOcean firewall inbound 22 only), all three verified 23 Oct. SKYD closed at TP (clean sample 10, W 2/10). Finding: AMP cache regression (known issue 8) | pulled forward; step 3 design done a week early; security work unplanned, no code change |
| Oct 26 – Oct 30 | **1st: Finviz** (if still broken on 23 Oct night): identify the cause, design and review a minimal fix with the reviewer. **2nd, if time:** step 3 implementation on `recorder/admission-v1` (not activated) | | replanned 23 Oct: step 3 design finished early. If the Finviz work takes the week, step 3 implementation and later checkpoints move one week (not a rushed project) |
| Nov 2 onward | evidence collection → step 4 | | no date until there is enough evidence to judge |

## What runs (cron, weekdays only)

| UTC | MYT | Job | What it does |
|---|---|---|---|
| 13:00, 16:00, 17:25 | 21:00, 00:00, 01:25 | `restart_gateway.sh` | starts IB Gateway **only if no gateway process exists** |
| 13:30, 21:00 | 21:30, 05:00 | `run_ares.sh` → `daily_report.py`, `build_dashboard.py` | scan, fill pending, check exits, dashboard to Telegram, commit `logs/` |
| 16:10, 17:30 | 00:10, 01:30 | `run_monitor.sh` → `monitor_trades.py` | live-price exit check, Telegram, commit `logs/` |

Production state is in `logs/` (`virtual_trades.json` is authoritative), and is
backed up to GitHub by the bot's own commits.

## Code

- **Trading:** `daily_report.py`, `monitor_trades.py`, `build_dashboard.py`,
  `engine/{data_feed,indicators,signals,screener,sample,tracker}.py`, the four
  launchers, and `config/`.
- **Parity check (Phase 4), kept and frozen:**
  `engine/{parity_*,tracker_compat,exit_policy}.py`.
  - Report path only. Observes and never writes trading state; output goes to
    `logs/tracker_parity_v1.jsonl`.
  - Off-switch: comment out `export ARES_PARITY=1` in `run_ares.sh`.
- **Monitor-path recorder (active since `5fd13e9`):** `engine/monitor_observer.py`
  plus 46 registered observation lines in `monitor_trades.py`.
  - Observes only, and writes `logs/monitor_{observation,heartbeat,health}_v1.jsonl`.
  - Each cycle is checked with `tools/inspect_monitor_cycles.py`.
  - Rollback and procedure: `ACTIVATION_monitor_recorder_v1.md` §3.5.
- **Tests:** `tests/`, `tools/`. Only needed if trading or parity code changes.
  Run `for t in tests/test_*.py; do python3 "$t" || echo "FAIL $t"; done`; all
  must exit 0 (they did on 2026-10-12).

## How deployment actually works

Merging to `main` does **not** reliably reach the VPS. Each launcher runs Python
**first** and git **afterwards**, and it pulls only if its push fails. To deploy,
outside the cron times above:

```sh
cd /root/ares/Ares && git pull --ff-only && git log -1 --oneline
```

## Known issues with live consequence (unrepaired)

1. **The bot cannot detect a delisting.** WBD was cash-acquired by SKYD and
   delisted on 2026-10-06. The bot kept holding it with no price for a week,
   until it was closed by hand on 2026-10-12 (commit `22142a4`; see the
   logbook). A future acquisition will look the same: "No live price", a
   1-row cache file and a parity failure, while the position blocks a slot.
2. **The monitor's decisions were not recorded.** The monitor-path recorder
   (activated `5fd13e9`, 2026-10-13) records them from the 16:10 UTC cycle on
   Oct 13. **Confirmed 2026-10-15**: 4 cycles inspected, PASS, including MANE's close. Known
   producer limitation: it can write non-finite values marked complete; the
   inspection (`tools/inspect_monitor_cycles.py`) rejects them.
3. **The gateway restart only acts when no gateway process exists at all.** A
   running but wedged gateway is never restarted.
4. **All Ares processes use IB `clientId=10`, and only `connect` has a
   timeout.** A stalled monitor can block the report (Oct 1).
5. **SSH password login is enabled on the VPS**, and outsiders are trying
   passwords. Since 2026-10-22, a DigitalOcean cloud firewall allows inbound
   TCP 22 only (port 4002 verified dropped from outside). SSH itself is
   unchanged.
6. **The Finviz screener returns nothing (since Mon 19 Oct 13:32 UTC).**
   - Finviz moved and redesigned its screener page: `/screener/stocks`, no
     HTML tables.
   - `finvizfinance` 1.5.0, the newest version, silently returns `None`.
   - Every report screens only SPY/QQQ/IWM ("3 screened"), so no new signals
     come in. The fixed-watchlist fallback never fires, and the dashboard
     gives no warning.
   - Decision 2026-10-20: **wait, no change** (FROZEN). Revisit at the weekly
     checkpoint. Details are in the logbook.
   - Checkpoint 2026-10-23: still broken after 5 weekdays (10 reports, 0
     signals). Since SKYD closed, 1 slot is idle and the queue is empty. Step
     3 needs admissions to observe.
   - **Decision:** hold, and check whether the 23 Oct 21:32 MYT dashboard
     still says 3 screened. If it does, Finviz becomes next week's first
     target: identify the cause, then a reviewed fix in a joint session with
     the reviewer.
7. **Intra-session admissions fill at that session's open, which came before
   the decision** (found 2026-10-20, corrected r2, r3 and r4). 11 fills (9
   `clean_v3`) are bounded: each holds if its admitting run started at its
   cron slot. None can be established from history: even a matching cron
   launch does not show which process made the commit. They are booked at a price that wasn't available when the
   pending was created (−0.79% to +3.49% against the decision price).
   - Recorded, not fixed (FROZEN). Audit:
     `archive/AUDIT_2026-10-20_admission_routes.md`.
   - Step 3 will record the decision time and price, plus the fill bar, so
     the gap is measured on every trade.
8. **The OHLCV cache accepts a history that goes backwards** (found
   2026-10-23).
   - `download_stock` (`engine/data_feed.py:72-80`) saves whatever yfinance
     returns. On 22 Oct 13:30 UTC, Yahoo returned AMP without its 21 Oct
     row, which replaced a cache that had it.
   - Both 22 Oct reports used the 20 Oct bar for AMP's daily exit check. AMP
     was far from its SL and TP, so there was probably no effect.
   - Recorded, not fixed (FROZEN). Self-check: did the 23 Oct 13:30
     re-download restore 21/22 Oct?

## Open questions (not being worked on)

- the five monitor exits that cannot be recovered
- admission-source capture
- which exit policy is authoritative
- `MIN_CLOSED_SAMPLE`
- why `get_live_price` returns `None` (four possible causes)
- the monitor's missing per-trade exception guard
- the monitor's exit-date basis
- the pending-path drift guard
- PCVX fill realism (permanently unanswerable)
- the Hermes record says "halted", but Hermes is live
