"""
One-shot scanner for GitHub Actions.
Runs once, sends alerts, saves state, exits.
GitHub Actions triggers this every 5 minutes via cron.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import pytz

from config import MAG7, EXTRA_STOCKS, CRYPTO, TIMEFRAMES, EARNINGS_BUFFER_DAYS
from data_fetcher import fetch_all_timeframes
from strategy import detect_signal, detect_golden_cross, detect_vwap_bounce
from alerts import send_telegram_alert, send_startup_message

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

SGT        = pytz.timezone("Asia/Singapore")
ET         = pytz.timezone("US/Eastern")
STATE_FILE = Path("state.json")


# ── State management ──────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _state_key(symbol: str, tf: str) -> str:
    return f"{symbol}_{tf}"


def already_alerted(state: dict, symbol: str, tf: str) -> bool:
    key   = symbol if tf == "" else _state_key(symbol, tf)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return state.get(key) == today


def mark_alerted(state: dict, symbol: str, tf: str) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key   = symbol if tf == "" else _state_key(symbol, tf)
    state[key] = today


# ── Market hours ──────────────────────────────────────────────────────────────

def is_stock_market_open() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    open_time  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    close_time = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return open_time <= now <= close_time


# ── Earnings blackout ─────────────────────────────────────────────────────────

def near_earnings(symbol: str) -> bool:
    if symbol not in MAG7:
        return False
    try:
        import yfinance as yf
        cal = yf.Ticker(symbol).calendar
        if cal is None or cal.empty:
            return False
        if "Earnings Date" in cal.index:
            earnings_date = cal.loc["Earnings Date"].iloc[0]
            if hasattr(earnings_date, "date"):
                earnings_date = earnings_date.date()
            days_away = (earnings_date - datetime.now(timezone.utc).date()).days
            if abs(days_away) <= EARNINGS_BUFFER_DAYS:
                log.info(f"{symbol}: earnings in {days_away} days — skipping")
                return True
    except Exception:
        pass
    return False


# ── Core scan ─────────────────────────────────────────────────────────────────

def scan_symbol(symbol: str, is_stock: bool, state: dict) -> int:
    """Scan one symbol. Returns number of alerts sent."""
    if is_stock and not is_stock_market_open():
        log.info(f"{symbol}: market closed — skipping")
        return 0

    if near_earnings(symbol):
        return 0

    log.info(f"Scanning {symbol} ...")
    tf_data = fetch_all_timeframes(symbol)
    alerts_sent = 0

    for tf_label, _, _, _ in TIMEFRAMES:
        df = tf_data.get(tf_label)
        if df is None:
            continue

        # ── Strategy 1: SMA Momentum ─────────────────────────────────────
        s1_key = f"s1_{symbol}_{tf_label}"
        if not already_alerted(state, s1_key, ""):
            signal = detect_signal(df, symbol, tf_label)
            if signal:
                log.info(f"  S1 SIGNAL: {symbol} {tf_label} | {signal['strength']} | {signal['pattern']}")
                if send_telegram_alert(signal):
                    mark_alerted(state, s1_key, "")
                    alerts_sent += 1
            else:
                log.info(f"  {symbol} {tf_label}: no S1 signal")

        # ── Strategy 2: Golden Cross ──────────────────────────────────────
        s2_key = f"s2_{symbol}_{tf_label}"
        if not already_alerted(state, s2_key, ""):
            gc_signal = detect_golden_cross(df, symbol, tf_label)
            if gc_signal:
                log.info(f"  GOLDEN CROSS: {symbol} {tf_label} | {gc_signal['strength']}")
                if send_telegram_alert(gc_signal):
                    mark_alerted(state, s2_key, "")
                    alerts_sent += 1

        # ── Strategy 3: VWAP Bounce ───────────────────────────────────────
        s3_key = f"s3_{symbol}_{tf_label}"
        if not already_alerted(state, s3_key, ""):
            vwap_signal = detect_vwap_bounce(df, symbol, tf_label, mag7=MAG7, crypto_symbols=CRYPTO)
            if vwap_signal:
                log.info(f"  VWAP BOUNCE: {symbol} {tf_label} | {vwap_signal['strength']}")
                if send_telegram_alert(vwap_signal):
                    mark_alerted(state, s3_key, "")
                    alerts_sent += 1

    return alerts_sent


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    now_sgt = datetime.now(timezone.utc).astimezone(SGT)
    log.info("=" * 55)
    log.info(f"Trading Scanner — {now_sgt.strftime('%Y-%m-%d %H:%M')} SGT")
    log.info("=" * 55)

    # Send startup message only on first run of the day
    state = load_state()
    first_run_key = f"_startup_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    if first_run_key not in state:
        send_startup_message()
        state[first_run_key] = "sent"

    total_alerts = 0

    for symbol in MAG7 + EXTRA_STOCKS:
        try:
            total_alerts += scan_symbol(symbol, is_stock=True, state=state)
        except Exception as e:
            log.error(f"Error scanning {symbol}: {e}")

    for symbol in CRYPTO:
        try:
            total_alerts += scan_symbol(symbol, is_stock=False, state=state)
        except Exception as e:
            log.error(f"Error scanning {symbol}: {e}")

    save_state(state)
    log.info(f"Scan complete. {total_alerts} alert(s) sent.")


if __name__ == "__main__":
    main()
