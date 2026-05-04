import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# ── Alpaca ────────────────────────────────────────────────────────────────────
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

# ── Assets ────────────────────────────────────────────────────────────────────
MAG7 = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA"]
EXTRA_STOCKS = ["VOO", "QQQM", "TSM", "AMD", "SPY", "QQQ", "AVGO", "COIN"]
CRYPTO = ["BTC-USD", "ETH-USD", "LTC-USD"]
ALL_SYMBOLS = MAG7 + EXTRA_STOCKS + CRYPTO

# ── Timeframes ────────────────────────────────────────────────────────────────
TIMEFRAMES = [
    ("15m", None, None, None),
    ("30m", None, None, None),
    ("1h",  None, None, None),
    ("2h",  None, None, None),
    ("4h",  None, None, None),
    ("1d",  None, None, None),
    ("1w",  None, None, None),
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
