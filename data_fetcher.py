from __future__ import annotations

import time
import logging
import yfinance as yf
import pandas as pd

from config import TIMEFRAMES, MIN_BARS

log = logging.getLogger(__name__)

# How long to wait between yfinance requests (avoids rate limiting)
REQUEST_DELAY = 1.0


def _resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    df = df.resample(rule).agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }).dropna()
    return df


def fetch_ohlcv(symbol: str, tf_label: str) -> pd.DataFrame | None:
    """
    Fetch OHLCV data for a symbol on a given timeframe label (e.g. '15m', '1h').
    Returns a DataFrame with lowercase columns: open, high, low, close, volume.
    Returns None if data is unavailable or insufficient.
    """
    # Find the timeframe config
    tf_config = next((t for t in TIMEFRAMES if t[0] == tf_label), None)
    if tf_config is None:
        log.warning(f"Unknown timeframe label: {tf_label}")
        return None

    label, interval, period, resample_rule = tf_config

    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval, auto_adjust=True)

        if df is None or df.empty:
            log.debug(f"{symbol} {label}: no data returned")
            return None

        # Standardise column names
        df.columns = [c.lower() for c in df.columns]
        df = df[["open", "high", "low", "close", "volume"]].copy()

        # Normalise index to UTC-naive datetime
        if df.index.tz is not None:
            df.index = df.index.tz_convert("UTC").tz_localize(None)
        df.index = pd.to_datetime(df.index)
        df.sort_index(inplace=True)
        df.dropna(inplace=True)

        # Resample if needed (2h / 4h)
        if resample_rule:
            df = _resample_ohlcv(df, resample_rule)

        if len(df) < MIN_BARS:
            log.debug(f"{symbol} {label}: only {len(df)} bars (need {MIN_BARS})")
            return None

        time.sleep(REQUEST_DELAY)
        return df

    except Exception as e:
        log.warning(f"fetch_ohlcv failed for {symbol} {label}: {e}")
        return None


def fetch_all_timeframes(symbol: str) -> dict[str, pd.DataFrame]:
    """
    Fetch OHLCV for all configured timeframes for a symbol.
    Returns a dict of {tf_label: DataFrame}.
    Skips timeframes where data is unavailable.
    """
    results: dict[str, pd.DataFrame] = {}

    # Optimisation: fetch 1h data once and reuse for 2h and 4h
    h1_df: pd.DataFrame | None = None

    for label, interval, period, resample_rule in TIMEFRAMES:
        if resample_rule and interval == "1h":
            # Reuse cached 1h data
            if h1_df is None:
                h1_df = fetch_ohlcv(symbol, "1h")
            if h1_df is not None:
                resampled = _resample_ohlcv(h1_df, resample_rule)
                if len(resampled) >= MIN_BARS:
                    results[label] = resampled
        else:
            df = fetch_ohlcv(symbol, label)
            if df is not None:
                results[label] = df
                if label == "1h":
                    h1_df = df  # Cache for 2h/4h resampling

    return results
