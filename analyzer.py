"""
analyzer.py — OI Analysis Engine for NIFTY Option Chain CSV Bot.

Responsibilities
----------------
* Load the downloaded CSV into a pandas DataFrame.
* Normalize messy NSE column names (they vary between versions).
* Extract all required fields:
    StrikePrice, Call_OI, Put_OI, Call_OI_Change, Put_OI_Change,
    Volume, IV, LTP (for both CE and PE sides).
* Calculate:
    - PCR (Put-Call Ratio)
    - Max Call OI strike  → Resistance
    - Max Put OI strike   → Support
    - ATM strike (closest to spot price embedded in CSV header/footer)
    - ATM-window (ATM ± ATM_STRIKE_RANGE strikes)
    - OI change categorization per strike
* Returns a structured analysis dict consumed by signals.py.

OI Change Categories (per strike)
----------------------------------
  Call Writing     : Call OI ↑  →  Bears adding short positions at that strike
  Call Unwinding   : Call OI ↓  →  Bears covering shorts (bullish)
  Put Writing      : Put OI ↑   →  Bulls adding short puts at that strike
  Put Unwinding    : Put OI ↓   →  Bulls covering shorts (bearish)
"""

import logging
import re
from pathlib import Path
from typing import Optional

import pandas as pd

from config import ATM_STRIKE_RANGE

logger = logging.getLogger("analyzer")


# ─── Column-name aliases ───────────────────────────────────────────────────────
# NSE CSV uses a two-row header where columns are grouped as:
#   [CALLS] | Strike Price | [PUTS]
# The exact column names differ slightly across NSE versions.
# We normalise everything into snake_case internal names.

# Mapping: normalised_name → list of possible raw names (case-insensitive)
_COL_ALIASES: dict[str, list[str]] = {
    # Strike price (shared column in the middle)
    "strike_price": ["strike price", "strikeprice", "strike"],

    # CALL side
    "call_oi":         ["oi", "calls oi", "call oi", "ce oi"],
    "call_oi_change":  ["chng in oi", "change in oi", "chng in oi.1",
                        "calls chng in oi", "call chng in oi"],
    "call_volume":     ["volume", "calls volume", "call volume", "ce volume"],
    "call_iv":         ["iv", "calls iv", "call iv", "ce iv", "implied volatility"],
    "call_ltp":        ["ltp", "calls ltp", "call ltp", "ce ltp"],

    # PUT side (NSE appends ".1" suffix to duplicate column names)
    "put_oi":          ["oi.1", "puts oi", "put oi", "pe oi"],
    "put_oi_change":   ["chng in oi.1", "change in oi.1", "puts chng in oi",
                        "put chng in oi"],
    "put_volume":      ["volume.1", "puts volume", "put volume", "pe volume"],
    "put_iv":          ["iv.1", "puts iv", "put iv", "pe iv"],
    "put_ltp":         ["ltp.1", "puts ltp", "put ltp", "pe ltp"],
}


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Rename raw NSE CSV columns to our internal snake_case names.
    Unrecognised columns are kept as-is.
    """
    # Build a reverse lookup: raw_lower → normalised_name
    reverse: dict[str, str] = {}
    for norm, aliases in _COL_ALIASES.items():
        for alias in aliases:
            reverse[alias.lower().strip()] = norm

    rename_map: dict[str, str] = {}
    for raw_col in df.columns:
        key = str(raw_col).lower().strip()
        if key in reverse:
            rename_map[raw_col] = reverse[key]

    df = df.rename(columns=rename_map)
    logger.debug("Column rename map: %s", rename_map)
    return df


def _to_numeric(series: pd.Series) -> pd.Series:
    """Convert a column with possible commas / dashes to float."""
    return (
        series
        .astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("-", "0", regex=False)
        .str.strip()
        .pipe(pd.to_numeric, errors="coerce")
        .fillna(0)
    )


def _extract_spot_from_csv(raw_text: str) -> Optional[float]:
    """
    NSE CSVs embed the underlying value in the first few rows as text like:
       'Underlying Index : NIFTY 50 ,24862.25,'
    Try to parse it.
    """
    # Pattern: a number with possible commas/decimals after "NIFTY" or similar
    patterns = [
        r"Underlying.*?([0-9,]+\.[0-9]+)",
        r"NIFTY\s+50\s*,\s*([0-9,]+\.[0-9]+)",
        r"([0-9]{4,6}\.[0-9]{2})",
    ]
    for pat in patterns:
        m = re.search(pat, raw_text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                pass
    return None


# ─── Public function ───────────────────────────────────────────────────────────

def analyze_csv(csv_path: str) -> Optional[dict]:
    """
    Parse the NSE option chain CSV and return a rich analysis dictionary.

    Parameters
    ----------
    csv_path : str
        Absolute path to the downloaded CSV file.

    Returns
    -------
    dict | None
        Analysis results, or None if the file is unreadable / empty.
    """
    path = Path(csv_path)
    if not path.exists() or path.stat().st_size == 0:
        logger.error("CSV file missing or empty: %s", csv_path)
        return None

    # ── Read raw text to grab the spot price from the header comment rows ──
    try:
        raw_text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.error("Cannot read CSV: %s", exc)
        return None

    spot_price = _extract_spot_from_csv(raw_text)

    # ── Load CSV into DataFrame ────────────────────────────────────────────
    # NSE CSVs have a few metadata/header rows before the actual table.
    # We skip rows until we hit the row containing "Strike Price".
    df_raw = None
    for skip in range(0, 10):
        try:
            candidate = pd.read_csv(csv_path, skiprows=skip, encoding="utf-8",
                                    on_bad_lines="skip")
            # Check if the row looks like the actual header
            cols_lower = [str(c).lower().strip() for c in candidate.columns]
            if any("strike" in c for c in cols_lower):
                df_raw = candidate
                logger.debug("CSV header found at skiprows=%d", skip)
                break
        except Exception:
            continue

    if df_raw is None or df_raw.empty:
        logger.error("Could not parse a valid data table from CSV: %s", csv_path)
        return None

    # ── Normalise column names ─────────────────────────────────────────────
    df = _normalise_columns(df_raw)

    # ── Verify required columns ────────────────────────────────────────────
    required = ["strike_price", "call_oi", "put_oi"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        logger.error("Required columns missing after normalisation: %s. "
                     "Available: %s", missing, list(df.columns))
        return None

    # ── Convert to numeric ─────────────────────────────────────────────────
    numeric_cols = [
        "strike_price",
        "call_oi", "call_oi_change", "call_volume", "call_iv", "call_ltp",
        "put_oi",  "put_oi_change",  "put_volume",  "put_iv",  "put_ltp",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = _to_numeric(df[col])

    # Drop rows where strike_price is 0 or NaN
    df = df[df["strike_price"] > 0].copy()
    df = df.reset_index(drop=True)

    if df.empty:
        logger.error("No valid strike rows found in CSV.")
        return None

    logger.info("Loaded %d strikes from CSV.", len(df))

    # ── Spot price fallback: use the strike closest to the median of all ───
    if spot_price is None:
        mid_idx = len(df) // 2
        spot_price = float(df["strike_price"].iloc[mid_idx])
        logger.warning("Spot price not found in CSV; using middle strike: %s", spot_price)

    # ── ATM detection ──────────────────────────────────────────────────────
    df["dist_from_spot"] = (df["strike_price"] - spot_price).abs()
    atm_idx = df["dist_from_spot"].idxmin()
    atm_strike = float(df.loc[atm_idx, "strike_price"])
    logger.info("Spot=%.2f  ATM strike=%.0f", spot_price, atm_strike)

    # ── ATM window ────────────────────────────────────────────────────────
    all_strikes = sorted(df["strike_price"].unique().tolist())
    atm_pos = all_strikes.index(atm_strike) if atm_strike in all_strikes else len(all_strikes) // 2
    lo = max(0, atm_pos - ATM_STRIKE_RANGE)
    hi = min(len(all_strikes) - 1, atm_pos + ATM_STRIKE_RANGE)
    window_strikes = all_strikes[lo: hi + 1]
    df_window = df[df["strike_price"].isin(window_strikes)].copy()

    # ── PCR ───────────────────────────────────────────────────────────────
    total_call_oi = df["call_oi"].sum()
    total_put_oi  = df["put_oi"].sum()
    pcr_overall   = (total_put_oi / total_call_oi) if total_call_oi > 0 else 0.0

    total_call_oi_window = df_window["call_oi"].sum()
    total_put_oi_window  = df_window["put_oi"].sum()
    pcr_window = (
        total_put_oi_window / total_call_oi_window
        if total_call_oi_window > 0
        else 0.0
    )

    # ── Max OI strikes ────────────────────────────────────────────────────
    max_call_oi_strike = float(df.loc[df["call_oi"].idxmax(), "strike_price"])
    max_put_oi_strike  = float(df.loc[df["put_oi"].idxmax(),  "strike_price"])
    max_call_oi_value  = float(df["call_oi"].max())
    max_put_oi_value   = float(df["put_oi"].max())

    # ── OI change categorization per strike ───────────────────────────────
    categories: dict[float, str] = {}
    if "call_oi_change" in df.columns and "put_oi_change" in df.columns:
        for _, row in df.iterrows():
            call_ch = row.get("call_oi_change", 0) or 0
            put_ch  = row.get("put_oi_change",  0) or 0
            strike  = row["strike_price"]

            if call_ch > 0 and put_ch <= 0:
                cat = "Call Writing"          # Bearish signal
            elif call_ch < 0 and put_ch >= 0:
                cat = "Call Unwinding"        # Bullish signal
            elif put_ch > 0 and call_ch <= 0:
                cat = "Put Writing"           # Bullish signal
            elif put_ch < 0 and call_ch >= 0:
                cat = "Put Unwinding"         # Bearish signal
            elif call_ch > 0 and put_ch > 0:
                cat = "Both Writing"
            elif call_ch < 0 and put_ch < 0:
                cat = "Both Unwinding"
            else:
                cat = "Neutral"

            categories[float(strike)] = cat

    # ── Aggregate OI change counts across full chain ───────────────────────
    oi_change_summary = {
        "call_writing":   sum(1 for v in categories.values() if "Call Writing"  in v),
        "call_unwinding": sum(1 for v in categories.values() if "Call Unwinding" in v),
        "put_writing":    sum(1 for v in categories.values() if "Put Writing"   in v),
        "put_unwinding":  sum(1 for v in categories.values() if "Put Unwinding" in v),
    }

    # ── ATM-window OI change summary ──────────────────────────────────────
    window_call_oi_change = float(df_window.get("call_oi_change", pd.Series([0])).sum()) \
        if "call_oi_change" in df_window.columns else 0.0
    window_put_oi_change  = float(df_window.get("put_oi_change",  pd.Series([0])).sum()) \
        if "put_oi_change"  in df_window.columns else 0.0

    # ── ATM row data ──────────────────────────────────────────────────────
    atm_row = df.loc[atm_idx]
    atm_data = {
        "strike":         atm_strike,
        "call_oi":        float(atm_row.get("call_oi", 0)),
        "put_oi":         float(atm_row.get("put_oi",  0)),
        "call_oi_change": float(atm_row.get("call_oi_change", 0)),
        "put_oi_change":  float(atm_row.get("put_oi_change",  0)),
        "call_iv":        float(atm_row.get("call_iv", 0)),
        "put_iv":         float(atm_row.get("put_iv",  0)),
        "call_ltp":       float(atm_row.get("call_ltp", 0)),
        "put_ltp":        float(atm_row.get("put_ltp",  0)),
    }

    # ── Build final analysis dict ─────────────────────────────────────────
    analysis = {
        # Meta
        "csv_path":           csv_path,
        "num_strikes":        len(df),
        "spot_price":         spot_price,

        # ATM
        "atm_strike":         atm_strike,
        "atm_data":           atm_data,
        "window_strikes":     window_strikes,

        # PCR
        "pcr_overall":        round(pcr_overall, 4),
        "pcr_window":         round(pcr_window,  4),
        "total_call_oi":      int(total_call_oi),
        "total_put_oi":       int(total_put_oi),

        # Support / Resistance
        "max_call_oi_strike": max_call_oi_strike,   # Resistance
        "max_call_oi_value":  int(max_call_oi_value),
        "max_put_oi_strike":  max_put_oi_strike,    # Support
        "max_put_oi_value":   int(max_put_oi_value),

        # OI change categorization
        "oi_categories":      categories,
        "oi_change_summary":  oi_change_summary,

        # ATM-window aggregates
        "window_call_oi_change": window_call_oi_change,
        "window_put_oi_change":  window_put_oi_change,

        # Full DataFrame (for logging / further use)
        "dataframe":          df,
    }

    logger.info(
        "Analysis done | PCR=%.2f | ATM=%.0f | Resistance=%.0f | Support=%.0f",
        pcr_overall, atm_strike, max_call_oi_strike, max_put_oi_strike,
    )

    return analysis


# ─── CLI test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import glob

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    from config import DOWNLOAD_DIR
    csvs = sorted(glob.glob(str(Path(DOWNLOAD_DIR) / "*.csv")))
    if not csvs:
        print("No CSV files found in downloads/. Run downloader.py first.")
        sys.exit(1)

    latest = csvs[-1]
    print(f"Analysing: {latest}")
    result = analyze_csv(latest)
    if result:
        for k, v in result.items():
            if k not in ("dataframe", "oi_categories", "window_strikes"):
                print(f"  {k}: {v}")
    else:
        print("Analysis failed.")
        sys.exit(1)
