import json
from pathlib import Path
from engine.data_feed import load_stock
from engine.indicators import add_indicators

def load_strategy_params():
    config_path = Path(__file__).parent.parent / "config" / "strategy_params.json"
    with open(config_path) as f:
        return json.load(f)

def load_watchlist():
    watchlist_path = Path(__file__).parent.parent / "config" / "watchlist.json"
    with open(watchlist_path) as f:
        return json.load(f)

def check_rsi_reversal(symbol, df, params):
    """Check if stock shows RSI mean-reversion signal.
    Triggers on either:
      1. RSI drops below oversold threshold (original)
      2. Bullish divergence detected (price lower low + RSI higher low)
    """
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    oversold_trigger = (
        latest['rsi'] < params['rsi_oversold']
        and prev['rsi'] >= params['rsi_oversold']
        and latest['vol_ratio'] >= params['min_vol_ratio']
    )

    divergence_trigger = (
        bool(latest.get('bullish_divergence', False))
        and latest['rsi'] < 50
        and latest['vol_ratio'] >= params['min_vol_ratio']
    )

    if not oversold_trigger and not divergence_trigger:
        return None

    if divergence_trigger and not oversold_trigger:
        trigger_type = 'bullish_divergence'
        strength = 'high'
    elif oversold_trigger and divergence_trigger:
        trigger_type = 'oversold+divergence'
        strength = 'high'
    else:
        trigger_type = 'oversold'
        strength = 'high' if latest['rsi'] < 25 else 'medium'

    return {
        'symbol': symbol,
        'strategy': 'rsi_reversal',
        'trigger': trigger_type,
        'date': str(latest.name)[:10],
        'price': round(float(latest['Close']), 2),
        'rsi': round(float(latest['rsi']), 1),
        'vol_ratio': round(float(latest['vol_ratio']), 2),
        'stdev_20': round(float(latest['stdev_20']), 4),
        'strength': strength
    }

def check_momentum_breakout(symbol, df, params):
    """Check if stock is breaking to new 52-week high on volume.
    Rejects signal if bearish divergence is detected (weakening momentum).
    """
    latest = df.iloc[-1]

    if latest['pct_from_high'] < -0.01:
        return None
    if latest['vol_ratio'] < params['min_vol_ratio']:
        return None
    if latest['macd'] < latest['macd_signal']:
        return None
    if bool(latest.get('bearish_divergence', False)):
        return None

    return {
        'symbol': symbol,
        'strategy': 'momentum_breakout',
        'trigger': 'breakout',
        'date': str(latest.name)[:10],
        'price': round(float(latest['Close']), 2),
        'rsi': round(float(latest['rsi']), 1),
        'vol_ratio': round(float(latest['vol_ratio']), 2),
        'stdev_20': round(float(latest['stdev_20']), 4),
        'strength': 'high' if latest['vol_ratio'] > 3.0 else 'medium'
    }

def get_symbol_category(symbol, watchlist):
    """Find which category a symbol belongs to."""
    for cat_name, cat_data in watchlist['categories'].items():
        if symbol in cat_data['symbols']:
            return cat_name
    return 'unknown'

def get_all_symbols(watchlist):
    """Get flat list of all symbols from all categories."""
    symbols = []
    for cat_data in watchlist['categories'].values():
        symbols.extend(cat_data['symbols'])
    return list(set(symbols))

def scan_universe(symbols=None):
    """Scan all stocks in watchlist for signals."""
    params = load_strategy_params()
    watchlist = load_watchlist()

    if symbols is None:
        symbols = get_all_symbols(watchlist)

    signals = []
    for symbol in symbols:
        try:
            df = load_stock(symbol)
            df = add_indicators(df)

            signal = check_rsi_reversal(symbol, df, params)
            if signal:
                signal['category'] = get_symbol_category(symbol, watchlist)
                signals.append(signal)
                continue

            signal = check_momentum_breakout(symbol, df, params)
            if signal:
                signal['category'] = get_symbol_category(symbol, watchlist)
                signals.append(signal)
        except Exception as e:
            print(f"Error scanning {symbol}: {e}")

    return signals
