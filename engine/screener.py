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
            'Relative Volume': 'Over 2',
            'Market Cap.': '+Mid (over $2bln)',
            'Average Volume': 'Over 500K',
        },
        'oversold_bounce': {
            'RSI (14)': 'Oversold (40)',
            'Market Cap.': '+Mid (over $2bln)',
            'Average Volume': 'Over 500K',
        },
        'near_52w_high': {
            '52-Week High/Low': '0-3% below High',
            'Market Cap.': '+Mid (over $2bln)',
            'Average Volume': 'Over 500K',
            'Relative Volume': 'Over 1',
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

def get_screened_symbols():
    """Get flat list of unique symbols from all screens."""
    candidates = scan_market()
    return list(candidates.keys()), candidates
