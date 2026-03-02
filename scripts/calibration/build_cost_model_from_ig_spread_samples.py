"""
Build a CalibratedFixedPointCostModel JSON (v2.0) from IG spread sample data.

Reads data/ig_spread_samples.csv, classifies rows into ENTRY/EXIT/OTHER
time buckets, computes half-spread statistics nested by:
  expiry_pattern -> time_bucket -> strike_type

Writes a v2.0 calibration JSON compatible with CalibratedFixedPointCostModel,
plus a human-readable report file.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

TZ_UK = ZoneInfo("Europe/London")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Build v2.0 cost calibration JSON from IG spread samples."
    )
    parser.add_argument(
        "--input",
        default="data/ig_spread_samples.csv",
        help="Path to IG spread samples CSV (default: data/ig_spread_samples.csv)",
    )
    parser.add_argument(
        "--output",
        default="calibration/cost_model_ig.json",
        help="Path for output calibration JSON (default: calibration/cost_model_ig.json)",
    )
    parser.add_argument(
        "--stat",
        choices=["median", "p75", "p90"],
        default="median",
        help="Summary statistic for half-spread (default: median)",
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
        help="Minutes either side of entry hour for ENTRY window (default: 15)",
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
        help="Minutes either side of exit hour for EXIT window (default: 15)",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Time bucket classification
# ---------------------------------------------------------------------------

def _in_window(ts_uk, centre_hour, minute_window):
    """
    Check if a UK-localised datetime falls within [centre_hour:00 - minute_window,
    centre_hour:00 + minute_window].
    """
    ts_mins = ts_uk.hour * 60 + ts_uk.minute
    centre_mins = centre_hour * 60
    return abs(ts_mins - centre_mins) <= minute_window


def classify_time_bucket(ts_uk, entry_hour, entry_window, exit_hour, exit_window):
    """Classify a UK timestamp into ENTRY / EXIT / OTHER."""
    if _in_window(ts_uk, entry_hour, entry_window):
        return "ENTRY"
    if _in_window(ts_uk, exit_hour, exit_window):
        return "EXIT"
    return "OTHER"


# ---------------------------------------------------------------------------
# Stat computation
# ---------------------------------------------------------------------------

def compute_stat(series, stat_name):
    """Compute the chosen summary statistic on a pandas Series."""
    if stat_name == "median":
        return series.median()
    elif stat_name == "p75":
        return series.quantile(0.75)
    elif stat_name == "p90":
        return series.quantile(0.90)
    else:
        raise ValueError(f"Unknown stat: {stat_name}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    # ------------------------------------------------------------------
    # 1. Read CSV
    # ------------------------------------------------------------------
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}")
        sys.exit(1)

    df = pd.read_csv(input_path)
    print(f"Loaded {len(df)} rows from {input_path}")

    # ------------------------------------------------------------------
    # 2. Parse timestamps and convert to UK time
    # ------------------------------------------------------------------
    df["ts"] = pd.to_datetime(df["timestamp"], utc=True)
    df["ts_uk"] = df["ts"].dt.tz_convert(TZ_UK)

    # ------------------------------------------------------------------
    # 3. Filter: keep only TRADEABLE rows
    # ------------------------------------------------------------------
    pre_filter = len(df)
    df = df[df["market_status"] == "TRADEABLE"].copy()
    print(f"Filtered to TRADEABLE: {pre_filter} -> {len(df)} rows")

    if df.empty:
        print("ERROR: No TRADEABLE rows found.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 4. Assign time_bucket
    # ------------------------------------------------------------------
    df["time_bucket"] = df["ts_uk"].apply(
        lambda ts: classify_time_bucket(
            ts, args.entry_hour_uk, args.entry_minute_window,
            args.exit_hour_uk, args.exit_minute_window,
        )
    )

    bucket_counts = df["time_bucket"].value_counts()
    print("Time bucket counts:")
    for b in ["ENTRY", "EXIT", "OTHER"]:
        print(f"  {b}: {bucket_counts.get(b, 0)}")

    # ------------------------------------------------------------------
    # 5. Get dimensions
    # ------------------------------------------------------------------
    expiry_patterns = sorted(df["expiry_pattern"].unique())
    strike_types = sorted(df["strike_type"].unique())
    print(f"Expiry patterns: {expiry_patterns}")
    print(f"Strike types: {strike_types}")

    stat_name = args.stat

    # ------------------------------------------------------------------
    # 6. Build spreads: expiry_pattern -> time_bucket -> strike_type
    # ------------------------------------------------------------------
    spreads = {}
    fallback_notes = []

    for ep in expiry_patterns:
        ep_data = {}
        for tb in ["ENTRY", "EXIT"]:
            tb_data = {}
            subset = df[
                (df["expiry_pattern"] == ep) & (df["time_bucket"] == tb)
            ]
            if subset.empty:
                fallback_notes.append(f"{ep}/{tb}: no samples")
                continue

            for st in strike_types:
                st_subset = subset[subset["strike_type"] == st]
                if st_subset.empty:
                    continue
                hs = compute_stat(st_subset["half_spread_pts"], stat_name)
                tb_data[st] = {
                    "half_spread_pts": round(float(hs), 4),
                    "n": len(st_subset),
                }
            if tb_data:
                ep_data[tb] = tb_data
        if ep_data:
            spreads[ep] = ep_data

    # ------------------------------------------------------------------
    # 7. Compute global_defaults from overall ENTRY median
    # ------------------------------------------------------------------
    all_entry = df[df["time_bucket"] == "ENTRY"]
    if len(all_entry) > 0:
        global_hs = compute_stat(all_entry["half_spread_pts"], stat_name)
    else:
        global_hs = compute_stat(df["half_spread_pts"], stat_name)
    global_hs = round(float(global_hs), 4)

    # ------------------------------------------------------------------
    # 8. Build v2.0 calibration JSON
    # ------------------------------------------------------------------
    notes_parts = [
        f"Built from {input_path} ({len(df)} TRADEABLE rows).",
        f"Stat: {stat_name}. Schema v2.0.",
    ]
    if fallback_notes:
        notes_parts.append(
            "Missing time buckets: " + "; ".join(fallback_notes) + "."
        )

    cal = {
        "version": "2.0",
        "provider": "IG_SPREAD_SAMPLES",
        "created_utc": datetime.now(tz=ZoneInfo("UTC")).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "notes": " ".join(notes_parts),
        "global_defaults": {
            "half_spread_pts": global_hs,
            "slippage_pts": 0.00,
        },
        "spreads": spreads,
    }

    # ------------------------------------------------------------------
    # 9. Write JSON
    # ------------------------------------------------------------------
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(cal, f, indent=2)
    print(f"\nCalibration JSON written: {out_path}")

    # ------------------------------------------------------------------
    # 10. Write human report
    # ------------------------------------------------------------------
    report_path = out_path.with_name(out_path.stem + "_report.txt")
    lines = _build_report(
        args, input_path, out_path, stat_name, df, bucket_counts,
        expiry_patterns, strike_types, spreads, global_hs, fallback_notes,
    )
    report_text = "\n".join(lines) + "\n"
    with open(report_path, "w") as f:
        f.write(report_text)
    print(f"Report written: {report_path}")


def _build_report(
    args, input_path, out_path, stat_name, df, bucket_counts,
    expiry_patterns, strike_types, spreads, global_hs, fallback_notes,
):
    """Build the human-readable report lines."""
    lines = []
    lines.append("=" * 78)
    lines.append("IG Spread Calibration Report (v2.0 schema)")
    lines.append("=" * 78)
    lines.append(f"Input:  {input_path}")
    lines.append(f"Output: {out_path}")
    lines.append(f"Stat:   {stat_name}")
    lines.append(
        f"Date:   {datetime.now(tz=ZoneInfo('UTC')).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    lines.append(
        f"ENTRY window: {args.entry_hour_uk:02d}:00 UK "
        f"+/- {args.entry_minute_window} min"
    )
    lines.append(
        f"EXIT  window: {args.exit_hour_uk:02d}:00 UK "
        f"+/- {args.exit_minute_window} min"
    )
    lines.append("")

    # Sample counts
    lines.append("-" * 78)
    lines.append("Sample counts by time_bucket")
    lines.append("-" * 78)
    for tb in ["ENTRY", "EXIT", "OTHER"]:
        cnt = bucket_counts.get(tb, 0)
        lines.append(f"  {tb:8s}: {cnt:5d} rows")
    lines.append(f"  {'TOTAL':8s}: {len(df):5d} rows")
    lines.append("")

    # Global defaults
    lines.append("-" * 78)
    lines.append("Global defaults")
    lines.append("-" * 78)
    lines.append(f"  half_spread_pts: {global_hs:.4f}")
    lines.append(f"  slippage_pts:    0.0000")
    lines.append("")

    # Per expiry_pattern x time_bucket x strike_type
    lines.append("-" * 78)
    lines.append(
        f"Spreads by expiry_pattern / time_bucket / strike_type ({stat_name})"
    )
    lines.append("-" * 78)
    for ep in expiry_patterns:
        ep_data = spreads.get(ep, {})
        lines.append(f"\n  {ep}:")
        for tb in ["ENTRY", "EXIT"]:
            tb_data = ep_data.get(tb)
            if not tb_data:
                lines.append(f"    {tb}: (no samples)")
                continue
            lines.append(f"    {tb}:")
            for st in strike_types:
                if st in tb_data:
                    node = tb_data[st]
                    lines.append(
                        f"      {st:12s}: "
                        f"half_spread={node['half_spread_pts']:.4f} pts  "
                        f"n={node['n']}"
                    )
    lines.append("")

    # Fallback notes
    if fallback_notes:
        lines.append("-" * 78)
        lines.append("WARNINGS / FALLBACK NOTES")
        lines.append("-" * 78)
        for note in fallback_notes:
            lines.append(f"  {note}")
        lines.append("")

    # Summary comparison table
    lines.append("-" * 78)
    lines.append("Quick reference: ENTRY half_spread_pts by pattern x strike")
    lines.append("-" * 78)
    header = f"  {'':12s}"
    for ep in expiry_patterns:
        header += f"  {ep:>10s}"
    lines.append(header)
    lines.append(f"  {'-'*12}" + f"  {'-'*10}" * len(expiry_patterns))
    for st in strike_types:
        row = f"  {st:12s}"
        for ep in expiry_patterns:
            ep_entry = spreads.get(ep, {}).get("ENTRY", {})
            if st in ep_entry:
                row += f"  {ep_entry[st]['half_spread_pts']:10.4f}"
            else:
                row += f"  {'---':>10s}"
        lines.append(row)
    lines.append("")

    lines.append("=" * 78)
    lines.append("END OF REPORT")
    lines.append("=" * 78)
    return lines


if __name__ == "__main__":
    main()
