# NIFTY Option Chain CSV Bot — Implementation Plan

## Architecture

New modular system using Playwright for browser automation + CSV download.

### New Files
- `bot.py` — Main orchestrator / loop controller
- `downloader.py` — Playwright browser automation + CSV download
- `analyzer.py` — OI analysis engine (complete rewrite)
- `signals.py` — Signal generation logic
- `config.py` — Extended config (update existing)
- `requirements.txt` — Updated with playwright

### Existing files kept
- `db.py` — Kept as-is (DB layer)
- `processor.py` — Kept as-is (JSON processing path, not used in new flow)
- `scraper.py` — Kept as-is (legacy, not used in new flow)
- `main.py` — Kept as-is (legacy entry, new entry is bot.py)

### New Folders
- `downloads/` — CSVs saved here
- `logs/` — Rotating log files

## Module Responsibilities

### config.py
- NSE URLs
- Download folder, log folder paths
- Market hours
- Loop interval (60 seconds)
- Telegram config (optional, off by default)
- ATM range (±10 strikes)
- PCR thresholds

### downloader.py
- Launch Playwright Chromium
- Navigate to NSE option chain page
- Accept cookies/dismiss popups
- Select NIFTY as underlying
- Select nearest expiry
- Click "Download (.csv)"
- Wait for download via Playwright download event
- Move file to downloads/ with timestamp
- Delete old CSVs before download
- Retry logic (3 attempts)
- Returns path to downloaded file

### analyzer.py
- Load CSV with pandas
- Normalize column names
- Extract: StrikePrice, CE_OI, PE_OI, CE_OI_Change, PE_OI_Change, Volume, IV, LTP
- Calculate PCR
- Find Max Call OI strike (Resistance)
- Find Max Put OI strike (Support)
- ATM detection (closest strike to spot price)
- ATM ± 10 strike window analysis
- OI change categorization (call writing, put writing, short covering, long unwinding)
- Returns analysis dict

### signals.py
- Takes analysis dict
- Applies bullish/bearish/neutral rule engine
- Returns structured signal dict with reasoning

### bot.py
- Main loop (asyncio)
- is_market_open() check
- Calls downloader → analyzer → signals
- Saves logs
- Compares with previous snapshot for OI change signals
- Sends Telegram alert (if configured)
- Handles errors gracefully
- Runs every 60 seconds
