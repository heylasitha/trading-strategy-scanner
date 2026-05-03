from __future__ import annotations

import logging
import time

import pandas as pd
import requests

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, CRYPTO, TIMEFRAMES, MIN_BARS

log = logging.getLogger(__name__)

ALPACA_DATA_URL = "https://data.alpaca.markets/v2/stocks"
BINANCE_URL     = "https://api.binance.com/api/v3/klines"

# Alpaca supports native 2Hour and 4Hour bars — no resampling needed
ALPACA_TF_MAP = {
    "15m": "15Min",
    "30m": "30Min",
    "1h":  "1Hour",
    "2h":  "2Hour",
    "4h":  "4Hour",
    "1d":  "1Day",
    "1w":  "1Week",
}

BINANCE_SYMBOL_MAP = {
    "BTC-USD": "BTCUSDT",
    "ETH-USD": "ETHUSDT",
    "LTC-USD": "LTCUSDT",
}

BINANCE_TF_MAP = {
    "15m": "15m",
    "30m": "30m",
    "1h":  "1h",
    "2h":  "2h",
    "4h":  "4h",
    "1d":  "1d",
    "1w":  "1w",
}


def _alpaca_headers() -> dict:
    return {
        "APCA-API-KEY-ID":     ALPACA_API_KEY    or "",
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY or "",
    }


def _fetch_alpaca(symbol: str, tf_label: str) -> pd.DataFrame | None:
    alpaca_tf = ALPACA_TF_MAP.get(tf_label)
    if not alpaca_tf:
        return None

    params = {
        "timeframe":  alpaca_tf,
        "limit":      1000,
        "adjustment": "split",
        "feed":       "iex",
        "sort":       "asc",
    }

    try:
        resp = requests.get(
            f"{ALPACA_DATA_URL}/{symbol}/bars",
            headers=_alpaca_headers(),
            params=params,
            timeout=15,
        )
        if resp.status_code != 200:
            log.error(f"Alpaca {symbol} {tf_label}: {resp.status_code} {resp.text[:200]}")
            return None

        bars = resp.json().get("bars") or []
        if not bars:
            log.debug(f"Alpaca {symbol} {tf_label}: no bars returned")
            return None

        df = pd.DataFrame(bars)
        df = df.rename(columns={"t": "datetime", "o": "open", "h": "high",
                                 "l": "low",      "c": "close", "v": "volume"})
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        df = df.set_index("datetime")
        df = df[["open", "high", "low", "close", "volume"]].astype(float)
        df.sort_index(inplace=True)
        return df

    except Exception as e:
        log.error(f"Alpaca fetch failed {symbol} {tf_label}: {e}")
        return None


def _fetch_binance(symbol: str, tf_label: str) -> pd.DataFrame | None:
    binance_symbol = BINANCE_SYMBOL_MAP.get(symbol)
    binance_tf     = BINANCE_TF_MAP.get(tf_label)
    if not binance_symbol or not binance_tf:
        return None

    params = {
        "symbol":   binance_symbol,
        "interval": binance_tf,
        "limit":    1000,
    }

    try:
        resp = requests.get(BINANCE_URL, params=params, timeout=15)
        if resp.status_code != 200:
            log.error(f"Binance {symbol} {tf_label}: {resp.status_code}")
            return None

        raw = resp.json()
        if not raw:
            return None

        df = pd.DataFrame(raw, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore",
        ])
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df = df.set_index("open_time")
        df = df[["open", "high", "low", "close", "volume"]].astype(float)
        df.sort_index(inplace=True)
        # Drop the last bar — it's still forming
        df = df.iloc[:-1]
        return df

    except Exception as e:
        log.error(f"Binance fetch failed {symbol} {tf_label}: {e}")
        return None


def fetch_ohlcv(symbol: str, tf_label: str) -> pd.DataFrame | None:
    df = _fetch_binance(symbol, tf_label) if symbol in CRYPTO else _fetch_alpaca(symbol, tf_label)

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
