from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, CRYPTO, TIMEFRAMES, MIN_BARS

log = logging.getLogger(__name__)

# ── Alpaca (stocks) ───────────────────────────────────────────────────────────
ALPACA_STOCK_URL = "https://data.alpaca.markets/v2/stocks"

ALPACA_TF_MAP = {
    "15m": "15Min",
    "30m": "30Min",
    "1h":  "1Hour",
    "2h":  "2Hour",
    "4h":  "4Hour",
    "1d":  "1Day",
    "1w":  "1Week",
}

TF_LOOKBACK_DAYS = {
    "15m": 90,
    "30m": 120,
    "1h":  365,
    "2h":  365,
    "4h":  730,
    "1d":  1500,
    "1w":  2000,
}

# ── Binance (crypto) — full multi-year history, free ─────────────────────────
BINANCE_URL = "https://api.binance.us/api/v3/klines"

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

    lookback = TF_LOOKBACK_DAYS.get(tf_label, 365)
    start    = (datetime.now(timezone.utc) - timedelta(days=lookback)).strftime("%Y-%m-%d")

    params = {
        "timeframe":  alpaca_tf,
        "limit":      1000,
        "adjustment": "split",
        "feed":       "iex",
        "sort":       "asc",
        "start":      start,
    }

    try:
        resp = requests.get(
            f"{ALPACA_STOCK_URL}/{symbol}/bars",
            headers=_alpaca_headers(),
            params=params,
            timeout=15,
        )
        if resp.status_code != 200:
            log.error(f"Alpaca {symbol} {tf_label}: {resp.status_code} {resp.text[:200]}")
            return None

        bars = resp.json().get("bars") or []
        if not bars:
            log.info(f"Alpaca {symbol} {tf_label}: no bars returned")
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

    rows     = []
    end_time = None   # None = fetch most recent bars first

    for _ in range(5):   # max 5 pages × 1000 bars = 5000 bars
        params = {
            "symbol":   binance_symbol,
            "interval": binance_tf,
            "limit":    1000,
        }
        if end_time is not None:
            params["endTime"] = end_time

        try:
            resp = requests.get(BINANCE_URL, params=params, timeout=15)
            if resp.status_code != 200:
                log.error(f"Binance {symbol} {tf_label}: {resp.status_code}")
                break
            raw = resp.json()
            if not raw:
                break
        except Exception as e:
            log.error(f"Binance fetch failed {symbol} {tf_label}: {e}")
            break

        rows     = raw + rows          # prepend older data
        if len(rows) >= MIN_BARS + 50: # enough — stop paging
            break

        end_time = raw[0][0] - 1       # 1 ms before oldest bar → go further back
        time.sleep(0.1)

    if not rows:
        return None

    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("open_time")
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df = df[~df.index.duplicated(keep="last")]
    df.sort_index(inplace=True)
    df = df.iloc[:-1]   # drop last incomplete candle
    return df


def fetch_ohlcv(symbol: str, tf_label: str) -> pd.DataFrame | None:
    df = _fetch_binance(symbol, tf_label) if symbol in CRYPTO else _fetch_alpaca(symbol, tf_label)

    if df is None or df.empty:
        return None
    if len(df) < MIN_BARS:
        log.info(f"{symbol} {tf_label}: only {len(df)} bars (need {MIN_BARS}) — skipping")
        return None

    log.info(f"{symbol} {tf_label}: {len(df)} bars fetched ✓")
    return df


def fetch_all_timeframes(symbol: str) -> dict[str, pd.DataFrame]:
    results: dict[str, pd.DataFrame] = {}
    for tf_label, _, _, _ in TIMEFRAMES:
        df = fetch_ohlcv(symbol, tf_label)
        if df is not None:
            results[tf_label] = df
        time.sleep(0.2)
    return results
