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
        f"  ADX     : {signal['adx']:.1f}  ✅ (>{signal.get('adx_threshold', 25)})",
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


def _format_golden_cross(signal: dict) -> str:
    sym      = signal["symbol"]
    tf       = signal["timeframe"]
    strength = signal["strength"]

    emoji    = STRENGTH_EMOJI.get(strength, "📊")

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
    sma200 = _format_price(signal["sma200"], sym)
    close  = _format_price(signal["close"],  sym)

    lines = [
        f"{emoji} ✨ GOLDEN CROSS DETECTED",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Asset     : {sym}",
        f"Timeframe : {tf.upper()}",
        f"Signal    : SMA 20 crossed ABOVE SMA 200",
        f"Strength  : {strength}",
        f"",
        f"📊 SMA Cross:",
        f"  SMA 20  : {sma20}  ✅ Now above",
        f"  SMA 200 : {sma200}",
        f"  Price   : {close}  ✅ Above both",
        f"",
        f"💪 Trend Strength",
        f"  ADX     : {signal['adx']:.1f}  ✅ Trending",
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
        f"⚠️  New trend beginning — confirm on candle close",
    ]
    return "\n".join(lines)


def _format_vwap_bounce(signal: dict) -> str:
    sym      = signal["symbol"]
    tf       = signal["timeframe"]
    strength = signal["strength"]
    emoji    = STRENGTH_EMOJI.get(strength, "📊")

    now_utc  = datetime.now(timezone.utc)
    now_sgt  = now_utc.astimezone(SGT)
    now_et   = now_utc.astimezone(ET)
    time_str = (
        f"{now_sgt.strftime('%Y-%m-%d %H:%M')} SGT  |  "
        f"{now_et.strftime('%H:%M')} ET"
    )

    entry      = _format_price(signal["entry"],      sym)
    stop       = _format_price(signal["stop"],       sym)
    target     = _format_price(signal["target"],     sym)
    vwap       = _format_price(signal["vwap"],       sym)
    vwap_upper = _format_price(signal["vwap_upper"], sym)
    vwap_lower = _format_price(signal["vwap_lower"], sym)

    lines = [
        f"{emoji} 💧 VWAP BOUNCE DETECTED",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Asset     : {sym}",
        f"Timeframe : {tf.upper()}",
        f"Pattern   : {signal['pattern']}",
        f"Strength  : {strength}",
        f"",
        f"📊 VWAP Bands:",
        f"  Upper (+1SD) : {vwap_upper}  🎯 Target",
        f"  VWAP Middle  : {vwap}   ← Bounce here",
        f"  Lower (-1SD) : {vwap_lower}  🛑 Stop zone",
        f"",
        f"📉 SMA Trend Confirmation:",
        f"  SMA 20  : {_format_price(signal['sma20'],  sym)}  ✅",
        f"  SMA 200 : {_format_price(signal['sma200'], sym)}  ✅",
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
        f"⚠️  Price bounced off VWAP — ride to upper band",
    ]
    return "\n".join(lines)


def _format_vwap_fakeout(signal: dict) -> str:
    sym      = signal["symbol"]
    tf       = signal["timeframe"]
    strength = signal["strength"]
    emoji    = STRENGTH_EMOJI.get(strength, "📊")

    now_utc  = datetime.now(timezone.utc)
    now_sgt  = now_utc.astimezone(SGT)
    now_et   = now_utc.astimezone(ET)
    time_str = (
        f"{now_sgt.strftime('%Y-%m-%d %H:%M')} SGT  |  "
        f"{now_et.strftime('%H:%M')} ET"
    )

    entry      = _format_price(signal["entry"],      sym)
    stop       = _format_price(signal["stop"],       sym)
    target     = _format_price(signal["target"],     sym)
    vwap       = _format_price(signal["vwap"],       sym)
    vwap_upper = _format_price(signal["vwap_upper"], sym)
    fakeout    = _format_price(signal["fakeout_low"],sym)

    lines = [
        f"{emoji} 🪤 VWAP FAKEOUT REVERSAL",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Asset     : {sym}",
        f"Timeframe : {tf.upper()}",
        f"Pattern   : {signal['pattern']}",
        f"Strength  : {strength}",
        f"",
        f"📊 VWAP Levels:",
        f"  Upper (+1SD) : {vwap_upper}  🎯 Target",
        f"  VWAP Middle  : {vwap}",
        f"  Fakeout Low  : {fakeout}  🪤 Stop hunt",
        f"",
        f"📉 Trend Confirmation:",
        f"  SMA 20  : {_format_price(signal['sma20'],  sym)}  ✅",
        f"  SMA 200 : {_format_price(signal['sma200'], sym)}  ✅",
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
        f"⚠️  Stop hunt confirmed — ride the reversal",
    ]
    return "\n".join(lines)


def _format_short_message(signal: dict) -> str:
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
        f"{emoji} 🔴 SHORT — {priority}",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Asset     : {sym}",
        f"Timeframe : {tf.upper()}",
        f"Pattern   : {pattern}",
        f"Strength  : {strength}",
        f"Direction : SHORT 🔴",
        f"",
        f"📊 SMA Stack (Bearish ✅)",
        f"  SMA 20  : {sma20}",
        f"  SMA 50  : {sma50}",
        f"  SMA 200 : {sma200}",
        f"  Price   : {close}",
        f"",
        f"📉 Momentum",
        f"  Stoch K : {signal['stoch_k']:.1f}",
        f"  Stoch D : {signal['stoch_d']:.1f}",
        f"  K < D   : ✅ Falling",
        f"",
        f"💪 Trend Strength",
        f"  ADX     : {signal['adx']:.1f}  ✅ (>{signal.get('adx_threshold', 30)})",
        f"",
        f"📦 Volume",
        f"  Ratio   : {signal['volume_ratio']:.1f}x avg  ✅",
        f"",
        f"🎯 Trade Levels",
        f"  Entry   : {entry}",
        f"  Stop    : {stop}  (+{signal['stop_pct']}%)",
        f"  Target  : {target}  (-{signal['target_pct']}%)",
        f"  R:R     : {signal['rr']} : 1",
        f"",
        f"⏰ {time_str}",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"⚠️  Confirm on candle close before entry",
    ]
    return "\n".join(lines)


def _format_death_cross(signal: dict) -> str:
    sym      = signal["symbol"]
    tf       = signal["timeframe"]
    strength = signal["strength"]
    emoji    = STRENGTH_EMOJI.get(strength, "📊")

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
    sma200 = _format_price(signal["sma200"], sym)
    close  = _format_price(signal["close"],  sym)

    lines = [
        f"{emoji} 💀 DEATH CROSS DETECTED",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Asset     : {sym}",
        f"Timeframe : {tf.upper()}",
        f"Signal    : SMA 20 crossed BELOW SMA 200",
        f"Strength  : {strength}",
        f"Direction : SHORT 🔴",
        f"",
        f"📊 SMA Cross:",
        f"  SMA 20  : {sma20}  ✅ Now below",
        f"  SMA 200 : {sma200}",
        f"  Price   : {close}  ✅ Below both",
        f"",
        f"💪 Trend Strength",
        f"  ADX     : {signal['adx']:.1f}  ✅ Trending",
        f"",
        f"📦 Volume",
        f"  Ratio   : {signal['volume_ratio']:.1f}x avg  ✅",
        f"",
        f"🎯 Trade Levels",
        f"  Entry   : {entry}",
        f"  Stop    : {stop}  (+{signal['stop_pct']}%)",
        f"  Target  : {target}  (-{signal['target_pct']}%)",
        f"  R:R     : {signal['rr']} : 1",
        f"",
        f"⏰ {time_str}",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"⚠️  New downtrend beginning — confirm on candle close",
    ]
    return "\n".join(lines)


def _format_vwap_rejection(signal: dict) -> str:
    sym      = signal["symbol"]
    tf       = signal["timeframe"]
    strength = signal["strength"]
    emoji    = STRENGTH_EMOJI.get(strength, "📊")

    now_utc  = datetime.now(timezone.utc)
    now_sgt  = now_utc.astimezone(SGT)
    now_et   = now_utc.astimezone(ET)
    time_str = (
        f"{now_sgt.strftime('%Y-%m-%d %H:%M')} SGT  |  "
        f"{now_et.strftime('%H:%M')} ET"
    )

    entry      = _format_price(signal["entry"],      sym)
    stop       = _format_price(signal["stop"],       sym)
    target     = _format_price(signal["target"],     sym)
    vwap       = _format_price(signal["vwap"],       sym)
    vwap_upper = _format_price(signal["vwap_upper"], sym)
    vwap_lower = _format_price(signal["vwap_lower"], sym)

    lines = [
        f"{emoji} 🔴 VWAP REJECTION DETECTED",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Asset     : {sym}",
        f"Timeframe : {tf.upper()}",
        f"Pattern   : {signal['pattern']}",
        f"Strength  : {strength}",
        f"Direction : SHORT 🔴",
        f"",
        f"📊 VWAP Bands:",
        f"  Upper (+1SD) : {vwap_upper}  🛑 Stop zone",
        f"  VWAP Middle  : {vwap}   ← Rejected here",
        f"  Lower (-1SD) : {vwap_lower}  🎯 Target",
        f"",
        f"📉 SMA Trend Confirmation:",
        f"  SMA 20  : {_format_price(signal['sma20'],  sym)}  ✅",
        f"  SMA 200 : {_format_price(signal['sma200'], sym)}  ✅",
        f"",
        f"📦 Volume",
        f"  Ratio   : {signal['volume_ratio']:.1f}x avg  ✅",
        f"",
        f"🎯 Trade Levels",
        f"  Entry   : {entry}",
        f"  Stop    : {stop}  (+{signal['stop_pct']}%)",
        f"  Target  : {target}  (-{signal['target_pct']}%)",
        f"  R:R     : {signal['rr']} : 1",
        f"",
        f"⏰ {time_str}",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"⚠️  Price rejected VWAP — ride to lower band",
    ]
    return "\n".join(lines)


def send_telegram_alert(signal: dict) -> bool:
    """Format and send a Telegram message. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Telegram credentials missing — check .env file")
        return False

    strategy = signal.get("strategy")
    if strategy == "GOLDEN CROSS":
        message = _format_golden_cross(signal)
    elif strategy == "VWAP BOUNCE":
        message = _format_vwap_bounce(signal)
    elif strategy == "VWAP FAKEOUT":
        message = _format_vwap_fakeout(signal)
    elif strategy == "BEARISH SMA":
        message = _format_short_message(signal)
    elif strategy == "DEATH CROSS":
        message = _format_death_cross(signal)
    elif strategy == "VWAP REJECTION":
        message = _format_vwap_rejection(signal)
    else:
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
        "  📊 Stocks: VOO QQQM TSM AMD\n"
        "  🪙 Crypto: BTC ETH LTC\n\n"
        "Timeframes: 15m 30m 1h 2h 4h 1d 1w\n\n"
        "Long Strategies:\n"
        "  📈 S1: SMA 20/50/200 Bullish Stack\n"
        "  📈 S2: Golden Cross (SMA20 > SMA200)\n"
        "  📈 S3: VWAP Bounce\n"
        "  📈 S4: VWAP Fakeout Reversal\n\n"
        "Short Strategies:\n"
        "  📉 S5: SMA 20/50/200 Bearish Stack\n"
        "  📉 S6: Death Cross (SMA20 < SMA200)\n"
        "  📉 S7: Bearish VWAP Rejection\n\n"
        f"⏰ {now_sgt.strftime('%Y-%m-%d %H:%M')} SGT\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        log.error(f"Could not send startup message: {e}")
