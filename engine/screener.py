import pandas as pd
from finvizfinance.screener.overview import Overview

def _run_screen(filters, description=""):
    """Run a single Finviz screen and return list of tickers."""
    try:
        screener = Overview()
        screener.set_filter(filters_dict=filters)
        df = screener.screener_view()
        if df is not None and len(df) > 0:
            tickers = df['Ticker'].tolist()
            print(f"  Screen [{description}]: {len(tickers)} stocks")
            return tickers
        print(f"  Screen [{description}]: 0 stocks")
        return []
    except Exception as e:
        print(f"  Screen [{description}] error: {e}")
        return []

def scan_market():
    """Run multiple Finviz screens to find interesting stocks today.
    Returns dict with tickers as keys and list of screen names as values.
    """
    screens = {
        'unusual_volume': {
            'Relative Volume': 'Over 3',
            'Market Cap.': '+Large (over $10bln)',
            'Average Volume': 'Over 1M',
        },
        'oversold_bounce': {
            'RSI (14)': 'Oversold (30)',
            'Market Cap.': '+Large (over $10bln)',
            'Average Volume': 'Over 1M',
        },
        'near_52w_high': {
            '52-Week High/Low': '0-3% below High',
            'Market Cap.': '+Large (over $10bln)',
            'Average Volume': 'Over 1M',
            'Relative Volume': 'Over 1.5',
        },
        'big_movers_up': {
            'Change': 'Up 5%',
            'Market Cap.': '+Large (over $10bln)',
            'Average Volume': 'Over 1M',
        },
        'big_movers_down': {
            'Change': 'Down 5%',
            'Market Cap.': '+Large (over $10bln)',
            'Average Volume': 'Over 1M',
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
