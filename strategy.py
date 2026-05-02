from __future__ import annotations

import pandas as pd

from config import (
    ADX_THRESHOLD, VOLUME_MULTIPLIER, MIN_RR,
)
from indicators import add_all_indicators


# ── Pattern classifier ────────────────────────────────────────────────────────

def _classify_pattern(df: pd.DataFrame) -> str:
    """Identify which pattern triggered the setup."""
    last  = df.iloc[-1]
    prev5 = df.iloc[-6:-1]

    # Check if price recently bounced from SMA20 (within last 5 candles)
    sma20_touch = any(
        row["low"] <= row["sma20"] * 1.01
        for _, row in prev5.iterrows()
    )
    # Check if price recently bounced from SMA50
    sma50_touch = any(
        row["low"] <= row["sma50"] * 1.01
        for _, row in prev5.iterrows()
    )

    # Fan widening: SMA20-SMA200 spread is larger than 5 bars ago
    spread_now  = last["sma20"]  - last["sma200"]
    spread_prev = df.iloc[-6]["sma20"] - df.iloc[-6]["sma200"]
    fan_widening = spread_now > spread_prev

    # Breaking recent high (last 10 candles)
    recent_high = df["high"].iloc[-11:-1].max()
    breaking_high = last["close"] > recent_high

    if sma20_touch and fan_widening:
        return "SMA20 Bounce + Fan"
    if sma50_touch and fan_widening:
        return "SMA50 Bounce + Fan"
    if breaking_high and fan_widening:
        return "Breakout + Fan"
    if fan_widening:
        return "SMA Fan Widening"
    if sma20_touch:
        return "SMA20 Bounce"
    if sma50_touch:
        return "SMA50 Bounce"
    if breaking_high:
        return "Structure Breakout"
    return "Momentum Continuation"


# ── Stop loss & target ────────────────────────────────────────────────────────

def _calculate_levels(df: pd.DataFrame) -> dict:
    """
    Entry  : last close
    Stop   : lowest wick of last 10 candles × 0.99  (1 % buffer)
             but never above SMA50
    Target : entry + (entry − stop) × MIN_RR
    """
    last  = df.iloc[-1]
    entry = last["close"]

    swing_low  = df["low"].iloc[-10:].min()
    stop_raw   = swing_low * 0.99
    stop       = min(stop_raw, last["sma50"] * 0.98)  # whichever is lower

    risk = entry - stop
    if risk <= 0:
        return {}

    target = entry + risk * MIN_RR
    rr     = round((target - entry) / risk, 2)

    stop_pct   = round(risk / entry * 100, 2)
    target_pct = round((target - entry) / entry * 100, 2)

    return {
        "entry":       round(entry,  6),
        "stop":        round(stop,   6),
        "target":      round(target, 6),
        "risk":        round(risk,   6),
        "rr":          rr,
        "stop_pct":    stop_pct,
        "target_pct":  target_pct,
    }


# ── Signal strength ───────────────────────────────────────────────────────────

def _signal_strength(last: pd.Series, prev: pd.Series) -> str:
    score = 0
    if last["stoch_k"] > 70:
        score += 1
    if last["adx"] > 35:
        score += 1
    if last["volume_ratio"] > 2.0:
        score += 1
    k_gap = last["stoch_k"] - last["stoch_d"]
    if k_gap > 15:
        score += 1

    if score >= 3:
        return "STRONG"
    if score >= 1:
        return "MODERATE"
    return "WATCH"


# ── Main detector ─────────────────────────────────────────────────────────────

def detect_signal(df: pd.DataFrame, symbol: str, tf_label: str) -> dict | None:
    """
    Run all 7 conditions on the dataframe.
    Returns a signal dict if ALL conditions pass, else None.

    Conditions:
      1. SMA20 > SMA50 > SMA200
      2. Price (close) > SMA20
      3. Stochastic K > D
      4. Stochastic K rising
      5. Volume > VOLUME_MULTIPLIER × 20-bar average
      6. ADX > ADX_THRESHOLD
      7. Higher-timeframe alignment (SMA20 > SMA50 confirmed on last 3 bars)
    """
    df = add_all_indicators(df)
    df.dropna(subset=["sma20", "sma50", "sma200", "stoch_k", "stoch_d", "adx", "volume_ratio"], inplace=True)

    if len(df) < 15:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # ── Condition 1: SMA alignment ────────────────────────────────────────────
    if not (last["sma20"] > last["sma50"] > last["sma200"]):
        return None

    # ── Condition 2: Price above SMA20 ───────────────────────────────────────
    if last["close"] <= last["sma20"]:
        return None

    # ── Condition 3: Stochastic K above D ────────────────────────────────────
    if last["stoch_k"] <= last["stoch_d"]:
        return None

    # ── Condition 4: Stochastic K rising ─────────────────────────────────────
    if last["stoch_k"] <= prev["stoch_k"]:
        return None

    # ── Condition 5: Volume confirmation ─────────────────────────────────────
    if pd.isna(last["volume_ratio"]) or last["volume_ratio"] < VOLUME_MULTIPLIER:
        return None

    # ── Condition 6: ADX trend strength ──────────────────────────────────────
    if pd.isna(last["adx"]) or last["adx"] < ADX_THRESHOLD:
        return None

    # ── Condition 7: Consistent alignment over last 3 bars ───────────────────
    recent = df.iloc[-3:]
    if not all(
        row["sma20"] > row["sma50"] > row["sma200"]
        for _, row in recent.iterrows()
    ):
        return None

    # ── All conditions passed — build signal dict ─────────────────────────────
    levels = _calculate_levels(df)
    if not levels:
        return None

    pattern  = _classify_pattern(df)
    strength = _signal_strength(last, prev)

    return {
        "symbol":       symbol,
        "timeframe":    tf_label,
        "strength":     strength,
        "pattern":      pattern,
        # Price levels
        "entry":        levels["entry"],
        "stop":         levels["stop"],
        "target":       levels["target"],
        "rr":           levels["rr"],
        "stop_pct":     levels["stop_pct"],
        "target_pct":   levels["target_pct"],
        # Indicator values
        "sma20":        round(last["sma20"],       4),
        "sma50":        round(last["sma50"],       4),
        "sma200":       round(last["sma200"],      4),
        "stoch_k":      round(last["stoch_k"],     2),
        "stoch_d":      round(last["stoch_d"],     2),
        "adx":          round(last["adx"],         2),
        "volume_ratio": round(last["volume_ratio"],2),
        "close":        round(last["close"],       4),
    }
