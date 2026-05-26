"""
signals.py — Signal Generation Engine for NIFTY Option Chain CSV Bot.

Takes the analysis dictionary produced by analyzer.analyze_csv() and
applies a rule-based engine to generate actionable intraday signals.

Signal Levels
-------------
  STRONG BULLISH  — Multiple confluent bullish factors
  BULLISH         — Dominant bullish factor(s)
  NEUTRAL         — Balanced or unclear picture
  BEARISH         — Dominant bearish factor(s)
  STRONG BEARISH  — Multiple confluent bearish factors

Each signal is accompanied by:
  * reasons  : list of string explanations
  * score    : integer score (+ve = bullish, -ve = bearish)
  * alerts   : list of important observations worth a Telegram ping
"""

import logging
from typing import Optional

from config import (
    ATM_STRIKE_RANGE,
    OI_LARGE_SHIFT_LOTS,
    PCR_BEARISH_THRESHOLD,
    PCR_BULLISH_THRESHOLD,
)

logger = logging.getLogger("signals")


# ─── Signal scoring constants ──────────────────────────────────────────────────
# Each rule adds/subtracts from a cumulative score.
# Final score → label conversion at the bottom.

_PCR_STRONG_BULL  =  2    # PCR > 1.20
_PCR_BULL         =  1    # PCR > PCR_BULLISH_THRESHOLD
_PCR_STRONG_BEAR  = -2    # PCR < 0.80
_PCR_BEAR         = -1    # PCR < PCR_BEARISH_THRESHOLD

_PUT_WRITE_BULL   =  1    # Net put writing in ATM window → bulls adding shorts
_PUT_UNWIND_BEAR  = -1    # Net put unwinding in ATM window → bulls exiting
_CALL_WRITE_BEAR  = -1    # Net call writing in ATM window → bears adding shorts
_CALL_UNWIND_BULL =  1    # Net call unwinding in ATM window → bears exiting

_LARGE_OI_SHIFT   =  0    # Large OI shifts don't change score, just alert


def generate_signal(
    analysis: dict,
    prev_analysis: Optional[dict] = None,
) -> dict:
    """
    Generate a trading signal from an analysis snapshot.

    Parameters
    ----------
    analysis      : dict  — Current cycle analysis from analyzer.analyze_csv()
    prev_analysis : dict  — Previous cycle analysis (for delta comparison).
                            Pass None if no previous snapshot is available.

    Returns
    -------
    dict with keys:
        signal   : str   — "STRONG BULLISH" / "BULLISH" / "NEUTRAL" /
                           "BEARISH" / "STRONG BEARISH"
        score    : int   — Raw numeric score
        reasons  : list  — Human-readable explanations
        alerts   : list  — Noteworthy events (large OI shifts, etc.)
        summary  : str   — Single-line description for logging / Telegram
    """
    score: int = 0
    reasons: list[str] = []
    alerts:  list[str] = []

    pcr         = analysis.get("pcr_overall", 0)
    pcr_window  = analysis.get("pcr_window",  0)
    spot        = analysis.get("spot_price",  0)
    atm         = analysis.get("atm_strike",  0)
    resistance  = analysis.get("max_call_oi_strike", 0)
    support     = analysis.get("max_put_oi_strike",  0)
    oi_summary  = analysis.get("oi_change_summary",  {})
    win_call_ch = analysis.get("window_call_oi_change", 0)
    win_put_ch  = analysis.get("window_put_oi_change",  0)
    total_call  = analysis.get("total_call_oi", 0)
    total_put   = analysis.get("total_put_oi",  0)

    # ── Rule 1: Overall PCR ───────────────────────────────────────────────────
    if pcr > 1.20:
        score += _PCR_STRONG_BULL
        reasons.append(
            f"🟢 Strong bullish sentiment — Overall PCR is very high ({pcr:.2f} > 1.20). "
            f"Huge put writing across chain."
        )
    elif pcr > PCR_BULLISH_THRESHOLD:
        score += _PCR_BULL
        reasons.append(
            f"🟢 Bullish bias — PCR {pcr:.2f} > {PCR_BULLISH_THRESHOLD}. "
            f"Put writers dominating."
        )
    elif pcr < 0.80:
        score += _PCR_STRONG_BEAR
        reasons.append(
            f"🔴 Strong bearish sentiment — PCR very low ({pcr:.2f} < 0.80). "
            f"Heavy call writing."
        )
    elif pcr < PCR_BEARISH_THRESHOLD:
        score += _PCR_BEAR
        reasons.append(
            f"🔴 Bearish bias — PCR {pcr:.2f} < {PCR_BEARISH_THRESHOLD}. "
            f"Call writers dominating."
        )
    else:
        reasons.append(
            f"⚪ PCR neutral ({pcr:.2f}) — No clear directional bias from PCR."
        )

    # ── Rule 2: ATM-window PCR ────────────────────────────────────────────────
    if pcr_window > 1.10:
        score += 1
        reasons.append(
            f"🟢 ATM-window PCR bullish ({pcr_window:.2f}) — "
            f"Put writing concentrated near ATM ({atm:.0f})."
        )
    elif pcr_window < 0.90:
        score -= 1
        reasons.append(
            f"🔴 ATM-window PCR bearish ({pcr_window:.2f}) — "
            f"Call writing concentrated near ATM ({atm:.0f})."
        )

    # ── Rule 3: ATM-window OI change direction ────────────────────────────────
    if win_put_ch > 0 and win_call_ch <= 0:
        score += _PUT_WRITE_BULL
        reasons.append(
            f"🟢 Put buildup near ATM — Put OI rising (+{win_put_ch:,.0f}) "
            f"while Call OI flat/falling. Bullish buildup detected."
        )
    elif win_call_ch > 0 and win_put_ch <= 0:
        score += _CALL_WRITE_BEAR
        reasons.append(
            f"🔴 Call buildup near ATM — Call OI rising (+{win_call_ch:,.0f}) "
            f"while Put OI flat/falling. Bearish buildup detected."
        )
    elif win_put_ch < 0 and win_call_ch >= 0:
        score += _PUT_UNWIND_BEAR
        reasons.append(
            f"🔴 Put unwinding near ATM — Put OI falling ({win_put_ch:,.0f}). "
            f"Bulls exiting. Bearish pressure."
        )
    elif win_call_ch < 0 and win_put_ch >= 0:
        score += _CALL_UNWIND_BULL
        reasons.append(
            f"🟢 Call unwinding near ATM — Call OI falling ({win_call_ch:,.0f}). "
            f"Bears covering shorts. Bullish pressure."
        )

    # ── Rule 4: Chain-wide OI change counts ───────────────────────────────────
    cw = oi_summary.get("call_writing",   0)
    cu = oi_summary.get("call_unwinding", 0)
    pw = oi_summary.get("put_writing",    0)
    pu = oi_summary.get("put_unwinding",  0)

    net_call = cw - cu   # +ve = more call writing (bearish)
    net_put  = pw - pu   # +ve = more put writing (bullish)

    if net_put > 3 and net_call < 0:
        score += 1
        reasons.append(
            f"🟢 Chain-wide put writing dominant ({pw} strikes) with call unwinding "
            f"({cu} strikes). Broad bullish structure."
        )
    elif net_call > 3 and net_put < 0:
        score -= 1
        reasons.append(
            f"🔴 Chain-wide call writing dominant ({cw} strikes) with put unwinding "
            f"({pu} strikes). Broad bearish structure."
        )

    # ── Rule 5: Support / Resistance proximity ────────────────────────────────
    if spot > 0 and resistance > 0:
        dist_to_resistance = ((resistance - spot) / spot) * 100
        if dist_to_resistance < 0.5:
            score -= 1
            reasons.append(
                f"⚠️  Spot ({spot:.0f}) is very close to resistance ({resistance:.0f}). "
                f"Strong call wall overhead. Upside limited short-term."
            )
            alerts.append(
                f"RESISTANCE ALERT: Spot {spot:.0f} ≈ Max Call OI at {resistance:.0f}"
            )
        elif dist_to_resistance > 2.0:
            reasons.append(
                f"ℹ️  Resistance ({resistance:.0f}) is {dist_to_resistance:.1f}% above spot. "
                f"Room to move upward."
            )

    if spot > 0 and support > 0:
        dist_to_support = ((spot - support) / spot) * 100
        if dist_to_support < 0.5:
            score += 1
            reasons.append(
                f"⚠️  Spot ({spot:.0f}) is very close to support ({support:.0f}). "
                f"Strong put wall below. Downside likely limited short-term."
            )
            alerts.append(
                f"SUPPORT ALERT: Spot {spot:.0f} ≈ Max Put OI at {support:.0f}"
            )
        elif dist_to_support > 2.0:
            reasons.append(
                f"ℹ️  Support ({support:.0f}) is {dist_to_support:.1f}% below spot. "
                f"Room to fall before a put wall cushion."
            )

    # ── Rule 6: Previous snapshot delta (OI velocity) ────────────────────────
    if prev_analysis is not None:
        prev_pcr   = prev_analysis.get("pcr_overall", pcr)
        delta_pcr  = pcr - prev_pcr

        if delta_pcr > 0.10:
            score += 1
            reasons.append(
                f"🟢 PCR rising quickly (Δ+{delta_pcr:.2f} since last cycle). "
                f"Accelerating bullish momentum."
            )
        elif delta_pcr < -0.10:
            score -= 1
            reasons.append(
                f"🔴 PCR falling quickly (Δ{delta_pcr:.2f} since last cycle). "
                f"Accelerating bearish momentum."
            )

        prev_total_call = prev_analysis.get("total_call_oi", total_call)
        prev_total_put  = prev_analysis.get("total_put_oi",  total_put)
        delta_call_oi   = total_call - prev_total_call
        delta_put_oi    = total_put  - prev_total_put

        if abs(delta_call_oi) >= OI_LARGE_SHIFT_LOTS:
            direction = "rising" if delta_call_oi > 0 else "falling"
            alerts.append(
                f"⚡ LARGE OI SHIFT: Total Call OI {direction} by "
                f"{abs(delta_call_oi):,} contracts since last cycle."
            )
        if abs(delta_put_oi) >= OI_LARGE_SHIFT_LOTS:
            direction = "rising" if delta_put_oi > 0 else "falling"
            alerts.append(
                f"⚡ LARGE OI SHIFT: Total Put OI {direction} by "
                f"{abs(delta_put_oi):,} contracts since last cycle."
            )

    # ── Convert score → label ─────────────────────────────────────────────────
    if score >= 3:
        signal = "STRONG BULLISH"
        emoji  = "🚀"
    elif score >= 1:
        signal = "BULLISH"
        emoji  = "🟢"
    elif score <= -3:
        signal = "STRONG BEARISH"
        emoji  = "🔻"
    elif score <= -1:
        signal = "BEARISH"
        emoji  = "🔴"
    else:
        signal = "NEUTRAL"
        emoji  = "⚪"

    summary = (
        f"{emoji} {signal} | PCR: {pcr:.2f} | ATM: {atm:.0f} | "
        f"Spot: {spot:.0f} | Resistance: {resistance:.0f} | Support: {support:.0f} | "
        f"Score: {score:+d}"
    )

    logger.info("Signal generated: %s (score=%d)", signal, score)

    return {
        "signal":     signal,
        "score":      score,
        "reasons":    reasons,
        "alerts":     alerts,
        "summary":    summary,
        # Pass-through useful fields for logging
        "pcr":        pcr,
        "atm_strike": atm,
        "spot_price": spot,
        "resistance": resistance,
        "support":    support,
    }


# ─── CLI test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys, glob, asyncio, logging
    from pathlib import Path
    from analyzer import analyze_csv
    from config import DOWNLOAD_DIR

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    csvs = sorted(glob.glob(str(Path(DOWNLOAD_DIR) / "*.csv")))
    if not csvs:
        print("No CSVs in downloads/. Run downloader.py first.")
        sys.exit(1)

    analysis = analyze_csv(csvs[-1])
    if not analysis:
        print("Analysis failed.")
        sys.exit(1)

    sig = generate_signal(analysis)
    print("\n" + "=" * 70)
    print(f"  {sig['summary']}")
    print("=" * 70)
    for r in sig["reasons"]:
        print(f"  {r}")
    if sig["alerts"]:
        print("\n  ALERTS:")
        for a in sig["alerts"]:
            print(f"    ⚡ {a}")
    print("=" * 70)
