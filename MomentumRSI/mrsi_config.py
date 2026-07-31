import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── Symbols ───────────────────────────────────────────────────────────────────
SYMBOLS = [
    # Magnificent 7
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
    # Semiconductors
    "AMD",  "AVGO", "TSM",  "MRVL",  "ASML", "QCOM", "MU", "INTC", "AMAT", "TXN", "ADI",
    # Tech / Software
    "PLTR", "ORCL", "NFLX", "NOW",   "CRM",  "NET",  "CRWD", "SNOW", "SHOP",
    # Finance / Payments
    "JPM",  "GS",   "V",    "BAC",
    # Consumer / Other
    "WMT",  "LLY",  "UBER",
    # Crypto exchange
    "COIN",
    # Crypto
    "BTC-USD", "ETH-USD",
]

# ── Strategy parameters ───────────────────────────────────────────────────────
RSI_PERIOD      = 14
RSI_THRESHOLD   = 35       # Enter when RSI drops below this
SMA_TREND       = 200      # Price must be above this SMA
VIX_RANK_MAX    = 70       # VIX must be below 70th percentile of 1-year range
PROFIT_TARGET   = 0.03     # 3% profit target
MAX_HOLD_DAYS   = 10       # Exit after 10 days if target not hit

# ── Scanner ───────────────────────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS = 3600   # Run every hour
