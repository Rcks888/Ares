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
    """Get live/delayed price from IBKR for stop-loss and trailing stop checks."""
    ib = _get_ib()
    if not ib:
        return None

    try:
        from ib_insync import Stock
        import time
        ibkr_symbol = SYMBOL_MAP_TO_IBKR.get(symbol, symbol)
        contract = Stock(ibkr_symbol, 'SMART', 'USD')
        ib.qualifyContracts(contract)
        ib.reqMarketDataType(3)
        ticker = ib.reqMktData(contract)
        time.sleep(3)
        price = ticker.last if ticker.last == ticker.last else ticker.close
        ib.cancelMktData(contract)
        if price and price == price:
            return float(price)
        return None
    except Exception:
        return None

def download_stock(symbol, period="2y"):
    """Download OHLCV daily data from yfinance. Free, no rate limit."""
    try:
        df = yf.download(symbol, period=period)
        if df is not None and len(df) > 0:
            filepath = DATA_DIR / f"{symbol}.csv"
            df.to_csv(filepath)
            return df
    except Exception as e:
        print(f"  yfinance error for {symbol}: {e}")
    return None

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
    """Download fresh daily data from yfinance for all stocks."""
    print(f"  Data source: yfinance (daily candles)")
    data = {}
    for symbol in symbols:
        df = download_stock(symbol)
        if df is not None:
            data[symbol] = df
    print(f"  Downloaded: {len(data)} stocks")
    return data
    return data