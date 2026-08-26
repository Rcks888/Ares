import yfinance as yf
import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data" / "ohlcv"
DATA_DIR.mkdir(parents=True, exist_ok=True)

def download_stock(symbol, period="2y"):
    """Download OHLCV data for a stock and cache it locally."""
    df = yf.download(symbol, period=period)
    filepath = DATA_DIR / f"{symbol}.csv"
    df.to_csv(filepath)
    return df

def load_stock(symbol):
    """Load cached stock data from disk."""
    filepath = DATA_DIR / f"{symbol}.csv"
    if not filepath.exists():
        return download_stock(symbol)
    df = pd.read_csv(filepath, index_col=0, parse_dates=True, date_format='ISO8601')
    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df

def refresh_watchlist(symbols):
    """Download fresh data for all stocks in watchlist."""
    data = {}
    for symbol in symbols:
        data[symbol] = download_stock(symbol)
    return data