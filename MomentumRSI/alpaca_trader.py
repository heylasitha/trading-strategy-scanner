"""
Alpaca Paper Trader — Momentum RSI Strategy
- Places paper trades automatically when signal fires
- Monitors open positions daily
- Closes at +3% target or after 10 days
- Sends Telegram updates on every action
"""
from __future__ import annotations
import sys, os, logging, requests as _req
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger(__name__)

# Alpaca paper trading base URL
BASE_URL   = "https://paper-api.alpaca.markets"
HEADERS    = {
    "APCA-API-KEY-ID":     ALPACA_API_KEY    or "",
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY or "",
    "Content-Type":        "application/json",
}

PROFIT_TARGET = 0.03   # 3%
MAX_HOLD_DAYS = 10
TRADE_VALUE   = 1000   # USD per trade (paper money)


# ── Telegram ──────────────────────────────────────────────────────────────────

def _telegram(msg: str) -> None:
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


# ── Alpaca helpers ────────────────────────────────────────────────────────────

def _get(path: str) -> dict | list:
    r = _req.get(f"{BASE_URL}{path}", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict) -> dict:
    r = _req.post(f"{BASE_URL}{path}", headers=HEADERS, json=body, timeout=15)
    r.raise_for_status()
    return r.json()


def _delete(path: str) -> None:
    r = _req.delete(f"{BASE_URL}{path}", headers=HEADERS, timeout=15)
    r.raise_for_status()


def get_account() -> dict:
    return _get("/v2/account")


def get_positions() -> list:
    return _get("/v2/positions")


def get_open_orders() -> list:
    return _get("/v2/orders?status=open")


def is_market_open() -> bool:
    clock = _get("/v2/clock")
    return bool(clock.get("is_open", False))


def place_buy(symbol: str, entry: float, target: float, rsi: float,
              vix_rank: float) -> bool:
    """Place a paper market buy order. Returns True if successful."""
    # Skip crypto — Alpaca paper handles stocks only cleanly
    if "-" in symbol:
        log.info(f"  {symbol}: skipping crypto for Alpaca (use exchange directly)")
        return False

    # Check if already holding this symbol
    positions = get_positions()
    holding = [p for p in positions if p["symbol"] == symbol]
    if holding:
        log.info(f"  {symbol}: already holding — skip")
        return False

    # Calculate qty based on trade value
    qty = max(1, int(TRADE_VALUE / entry))

    try:
        order = _post("/v2/orders", {
            "symbol":        symbol,
            "qty":           str(qty),
            "side":          "buy",
            "type":          "market",
            "time_in_force": "day",
        })
        log.info(f"  ✅ BUY placed: {symbol} x{qty} @ ~${entry}")

        msg = (
            f"🟢 PAPER TRADE OPENED\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Symbol  : {symbol}\n"
            f"Action  : BUY {qty} shares\n"
            f"Entry   : ~${entry}\n"
            f"Target  : ${target} (+3%)\n"
            f"Max hold: {MAX_HOLD_DAYS} days\n"
            f"RSI     : {rsi} (oversold)\n"
            f"VIX Rank: {vix_rank:.0f}/100\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Paper trade — Alpaca sandbox\n"
            f"Backtest: 70% WR"
        )
        _telegram(msg)
        return True

    except Exception as e:
        log.error(f"  {symbol}: order failed — {e}")
        _telegram(f"⚠️ Order failed: {symbol}\nError: {e}")
        return False


def _get_entry_dates() -> dict[str, int]:
    """Return {symbol: days_held} by checking filled order history."""
    from datetime import datetime, timezone
    try:
        r = _req.get(
            f"{BASE_URL}/v2/orders",
            headers=HEADERS,
            params={"status": "filled", "limit": 200, "direction": "desc"},
            timeout=15,
        )
        orders = r.json() if r.status_code == 200 else []
        # Find earliest buy fill date per symbol
        earliest: dict[str, datetime] = {}
        for o in orders:
            if o.get("side") != "buy":
                continue
            sym = o.get("symbol", "")
            filled = o.get("filled_at") or o.get("submitted_at") or ""
            if not filled:
                continue
            dt = datetime.fromisoformat(filled.replace("Z", "+00:00"))
            if sym not in earliest or dt < earliest[sym]:
                earliest[sym] = dt
        now = datetime.now(timezone.utc)
        return {sym: (now - dt).days for sym, dt in earliest.items()}
    except Exception:
        return {}


def monitor_and_close() -> None:
    """Check all open positions — close if +3% target hit or 10 days passed."""
    from datetime import datetime, timezone

    positions = get_positions()
    if not positions:
        log.info("No open positions to monitor.")
        return

    log.info(f"Monitoring {len(positions)} open position(s)...")
    entry_dates = _get_entry_dates()

    for pos in positions:
        symbol     = pos["symbol"]
        qty        = int(float(pos["qty"]))
        avg_entry  = float(pos["avg_entry_price"])
        current    = float(pos["current_price"])
        unrealized = float(pos["unrealized_plpc"]) * 100  # %
        days_held  = entry_dates.get(symbol, 0)

        target  = avg_entry * (1 + PROFIT_TARGET)
        hit_tgt = current >= target
        expired = days_held >= MAX_HOLD_DAYS

        log.info(
            f"  {symbol}: entry=${avg_entry:.2f} current=${current:.2f} "
            f"P&L={unrealized:+.1f}% days={days_held} "
            f"{'→ HIT TARGET' if hit_tgt else '→ EXPIRED' if expired else '→ HOLD'}"
        )

        if hit_tgt or expired:
            reason = "TARGET HIT +3%" if hit_tgt else f"MAX HOLD {MAX_HOLD_DAYS} DAYS"
            emoji  = "💰" if hit_tgt else "⏱️"
            result = "WIN" if hit_tgt else ("WIN" if unrealized > 0 else "LOSS")

            try:
                _post("/v2/orders", {
                    "symbol":        symbol,
                    "qty":           str(qty),
                    "side":          "sell",
                    "type":          "market",
                    "time_in_force": "day",
                })
                log.info(f"  ✅ SELL placed: {symbol} — {reason}")

                msg = (
                    f"{emoji} PAPER TRADE CLOSED — {result}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Symbol  : {symbol}\n"
                    f"Entry   : ${avg_entry:.2f}\n"
                    f"Exit    : ${current:.2f}\n"
                    f"P&L     : {unrealized:+.1f}%\n"
                    f"Reason  : {reason}\n"
                    f"Days    : {days_held}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 Paper trade — Alpaca sandbox"
                )
                _telegram(msg)

            except Exception as e:
                log.error(f"  {symbol}: close failed — {e}")


def print_status() -> None:
    """Print current account and positions status."""
    try:
        acct = get_account()
        print(f"\n{'='*50}")
        print(f"ALPACA PAPER ACCOUNT STATUS")
        print(f"{'='*50}")
        print(f"  Portfolio Value : ${float(acct['portfolio_value']):,.2f}")
        print(f"  Cash            : ${float(acct['cash']):,.2f}")
        print(f"  Buying Power    : ${float(acct['buying_power']):,.2f}")

        positions = get_positions()
        if positions:
            print(f"\n  Open Positions ({len(positions)}):")
            for p in positions:
                pnl = float(p["unrealized_plpc"])*100
                fl  = "✅" if pnl > 0 else "❌"
                print(f"    {fl} {p['symbol']:<8} "
                      f"entry=${float(p['avg_entry_price']):.2f} "
                      f"now=${float(p['current_price']):.2f} "
                      f"P&L={pnl:+.1f}%")
        else:
            print(f"\n  No open positions.")
        print()
    except Exception as e:
        print(f"Error fetching account: {e}")


if __name__ == "__main__":
    print_status()
    monitor_and_close()
