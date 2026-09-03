import yfinance as yf
import pandas as pd
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(__file__).parent.parent / "data" / "ohlcv"
DATA_DIR.mkdir(parents=True, exist_ok=True)

IBKR_PORT = 4002
IBKR_HOST = '127.0.0.1'

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

def download_stock_ibkr(symbol, duration="2 Y"):
    """Download OHLCV data from IBKR."""
    ib = _get_ib()
    if not ib:
        return None

    try:
        from ib_insync import Stock
        contract = Stock(symbol, 'SMART', 'USD')
        ib.qualifyContracts(contract)
        bars = ib.reqHistoricalData(
            contract,
            endDateTime='',
            durationStr=duration,
            barSizeSetting='1 day',
            whatToShow='TRADES',
            useRTH=True,
            timeout=30
        )
        if not bars:
            return None

        data = [{
            'Date': bar.date,
            'Open': bar.open,
            'High': bar.high,
            'Low': bar.low,
            'Close': bar.close,
            'Volume': bar.volume
        } for bar in bars]

        df = pd.DataFrame(data)
        df['Date'] = pd.to_datetime(df['Date'])
        df.set_index('Date', inplace=True)

        filepath = DATA_DIR / f"{symbol}.csv"
        df.to_csv(filepath)
        return df
    except Exception as e:
        print(f"  IBKR error for {symbol}: {e}")
        return None

def download_stock_yfinance(symbol, period="2y"):
    """Download OHLCV data from yfinance (fallback)."""
    df = yf.download(symbol, period=period)
    filepath = DATA_DIR / f"{symbol}.csv"
    df.to_csv(filepath)
    return df

def download_stock(symbol, period="2y"):
    """Download OHLCV data. Try IBKR first, fallback to yfinance."""
    df = download_stock_ibkr(symbol)
    if df is not None and len(df) > 50:
        return df
    return download_stock_yfinance(symbol, period)

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
    """Download fresh data for all stocks. IBKR first, yfinance fallback."""
    ib = _get_ib()
    source = "IBKR" if ib else "yfinance"
    print(f"  Data source: {source}")

    data = {}
    ibkr_count = 0
    yf_count = 0

    for symbol in symbols:
        df = download_stock_ibkr(symbol) if ib else None
        if df is not None and len(df) > 50:
            data[symbol] = df
            ibkr_count += 1
        else:
            data[symbol] = download_stock_yfinance(symbol)
            yf_count += 1

    print(f"  Downloaded: {ibkr_count} from IBKR, {yf_count} from yfinance")
    return data