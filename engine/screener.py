import json
import re
import time

import requests

# Finviz moved its screener to /screener/stocks on 2026-10-17/19. The new page
# has no <table>; results are in an embedded JSON block. finvizfinance 1.5.0
# parses the table, finds none and returns None, which read as "0 stocks".
SCREENER_URL = "https://finviz.com/screener/stocks"
PAGE_SIZE = 20
PAGE_SLEEP_SEC = 1
REQUEST_TIMEOUT_SEC = 20
_HEADERS = {"User-Agent": "Mozilla/5.0"}
_RESULTS_BLOCK = re.compile(
    r'<script type="application/json" data-ssr-props="ssr-island-1">(.*?)</script>',
    re.S,
)

# URL codes for the filters used in scan_market(), copied from finvizfinance
# 1.5.0 constants.filter_dict so each screen sends the same query as before.
# Finviz silently ignores unknown codes, so an unmapped pair must raise.
_FILTER_CODES = {
    ('Relative Volume', 'Over 2'): 'sh_relvol_o2',
    ('Relative Volume', 'Over 1.5'): 'sh_relvol_o1.5',
    ('Market Cap.', '+Mid (over $2bln)'): 'cap_midover',
    ('Market Cap.', '+Large (over $10bln)'): 'cap_largeover',
    ('Average Volume', 'Over 500K'): 'sh_avgvol_o500',
    ('RSI (14)', 'Oversold (30)'): 'ta_rsi_os30',
    ('52-Week High/Low', '0-3% below High'): 'ta_highlow52w_b0to3h',
    ('Change', 'Up 3%'): 'ta_change_u3',
    ('Change', 'Down 3%'): 'ta_change_d3',
}


class ScreenerPageError(Exception):
    """The screener page was fetched but its results could not be read."""


def _filter_string(filters):
    return ",".join(_FILTER_CODES[(k, v)] for k, v in filters.items())


def _fetch_page(filter_string, first_row):
    resp = requests.get(
        SCREENER_URL,
        params={"v": "111", "f": filter_string, "o": "ticker", "r": str(first_row)},
        headers=_HEADERS,
        timeout=REQUEST_TIMEOUT_SEC,
    )
    resp.raise_for_status()
    m = _RESULTS_BLOCK.search(resp.text)
    if m is None:
        raise ScreenerPageError("results block not found in page")
    try:
        data = json.loads(m.group(1))["data"]
        total = data["totalCount"]
        tickers = [row["text"]["ticker"] for row in data["rows"]]
    except (ValueError, KeyError, TypeError) as e:
        raise ScreenerPageError(f"results block unreadable: {e!r}") from e
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise ScreenerPageError(f"invalid totalCount: {total!r}")
    if not all(isinstance(t, str) and t for t in tickers):
        raise ScreenerPageError("row without a ticker")
    return total, tickers


def _screen_tickers(filters):
    """All tickers for one screen, in Finviz ticker order, de-duplicated."""
    f = _filter_string(filters)
    total, tickers = _fetch_page(f, 1)
    found = list(dict.fromkeys(tickers))
    first_row = 1 + PAGE_SIZE
    while first_row <= total:
        time.sleep(PAGE_SLEEP_SEC)
        _, page = _fetch_page(f, first_row)
        seen = set(found)
        found.extend(t for t in dict.fromkeys(page) if t not in seen)
        first_row += PAGE_SIZE
    return found


def _run_screen(filters, description=""):
    """Run a single Finviz screen and return list of tickers."""
    try:
        tickers = _screen_tickers(filters)
        print(f"  Screen [{description}]: {len(tickers)} stocks")
        return tickers
    except Exception as e:
        print(f"  Screen [{description}] error: {e!r}")
        return []

def scan_market():
    """Run multiple Finviz screens to find interesting stocks today.
    Returns dict with tickers as keys and list of screen names as values.
    """
    screens = {
        'unusual_volume': {
            'Relative Volume': 'Over 2',
            'Market Cap.': '+Mid (over $2bln)',
            'Average Volume': 'Over 500K',
        },
        'oversold_bounce': {
            'RSI (14)': 'Oversold (30)',
            'Market Cap.': '+Mid (over $2bln)',
            'Average Volume': 'Over 500K',
        },
        'near_52w_high': {
            '52-Week High/Low': '0-3% below High',
            'Market Cap.': '+Large (over $10bln)',
            'Average Volume': 'Over 500K',
            'Relative Volume': 'Over 1.5',
        },
        'big_movers_up': {
            'Change': 'Up 3%',
            'Market Cap.': '+Mid (over $2bln)',
            'Average Volume': 'Over 500K',
        },
        'big_movers_down': {
            'Change': 'Down 3%',
            'Market Cap.': '+Mid (over $2bln)',
            'Average Volume': 'Over 500K',
        },
    }

    candidates = {}

    for screen_name, filters in screens.items():
        tickers = _run_screen(filters, screen_name)
        for ticker in tickers:
            if ticker not in candidates:
                candidates[ticker] = []
            candidates[ticker].append(screen_name)

    unique = list(candidates.keys())
    multi_screen = {k: v for k, v in candidates.items() if len(v) > 1}

    print(f"\n  Finviz Summary:")
    print(f"    Total unique candidates: {len(unique)}")
    print(f"    Appeared in 2+ screens: {len(multi_screen)}")

    return candidates

MAX_CANDIDATES = 100

def get_screened_symbols():
    """Get flat list of unique symbols, capped at MAX_CANDIDATES.
    Prioritizes stocks appearing in multiple screens.
    """
    candidates = scan_market()

    sorted_symbols = sorted(
        candidates.keys(),
        key=lambda s: len(candidates[s]),
        reverse=True
    )

    if len(sorted_symbols) > MAX_CANDIDATES:
        print(f"  Capping from {len(sorted_symbols)} to {MAX_CANDIDATES} candidates")
        sorted_symbols = sorted_symbols[:MAX_CANDIDATES]
        candidates = {s: candidates[s] for s in sorted_symbols}

    return sorted_symbols, candidates
