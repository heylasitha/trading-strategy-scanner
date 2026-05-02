import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# ── Assets ────────────────────────────────────────────────────────────────────
MAG7 = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA"]
EXTRA_STOCKS = ["VOO", "QQQM", "TSM", "AMD"]
CRYPTO = ["BTC-USD", "ETH-USD", "LTC-USD"]
ALL_SYMBOLS = MAG7 + EXTRA_STOCKS + CRYPTO

# ── Timeframes ────────────────────────────────────────────────────────────────
# Each entry: (label, yfinance_interval, yfinance_period, resample_rule_or_None)
TIMEFRAMES = [
    ("15m", "15m",  "60d",   None),
    ("30m", "30m",  "60d",   None),
    ("1h",  "1h",   "730d",  None),
    ("2h",  "1h",   "730d",  "2h"),
    ("4h",  "1h",   "730d",  "4h"),
    ("1d",  "1d",   "5y",    None),
    ("1w",  "1wk",  "max",   None),
]

# ── Indicator Settings ────────────────────────────────────────────────────────
SMA_FAST   = 20
SMA_MID    = 50
SMA_SLOW   = 200

STOCH_K      = 14
STOCH_D      = 3
STOCH_SMOOTH = 3

ADX_PERIOD        = 14
ADX_THRESHOLD     = 25       # ADX must be above this

VOLUME_LOOKBACK   = 20
VOLUME_MULTIPLIER = 1.5      # Current volume must be 1.5x the average

# ── Signal Filter ─────────────────────────────────────────────────────────────
MIN_RR           = 2.0       # Minimum Risk:Reward ratio
MIN_BARS         = 215       # Minimum bars needed for SMA200 + buffer
EARNINGS_BUFFER_DAYS = 5     # Skip Mag7 trades within 5 days of earnings

# ── Market Hours (Eastern Time, stocks only) ──────────────────────────────────
MARKET_OPEN_HOUR    = 9
MARKET_OPEN_MINUTE  = 30
MARKET_CLOSE_HOUR   = 16
MARKET_CLOSE_MINUTE = 0

# ── Scanner ───────────────────────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS = 300   # Scan every 5 minutes
