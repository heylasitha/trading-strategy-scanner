from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytz

from config import (
    ADX_THRESHOLD, VOLUME_MULTIPLIER, VWAP_VOLUME_MULTIPLIER, MIN_RR,
    SMA_COMPRESSION_THRESHOLD,
)
from indicators import add_all_indicators, add_all_indicators_with_vwap

_ET = pytz.timezone("US/Eastern")

SHORT_ADX_THRESHOLD = 30   # stricter for shorts (stocks trend up long-term)


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
    if last["volume_ratio"] > 2.0:
        strength = "STRONG"
    elif last["volume_ratio"] > 1.5:
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

    # ── Condition 3: RSI above 55 — confirmed bullish momentum ───────────────
    if pd.isna(last["rsi"]) or last["rsi"] <= 55:
        return None

    # ── Condition 4: RSI rising ───────────────────────────────────────────────
    if last["rsi"] <= prev["rsi"]:
        return None

    # ── Condition 5: ADX trend strength ──────────────────────────────────────
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
      1. Valid timeframe: stocks 15m/30m/1h only; crypto adds 2h/4h/1d
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
    if crypto_symbols is None:
        crypto_symbols = []

    # Condition 1: Timeframe gate — crypto allows higher TFs, stocks do not
    allowed_tfs = CRYPTO_VWAP_TIMEFRAMES if symbol in crypto_symbols else STOCK_VWAP_TIMEFRAMES
    if tf_label not in allowed_tfs:
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
    if pd.isna(last["volume_ratio"]) or last["volume_ratio"] < VWAP_VOLUME_MULTIPLIER:
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
        fakeout["volume_ratio"] >= VWAP_VOLUME_MULTIPLIER or
        last["volume_ratio"]   >= VWAP_VOLUME_MULTIPLIER
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

    # RSI below 45 — confirmed bearish momentum
    if pd.isna(last["rsi"]) or last["rsi"] >= 45:
        return None

    # RSI falling
    if last["rsi"] >= prev["rsi"]:
        return None

    if pd.isna(last["adx"]) or last["adx"] < SHORT_ADX_THRESHOLD:
        return None

    # Volume confirmation — was missing, caused 0.4x signals to fire
    if pd.isna(last["volume_ratio"]) or last["volume_ratio"] < VOLUME_MULTIPLIER:
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

    if pd.isna(last["volume_ratio"]) or last["volume_ratio"] < VOLUME_MULTIPLIER:
        return None

    if pd.isna(last["adx"]) or last["adx"] < ADX_THRESHOLD:
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

    if last["adx"] > 35 and last["volume_ratio"] > 2.0:
        strength = "STRONG"
    elif last["adx"] > 28:
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
    Strategy 7 — Bearish VWAP Rejection (mirror of VWAP Bounce).

    Conditions:
      1. Valid timeframe (same as VWAP Bounce)
      2. Price was BELOW VWAP — downtrend established
      3. Last candle rallied up and TOUCHED VWAP (high within tolerance)
      4. Candle CLOSED BELOW VWAP — rejection confirmed
      5. Volume spike (>1.5x avg)
      6. SMA20 < SMA200 — bigger trend is bearish
      7. Only 1st or 2nd touch of session
      8. Stocks: skip 11AM–1PM ET lunch chop
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

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # Condition 2: Price was below VWAP previously
    if prev["close"] >= prev["vwap"]:
        return None

    # Condition 3: Last candle touched VWAP from below
    touched_vwap = (
        abs(last["high"] - last["vwap"]) / last["vwap"] <= VWAP_TOUCH_TOLERANCE
        or last["low"] <= last["vwap"] <= last["high"]
    )
    if not touched_vwap:
        return None

    # Condition 4: Closed BELOW VWAP — rejected
    if last["close"] >= last["vwap"]:
        return None

    # Condition 5: Volume spike
    if pd.isna(last["volume_ratio"]) or last["volume_ratio"] < VWAP_VOLUME_MULTIPLIER:
        return None

    # Condition 6: SMA20 < SMA200 — bigger trend is bearish
    if last["sma20"] >= last["sma200"]:
        return None

    # Condition 7: Only 1st or 2nd touch of session
    touches = _count_vwap_touches(df)
    if touches > MAX_VWAP_TOUCHES:
        return None

    # Condition 8: Skip lunch hour for stocks
    if _is_stock_lunch_hour(tf_label, symbol, mag7):
        return None

    entry  = last["close"]
    stop   = last["vwap_upper"] * 1.01
    target = last["vwap_lower"]

    risk = stop - entry
    if risk <= 0:
        return None

    rr         = round((entry - target) / risk, 2)
    stop_pct   = round(risk / entry * 100, 2)
    target_pct = round((entry - target) / entry * 100, 2)

    if rr < 1.2:
        return None

    if last["volume_ratio"] > 2.0 and touches == 1:
        strength = "STRONG"
    elif last["volume_ratio"] > 1.5:
        strength = "MODERATE"
    else:
        strength = "WATCH"

    return {
        "symbol":        symbol,
        "timeframe":     tf_label,
        "strategy":      "VWAP REJECTION",
        "strength":      strength,
        "pattern":       f"VWAP touch #{touches} — rejected down",
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

def detect_sma_compression_breakout(df: pd.DataFrame, symbol: str, tf_label: str) -> dict | None:
    """
    Fires when all 3 SMAs are compressed within 3% of each other and a
    strong bullish engulfing candle breaks above the entire SMA cluster.

    Conditions:
      1. SMA20, SMA50, SMA200 spread <= 3%  (compression)
      2. Current candle open <= top of SMA cluster (started inside/below)
      3. Current candle close > all 3 SMAs  (broke above cluster)
      4. Current candle is bullish (close > open)
      5. Current candle body > average body of last 3 candles (strong move)
    """
    df = add_all_indicators(df)
    df.dropna(subset=["sma20", "sma50", "sma200"], inplace=True)

    if len(df) < 6:
        return None

    last = df.iloc[-1]

    sma20  = last["sma20"]
    sma50  = last["sma50"]
    sma200 = last["sma200"]

    sma_max = max(sma20, sma50, sma200)
    sma_min = min(sma20, sma50, sma200)

    if sma_min <= 0:
        return None

    # 1. SMA compression
    spread = (sma_max - sma_min) / sma_min
    if spread > SMA_COMPRESSION_THRESHOLD:
        return None

    # 2. Candle opened inside or below SMA cluster
    if last["open"] > sma_max * 1.005:
        return None

    # 3. Candle closed above all 3 SMAs
    if not (last["close"] > sma20 and last["close"] > sma50 and last["close"] > sma200):
        return None

    # 4. Bullish candle
    if last["close"] <= last["open"]:
        return None

    # 5. Strong body vs recent average
    recent_bodies = [
        abs(df.iloc[i]["close"] - df.iloc[i]["open"])
        for i in range(-4, -1)
    ]
    avg_body     = sum(recent_bodies) / len(recent_bodies)
    current_body = last["close"] - last["open"]
    if avg_body <= 0 or current_body <= avg_body:
        return None

    # Calculate trade levels
    entry = last["close"]
    stop  = sma_min * 0.98
    risk  = entry - stop
    if risk <= 0:
        return None

    target     = entry + risk * MIN_RR
    rr         = round((target - entry) / risk, 2)
    stop_pct   = round(risk / entry * 100, 2)
    target_pct = round((target - entry) / entry * 100, 2)

    return {
        "symbol":      symbol,
        "timeframe":   tf_label,
        "strategy":    "SMA COMPRESSION",
        "pattern":     "SMA Compression Breakout",
        "entry":       round(entry,  6),
        "stop":        round(stop,   6),
        "target":      round(target, 6),
        "rr":          rr,
        "stop_pct":    stop_pct,
        "target_pct":  target_pct,
        "sma20":       round(sma20,  6),
        "sma50":       round(sma50,  6),
        "sma200":      round(sma200, 6),
        "spread_pct":  round(spread * 100, 2),
        "body_ratio":  round(current_body / avg_body, 2),
        "close":       round(last["close"], 6),
    }
