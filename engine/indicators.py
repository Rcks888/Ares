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
    
    return df