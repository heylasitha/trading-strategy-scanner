"""Place stock orders via Alpaca paper trading."""
from __future__ import annotations

import logging
import requests
from config import ALPACA_API_KEY, ALPACA_SECRET_KEY

log = logging.getLogger(__name__)

ALPACA_TRADE_URL = "https://paper-api.alpaca.markets/v2"


def _headers() -> dict:
    return {
        "APCA-API-KEY-ID":     ALPACA_API_KEY or "",
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY or "",
        "Content-Type":        "application/json",
    }


def place_bracket_order(
    symbol: str,
    qty: int,
    entry: float,
    stop: float,
    target: float,
) -> dict | None:
    """
    Place a bracket order (entry + stop loss + take profit) on Alpaca paper account.
    Returns order dict on success, None on failure.
    """
    payload = {
        "symbol":        symbol,
        "qty":           str(qty),
        "side":          "buy",
        "type":          "limit",
        "limit_price":   str(round(entry, 2)),
        "time_in_force": "day",
        "order_class":   "bracket",
        "stop_loss":     {"stop_price": str(round(stop, 2))},
        "take_profit":   {"limit_price": str(round(target, 2))},
    }
    try:
        resp = requests.post(
            f"{ALPACA_TRADE_URL}/orders",
            headers=_headers(),
            json=payload,
            timeout=10,
        )
        if resp.status_code in (200, 201):
            order = resp.json()
            log.info(f"✅ Bracket order placed: {symbol} x{qty} entry=${entry:.2f} stop=${stop:.2f} target=${target:.2f} id={order['id']}")
            return order
        else:
            log.error(f"❌ Order failed {resp.status_code}: {resp.text[:300]}")
            return None
    except Exception as e:
        log.error(f"❌ place_bracket_order exception: {e}")
        return None


def calculate_qty(entry: float, risk_dollars: float = 500) -> int:
    """
    Calculate number of shares to buy based on fixed $ risk per trade.
    risk_dollars = $500 per trade.
    """
    if entry <= 0:
        return 0
    qty = int(risk_dollars / entry)
    return max(qty, 1)
