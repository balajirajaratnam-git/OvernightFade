"""
Compare two backtest summary JSONs side by side.

Produces a human-readable comparison report with key metrics and a verdict
on whether the edge survives calibrated costs.
"""

import argparse
import json
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Compare two backtest summary JSONs."
    )
    parser.add_argument(
        "--a",
        required=True,
        help="Path to first summary JSON (baseline / control)",
    )
    parser.add_argument(
        "--b",
        required=True,
        help="Path to second summary JSON (test / calibrated)",
    )
    parser.add_argument(
        "--output",
        default="results/step9_comparison.txt",
        help="Path for output comparison report",
    )
    return parser.parse_args()


def _get(d, *keys, default="N/A"):
    """Safely navigate nested dict."""
    for k in keys:
        if isinstance(d, dict) and k in d:
            d = d[k]
        else:
            return default
    return d


def main():
    args = parse_args()

    # Load JSONs
    for label, path_str in [("A", args.a), ("B", args.b)]:
        if not Path(path_str).exists():
            print(f"ERROR: {label} file not found: {path_str}")
            sys.exit(1)

    with open(args.a) as f:
        a = json.load(f)
    with open(args.b) as f:
        b = json.load(f)

    # Extract metrics
    def extract(s):
        return {
            "label": Path(args.a if s is a else args.b).stem,
            "cost_model": _get(s, "run_parameters", "cost_model"),
            "cost_bucket": _get(s, "run_parameters", "cost_parameters", "bucket"),
            "trades": _get(s, "trade_counts", "total"),
            "wins": _get(s, "trade_counts", "wins"),
            "losses": _get(s, "trade_counts", "losses"),
            "wr": _get(s, "metrics", "win_rate"),
            "ev": _get(s, "metrics", "ev"),
            "avg_win": _get(s, "metrics", "avg_win"),
            "avg_loss": _get(s, "metrics", "avg_loss"),
            "p50": _get(s, "percentiles", "P50"),
            "p95": _get(s, "percentiles", "P95"),
            "p99": _get(s, "percentiles", "P99"),
            "rt_cost_pts": _get(s, "cost_audit", "avg_roundtrip_cost_pts"),
            "rt_cost_pct": _get(s, "cost_audit", "avg_roundtrip_cost_pct"),
            "entry_cost_pts": _get(s, "cost_audit", "avg_entry_cost_pts"),
            "exit_cost_pts": _get(s, "cost_audit", "avg_exit_cost_pts"),
            "calibration_file": _get(s, "run_parameters", "cost_parameters", "calibration_file"),
            "calibration_version": _get(s, "run_parameters", "cost_parameters", "calibration_version"),
        }

    ma = extract(a)
    mb = extract(b)

    # Build report
    lines = []
    lines.append("=" * 78)
    lines.append("BACKTEST COMPARISON REPORT")
    lines.append("=" * 78)
    lines.append("")
    lines.append(f"  Run A (baseline): {args.a}")
    lines.append(f"    Cost model: {ma['cost_model']}")
    lines.append(f"  Run B (test):     {args.b}")
    lines.append(f"    Cost model: {mb['cost_model']}")
    if mb["cost_bucket"] != "N/A":
        lines.append(f"    Bucket:     {mb['cost_bucket']}")
    if mb["calibration_file"] != "N/A":
        lines.append(f"    Cal file:   {mb['calibration_file']}")
    lines.append("")

    # Metric comparison table
    lines.append("-" * 78)
    lines.append("METRIC COMPARISON")
    lines.append("-" * 78)

    def fmt(val, suffix=""):
        if isinstance(val, (int,)):
            return f"{val:,}{suffix}"
        if isinstance(val, (float,)):
            return f"{val:+.4f}{suffix}" if val != 0 else f"{val:.4f}{suffix}"
        return str(val)

    def delta_str(va, vb):
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            d = vb - va
            return f"{d:+.4f}"
        return ""

    metrics = [
        ("Trades", "trades", ""),
        ("Wins", "wins", ""),
        ("Losses", "losses", ""),
        ("Win Rate (%)", "wr", "%"),
        ("EV (%)", "ev", "%"),
        ("Avg Win (%)", "avg_win", "%"),
        ("Avg Loss (%)", "avg_loss", "%"),
        ("P50 (%)", "p50", ""),
        ("P95 (%)", "p95", ""),
        ("P99 (%)", "p99", ""),
        ("Avg RT Cost (pts)", "rt_cost_pts", ""),
        ("Avg RT Cost (%)", "rt_cost_pct", "%"),
        ("Avg Entry Cost (pts)", "entry_cost_pts", ""),
        ("Avg Exit Cost (pts)", "exit_cost_pts", ""),
    ]

    lines.append(f"  {'Metric':<25s} {'Run A':>12s} {'Run B':>12s} {'Delta':>12s}")
    lines.append(f"  {'-'*25} {'-'*12} {'-'*12} {'-'*12}")

    for label, key, suffix in metrics:
        va = ma[key]
        vb = mb[key]
        va_s = fmt(va, suffix) if va != "N/A" else "N/A"
        vb_s = fmt(vb, suffix) if vb != "N/A" else "N/A"
        d_s = delta_str(va, vb)
        lines.append(f"  {label:<25s} {va_s:>12s} {vb_s:>12s} {d_s:>12s}")
    lines.append("")

    # Cost multiplier
    if isinstance(ma["rt_cost_pts"], (int, float)) and isinstance(mb["rt_cost_pts"], (int, float)):
        if ma["rt_cost_pts"] > 0:
            multiplier = mb["rt_cost_pts"] / ma["rt_cost_pts"]
            lines.append(f"  Cost multiplier (B / A): {multiplier:.1f}x")
            lines.append("")

    # Verdict
    lines.append("-" * 78)
    lines.append("VERDICT")
    lines.append("-" * 78)
    lines.append("")

    ev_a = ma["ev"] if isinstance(ma["ev"], (int, float)) else 0
    ev_b = mb["ev"] if isinstance(mb["ev"], (int, float)) else 0

    if ev_b > 0:
        verdict = "YES — edge survives calibrated costs"
    elif ev_a > 0 and ev_b <= 0:
        verdict = "NO — edge does NOT survive calibrated costs"
    elif ev_a <= 0 and ev_b <= 0:
        verdict = "NO — neither run shows positive EV"
    else:
        verdict = "INCONCLUSIVE"

    lines.append(f"  Edge survives calibrated costs: {verdict}")
    lines.append(f"  Run A EV: {ev_a:+.4f}%  |  Run B EV: {ev_b:+.4f}%")
    lines.append("")

    if ev_a > 0 and ev_b <= 0:
        ev_diff = ev_a - ev_b
        lines.append(f"  The calibrated costs erode {ev_diff:.2f}pp of EV.")
        lines.append(f"  Run A gross edge (+{ev_a:.2f}%) is consumed by the")
        lines.append(f"  higher roundtrip cost ({mb['rt_cost_pts']} pts vs {ma['rt_cost_pts']} pts).")
        lines.append("")
        lines.append("  See calibration/ig_spread_diagnostics_summary.txt for")
        lines.append("  details on which rows drove the calibrated spread values.")

    lines.append("")
    lines.append("=" * 78)
    lines.append("END OF COMPARISON")
    lines.append("=" * 78)

    # Write output
    report_text = "\n".join(lines) + "\n"

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        f.write(report_text)
    print(f"Comparison report: {out_path}")
    print()
    print(report_text)


if __name__ == "__main__":
    main()
