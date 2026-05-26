"""
downloader.py -- Playwright-based NSE Option Chain CSV Downloader.

Why NSE blocks Playwright's bundled Chromium
---------------------------------------------
NSE uses Akamai Bot Manager which performs TLS fingerprint (JA3) analysis.
Playwright's bundled Chromium has a recognisable JA3 hash that Akamai blocks
immediately -- even before any HTTP bytes are sent. This manifests as:
  * ERR_HTTP2_PROTOCOL_ERROR  (HTTP/2 TLS rejected)
  * ERR_TIMED_OUT             (connection silently dropped)

Fix: Use the real system Google Chrome binary.
----------------------------------------------
Google Chrome's TLS stack produces a valid JA3 fingerprint that Akamai
trusts.  Playwright can drive a real Chrome installation just like it drives
its bundled Chromium -- the only difference is passing `executable_path`.

Additionally we apply playwright-stealth to mask JS-level bot signals
(navigator.webdriver, plugins, etc.).

Two-phase navigation
---------------------
Phase 1: Visit nseindia.com homepage  -->  Akamai sets valid session cookies.
Phase 2: Navigate to /option-chain    -->  Accepted because session is valid.

Historical CSV Storage
-----------------------
Every download is preserved permanently in a date-based subfolder:

  downloads/
    2026-05-26/
      nifty_option_chain_091501.csv   <- 09:15:01 snapshot
      nifty_option_chain_091601.csv   <- 09:16:01 snapshot
      ...
    latest.csv                        <- always the newest snapshot

Old CSVs are NEVER deleted, building a complete minute-by-minute history.
"""

import asyncio
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

try:
    from playwright_stealth import stealth_async
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

from config import (
    BROWSER_HEADLESS,
    DOWNLOAD_DIR,
    DOWNLOAD_TIMEOUT,
    ELEMENT_TIMEOUT,
    MAX_DOWNLOAD_RETRIES,
    NSE_BASE_URL,
    NSE_OPTION_CHAIN_URL,
    PAGE_LOAD_TIMEOUT,
    RETRY_SLEEP_SECONDS,
)

logger = logging.getLogger("downloader")

# Path to real Google Chrome on macOS -- passes Akamai TLS fingerprint check
CHROME_BINARY = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _archive_paths() -> tuple:
    """
    Return (archive_path, latest_path) for the current timestamp.

    archive_path : downloads/YYYY-MM-DD/nifty_option_chain_HHMMSS.csv
                   Unique per-second file — NEVER overwritten or deleted.
    latest_path  : downloads/latest.csv
                   Always overwritten with the newest snapshot for fast access.

    The date subfolder is created automatically if it does not exist.
    """
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")      # e.g. 2026-05-26
    time_str = now.strftime("%H%M%S")        # e.g. 102109

    date_dir = Path(DOWNLOAD_DIR) / date_str
    date_dir.mkdir(parents=True, exist_ok=True)

    archive_path = str(date_dir / f"nifty_option_chain_{time_str}.csv")
    latest_path  = str(Path(DOWNLOAD_DIR) / "latest.csv")
    return archive_path, latest_path


def _chrome_executable() -> Optional[str]:
    """Return path to real Chrome if available, else None (use Playwright Chromium)."""
    if os.path.exists(CHROME_BINARY):
        logger.info("Using real Google Chrome: %s", CHROME_BINARY)
        return CHROME_BINARY
    logger.warning(
        "Google Chrome not found at %s. Falling back to Playwright Chromium "
        "(may be blocked by Akamai).", CHROME_BINARY
    )
    return None


# ---------------------------------------------------------------------------
# Core download coroutine
# ---------------------------------------------------------------------------

async def _download_once(playwright) -> Optional[str]:
    """
    Single attempt: open NSE option chain page and download CSV.

    Strategy
    --------
    1. Launch real Chrome (trusted TLS) or Playwright Chromium + stealth.
    2. Visit homepage first (Phase 1) to get Akamai session cookies.
    3. Navigate to /option-chain (Phase 2).
    4. Dismiss popups, select NIFTY, select expiry, click Download.
    """
    browser = None
    try:
        chrome_path = _chrome_executable()

        launch_kwargs = dict(
            headless=BROWSER_HEADLESS,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--window-size=1920,1080",
                "--disable-blink-features=AutomationControlled",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
            ],
        )
        if chrome_path:
            launch_kwargs["executable_path"] = chrome_path

        logger.info(
            "Launching browser (headless=%s, chrome=%s)...",
            BROWSER_HEADLESS, bool(chrome_path),
        )
        browser = await playwright.chromium.launch(**launch_kwargs)

        context = await browser.new_context(
            accept_downloads=True,
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            extra_http_headers={
                "Accept-Language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )

        # Apply stealth scripts to mask JS-level bot signals
        page = await context.new_page()
        if HAS_STEALTH:
            await stealth_async(page)
            logger.info("playwright-stealth applied.")
        else:
            # Manual minimal stealth
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                "window.chrome = { runtime: {} };"
            )

        # ── Phase 1: Homepage warm-up ─────────────────────────────────────────
        # Akamai validates session cookies set on the main NSE domain.
        # Skipping this causes the /option-chain request to be rejected.
        logger.info("Phase 1: Visiting NSE homepage to establish session...")
        try:
            await page.goto(
                NSE_BASE_URL,
                wait_until="domcontentloaded",
                timeout=PAGE_LOAD_TIMEOUT,
            )
            # Allow Akamai JS challenge to complete (~3-6 s)
            await asyncio.sleep(6)
            logger.info("Homepage loaded. Title: %s", await page.title())
        except PlaywrightTimeout:
            logger.warning("Homepage timed out (Akamai may still have set cookies). Continuing...")
        except Exception as e:
            logger.warning("Homepage warm-up error (continuing): %s", e)

        # ── Phase 2: Option Chain page ────────────────────────────────────────
        logger.info("Phase 2: Navigating to NSE Option Chain page...")
        await page.goto(
            NSE_OPTION_CHAIN_URL,
            wait_until="domcontentloaded",
            timeout=PAGE_LOAD_TIMEOUT,
        )

        # Wait for option chain table to render
        try:
            await page.wait_for_load_state("networkidle", timeout=30_000)
        except PlaywrightTimeout:
            logger.warning("networkidle timeout — continuing with current page state...")

        await asyncio.sleep(4)
        title = await page.title()
        logger.info("Option chain page ready. Title: %s", title)

        if "nseindia" not in title.lower() and "option" not in title.lower():
            logger.warning(
                "Unexpected page title '%s' -- may have been blocked. Taking screenshot...", title
            )
            await page.screenshot(
                path=os.path.join(DOWNLOAD_DIR, "debug_blocked.png"), full_page=True
            )

        # ── Dismiss popups ────────────────────────────────────────────────────
        await _dismiss_popups(page)

        # ── Select NIFTY ──────────────────────────────────────────────────────
        await _select_nifty(page)

        # ── Select nearest expiry ─────────────────────────────────────────────
        await _select_expiry(page)

        # ── Click Download CSV ────────────────────────────────────────────────
        archive_path, latest_path = _archive_paths()
        logger.info("Looking for Download CSV button...")

        async with page.expect_download(timeout=DOWNLOAD_TIMEOUT) as dl_info:
            clicked = False
            selectors = [
                "button:has-text('Download (.csv)')",
                "a:has-text('Download (.csv)')",
                "button:has-text('Download')",
                "a:has-text('Download')",
                "[title='Download']",
                "#download-btn",
                ".download-btn",
                "button[class*='download']",
                "a[class*='download']",
                "i[class*='download']",
            ]
            for sel in selectors:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=5_000):
                        await btn.click()
                        clicked = True
                        logger.info("Clicked download button: %s", sel)
                        break
                except PlaywrightTimeout:
                    continue

            if not clicked:
                logger.error("Download button not found.")
                screenshot_path = os.path.join(DOWNLOAD_DIR, "debug_screenshot.png")
                await page.screenshot(path=screenshot_path, full_page=True)
                logger.info("Debug screenshot saved: %s", screenshot_path)
                await browser.close()
                return None

        download = await dl_info.value

        # Save the permanent archive copy (never deleted)
        await download.save_as(archive_path)
        file_size = os.path.getsize(archive_path)
        logger.info("Archive saved: %s (%d bytes)", archive_path, file_size)

        # Update latest.csv (always points to newest snapshot)
        shutil.copy2(archive_path, latest_path)
        logger.info("latest.csv updated: %s", latest_path)

        await browser.close()
        return archive_path

    except PlaywrightTimeout as exc:
        logger.error("Timeout error: %s", exc)
    except Exception as exc:
        logger.exception("Unexpected error in _download_once: %s", exc)
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
    return None


# ---------------------------------------------------------------------------
# Popup / cookie dismissal
# ---------------------------------------------------------------------------

async def _dismiss_popups(page) -> None:
    """Try to close common NSE cookie banners and modal pop-ups."""
    selectors = [
        "button:has-text('Accept')",
        "button:has-text('I Accept')",
        "button:has-text('Accept All')",
        "#cookie-consent-button",
        ".cookie-accept",
        "button.close",
        "button[aria-label='Close']",
        ".modal-close",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2_000):
                await btn.click()
                logger.debug("Dismissed popup: %s", sel)
                await asyncio.sleep(0.4)
        except PlaywrightTimeout:
            pass
        except Exception:
            pass


# ---------------------------------------------------------------------------
# NIFTY selector
# ---------------------------------------------------------------------------

async def _select_nifty(page) -> None:
    """Ensure NIFTY is selected as the underlying index."""
    try:
        for sid in ["underlyingSelect", "symbolInput", "underlying"]:
            el = page.locator(f"#{sid}")
            if await el.is_visible(timeout=3_000):
                await el.select_option(label="NIFTY")
                logger.info("Selected NIFTY via <select>#%s", sid)
                try:
                    await page.wait_for_load_state("networkidle", timeout=15_000)
                except PlaywrightTimeout:
                    pass
                return

        tab = page.locator(
            "li:has-text('NIFTY'), button:has-text('NIFTY'), a:has-text('NIFTY')"
        ).first
        if await tab.is_visible(timeout=5_000):
            await tab.click()
            logger.info("Clicked NIFTY tab/button")
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:
                pass
            return

        logger.warning("NIFTY selector not found -- assuming page is already on NIFTY.")
    except Exception as exc:
        logger.warning("_select_nifty error (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Expiry selector
# ---------------------------------------------------------------------------

async def _select_expiry(page) -> None:
    """Select the nearest expiry from the dropdown (first option)."""
    try:
        for sel in ["#expiryDate", "#expiryDateDropdown",
                    "select[name='expiryDate']", "select.expiry-select"]:
            el = page.locator(sel)
            if await el.is_visible(timeout=5_000):
                options = await el.locator("option").all()
                if options:
                    first_val = await options[0].get_attribute("value")
                    await el.select_option(value=first_val)
                    logger.info("Selected expiry: %s", first_val)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=15_000)
                    except PlaywrightTimeout:
                        pass
                return

        logger.warning("Expiry dropdown not found -- using page default.")
    except Exception as exc:
        logger.warning("_select_expiry error (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def download_option_chain_csv() -> Optional[str]:
    """
    Entry point: download with retries. Old CSVs are NEVER deleted.

    Each successful download produces two files:
      * downloads/YYYY-MM-DD/nifty_option_chain_HHMMSS.csv  (permanent archive)
      * downloads/latest.csv                                 (always newest)

    Returns the archive path on success, or None on total failure.
    """
    async with async_playwright() as pw:
        for attempt in range(1, MAX_DOWNLOAD_RETRIES + 1):
            logger.info("Download attempt %d / %d", attempt, MAX_DOWNLOAD_RETRIES)
            path = await _download_once(pw)
            if path and os.path.exists(path) and os.path.getsize(path) > 0:
                logger.info("Download successful: %s", path)
                return path
            logger.warning(
                "Attempt %d failed. Waiting %ds before retry...",
                attempt, RETRY_SLEEP_SECONDS,
            )
            if attempt < MAX_DOWNLOAD_RETRIES:
                await asyncio.sleep(RETRY_SLEEP_SECONDS)

    logger.error("All %d download attempts failed.", MAX_DOWNLOAD_RETRIES)
    return None


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    path = asyncio.run(download_option_chain_csv())
    if path:
        print(f"\nDownloaded: {path}")
        sys.exit(0)
    else:
        print("\nDownload failed.")
        sys.exit(1)
