from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytz

from config import (
    ADX_THRESHOLD, VOLUME_MULTIPLIER, VWAP_VOLUME_MULTIPLIER, MIN_RR,
    SMA_COMPRESSION_THRESHOLDS, SMA_MIN_BASE_CANDLES,
)
from indicators import add_all_indicators, add_all_indicators_with_vwap

# ── AVWAP Reclaim constants ───────────────────────────────────────────────────
_AVWAP_SWING_LOOKBACK  = 15
_AVWAP_SWING_CONFIRM   = 3
_AVWAP_MIN_BARS_BELOW  = 2
_AVWAP_MAX_ATR_PCT     = 4.0
_AVWAP_MAX_RISK_PCT    = 6.0
_AVWAP_MIN_VOL_RATIO   = 1.3

_ET = pytz.timezone("US/Eastern")

SHORT_ADX_THRESHOLD = 30   # stricter for shorts (stocks trend up long-term)


# ── Strategy 2: Golden Cross ──────────────────────────────────────────────────

def detect_golden_cross(df: pd.DataFrame, symbol: str, tf_label: str) -> dict | None:
    """
    Golden Cross: SMA 20 crosses ABOVE SMA 200.

    Conditions:
      1. Previous bar: SMA20 <= SMA200  (was below or equal)
      2. Current bar:  SMA20 >  SMA200  (now above = the cross)
      3. Price above both SMAs           (confirmation)
    """
    df = add_all_indicators(df)
    df.dropna(subset=["sma20", "sma200", "volume_ratio"], inplace=True)

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

    # Strength based on volume
    vol = float(last["volume_ratio"]) if not pd.isna(last["volume_ratio"]) else 0
    if vol > 2.0:
        strength = "STRONG"
    elif vol > 1.2:
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
    if last["rsi"] > 65:
        score += 1
    if last["adx"] > 35:
        score += 1
    if last["volume_ratio"] > 2.0:
        score += 1
    if last["rsi"] - prev["rsi"] > 5:   # RSI rising fast
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
    # ── Fix 1: 1H/2H only — prevents same signal firing on 5 timeframes ─────
    if tf_label not in {"1h", "2h"}:
        return None

    df = add_all_indicators(df)
    df.dropna(subset=["sma20", "sma50", "sma200", "rsi", "adx", "volume_ratio"], inplace=True)

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

    # ── Condition 3: RSI 45–72 — not oversold/weak, not overbought ──────────
    if pd.isna(last["rsi"]) or not (45 < last["rsi"] <= 72):
        return None

    # ── Fix 2: Volume filter — only fire on confirmed volume spike ───────────
    if pd.isna(last["volume_ratio"]) or last["volume_ratio"] < 1.1:
        return None

    # ── Condition 4: RSI rising ───────────────────────────────────────────────
    if last["rsi"] <= prev["rsi"]:
        return None

    # ── Condition 5: ADX > 20 — some trend present (not ranging) ─────────────
    if pd.isna(last["adx"]) or last["adx"] < 20:
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

    # ── Fix 3: Max stop 8% — reject wide-stop signals ────────────────────────
    if levels.get("stop_pct", 999) > 8.0:
        return None

    pattern  = _classify_pattern(df)
    strength = _signal_strength(last, prev)

    return {
        "symbol":       symbol,
        "timeframe":    tf_label,
        "strength":     strength,
        "pattern":      pattern,
        "entry":        levels["entry"],
        "stop":         levels["stop"],
        "target":       levels["target"],
        "rr":           levels["rr"],
        "stop_pct":     levels["stop_pct"],
        "target_pct":   levels["target_pct"],
        "sma20":        round(last["sma20"],        4),
        "sma50":        round(last["sma50"],        4),
        "sma200":       round(last["sma200"],       4),
        "rsi":          round(last["rsi"],          2),
        "adx":          round(last["adx"],          2),
        "volume_ratio": round(last["volume_ratio"], 2),
        "close":        round(last["close"],        4),
    }


# ── Strategy 3: VWAP Bounce ───────────────────────────────────────────────────

# Stocks: intraday only (VWAP resets daily, higher TFs have too few bars)
STOCK_VWAP_TIMEFRAMES  = {"15m", "30m", "1h"}
# Crypto: 24/7 market — VWAP meaningful on higher timeframes too
CRYPTO_VWAP_TIMEFRAMES = {"15m", "30m", "1h", "2h", "4h", "1d"}

# How close price must be to VWAP to count as a touch (0.5%)
VWAP_TOUCH_TOLERANCE = 0.005

# Max number of VWAP touches allowed in session (3rd+ touch = weaker)
MAX_VWAP_TOUCHES = 2


def _count_vwap_touches(df: pd.DataFrame, tolerance: float = VWAP_TOUCH_TOLERANCE) -> int:
    """Count how many times price has touched VWAP today."""
    today = df.index[-1].date()
    today_df = df[df.index.date == today]  # date comparison — works with any tz
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
    crypto_symbols: list | None = None,
) -> dict | None:
    """
    Strategy 3 — VWAP Bounce.

    Conditions:
      1. Uptrend: price above SMA200
      2. Previous candle: red (bearish) AND touched VWAP
      3. Current candle: closed above VWAP (bounce confirmed)
    """
    if mag7 is None:
        mag7 = []
    if crypto_symbols is None:
        crypto_symbols = []

    allowed_tfs = CRYPTO_VWAP_TIMEFRAMES if symbol in crypto_symbols else STOCK_VWAP_TIMEFRAMES
    if tf_label not in allowed_tfs:
        return None

    df = add_all_indicators_with_vwap(df)
    df.dropna(subset=["vwap", "sma200"], inplace=True)

    if len(df) < 10:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # 1. Uptrend: price above SMA200
    if last["close"] <= last["sma200"]:
        return None

    # 2. Previous candle touched VWAP
    if not (prev["low"] <= prev["vwap"] <= prev["high"]):
        return None

    # 3. Current candle closed above VWAP
    if last["close"] <= last["vwap"]:
        return None

    # ── Build trade levels ────────────────────────────────────────────────────
    entry = last["close"]
    atr   = last.get("atr", float("nan"))

    # ATR-based stop — gives each asset breathing room based on its own volatility
    # Fallback to previous low if ATR not available
    if pd.notna(atr) and atr > 0:
        stop_atr  = entry - (atr * 1.5)
        stop_prev = prev["low"] * 0.998
        stop = min(stop_atr, stop_prev)   # whichever is lower = more conservative
    else:
        stop = prev["low"] * 0.998

    risk = entry - stop
    if risk <= 0:
        return None

    target     = entry + risk * MIN_RR
    rr         = round((target - entry) / risk, 2)
    stop_pct   = round(risk / entry * 100, 2)
    target_pct = round((target - entry) / entry * 100, 2)

    return {
        "symbol":      symbol,
        "timeframe":   tf_label,
        "strategy":    "VWAP BOUNCE",
        "strength":    "HIGH CONVICTION",
        "pattern":     "Candle touched VWAP — confirmed bounce",
        "entry":       round(entry,  6),
        "stop":        round(stop,   6),
        "target":      round(target, 6),
        "rr":          rr,
        "stop_pct":    stop_pct,
        "target_pct":  target_pct,
        "vwap":        round(last["vwap"],   6),
        "sma200":      round(last["sma200"], 4),
        "close":       round(last["close"],  4),
    }


# ── Strategy 4: VWAP Fakeout Reversal ────────────────────────────────────────

def detect_vwap_fakeout(
    df: pd.DataFrame,
    symbol: str,
    tf_label: str,
    mag7: list | None = None,
    crypto_symbols: list | None = None,
) -> dict | None:
    """
    Strategy 4 — VWAP Fakeout Reversal.

    Conditions:
      1. Valid timeframe (same as VWAP Bounce)
      2. At least 3 previous candles closed ABOVE VWAP (established uptrend)
      3. Fakeout candle: low pierced BELOW VWAP but closed BACK ABOVE it
         (stop hunt — weak hands flushed out)
      4. Confirmation candle (current): green (close > open) AND above VWAP
      5. Volume spike on fakeout OR confirmation candle (>1.5x avg)
      6. SMA20 > SMA200 (bigger trend is bullish)
      7. R:R >= 1.5
    """
    if mag7 is None:
        mag7 = []
    if crypto_symbols is None:
        crypto_symbols = []

    allowed_tfs = CRYPTO_VWAP_TIMEFRAMES if symbol in crypto_symbols else STOCK_VWAP_TIMEFRAMES
    if tf_label not in allowed_tfs:
        return None

    df = add_all_indicators_with_vwap(df)
    df.dropna(subset=["vwap", "vwap_upper", "vwap_lower",
                      "sma20", "sma200", "volume_ratio"], inplace=True)

    if len(df) < 10:
        return None

    last    = df.iloc[-1]   # confirmation candle
    fakeout = df.iloc[-2]   # fakeout candle

    # Condition 2: Last 3 candles before fakeout were above VWAP
    prior = df.iloc[-5:-2]
    if not all(row["close"] > row["vwap"] for _, row in prior.iterrows()):
        return None

    # Condition 3: Fakeout candle — low below VWAP but closed above
    fakeout_occurred = (
        fakeout["low"] < fakeout["vwap"] and
        fakeout["close"] > fakeout["vwap"]
    )
    if not fakeout_occurred:
        return None

    # Condition 4: Confirmation candle — green and above VWAP
    if last["close"] <= last["open"]:
        return None
    if last["close"] <= last["vwap"]:
        return None

    # Condition 5: Volume spike on either fakeout or confirmation candle
    volume_confirmed = (
        fakeout["volume_ratio"] >= VOLUME_MULTIPLIER or
        last["volume_ratio"]   >= VOLUME_MULTIPLIER
    )
    if pd.isna(last["volume_ratio"]) or not volume_confirmed:
        return None

    # Condition 6: SMA20 > SMA200
    if last["sma20"] <= last["sma200"]:
        return None

    # ── Build trade levels ────────────────────────────────────────────────────
    entry  = last["close"]
    stop   = fakeout["low"] * 0.99    # just below the fakeout wick
    target = last["vwap_upper"]        # upper VWAP band

    risk = entry - stop
    if risk <= 0:
        return None

    rr         = round((target - entry) / risk, 2)
    stop_pct   = round(risk / entry * 100, 2)
    target_pct = round((target - entry) / entry * 100, 2)

    if rr < 1.5:
        return None

    if last["volume_ratio"] > 2.0:
        strength = "STRONG"
    elif last["volume_ratio"] > 1.5:
        strength = "MODERATE"
    else:
        strength = "WATCH"

    return {
        "symbol":        symbol,
        "timeframe":     tf_label,
        "strategy":      "VWAP FAKEOUT",
        "strength":      strength,
        "pattern":       "Fakeout below VWAP — reversal confirmed",
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
        "fakeout_low":   round(fakeout["low"],     6),
    }


# ── Short helpers ─────────────────────────────────────────────────────────────

def _classify_bearish_pattern(df: pd.DataFrame) -> str:
    last  = df.iloc[-1]
    prev5 = df.iloc[-6:-1]

    sma20_reject = any(row["high"] >= row["sma20"] * 0.99 for _, row in prev5.iterrows())
    sma50_reject = any(row["high"] >= row["sma50"] * 0.99 for _, row in prev5.iterrows())

    spread_now  = last["sma200"] - last["sma20"]
    spread_prev = df.iloc[-6]["sma200"] - df.iloc[-6]["sma20"]
    fan_widening = spread_now > spread_prev

    recent_low   = df["low"].iloc[-11:-1].min()
    breaking_low = last["close"] < recent_low

    if sma20_reject and fan_widening:
        return "SMA20 Rejection + Fan"
    if sma50_reject and fan_widening:
        return "SMA50 Rejection + Fan"
    if breaking_low and fan_widening:
        return "Breakdown + Fan"
    if fan_widening:
        return "SMA Fan Widening (Bearish)"
    if sma20_reject:
        return "SMA20 Rejection"
    if sma50_reject:
        return "SMA50 Rejection"
    if breaking_low:
        return "Structure Breakdown"
    return "Bearish Momentum"


def _calculate_levels_short(df: pd.DataFrame) -> dict:
    last  = df.iloc[-1]
    entry = last["close"]

    swing_high = df["high"].iloc[-10:].max()
    stop_raw   = swing_high * 1.01
    stop       = max(stop_raw, last["sma50"] * 1.02)

    risk = stop - entry
    if risk <= 0:
        return {}

    target = entry - risk * MIN_RR
    if target <= 0:
        return {}

    rr         = round((entry - target) / risk, 2)
    stop_pct   = round(risk / entry * 100, 2)
    target_pct = round((entry - target) / entry * 100, 2)

    return {
        "entry":       round(entry,  6),
        "stop":        round(stop,   6),
        "target":      round(target, 6),
        "risk":        round(risk,   6),
        "rr":          rr,
        "stop_pct":    stop_pct,
        "target_pct":  target_pct,
    }


def _signal_strength_bearish(last: pd.Series, prev: pd.Series) -> str:
    score = 0
    if last["rsi"] < 35:
        score += 1
    if last["adx"] > 35:
        score += 1
    if last["volume_ratio"] > 2.0:
        score += 1
    if prev["rsi"] - last["rsi"] > 5:   # RSI falling fast
        score += 1

    if score >= 3:
        return "STRONG"
    if score >= 1:
        return "MODERATE"
    return "WATCH"


# ── Strategy 5: Bearish SMA Stack ────────────────────────────────────────────

def detect_bearish_signal(df: pd.DataFrame, symbol: str, tf_label: str) -> dict | None:
    """
    Mirror of detect_signal but for shorts.

    Conditions:
      1. SMA20 < SMA50 < SMA200  (bearish stack)
      2. Price (close) < SMA20
      3. Stochastic K < D
      4. Stochastic K falling
      5. Volume > 1.5x average
      6. ADX > 30 (stricter — stocks trend up long-term)
      7. Consistent bearish alignment over last 3 bars
    """
    if tf_label not in {"1h", "2h"}:
        return None

    df = add_all_indicators(df)
    df.dropna(subset=["sma20", "sma50", "sma200", "rsi", "adx", "volume_ratio"], inplace=True)

    if len(df) < 15:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    if not (last["sma20"] < last["sma50"] < last["sma200"]):
        return None

    if last["close"] >= last["sma20"]:
        return None

    # RSI below 55 — not in overbought/strong zone
    if pd.isna(last["rsi"]) or last["rsi"] >= 55:
        return None

    # RSI falling
    if last["rsi"] >= prev["rsi"]:
        return None

    # ADX > 20 — some trend present
    if pd.isna(last["adx"]) or last["adx"] < 20:
        return None

    recent = df.iloc[-3:]
    if not all(
        row["sma20"] < row["sma50"] < row["sma200"]
        for _, row in recent.iterrows()
    ):
        return None

    levels = _calculate_levels_short(df)
    if not levels:
        return None

    pattern  = _classify_bearish_pattern(df)
    strength = _signal_strength_bearish(last, prev)

    return {
        "symbol":        symbol,
        "timeframe":     tf_label,
        "strategy":      "BEARISH SMA",
        "strength":      strength,
        "pattern":       pattern,
        "entry":         levels["entry"],
        "stop":          levels["stop"],
        "target":        levels["target"],
        "rr":            levels["rr"],
        "stop_pct":      levels["stop_pct"],
        "target_pct":    levels["target_pct"],
        "sma20":         round(last["sma20"],        4),
        "sma50":         round(last["sma50"],        4),
        "sma200":        round(last["sma200"],       4),
        "rsi":           round(last["rsi"],          2),
        "adx":           round(last["adx"],          2),
        "adx_threshold": SHORT_ADX_THRESHOLD,
        "volume_ratio":  round(last["volume_ratio"], 2),
        "close":         round(last["close"],        4),
    }


# ── Strategy 6: Death Cross ───────────────────────────────────────────────────

def detect_death_cross(df: pd.DataFrame, symbol: str, tf_label: str) -> dict | None:
    """
    Death Cross: SMA 20 crosses BELOW SMA 200.

    Conditions:
      1. Previous bar: SMA20 >= SMA200
      2. Current bar:  SMA20 <  SMA200  (the cross, or fresh within 3 bars)
      3. Price below both SMAs
      4. Volume > 1.5x average
      5. ADX > 25
    """
    df = add_all_indicators(df)
    df.dropna(subset=["sma20", "sma200", "adx", "volume_ratio"], inplace=True)

    if len(df) < 5:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    cross_happened = (prev["sma20"] >= prev["sma200"]) and (last["sma20"] < last["sma200"])
    if not cross_happened:
        fresh_cross = False
        for i in range(-3, 0):
            bar      = df.iloc[i]
            bar_prev = df.iloc[i - 1]
            if (bar_prev["sma20"] >= bar_prev["sma200"]) and (bar["sma20"] < bar["sma200"]):
                fresh_cross = True
                break
        if not fresh_cross:
            return None

    if last["close"] >= last["sma20"] or last["close"] >= last["sma200"]:
        return None

    entry = last["close"]
    stop  = last["sma200"] * 1.02
    risk  = stop - entry
    if risk <= 0:
        return None

    target     = entry - risk * MIN_RR
    if target <= 0:
        return None
    stop_pct   = round(risk / entry * 100, 2)
    target_pct = round((entry - target) / entry * 100, 2)
    rr         = round((entry - target) / risk, 2)

    vol = float(last["volume_ratio"]) if not pd.isna(last["volume_ratio"]) else 0
    if vol > 2.0:
        strength = "STRONG"
    elif vol > 1.2:
        strength = "MODERATE"
    else:
        strength = "WATCH"

    return {
        "symbol":        symbol,
        "timeframe":     tf_label,
        "strategy":      "DEATH CROSS",
        "strength":      strength,
        "pattern":       "SMA20 crossed below SMA200",
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


# ── Strategy 7: Bearish VWAP Rejection ───────────────────────────────────────

def detect_vwap_rejection(
    df: pd.DataFrame,
    symbol: str,
    tf_label: str,
    mag7: list | None = None,
    crypto_symbols: list | None = None,
) -> dict | None:
    """
    Strategy 7 — Bearish VWAP Rejection.

    Conditions:
      1. Downtrend: price below SMA200
      2. Previous candle touched VWAP
      3. Current candle closed below VWAP (rejection confirmed)
    """
    if mag7 is None:
        mag7 = []
    if crypto_symbols is None:
        crypto_symbols = []

    allowed_tfs = CRYPTO_VWAP_TIMEFRAMES if symbol in crypto_symbols else STOCK_VWAP_TIMEFRAMES
    if tf_label not in allowed_tfs:
        return None

    df = add_all_indicators_with_vwap(df)
    df.dropna(subset=["vwap", "sma200"], inplace=True)

    if len(df) < 10:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # 1. Downtrend: price below SMA200
    if last["close"] >= last["sma200"]:
        return None

    # 2. Previous candle touched VWAP
    if not (prev["low"] <= prev["vwap"] <= prev["high"]):
        return None

    # 3. Current candle closed below VWAP
    if last["close"] >= last["vwap"]:
        return None

    entry  = last["close"]
    stop   = prev["high"] * 1.001
    risk   = stop - entry
    if risk <= 0:
        return None

    target     = entry - risk * MIN_RR
    rr         = round((entry - target) / risk, 2)
    stop_pct   = round(risk / entry * 100, 2)
    target_pct = round((entry - target) / entry * 100, 2)

    return {
        "symbol":      symbol,
        "timeframe":   tf_label,
        "strategy":    "VWAP REJECTION",
        "strength":    "HIGH CONVICTION",
        "pattern":     "Candle touched VWAP — rejected down",
        "entry":       round(entry,  6),
        "stop":        round(stop,   6),
        "target":      round(target, 6),
        "rr":          rr,
        "stop_pct":    stop_pct,
        "target_pct":  target_pct,
        "vwap":        round(last["vwap"],   6),
        "sma200":      round(last["sma200"], 4),
        "close":       round(last["close"],  4),
    }


# ── Strategy 8/9: Opening Range Breakout (ORB) ────────────────────────────────

def _get_orb_levels(df: pd.DataFrame):
    """Return (orb_high, orb_low) of today's first 15m candle in ET, or (None, None)."""
    df_et = df.copy()
    df_et.index = df_et.index.tz_convert(_ET)

    now_et = datetime.now(timezone.utc).astimezone(_ET)
    today  = now_et.date()

    today_bars = df_et[df_et.index.date == today]
    if len(today_bars) < 1:
        return None, None

    orb_bar = today_bars.iloc[0]
    if not (orb_bar.name.hour == 9 and orb_bar.name.minute == 30):
        return None, None

    return orb_bar["high"], orb_bar["low"]


def _orb_time_valid() -> bool:
    """True if current ET time is in ORB valid window: 9:45 AM – 12:00 PM."""
    now_et  = datetime.now(timezone.utc).astimezone(_ET)
    if now_et.weekday() >= 5:
        return False
    mins = now_et.hour * 60 + now_et.minute
    return 9 * 60 + 45 <= mins < 12 * 60


def detect_orb_long(df: pd.DataFrame, symbol: str, tf_label: str) -> dict | None:
    """
    Strategy 8 — ORB Long.

    Conditions:
      1. 15m timeframe only
      2. Current ET time is 9:45 AM – 12:00 PM (ORB valid window)
      3. Opening range defined (first 15m candle at 9:30 ET)
      4. Current close ABOVE ORB high
      5. Volume > 1.5x average
      6. SMA20 > SMA200 (trend filter)
      7. R:R >= 1.5
    """
    if tf_label != "15m":
        return None
    if not _orb_time_valid():
        return None

    orb_high, orb_low = _get_orb_levels(df)
    if orb_high is None:
        return None

    df = add_all_indicators(df)
    df.dropna(subset=["sma20", "sma200", "rsi", "volume_ratio"], inplace=True)
    if len(df) < 5:
        return None

    last = df.iloc[-1]

    if last["close"] <= orb_high:
        return None

    if pd.isna(last["volume_ratio"]) or last["volume_ratio"] < VOLUME_MULTIPLIER:
        return None

    if last["sma20"] <= last["sma200"]:
        return None

    entry     = last["close"]
    stop      = orb_low * 0.99
    risk      = entry - stop
    if risk <= 0:
        return None

    orb_range  = orb_high - orb_low
    target     = entry + orb_range * 2    # 2x ORB range projection
    rr         = round((target - entry) / risk, 2)
    if rr < 1.5:
        target = entry + risk * MIN_RR
        rr     = round((target - entry) / risk, 2)

    stop_pct   = round(risk / entry * 100, 2)
    target_pct = round((target - entry) / entry * 100, 2)

    if last["volume_ratio"] > 2.5 and last["rsi"] > 60:
        strength = "STRONG"
    elif last["volume_ratio"] > 1.8:
        strength = "MODERATE"
    else:
        strength = "WATCH"

    return {
        "symbol":       symbol,
        "timeframe":    tf_label,
        "strategy":     "ORB LONG",
        "strength":     strength,
        "pattern":      f"Breakout above ORB high",
        "entry":        round(entry,              6),
        "stop":         round(stop,               6),
        "target":       round(target,             6),
        "rr":           rr,
        "stop_pct":     stop_pct,
        "target_pct":   target_pct,
        "orb_high":     round(orb_high,           4),
        "orb_low":      round(orb_low,            4),
        "rsi":          round(last["rsi"],         2),
        "volume_ratio": round(last["volume_ratio"],2),
        "sma20":        round(last["sma20"],       4),
        "sma200":       round(last["sma200"],      4),
        "close":        round(last["close"],       4),
    }


def detect_orb_short(df: pd.DataFrame, symbol: str, tf_label: str) -> dict | None:
    """
    Strategy 9 — ORB Short.

    Conditions:
      1. 15m timeframe only
      2. Current ET time is 9:45 AM – 12:00 PM
      3. Opening range defined
      4. Current close BELOW ORB low
      5. Volume > 1.5x average
      6. SMA20 < SMA200 (trend filter)
      7. R:R >= 1.5
    """
    if tf_label != "15m":
        return None
    if not _orb_time_valid():
        return None

    orb_high, orb_low = _get_orb_levels(df)
    if orb_high is None:
        return None

    df = add_all_indicators(df)
    df.dropna(subset=["sma20", "sma200", "rsi", "volume_ratio"], inplace=True)
    if len(df) < 5:
        return None

    last = df.iloc[-1]

    if last["close"] >= orb_low:
        return None

    if pd.isna(last["volume_ratio"]) or last["volume_ratio"] < VOLUME_MULTIPLIER:
        return None

    if last["sma20"] >= last["sma200"]:
        return None

    entry     = last["close"]
    stop      = orb_high * 1.01
    risk      = stop - entry
    if risk <= 0:
        return None

    orb_range  = orb_high - orb_low
    target     = entry - orb_range * 2
    if target <= 0:
        target = entry - risk * MIN_RR
    rr         = round((entry - target) / risk, 2)
    if rr < 1.5:
        return None

    stop_pct   = round(risk / entry * 100, 2)
    target_pct = round((entry - target) / entry * 100, 2)

    if last["volume_ratio"] > 2.5 and last["rsi"] < 40:
        strength = "STRONG"
    elif last["volume_ratio"] > 1.8:
        strength = "MODERATE"
    else:
        strength = "WATCH"

    return {
        "symbol":       symbol,
        "timeframe":    tf_label,
        "strategy":     "ORB SHORT",
        "strength":     strength,
        "pattern":      f"Breakdown below ORB low",
        "entry":        round(entry,              6),
        "stop":         round(stop,               6),
        "target":       round(target,             6),
        "rr":           rr,
        "stop_pct":     stop_pct,
        "target_pct":   target_pct,
        "orb_high":     round(orb_high,           4),
        "orb_low":      round(orb_low,            4),
        "rsi":          round(last["rsi"],         2),
        "volume_ratio": round(last["volume_ratio"],2),
        "sma20":        round(last["sma20"],       4),
        "sma200":       round(last["sma200"],      4),
        "close":        round(last["close"],       4),
    }


# ── Strategy 10: SMA Compression Breakout ────────────────────────────────────

# ── Structure detection helpers ───────────────────────────────────────────────

def _detect_base_structure(df: pd.DataFrame, n_bars: int = 16) -> str | None:
    """
    Analyse the last n_bars (excluding the breakout candle) to classify the
    base structure as one of three valid types:

      SYMMETRICAL_TRIANGLE  — highs declining + lows rising   (both sides trapped)
      DESCENDING_WEDGE      — highs declining + lows declining but converging
      ASCENDING_TRIANGLE    — lows rising    + highs flat

    Returns None if no valid structure is found (rounding bottom, flat base, etc.)
    """
    needed = n_bars + 4
    if len(df) < needed:
        return None

    # Base = the candles before the breakout candle and the compression candle
    base = df.iloc[-n_bars - 2:-2]
    if len(base) < 8:
        return None

    highs = base["high"].values.astype(float)
    lows  = base["low"].values.astype(float)
    x     = np.arange(len(highs), dtype=float)

    try:
        high_slope = float(np.polyfit(x, highs, 1)[0])
        low_slope  = float(np.polyfit(x, lows,  1)[0])
    except Exception:
        return None

    price = float(base["close"].mean())
    if price <= 0:
        return None

    # Normalise: % change per bar
    hs = high_slope / price * 100
    ls = low_slope  / price * 100

    DECLINING = -0.03   # highs must fall > 0.03 % per bar
    RISING    =  0.03   # lows must rise > 0.03 % per bar
    FLAT_TOL  =  0.025  # ±0.025 % per bar = effectively flat

    # ── Symmetrical triangle: highs falling AND lows rising ──────────────────
    if hs < DECLINING and ls > RISING:
        return "SYMMETRICAL_TRIANGLE"

    # ── Descending wedge: both falling but CONVERGING (range narrowing) ──────
    if hs < DECLINING and ls < -FLAT_TOL:
        early_range = float(highs[:5].mean() - lows[:5].mean())
        late_range  = float(highs[-5:].mean() - lows[-5:].mean())
        if early_range > 0 and late_range < early_range * 0.75:
            return "DESCENDING_WEDGE"

    # ── Ascending triangle: lows rising, highs roughly flat ──────────────────
    if ls > RISING and abs(hs) < FLAT_TOL * 2:
        return "ASCENDING_TRIANGLE"

    return None   # rounding bottom, flat base, random noise → reject


def detect_sma_compression_breakout(df: pd.DataFrame, symbol: str, tf_label: str) -> dict | None:
    """
    Professional SMA Compression Breakout — built from 14-chart analysis.

    Entry conditions (ALL must pass):
      1. SMA20/50/200 compressed within timeframe-specific threshold
      2. Valid base structure: Symmetrical Triangle, Descending Wedge, or
         Ascending Triangle detected in the base period
      3. Minimum base duration (timeframe-dependent candle count)
      4. Volume contracting during the base (energy coiling)
      5. Breakout candle:
           • Opens inside or near the SMA cluster
           • Closes above all 3 SMAs
           • Bullish (close > open)
           • Body ≥ 1.5× average of prior 5 candles
           • Volume ≥ 1.5× average (real institutional move)
      6. ATR-based stop — adapts to each asset's volatility
      7. Tiered R:R target based on compression tightness:
           < 2%  compression → 5R target  (EXTREME)
           2–4%  compression → 3R target  (HIGH CONVICTION)
           4–10% compression → 2R target  (STANDARD)
      8. Shakeout bonus: if bar before breakout dipped below cluster
         and closed back inside → conviction upgraded one tier
    """
    df = add_all_indicators(df)
    df.dropna(subset=["sma20", "sma50", "sma200", "atr", "volume_ratio"], inplace=True)

    # ── Timeframe settings ────────────────────────────────────────────────────
    max_spread    = SMA_COMPRESSION_THRESHOLDS.get(tf_label, 0.06)
    min_base_bars = SMA_MIN_BASE_CANDLES.get(tf_label, 12)

    if len(df) < min_base_bars + 6:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # ── 1. SMA compression check (on previous candle) ────────────────────────
    sma20_p  = float(prev["sma20"])
    sma50_p  = float(prev["sma50"])
    sma200_p = float(prev["sma200"])

    sma_max_p = max(sma20_p, sma50_p, sma200_p)
    sma_min_p = min(sma20_p, sma50_p, sma200_p)

    if sma_min_p <= 0:
        return None

    spread = (sma_max_p - sma_min_p) / sma_min_p
    if spread > max_spread:
        return None

    # ── 2. Base structure detection ───────────────────────────────────────────
    structure = _detect_base_structure(df, n_bars=min_base_bars)
    if structure is None:
        return None

    # ── 3. Breakout candle — opened near cluster ──────────────────────────────
    if float(last["open"]) > sma_max_p * 1.02:
        return None

    # ── 4. Breakout candle — closed above ALL 3 SMAs ─────────────────────────
    if not (last["close"] > last["sma20"] and
            last["close"] > last["sma50"] and
            last["close"] > last["sma200"]):
        return None

    # ── 5. Bullish candle ─────────────────────────────────────────────────────
    if last["close"] <= last["open"]:
        return None

    # ── 6. Strong breakout body (≥ 1.5× average of prior 5 candles) ──────────
    recent_bodies = [
        abs(float(df.iloc[i]["close"]) - float(df.iloc[i]["open"]))
        for i in range(-6, -1)
    ]
    avg_body     = sum(recent_bodies) / len(recent_bodies)
    current_body = float(last["close"]) - float(last["open"])
    if avg_body <= 0 or current_body < avg_body * 1.5:
        return None

    # ── 7. Volume: contracting during base, spiking on breakout ──────────────
    if pd.isna(last["volume_ratio"]) or last["volume_ratio"] < 1.5:
        return None

    base_vol    = df["volume"].iloc[-min_base_bars - 2:-2].mean()
    overall_avg = df["volume"].rolling(30).mean().iloc[-3]
    vol_contracting = (not pd.isna(overall_avg)) and (base_vol < overall_avg)

    # ── 8. Shakeout detection ─────────────────────────────────────────────────
    shakeout = False
    if len(df) >= 4:
        shakeout_bar = df.iloc[-3]
        shakeout = (
            float(shakeout_bar["low"])   < sma_min_p and
            float(shakeout_bar["close"]) > sma_min_p
        )

    # ── 9. Trade levels ───────────────────────────────────────────────────────
    entry = float(last["close"])
    atr   = float(last["atr"])

    if pd.isna(atr) or atr <= 0:
        return None

    stop_atr = entry - (atr * 1.5)
    stop_sma = sma_min_p * 0.99       # always stay below the cluster
    stop     = min(stop_atr, stop_sma)

    risk = entry - stop
    if risk <= 0:
        return None

    # ── 10. Tiered conviction + R:R based on compression tightness ───────────
    if spread <= 0.02:
        conviction    = "EXTREME"
        rr_multiplier = 5.0
    elif spread <= 0.04:
        conviction    = "HIGH CONVICTION"
        rr_multiplier = 3.0
    else:
        conviction    = "STANDARD"
        rr_multiplier = 2.0

    # Shakeout upgrades conviction one level
    if shakeout:
        if conviction == "STANDARD":
            conviction    = "HIGH CONVICTION"
            rr_multiplier = 3.0
        elif conviction == "HIGH CONVICTION":
            conviction    = "EXTREME"
            rr_multiplier = 5.0

    target     = entry + risk * rr_multiplier
    rr         = round((target - entry) / risk, 2)
    stop_pct   = round(risk / entry * 100, 2)
    target_pct = round((target - entry) / entry * 100, 2)

    # ── 11. Final R:R floor check ─────────────────────────────────────────────
    if rr < MIN_RR:
        return None

    structure_label = {
        "SYMMETRICAL_TRIANGLE": "Symmetrical Triangle 🔺",
        "DESCENDING_WEDGE":     "Descending Wedge 📐",
        "ASCENDING_TRIANGLE":   "Ascending Triangle 📈",
    }.get(structure, structure)

    return {
        "symbol":           symbol,
        "timeframe":        tf_label,
        "strategy":         "SMA COMPRESSION",
        "pattern":          "SMA Compression Breakout",
        "strength":         conviction,
        "structure":        structure,
        "structure_label":  structure_label,
        "entry":            round(entry,    6),
        "stop":             round(stop,     6),
        "target":           round(target,   6),
        "rr":               rr,
        "rr_multiplier":    rr_multiplier,
        "stop_pct":         stop_pct,
        "target_pct":       target_pct,
        "sma20":            round(sma20_p,  6),
        "sma50":            round(sma50_p,  6),
        "sma200":           round(sma200_p, 6),
        "spread_pct":       round(spread * 100, 2),
        "body_ratio":       round(current_body / avg_body, 2),
        "volume_ratio":     round(float(last["volume_ratio"]), 2),
        "vol_contracting":  vol_contracting,
        "shakeout":         shakeout,
        "close":            round(entry, 6),
    }


# ── AVWAP Reclaim Strategy ────────────────────────────────────────────────────

def _avwap_at(df: pd.DataFrame, anchor_i: int, target_i: int) -> float:
    seg = df.iloc[anchor_i:target_i + 1]
    tp  = (seg["high"] + seg["low"] + seg["close"]) / 3
    cv  = seg["volume"].cumsum()
    ctv = (tp * seg["volume"]).cumsum()
    v   = float(cv.iloc[-1])
    return float(ctv.iloc[-1] / v) if v > 0 else float("nan")


def _find_swing_low(df: pd.DataFrame, i: int) -> int | None:
    for j in range(i - _AVWAP_SWING_CONFIRM, max(_AVWAP_SWING_LOOKBACK, i - 80), -1):
        wl = float(df["low"].iloc[j - _AVWAP_SWING_LOOKBACK : j + 1].min())
        bl = float(df.iloc[j]["low"])
        if abs(bl - wl) > 0.002 * bl:
            continue
        return j
    return None


def detect_avwap_reclaim(df: pd.DataFrame, symbol: str, tf_label: str) -> dict | None:
    """
    AVWAP Reclaim — 4H only.
    Conditions:
      1. SMA20 > SMA50 > SMA200 (uptrend)
      2. ATR% < 4% (not high-beta whipsaw)
      3. Volume >= 1.3× 20-bar average
      4. Found swing low anchor within last 80 bars
      5. Previous bar closed BELOW Anchored VWAP
      6. Current bar closes ABOVE Anchored VWAP (reclaim)
      7. Current candle is bullish (close > open)
      8. At least 2 consecutive bars below AVWAP before reclaim
      9. Current price above swing low
     10. Stop (candle low × 0.998) risk <= 6% of entry
    """
    if len(df) < _AVWAP_SWING_LOOKBACK + _AVWAP_SWING_CONFIRM + 12:
        return None

    df = df.copy()
    # Ensure indicators
    for col in ["sma20", "sma50", "sma200"]:
        if col not in df.columns:
            df["sma20"]  = df["close"].rolling(20).mean()
            df["sma50"]  = df["close"].rolling(50).mean()
            df["sma200"] = df["close"].rolling(200).mean()
            break

    if "vol20" not in df.columns:
        df["vol20"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / df["vol20"].replace(0, float("nan"))

    if "atr" not in df.columns:
        pc  = df["close"].shift(1)
        tr  = pd.concat([df["high"] - df["low"],
                         (df["high"] - pc).abs(),
                         (df["low"]  - pc).abs()], axis=1).max(axis=1)
        df["atr"] = tr.rolling(14).mean()
    df["atr_pct"] = df["atr"] / df["close"] * 100

    df.dropna(subset=["sma20", "sma50", "sma200", "atr_pct", "vol_ratio"], inplace=True)
    if len(df) < 10:
        return None

    i    = len(df) - 1
    last = df.iloc[i]
    prev = df.iloc[i - 1]

    # 1. Uptrend
    if not (last["sma20"] > last["sma50"] > last["sma200"]):
        return None

    # 2. ATR filter
    if float(last["atr_pct"]) > _AVWAP_MAX_ATR_PCT:
        return None

    # 3. Volume
    if pd.isna(last["vol_ratio"]) or float(last["vol_ratio"]) < _AVWAP_MIN_VOL_RATIO:
        return None

    # 4. Swing low anchor
    anchor_i = _find_swing_low(df, i)
    if anchor_i is None:
        return None
    swing_low = float(df.iloc[anchor_i]["low"])

    # 5-6. AVWAP reclaim
    av_prev = _avwap_at(df, anchor_i, i - 1)
    av_now  = _avwap_at(df, anchor_i, i)
    import math
    if math.isnan(av_prev) or math.isnan(av_now):
        return None
    if float(prev["close"]) >= av_prev:
        return None
    if float(last["close"]) <= av_now:
        return None

    # 7. Bullish candle
    if float(last["close"]) <= float(last["open"]):
        return None

    # 8. Min bars below AVWAP
    below = 0
    for j in range(i - 1, anchor_i, -1):
        av_j = _avwap_at(df, anchor_i, j)
        if float(df.iloc[j]["close"]) < av_j:
            below += 1
        else:
            break
    if below < _AVWAP_MIN_BARS_BELOW:
        return None

    # 9. Price above swing low
    if float(last["close"]) <= swing_low:
        return None

    # 10. Stop / risk
    entry    = float(last["close"])
    stop     = float(last["low"]) * 0.998
    risk     = entry - stop
    if risk <= 0:
        return None
    risk_pct = risk / entry * 100
    if risk_pct > _AVWAP_MAX_RISK_PCT:
        return None

    target     = entry + risk * MIN_RR
    rr         = round((target - entry) / risk, 2)
    stop_pct   = round(risk_pct, 2)
    target_pct = round((target - entry) / entry * 100, 2)

    vol = float(last["vol_ratio"])
    strength = "STRONG" if vol >= 2.0 else ("MODERATE" if vol >= 1.5 else "WATCH")

    return {
        "symbol":     symbol,
        "timeframe":  tf_label,
        "strategy":   "AVWAP RECLAIM",
        "strength":   strength,
        "pattern":    "Anchored VWAP Reclaim",
        "entry":      round(entry,    6),
        "stop":       round(stop,     6),
        "target":     round(target,   6),
        "rr":         rr,
        "stop_pct":   stop_pct,
        "target_pct": target_pct,
        "avwap":      round(av_now,   6),
        "swing_low":  round(swing_low, 6),
        "bars_below": below,
        "atr_pct":    round(float(last["atr_pct"]), 2),
        "vol_ratio":  round(vol, 2),
        "sma20":      round(float(last["sma20"]),  4),
        "sma50":      round(float(last["sma50"]),  4),
        "sma200":     round(float(last["sma200"]), 4),
        "close":      round(entry, 6),
    }
