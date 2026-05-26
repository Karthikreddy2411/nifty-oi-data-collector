"""
bot.py — Main Orchestrator for NIFTY Option Chain CSV Bot.

Architecture
------------
  1. Validate market hours (IST).
  2. Download CSV via Playwright (downloader.py).
  3. Parse & analyse OI data (analyzer.py).
  4. Compare with previous cycle snapshot.
  5. Generate trading signal (signals.py).
  6. Log results to rotating log files.
  7. Send Telegram alert (optional).
  8. Sleep until next cycle (LOOP_INTERVAL_SECONDS).
  9. Repeat continuously.

Usage
-----
  python bot.py                  # Respects market hours
  python bot.py --force          # Run once regardless of market hours (testing)
  python bot.py --debug          # Verbose logging + visible browser
  python bot.py --once           # Run a single cycle then exit
"""

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import pytz

from config import (
    BROWSER_HEADLESS,
    DOWNLOAD_DIR,
    LOG_DIR,
    LOOP_INTERVAL_SECONDS,
    MARKET_END_HOUR,
    MARKET_END_MINUTE,
    MARKET_START_HOUR,
    MARKET_START_MINUTE,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_ENABLED,
)
from downloader import download_option_chain_csv
from analyzer import analyze_csv
from signals import generate_signal

IST = pytz.timezone("Asia/Kolkata")


# ─── Logging setup ────────────────────────────────────────────────────────────

def _setup_logging(debug: bool = False) -> None:
    """Configure root logger with both console and rotating file handlers."""
    level = logging.DEBUG if debug else logging.INFO

    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # Rotating file handler — one file per day, keep 30 days
    log_file = Path(LOG_DIR) / "bot.log"
    fh = logging.handlers.TimedRotatingFileHandler(
        log_file, when="midnight", backupCount=30, encoding="utf-8"
    )
    fh.setLevel(level)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    logging.info("Logging initialised. Log file: %s", log_file)


logger = logging.getLogger("bot")


# ─── Signal log ───────────────────────────────────────────────────────────────

_SIGNAL_LOG_PATH = Path(LOG_DIR) / "signals.jsonl"


def _append_signal_log(signal_dict: dict, analysis: dict) -> None:
    """Append a structured signal entry to signals.jsonl."""
    entry = {
        "timestamp":  datetime.now(IST).isoformat(),
        "signal":     signal_dict.get("signal"),
        "score":      signal_dict.get("score"),
        "summary":    signal_dict.get("summary"),
        "pcr":        analysis.get("pcr_overall"),
        "spot_price": analysis.get("spot_price"),
        "atm_strike": analysis.get("atm_strike"),
        "resistance": analysis.get("max_call_oi_strike"),
        "support":    analysis.get("max_put_oi_strike"),
        "reasons":    signal_dict.get("reasons", []),
        "alerts":     signal_dict.get("alerts",  []),
    }
    try:
        with open(_SIGNAL_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as exc:
        logger.warning("Could not write to signal log: %s", exc)


# ─── Market hours check ───────────────────────────────────────────────────────

def is_market_open() -> bool:
    """Return True if current IST time is within NSE market hours (Mon–Fri)."""
    now = datetime.now(IST)
    if now.weekday() > 4:          # Saturday=5, Sunday=6
        return False
    start = now.replace(hour=MARKET_START_HOUR, minute=MARKET_START_MINUTE,
                        second=0, microsecond=0)
    end   = now.replace(hour=MARKET_END_HOUR,   minute=MARKET_END_MINUTE,
                        second=0, microsecond=0)
    return start <= now <= end


# ─── Telegram alerts ──────────────────────────────────────────────────────────

async def _send_telegram(message: str) -> None:
    """Send a message to a Telegram chat via the Bot API."""
    if not TELEGRAM_ENABLED:
        return
    try:
        import aiohttp  # optional dependency
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    logger.info("Telegram alert sent.")
                else:
                    body = await resp.text()
                    logger.warning("Telegram send failed (%d): %s", resp.status, body)
    except ImportError:
        logger.warning("aiohttp not installed — Telegram alerts disabled.")
    except Exception as exc:
        logger.warning("Telegram error: %s", exc)


def _build_telegram_message(signal_dict: dict, analysis: dict) -> str:
    """Format a Telegram-friendly message."""
    lines = [
        f"<b>NIFTY Option Chain Signal</b>",
        f"🕐 {datetime.now(IST).strftime('%d %b %Y %H:%M IST')}",
        "",
        f"<b>{signal_dict['summary']}</b>",
        "",
        "<b>Reasons:</b>",
    ]
    for r in signal_dict.get("reasons", []):
        lines.append(f"• {r}")

    alerts = signal_dict.get("alerts", [])
    if alerts:
        lines.append("")
        lines.append("<b>⚡ Alerts:</b>")
        for a in alerts:
            lines.append(f"• {a}")

    return "\n".join(lines)


# ─── Single bot cycle ─────────────────────────────────────────────────────────

async def run_cycle(
    prev_analysis: Optional[dict],
    force: bool = False,
) -> Tuple[Optional[dict], Optional[dict]]:
    """
    Execute one full bot cycle.

    Returns
    -------
    (analysis, signal_dict) on success, (None, None) on failure.
    The caller should store analysis as prev_analysis for the next cycle.
    """
    now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")

    if not force and not is_market_open():
        logger.info("[%s] Market closed — skipping cycle.", now_str)
        return None, None

    logger.info("=" * 60)
    logger.info("▶  Cycle started at %s", now_str)
    logger.info("=" * 60)

    # ── Step 1: Download CSV ──────────────────────────────────────────────────
    logger.info("Step 1/4 — Downloading option chain CSV…")
    try:
        csv_path = await download_option_chain_csv()
    except Exception as exc:
        logger.error("Download raised exception: %s\n%s", exc, traceback.format_exc())
        csv_path = None

    if not csv_path:
        logger.error("❌ Download failed. Skipping this cycle.")
        return None, None

    logger.info("✅ CSV ready: %s", csv_path)

    # ── Step 2: Analyse CSV ───────────────────────────────────────────────────
    logger.info("Step 2/4 — Analysing OI data…")
    try:
        analysis = analyze_csv(csv_path)
    except Exception as exc:
        logger.error("Analysis raised exception: %s\n%s", exc, traceback.format_exc())
        analysis = None

    if not analysis:
        logger.error("❌ Analysis failed. Skipping this cycle.")
        return None, None

    logger.info(
        "✅ Analysis done | Spot=%.0f | ATM=%.0f | PCR=%.2f | "
        "Resistance=%.0f | Support=%.0f",
        analysis["spot_price"],
        analysis["atm_strike"],
        analysis["pcr_overall"],
        analysis["max_call_oi_strike"],
        analysis["max_put_oi_strike"],
    )

    # ── Step 3: Generate signal ───────────────────────────────────────────────
    logger.info("Step 3/4 — Generating signal…")
    try:
        signal_dict = generate_signal(analysis, prev_analysis)
    except Exception as exc:
        logger.error("Signal generation raised exception: %s", exc)
        signal_dict = None

    if not signal_dict:
        logger.error("❌ Signal generation failed.")
        return analysis, None

    logger.info("✅ Signal: %s", signal_dict["summary"])

    # Print reasons to console / log
    for reason in signal_dict.get("reasons", []):
        logger.info("  %s", reason)

    alerts = signal_dict.get("alerts", [])
    if alerts:
        logger.warning("  ALERTS:")
        for alert in alerts:
            logger.warning("    %s", alert)

    # ── Step 4: Persist & alert ───────────────────────────────────────────────
    logger.info("Step 4/4 — Saving logs & sending alerts…")
    _append_signal_log(signal_dict, analysis)

    # Send Telegram for non-neutral signals or if there are alerts
    if TELEGRAM_ENABLED and (signal_dict["score"] != 0 or alerts):
        msg = _build_telegram_message(signal_dict, analysis)
        await _send_telegram(msg)

    logger.info("✅ Cycle complete.\n")
    return analysis, signal_dict


# ─── Main loop ────────────────────────────────────────────────────────────────

async def main_loop(force: bool = False, once: bool = False) -> None:
    """Continuous loop that runs a cycle every LOOP_INTERVAL_SECONDS."""
    logger.info("🤖 NIFTY Option Chain CSV Bot starting…")
    logger.info(
        "Loop interval: %ds | Market hours: %02d:%02d–%02d:%02d IST",
        LOOP_INTERVAL_SECONDS,
        MARKET_START_HOUR, MARKET_START_MINUTE,
        MARKET_END_HOUR,   MARKET_END_MINUTE,
    )

    prev_analysis: Optional[dict] = None

    while True:
        cycle_start = asyncio.get_event_loop().time()

        analysis, _signal = await run_cycle(prev_analysis, force=force)
        if analysis is not None:
            prev_analysis = analysis

        if once:
            logger.info("--once flag set. Exiting.")
            break

        # Sleep for the remainder of the interval
        elapsed = asyncio.get_event_loop().time() - cycle_start
        sleep_for = max(0, LOOP_INTERVAL_SECONDS - elapsed)
        logger.info("Next cycle in %.0fs…", sleep_for)
        await asyncio.sleep(sleep_for)


# ─── Entry point ──────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="NIFTY Option Chain CSV Bot",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Run regardless of market hours (useful for testing).",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single cycle and exit.",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Enable verbose debug logging and show browser window.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    # Override headless flag when --debug is set
    if args.debug:
        import config
        config.BROWSER_HEADLESS = False

    _setup_logging(debug=args.debug)

    try:
        asyncio.run(main_loop(force=args.force, once=args.once))
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (KeyboardInterrupt).")
        sys.exit(0)
    except Exception as exc:
        logger.critical("Fatal error: %s\n%s", exc, traceback.format_exc())
        sys.exit(1)
