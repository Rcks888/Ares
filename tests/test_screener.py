"""The Finviz screener contract after the site change of 2026-10-17/19.

WHY THIS FILE EXISTS
--------------------
Finviz replaced its HTML results table with an embedded JSON block.
finvizfinance 1.5.0 found no table and returned None, which _run_screen
reported as "0 stocks" with no error line. Every report then screened only
SPY/QQQ/IWM for six weekdays, and nothing flagged it.

This file pins:
- the five screens send the same filter codes as finvizfinance 1.5.0 did;
- every page up to totalCount is fetched, with the same v/f/o/r parameters;
- a page that cannot be read is reported as an error, never as "0 stocks";
- a genuine empty result is still "0 stocks", not an error.
No network: requests.get and time.sleep are replaced.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import screener as sc  # noqa: E402

FAILS = []
COUNT = 0


def check(label, cond, got=None):
    global COUNT
    COUNT += 1
    if cond:
        print(f"  pass  {label}")
    else:
        print(f"  FAIL  {label}  {got if got is not None else ''}")
        FAILS.append(label)


class _Resp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _page(total, tickers):
    data = {"settings": {}, "data": {"rows": [{"text": {"ticker": t}, "numbers": {}} for t in tickers],
                                     "totalCount": total, "firstRow": 1, "pageSize": 20}}
    return ('<html><script type="application/json" data-ssr-props="ssr-island-1">'
            + json.dumps(data) + '</script></html>')


class _Site:
    """Serves pages of a fixed result list, like Finviz: past the end, the last page repeats."""

    def __init__(self, tickers, total=None, override=None):
        self.tickers = tickers
        self.total = len(tickers) if total is None else total
        self.override = override
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params), timeout))
        if self.override is not None:
            return self.override
        r = int(params["r"])
        last_start = max(0, ((len(self.tickers) - 1) // 20) * 20)
        start = min(r - 1, last_start)
        return _Resp(_page(self.total, self.tickers[start:start + 20]))


def _install(site):
    sc.requests.get = site.get
    sc.time.sleep = lambda s: None


def _quiet(fn, *a):
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = fn(*a)
    return out, buf.getvalue()


SCREEN = {'Change': 'Up 3%', 'Market Cap.': '+Mid (over $2bln)', 'Average Volume': 'Over 500K'}

print("filter codes (pinned from finvizfinance 1.5.0 constants.filter_dict)")
expected = {
    'unusual_volume': 'sh_relvol_o2,cap_midover,sh_avgvol_o500',
    'oversold_bounce': 'ta_rsi_os30,cap_midover,sh_avgvol_o500',
    'near_52w_high': 'ta_highlow52w_b0to3h,cap_largeover,sh_avgvol_o500,sh_relvol_o1.5',
    'big_movers_up': 'ta_change_u3,cap_midover,sh_avgvol_o500',
    'big_movers_down': 'ta_change_d3,cap_midover,sh_avgvol_o500',
}
captured = {}
orig_run = sc._run_screen
sc._run_screen = lambda filters, name="": captured.__setitem__(name, sc._filter_string(filters)) or []
_quiet(sc.scan_market)
sc._run_screen = orig_run
check("scan_market runs exactly the five screens", set(captured) == set(expected), sorted(captured))
for name, codes in expected.items():
    check(f"{name} -> {codes}", captured.get(name) == codes, captured.get(name))

print("paging")
names = [f"T{i:03d}" for i in range(67)]
site = _Site(names)
_install(site)
out, log = _quiet(sc._run_screen, SCREEN, "big_movers_up")
check("67 results -> 67 tickers in order", out == names, out[:3])
check("4 page requests (r=1,21,41,61)", [c[1]["r"] for c in site.calls] == ["1", "21", "41", "61"],
      [c[1]["r"] for c in site.calls])
p = site.calls[0][1]
check("params v=111, o=ticker, f=codes", p["v"] == "111" and p["o"] == "ticker"
      and p["f"] == "ta_change_u3,cap_midover,sh_avgvol_o500", p)
check("URL is /screener/stocks", site.calls[0][0] == "https://finviz.com/screener/stocks", site.calls[0][0])
check("every request has a timeout", all(c[2] for c in site.calls))
check("log line unchanged: 'Screen [big_movers_up]: 67 stocks'", "Screen [big_movers_up]: 67 stocks" in log, log)

site = _Site(names[:20])
_install(site)
out, _ = _quiet(sc._run_screen, SCREEN, "x")
check("exactly one full page -> one request", len(site.calls) == 1 and len(out) == 20, len(site.calls))

site = _Site(names[:45], total=70)
_install(site)
out, _ = _quiet(sc._run_screen, SCREEN, "x")
check("totalCount grew mid-scan: repeated last page is not double-counted", out == names[:45]
      and [c[1]["r"] for c in site.calls] == ["1", "21", "41", "61"], len(out))

print("empty and broken pages")
site = _Site([])
_install(site)
out, log = _quiet(sc._run_screen, SCREEN, "empty")
check("genuine 0 results -> [] and '0 stocks', no error", out == [] and "empty]: 0 stocks" in log
      and "error" not in log, log)

broken = {
    "no results block (old failure mode)": _Resp("<html><body>no data</body></html>"),
    "block is not JSON": _Resp('<script type="application/json" data-ssr-props="ssr-island-1">{oops</script>'),
    "block without data": _Resp('<script type="application/json" data-ssr-props="ssr-island-1">{"x":1}</script>'),
    "totalCount a string": _Resp(_page("67", ["A"])),
    "totalCount a float": _Resp(_page(67.0, ["A"])),
    "totalCount a bool": _Resp(_page(True, ["A"])),
    "totalCount negative": _Resp(_page(-1, ["A"])),
    "row without ticker": _Resp(_page(1, [""])),
    "HTTP 403": _Resp("", status=403),
}
for label, resp in broken.items():
    site = _Site([], override=resp)
    _install(site)
    out, log = _quiet(sc._run_screen, SCREEN, "b")
    check(f"{label} -> error line, not '0 stocks'", out == [] and "Screen [b] error:" in log
          and "0 stocks" not in log, log.strip())

site = _Site([], override=_Resp(_page(0, []), status=403))
_install(site)
out, log = _quiet(sc._run_screen, SCREEN, "h")
check("HTTP error is reported as such, even if the body parses", out == [] and "403" in log, log.strip())

out, log = _quiet(sc._run_screen, {'Change': 'Up 4%'}, "u")
check("unmapped filter -> error line (Finviz would silently ignore it)", out == [] and "error" in log, log)

print(f"\n{COUNT - len(FAILS)}/{COUNT} passed")
sys.exit(1 if FAILS else 0)
