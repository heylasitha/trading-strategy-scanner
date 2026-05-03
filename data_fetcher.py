from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, CRYPTO, TIMEFRAMES, MIN_BARS

# How far back to fetch data per timeframe to ensure 200+ bars
TF_LOOKBACK_DAYS = {
    "15m": 90,
    "30m": 120,
    "1h":  365,
    "2h":  365,
    "4h":  730,
    "1d":  1500,
    "1w":  2000,
}

log = logging.getLogger(__name__)

ALPACA_STOCK_URL  = "https://data.alpaca.markets/v2/stocks"
ALPACA_CRYPTO_URL = "https://data.alpaca.markets/v1beta3/crypto/us"

ALPACA_TF_MAP = {
    "15m": "15Min",
    "30m": "30Min",
    "1h":  "1Hour",
    "2h":  "2Hour",
    "4h":  "4Hour",
    "1d":  "1Day",
    "1w":  "1Week",
}

# Alpaca crypto uses BTC/USD format
CRYPTO_SYMBOL_MAP = {
    "BTC-USD": "BTC/USD",
    "ETH-USD": "ETH/USD",
    "LTC-USD": "LTC/USD",
}


def _headers() -> dict:
    return {
        "APCA-API-KEY-ID":     ALPACA_API_KEY    or "",
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY or "",
    }


def _parse_bars(bars: list) -> pd.DataFrame:
    df = pd.DataFrame(bars)
    df = df.rename(columns={"t": "datetime", "o": "open", "h": "high",
                             "l": "low",      "c": "close", "v": "volume"})
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.set_index("datetime")
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df.sort_index(inplace=True)
    return df


def _fetch_alpaca(symbol: str, tf_label: str) -> pd.DataFrame | None:
    alpaca_tf = ALPACA_TF_MAP.get(tf_label)
    if not alpaca_tf:
        return None

    is_crypto = symbol in CRYPTO
    alpaca_symbol = CRYPTO_SYMBOL_MAP.get(symbol, symbol) if is_crypto else symbol

    lookback = TF_LOOKBACK_DAYS.get(tf_label, 365)
    start    = (datetime.now(timezone.utc) - timedelta(days=lookback)).strftime("%Y-%m-%d")

    if is_crypto:
        url    = f"{ALPACA_CRYPTO_URL}/bars"
        params = {"symbols": alpaca_symbol, "timeframe": alpaca_tf, "limit": 1000, "sort": "asc", "start": start}
    else:
        url    = f"{ALPACA_STOCK_URL}/{symbol}/bars"
        params = {"timeframe": alpaca_tf, "limit": 1000, "adjustment": "split", "feed": "iex", "sort": "asc", "start": start}

    try:
        resp = requests.get(url, headers=_headers(), params=params, timeout=15)
        if resp.status_code != 200:
            log.error(f"Alpaca {symbol} {tf_label}: {resp.status_code} {resp.text[:200]}")
            return None

        data = resp.json()
        if is_crypto:
            bars = (data.get("bars") or {}).get(alpaca_symbol, [])
        else:
            bars = data.get("bars") or []

        if not bars:
            log.debug(f"Alpaca {symbol} {tf_label}: no bars returned")
            return None

        return _parse_bars(bars)

    except Exception as e:
        log.error(f"Alpaca fetch failed {symbol} {tf_label}: {e}")
        return None


def fetch_ohlcv(symbol: str, tf_label: str) -> pd.DataFrame | None:
    df = _fetch_alpaca(symbol, tf_label)

    if df is None or df.empty:
        return None
    if len(df) < MIN_BARS:
        log.debug(f"{symbol} {tf_label}: only {len(df)} bars (need {MIN_BARS})")
        return None

    return df


def fetch_all_timeframes(symbol: str) -> dict[str, pd.DataFrame]:
    results: dict[str, pd.DataFrame] = {}
    for tf_label, _, _, _ in TIMEFRAMES:
        df = fetch_ohlcv(symbol, tf_label)
        if df is not None:
            results[tf_label] = df
            log.debug(f"  {symbol} {tf_label}: {len(df)} bars")
        time.sleep(0.2)
    return results
