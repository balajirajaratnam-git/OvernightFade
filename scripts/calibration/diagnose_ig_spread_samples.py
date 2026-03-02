"""
Diagnose IG spread sample data to validate calibration inputs.

Produces a detailed breakdown of which rows contributed to the calibration,
with special focus on ATM CALL spreads in the ENTRY window. Helps identify
whether the calibrated half-spread is trustworthy or inflated by mislabelled
strikes, wrong option types, non-comparable expiries, or low-liquidity
timestamps.
"""

import argparse
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

TZ_UK = ZoneInfo("Europe/London")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Diagnose IG spread samples for calibration validation."
    )
    parser.add_argument(
        "--input",
        default="data/ig_spread_samples.csv",
        help="Path to IG spread samples CSV",
    )
    parser.add_argument(
        "--entry-hour-uk",
        type=int,
        default=21,
        help="Centre hour (UK) for ENTRY window (default: 21)",
    )
    parser.add_argument(
        "--entry-minute-window",
        type=int,
        default=15,
        help="Minutes either side of entry hour (default: 15)",
    )
    parser.add_argument(
        "--exit-hour-uk",
        type=int,
        default=14,
        help="Centre hour (UK) for EXIT window (default: 14)",
    )
    parser.add_argument(
        "--exit-minute-window",
        type=int,
        default=15,
        help="Minutes either side of exit hour (default: 15)",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _in_window(ts_uk, centre_hour, minute_window):
    ts_mins = ts_uk.hour * 60 + ts_uk.minute
    centre_mins = centre_hour * 60
    return abs(ts_mins - centre_mins) <= minute_window


def classify_time_bucket(ts_uk, entry_hour, entry_window, exit_hour, exit_window):
    if _in_window(ts_uk, entry_hour, entry_window):
        return "ENTRY"
    if _in_window(ts_uk, exit_hour, exit_window):
        return "EXIT"
    return "OTHER"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}")
        sys.exit(1)

    df = pd.read_csv(input_path)
    print(f"Loaded {len(df)} rows from {input_path}")

    # Parse timestamps, convert to UK
    df["ts"] = pd.to_datetime(df["timestamp"], utc=True)
    df["ts_uk"] = df["ts"].dt.tz_convert(TZ_UK)
    df["hour_uk"] = df["ts_uk"].dt.hour
    df["minute_uk"] = df["ts_uk"].dt.minute

    # Classify time buckets
    df["time_bucket"] = df["ts_uk"].apply(
        lambda ts: classify_time_bucket(
            ts, args.entry_hour_uk, args.entry_minute_window,
            args.exit_hour_uk, args.exit_minute_window,
        )
    )

    # ======================================================================
    # Build report lines
    # ======================================================================
    lines = []
    lines.append("=" * 78)
    lines.append("IG Spread Samples — Calibration Diagnostics")
    lines.append("=" * 78)
    lines.append(f"Input:         {input_path}")
    lines.append(f"Total rows:    {len(df)}")
    lines.append(f"ENTRY window:  {args.entry_hour_uk:02d}:00 UK +/- {args.entry_minute_window} min")
    lines.append(f"EXIT  window:  {args.exit_hour_uk:02d}:00 UK +/- {args.exit_minute_window} min")
    lines.append("")

    # ------------------------------------------------------------------
    # Section 1: Overall counts
    # ------------------------------------------------------------------
    lines.append("-" * 78)
    lines.append("1. ROW COUNTS BY TIME BUCKET")
    lines.append("-" * 78)
    for tb in ["ENTRY", "EXIT", "OTHER"]:
        cnt = (df["time_bucket"] == tb).sum()
        lines.append(f"  {tb:8s}: {cnt:5d}")
    lines.append(f"  {'TOTAL':8s}: {len(df):5d}")
    lines.append("")

    exit_count = (df["time_bucket"] == "EXIT").sum()
    if exit_count == 0:
        lines.append("  ** WARNING: Zero EXIT-window rows. Exit spreads cannot be")
        lines.append("     calibrated independently; ENTRY values were reused. **")
        lines.append("")

    # ------------------------------------------------------------------
    # Section 2: ENTRY rows breakdown
    # ------------------------------------------------------------------
    entry = df[df["time_bucket"] == "ENTRY"].copy()
    lines.append("-" * 78)
    lines.append("2. ENTRY WINDOW ROWS — BREAKDOWN")
    lines.append("-" * 78)
    lines.append(f"  Total ENTRY rows: {len(entry)}")
    lines.append("")

    # By market_status
    lines.append("  By market_status:")
    for ms, cnt in entry["market_status"].value_counts().items():
        lines.append(f"    {ms:15s}: {cnt:5d}")
    lines.append("")

    # By expiry_pattern
    lines.append("  By expiry_pattern:")
    for ep, cnt in entry["expiry_pattern"].value_counts().sort_index().items():
        lines.append(f"    {ep:15s}: {cnt:5d}")
    lines.append("")

    # By option_type
    lines.append("  By option_type:")
    for ot, cnt in entry["option_type"].value_counts().sort_index().items():
        lines.append(f"    {ot:15s}: {cnt:5d}")
    lines.append("")

    # By strike_type
    lines.append("  By strike_type:")
    for st, cnt in entry["strike_type"].value_counts().sort_index().items():
        lines.append(f"    {st:15s}: {cnt:5d}")
    lines.append("")

    # Cross-tab: strike_type x expiry_pattern
    lines.append("  Cross-tab: strike_type x expiry_pattern (ENTRY only):")
    xtab = pd.crosstab(entry["strike_type"], entry["expiry_pattern"])
    lines.append("  " + xtab.to_string().replace("\n", "\n  "))
    lines.append("")

    # Cross-tab: strike_type x option_type
    lines.append("  Cross-tab: strike_type x option_type (ENTRY only):")
    xtab2 = pd.crosstab(entry["strike_type"], entry["option_type"])
    lines.append("  " + xtab2.to_string().replace("\n", "\n  "))
    lines.append("")

    # ------------------------------------------------------------------
    # Section 3: ATM CALL ENTRY deep dive (the critical subset)
    # ------------------------------------------------------------------
    lines.append("-" * 78)
    lines.append("3. ATM CALL — ENTRY WINDOW DEEP DIVE")
    lines.append("-" * 78)

    atm_call_entry = entry[
        (entry["strike_type"] == "ATM") & (entry["option_type"] == "CALL")
    ].copy()

    # Also filter to TRADEABLE
    atm_call_entry_trd = atm_call_entry[
        atm_call_entry["market_status"] == "TRADEABLE"
    ].copy()

    lines.append(f"  ATM CALL ENTRY rows (all):        {len(atm_call_entry)}")
    lines.append(f"  ATM CALL ENTRY rows (TRADEABLE):  {len(atm_call_entry_trd)}")
    lines.append("")

    if len(atm_call_entry_trd) > 0:
        sub = atm_call_entry_trd

        # Median stats
        lines.append("  Summary stats (TRADEABLE ATM CALL ENTRY):")
        for col in ["bid", "ask", "mid", "spread_pts", "half_spread_pts", "spread_pct"]:
            med = sub[col].median()
            mn = sub[col].min()
            mx = sub[col].max()
            mean = sub[col].mean()
            lines.append(f"    {col:20s}: median={med:8.4f}  mean={mean:8.4f}  min={mn:8.4f}  max={mx:8.4f}")
        lines.append("")

        # By expiry_pattern
        lines.append("  By expiry_pattern (TRADEABLE ATM CALL ENTRY):")
        for ep in sorted(sub["expiry_pattern"].unique()):
            ep_sub = sub[sub["expiry_pattern"] == ep]
            med_hs = ep_sub["half_spread_pts"].median()
            med_sp = ep_sub["spread_pts"].median()
            med_mid = ep_sub["mid"].median()
            lines.append(
                f"    {ep:12s}: n={len(ep_sub):3d}  "
                f"median_half_spread={med_hs:.4f}  "
                f"median_spread={med_sp:.4f}  "
                f"median_mid={med_mid:.2f}"
            )
        lines.append("")

        # Moneyness check: how far is the ATM strike from underlying?
        sub = sub.copy()
        sub["moneyness_pct"] = (
            (sub["strike"] - sub["underlying_mid"]) / sub["underlying_mid"] * 100
        )
        lines.append("  Moneyness of ATM strikes (strike vs underlying_mid):")
        lines.append(f"    median: {sub['moneyness_pct'].median():+.3f}%")
        lines.append(f"    min:    {sub['moneyness_pct'].min():+.3f}%")
        lines.append(f"    max:    {sub['moneyness_pct'].max():+.3f}%")
        lines.append(f"    (Negative = ITM for CALL, Positive = OTM for CALL)")
        lines.append("")

        # Top 10 widest spreads
        lines.append("  Top 10 widest spreads (TRADEABLE ATM CALL ENTRY):")
        top10 = sub.nlargest(10, "spread_pts")
        lines.append(
            f"    {'timestamp':>28s}  {'expiry':>8s}  "
            f"{'bid':>8s}  {'ask':>8s}  {'mid':>8s}  "
            f"{'spread':>7s}  {'half_sp':>7s}  {'sp_pct':>7s}"
        )
        lines.append(f"    {'-'*28}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*7}  {'-'*7}  {'-'*7}")
        for _, row in top10.iterrows():
            ts_str = row["ts_uk"].strftime("%Y-%m-%d %H:%M:%S %Z")
            lines.append(
                f"    {ts_str:>28s}  {row['expiry_pattern']:>8s}  "
                f"{row['bid']:8.2f}  {row['ask']:8.2f}  {row['mid']:8.2f}  "
                f"{row['spread_pts']:7.2f}  {row['half_spread_pts']:7.2f}  "
                f"{row['spread_pct']:7.2f}"
            )
        lines.append("")

        # All ATM CALL ENTRY rows (full listing since count is small)
        lines.append("  Complete listing — ALL TRADEABLE ATM CALL ENTRY rows:")
        lines.append(
            f"    {'timestamp':>28s}  {'expiry':>8s}  {'strike':>7s}  "
            f"{'undly':>8s}  {'bid':>8s}  {'ask':>8s}  {'mid':>8s}  "
            f"{'spread':>7s}  {'half_sp':>7s}  {'moneyness':>10s}"
        )
        lines.append(
            f"    {'-'*28}  {'-'*8}  {'-'*7}  "
            f"{'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  "
            f"{'-'*7}  {'-'*7}  {'-'*10}"
        )
        for _, row in sub.sort_values("ts").iterrows():
            ts_str = row["ts_uk"].strftime("%Y-%m-%d %H:%M:%S %Z")
            lines.append(
                f"    {ts_str:>28s}  {row['expiry_pattern']:>8s}  "
                f"{row['strike']:7.0f}  {row['underlying_mid']:8.2f}  "
                f"{row['bid']:8.2f}  {row['ask']:8.2f}  {row['mid']:8.2f}  "
                f"{row['spread_pts']:7.2f}  {row['half_spread_pts']:7.2f}  "
                f"{row['moneyness_pct']:+9.3f}%"
            )
        lines.append("")
    else:
        lines.append("  ** No TRADEABLE ATM CALL rows in ENTRY window! **")
        lines.append("")

    # ------------------------------------------------------------------
    # Section 4: ATM PUT ENTRY (for comparison)
    # ------------------------------------------------------------------
    lines.append("-" * 78)
    lines.append("4. ATM PUT — ENTRY WINDOW (for comparison)")
    lines.append("-" * 78)

    atm_put_entry = entry[
        (entry["strike_type"] == "ATM")
        & (entry["option_type"] == "PUT")
        & (entry["market_status"] == "TRADEABLE")
    ].copy()

    lines.append(f"  ATM PUT ENTRY rows (TRADEABLE): {len(atm_put_entry)}")
    if len(atm_put_entry) > 0:
        for col in ["bid", "ask", "mid", "spread_pts", "half_spread_pts"]:
            med = atm_put_entry[col].median()
            lines.append(f"    {col:20s}: median={med:8.4f}")
        lines.append("")

        lines.append("  By expiry_pattern:")
        for ep in sorted(atm_put_entry["expiry_pattern"].unique()):
            ep_sub = atm_put_entry[atm_put_entry["expiry_pattern"] == ep]
            med_hs = ep_sub["half_spread_pts"].median()
            lines.append(f"    {ep:12s}: n={len(ep_sub):3d}  median_half_spread={med_hs:.4f}")
        lines.append("")

    # ------------------------------------------------------------------
    # Section 5: The key question — what drove the 0.75 median?
    # ------------------------------------------------------------------
    lines.append("-" * 78)
    lines.append("5. CALIBRATION DRIVER ANALYSIS")
    lines.append("-" * 78)

    # All TRADEABLE ENTRY rows for ATM (both CALL and PUT)
    atm_entry_all = entry[
        (entry["strike_type"] == "ATM") & (entry["market_status"] == "TRADEABLE")
    ]

    lines.append(f"  All TRADEABLE ATM ENTRY rows: {len(atm_entry_all)}")
    lines.append("")

    if len(atm_entry_all) > 0:
        # Count by expiry_pattern
        lines.append("  Distribution of half_spread_pts values:")
        hs_counts = atm_entry_all["half_spread_pts"].value_counts().sort_index()
        for val, cnt in hs_counts.items():
            pct = cnt / len(atm_entry_all) * 100
            lines.append(f"    half_spread_pts = {val:.4f}: {cnt:3d} rows ({pct:.1f}%)")
        lines.append("")

        overall_median = atm_entry_all["half_spread_pts"].median()
        lines.append(f"  Overall ATM ENTRY median half_spread_pts: {overall_median:.4f}")
        lines.append("")

        # Explain the median
        n_spxwed = len(atm_entry_all[atm_entry_all["expiry_pattern"] == "SPXWED"])
        n_other = len(atm_entry_all) - n_spxwed
        lines.append(f"  SPXWED (weekly, nearest expiry): {n_spxwed} rows -> half_spread = 0.60 pts")
        lines.append(f"  SPXEMO + SPXEOM (longer-dated):  {n_other} rows -> half_spread = 0.75 pts")
        lines.append("")

        if n_other > n_spxwed:
            lines.append("  ** The 0.75 median is driven by SPXEMO/SPXEOM outnumbering SPXWED. **")
            lines.append("  ** For the weekly overnight strategy (1-2 DTE), the SPXWED spread")
            lines.append("     of 0.60 pts half-spread (1.20 pts roundtrip) may be more relevant. **")
        elif n_spxwed > n_other:
            lines.append("  SPXWED rows dominate -> median reflects weekly option spreads.")
        else:
            lines.append("  Equal split -> median is at the boundary (0.60 or 0.75).")
        lines.append("")

        # SPXWED-only ATM CALL stats
        spxwed_atm_call = atm_call_entry_trd[
            atm_call_entry_trd["expiry_pattern"] == "SPXWED"
        ] if len(atm_call_entry_trd) > 0 else pd.DataFrame()

        if len(spxwed_atm_call) > 0:
            lines.append("  SPXWED ATM CALL ENTRY stats (weekly options only):")
            lines.append(f"    n = {len(spxwed_atm_call)}")
            lines.append(f"    median half_spread_pts = {spxwed_atm_call['half_spread_pts'].median():.4f}")
            lines.append(f"    median spread_pts      = {spxwed_atm_call['spread_pts'].median():.4f}")
            lines.append(f"    median mid             = {spxwed_atm_call['mid'].median():.2f}")
            lines.append(f"    median spread_pct      = {spxwed_atm_call['spread_pct'].median():.2f}%")
            lines.append("")

    # ------------------------------------------------------------------
    # Section 6: Verdict
    # ------------------------------------------------------------------
    lines.append("-" * 78)
    lines.append("6. VERDICT")
    lines.append("-" * 78)
    lines.append("")

    if len(atm_entry_all) > 0:
        overall_median = atm_entry_all["half_spread_pts"].median()
        spxwed_only = atm_entry_all[atm_entry_all["expiry_pattern"] == "SPXWED"]
        spxwed_median = spxwed_only["half_spread_pts"].median() if len(spxwed_only) > 0 else None

        lines.append(f"  Calibrated ATM half_spread (all expiries):  {overall_median:.4f} pts")
        if spxwed_median is not None:
            lines.append(f"  SPXWED-only ATM half_spread (weekly):        {spxwed_median:.4f} pts")
            lines.append(f"  Roundtrip cost (all expiries):               {overall_median * 2:.4f} pts")
            lines.append(f"  Roundtrip cost (SPXWED only):                {spxwed_median * 2:.4f} pts")
        lines.append("")

        lines.append("  The calibrated 0.75 half-spread includes SPXEMO and SPXEOM")
        lines.append("  (2-5 DTE and monthly) which have wider IG spreads (1.5 pts)")
        lines.append("  than SPXWED (1.2 pts). For the overnight strategy using")
        lines.append("  weekly options, the SPXWED spread is the more appropriate")
        lines.append("  benchmark.")
        lines.append("")
        if spxwed_median is not None:
            rt_weekly = spxwed_median * 2
            lines.append(f"  Even at the SPXWED rate ({rt_weekly:.2f} pts roundtrip),")
            lines.append(f"  the cost is likely too high for the ~+3.8% gross edge")
            lines.append(f"  (avg premium ~1.6-2.5 pts, so {rt_weekly:.2f} RT = ")
            lines.append(f"  ~{rt_weekly / 2.0 * 100:.0f}% of a 2.0 pt premium).")
    lines.append("")
    lines.append("=" * 78)
    lines.append("END OF DIAGNOSTICS")
    lines.append("=" * 78)

    # ======================================================================
    # Write outputs
    # ======================================================================
    out_dir = Path("calibration")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Summary text
    summary_path = out_dir / "ig_spread_diagnostics_summary.txt"
    summary_text = "\n".join(lines) + "\n"
    with open(summary_path, "w") as f:
        f.write(summary_text)
    print(f"\nDiagnostics summary: {summary_path}")

    # Also print to console
    print()
    print(summary_text)

    # Detailed CSV of all ENTRY rows
    csv_path = out_dir / "ig_spread_diagnostics.csv"
    if len(entry) > 0:
        csv_cols = [
            "timestamp", "ts_uk", "hour_uk", "minute_uk", "time_bucket",
            "market_status", "day_of_week", "expiry_pattern", "expiry_date",
            "strike", "option_type", "strike_type",
            "underlying_bid", "underlying_ask", "underlying_mid",
            "bid", "ask", "mid", "spread_pts", "half_spread_pts", "spread_pct",
        ]
        # Include all rows (not just ENTRY) for full context
        export_df = df[[c for c in csv_cols if c in df.columns]].copy()
        export_df.to_csv(csv_path, index=False)
        print(f"Detailed CSV: {csv_path} ({len(export_df)} rows)")
    else:
        print("WARNING: No ENTRY rows to export.")


if __name__ == "__main__":
    main()
