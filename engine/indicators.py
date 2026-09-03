import pandas as pd
import pandas_ta as ta
import json
from pathlib import Path

def load_params():
    config_path = Path(__file__).parent.parent / "config" / "strategy_params.json"
    with open(config_path) as f:
        return json.load(f)

def add_indicators(df):
    """Add all technical indicators to a DataFrame. Ares V2."""
    params = load_params()
    rsi_len = params.get('rsi_length', 21)

    ohlc4 = (df['Open'] + df['High'] + df['Low'] + df['Close']) / 4
    df['ohlc4'] = ohlc4

    df['rsi'] = ta.rsi(ohlc4, length=rsi_len)

    df['vol_avg_20'] = df['Volume'].rolling(window=20).mean()
    df['vol_ratio'] = df['Volume'] / df['vol_avg_20']

    macd = ta.macd(df['Close'], fast=12, slow=26, signal=9)
    df['macd'] = macd.iloc[:, 0]
    df['macd_hist'] = macd.iloc[:, 1]
    df['macd_signal'] = macd.iloc[:, 2]

    df['stdev_20'] = df['Close'].pct_change().rolling(20).std()

    df['high_52w'] = df['High'].rolling(252).max()
    df['pct_from_high'] = (df['Close'] - df['high_52w']) / df['high_52w']

    sma_len = params.get('sma_trend_length', 50)
    df['sma_50'] = df['Close'].rolling(sma_len).mean()
    df['sma_slope'] = (df['sma_50'] - df['sma_50'].shift(5)) / df['sma_50'].shift(5)

    df['regime'] = detect_market_regime(df, params)

    df['bullish_div'] = detect_bullish_divergence(df, rsi_len)
    df['bearish_div'] = detect_bearish_divergence(df, rsi_len)
    df['hidden_bull_div'] = detect_hidden_bullish_divergence(df, rsi_len)
    df['hidden_bear_div'] = detect_hidden_bearish_divergence(df, rsi_len)

    return df

def detect_market_regime(df, params):
    """Classify market as UPTREND, DOWNTREND, or RANGE."""
    result = pd.Series('range', index=df.index)
    threshold = params.get('sma_slope_threshold', 0.001)

    if 'sma_slope' not in df.columns:
        return result

    for i in range(len(df)):
        slope = df['sma_slope'].iloc[i]
        if pd.isna(slope):
            continue

        close = df['Close'].iloc[i]
        sma = df['sma_50'].iloc[i]
        if pd.isna(sma):
            continue

        if slope > threshold and close > sma:
            result.iloc[i] = 'uptrend'
        elif slope < -threshold and close < sma:
            result.iloc[i] = 'downtrend'
        else:
            result.iloc[i] = 'range'

    return result

def _find_swing_lows(series, window=5):
    """Find local minima indices."""
    lows = []
    for i in range(window, len(series) - window):
        if pd.isna(series.iloc[i]):
            continue
        is_low = True
        for j in range(1, window + 1):
            if series.iloc[i] >= series.iloc[i - j] or series.iloc[i] >= series.iloc[i + j]:
                is_low = False
                break
        if is_low:
            lows.append(i)
    return lows

def _find_swing_highs(series, window=5):
    """Find local maxima indices."""
    highs = []
    for i in range(window, len(series) - window):
        if pd.isna(series.iloc[i]):
            continue
        is_high = True
        for j in range(1, window + 1):
            if series.iloc[i] <= series.iloc[i - j] or series.iloc[i] <= series.iloc[i + j]:
                is_high = False
                break
        if is_high:
            highs.append(i)
    return highs

def detect_bullish_divergence(df, lookback=21):
    """Regular bullish: price lower low + RSI higher low → reversal UP."""
    result = pd.Series(False, index=df.index)
    if len(df) < lookback * 3:
        return result

    price_lows = _find_swing_lows(df['Close'])
    rsi_lows = _find_swing_lows(df['rsi'])

    for i in range(1, len(price_lows)):
        p_prev, p_curr = price_lows[i - 1], price_lows[i]
        if p_curr - p_prev > lookback * 2 or p_curr - p_prev < 3:
            continue
        if df['Close'].iloc[p_curr] < df['Close'].iloc[p_prev]:
            r_prev_candidates = [r for r in rsi_lows if abs(r - p_prev) <= 3]
            r_curr_candidates = [r for r in rsi_lows if abs(r - p_curr) <= 3]
            if r_prev_candidates and r_curr_candidates:
                r_prev = r_prev_candidates[0]
                r_curr = r_curr_candidates[0]
                if df['rsi'].iloc[r_curr] > df['rsi'].iloc[r_prev]:
                    result.iloc[p_curr] = True

    return result

def detect_bearish_divergence(df, lookback=21):
    """Regular bearish: price higher high + RSI lower high → reversal DOWN."""
    result = pd.Series(False, index=df.index)
    if len(df) < lookback * 3:
        return result

    price_highs = _find_swing_highs(df['Close'])
    rsi_highs = _find_swing_highs(df['rsi'])

    for i in range(1, len(price_highs)):
        p_prev, p_curr = price_highs[i - 1], price_highs[i]
        if p_curr - p_prev > lookback * 2 or p_curr - p_prev < 3:
            continue
        if df['Close'].iloc[p_curr] > df['Close'].iloc[p_prev]:
            r_prev_candidates = [r for r in rsi_highs if abs(r - p_prev) <= 3]
            r_curr_candidates = [r for r in rsi_highs if abs(r - p_curr) <= 3]
            if r_prev_candidates and r_curr_candidates:
                r_prev = r_prev_candidates[0]
                r_curr = r_curr_candidates[0]
                if df['rsi'].iloc[r_curr] < df['rsi'].iloc[r_prev]:
                    result.iloc[p_curr] = True

    return result

def detect_hidden_bullish_divergence(df, lookback=21):
    """Hidden bullish: price higher low + RSI lower low → continuation UP."""
    result = pd.Series(False, index=df.index)
    if len(df) < lookback * 3:
        return result

    price_lows = _find_swing_lows(df['Close'])
    rsi_lows = _find_swing_lows(df['rsi'])

    for i in range(1, len(price_lows)):
        p_prev, p_curr = price_lows[i - 1], price_lows[i]
        if p_curr - p_prev > lookback * 2 or p_curr - p_prev < 3:
            continue
        if df['Close'].iloc[p_curr] > df['Close'].iloc[p_prev]:
            r_prev_candidates = [r for r in rsi_lows if abs(r - p_prev) <= 3]
            r_curr_candidates = [r for r in rsi_lows if abs(r - p_curr) <= 3]
            if r_prev_candidates and r_curr_candidates:
                r_prev = r_prev_candidates[0]
                r_curr = r_curr_candidates[0]
                if df['rsi'].iloc[r_curr] < df['rsi'].iloc[r_prev]:
                    result.iloc[p_curr] = True

    return result

def detect_hidden_bearish_divergence(df, lookback=21):
    """Hidden bearish: price lower high + RSI higher high → continuation DOWN."""
    result = pd.Series(False, index=df.index)
    if len(df) < lookback * 3:
        return result

    price_highs = _find_swing_highs(df['Close'])
    rsi_highs = _find_swing_highs(df['rsi'])

    for i in range(1, len(price_highs)):
        p_prev, p_curr = price_highs[i - 1], price_highs[i]
        if p_curr - p_prev > lookback * 2 or p_curr - p_prev < 3:
            continue
        if df['Close'].iloc[p_curr] < df['Close'].iloc[p_prev]:
            r_prev_candidates = [r for r in rsi_highs if abs(r - p_prev) <= 3]
            r_curr_candidates = [r for r in rsi_highs if abs(r - p_curr) <= 3]
            if r_prev_candidates and r_curr_candidates:
                r_prev = r_prev_candidates[0]
                r_curr = r_curr_candidates[0]
                if df['rsi'].iloc[r_curr] > df['rsi'].iloc[r_prev]:
                    result.iloc[p_curr] = True

    return result