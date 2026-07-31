"""
Dynamic Stock Selector — runs every 10 days
Scans S&P 500 universe, finds top stocks by Momentum RSI win rate
Updates mrsi_config.py automatically
"""
from __future__ import annotations
import yfinance as yf
import pandas as pd
import re
import os
import logging
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

# Fixed stocks always kept regardless of WR
ALWAYS_KEEP = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",  # Mag 7
    "BTC-USD", "ETH-USD",                                        # Crypto
]

# Full candidate universe — high-volume US stocks
UNIVERSE = [
    "AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA",
    "AMD","AVGO","TSM","MRVL","ASML","QCOM","MU","INTC","AMAT","TXN","ADI","ARM","MCHP",
    "PLTR","ORCL","NFLX","NOW","CRM","NET","CRWD","SNOW","SHOP","DDOG","ZS","PANW",
    "JPM","GS","V","MA","BAC","WFC","MS","C","AXP",
    "WMT","COST","HD","MCD","SBUX","TGT",
    "LLY","JNJ","UNH","ABBV","MRK","PFE","TMO",
    "XOM","CVX","COP","CEG",
    "UBER","COIN","HOOD","SOFI","PYPL",
    "BA","RTX","LMT","CAT","HON","DE",
    "BRK-B","T","VZ","TMUS",
    "BTC-USD","ETH-USD",
]

TOP_N      = 30   # keep top 30 non-Mag7 stocks
MIN_WR     = 0.60 # minimum 60% win rate
MIN_TRADES = 3    # minimum 3 trades in backtest


def backtest_symbol(symbol: str, period_days: int = 730) -> tuple[int, float]:
    """Returns (num_trades, win_rate) for Momentum RSI strategy."""
    end   = datetime.today()
    start = end - timedelta(days=period_days)
    try:
        raw = yf.download(symbol, start=start, end=end, interval="1d",
                          auto_adjust=True, progress=False)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        if raw.empty or len(raw) < 200:
            return 0, 0.0

        raw["sma200"] = raw["Close"].rolling(200).mean()
        delta = raw["Close"].diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        raw["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, 1e-9))
        df = raw.dropna(subset=["sma200", "rsi"])

        trades = []; in_trade = False
        for i in range(len(df)):
            row = df.iloc[i]
            if in_trade:
                if row["High"] >= entry * 1.03 or (df.index[i] - entry_date).days >= 10:
                    trades.append(bool(row["High"] >= entry * 1.03))
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


def select_best_symbols() -> list[str]:
    """Scan universe and return best symbols by win rate."""
    log.info("=" * 55)
    log.info(f"Stock Selector — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    log.info(f"Scanning {len(UNIVERSE)} candidates...")
    log.info("=" * 55)

    scores = []
    for symbol in UNIVERSE:
        if symbol in ("BTC-USD", "ETH-USD"):
            scores.append((symbol, 8, 0.86))  # known WR
            continue
        n, wr = backtest_symbol(symbol)
        flag  = "✅" if wr >= MIN_WR and n >= MIN_TRADES else "➖"
        log.info(f"  {flag} {symbol:<10} {n:>3}T  WR={wr*100:.0f}%")
        if n >= MIN_TRADES:
            scores.append((symbol, n, wr))

    # Sort by WR descending
    scores.sort(key=lambda x: -x[2])

    # Always keep Mag 7 + crypto, then add top performers
    selected = list(ALWAYS_KEEP)
    for symbol, n, wr in scores:
        if symbol in selected:
            continue
        if wr >= MIN_WR and len(selected) < len(ALWAYS_KEEP) + TOP_N:
            selected.append(symbol)

    log.info(f"\nSelected {len(selected)} symbols")
    for s in selected:
        log.info(f"  {s}")
    return selected


def update_config(symbols: list[str]) -> None:
    """Rewrite SYMBOLS list in mrsi_config.py."""
    config_path = os.path.join(os.path.dirname(__file__), "mrsi_config.py")
    with open(config_path, "r") as f:
        content = f.read()

    # Build new SYMBOLS block
    lines = ["SYMBOLS = [\n"]
    chunk = []
    for s in symbols:
        chunk.append(f'    "{s}"')
        if len(chunk) == 6:
            lines.append(", ".join(chunk) + ",\n")
            chunk = []
    if chunk:
        lines.append(", ".join(chunk) + ",\n")
    lines.append("]\n")
    new_block = "".join(lines)

    # Replace existing SYMBOLS block
    new_content = re.sub(
        r"SYMBOLS\s*=\s*\[.*?\]",
        new_block.rstrip("\n"),
        content,
        flags=re.DOTALL,
    )
    with open(config_path, "w") as f:
        f.write(new_content)
    log.info(f"mrsi_config.py updated with {len(symbols)} symbols")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()],
    )
    best = select_best_symbols()
    update_config(best)
    print(f"\nDone. {len(best)} symbols selected and saved.")
