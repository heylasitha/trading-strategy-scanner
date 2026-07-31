"""
Momentum RSI + VIX Rank Strategy
- Verified: 75.1% WR over 2 years, 185 trades, 20 stocks
- Entry: price > SMA200, RSI(14) < 35, VIX Rank <= 70
- Exit:  3% profit target OR 10-day max hold
"""
from __future__ import annotations
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from mrsi_config import RSI_PERIOD, RSI_THRESHOLD, SMA_TREND, VIX_RANK_MAX, PROFIT_TARGET


# ── VIX Rank cache (refreshed daily) ─────────────────────────────────────────
_vix_cache: dict = {"date": None, "rank": None}


def get_vix_rank() -> float | None:
    """Current VIX rank (0–100): where today's VIX sits in its 1-year range."""
    today = datetime.utcnow().date()
    if _vix_cache["date"] == today and _vix_cache["rank"] is not None:
        return _vix_cache["rank"]
    try:
        raw = yf.download("^VIX", period="14mo", interval="1d",
                          auto_adjust=True, progress=False)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        if raw.empty or len(raw) < 50:
            return None
        vc       = raw["Close"]
        hi252    = vc.rolling(252, min_periods=50).max().iloc[-1]
        lo252    = vc.rolling(252, min_periods=50).min().iloc[-1]
        current  = float(vc.iloc[-1])
        rank     = (current - lo252) / (hi252 - lo252 + 1e-9) * 100
        _vix_cache["date"] = today
        _vix_cache["rank"] = round(rank, 1)
        return _vix_cache["rank"]
    except Exception:
        return None


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["sma200"] = df["Close"].rolling(SMA_TREND).mean()
    delta        = df["Close"].diff()
    gain         = delta.clip(lower=0).rolling(RSI_PERIOD).mean()
    loss         = (-delta.clip(upper=0)).rolling(RSI_PERIOD).mean()
    df["rsi"]    = 100 - (100 / (1 + gain / loss.replace(0, 1e-9)))
    return df


CRYPTO_SYMBOLS = {"BTC-USD", "ETH-USD"}


def detect(df: pd.DataFrame, symbol: str, vix_rank: float) -> dict | None:
    """
    Returns signal dict if all conditions pass, else None.
    Conditions:
      1. VIX Rank <= 70  (calm market — skipped for crypto)
      2. Price > SMA200  (long-term uptrend)
      3. RSI(14) < 35    (short-term pullback)
    """
    is_crypto = symbol in CRYPTO_SYMBOLS

    # Condition 1: VIX filter (skip for crypto — trades 24/7)
    if not is_crypto and (vix_rank is None or vix_rank > VIX_RANK_MAX):
        return None

    df = add_indicators(df).dropna(subset=["sma200", "rsi"])
    if len(df) < 5:
        return None

    last = df.iloc[-1]

    # Condition 2: price above SMA200
    if last["Close"] <= last["sma200"]:
        return None

    # Condition 3: RSI below threshold
    if last["rsi"] >= RSI_THRESHOLD:
        return None

    entry  = float(last["Close"])
    target = round(entry * (1 + PROFIT_TARGET), 4)

    return {
        "symbol":   symbol,
        "strategy": "MOMENTUM RSI",
        "entry":    round(entry, 4),
        "target":   target,
        "target_pct": round(PROFIT_TARGET * 100, 1),
        "max_hold": 10,
        "rsi":      round(float(last["rsi"]), 2),
        "sma200":   round(float(last["sma200"]), 4),
        "vix_rank": vix_rank,
        "close":    round(entry, 4),
    }
