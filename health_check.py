"""
Pre-market health check — runs every weekday at 9:00 AM ET (before market opens).
Verifies all systems are working correctly and sends a Telegram summary.
If anything is broken — alerts immediately so you know BEFORE trading starts.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import pytz
import requests
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

SGT = pytz.timezone("Asia/Singapore")
ET  = pytz.timezone("US/Eastern")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# Test symbols — one from each group
TEST_STOCKS = ["AAPL", "NVDA", "GOOGL", "TSM", "PLTR"]
TEST_CRYPTO = ["BTC-USD", "ETH-USD"]

# Expected price ranges (sanity check — if price is outside this range something is wrong)
PRICE_SANITY = {
    "AAPL":    (50,   1000),
    "NVDA":    (10,   2000),
    "GOOGL":   (50,   1000),
    "TSM":     (50,   1000),
    "PLTR":    (1,    500),
    "BTC-USD": (10000, 200000),
    "ETH-USD": (500,  20000),
}


def _send_telegram(msg: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Telegram credentials missing")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10,
        )
    except Exception as e:
        log.error(f"Telegram send failed: {e}")


def check_yfinance_prices() -> tuple[bool, list[str]]:
    """Check yfinance returns correct prices for test symbols."""
    issues = []
    for symbol in TEST_STOCKS + TEST_CRYPTO:
        try:
            ticker = yf.Ticker(symbol)
            hist   = ticker.history(period="2d", interval="1h")
            if hist.empty or len(hist) < 2:
                issues.append(f"❌ {symbol}: no data returned")
                continue
            price = float(hist["Close"].iloc[-1])
            lo, hi = PRICE_SANITY.get(symbol, (0, 9999999))
            if not (lo < price < hi):
                issues.append(f"❌ {symbol}: price ${price:.2f} outside expected range ${lo}–${hi} — possible data error")
            else:
                log.info(f"✅ {symbol}: ${price:.2f}")
        except Exception as e:
            issues.append(f"❌ {symbol}: exception — {e}")
    return len(issues) == 0, issues


def check_telegram() -> tuple[bool, str]:
    """Check Telegram credentials are present."""
    if not TELEGRAM_BOT_TOKEN:
        return False, "❌ TELEGRAM_BOT_TOKEN missing"
    if not TELEGRAM_CHAT_ID:
        return False, "❌ TELEGRAM_CHAT_ID missing"
    return True, "✅ Telegram credentials present"


def check_google_sheets() -> tuple[bool, str]:
    """Check Google Sheets credentials are present and valid JSON."""
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        return False, "❌ GOOGLE_CREDENTIALS_JSON missing"
    try:
        info = json.loads(creds_json)
        if "client_email" not in info:
            return False, "❌ GOOGLE_CREDENTIALS_JSON invalid — missing client_email"
        return True, f"✅ Google Sheets credentials valid ({info.get('client_email','')})"
    except Exception as e:
        return False, f"❌ GOOGLE_CREDENTIALS_JSON invalid JSON — {e}"


def check_strategy_logic() -> tuple[bool, list[str]]:
    """Run detect_signal on live data and verify it returns correct price."""
    issues = []
    try:
        import sys
        sys.path.insert(0, ".")
        from data_fetcher import fetch_ohlcv
        from strategy import detect_signal

        for symbol in ["AAPL", "NVDA"]:
            df = fetch_ohlcv(symbol, "1h")
            if df is None or df.empty:
                issues.append(f"❌ {symbol} 1H: fetch_ohlcv returned no data")
                continue
            price = float(df["close"].iloc[-1])
            lo, hi = PRICE_SANITY.get(symbol, (0, 9999999))
            if not (lo < price < hi):
                issues.append(f"❌ {symbol} 1H: last close ${price:.2f} outside expected range — data error")
            else:
                log.info(f"✅ {symbol} 1H data: last close ${price:.2f}")

            # Run signal detection — should not crash
            try:
                sig = detect_signal(df, symbol, "1h")
                log.info(f"✅ {symbol} detect_signal: {'SIGNAL' if sig else 'no signal (ok)'}")
            except Exception as e:
                issues.append(f"❌ {symbol} detect_signal crashed: {e}")

    except Exception as e:
        issues.append(f"❌ Strategy import/run failed: {e}")

    return len(issues) == 0, issues


def run_health_check() -> None:
    now_sgt = datetime.now(timezone.utc).astimezone(SGT)
    now_et  = datetime.now(timezone.utc).astimezone(ET)
    log.info(f"=== Health Check {now_sgt.strftime('%Y-%m-%d %H:%M')} SGT ===")

    results   = []
    all_ok    = True

    # 1. Telegram
    ok, msg = check_telegram()
    results.append(msg)
    if not ok:
        all_ok = False

    # 2. Google Sheets
    ok, msg = check_google_sheets()
    results.append(msg)
    if not ok:
        all_ok = False

    # 3. yfinance prices
    ok, issues = check_yfinance_prices()
    if ok:
        results.append(f"✅ yfinance prices correct for all {len(TEST_STOCKS + TEST_CRYPTO)} symbols")
    else:
        results.append("❌ yfinance price issues:")
        results.extend(issues)
        all_ok = False

    # 4. Strategy logic
    ok, issues = check_strategy_logic()
    if ok:
        results.append("✅ Strategy logic (fetch + detect_signal) working correctly")
    else:
        results.append("❌ Strategy logic issues:")
        results.extend(issues)
        all_ok = False

    # Build Telegram message
    status_icon = "✅ ALL SYSTEMS GO" if all_ok else "🚨 ISSUES DETECTED — CHECK BEFORE TRADING"

    msg = (
        f"🔍 PRE-MARKET HEALTH CHECK\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{now_sgt.strftime('%Y-%m-%d %H:%M')} SGT | {now_et.strftime('%H:%M')} ET\n"
        f"\n"
        f"Status: {status_icon}\n"
        f"\n"
        + "\n".join(results) +
        f"\n\n"
        f"Scanner: SMA MOMENTUM 1H/2H\n"
        f"Stocks : MAG7 + CHIPS + AI_SOFTWARE\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    _send_telegram(msg)
    log.info(f"Health check complete — {'ALL OK' if all_ok else 'ISSUES FOUND'}")

    if not all_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    run_health_check()
