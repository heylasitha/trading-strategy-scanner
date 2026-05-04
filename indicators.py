from __future__ import annotations

import numpy as np
import pandas as pd

from config import (
    SMA_FAST, SMA_MID, SMA_SLOW,
    STOCH_K, STOCH_D, STOCH_SMOOTH,
    ADX_PERIOD, VOLUME_LOOKBACK,
)


def add_sma(df: pd.DataFrame) -> pd.DataFrame:
    df["sma20"]  = df["close"].rolling(SMA_FAST).mean()
    df["sma50"]  = df["close"].rolling(SMA_MID).mean()
    df["sma200"] = df["close"].rolling(SMA_SLOW).mean()
    return df


def add_stochastic(df: pd.DataFrame) -> pd.DataFrame:
    low_min  = df["low"].rolling(STOCH_K).min()
    high_max = df["high"].rolling(STOCH_K).max()
    denom    = high_max - low_min

    raw_k = pd.Series(
        np.where(denom == 0, 50.0, 100.0 * (df["close"] - low_min) / denom),
        index=df.index,
    )
    df["stoch_k"] = raw_k.rolling(STOCH_SMOOTH).mean()   # Smoothed %K
    df["stoch_d"] = df["stoch_k"].rolling(STOCH_D).mean() # Signal line %D
    return df


def add_adx(df: pd.DataFrame) -> pd.DataFrame:
    """Average Directional Index — measures trend strength (not direction)."""
    high  = df["high"]
    low   = df["low"]
    close = df["close"]

    prev_high  = high.shift(1)
    prev_low   = low.shift(1)
    prev_close = close.shift(1)

    # True Range
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Directional Movement
    up_move   = high - prev_high
    down_move = prev_low - low

    plus_dm  = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_dm_s  = pd.Series(plus_dm,  index=df.index).rolling(ADX_PERIOD).sum()
    minus_dm_s = pd.Series(minus_dm, index=df.index).rolling(ADX_PERIOD).sum()
    atr_s      = tr.rolling(ADX_PERIOD).sum()

    plus_di  = 100 * plus_dm_s  / atr_s.replace(0, np.nan)
    minus_di = 100 * minus_dm_s / atr_s.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["adx"]      = dx.rolling(ADX_PERIOD).mean()
    df["plus_di"]  = plus_di
    df["minus_di"] = minus_di
    return df


def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    delta    = df["close"].diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


def add_volume_ratio(df: pd.DataFrame) -> pd.DataFrame:
    """Ratio of current volume to rolling average — >1.5 means strong volume."""
    avg_vol = df["volume"].rolling(VOLUME_LOOKBACK).mean()
    df["volume_ratio"] = df["volume"] / avg_vol.replace(0, np.nan)
    return df


def add_vwap(df: pd.DataFrame) -> pd.DataFrame:
    """
    VWAP with +1/-1 Standard Deviation bands.
    Resets each day — groups by date for correct daily calculation.
    """
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    tp_vol = typical_price * df["volume"]

    # Group by date for daily reset
    dates = df.index.normalize()
    cum_tp_vol = tp_vol.groupby(dates).cumsum()
    cum_vol    = df["volume"].groupby(dates).cumsum()

    vwap = cum_tp_vol / cum_vol.replace(0, np.nan)

    # Standard deviation bands
    squared_diff = (typical_price - vwap) ** 2
    cum_sq = (squared_diff * df["volume"]).groupby(dates).cumsum()
    variance = cum_sq / cum_vol.replace(0, np.nan)
    std_dev  = np.sqrt(variance)

    df["vwap"]       = vwap
    df["vwap_upper"] = vwap + std_dev        # +1 SD
    df["vwap_lower"] = vwap - std_dev        # -1 SD

    return df


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df = add_sma(df)
    df = add_rsi(df)
    df = add_adx(df)
    df = add_volume_ratio(df)
    return df


def add_all_indicators_with_vwap(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df = add_sma(df)
    df = add_rsi(df)
    df = add_adx(df)
    df = add_volume_ratio(df)
    df = add_vwap(df)
    return df
