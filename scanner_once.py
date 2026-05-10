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
from strategy import (
    detect_signal, detect_golden_cross, detect_vwap_bounce, detect_vwap_fakeout,
    detect_bearish_signal, detect_death_cross, detect_vwap_rejection,
    detect_orb_long, detect_orb_short, detect_sma_compression_breakout,
)
from alerts import send_telegram_alert, send_startup_message
from sheets_logger import log_signal

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
    """Earnings blackout check — disabled (yfinance removed). Always returns False."""
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
                    log_signal(signal)
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
                    log_signal(gc_signal)
                    mark_alerted(state, s2_key, "")
                    alerts_sent += 1

        # ── Strategy 3: VWAP Bounce ───────────────────────────────────────
        s3_key = f"s3_{symbol}_{tf_label}"
        if not already_alerted(state, s3_key, ""):
            vwap_signal = detect_vwap_bounce(df, symbol, tf_label, mag7=MAG7, crypto_symbols=CRYPTO)
            if vwap_signal:
                log.info(f"  VWAP BOUNCE: {symbol} {tf_label} | {vwap_signal['strength']}")
                if send_telegram_alert(vwap_signal):
                    log_signal(vwap_signal)
                    mark_alerted(state, s3_key, "")
                    alerts_sent += 1

        # ── Strategy 4: VWAP Fakeout Reversal (Telegram only — no Sheets yet) ──
        s4_key = f"s4_{symbol}_{tf_label}"
        if not already_alerted(state, s4_key, ""):
            fakeout_signal = detect_vwap_fakeout(df, symbol, tf_label, mag7=MAG7, crypto_symbols=CRYPTO)
            if fakeout_signal:
                log.info(f"  VWAP FAKEOUT: {symbol} {tf_label} | {fakeout_signal['strength']}")
                if send_telegram_alert(fakeout_signal):
                    mark_alerted(state, s4_key, "")
                    alerts_sent += 1

        # ── Strategy 5: Bearish SMA Stack ────────────────────────────────────
        s5_key = f"s5_{symbol}_{tf_label}"
        if not already_alerted(state, s5_key, ""):
            bear_signal = detect_bearish_signal(df, symbol, tf_label)
            if bear_signal:
                log.info(f"  BEARISH SMA: {symbol} {tf_label} | {bear_signal['strength']} | {bear_signal['pattern']}")
                if send_telegram_alert(bear_signal):
                    log_signal(bear_signal)
                    mark_alerted(state, s5_key, "")
                    alerts_sent += 1
            else:
                log.info(f"  {symbol} {tf_label}: no S5 signal")

        # ── Strategy 6: Death Cross ───────────────────────────────────────────
        s6_key = f"s6_{symbol}_{tf_label}"
        if not already_alerted(state, s6_key, ""):
            dc_signal = detect_death_cross(df, symbol, tf_label)
            if dc_signal:
                log.info(f"  DEATH CROSS: {symbol} {tf_label} | {dc_signal['strength']}")
                if send_telegram_alert(dc_signal):
                    log_signal(dc_signal)
                    mark_alerted(state, s6_key, "")
                    alerts_sent += 1

        # ── Strategy 7: Bearish VWAP Rejection ───────────────────────────────
        s7_key = f"s7_{symbol}_{tf_label}"
        if not already_alerted(state, s7_key, ""):
            rejection_signal = detect_vwap_rejection(df, symbol, tf_label, mag7=MAG7, crypto_symbols=CRYPTO)
            if rejection_signal:
                log.info(f"  VWAP REJECTION: {symbol} {tf_label} | {rejection_signal['strength']}")
                if send_telegram_alert(rejection_signal):
                    log_signal(rejection_signal)
                    mark_alerted(state, s7_key, "")
                    alerts_sent += 1

        # ── Strategy 10: SMA Compression Breakout ────────────────────────────
        s10_key = f"s10_{symbol}_{tf_label}"
        if not already_alerted(state, s10_key, ""):
            compression_signal = detect_sma_compression_breakout(df, symbol, tf_label)
            if compression_signal:
                log.info(
                    f"  SMA COMPRESSION: {symbol} {tf_label} | "
                    f"spread {compression_signal['spread_pct']}% | body {compression_signal['body_ratio']}x"
                )
                if send_telegram_alert(compression_signal):
                    log_signal(compression_signal)
                    mark_alerted(state, s10_key, "")
                    alerts_sent += 1

    # ── Strategy 8/9: ORB — stocks only, 15m only, once per day ─────────────
    if is_stock:
        orb_df = tf_data.get("15m")
        if orb_df is not None:
            s8_key = f"s8_{symbol}"
            if not already_alerted(state, s8_key, ""):
                orb_long = detect_orb_long(orb_df, symbol, "15m")
                if orb_long:
                    log.info(f"  ORB LONG: {symbol} | {orb_long['strength']}")
                    if send_telegram_alert(orb_long):
                        log_signal(orb_long)
                        mark_alerted(state, s8_key, "")
                        alerts_sent += 1

            s9_key = f"s9_{symbol}"
            if not already_alerted(state, s9_key, ""):
                orb_short = detect_orb_short(orb_df, symbol, "15m")
                if orb_short:
                    log.info(f"  ORB SHORT: {symbol} | {orb_short['strength']}")
                    if send_telegram_alert(orb_short):
                        log_signal(orb_short)
                        mark_alerted(state, s9_key, "")
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
