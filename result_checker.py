"""
Auto Result Checker
Reads all 'Pending' trades from Google Sheets,
fetches price history, checks if Stop or Target was hit,
and updates Result column automatically.

Run manually or add to cron to run daily.
"""
from __future__ import annotations

import json
import logging
import os
import time
import warnings
warnings.filterwarnings("ignore")

from datetime import datetime, timezone, timedelta

import pandas as pd
import pytz
import yfinance as yf
import requests
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

SGT            = pytz.timezone("Asia/Singapore")
SPREADSHEET_ID = "1eSHc5hElBBmdLofE-SBgooG91_19ryiQVtrCZtSOvso"
SHEET_NAME     = "Signals"
SCOPES         = ["https://www.googleapis.com/auth/spreadsheets"]

# Column indices (1-based)
COL_DATETIME = 1
COL_SYMBOL   = 2
COL_TF       = 3
COL_STRATEGY = 4
COL_STRENGTH = 5
COL_PATTERN  = 6
COL_ENTRY    = 7
COL_STOP     = 8
COL_TARGET   = 9
COL_RR       = 10
COL_STOP_PCT = 11
COL_TGT_PCT  = 12
COL_RESULT   = 13
COL_NOTES    = 14

BINANCE_SYMBOL_MAP = {
    "BTC-USD": "BTCUSDT",
    "ETH-USD": "ETHUSDT",
    "LTC-USD": "LTCUSDT",
}

CRYPTO_SYMBOLS = list(BINANCE_SYMBOL_MAP.keys())


def _get_sheet() -> gspread.Worksheet | None:
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        log.error("GOOGLE_CREDENTIALS_JSON not set")
        return None
    try:
        info  = json.loads(creds_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        client = gspread.authorize(creds)
        wb = client.open_by_key(SPREADSHEET_ID)
        return wb.worksheet(SHEET_NAME)
    except Exception as e:
        log.error(f"Sheets auth failed: {e}")
        return None


def _fetch_price_history(symbol: str, since: datetime) -> pd.DataFrame | None:
    """Fetch 1h OHLCV from signal date to now."""
    try:
        if symbol in CRYPTO_SYMBOLS:
            binance_sym = BINANCE_SYMBOL_MAP[symbol]
            start_ms = int(since.timestamp() * 1000)
            rows = []
            end_time = None
            for _ in range(10):
                params = {"symbol": binance_sym, "interval": "1h", "limit": 1000}
                if end_time:
                    params["endTime"] = end_time
                else:
                    params["startTime"] = start_ms
                resp = requests.get("https://api.binance.us/api/v3/klines", params=params, timeout=15)
                if resp.status_code != 200:
                    break
                raw = resp.json()
                if not raw:
                    break
                rows.extend(raw)
                if len(raw) < 1000:
                    break
                end_time = raw[-1][0] + 1
                time.sleep(0.1)
            if not rows:
                return None
            df = pd.DataFrame(rows, columns=[
                "open_time","open","high","low","close","volume",
                "close_time","quote_vol","trades","taker_base","taker_quote","ignore"
            ])
            df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            df = df.set_index("open_time")[["open","high","low","close","volume"]].astype(float)
        else:
            days_since = max(1, (datetime.now(timezone.utc) - since).days + 2)
            period = f"{min(days_since, 730)}d"
            df = yf.Ticker(symbol).history(period=period, interval="1h", auto_adjust=True)
            if df is None or df.empty:
                return None
            df.columns = [c.lower() for c in df.columns]
            df = df[["open","high","low","close","volume"]].astype(float)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            else:
                df.index = df.index.tz_convert("UTC")

        df = df[~df.index.duplicated(keep="last")]
        df.sort_index(inplace=True)
        # Only keep bars after signal
        df = df[df.index >= since]
        return df

    except Exception as e:
        log.error(f"Price fetch failed {symbol}: {e}")
        return None


def _check_outcome(df: pd.DataFrame, entry: float, stop: float, target: float, direction: str) -> tuple[str, str]:
    """
    Returns (result, notes).
    direction: 'long' or 'short'
    """
    if df is None or len(df) == 0:
        # Use latest price to check open trade
        return "Pending", "No price data"

    if direction == "long":
        hit_target = df["high"] >= target
        hit_stop   = df["low"]  <= stop
    else:  # short
        hit_target = df["low"]  <= target
        hit_stop   = df["high"] >= stop

    has_target = hit_target.any()
    has_stop   = hit_stop.any()

    if has_target and has_stop:
        target_bar = df[hit_target].index[0]
        stop_bar   = df[hit_stop].index[0]
        if target_bar <= stop_bar:
            return "WIN ✅", f"Target hit {target_bar.strftime('%Y-%m-%d %H:%M')}"
        else:
            return "LOSS ❌", f"Stop hit {stop_bar.strftime('%Y-%m-%d %H:%M')}"
    elif has_target:
        target_bar = df[hit_target].index[0]
        return "WIN ✅", f"Target hit {target_bar.strftime('%Y-%m-%d %H:%M')}"
    elif has_stop:
        stop_bar = df[hit_stop].index[0]
        return "LOSS ❌", f"Stop hit {stop_bar.strftime('%Y-%m-%d %H:%M')}"
    else:
        current = float(df["close"].iloc[-1])
        if direction == "long":
            pnl_pct = round((current - entry) / entry * 100, 2)
        else:
            pnl_pct = round((entry - current) / entry * 100, 2)
        return "Open ⏳", f"Current: {current:.2f} ({pnl_pct:+.2f}%)"


def run_checker() -> None:
    log.info("=== Result Checker Starting ===")
    ws = _get_sheet()
    if ws is None:
        return

    all_rows = ws.get_all_values()
    if len(all_rows) < 2:
        log.info("No trades found in sheet")
        return

    headers  = all_rows[0]
    data_rows = all_rows[1:]

    updated = 0
    for i, row in enumerate(data_rows):
        sheet_row = i + 2  # 1-based, skip header

        # Pad row if short
        while len(row) < COL_NOTES:
            row.append("")

        result = row[COL_RESULT - 1].strip()

        # Skip already resolved
        if result in ("WIN ✅", "LOSS ❌"):
            continue

        symbol = row[COL_SYMBOL - 1].strip()
        tf     = row[COL_TF     - 1].strip()
        dt_str = row[COL_DATETIME - 1].strip()

        try:
            entry  = float(str(row[COL_ENTRY  - 1]).replace(",",""))
            stop   = float(str(row[COL_STOP   - 1]).replace(",",""))
            target = float(str(row[COL_TARGET - 1]).replace(",",""))
        except:
            log.warning(f"Row {sheet_row}: invalid entry/stop/target — skipping")
            continue

        # Parse signal datetime
        try:
            since = datetime.strptime(dt_str, "%Y-%m-%d %H:%M SGT")
            since = SGT.localize(since).astimezone(timezone.utc)
        except:
            log.warning(f"Row {sheet_row}: cannot parse date '{dt_str}' — skipping")
            continue

        # Determine direction from stop vs entry
        direction = "long" if stop < entry else "short"

        log.info(f"Checking {symbol} {tf} {direction} from {dt_str}...")

        df = _fetch_price_history(symbol, since)
        new_result, notes = _check_outcome(df, entry, stop, target, direction)

        # Update sheet
        ws.update_cell(sheet_row, COL_RESULT, new_result)
        ws.update_cell(sheet_row, COL_NOTES,  notes)
        log.info(f"  → {new_result}  {notes}")
        updated += 1
        time.sleep(0.5)  # avoid rate limit

    log.info(f"=== Done. Updated {updated} trades ===")


if __name__ == "__main__":
    run_checker()
