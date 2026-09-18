import time
import yfinance as yf
import pandas as pd
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(__file__).parent.parent / "data" / "ohlcv"
DATA_DIR.mkdir(parents=True, exist_ok=True)

IBKR_PORT = 4002
IBKR_HOST = '127.0.0.1'

SYMBOL_MAP_TO_IBKR = {
    'BRK-B': 'BRK B',
}

_ib_connection = None

def _get_ib():
    """Get or create a shared IBKR connection."""
    global _ib_connection
    try:
        if _ib_connection and _ib_connection.isConnected():
            return _ib_connection
        from ib_insync import IB
        ib = IB()
        ib.connect(IBKR_HOST, IBKR_PORT, clientId=10, timeout=10)
        _ib_connection = ib
        return ib
    except Exception:
        _ib_connection = None
        return None

def disconnect_ib():
    """Disconnect IBKR connection."""
    global _ib_connection
    if _ib_connection and _ib_connection.isConnected():
        _ib_connection.disconnect()
    _ib_connection = None

def get_live_price(symbol):
    """Get latest price from IBKR using 1-min historical bars.
    Paper accounts lack live streaming subscription, but historical bars work.
    """
    ib = _get_ib()
    if not ib:
        return None

    try:
        from ib_insync import Stock
        ibkr_symbol = SYMBOL_MAP_TO_IBKR.get(symbol, symbol)
        contract = Stock(ibkr_symbol, 'SMART', 'USD')
        ib.qualifyContracts(contract)
        bars = ib.reqHistoricalData(
            contract, endDateTime='', durationStr='1 D',
            barSizeSetting='1 min', whatToShow='TRADES', useRTH=False
        )
        if bars:
            return round(float(bars[-1].close), 2)
        return None
    except Exception:
        return None

def download_stock(symbol, period="2y"):
    """Download OHLCV daily data from yfinance and cache it as flat-column CSV.

    yfinance returns MultiIndex columns like ('Close', 'AAPL'). Those must be
    flattened before caching, otherwise df['Volume'] yields a DataFrame rather
    than a Series and indicator assignment raises
    "Cannot set a DataFrame with multiple columns to the single column ...".
    """
    try:
        df = yf.download(symbol, period=period, progress=False)
        if df is None or len(df) == 0:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [str(c) for c in df.columns]
        df.to_csv(DATA_DIR / f"{symbol}.csv")
        return df
    except Exception as e:
        print(f"  yfinance error for {symbol}: {e}")
    return None

CACHE_MAX_AGE_HOURS = 20

def load_stock(symbol, max_age_hours=CACHE_MAX_AGE_HOURS):
    """Load cached stock data from disk, re-downloading if the cache is stale.

    Staleness matters: refresh_watchlist() only refreshes currently-screened
    symbols, but queue validation reads symbols that may have dropped out of
    the screener. Without this check, drift/RSI/EMA20 gates would evaluate
    against old data.
    """
    filepath = DATA_DIR / f"{symbol}.csv"

    need_download = not filepath.exists()
    age_h = 0.0
    if not need_download:
        age_h = (time.time() - filepath.stat().st_mtime) / 3600
        need_download = age_h > max_age_hours

    if need_download:
        if download_stock(symbol) is None and filepath.exists():
            print(f"  [stale cache] {symbol} is {age_h:.0f}h old, "
                  f"refresh failed — using cached")

    if not filepath.exists():
        return None

    # Single parse path. Never return the raw yfinance frame directly: its
    # column shape differs from the cached CSV and silently breaks indicators.
    try:
        df = pd.read_csv(filepath, index_col=0, parse_dates=True, date_format='ISO8601')
    except Exception as e:
        print(f"  [cache unreadable] {symbol}: {e}")
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # Legacy CSVs written with MultiIndex headers leave junk rows that coerce
    # to NaN. Drop them so indicators receive clean numeric data.
    if 'Close' in df.columns:
        df = df[df['Close'].notna()]
    if len(df) < 2:
        return None
    return df

def refresh_watchlist(symbols):
    """Download fresh daily data from yfinance for all stocks."""
    print(f"  Data source: yfinance (daily candles)")
    data = {}
    for symbol in symbols:
        df = download_stock(symbol)
        if df is not None:
            data[symbol] = df
    print(f"  Downloaded: {len(data)} stocks")
    return data

def prune_cache(keep_symbols, max_age_days=30):
    """Delete cached OHLCV files that are both stale and no longer referenced.
    Prevents unbounded growth of data/ohlcv as the screener rotates symbols.
    """
    keep = set(keep_symbols)
    cutoff = time.time() - (max_age_days * 86400)
    removed = 0
    for f in DATA_DIR.glob("*.csv"):
        if f.stem in keep:
            continue
        if f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                removed += 1
            except Exception:
                pass
    if removed:
        print(f"  Pruned {removed} stale cache file(s)")
    return removed