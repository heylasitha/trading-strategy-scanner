from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, date

import pandas as pd
import pytz

from config import (
    MAG7, CHIPS, AI_SOFTWARE, CRYPTO, TIMEFRAMES,
    SCAN_INTERVAL_SECONDS,
    MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE,
    MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE,
    EARNINGS_BUFFER_DAYS,
)
from data_fetcher import fetch_all_timeframes
from strategy import detect_signal, detect_sma_compression_breakout, detect_bearish_signal, detect_death_cross, detect_avwap_reclaim, detect_pre_golden_cross
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

# ── AVWAP Reclaim symbol universe (backtested 18 months, 62.7% WR) ───────────
AVWAP_SYMBOLS = [
    "AAPL","MSFT","GOOGL","AMZN","META","NVDA","TSLA","AVGO","QCOM","TSM","MRVL","ASML",
    "AMAT","LRCX","KLAC","MU","TXN","CRM","DDOG","V","JPM","GS",
    "SPY","QQQ","SOXX","ETH-USD",
]

# ── Alert deduplication ───────────────────────────────────────────────────────
_alerted: set[tuple] = set()
_alerted_compression: set[tuple] = set()
_alerted_avwap: set[tuple] = set()
_last_clear: date | None = None

# ── Signal cooldown removed 2026-06-05 ───────────────────────────────────────
# Fixed 24H time-based cooldown was blocking valid re-entries in strong trends
# (e.g. AAPL passed all filters 10 days straight — zero signals sent)
# Spam protection now handled by:
#   1. sheets_logger duplicate guard (same entry price = blocked)
#   2. Daily dedup cache _alerted (same symbol+TF+day = one Telegram alert max)
#   3. ADX > 25 + SMA20>50>200 filters (quality control)
_signal_cooldown: dict[tuple, datetime] = {}  # kept for reference, no longer used


def _dedup_key(symbol: str, tf: str) -> tuple:
    today = datetime.now(timezone.utc).date()
    return (symbol, tf, today)


def _already_alerted(symbol: str, tf: str) -> bool:
    return _dedup_key(symbol, tf) in _alerted


def _mark_alerted(symbol: str, tf: str) -> None:
    _alerted.add(_dedup_key(symbol, tf))


# _is_in_cooldown and _mark_cooldown removed 2026-06-05
# Cooldown replaced by: sheets_logger duplicate guard + daily _alerted cache


def _clear_old_alerts() -> None:
    """Clear yesterday's alerts daily."""
    global _last_clear
    today = datetime.now(timezone.utc).date()
    if _last_clear != today:
        _alerted.clear()
        _alerted_compression.clear()
        _alerted_avwap.clear()
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


# ── SPY market trend check ───────────────────────────────────────────────────

_spy_trend_cache: dict = {"date": None, "bullish": None}

def _is_spy_bullish() -> bool:
    """
    Returns True if SPY is in an uptrend: close > SMA50 AND SMA50 rising.
    Cached once per day to avoid repeated downloads.
    """
    today = datetime.now(timezone.utc).date()
    if _spy_trend_cache["date"] == today and _spy_trend_cache["bullish"] is not None:
        return _spy_trend_cache["bullish"]
    try:
        import yfinance as yf
        spy = yf.Ticker("SPY").history(period="6mo", interval="1d", auto_adjust=True)
        if spy is None or len(spy) < 52:
            return True  # can't check — don't block
        spy.columns = [c.lower() for c in spy.columns]
        sma50 = spy["close"].rolling(50).mean()
        last_close = float(spy["close"].iloc[-1])
        last_sma50 = float(sma50.iloc[-1])
        sma50_5ago = float(sma50.iloc[-6])
        bullish = last_close > last_sma50 and last_sma50 > sma50_5ago
        _spy_trend_cache["date"]    = today
        _spy_trend_cache["bullish"] = bullish
        log.info(f"SPY trend: {'BULLISH' if bullish else 'BEARISH'} (close={last_close:.2f} sma50={last_sma50:.2f})")
        return bullish
    except Exception as e:
        log.warning(f"SPY trend check failed: {e}")
        return True  # fail open — don't block alerts


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
    if symbol not in MAG7 + CHIPS + AI_SOFTWARE:
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

        # ── Long strategies: SMA MOMENTUM on 1H + 2H only ───────────────────
        # 4H removed — validated 36.8% WR vs 85% for 2H / 68% for 1H
        if tf_label not in ("1h", "2h"):
            pass  # skip SMA MOMENTUM signal for this TF (still used for trend filter)
        elif not _already_alerted(symbol, tf_label):
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

        # ── Pre-Golden Cross Alert — all timeframes, SPY must be bullish ───────
        pre_key = (symbol, f"pre_gc_{tf_label}", datetime.now(timezone.utc).date())
        if pre_key not in _alerted and _is_spy_bullish():
            pre = detect_pre_golden_cross(df, symbol, tf_label)
            if pre is not None:
                msg = (
                    f"⚠️ INCOMING GOLDEN CROSS\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Symbol    : {symbol}\n"
                    f"Timeframe : {tf_label.upper()}\n"
                    f"SMA20     : {pre['sma20']}\n"
                    f"SMA200    : {pre['sma200']}\n"
                    f"Gap       : {pre['gap_pct']}% away\n"
                    f"Price     : {pre['close']}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"SMA20 rising and approaching SMA200 — cross imminent"
                )
                from alerts import send_telegram_message
                send_telegram_message(msg)
                _alerted.add(pre_key)
                log.info(f"  PRE-GOLDEN CROSS: {symbol} {tf_label} — {pre['gap_pct']}% away")

        # ── SMA Compression Breakout — DISABLED ──────────────────────────────
        # Validated 35.3% WR from 250 real trades (33% at 15M, 28% at 30M, 50% at 1H)
        # Insufficient edge — removed 2026-06-05


# ── AVWAP Reclaim scan ────────────────────────────────────────────────────────

def scan_avwap_reclaim(symbol: str, is_crypto: bool) -> None:
    """
    AVWAP Reclaim — 4H only, fires after each 4H candle close.
    Runs on all 24 backtested symbols regardless of market hours
    (crypto runs 24/7, stocks checked on 4H candle close).
    """
    # Stocks: only scan during market hours (4H candle closes at 10, 14 ET)
    if not is_crypto and not _is_stock_market_open():
        return

    dedup_key = (symbol, "4h", datetime.now(timezone.utc).date())
    if dedup_key in _alerted_avwap:
        return

    try:
        import yfinance as yf
        import numpy as np

        if is_crypto:
            import requests as _req
            crypto_map = {"ETH-USD": "ETHUSDT"}
            binance_sym = crypto_map.get(symbol)
            if not binance_sym:
                return
            r = _req.get("https://api.binance.us/api/v3/klines",
                         params={"symbol": binance_sym, "interval": "4h", "limit": 300},
                         timeout=15)
            if r.status_code != 200:
                return
            raw = r.json()
            df = pd.DataFrame(raw, columns=[
                "open_time","open","high","low","close","volume",
                "close_time","quote_vol","trades","taker_base","taker_quote","ignore"])
            df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            df = df.set_index("open_time")[["open","high","low","close","volume"]].astype(float)
        else:
            raw1h = yf.Ticker(symbol).history(period="2y", interval="1h", auto_adjust=True)
            if raw1h is None or raw1h.empty:
                return
            raw1h.columns = [c.lower() for c in raw1h.columns]
            raw1h = raw1h[["open","high","low","close","volume"]].astype(float)
            if raw1h.index.tz is None:
                raw1h.index = raw1h.index.tz_localize("UTC")
            else:
                raw1h.index = raw1h.index.tz_convert("UTC")
            df = raw1h.resample("4h").agg(
                {"open":"first","high":"max","low":"min","close":"last","volume":"sum"}
            ).dropna()

        if df is None or len(df) < 50:
            return

        df = df[~df.index.duplicated()].sort_index()
        signal = detect_avwap_reclaim(df, symbol, "4h")
        if signal is None:
            return

        log.info(f"  AVWAP RECLAIM: {symbol} 4H | {signal['strength']} | AVWAP={signal['avwap']:.2f}")
        sent = send_telegram_alert(signal)
        if sent:
            _alerted_avwap.add(dedup_key)
            log_signal(signal)

    except Exception as e:
        log.error(f"AVWAP scan error {symbol}: {e}")


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_scanner() -> None:
    log.info("=" * 60)
    log.info("Trading Strategy Scanner — Starting")
    log.info(f"Assets  : {MAG7 + CHIPS + AI_SOFTWARE + CRYPTO}")
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

        for symbol in MAG7 + CHIPS + AI_SOFTWARE:
            try:
                scan_symbol(symbol, is_stock=True)
            except Exception as e:
                log.error(f"Error scanning {symbol}: {e}")

        for symbol in CRYPTO:
            try:
                scan_symbol(symbol, is_stock=False)
            except Exception as e:
                log.error(f"Error scanning {symbol}: {e}")

        # ── AVWAP Reclaim — 4H, 24 symbols ───────────────────────────────────
        crypto_avwap = {"ETH-USD"}
        for symbol in AVWAP_SYMBOLS:
            try:
                scan_avwap_reclaim(symbol, is_crypto=(symbol in crypto_avwap))
            except Exception as e:
                log.error(f"AVWAP scan error {symbol}: {e}")

        log.info(f"--- Scan #{scan_number} complete. Next scan in {SCAN_INTERVAL_SECONDS}s ---\n")
        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_scanner()
