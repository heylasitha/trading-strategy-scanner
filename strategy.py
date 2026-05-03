from __future__ import annotations

import pandas as pd

from config import (
    ADX_THRESHOLD, VOLUME_MULTIPLIER, MIN_RR,
)
from indicators import add_all_indicators, add_all_indicators_with_vwap


# ── Strategy 2: Golden Cross ──────────────────────────────────────────────────

def detect_golden_cross(df: pd.DataFrame, symbol: str, tf_label: str) -> dict | None:
    """
    Golden Cross: SMA 20 crosses ABOVE SMA 200.

    Conditions:
      1. Previous bar: SMA20 <= SMA200  (was below or equal)
      2. Current bar:  SMA20 >  SMA200  (now above = the cross)
      3. Price above both SMAs           (strength confirmation)
      4. Volume > 1.5x average           (real move, not fake)
      5. ADX > 25                        (trending market, not sideways)
    """
    df = add_all_indicators(df)
    df.dropna(subset=["sma20", "sma200", "adx", "volume_ratio"], inplace=True)

    if len(df) < 5:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # Condition 1 + 2: The cross happened on this bar
    cross_happened = (prev["sma20"] <= prev["sma200"]) and (last["sma20"] > last["sma200"])
    if not cross_happened:
        # Also check if cross happened within last 3 bars (still fresh)
        fresh_cross = False
        for i in range(-3, 0):
            bar      = df.iloc[i]
            bar_prev = df.iloc[i - 1]
            if (bar_prev["sma20"] <= bar_prev["sma200"]) and (bar["sma20"] > bar["sma200"]):
                fresh_cross = True
                break
        if not fresh_cross:
            return None

    # Condition 3: Price above both SMAs
    if last["close"] <= last["sma20"] or last["close"] <= last["sma200"]:
        return None

    # Condition 4: Volume confirmation
    if pd.isna(last["volume_ratio"]) or last["volume_ratio"] < VOLUME_MULTIPLIER:
        return None

    # Condition 5: ADX — trending market only
    if pd.isna(last["adx"]) or last["adx"] < ADX_THRESHOLD:
        return None

    # Calculate levels
    entry     = last["close"]
    stop      = last["sma200"] * 0.98   # Stop below SMA200
    risk      = entry - stop
    if risk <= 0:
        return None

    target     = entry + risk * MIN_RR
    stop_pct   = round(risk / entry * 100, 2)
    target_pct = round((target - entry) / entry * 100, 2)
    rr         = round((target - entry) / risk, 2)

    # Strength based on ADX and volume
    if last["adx"] > 35 and last["volume_ratio"] > 2.0:
        strength = "STRONG"
    elif last["adx"] > 28:
        strength = "MODERATE"
    else:
        strength = "WATCH"

    return {
        "symbol":        symbol,
        "timeframe":     tf_label,
        "strategy":      "GOLDEN CROSS",
        "strength":      strength,
        "pattern":       "SMA20 crossed above SMA200",
        "entry":         round(entry,  6),
        "stop":          round(stop,   6),
        "target":        round(target, 6),
        "rr":            rr,
        "stop_pct":      stop_pct,
        "target_pct":    target_pct,
        "sma20":         round(last["sma20"],        4),
        "sma200":        round(last["sma200"],        4),
        "adx":           round(last["adx"],           2),
        "volume_ratio":  round(last["volume_ratio"],  2),
        "close":         round(last["close"],         4),
    }


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


# ── Strategy 3: VWAP Bounce ───────────────────────────────────────────────────

# Intraday timeframes only — VWAP resets daily
VWAP_TIMEFRAMES = {"15m", "30m", "1h"}

# How close price must be to VWAP to count as a touch (0.5%)
VWAP_TOUCH_TOLERANCE = 0.005

# Max number of VWAP touches allowed in session (3rd+ touch = weaker)
MAX_VWAP_TOUCHES = 2


def _count_vwap_touches(df: pd.DataFrame, tolerance: float = VWAP_TOUCH_TOLERANCE) -> int:
    """Count how many times price has touched VWAP today."""
    today = df.index[-1].date()
    today_df = df[df.index.normalize() == pd.Timestamp(today)]
    touches = 0
    for _, row in today_df.iterrows():
        if abs(row["low"] - row["vwap"]) / row["vwap"] <= tolerance:
            touches += 1
    return touches


def _is_stock_lunch_hour(tf_label: str, symbol: str, mag7: list) -> bool:
    """Return True if current ET time is in choppy lunch zone (11AM-1PM)."""
    if symbol not in mag7:
        return False
    try:
        import pytz
        from datetime import datetime, timezone
        et  = pytz.timezone("US/Eastern")
        now = datetime.now(timezone.utc).astimezone(et)
        return 11 <= now.hour < 13
    except Exception:
        return False


def detect_vwap_bounce(
    df: pd.DataFrame,
    symbol: str,
    tf_label: str,
    mag7: list | None = None,
) -> dict | None:
    """
    Strategy 3 — VWAP Bounce.

    Conditions:
      1. Intraday timeframe only (15m / 30m / 1h)
      2. Price was above VWAP (uptrend)
      3. Price pulled back and touched VWAP middle (within 0.5%)
      4. Confirmation: candle CLOSED above VWAP after touch
      5. Volume spike on bounce candle (>1.5x avg)
      6. SMA20 > SMA200 (bigger trend is bullish)
      7. Only 1st or 2nd touch of session (fresh signal)
      8. Stocks: skip 11AM–1PM ET (lunch chop)
    """
    if mag7 is None:
        mag7 = []

    # Condition 1: Intraday only
    if tf_label not in VWAP_TIMEFRAMES:
        return None

    df = add_all_indicators_with_vwap(df)
    df.dropna(subset=["vwap", "vwap_upper", "vwap_lower",
                      "sma20", "sma200", "volume_ratio"], inplace=True)

    if len(df) < 10:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # Condition 2: Price was above VWAP — check previous candle
    if prev["close"] <= prev["vwap"]:
        return None

    # Condition 3: Last candle touched VWAP (low within tolerance)
    touched_vwap = (
        abs(last["low"] - last["vwap"]) / last["vwap"] <= VWAP_TOUCH_TOLERANCE
        or last["low"] <= last["vwap"] <= last["high"]
    )
    if not touched_vwap:
        return None

    # Condition 4: Candle closed ABOVE VWAP (confirmation — no fake bounce)
    if last["close"] <= last["vwap"]:
        return None

    # Condition 5: Volume spike on bounce candle
    if pd.isna(last["volume_ratio"]) or last["volume_ratio"] < VOLUME_MULTIPLIER:
        return None

    # Condition 6: SMA20 > SMA200 — bigger trend is bullish
    if last["sma20"] <= last["sma200"]:
        return None

    # Condition 7: Only 1st or 2nd touch of session
    touches = _count_vwap_touches(df)
    if touches > MAX_VWAP_TOUCHES:
        return None

    # Condition 8: Skip lunch hour for stocks
    if _is_stock_lunch_hour(tf_label, symbol, mag7):
        return None

    # ── Build trade levels ────────────────────────────────────────────────────
    entry      = last["close"]
    stop       = last["vwap_lower"] * 0.99   # Below lower VWAP band
    target     = last["vwap_upper"]           # Upper VWAP band

    risk = entry - stop
    if risk <= 0:
        return None

    rr         = round((target - entry) / risk, 2)
    stop_pct   = round(risk / entry * 100, 2)
    target_pct = round((target - entry) / entry * 100, 2)

    # Minimum R:R check
    if rr < 1.2:
        return None

    # Strength
    if last["volume_ratio"] > 2.0 and touches == 1:
        strength = "STRONG"
    elif last["volume_ratio"] > 1.5:
        strength = "MODERATE"
    else:
        strength = "WATCH"

    return {
        "symbol":        symbol,
        "timeframe":     tf_label,
        "strategy":      "VWAP BOUNCE",
        "strength":      strength,
        "pattern":       f"VWAP touch #{touches} — confirmed bounce",
        "entry":         round(entry,              6),
        "stop":          round(stop,               6),
        "target":        round(target,             6),
        "rr":            rr,
        "stop_pct":      stop_pct,
        "target_pct":    target_pct,
        "vwap":          round(last["vwap"],       6),
        "vwap_upper":    round(last["vwap_upper"], 6),
        "vwap_lower":    round(last["vwap_lower"], 6),
        "sma20":         round(last["sma20"],      4),
        "sma200":        round(last["sma200"],     4),
        "volume_ratio":  round(last["volume_ratio"], 2),
        "close":         round(last["close"],      4),
        "touch_number":  touches,
    }
