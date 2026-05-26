# NIFTY Option Chain CSV Bot 🤖📊

A production-grade Python bot that downloads NIFTY option chain data from NSE
using real browser automation (Playwright), analyses Open Interest, and generates
actionable intraday trading signals — all running automatically every 60 seconds.

---

## ✨ Features

| Feature | Details |
|---|---|
| **Browser automation** | Playwright Chromium — avoids NSE blocking |
| **CSV download** | Clicks the real NSE "Download (.csv)" button |
| **OI analysis** | PCR, ATM window, max OI levels, change categories |
| **Signal engine** | Scored rule-based signals with reasons |
| **Loop** | Runs every 60 s during market hours |
| **Logging** | Rotating daily logs + signals.jsonl |
| **Telegram** | Optional push alerts for non-neutral signals |
| **Error handling** | Retries, popup dismissal, browser crash recovery |

---

## 📁 Project Structure

```
project/
├── bot.py          ← Main orchestrator (run this)
├── downloader.py   ← Playwright browser + CSV download
├── analyzer.py     ← OI analysis engine
├── signals.py      ← Signal generation (rule-based scoring)
├── config.py       ← All tunable settings
├── downloads/      ← CSVs saved here (auto-cleared each run)
├── logs/
│   ├── bot.log     ← Rotating daily log
│   └── signals.jsonl ← Structured signal history (JSON Lines)
└── requirements.txt
```

---

## 🚀 Quick Start

### 1. Install dependencies

```bash
cd "oi based ai"

# Activate your virtual environment (if any)
source venv/bin/activate

pip install -r requirements.txt

# Install Playwright browser binaries (one-time)
playwright install chromium
```

### 2. Run the bot

```bash
# Normal mode — respects market hours (09:15–15:30 IST, Mon–Fri)
python bot.py

# Force run regardless of market hours (for testing)
python bot.py --force

# Run a single cycle and exit
python bot.py --force --once

# Debug mode — visible browser window + verbose logs
python bot.py --debug --force --once
```

---

## ⚙️ Configuration (`config.py`)

| Setting | Default | Description |
|---|---|---|
| `BROWSER_HEADLESS` | `True` | Set `False` to watch the browser |
| `LOOP_INTERVAL_SECONDS` | `60` | How often the bot runs (seconds) |
| `MAX_DOWNLOAD_RETRIES` | `3` | Download retry attempts |
| `ATM_STRIKE_RANGE` | `10` | Strikes above/below ATM to analyse |
| `PCR_BULLISH_THRESHOLD` | `1.05` | PCR above this → bullish |
| `PCR_BEARISH_THRESHOLD` | `0.95` | PCR below this → bearish |
| `OI_LARGE_SHIFT_LOTS` | `50000` | Threshold for large OI shift alerts |

---

## 📱 Telegram Setup (Optional)

1. Create a bot via [@BotFather](https://t.me/BotFather) and get your token.
2. Get your Chat ID from [@userinfobot](https://t.me/userinfobot).
3. Install aiohttp: `pip install aiohttp`
4. Set environment variables:

```bash
export TELEGRAM_BOT_TOKEN="your_token_here"
export TELEGRAM_CHAT_ID="your_chat_id_here"
python bot.py
```

---

## 📊 Signal Logic

### Score-based engine

Each analysis cycle generates a **score** (positive = bullish, negative = bearish):

| Rule | Bullish Score | Bearish Score |
|---|---|---|
| PCR > 1.20 (very high) | +2 | — |
| PCR > 1.05 | +1 | — |
| PCR < 0.80 (very low) | — | -2 |
| PCR < 0.95 | — | -1 |
| ATM-window PCR > 1.10 | +1 | — |
| ATM-window PCR < 0.90 | — | -1 |
| Put buildup near ATM | +1 | — |
| Call unwinding near ATM | +1 | — |
| Call buildup near ATM | — | -1 |
| Put unwinding near ATM | — | -1 |
| Chain-wide put writing dominant | +1 | — |
| Chain-wide call writing dominant | — | -1 |
| Spot near support level | +1 | — |
| Spot near resistance level | — | -1 |
| PCR rising (vs prev cycle) | +1 | — |
| PCR falling (vs prev cycle) | — | -1 |

### Signal labels

| Score | Signal |
|---|---|
| ≥ +3 | 🚀 STRONG BULLISH |
| +1 or +2 | 🟢 BULLISH |
| 0 | ⚪ NEUTRAL |
| -1 or -2 | 🔴 BEARISH |
| ≤ -3 | 🔻 STRONG BEARISH |

---

## 📈 OI Change Categories (per strike)

| Category | Meaning | Bias |
|---|---|---|
| Call Writing | Call OI ↑ | Bearish — bears adding shorts |
| Call Unwinding | Call OI ↓ | Bullish — bears exiting |
| Put Writing | Put OI ↑ | Bullish — bulls adding put shorts |
| Put Unwinding | Put OI ↓ | Bearish — bulls exiting |

---

## 🔍 Testing Individual Modules

```bash
# Test only the downloader (downloads CSV)
python downloader.py

# Test only the analyser (needs a CSV in downloads/)
python analyzer.py

# Test only the signal engine (needs a CSV in downloads/)
python signals.py
```

---

## 📋 Log Files

| File | Description |
|---|---|
| `logs/bot.log` | Full rotating log (daily, 30-day retention) |
| `logs/signals.jsonl` | Structured signal history (JSON Lines) |
| `downloads/*.csv` | Latest downloaded option chain CSV |
| `downloads/debug_screenshot.png` | Screenshot saved on download failure |

---

## ⚠️ Important Notes

- **Do NOT** reduce the loop interval below 60 seconds — NSE may block your IP.
- The bot only runs during **market hours** by default. Use `--force` for testing.
- The Playwright Chromium binary must be installed separately (`playwright install chromium`).
- If the download button selector changes on NSE, update `downloader.py → selectors` list.
