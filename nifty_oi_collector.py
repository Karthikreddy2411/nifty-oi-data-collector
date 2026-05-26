"""
nifty_oi_collector.py
---------------------
GitHub Actions-compatible NSE NIFTY option chain collector.

Strategy
--------
NSE requires valid session cookies (set by Akamai on the homepage).
We simulate a real browser session using:
  1. Warm-up  : GET nseindia.com  → collects cookies
  2. Referer  : GET /option-chain  → sets Referer header
  3. API call : GET /api/option-chain-indices?symbol=NIFTY

All data is appended to data/nifty_oi_data.csv in the repo.
"""

import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# ── Output path ───────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
CSV_PATH = DATA_DIR / "nifty_oi_data.csv"

# ── NSE endpoints ─────────────────────────────────────────────────────────────
NSE_HOME    = "https://www.nseindia.com"
NSE_OC_PAGE = "https://www.nseindia.com/option-chain"
NSE_API     = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"

# ── Headers that mimic a real Chrome browser ──────────────────────────────────
BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
    "DNT":             "1",
    "Upgrade-Insecure-Requests": "1",
}

API_HEADERS = {
    "User-Agent": BASE_HEADERS["User-Agent"],
    "Accept":     "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":    NSE_OC_PAGE,
    "X-Requested-With": "XMLHttpRequest",
    "Connection": "keep-alive",
}

# CSV columns written to file
CSV_COLUMNS = [
    "timestamp", "spot_price",
    "strike_price",
    "ce_oi", "ce_oi_change", "ce_volume", "ce_ltp", "ce_iv",
    "pe_oi", "pe_oi_change", "pe_volume", "pe_ltp", "pe_iv",
    "pcr_strike",
]

MAX_RETRIES = 4
RETRY_DELAY = 15   # seconds between retries


# ─────────────────────────────────────────────────────────────────────────────
# Session warm-up
# ─────────────────────────────────────────────────────────────────────────────

def build_session() -> requests.Session:
    """
    Create a requests Session with NSE cookies obtained via a two-step warm-up:
      1. Hit the homepage  →  Akamai sets initial cookies
      2. Hit /option-chain →  page sets additional session cookies
    """
    session = requests.Session()
    session.headers.update(BASE_HEADERS)

    print("[Session] Warming up — visiting NSE homepage...")
    try:
        r = session.get(NSE_HOME, timeout=30)
        print(f"[Session] Homepage status: {r.status_code}")
    except Exception as exc:
        print(f"[Session] Homepage warm-up warning: {exc}")

    time.sleep(3)

    print("[Session] Visiting option-chain page...")
    try:
        r = session.get(NSE_OC_PAGE, timeout=30)
        print(f"[Session] Option-chain page status: {r.status_code}")
    except Exception as exc:
        print(f"[Session] Option-chain page warning: {exc}")

    time.sleep(2)
    return session


# ─────────────────────────────────────────────────────────────────────────────
# Fetch NSE API
# ─────────────────────────────────────────────────────────────────────────────

def fetch_nse_data(session: requests.Session) -> dict | None:
    """Call the NSE option chain API and return parsed JSON, or None on failure."""
    print(f"[Fetch] Calling NSE API...")
    try:
        resp = session.get(NSE_API, headers=API_HEADERS, timeout=30)
        print(f"[Fetch] Status: {resp.status_code}  Bytes: {len(resp.content)}")

        if resp.status_code != 200:
            print(f"[Fetch] Non-200 response. Body preview: {resp.text[:300]}")
            return None

        data = resp.json()
        if "records" not in data:
            print(f"[Fetch] Unexpected JSON keys: {list(data.keys())}")
            return None

        return data

    except json.JSONDecodeError as exc:
        print(f"[Fetch] JSON decode error: {exc}")
    except requests.RequestException as exc:
        print(f"[Fetch] Request error: {exc}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Parse API response
# ─────────────────────────────────────────────────────────────────────────────

def parse_data(data: dict) -> list[dict]:
    """Extract per-strike rows from the NSE API JSON."""
    records = data.get("records", {})
    timestamp_raw = records.get("timestamp", "")
    # Normalise timestamp to ISO-ish format
    try:
        ts = datetime.strptime(timestamp_raw, "%d-%b-%Y %H:%M:%S")
        timestamp = ts.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Spot price
    spot_price = records.get("underlyingValue", 0.0)
    if not spot_price:
        items = records.get("data", [])
        for item in items:
            uv = item.get("CE", {}).get("underlyingValue") or \
                 item.get("PE", {}).get("underlyingValue")
            if uv:
                spot_price = uv
                break

    rows = []
    for item in records.get("data", []):
        strike = item.get("strikePrice", 0)
        ce = item.get("CE", {})
        pe = item.get("PE", {})

        ce_oi = ce.get("openInterest", 0) or 0
        pe_oi = pe.get("openInterest", 0) or 0
        pcr_strike = round(pe_oi / ce_oi, 4) if ce_oi else 0.0

        rows.append({
            "timestamp":    timestamp,
            "spot_price":   spot_price,
            "strike_price": strike,
            "ce_oi":        ce_oi,
            "ce_oi_change": ce.get("changeinOpenInterest", 0) or 0,
            "ce_volume":    ce.get("totalTradedVolume", 0) or 0,
            "ce_ltp":       ce.get("lastPrice", 0.0) or 0.0,
            "ce_iv":        ce.get("impliedVolatility", 0.0) or 0.0,
            "pe_oi":        pe_oi,
            "pe_oi_change": pe.get("changeinOpenInterest", 0) or 0,
            "pe_volume":    pe.get("totalTradedVolume", 0) or 0,
            "pe_ltp":       pe.get("lastPrice", 0.0) or 0.0,
            "pe_iv":        pe.get("impliedVolatility", 0.0) or 0.0,
            "pcr_strike":   pcr_strike,
        })

    print(f"[Parse] {len(rows)} strikes parsed. Spot={spot_price}")
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Save to CSV
# ─────────────────────────────────────────────────────────────────────────────

def save_to_csv(rows: list[dict]) -> None:
    """Append rows to the master CSV, writing header only if file is new."""
    file_exists = CSV_PATH.exists() and CSV_PATH.stat().st_size > 0
    with open(CSV_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)
    print(f"[CSV] Appended {len(rows)} rows → {CSV_PATH}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n{'='*60}")
        print(f"[Main] Attempt {attempt}/{MAX_RETRIES}  —  {datetime.now().isoformat()}")
        print(f"{'='*60}")

        session = build_session()
        data = fetch_nse_data(session)

        if data:
            rows = parse_data(data)
            if rows:
                save_to_csv(rows)

                # Print quick summary
                total_ce = sum(r["ce_oi"] for r in rows)
                total_pe = sum(r["pe_oi"] for r in rows)
                pcr = round(total_pe / total_ce, 4) if total_ce else 0
                spot = rows[0]["spot_price"]
                print(f"\n✅ Success! Spot={spot}  CE_OI={total_ce:,}  PE_OI={total_pe:,}  PCR={pcr}")
                sys.exit(0)
            else:
                print("[Main] Parsed 0 rows — data may be empty (market closed?)")
                sys.exit(0)   # Not a script error; just no data
        else:
            print(f"[Main] Fetch failed on attempt {attempt}.")
            if attempt < MAX_RETRIES:
                print(f"[Main] Retrying in {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)

    print("\n❌ All attempts failed. Exiting with error.")
    sys.exit(1)


if __name__ == "__main__":
    main()
