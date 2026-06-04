from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, date, timedelta

import pandas as pd
import pytz

from config import (
    MAG7, CRYPTO, TIMEFRAMES,
    SCAN_INTERVAL_SECONDS,
    MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE,
    MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE,
    EARNINGS_BUFFER_DAYS,
    SIGNAL_COOLDOWN_HOURS,
)
from data_fetcher import fetch_all_timeframes
from strategy import detect_signal, detect_sma_compression_breakout, detect_bearish_signal, detect_death_cross
from alerts import send_telegram_alert, send_startup_message
from sheets_logger import log_signal

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("scanner.log"),
    ],
)
log = logging.getLogger(__name__)

ET  = pytz.timezone("US/Eastern")
SGT = pytz.timezone("Asia/Singapore")

# ── Alert deduplication ───────────────────────────────────────────────────────
_alerted: set[tuple] = set()
_alerted_compression: set[tuple] = set()
_last_clear: date | None = None

# ── 24-hour signal cooldown per asset per direction ───────────────────────────
# Prevents same asset firing multiple times in same direction (e.g. 3× BTC long)
_signal_cooldown: dict[tuple, datetime] = {}


def _dedup_key(symbol: str, tf: str) -> tuple:
    today = datetime.now(timezone.utc).date()
    return (symbol, tf, today)


def _already_alerted(symbol: str, tf: str) -> bool:
    return _dedup_key(symbol, tf) in _alerted


def _mark_alerted(symbol: str, tf: str) -> None:
    _alerted.add(_dedup_key(symbol, tf))


def _is_in_cooldown(symbol: str, direction: str = "long") -> bool:
    """Returns True if this asset+direction fired a signal within the cooldown window."""
    key       = (symbol, direction)
    last_time = _signal_cooldown.get(key)
    if last_time is None:
        return False
    return (datetime.now(timezone.utc) - last_time) < timedelta(hours=SIGNAL_COOLDOWN_HOURS)


def _mark_cooldown(symbol: str, direction: str = "long") -> None:
    _signal_cooldown[(symbol, direction)] = datetime.now(timezone.utc)


def _clear_old_alerts() -> None:
    """Clear yesterday's alerts daily."""
    global _last_clear
    today = datetime.now(timezone.utc).date()
    if _last_clear != today:
        _alerted.clear()
        _alerted_compression.clear()
        _last_clear = today
        log.info("Alert deduplication cache cleared for new day.")


# ── Higher timeframe trend filter ─────────────────────────────────────────────

def _is_higher_tf_bullish(tf_data: dict) -> bool:
    """
    Returns True if 4H or Daily chart is in a bullish trend (close > SMA50).
    Blocks long signals when the macro trend is against us.
    If neither timeframe has enough data — allow the signal (don't block).
    """
    checked = False
    for tf in ("4h", "1d"):
        df = tf_data.get(tf)
        if df is not None and len(df) >= 52:
            checked = True
            sma50 = df["close"].rolling(50).mean().iloc[-1]
            if not pd.isna(sma50) and float(df["close"].iloc[-1]) > float(sma50):
                return True   # at least one higher TF is bullish — allow
    return not checked        # no data = don't block; all checked = bearish = block


# ── Market hours ──────────────────────────────────────────────────────────────

def _is_stock_market_open() -> bool:
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    market_open  = now_et.replace(hour=MARKET_OPEN_HOUR,  minute=MARKET_OPEN_MINUTE,  second=0)
    market_close = now_et.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0)
    return market_open <= now_et <= market_close


# ── Earnings blackout ─────────────────────────────────────────────────────────

def _near_earnings(symbol: str) -> bool:
    """
    Check if symbol has earnings within EARNINGS_BUFFER_DAYS.
    Uses yfinance calendar data. Returns False on any error (safe default).
    """
    if symbol not in MAG7:
        return False
    try:
        import yfinance as yf
        cal = yf.Ticker(symbol).calendar
        if cal is None or cal.empty:
            return False
        # calendar may have 'Earnings Date' row
        if "Earnings Date" in cal.index:
            earnings_date = cal.loc["Earnings Date"].iloc[0]
            if hasattr(earnings_date, "date"):
                earnings_date = earnings_date.date()
            days_away = (earnings_date - datetime.now(timezone.utc).date()).days
            if abs(days_away) <= EARNINGS_BUFFER_DAYS:
                log.info(f"{symbol} earnings in {days_away} days — skipping")
                return True
    except Exception:
        pass
    return False


# ── Core scan ─────────────────────────────────────────────────────────────────

def scan_symbol(symbol: str, is_stock: bool) -> None:
    """Run the full strategy check for one symbol across all timeframes."""

    # Market hours gate for stocks
    if is_stock and not _is_stock_market_open():
        return

    # Earnings blackout
    if _near_earnings(symbol):
        return

    log.info(f"Scanning {symbol} ...")
    tf_data = fetch_all_timeframes(symbol)

    # Higher timeframe trend — used to block compression longs in macro downtrend
    macro_bullish = _is_higher_tf_bullish(tf_data)
    if not macro_bullish:
        log.info(f"  {symbol}: 4H/1D trend bearish — skipping long compression signals")

    for tf_label, _, _, _ in TIMEFRAMES:
        df = tf_data.get(tf_label)
        if df is None:
            continue

        # ── Long strategies (VWAP, Golden Cross, etc.) ───────────────────────
        if not _already_alerted(symbol, tf_label):
            if macro_bullish:
                signal = detect_signal(df, symbol, tf_label)
                if signal is not None:
                    log.info(
                        f"  SIGNAL: {symbol} {tf_label} | "
                        f"{signal['strength']} | {signal['pattern']} | "
                        f"R:R {signal['rr']}"
                    )
                    sent = send_telegram_alert(signal)
                    if sent:
                        _mark_alerted(symbol, tf_label)
                        log_signal(signal)

            # SHORT strategies disabled — validated 49.4% WR vs 83.3% for LONG 1H/2H/4H

        # ── SMA Compression Breakout (long only) ─────────────────────────────
        compression_key = (_dedup_key(symbol, tf_label), "compression")
        if compression_key in _alerted_compression:
            continue

        # Block compression longs if macro trend is bearish
        if not macro_bullish:
            continue

        # Block if same asset fired a long signal in the last 24 hours
        if _is_in_cooldown(symbol, "long"):
            log.info(f"  {symbol} {tf_label}: in 24h cooldown — skipping compression")
            continue

        csignal = detect_sma_compression_breakout(df, symbol, tf_label)
        if csignal is not None:
            log.info(
                f"  COMPRESSION: {symbol} {tf_label} | "
                f"{csignal['strength']} | {csignal['structure']} | "
                f"spread {csignal['spread_pct']}% | R:R {csignal['rr']}"
            )
            sent = send_telegram_alert(csignal)
            if sent:
                _alerted_compression.add(compression_key)
                _mark_cooldown(symbol, "long")   # start 24h cooldown
                log_signal(csignal)


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_scanner() -> None:
    log.info("=" * 60)
    log.info("Trading Strategy Scanner — Starting")
    log.info(f"Assets  : {MAG7 + CRYPTO}")
    log.info(f"Strategy: SMA 20/50/200 + Stochastic + Volume + ADX")
    log.info(f"Interval: every {SCAN_INTERVAL_SECONDS}s")
    log.info("=" * 60)

    send_startup_message()

    scan_number = 0

    while True:
        _clear_old_alerts()
        scan_number += 1
        now_sgt = datetime.now(timezone.utc).astimezone(SGT)
        log.info(f"--- Scan #{scan_number} | {now_sgt.strftime('%Y-%m-%d %H:%M')} SGT ---")

        for symbol in MAG7:
            try:
                scan_symbol(symbol, is_stock=True)
            except Exception as e:
                log.error(f"Error scanning {symbol}: {e}")

        for symbol in CRYPTO:
            try:
                scan_symbol(symbol, is_stock=False)
            except Exception as e:
                log.error(f"Error scanning {symbol}: {e}")

        log.info(f"--- Scan #{scan_number} complete. Next scan in {SCAN_INTERVAL_SECONDS}s ---\n")
        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_scanner()
