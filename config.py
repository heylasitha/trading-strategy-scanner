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
EXTRA_STOCKS = ["VOO", "QQQM", "TSM", "AMD", "SPY", "AVGO", "COIN"]
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
VOLUME_MULTIPLIER      = 1.2  # For crosses (S2/S6) and ORB (S8/S9)
VWAP_VOLUME_MULTIPLIER = 1.5  # For VWAP strategies (S3/S4/S7) — reversal needs clear spike

# ── Signal Filter ─────────────────────────────────────────────────────────────
MIN_RR = 1.9   # Minimum Risk:Reward ratio (1.9 allows MEME-type setups, blocks garbage)

# SMA Compression — max allowed spread per timeframe
# Fast timeframes need tighter compression to be meaningful
SMA_COMPRESSION_THRESHOLDS = {
    "15m": 0.030,   # 3%
    "30m": 0.060,   # 6%
    "1h":  0.060,   # 6%
    "2h":  0.060,   # 6%
    "4h":  0.100,   # 10%
    "1d":  0.100,   # 10%
    "1w":  0.100,   # 10%
}

# Minimum candles the base must have formed before breakout fires
SMA_MIN_BASE_CANDLES = {
    "15m": 20,
    "30m": 16,
    "1h":  12,
    "2h":  10,
    "4h":   8,
    "1d":   6,
    "1w":   4,
}

MIN_BARS             = 215   # Minimum bars needed for SMA200 + buffer
EARNINGS_BUFFER_DAYS = 5     # Skip Mag7 trades within 5 days of earnings

# Cooldown — block same asset same direction for this many hours after a signal
SIGNAL_COOLDOWN_HOURS = 24

# ── Market Hours (Eastern Time, stocks only) ──────────────────────────────────
MARKET_OPEN_HOUR    = 9
MARKET_OPEN_MINUTE  = 30
MARKET_CLOSE_HOUR   = 16
MARKET_CLOSE_MINUTE = 0

# ── Scanner ───────────────────────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS = 300   # Scan every 5 minutes
