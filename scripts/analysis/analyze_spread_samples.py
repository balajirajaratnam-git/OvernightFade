"""
Step 6A / Step 7: Analyze IG spread samples and calibrate cost model.

Two input formats are supported:

  --source manual   (default) reads data/spread_samples.csv
                    manually filled template with columns:
                    Date, Time_UK, Expiry, Strike_Type, Strike, Buy_Price, Sell_Price

  --source auto     reads data/ig_spread_samples.csv
                    collected by scripts/data/collect_ig_spreads.py; columns:
                    timestamp, source, underlying_mid, strike_type, expiry_pattern,
                    strike, bid, ask, mid, spread_pts, spread_pct, half_spread_pts

  --source <path>   reads any CSV at the given path (manual format assumed)

Produces:
  1. Per-sample: mid, spread in points, spread as % of mid
  2. By strike type: median spread (pts), median spread (%), P90 spread
  3. Calibrated cost model parameters for the backtest
  4. Re-runs ATM backtest with calibrated costs and reports EV

Usage:
  python scripts/analysis/analyze_spread_samples.py
  python scripts/analysis/analyze_spread_samples.py --source auto
  python scripts/analysis/analyze_spread_samples.py --source manual
"""
import sys
import argparse
sys.path.insert(0, 'src')

import pandas as pd
import numpy as np
from rich.console import Console
from rich.table import Table

console = Console()


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_manual(path='data/spread_samples.csv'):
    """
    Load manually-filled spread samples.

    Expected columns: Date, Strike_Type, Buy_Price, Sell_Price
    Optional: Time_UK, Expiry, Strike, Underlying_Price
    """
    df = pd.read_csv(path)

    required = ['Date', 'Strike_Type', 'Buy_Price', 'Sell_Price']
    for col in required:
        if col not in df.columns:
            console.print(f"[red]Missing column: {col}[/red]")
            return None

    df = df.dropna(subset=['Buy_Price', 'Sell_Price'])

    if len(df) == 0:
        console.print("[red]No valid rows with Buy/Sell prices[/red]")
        return None

    df['Mid'] = (df['Buy_Price'] + df['Sell_Price']) / 2
    df['Spread_Pts'] = df['Buy_Price'] - df['Sell_Price']
    df['Half_Spread_Pts'] = df['Spread_Pts'] / 2
    df['Spread_Pct'] = df['Spread_Pts'] / df['Mid'] * 100

    return df


def load_auto(path='data/ig_spread_samples.csv'):
    """
    Load automated spread samples collected by collect_ig_spreads.py.

    Expected columns: timestamp, source, underlying_mid, strike_type,
                      expiry_pattern, strike, bid, ask, mid,
                      spread_pts, spread_pct, half_spread_pts
    """
    df = pd.read_csv(path)

    auto_cols = ['strike_type', 'bid', 'ask', 'mid', 'spread_pts', 'half_spread_pts', 'spread_pct']
    for col in auto_cols:
        if col not in df.columns:
            console.print(f"[red]Auto CSV missing column: {col}[/red]")
            return None

    df = df.dropna(subset=['bid', 'ask'])

    if len(df) == 0:
        console.print("[red]No valid rows in auto CSV[/red]")
        return None

    # Normalise to a common schema matching the manual format
    df_norm = pd.DataFrame()
    df_norm['Date'] = df.get('timestamp', pd.Series([''] * len(df))).astype(str).str[:10]
    df_norm['Time_UK'] = df.get('timestamp', pd.Series([''] * len(df))).astype(str).str[11:16]
    df_norm['Expiry'] = df.get('expiry_pattern', pd.Series([''] * len(df)))
    df_norm['Strike_Type'] = df['strike_type']
    df_norm['Strike'] = df.get('strike', pd.Series([None] * len(df)))
    df_norm['Buy_Price'] = df['ask']   # IG: you BUY at the ASK
    df_norm['Sell_Price'] = df['bid']  # IG: you SELL at the BID
    df_norm['Underlying_Price'] = df.get('underlying_mid', pd.Series([None] * len(df)))

    df_norm['Mid'] = df['mid']
    df_norm['Spread_Pts'] = df['spread_pts']
    df_norm['Half_Spread_Pts'] = df['half_spread_pts']
    df_norm['Spread_Pct'] = df['spread_pct']

    return df_norm


def load_and_validate(path, source):
    """
    Dispatch to the right loader based on source mode.

    Args:
        path: CSV file path
        source: 'auto' or 'manual'

    Returns:
        DataFrame with normalised schema, or None on error.
    """
    if source == 'auto':
        return load_auto(path)
    return load_manual(path)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def display_samples(df):
    """Show all samples."""
    table = Table(title="Raw Spread Samples", show_header=True, header_style="bold cyan")
    table.add_column("Date", width=12)
    table.add_column("Time", width=8)
    table.add_column("Expiry", width=10)
    table.add_column("Strike Type", width=10)
    table.add_column("Strike", justify="right", width=8)
    table.add_column("Buy", justify="right", width=8)
    table.add_column("Sell", justify="right", width=8)
    table.add_column("Mid", justify="right", width=8)
    table.add_column("Spread pts", justify="right", width=10)
    table.add_column("Spread %", justify="right", width=10)

    for _, row in df.iterrows():
        table.add_row(
            str(row.get('Date', '')),
            str(row.get('Time_UK', '')),
            str(row.get('Expiry', '')),
            str(row.get('Strike_Type', '')),
            str(row.get('Strike', '')),
            f"{row['Buy_Price']:.2f}",
            f"{row['Sell_Price']:.2f}",
            f"{row['Mid']:.2f}",
            f"{row['Spread_Pts']:.3f}",
            f"{row['Spread_Pct']:.1f}%",
        )

    console.print(table)
    console.print()


def display_summary(df):
    """Summary by strike type."""
    console.print("[bold]Summary by Strike Type[/bold]")
    console.print()

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Strike Type", width=12)
    table.add_column("N", justify="right", width=4)
    table.add_column("Median Spread pts", justify="right", width=16)
    table.add_column("Median Spread %", justify="right", width=14)
    table.add_column("P90 Spread pts", justify="right", width=14)
    table.add_column("P90 Spread %", justify="right", width=12)
    table.add_column("Median Mid", justify="right", width=10)
    table.add_column("Median Half-Spread", justify="right", width=16)

    # All strike types present in this dataset (supports both manual and auto formats)
    all_types = sorted(df['Strike_Type'].dropna().unique())
    for stype in all_types:
        sub = df[df['Strike_Type'] == stype]
        if len(sub) == 0:
            continue

        med_spread = sub['Spread_Pts'].median()
        med_pct = sub['Spread_Pct'].median()
        p90_spread = sub['Spread_Pts'].quantile(0.90)
        p90_pct = sub['Spread_Pct'].quantile(0.90)
        med_mid = sub['Mid'].median()
        med_half = sub['Half_Spread_Pts'].median()

        table.add_row(
            stype,
            str(len(sub)),
            f"{med_spread:.3f}",
            f"{med_pct:.1f}%",
            f"{p90_spread:.3f}",
            f"{p90_pct:.1f}%",
            f"{med_mid:.2f}",
            f"{med_half:.3f}",
        )

    # Totals row
    med_spread = df['Spread_Pts'].median()
    med_pct = df['Spread_Pct'].median()
    p90_spread = df['Spread_Pts'].quantile(0.90)
    p90_pct = df['Spread_Pct'].quantile(0.90)
    med_mid = df['Mid'].median()
    med_half = df['Half_Spread_Pts'].median()

    table.add_row(
        "[bold]ALL[/bold]",
        f"[bold]{len(df)}[/bold]",
        f"[bold]{med_spread:.3f}[/bold]",
        f"[bold]{med_pct:.1f}%[/bold]",
        f"[bold]{p90_spread:.3f}[/bold]",
        f"[bold]{p90_pct:.1f}%[/bold]",
        f"[bold]{med_mid:.2f}[/bold]",
        f"[bold]{med_half:.3f}[/bold]",
    )

    console.print(table)
    console.print()

    # By expiry DTE
    console.print("[bold]Summary by Expiry (DTE)[/bold]")
    console.print()
    if 'Expiry' in df.columns:
        for expiry in df['Expiry'].dropna().unique():
            sub = df[df['Expiry'] == expiry]
            console.print(
                f"  {expiry}: {len(sub)} samples, "
                f"median spread = {sub['Spread_Pts'].median():.3f} pts "
                f"({sub['Spread_Pct'].median():.1f}%)"
            )
    console.print()


def suggest_cost_model(df):
    """Suggest calibrated cost model parameters."""
    console.print("=" * 80)
    console.print("[bold white]CALIBRATED COST MODEL[/bold white]")
    console.print("=" * 80)
    console.print()

    atm = df[df['Strike_Type'] == 'ATM']
    # Support both 'OTM_10' (auto format) and '0.3%OTM' (manual format)
    otm = df[df['Strike_Type'].isin(['OTM_10', '0.3%OTM'])]

    if len(atm) > 0:
        atm_half = atm['Half_Spread_Pts'].median()
        atm_half_p90 = atm['Half_Spread_Pts'].quantile(0.90)
        atm_pct = atm['Spread_Pct'].median()
        console.print("[bold]ATM:[/bold]")
        console.print(f"  Median half-spread: {atm_half:.3f} pts")
        console.print(f"  P90 half-spread:    {atm_half_p90:.3f} pts")
        console.print(f"  Median spread %:    {atm_pct:.1f}%")
        console.print()

    if len(otm) > 0:
        otm_half = otm['Half_Spread_Pts'].median()
        otm_half_p90 = otm['Half_Spread_Pts'].quantile(0.90)
        otm_pct = otm['Spread_Pct'].median()
        console.print("[bold]OTM:[/bold]")
        console.print(f"  Median half-spread: {otm_half:.3f} pts")
        console.print(f"  P90 half-spread:    {otm_half_p90:.3f} pts")
        console.print(f"  Median spread %:    {otm_pct:.1f}%")
        console.print()

    console.print("[bold]Recommended backtest parameters:[/bold]")
    console.print()

    if len(atm) > 0:
        rec_half = atm['Half_Spread_Pts'].median()
        rec_slippage = 0.02
        console.print(f"  ATM half_spread_points = {rec_half:.3f}")
        console.print(f"  ATM slippage_points    = {rec_slippage:.3f}")
        console.print(f"  ATM total one-side     = {rec_half + rec_slippage:.3f} pts")
        console.print(f"  ATM round-trip         = {(rec_half + rec_slippage) * 2:.3f} pts")
        console.print()

    if len(otm) > 0:
        rec_half = otm['Half_Spread_Pts'].median()
        rec_slippage = 0.02
        console.print(f"  OTM half_spread_points = {rec_half:.3f}")
        console.print(f"  OTM slippage_points    = {rec_slippage:.3f}")
        console.print(f"  OTM total one-side     = {rec_half + rec_slippage:.3f} pts")
        console.print(f"  OTM round-trip         = {(rec_half + rec_slippage) * 2:.3f} pts")
        console.print()

    console.print("[bold]Comparison to previous models:[/bold]")
    console.print("  Baseline pct model:   5% of premium (~0.06 pts half-spread on ATM)")
    if len(atm) > 0:
        console.print(
            f"  Real ATM median:      {atm['Spread_Pct'].median():.1f}% of premium "
            f"({atm['Half_Spread_Pts'].median():.3f} pts half-spread)"
        )
    console.print()

    console.print("[bold]Next step:[/bold]")
    console.print("  Run: python scripts/backtesting/run_backtest_bs_pricing.py --cost-model fixed")
    console.print("  with the calibrated half_spread values above.")
    console.print("  If ATM EV is still positive, the strategy is viable on IG.")
    console.print("  If ATM EV is ~0 or negative, consider signal strengthening (Step 8).")
    console.print()
    console.print("=" * 80)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Analyze IG spread samples and calibrate cost model.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/analysis/analyze_spread_samples.py
  python scripts/analysis/analyze_spread_samples.py --source auto
  python scripts/analysis/analyze_spread_samples.py --source manual
  python scripts/analysis/analyze_spread_samples.py --source /path/to/custom.csv
        """,
    )
    parser.add_argument(
        '--source',
        default='manual',
        help=(
            "Data source: 'auto' (data/ig_spread_samples.csv from collector), "
            "'manual' (data/spread_samples.csv, default), "
            "or a file path."
        ),
    )
    args = parser.parse_args()

    console.print("=" * 80)
    console.print("[bold blue]STEP 6A / STEP 7: IG Spread Sampling Analysis[/bold blue]")
    console.print("=" * 80)
    console.print()

    import os

    source = args.source

    # Resolve path and source mode
    if source == 'auto':
        path = 'data/ig_spread_samples.csv'
        mode = 'auto'
    elif source == 'manual':
        path = 'data/spread_samples.csv'
        mode = 'manual'
    else:
        # Treat as a file path (manual format assumed)
        path = source
        mode = 'manual'
        source = 'manual (custom path)'

    console.print(f"[cyan]Source mode:  {source}[/cyan]")
    console.print(f"[cyan]Data file:    {path}[/cyan]")
    console.print()

    if not os.path.exists(path):
        if mode == 'manual' and os.path.exists('data/spread_samples_template.csv'):
            console.print("[yellow]No data/spread_samples.csv found.[/yellow]")
            console.print("[yellow]Template exists at data/spread_samples_template.csv[/yellow]")
            console.print()
            console.print("[bold]Instructions:[/bold]")
            console.print("  Option A — manual collection:")
            console.print("    1. Copy template to data/spread_samples.csv")
            console.print("    2. Fill in Buy_Price, Sell_Price, Underlying_Price, Strike from IG deal ticket")
            console.print("    3. Record at ~21:00 UK for 5 trading days")
            console.print("    4. Re-run this script")
            console.print()
            console.print("  Option B — automated collection:")
            console.print("    1. Set up config/ig_api_credentials.json")
            console.print("    2. Run: python scripts/data/collect_ig_spreads.py --dry-run")
            console.print("    3. Run: python scripts/data/collect_ig_spreads.py")
            console.print("    4. Re-run: python scripts/analysis/analyze_spread_samples.py --source auto")
        elif mode == 'auto':
            console.print("[yellow]No data/ig_spread_samples.csv found.[/yellow]")
            console.print("[yellow]Run the collector first:[/yellow]")
            console.print("  python scripts/data/collect_ig_spreads.py --dry-run")
            console.print("  python scripts/data/collect_ig_spreads.py")
        else:
            console.print(f"[red]File not found: {path}[/red]")
        return

    df = load_and_validate(path, mode)

    if df is None:
        return

    console.print(f"[green]Loaded {len(df)} valid samples[/green]")
    console.print()

    display_samples(df)
    display_summary(df)
    suggest_cost_model(df)


if __name__ == "__main__":
    main()
