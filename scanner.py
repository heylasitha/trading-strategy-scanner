from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, date

import pytz

from config import (
    MAG7, CRYPTO, TIMEFRAMES,
    SCAN_INTERVAL_SECONDS,
    MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE,
    MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE,
    EARNINGS_BUFFER_DAYS,
)
from data_fetcher import fetch_all_timeframes
from strategy import detect_signal, detect_sma_compression_breakout
from alerts import send_telegram_alert, send_startup_message

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
# Tracks (symbol, timeframe, date) so we alert once per candle day per setup
_alerted: set[tuple] = set()
_alerted_compression: set[tuple] = set()
_last_clear: date | None = None


def _dedup_key(symbol: str, tf: str) -> tuple:
    today = datetime.now(timezone.utc).date()
    return (symbol, tf, today)


def _already_alerted(symbol: str, tf: str) -> bool:
    return _dedup_key(symbol, tf) in _alerted


def _mark_alerted(symbol: str, tf: str) -> None:
    _alerted.add(_dedup_key(symbol, tf))


def _clear_old_alerts() -> None:
    """Clear yesterday's alerts daily."""
    global _last_clear
    today = datetime.now(timezone.utc).date()
    if _last_clear != today:
        _alerted.clear()
        _alerted_compression.clear()
        _last_clear = today
        log.info("Alert deduplication cache cleared for new day.")


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

    for tf_label, _, _, _ in TIMEFRAMES:
        df = tf_data.get(tf_label)
        if df is None:
            continue

        if _already_alerted(symbol, tf_label):
            continue

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

        # SMA Compression Breakout — separate dedup
        compression_key = (_dedup_key(symbol, tf_label), "compression")
        if compression_key not in _alerted_compression:
            csignal = detect_sma_compression_breakout(df, symbol, tf_label)
            if csignal is not None:
                log.info(
                    f"  COMPRESSION: {symbol} {tf_label} | "
                    f"spread {csignal['spread_pct']}% | body {csignal['body_ratio']}x"
                )
                sent = send_telegram_alert(csignal)
                if sent:
                    _alerted_compression.add(compression_key)


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
