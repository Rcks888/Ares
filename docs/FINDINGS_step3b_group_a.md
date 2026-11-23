# Step 3b group A: findings and proposals

Week of 23 Nov 2026. Environment and investigation only: nothing here changes
Ares code, configuration or the VPS by itself. Each proposal needs the
reviewer's ruling, and then the owner's go-ahead.

**Review round 1 (23 Nov, bundle `review_bundle_step3b_group_a_v1`):**
Q-G1 ACCEPT, Q-G2 ACCEPT, Q-G3 ACCEPT, **Q-G4 CHANGES REQUIRED / HOLD
execution**. See "Rulings" at the end.

**Review round 2 (23 Nov, bundle `review_bundle_step3b_group_a_v2`):**
Q-G4 ACCEPT (blocker closed; A4.3 approved for owner execution as written),
Q-G4c, Q-G5, Q-G6 ACCEPT. **Step 3b group A: APPROVED / COMPLETE.**

Evidence is from the VPS (`/root/ares/cron.log`, the venv, the OS), run by the
owner on Mon 23 Nov, and from the repository on `main`.

| Item | Finding | Proposal |
|---|---|---|
| A1 tzdata tracebacks | Resolved on 16 Sep by `pip install tzdata` in the venv; dormant since, but every IBKR price now depends on that venv package | no change; record the dependency (A1.4) |
| A2 13 Nov "55 screened" | Not a fault: the winter 13:30 UTC scan runs before the open and Finviz returns fewer matches | no change; add to known issue 11 |
| A3 wedged gateway | Never observed. In all three hangs the gateway was alive and answering; one request went unanswered (known issue 4) | reclassify issue 3 as unobserved; a probe design goes to group B |
| A4 SSH key-only | Password login on, no key installed; 13251 failed password attempts in the 2.4 days the journal holds | install and test a key, then key-only with a pre-tested way back (A4.3), after review |
| A5 "Out of memory" on the console (new) | the known 22 Oct Hermes MT5 lock-up: 13 kills, 11 `terminal64.exe`, 2 `systemd`, no Ares process; none since. The journal keeps only 2.4 days (50 MB cap) | no change; record it |
| A6 logins, four weeks (new) | no unknown login: every accepted login is the owner (home, company VPN), the VPS itself or the Web Console test; about 261,500 failed password attempts | no separate change; supports A4 |

## A1. tzdata tracebacks in `cron.log`

### A1.1 Evidence

- 19 tracebacks (38 `ZoneInfoNotFoundError` lines, two per traceback),
  between `cron.log` lines 1077 and 2146. There are none after line 2146;
  the 8 Sep 21:00 report banner is at line 2149.
- Each is `ib_insync.decoder.execDetails` → `util.parseIBDatetime` →
  `ZoneInfo('US/Eastern')`, raised during the handling of
  `ModuleNotFoundError: No module named 'tzdata'`. `ib_insync` caught each
  one and printed `Error handling fields: [...]`, so the runs continued.
- The fields are IBKR paper-account execution reports for HAFN (`BOT`, 16
  shares at $8.80), with three execution times: `20260908 09:30:16`
  (8 occurrences), `09:30:36` (8) and `09:31:09` (3) `US/Eastern`. That was
  the first V3 trade, when V3 still placed IBKR paper orders.
- The tracebacks run across several processes on 8 Sep (between the 8 Sep
  report and monitor banners). Reading: on connect, `ib_insync` asks for the
  day's executions, so every process that connected on 8 Sep received the
  same three fills and failed on each. On days without IBKR executions there
  is nothing to parse.

### A1.2 Why `US/Eastern` failed

- The VPS runs Ubuntu 24.04.4 LTS. `tzdata` (2026e) is installed;
  `tzdata-legacy` is not, so `/usr/share/zoneinfo/US/Eastern` does not exist
  (Ubuntu 24.04 moved the old `US/*` names into `tzdata-legacy`).
  `/usr/share/zoneinfo/America/New_York` exists.
- Python's `zoneinfo` searches the system `TZPATH` first and falls back to
  the `tzdata` Python package. In September the venv had no such package.

### A1.3 Status: resolved since 16 Sep

- The venv now has `tzdata` 2026.4 (IANA 2026d), installed
  2026-09-16 14:52:29 UTC; `pip show` lists no package that requires it, so it
  was installed explicitly. This matches the logbook (Sep 16: "missing
  `tzdata`", fix `apt install tzdata` + `pip install tzdata`; it had turned all
  IBKR data into `NaN`).
- Now, under the venv, `ZoneInfo('US/Eastern')` and
  `ZoneInfo('America/New_York')` both resolve.
- The current code places no IBKR orders (no `placeOrder` or order classes in
  the repository), so the execution-report path that raised these 19
  tracebacks is not reached.
- But the venv package is load-bearing today. The same 16 Sep entry says the
  missing zone "silently broke all IBKR data parsing" (the monitors showed
  "No live price" from 8 to 16 Sep). Today `get_live_price`
  (`data_feed.py:41-62`) catches every exception and returns `None`, so the
  same failure would again show only as "No live price". Live prices have
  worked since the 16 Sep install.
- The admission recorder and the inspection tool use
  `America/New_York`, which resolves from the system zone files as well.

### A1.4 Residual risk and proposal

- The fix lives only in the venv. The repository has no requirements file,
  so a rebuilt venv without `tzdata` would silently lose every IBKR price
  again (monitor "No live price", reports on daily fallback).
- **Proposal A1:** no change on the VPS. Record `tzdata` as a required venv
  package in the README's environment section, and check for it in any
  future venv rebuild.
- Option for the reviewer, not proposed by default: `apt install
  tzdata-legacy` (a system package, no Ares change) would make `US/Eastern`
  resolve from the system as well, so a venv rebuild could not break it. It
  is redundant while the venv package is present.

## A2. The Fri 13 Nov 13:31 UTC report screened 55

### A2.1 Evidence

Per-screen counts from `cron.log` for every November report (banner times
are MYT: 21:30 = 13:30 UTC, before the open in winter; 05:00 = 21:00 UTC, at
the close). Screens in order: unusual volume, oversold bounce, near 52-week
high, movers up 3%, movers down 3%. The scan caps at 100 candidates and adds
SPY, QQQ and IWM.

| Report (MYT) | Counts | Unique |
|---|---|---|
| 11-02 21:30 | 13 59 2 36 22 | 126 |
| 11-03 05:00 | 42 70 4 331 276 | 667 |
| 11-03 21:30 | 18 63 2 101 33 | 199 |
| 11-04 05:00 | 54 60 8 217 117 | 399 |
| 11-04 21:30 | 37 43 0 200 75 | 324 |
| 11-05 05:00 | 172 55 9 735 281 | 1072 |
| 11-05 21:30 | 17 66 1 82 145 | 275 |
| 11-06 05:00 | 234 161 34 59 794 | 945 |
| 11-06 21:30 | 24 86 0 189 48 | 313 |
| 11-07 05:00 | 86 81 4 339 143 | 553 |
| 11-09 21:30 | 8 69 0 97 29 | 192 |
| 11-10 05:00 | 43 44 1 704 32 | 775 |
| 11-10 21:30 | 21 44 4 22 52 | 135 |
| 11-11 05:00 | 34 42 1 78 213 | 333 |
| 11-11 21:30 | 15 28 1 177 9 | 222 |
| 11-12 05:00 | 23 26 1 276 45 | 353 |
| 11-12 21:30 | 19 26 0 17 144 | 195 |
| 11-13 05:00 | 28 30 1 33 312 | 377 |
| **11-13 21:30** | **6 26 0 17 9** | **52** |
| 11-14 05:00 | 19 22 0 75 126 | 229 |
| 11-16 21:30 | 13 29 1 16 161 | 205 |
| 11-17 05:00 | 20 44 5 39 451 | 512 |
| **11-17 21:30** | **8 40 1 37 18** | **99** |
| 11-18 05:00 | 31 63 3 36 269 | 355 |
| 11-18 21:30 | 12 66 0 37 60 | 156 |
| 11-19 05:00 | 37 98 3 152 212 | 421 |
| 11-19 21:30 | 24 66 0 154 9 | 243 |
| 11-20 05:00 | 51 130 1 29 423 | 544 |
| 11-20 21:30 | 10 156 0 11 176 | 318 |
| 11-21 05:00 | 58 232 2 20 572 | 698 |

### A2.2 Finding

- All five screens returned on 13 Nov; nothing failed. 52 unique is below the
  cap, so the report took 52 + 3 = 55.
- It is not unique: 17 Nov 13:30 UTC returned 99 unique, so it screened 102,
  which matches that night's dashboard ("102 screened"), noted on 18 Nov but
  not explained then.
- Pre-open (21:30 MYT) runs: 52–324 unique, median 199. At-close (05:00 MYT)
  runs: 229–1072, median 512. The pre-open volume and ±3% change screens are
  the thin ones (13 Nov: 6, 17, 9).
- The pre-open counts are not the previous session's: 13 Nov pre-open had 9
  "down 3%" matches against 312 at the 12 Nov close.
- Cause: since D-005 (2 Nov) the 13:30 UTC report runs at 08:30 ET, before
  the open, and Finviz's pre-open picture has few matches. Why Finviz's
  pre-open counts vary day to day is not determinable from Ares's logs.
- The dashboard's "screened" number is accurate. The 13:30 UTC scan's
  candidate list is smaller and occasionally under the cap; its indicators use
  the previous day's final bar (known issue 11).

### A2.3 Proposal

- **Proposal A2:** no change. Add to known issue 11 (winter timing): the
  13:30 UTC Finviz screen runs before the open, returns fewer candidates, and
  can fall below the 100 cap (13 Nov: 52, 17 Nov: 99). Any change to the
  schedule or the cap is a strategy matter for the V4 boundary (group C), not
  step 3b.

## A3. Known issue 3: a running but wedged gateway is never restarted

### A3.1 The gateway's daily life (VPS, 23 Nov)

- `restart_gateway.sh` runs at 13:00, 16:00 and 17:25 UTC on weekdays. It
  checks only `pgrep -f ibgateway`; if no process exists it runs
  `start_gateway.sh` (IBC, `nohup … > /root/ares/gateway.log`).
- `cron.log`: "IB Gateway is DOWN" 51 times, "is running" 105, "FAILED to
  restart" 0. Every weekday at 13:00 the gateway is down and is started
  (e.g. 16–20 Nov: PIDs 1244088, 1261396, 1281899, 1318670, 1335154, each
  "restarted successfully" at 13:00:33–34). The 16:00 and 17:25 checks find
  it running.
- The gateway stops by itself at 23:45 UTC: `gateway.log` was last written
  2026-11-20 23:45:05 UTC (14 lines, 359 bytes), and on Mon 23 Nov morning
  there was no gateway process and nothing listening on 4002. The logbook
  recorded the same 23:45 stop on Sep 10. So it runs 13:00 → 23:45 UTC on
  weekdays, which covers all four Ares cycles.
- `gateway.log` is overwritten at each start (`>`), so the log of any past
  incident is lost the next weekday (also noted 15 Oct).

### A3.2 The three hangs

| Date | Hung run (UTC) | Released | What happened at release |
|---|---|---|---|
| Thu 1 Oct | 17:30 monitor, ~3½ h | 21:01:10 | 21:00:01 report's first IB call got `Error 326: client id already in use`; the monitor exited 69 s later; the report then got IB prices |
| Fri 23 Oct | 16:10 monitor, 80 min | 17:30:12 | ~17:30:05 the 17:30 run's connect was refused (`clientId 10 already in use?`); the hung run finished 7 s later; the 17:30 run then priced the other symbols |
| Tue 17 Nov | 16:10 monitor, 80 min | 17:30:15 | 17:30:06 the 17:30 run started and connected with `clientId=10`; the hung run finished 9 s later; the 17:30 run's first connect timed out, then it priced the other four |

- In every case the gateway process was alive (17:25's check: "is running"),
  its API port answered (the second process was refused for a duplicate
  client id or timed out, then connected), and it served the next requests
  within seconds. Nothing restarted it.
- So none of the three was a wedged gateway. Each was one request inside
  `get_live_price` (`qualifyContracts` or `reqHistoricalData`, neither with a
  timeout) that got no answer, holding `clientId=10`: known issue 4.
- Each hung run was released within 7–69 s of another process trying the
  same client id. That is a consistent pattern, not a demonstrated mechanism.
- 17:25's `restart_gateway.sh` would not have helped even with a better
  check: the gateway itself was healthy.

### A3.3 Proposal

- **Proposal A3:** record known issue 3 as **not observed**: the check
  cannot see a wedged gateway, but no incident so far has been one. Keep it
  open.
- The observed failure is known issue 4, already in group B (monitor lock,
  call timeouts, client ids). Group B's design should address it first.
- If issue 3 is ever addressed, it is code (`restart_gateway.sh`), so group B,
  reviewed: an API probe with its own client id and a short timeout (connect,
  request the server time, disconnect), run in log-only mode first, with any
  kill-and-restart decided only after the log-only evidence. Appending to
  `gateway.log` instead of overwriting it belongs in the same change.
- No VPS change in step 3b group A.

## A4. Known issue 5: SSH password login is enabled

### A4.1 Evidence (VPS, 23 Nov)

- Effective `sshd -T`: `port 22`, `passwordauthentication yes`,
  `permitrootlogin yes`, `pubkeyauthentication yes`,
  `kbdinteractiveauthentication no`, `authenticationmethods any`,
  `usepam yes`.
- `sshd_config` line 12: `Include /etc/ssh/sshd_config.d/*.conf`, so the
  drop-ins are read first, in name order, and sshd keeps the first value it
  reads. `50-cloud-init.conf`: `PasswordAuthentication yes`;
  `60-cloudimg-settings.conf`: `PasswordAuthentication no`. The `50-` file
  wins, which is why password login is on.
- `ssh.socket` and `ssh.service` are both active.
- `/root/.ssh/authorized_keys` exists but is empty, so no key can log in
  today.
- `ssh` journal, queried with `--since '-7 days'`: 0 key logins, **1 password
  login** (from one IP, the owner's), **13251 failed password attempts**.
- **Correction (23 Nov, after round 1):** the journal holds only one boot's
  entries from **Fri 20 Nov 21:39:04 UTC** (`journalctl --list-boots`),
  although the host has been up since 17 Oct. So the counts cover about
  **2.4 days**, not 7: roughly **5500 failed attempts a day**, not 1900.
  Round 1 stated 7 days.
- The owner logs in from a Windows PC (PowerShell / Windows Terminal,
  OpenSSH `ssh root@<droplet>`) with the root password. The login banner
  of 23 Nov confirms that the one accepted password login came from his own
  address (06:48:58 UTC). IP addresses are not recorded in this document.
- DigitalOcean's Web Console (Droplet → Access) works (`hostname` →
  `Ares-Trading`, 23 Nov). It logged in through SSH with a temporary key: the
  journal shows 1 `Accepted publickey` in that hour, the only one in the
  journal's 2.4 days.
- Since 22 Oct a DigitalOcean cloud firewall allows inbound TCP 22 only, so
  SSH is the only exposed service.

### A4.2 Risk

The only thing between the internet and root on the VPS is the root
password, guessed at roughly 5500 times a day. A guessed password gives full
control of the bot, its repository credentials and the IBKR gateway login.

### A4.3 Proposal (to be applied only after review, by the owner)

1. **Ways back, checked first.**
   - The Web Console: it logs in with a temporary key through DigitalOcean's
     agent, so it keeps working after step 5 (keys stay allowed, and
     `prohibit-password` still allows root by key). It depends on sshd
     running, so it does not help if sshd itself is broken.
   - Independent of sshd: the Recovery Console (a local login with the root
     password). The owner did not find it under Access on 23 Nov; it may sit
     under the Droplet's Recovery tab. To be located and tested once before
     step 5. If DigitalOcean offers none, the remaining fallback is booting
     the recovery ISO, which is slower but does not depend on sshd.
2. **Key on the Windows PC.** `ssh-keygen -t ed25519` with a passphrase.
3. **Install the public key** in `/root/.ssh/authorized_keys` (mode 600,
   directory 700).
4. **Test the key** in a second session while the first stays open: the
   journal must show `Accepted publickey`.
5. **Key-only.** A new drop-in that sorts before `50-cloud-init.conf`, so
   its values win (A4.1), for example
   `/etc/ssh/sshd_config.d/10-ares-keyonly.conf`:
   `PasswordAuthentication no`, `KbdInteractiveAuthentication no`,
   `PermitRootLogin prohibit-password`. Then `sshd -t` and
   `systemctl reload ssh`.
6. **Verify**, with the first session still open: `sshd -T` shows the three
   values; a new key session works; a password-only attempt is refused
   (`ssh -o PubkeyAuthentication=no -o PreferredAuthentications=password`).
   Then watch the journal for a day: `Failed password` should stop and become
   preauth disconnects.
7. **Rollback:** delete the drop-in and `systemctl reload ssh`, from the open
   session or the Recovery Console. Rehearsed once before the first session
   is closed: move the drop-in aside, reload, `sshd -T` shows
   `passwordauthentication yes`; put it back, reload, repeat step 6.

- Port, firewall, Ares code and cron are unchanged. No Ares process uses SSH.
- `50-cloud-init.conf` is left as it is, so a cloud-init rewrite cannot undo
  the change; the `10-` drop-in overrides it.
- Separate and still open: the pending host reboot ("System restart
  required", 95 updates on 23 Nov; first noted 15 Oct). Not part of A4.
- Not proposed: changing the port, fail2ban. With key-only, password guesses
  cannot succeed, so they add nothing that is needed now.

## Questions for the reviewer

- **Q-G1 (A1).** Accept "resolved, no VPS change", with `tzdata` recorded
  as a required venv package in the README? Or also `tzdata-legacy`?
- **Q-G2 (A2).** Accept "not a fault", recorded under known issue 11, with
  any schedule or cap change left to the V4 boundary?
- **Q-G3 (A3).** Accept reclassifying known issue 3 as not observed, issue 4
  first in group B, and any gateway probe as a reviewed group B change
  (log-only first)?
- **Q-G4 (A4).** Approve the seven steps of A4.3 for the owner to apply? In
  particular: the `10-` drop-in instead of editing `50-cloud-init.conf`,
  `PermitRootLogin prohibit-password`, and whether the Web Console plus a
  located Recovery Console (or the recovery ISO) is a sufficient way back.

No approval here authorises Ares code, configuration or strategy changes;
Ares stays FROZEN.

## Rulings, review round 1 (23 Nov)

| Question | Verdict | Ruling |
|---|---|---|
| Q-G1 tzdata | ACCEPT | resolved, no VPS change; Python `tzdata` recorded as a required venv dependency (README). `tzdata-legacy` not required |
| Q-G2 winter scan | ACCEPT | not a fault in the screening machinery; recorded under known issue 11 as a consequence of the unchanged UTC schedule running pre-open after DST. Not a validation of the winter schedule as strategically equivalent; schedule and cap changes stay outside group A |
| Q-G3 gateway | ACCEPT | issue 3 unobserved, remains open; issue 4 is the observed mechanism, investigated first in group B; a gateway probe is group B only, reviewed design first, log-only before restart authority |
| Q-G4 SSH | CHANGES REQUIRED / HOLD | the design is approved (earlier-numbered drop-in overriding `50-cloud-init.conf`; `PermitRootLogin prohibit-password`; the order key → install → second-session test → `sshd -t` → reload → new key session → password-refusal test → rollback rehearsal). **Blocker:** no independent recovery path has been demonstrated. Execution is held until one is located and successfully tested, as observed evidence |

No Ares code, configuration, strategy or VPS change is authorised by Q-G1 to
Q-G3.

## A4 blocker: independent recovery, tested 23 Nov (for review round 2)

- The owner found the DigitalOcean **Recovery Console** under the Droplet's
  settings (it is not under Access, which shows only the Web Console).
- It opened a local console with an `Ares-Trading login:` prompt. This is
  the server's own console, not SSH.
- The owner logged in as `root` with the root password, ran `hostname`
  (output `Ares-Trading`), and ran `logout`. Everything was as expected.
- So the independent way back in A4.3 step 1 is now observed, not planned.
  It depends on the root password, which A4 leaves unchanged (password
  authentication is only switched off for SSH).
- Seen on the same console: kernel "Out of memory" messages printed before
  the login prompt. Recorded separately as a new finding (A5); not part of
  A4.

## A5. "Out of memory" on the console, and the short journal (new, 23 Nov)

### A5.1 Evidence (VPS, 23 Nov)

- Host up since **17 Oct 01:02:37 UTC**. Now: 1967 MB RAM, 1484 MB
  available; swap 296 of 2047 MB used.
- Kernel ring buffer since boot (`dmesg -T`): **13** `Killed process` lines,
  all on **22 Oct**, first 07:40:51 and last 08:18:31 UTC. Victims:
  `terminal64.exe` ×11 (the MT5 terminal under Wine, Hermes; about 85 MB
  resident each time) and `systemd` ×2 (a per-user manager, pid 1046,
  `oom_score_adj` 100). `kern.log*` (kept back to about 24 Oct) gives the
  same 13, all dated 2026-10-22.
- This is the **known 22 Oct 06:05–08:26 UTC host lock-up caused by Hermes
  MT5** (logbook 2026-10-22 → 23: monitor stall 17.8 min, 2284 MB paged out).
  No Ares job ran in that window, and the gateway runs only from 13:00 UTC.
- No kill before or after 22 Oct since the 17 Oct boot. The messages stay on
  the console until it scrolls, which is why the owner saw them on 23 Nov.
- The systemd journal holds only **20 Nov 21:39 UTC onwards** (45.1 MB used;
  `SystemMaxUse=50M`, set on purpose in September to stop journald growing).
  Most of it is SSH password noise, which is why it covers only 2.4 days
  (A4.1 correction). `syslog` and `kern.log` (rsyslog) keep about four
  weeks.

### A5.2 Finding and proposal

- Not an Ares fault and not new: the console messages are the recorded 22 Oct
  Hermes MT5 event; no Ares process was killed. The host is a shared 2 GB
  machine (gateway JVM, Ares, Hermes MT5 under Wine), already noted in
  September.
- **Proposal A5:** no change. Record it; a host memory or upgrade decision
  belongs with Hermes and the owner, not Ares step 3b.
- The 50 MB journal cap is kept. A4 (key-only) should reduce the SSH noise
  that fills it (bots get no password prompt, so fewer lines per attempt).
  Re-measure the retention one week after A4 is applied, and meanwhile use
  the rsyslog files for anything older than two days.

## A6. Who has logged in: four weeks of `auth.log` (new, 23 Nov)

A4.1's counts came from a 2.4-day journal, so the accepted logins were
re-checked over the rsyslog `auth.log*` files (about 26 Oct to 23 Nov;
`wtmp` reaches back to 23 Oct). IP addresses are not recorded here.

### A6.1 Evidence

- `Failed password` lines per file, newest first: 10352, 46810, 50507,
  73114, 80732: **about 261,500 in four weeks**.
- Accepted logins, all as root:

  | Logins | Method | Source | Sessions (UTC) | Identified as |
  |---|---|---|---|---|
  | 20 | password | owner's home address | | the owner |
  | 4 | password | address B | 30 Oct 06:32–07:35 | the owner, company VPN |
  | 1 | password | address C | 23 Oct 02:48–04:03 (`wtmp`), 6 Nov 02:47–02:54 | the owner, company VPN |
  | 1 | password | the droplet's own public address | 30 Oct 06:44:19, 17 s | a login from the VPS to itself, inside a B session; `known_hosts` updated the same second |
  | 1 | publickey | a DigitalOcean address | 23 Nov 07:11:58 | the Web Console test (A4) |

- The sessions match the owner's own work: C at the times of laptop commits
  on 23 Oct (10:51, 10:57 MYT) and 6 Nov (10:53 MYT); B during the Hermes
  MT5 login recovery on 30 Oct, whose soak PASS is at 07:36:41 UTC, one
  minute after B's last session. The owner confirmed on 23 Nov that B and C
  are his company VPN, which he uses from his work laptop on Fridays and at
  weekends.
- Compromise indicators, all clean:
  - accounts with uid 0 or ≥ 1000: `root` and `nobody` only;
  - listening: `sshd` on 22 (public), `systemd-resolved` and Hermes's
    `wineserver` on loopback only;
  - `/root/.ssh` (metadata only): `id_ed25519` pair from 18 Sep (the VPS's
    own key), `known_hosts` 30 Oct (the self-login), `authorized_keys`
    333 bytes at 23 Nov 07:11:53, 5 s before the Web Console's key login
    (DigitalOcean's temporary key; it was empty before);
  - root crontab last modified 22 Oct 08:29:10 UTC, just after the 22 Oct
    Hermes lock-up ended (08:26), so it differs from the 17 Oct md5 record.
    New md5 `f592e6b53275467862c3c652568528e5`. Its 9 lines are exactly the
    known jobs: the 7 Ares lines (gateway restart 13:00, 16:00, 17:25;
    `run_ares.sh` 13:30, 21:00; `run_monitor.sh` 16:10, 17:30; weekdays) and
    the 2 Hermes lines (`run_soak.py` at :07/:22/:37/:52, `run_hermes.py`
    at :08/:23/:38/:53, as in Hermes's STATUS). Nothing else.

### A6.2 Finding and proposal

- No unknown login in four weeks. The password was guessed about 9,000 times
  a day and has held so far; A4 removes that exposure.
- **Proposal A6:** no separate change; it supports A4. After A4, confirm
  that `authorized_keys` holds only the owner's key plus any current
  DigitalOcean temporary key, and that the temporary key is removed again.

## Questions for the reviewer, round 2

- **Q-G4 (A4, resubmitted).** The independent recovery path is now observed
  (Recovery Console, local root login, `hostname`, `logout`, 23 Nov). Is the
  blocker cleared, and is A4.3 approved for the owner to apply as written?
- **Q-G4c (A4.1 correction).** The round 1 counts covered 2.4 days, not 7
  (the journal starts 20 Nov 21:39 UTC). Accept the corrected rate (about
  5,500 a day in the journal; about 9,000 a day over four weeks of
  `auth.log`)?
- **Q-G5 (A5).** Accept the console "Out of memory" messages as the recorded
  22 Oct Hermes MT5 event, with no Ares change, and the 50 MB journal cap
  kept and re-measured after A4?
- **Q-G6 (A6).** Accept "no unknown login in four weeks" on this evidence?

## Rulings, review round 2 (23 Nov)

| Question | Verdict | Ruling |
|---|---|---|
| Q-G4 SSH key-only | ACCEPT | blocker closed: the Recovery Console is demonstrated out-of-band access, independent of sshd and distinct from the SSH-based Web Console. **A4.3 approved as written for owner execution**, with `PasswordAuthentication no`, `KbdInteractiveAuthentication no`, `PermitRootLogin prohibit-password`. The root password stays, for the Recovery Console |
| Q-G4c corrected rate | ACCEPT | 13,251 failures in about 2.4 days (about 5,500 a day); about 261,500 over four weeks of `auth.log` (about 9,000 a day) |
| Q-G5 OOM | ACCEPT | the known 22 Oct Hermes incident (13 kills, 0 Ares processes, none later); no active memory pressure on 23 Nov; the short journal is explained by `SystemMaxUse=50M`. No Ares step 3b change |
| Q-G6 logins | ACCEPT, within the evidence | "no unidentified successful login in the about four weeks of authentication evidence"; deliberately narrower than "the host cannot have been compromised by any mechanism" |

**Group A: APPROVED / COMPLETE.** This does not authorise Ares code, trading
configuration, strategy, group B or group C work, or lifting FROZEN.
