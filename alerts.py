from __future__ import annotations

import logging
import requests
from datetime import datetime, timezone

import pytz

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger(__name__)

SGT = pytz.timezone("Asia/Singapore")
ET  = pytz.timezone("US/Eastern")

STRENGTH_EMOJI = {
    "STRONG":   "🔥",
    "MODERATE": "📈",
    "WATCH":    "👀",
}

TF_PRIORITY = {
    "15m": "⚡ QUICK TRADE",
    "1h":  "🎯 TRADE SETUP",
    "2h":  "🎯 TRADE SETUP",
    "4h":  "💎 HIGH CONVICTION",
    "1d":  "🌐 SWING SETUP",
    "1w":  "🏔 MAJOR SIGNAL",
}


def _format_price(value: float, symbol: str) -> str:
    if "BTC" in symbol:
        return f"${value:,.2f}"
    if value >= 1000:
        return f"${value:,.2f}"
    if value >= 1:
        return f"${value:.4f}"
    return f"${value:.6f}"


def _format_message(signal: dict) -> str:
    sym      = signal["symbol"]
    tf       = signal["timeframe"]
    strength = signal["strength"]
    pattern  = signal["pattern"]

    emoji    = STRENGTH_EMOJI.get(strength, "📊")
    priority = TF_PRIORITY.get(tf, "📊 SETUP")

    now_utc = datetime.now(timezone.utc)
    now_sgt = now_utc.astimezone(SGT)
    now_et  = now_utc.astimezone(ET)

    time_str = (
        f"{now_sgt.strftime('%Y-%m-%d %H:%M')} SGT  |  "
        f"{now_et.strftime('%H:%M')} ET"
    )

    entry  = _format_price(signal["entry"],  sym)
    stop   = _format_price(signal["stop"],   sym)
    target = _format_price(signal["target"], sym)

    sma20  = _format_price(signal["sma20"],  sym)
    sma50  = _format_price(signal["sma50"],  sym)
    sma200 = _format_price(signal["sma200"], sym)
    close  = _format_price(signal["close"],  sym)

    lines = [
        f"{emoji} {priority}",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Asset     : {sym}",
        f"Timeframe : {tf.upper()}",
        f"Pattern   : {pattern}",
        f"Strength  : {strength}",
        f"",
        f"📊 SMA Stack (Bullish ✅)",
        f"  SMA 20  : {sma20}",
        f"  SMA 50  : {sma50}",
        f"  SMA 200 : {sma200}",
        f"  Price   : {close}",
        f"",
        f"📈 Momentum",
        f"  Stoch K : {signal['stoch_k']:.1f}",
        f"  Stoch D : {signal['stoch_d']:.1f}",
        f"  K > D   : ✅ Rising",
        f"",
        f"💪 Trend Strength",
        f"  ADX     : {signal['adx']:.1f}  ✅ (>{25})",
        f"",
        f"📦 Volume",
        f"  Ratio   : {signal['volume_ratio']:.1f}x avg  ✅",
        f"",
        f"🎯 Trade Levels",
        f"  Entry   : {entry}",
        f"  Stop    : {stop}  (-{signal['stop_pct']}%)",
        f"  Target  : {target}  (+{signal['target_pct']}%)",
        f"  R:R     : {signal['rr']} : 1",
        f"",
        f"⏰ {time_str}",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"⚠️  Confirm on candle close before entry",
    ]

    return "\n".join(lines)


def send_telegram_alert(signal: dict) -> bool:
    """Format and send a Telegram message. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Telegram credentials missing — check .env file")
        return False

    message = _format_message(signal)
    url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       message,
        "parse_mode": "HTML",
    }

    try:
        resp = requests.post(url, data=payload, timeout=10)
        if resp.status_code == 200:
            log.info(f"Alert sent: {signal['symbol']} {signal['timeframe']} [{signal['strength']}]")
            return True
        else:
            log.error(f"Telegram error {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        log.error(f"Failed to send Telegram alert: {e}")
        return False


def send_startup_message() -> None:
    """Send a startup notification so you know the scanner is live."""
    now_sgt = datetime.now(timezone.utc).astimezone(SGT)
    msg = (
        "🚀 Trading Scanner STARTED\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Watching:\n"
        "  📊 Mag7: AAPL MSFT NVDA GOOGL AMZN META TSLA\n"
        "  🪙 Crypto: BTC ETH LTC\n\n"
        "Timeframes: 15m 1h 2h 4h 1d 1w\n\n"
        "Strategy:\n"
        "  ✅ SMA 20/50/200 Alignment\n"
        "  ✅ Stochastic K > D Rising\n"
        "  ✅ Volume > 1.5x Average\n"
        "  ✅ ADX > 25 (Strong Trend)\n"
        "  ✅ 3-bar Higher TF Confirmation\n\n"
        f"⏰ {now_sgt.strftime('%Y-%m-%d %H:%M')} SGT\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Alerts will fire when ALL 7 conditions pass."
    )
    url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        log.error(f"Could not send startup message: {e}")
