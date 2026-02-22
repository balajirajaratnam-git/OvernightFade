"""
Step 6A / Step 7: Analyze IG spread samples and calibrate cost model.

Reads data/spread_samples.csv (filled in manually from IG deal tickets).
Produces:
  1. Per-sample: mid, spread in points, spread as % of mid
  2. By strike type: median spread (pts), median spread (%), P90 spread
  3. Calibrated cost model parameters for the backtest
  4. Re-runs ATM backtest with calibrated costs and reports EV

Usage:
  python scripts/analysis/analyze_spread_samples.py
"""
import sys
sys.path.insert(0, 'src')

import pandas as pd
import numpy as np
from rich.console import Console
from rich.table import Table

console = Console()


def load_and_validate(path='data/spread_samples.csv'):
    """Load spread samples and compute derived fields."""
    df = pd.read_csv(path)

    # Basic validation
    required = ['Date', 'Strike_Type', 'Buy_Price', 'Sell_Price']
    for col in required:
        if col not in df.columns:
            console.print(f"[red]Missing column: {col}[/red]")
            return None

    # Drop rows with missing prices
    df = df.dropna(subset=['Buy_Price', 'Sell_Price'])

    if len(df) == 0:
        console.print("[red]No valid rows with Buy/Sell prices[/red]")
        return None

    # Compute derived fields
    df['Mid'] = (df['Buy_Price'] + df['Sell_Price']) / 2
    df['Spread_Pts'] = df['Buy_Price'] - df['Sell_Price']
    df['Half_Spread_Pts'] = df['Spread_Pts'] / 2
    df['Spread_Pct'] = df['Spread_Pts'] / df['Mid'] * 100

    return df


def display_samples(df):
    """Show all samples."""
    table = Table(title="Raw Spread Samples", show_header=True, header_style="bold cyan")
    table.add_column("Date", width=12)
    table.add_column("Time", width=8)
    table.add_column("Expiry", width=8)
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

    for stype in ['ATM', '0.3%ITM', '0.3%OTM']:
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

    # ALL
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
            console.print(f"  {expiry}: {len(sub)} samples, median spread = {sub['Spread_Pts'].median():.3f} pts ({sub['Spread_Pct'].median():.1f}%)")
    console.print()


def suggest_cost_model(df):
    """Suggest calibrated cost model parameters."""
    console.print("=" * 80)
    console.print("[bold white]CALIBRATED COST MODEL[/bold white]")
    console.print("=" * 80)
    console.print()

    atm = df[df['Strike_Type'] == 'ATM']
    otm = df[df['Strike_Type'] == '0.3%OTM']

    if len(atm) > 0:
        atm_half = atm['Half_Spread_Pts'].median()
        atm_half_p90 = atm['Half_Spread_Pts'].quantile(0.90)
        atm_pct = atm['Spread_Pct'].median()
        console.print(f"[bold]ATM:[/bold]")
        console.print(f"  Median half-spread: {atm_half:.3f} pts")
        console.print(f"  P90 half-spread:    {atm_half_p90:.3f} pts")
        console.print(f"  Median spread %:    {atm_pct:.1f}%")
        console.print()

    if len(otm) > 0:
        otm_half = otm['Half_Spread_Pts'].median()
        otm_half_p90 = otm['Half_Spread_Pts'].quantile(0.90)
        otm_pct = otm['Spread_Pct'].median()
        console.print(f"[bold]0.3% OTM:[/bold]")
        console.print(f"  Median half-spread: {otm_half:.3f} pts")
        console.print(f"  P90 half-spread:    {otm_half_p90:.3f} pts")
        console.print(f"  Median spread %:    {otm_pct:.1f}%")
        console.print()

    console.print("[bold]Recommended backtest parameters:[/bold]")
    console.print()

    if len(atm) > 0:
        # Use median + small buffer for slippage
        rec_half = atm['Half_Spread_Pts'].median()
        rec_slippage = 0.02  # small buffer
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
    console.print("  Original flat model:  5% of premium (~0.06 pts half-spread on ATM)")
    if len(atm) > 0:
        console.print(f"  Real ATM median:      {atm['Spread_Pct'].median():.1f}% of premium ({atm['Half_Spread_Pts'].median():.3f} pts half-spread)")
    console.print()

    console.print("[bold]Next step:[/bold]")
    console.print("  Run: python scripts/backtesting/run_backtest_overnight_fade_spread_test.py")
    console.print("  with the calibrated half_spread values above.")
    console.print("  If ATM EV is still positive, the strategy is viable on IG.")
    console.print("  If ATM EV is ~0 or negative, consider signal strengthening (Step 8).")
    console.print()
    console.print("=" * 80)


def main():
    console.print("=" * 80)
    console.print("[bold blue]STEP 6A: IG Spread Sampling Analysis[/bold blue]")
    console.print("=" * 80)
    console.print()

    # Try to load filled-in samples
    import os
    if os.path.exists('data/spread_samples.csv'):
        df = load_and_validate('data/spread_samples.csv')
    elif os.path.exists('data/spread_samples_template.csv'):
        console.print("[yellow]No data/spread_samples.csv found.[/yellow]")
        console.print("[yellow]Template exists at data/spread_samples_template.csv[/yellow]")
        console.print()
        console.print("[bold]Instructions:[/bold]")
        console.print("  1. Copy template to data/spread_samples.csv")
        console.print("  2. Fill in Buy_Price, Sell_Price, Underlying_Price, Strike from IG deal ticket")
        console.print("  3. Record at ~21:00 UK for 5 trading days")
        console.print("  4. Re-run this script")
        console.print()
        console.print("[bold]What to record from IG:[/bold]")
        console.print("  - Open US 500 Weekly options chain")
        console.print("  - Find the contract expiring on the correct day (Wed or Fri)")
        console.print("  - For ATM: nearest strike to current price")
        console.print("  - For 0.3% OTM call: strike = round(price * 1.003)")
        console.print("  - For 0.3% ITM call: strike = round(price * 0.997)")
        console.print("  - Record the BUY and SELL prices shown on the deal ticket")
        console.print("  - Also note the underlying price and strike level")
        return
    else:
        console.print("[red]No spread data files found.[/red]")
        return

    if df is None:
        return

    console.print(f"[green]Loaded {len(df)} valid samples[/green]")
    console.print()

    display_samples(df)
    display_summary(df)
    suggest_cost_model(df)


if __name__ == "__main__":
    main()
