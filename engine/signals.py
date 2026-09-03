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

def _count_confluence(signals_list):
    """Count how many confluence signals are True."""
    return sum(1 for s in signals_list if s)

def check_uptrend_signals(symbol, df, params):
    """UPTREND: Look for pullback buys and hidden bullish divergence continuation."""
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    rsi = float(latest['rsi'])
    vol_ok = latest['vol_ratio'] >= params['min_vol_ratio']

    hidden_bull = bool(latest.get('hidden_bull_div', False))
    pullback_buy = (40 <= rsi <= 50) and (float(prev['rsi']) < rsi)
    macd_bullish = latest['macd'] > latest['macd_signal']

    confluence = _count_confluence([hidden_bull, pullback_buy and vol_ok, macd_bullish])
    min_conf = params.get('min_confluence', 2)

    if confluence < min_conf:
        return None

    triggers = []
    if hidden_bull:
        triggers.append('hidden_bull_div')
    if pullback_buy:
        triggers.append('pullback_buy')
    if macd_bullish:
        triggers.append('macd_bullish')

    return {
        'symbol': symbol,
        'strategy': 'trend_continuation',
        'trigger': '+'.join(triggers),
        'regime': 'uptrend',
        'date': str(latest.name)[:10],
        'price': round(float(latest['Close']), 2),
        'rsi': round(rsi, 1),
        'vol_ratio': round(float(latest['vol_ratio']), 2),
        'stdev_20': round(float(latest['stdev_20']), 4),
        'confluence': confluence,
        'strength': 'high' if confluence >= 3 else 'medium'
    }

def check_range_signals(symbol, df, params):
    """RANGE: Mean reversion with confluence. RSI < 30 at support + divergence."""
    latest = df.iloc[-1]
    rsi = float(latest['rsi'])
    vol_ok = latest['vol_ratio'] >= params['min_vol_ratio']

    oversold = rsi < params['rsi_oversold']
    bullish_div = bool(latest.get('bullish_div', False))
    near_sma_support = float(latest['Close']) <= float(latest['sma_50']) * 1.02

    confluence = _count_confluence([oversold, bullish_div, vol_ok, near_sma_support])
    min_conf = params.get('min_confluence', 2)

    if confluence < min_conf:
        return None
    if not oversold and not bullish_div:
        return None

    triggers = []
    if oversold:
        triggers.append('oversold')
    if bullish_div:
        triggers.append('bullish_div')
    if vol_ok:
        triggers.append('volume')
    if near_sma_support:
        triggers.append('sma_support')

    return {
        'symbol': symbol,
        'strategy': 'mean_reversion',
        'trigger': '+'.join(triggers),
        'regime': 'range',
        'date': str(latest.name)[:10],
        'price': round(float(latest['Close']), 2),
        'rsi': round(rsi, 1),
        'vol_ratio': round(float(latest['vol_ratio']), 2),
        'stdev_20': round(float(latest['stdev_20']), 4),
        'confluence': confluence,
        'strength': 'high' if confluence >= 3 else 'medium'
    }

def check_momentum_breakout(symbol, df, params):
    """UPTREND: Breakout to new 52-week high, blocked by bearish divergence."""
    latest = df.iloc[-1]

    if latest['pct_from_high'] < -0.01:
        return None
    if latest['vol_ratio'] < params['min_vol_ratio']:
        return None
    if latest['macd'] < latest['macd_signal']:
        return None
    if bool(latest.get('bearish_div', False)):
        return None
    if bool(latest.get('hidden_bear_div', False)):
        return None

    return {
        'symbol': symbol,
        'strategy': 'momentum_breakout',
        'trigger': 'breakout',
        'regime': str(latest.get('regime', 'unknown')),
        'date': str(latest.name)[:10],
        'price': round(float(latest['Close']), 2),
        'rsi': round(float(latest['rsi']), 1),
        'vol_ratio': round(float(latest['vol_ratio']), 2),
        'stdev_20': round(float(latest['stdev_20']), 4),
        'confluence': 3,
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
    """Scan all stocks using regime-aware strategy. Ares V2."""
    params = load_strategy_params()
    watchlist = load_watchlist()

    if symbols is None:
        symbols = get_all_symbols(watchlist)

    signals = []
    regime_counts = {'uptrend': 0, 'downtrend': 0, 'range': 0}

    for symbol in symbols:
        try:
            df = load_stock(symbol)
            df = add_indicators(df)
            latest = df.iloc[-1]
            regime = str(latest.get('regime', 'range'))
            regime_counts[regime] = regime_counts.get(regime, 0) + 1

            signal = None

            if regime == 'uptrend':
                signal = check_momentum_breakout(symbol, df, params)
                if not signal:
                    signal = check_uptrend_signals(symbol, df, params)
            elif regime == 'range':
                signal = check_range_signals(symbol, df, params)
            elif regime == 'downtrend':
                if bool(latest.get('bullish_div', False)):
                    print(f"  {symbol}: Bullish divergence in downtrend — WATCHLIST only")

            if signal:
                signal['category'] = get_symbol_category(symbol, watchlist)
                signals.append(signal)
        except Exception as e:
            print(f"Error scanning {symbol}: {e}")

    print(f"\n  Market Regimes: {regime_counts['uptrend']} uptrend | "
          f"{regime_counts['range']} range | {regime_counts['downtrend']} downtrend")

    return signals
