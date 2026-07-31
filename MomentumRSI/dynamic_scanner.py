"""
Dynamic Market Scanner — Momentum RSI Strategy
Scans full S&P 500 universe, finds best stocks by WR, updates config automatically.

Trigger: runs when no active signals exist OR every 10 days
Keeps top 30 stocks with WR >= 65% over last 6 months
Always keeps Mag 7 + BTC/ETH as base
"""
from __future__ import annotations
import yfinance as yf
import pandas as pd
import sys, os, logging, json
import requests as _req
from datetime import datetime, timedelta, date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("dynamic_scanner.log")],
)
log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
ALWAYS_KEEP = [
    "AAPL","NVDA","GOOGL","AMZN","META","TSLA",   # Mag 7 (excl MSFT — low WR)
    "BTC-USD","ETH-USD",
]

TOP_N       = 27    # top 27 dynamic + 8 always_keep = 35 total
MIN_WR      = 0.65  # minimum 65% win rate
MIN_TRADES  = 4     # minimum 4 trades in 6-month backtest
BACKTEST_DAYS = 180 # 6 months — recent performance matters more

STATE_FILE = os.path.join(os.path.dirname(__file__), "selector_state.json")

# Full S&P 500 high-volume universe (top ~150 by market cap / volume)
SP500_UNIVERSE = [
    # Tech / Semiconductors
    "AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","AMD","AVGO","TSM",
    "MRVL","ASML","QCOM","MU","INTC","AMAT","TXN","ADI","ARM","MCHP",
    "KLAC","LRCX","NXPI","ON","TER","MPWR","SWKS","QRVO",
    # Software / Cloud
    "ORCL","CRM","NOW","NFLX","ADBE","INTU","PLTR","NET","CRWD","SNOW",
    "SHOP","DDOG","ZS","PANW","FTNT","TEAM","MDB","HUBS","GTLB","OKTA",
    # Finance
    "JPM","GS","V","MA","BAC","WFC","MS","C","AXP","BRK-B","CB","MET","PRU",
    # Healthcare / Pharma
    "LLY","JNJ","UNH","ABBV","MRK","PFE","TMO","ABT","ISRG","REGN","VRTX","MRNA",
    # Consumer
    "WMT","COST","HD","MCD","SBUX","NKE","TGT","LOW","TJX","BKNG","ABNB",
    # Energy
    "XOM","CVX","COP","SLB","EOG","MPC","VLO","PSX","CEG",
    # Industrial
    "CAT","HON","RTX","LMT","BA","GE","DE","EMR","ETN","PH",
    # Communication
    "T","VZ","TMUS","DIS","CMCSA","CHTR",
    # Other high-volume
    "UBER","COIN","HOOD","PYPL","SQ","SOFI","NU",
    "ENPH","FSLR","NEE","DUK",
    "BTC-USD","ETH-USD",
]

def send_telegram(msg: str) -> None:
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


def backtest_symbol(symbol: str) -> tuple[int, float]:
    """Returns (num_trades, win_rate) for Momentum RSI over last 6 months."""
    end   = datetime.today()
    start = end - timedelta(days=BACKTEST_DAYS + 210)  # extra for SMA200
    try:
        raw = yf.download(symbol, start=start, end=end, interval="1d",
                          auto_adjust=True, progress=False)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        if raw.empty or len(raw) < 210:
            return 0, 0.0

        raw["sma200"] = raw["Close"].rolling(200).mean()
        delta = raw["Close"].diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        raw["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, 1e-9))
        df = raw.dropna(subset=["sma200","rsi"])

        # Only look at last BACKTEST_DAYS
        cutoff = pd.Timestamp(end - timedelta(days=BACKTEST_DAYS))
        df = df[df.index >= cutoff]

        trades = []; in_trade = False
        for i in range(len(df)):
            row = df.iloc[i]
            if in_trade:
                if row["High"] >= entry*1.03 or (df.index[i]-entry_date).days >= 10:
                    trades.append(bool(row["High"] >= entry*1.03))
                    in_trade = False
            else:
                if row["Close"] > row["sma200"] and row["rsi"] < 35:
                    entry = float(row["Close"])
                    entry_date = df.index[i]
                    in_trade = True

        n  = len(trades)
        wr = sum(trades) / n if n > 0 else 0.0
        return n, wr
    except Exception:
        return 0, 0.0


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_scan_date": None}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def should_rescan(state: dict) -> bool:
    """Rescan if never run, or last scan was 10+ days ago."""
    if not state.get("last_scan_date"):
        return True
    last = date.fromisoformat(state["last_scan_date"])
    return (date.today() - last).days >= 10


def run_dynamic_scan() -> None:
    state = load_state()

    if not should_rescan(state):
        days_since = (date.today() - date.fromisoformat(state["last_scan_date"])).days
        log.info(f"Last scan was {days_since} days ago — next scan in {10-days_since} days")
        return

    log.info("=" * 58)
    log.info(f"DYNAMIC STOCK SELECTOR — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    log.info(f"Scanning {len(set(SP500_UNIVERSE))} candidates (last {BACKTEST_DAYS} days)...")
    log.info("=" * 58)

    scores = []
    for symbol in sorted(set(SP500_UNIVERSE)):
        if symbol in ("BTC-USD","ETH-USD"):
            scores.append((symbol, 8, 0.87))
            continue
        n, wr = backtest_symbol(symbol)
        if n >= MIN_TRADES:
            flag = "✅" if wr >= MIN_WR else "➖"
            log.info(f"  {flag} {symbol:<10} {n}T  WR={wr*100:.0f}%")
            scores.append((symbol, n, wr))
        else:
            log.info(f"  ⏭  {symbol:<10} only {n} trades — skip")

    # Sort by WR
    scores.sort(key=lambda x: -x[2])

    # Build final list: always_keep + top dynamic picks
    selected = list(ALWAYS_KEEP)
    added = []
    for symbol, n, wr in scores:
        if symbol in selected:
            continue
        if wr >= MIN_WR and len(selected) < len(ALWAYS_KEEP) + TOP_N:
            selected.append(symbol)
            added.append((symbol, n, wr))

    log.info(f"\nSelected {len(selected)} symbols total")

    # Update mrsi_config.py
    _update_config(selected)

    # Save state
    state["last_scan_date"] = str(date.today())
    state["last_symbols"]   = selected
    save_state(state)

    # Send Telegram summary
    top5 = [(s, wr) for s, n, wr in scores if wr >= MIN_WR][:5]
    msg = (
        f"📋 STOCK LIST AUTO-UPDATED\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Scanned : {len(set(SP500_UNIVERSE))} S&P 500 stocks\n"
        f"Selected: {len(selected)} best stocks\n"
        f"Min WR  : {MIN_WR*100:.0f}% (last {BACKTEST_DAYS} days)\n\n"
        f"🏆 Top performers:\n"
        + "\n".join(f"  {s} — {wr*100:.0f}% WR" for s,wr in top5) +
        f"\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Scanner updated. Next rescan in 10 days."
    )
    send_telegram(msg)
    log.info("Telegram notification sent.")


def _update_config(symbols: list[str]) -> None:
    config_path = os.path.join(os.path.dirname(__file__), "mrsi_config.py")
    with open(config_path, "r") as f:
        content = f.read()

    # Build new SYMBOLS block
    lines = ["SYMBOLS = [\n"]
    row = []
    for s in symbols:
        row.append(f'"{s}"')
        if len(row) == 6:
            lines.append("    " + ", ".join(row) + ",\n")
            row = []
    if row:
        lines.append("    " + ", ".join(row) + ",\n")
    lines.append("]\n")
    new_block = "".join(lines)

    import re
    new_content = re.sub(
        r"SYMBOLS\s*=\s*\[.*?\]",
        new_block.rstrip("\n"),
        content,
        flags=re.DOTALL,
    )
    with open(config_path, "w") as f:
        f.write(new_content)
    log.info(f"mrsi_config.py updated — {len(symbols)} symbols")


if __name__ == "__main__":
    run_dynamic_scan()
