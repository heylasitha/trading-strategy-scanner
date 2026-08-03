"""
Momentum RSI + VIX Rank Scanner
Runs daily after market close — scans all 20 symbols
"""
from __future__ import annotations
import sys, os, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yfinance as yf
import pandas as pd
from datetime import datetime, timezone, date

sys.path.insert(0, os.path.dirname(__file__))
from mrsi_config import SYMBOLS
from mrsi_strategy import detect, get_vix_rank

from alerts import send_telegram_alert
from sheets_logger import log_signal
import requests as _req
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from alpaca_trader import place_buy

def send_telegram_message(msg: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        _req.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10,
        )
    except Exception as e:
        log.error(f"Telegram error: {e}")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("momentum_rsi.log")],
)
log = logging.getLogger(__name__)

_alerted: set[tuple] = set()


def _already_alerted(symbol: str) -> bool:
    return (symbol, date.today()) in _alerted


def _mark_alerted(symbol: str) -> None:
    _alerted.add((symbol, date.today()))


def run_scan() -> None:
    log.info("=" * 55)
    log.info(f"Momentum RSI Scanner — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    log.info("=" * 55)

    # Get VIX rank once for entire scan
    vix_rank = get_vix_rank()
    if vix_rank is None:
        log.warning("VIX rank unavailable — proceeding without filter")
    else:
        log.info(f"VIX Rank: {vix_rank:.1f} ({'✅ PASS' if vix_rank <= 70 else '❌ BLOCK — market too fearful'})")

    alerts_sent = 0

    for symbol in SYMBOLS:
        if _already_alerted(symbol):
            log.info(f"  {symbol}: already alerted today — skip")
            continue

        try:
            raw = yf.download(symbol, period="14mo", interval="1d",
                              auto_adjust=True, progress=False)
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            if raw.empty or len(raw) < 220:
                log.info(f"  {symbol}: not enough data")
                continue

            signal = detect(raw, symbol, vix_rank)

            if signal is None:
                rsi_val = raw["Close"].diff()
                log.info(f"  {symbol}: no signal")
                continue

            log.info(
                f"  ✅ SIGNAL: {symbol} | RSI={signal['rsi']} | "
                f"VIX Rank={signal['vix_rank']} | Entry={signal['entry']} | "
                f"Target={signal['target']} (+{signal['target_pct']}%)"
            )

            msg = (
                f"📉 MOMENTUM RSI PULLBACK\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Symbol    : {symbol}\n"
                f"Entry     : ${signal['entry']}\n"
                f"Target    : ${signal['target']} (+{signal['target_pct']}%)\n"
                f"Max hold  : {signal['max_hold']} days\n"
                f"RSI(14)   : {signal['rsi']} (oversold)\n"
                f"SMA200    : ${signal['sma200']}\n"
                f"VIX Rank  : {signal['vix_rank']:.0f}/100\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Price pulled back to RSI {signal['rsi']} while above SMA200\n"
                f"Backtest: 75% WR | Avg hold 5 days"
            )
            send_telegram_message(msg)

            # Auto-execute on Alpaca paper account
            place_buy(
                symbol=symbol,
                entry=signal["entry"],
                target=signal["target"],
                rsi=signal["rsi"],
                vix_rank=signal["vix_rank"] or 0,
            )

            log_signal({**signal, "timeframe": "1D", "strength": "MODERATE",
                        "pattern": "RSI Pullback in Uptrend",
                        "stop": round(signal["entry"] * 0.94, 4),
                        "rr": 1.5, "stop_pct": 6.0, "target_pct": signal["target_pct"],
                        "adx": 0, "volume_ratio": 0})
            _mark_alerted(symbol)
            alerts_sent += 1

        except Exception as e:
            log.error(f"  {symbol}: ERROR — {e}")

    log.info(f"Scan complete. {alerts_sent} alert(s) sent.")


if __name__ == "__main__":
    run_scan()
