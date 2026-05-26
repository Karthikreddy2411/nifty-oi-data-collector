"""
config.py — Central configuration for NIFTY Option Chain CSV Bot.

All tunable parameters live here so that the rest of the codebase
stays free of magic numbers / hard-coded strings.
"""

import os

# ─── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR  = os.path.join(BASE_DIR, "downloads")
LOG_DIR       = os.path.join(BASE_DIR, "logs")

# Ensure directories exist at import time
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(LOG_DIR,      exist_ok=True)

# ─── NSE URLs ─────────────────────────────────────────────────────────────────

NSE_BASE_URL         = "https://www.nseindia.com"
NSE_OPTION_CHAIN_URL = "https://www.nseindia.com/option-chain"

# Legacy API URL (kept for backward compat with scraper.py / processor.py)
NSE_API_URL = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"

# ─── Browser / Playwright ─────────────────────────────────────────────────────

# Set to True for debugging (shows browser window); False for production
BROWSER_HEADLESS = True

# Maximum seconds to wait for page / element / download events
PAGE_LOAD_TIMEOUT     = 60_000   # ms
ELEMENT_TIMEOUT       = 30_000   # ms
DOWNLOAD_TIMEOUT      = 60_000   # ms

# How many times to retry a failed download cycle
MAX_DOWNLOAD_RETRIES  = 3

# Seconds to wait between retry attempts
RETRY_SLEEP_SECONDS   = 10

# ─── Loop / Scheduler ─────────────────────────────────────────────────────────

# How often (in seconds) the main loop fires during market hours
LOOP_INTERVAL_SECONDS = 60

# ─── Market Hours (IST) ───────────────────────────────────────────────────────

MARKET_START_HOUR   = 9
MARKET_START_MINUTE = 15
MARKET_END_HOUR     = 15
MARKET_END_MINUTE   = 30

# ─── Analysis ─────────────────────────────────────────────────────────────────

# Number of strikes above / below ATM to include in ATM-window analysis
ATM_STRIKE_RANGE = 10

# PCR thresholds for signal generation
PCR_BULLISH_THRESHOLD = 1.05   # PCR above this → bullish sentiment
PCR_BEARISH_THRESHOLD = 0.95   # PCR below this → bearish sentiment

# OI change thresholds (in lots) for "large shift" alerts
OI_LARGE_SHIFT_LOTS = 50_000

# ─── Telegram (Optional) ──────────────────────────────────────────────────────

# Set both values in environment variables to enable Telegram alerts.
# Leave as None / empty to disable silently.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "")
TELEGRAM_ENABLED   = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

# ─── Database (Legacy — kept for backward compat) ─────────────────────────────

DB_PATH      = os.path.join(BASE_DIR, "nifty_data.db")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ─── Browser headers (used by legacy scraper.py) ──────────────────────────────

HEADERS = {
    "User-Agent":      (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
}
