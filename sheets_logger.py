from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import gspread
import pytz
from google.oauth2.service_account import Credentials

log = logging.getLogger(__name__)

SGT = pytz.timezone("Asia/Singapore")

SPREADSHEET_ID = "1eSHc5hElBBmdLofE-SBgooG91_19ryiQVtrCZtSOvso"
SHEET_NAME     = "Signals"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

HEADERS = [
    "Date/Time (SGT)", "Symbol", "Timeframe", "Strategy",
    "Strength", "Pattern", "Entry", "Stop", "Target",
    "R:R", "Stop %", "Target %", "Result", "Notes",
]


def _get_client() -> gspread.Client | None:
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        log.warning("GOOGLE_CREDENTIALS_JSON not set — skipping Sheets logging")
        return None
    try:
        info  = json.loads(creds_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        return gspread.authorize(creds)
    except Exception as e:
        log.error(f"Google Sheets auth failed: {e}")
        return None


def _get_sheet(client: gspread.Client) -> gspread.Worksheet | None:
    try:
        wb    = client.open_by_key(SPREADSHEET_ID)
        try:
            ws = wb.worksheet(SHEET_NAME)
        except gspread.WorksheetNotFound:
            ws = wb.add_worksheet(title=SHEET_NAME, rows=1000, cols=len(HEADERS))
            ws.append_row(HEADERS)
        # Add headers if sheet is empty
        if ws.row_count == 0 or ws.cell(1, 1).value != HEADERS[0]:
            ws.insert_row(HEADERS, 1)
        return ws
    except Exception as e:
        log.error(f"Could not open Google Sheet: {e}")
        return None


def log_signal(signal: dict) -> None:
    """Append one signal row to the Google Sheet."""
    client = _get_client()
    if client is None:
        return
    ws = _get_sheet(client)
    if ws is None:
        return

    now_sgt  = datetime.now(timezone.utc).astimezone(SGT)
    strategy = signal.get("strategy", "SMA MOMENTUM")

    row = [
        now_sgt.strftime("%Y-%m-%d %H:%M SGT"),
        signal.get("symbol",     ""),
        signal.get("timeframe",  "").upper(),
        strategy,
        signal.get("strength",   ""),
        signal.get("pattern",    ""),
        signal.get("entry",      ""),
        signal.get("stop",       ""),
        signal.get("target",     ""),
        signal.get("rr",         ""),
        signal.get("stop_pct",   ""),
        signal.get("target_pct", ""),
        "Pending",   # Result — user fills in later
        "",          # Notes
    ]

    try:
        ws.append_row(row, value_input_option="USER_ENTERED")
        log.info(f"Logged to Sheets: {signal['symbol']} {signal['timeframe']} [{strategy}]")
    except Exception as e:
        log.error(f"Failed to log to Sheets: {e}")
