import pandas as pd
import pandas_ta as ta

def add_indicators(df):
    """Add all technical indicators to a DataFrame."""
    df['rsi'] = ta.rsi(df['Close'], length=14)

    df['vol_avg_20'] = df['Volume'].rolling(window=20).mean()
    df['vol_ratio'] = df['Volume'] / df['vol_avg_20']

    macd = ta.macd(df['Close'], fast=12, slow=26, signal=9)
    df['macd'] = macd.iloc[:, 0]
    df['macd_signal'] = macd.iloc[:, 1]
    df['macd_hist'] = macd.iloc[:, 2]

    df['stdev_20'] = df['Close'].pct_change().rolling(20).std()

    df['high_52w'] = df['High'].rolling(252).max()
    df['pct_from_high'] = (df['Close'] - df['high_52w']) / df['high_52w']

    df['bullish_divergence'] = detect_bullish_divergence(df)
    df['bearish_divergence'] = detect_bearish_divergence(df)

    return df

def detect_bullish_divergence(df, lookback=14):
    """Detect bullish divergence: price makes lower low, RSI makes higher low."""
    result = pd.Series(False, index=df.index)
    if len(df) < lookback * 2:
        return result

    close = df['Close']
    rsi = df['rsi']

    for i in range(lookback * 2, len(df)):
        recent_close = close.iloc[i - lookback:i + 1]
        recent_rsi = rsi.iloc[i - lookback:i + 1]
        prior_close = close.iloc[i - lookback * 2:i - lookback + 1]
        prior_rsi = rsi.iloc[i - lookback * 2:i - lookback + 1]

        if recent_rsi.isna().any() or prior_rsi.isna().any():
            continue

        recent_low_price = recent_close.min()
        prior_low_price = prior_close.min()
        recent_low_rsi = recent_rsi.min()
        prior_low_rsi = prior_rsi.min()

        if recent_low_price < prior_low_price and recent_low_rsi > prior_low_rsi:
            result.iloc[i] = True

    return result

def detect_bearish_divergence(df, lookback=14):
    """Detect bearish divergence: price makes higher high, RSI makes lower high."""
    result = pd.Series(False, index=df.index)
    if len(df) < lookback * 2:
        return result

    close = df['Close']
    rsi = df['rsi']

    for i in range(lookback * 2, len(df)):
        recent_close = close.iloc[i - lookback:i + 1]
        recent_rsi = rsi.iloc[i - lookback:i + 1]
        prior_close = close.iloc[i - lookback * 2:i - lookback + 1]
        prior_rsi = rsi.iloc[i - lookback * 2:i - lookback + 1]

        if recent_rsi.isna().any() or prior_rsi.isna().any():
            continue

        recent_high_price = recent_close.max()
        prior_high_price = prior_close.max()
        recent_high_rsi = recent_rsi.max()
        prior_high_rsi = prior_rsi.max()

        if recent_high_price > prior_high_price and recent_high_rsi < prior_high_rsi:
            result.iloc[i] = True

    return result