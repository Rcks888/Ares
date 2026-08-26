import json
from pathlib import Path
from engine.data_feed import load_stock
from engine.indicators import add_indicators

def load_strategy_params():
    config_path = Path(__file__).parent.parent / "config" / "strategy_params.json"
    with open(config_path) as f:
        return json.load(f)
    
def check_rsi_reversal(symbol, df, params):
    """Check if stock shows RSI mean-reversion signal."""
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    if latest['rsi'] >= params['rsi_oversold']:
        return None
    if latest['vol_ratio'] < params['min_vol_ratio']:
        return None
    if prev['rsi'] < params['rsi_oversold']:
        return None
    
    return {
        'symbol': symbol,
        'strategy': 'rsi_reversal',
        'date': str(latest.name)[:10],
        'price': round(float(latest['Close']), 2),
        'rsi': round(float(latest['rsi']), 1),
        'vol_ratio': round(float(latest['vol_ratio']), 2),
        'stdev_20': round(float(latest['stdev_20']), 4),
        'strength': 'high' if latest['rsi'] < 25 else 'medium'
    }

def check_momentum_breakout(symbol, df, params):
    """Check if stock is breaking to new 52-week high on volume."""
    latest = df.iloc[-1]

    if latest['pct_from_high'] < -0.01:
        return None
    if latest['vol_ratio'] < params['min_vol_ratio']:
        return None
    if latest['macd'] < latest['macd_signal']:
        return None
    
    return {
        'symbol': symbol,
        'strategy': 'momentum_breakout',
        'date': str(latest.name)[:10],
        'price': round(float(latest['Close']), 2),
        'rsi': round(float(latest['rsi']), 1),
        'vol_ratio': round(float(latest['vol_ratio']), 2),
        'stdev_20': round(float(latest['stdev_20']), 4),
        'strength': 'high' if latest['vol_ratio'] > 3.0 else 'medium'
    }

def scan_universe(symbols=None):
    """Scan all stocks in watchlist for signals."""
    params = load_strategy_params()

    if symbols is None:
        watchlist_path = Path(__file__).parent.parent / "config" / "watchlist.json"
        with open(watchlist_path) as f:
            symbols = json.load(f)['symbols']

    signals = []
    for symbol in symbols:
        try:
            df = load_stock(symbol)
            df = add_indicators(df)
            signal = check_rsi_reversal(symbol, df, params)
            if signal:
                signals.append(signal)
                continue

            signal = check_momentum_breakout(symbol, df, params)
            if signal:
               signals.append(signal)
        except Exception as e:
            print(f"Error scanning {symbol}: {e}")
            
    return signals