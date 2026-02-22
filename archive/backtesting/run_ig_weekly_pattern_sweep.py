"""
Per-Pattern Optimized Sweep for IG Weekly US 500 Options

Each DTE pattern (1D, 2D, 3D) and direction (RED, GREEN) has different
optimal parameters. This sweep tests target % independently for each
combination to find the best rules per pattern.

Patterns:
  1D expiry: TUE-WED, THU-FRI  (option expires next day at 16:00 ET)
  2D expiry: MON-WED, WED-FRI  (option expires in 2 trading days at 16:00 ET)
  3D expiry: FRI-MON            (3 calendar days but 1 trading day to Monday close)

Uses the corrected 16:00 ET expiry model from run_backtest_ig_weekly.py.
"""
import sys
sys.path.insert(0, 'src')

import pandas as pd
import numpy as np
import json
import os
import math
from datetime import datetime, timedelta, date as date_type
from pathlib import Path
from rich.console import Console
from rich.table import Table
import pytz

console = Console()

# ---------------------------------------------------------------------------
# Import core functions from run_backtest_ig_weekly.py
# ---------------------------------------------------------------------------
sys.path.insert(0, 'scripts/backtesting')
from run_backtest_ig_weekly import (
    bs_price, norm_cdf,
    load_vix_data, get_iv,
    load_config, load_reality_adjustments,
    get_next_trading_day, get_next_wednesday, get_next_friday, get_next_monday,
    compute_T_remaining, normalize_intraday,
    RTH_MINUTES_PER_DAY, TRADING_MINUTES_PER_YEAR,
)


# ---------------------------------------------------------------------------
# Pre-compute all trade setups with bar-level premium tracking
# ---------------------------------------------------------------------------

def precompute_trade_setups(ticker, vix_series, use_vix_iv=True):
    """
    Pre-compute ALL trade setups with per-bar premium data.

    For each trade day:
      - Compute entry premium (BS)
      - Scan ALL bars from entry through 16:00 expiry
      - Record the MAXIMUM premium seen at each bar (best opportunity)
      - Store the max premium ratio (peak_prem / entry_prem - 1)

    This allows post-hoc target sweep: for any target_pct, check if
    max_premium_ratio >= target_pct.

    Returns:
        List of dicts, each with trade setup + max_premium_pct
    """
    daily_file = f'data/{ticker}/daily_OHLCV.parquet'
    if not os.path.exists(daily_file):
        console.print(f"[red]Missing {daily_file}[/red]")
        return []

    df_daily = pd.read_parquet(daily_file)
    valid_days = df_daily[df_daily.index.dayofweek < 5]
    trading_dates_set = set(valid_days.index.normalize())

    et_tz = pytz.timezone('America/New_York')
    sigma_dashboard = 0.15
    r = 0.05

    setups = []
    total = len(valid_days)

    for i in range(total):
        day_t = valid_days.iloc[i]
        date_t = valid_days.index[i]
        day_of_week = date_t.dayofweek

        magnitude = abs((day_t['Close'] - day_t['Open']) / day_t['Open']) * 100
        if magnitude < 0.10:
            continue

        # Determine expiry pattern
        if day_of_week == 0:
            expiry_date = get_next_wednesday(date_t)
            expiry_label = "MON-WED-2D"
            days_to_expiry = 2
            dte_group = "2D"
        elif day_of_week == 1:
            expiry_date = get_next_wednesday(date_t)
            expiry_label = "TUE-WED-1D"
            days_to_expiry = 1
            dte_group = "1D"
        elif day_of_week == 2:
            expiry_date = get_next_friday(date_t)
            expiry_label = "WED-FRI-2D"
            days_to_expiry = 2
            dte_group = "2D"
        elif day_of_week == 3:
            expiry_date = get_next_friday(date_t)
            expiry_label = "THU-FRI-1D"
            days_to_expiry = 1
            dte_group = "1D"
        elif day_of_week == 4:
            expiry_date = get_next_monday(date_t)
            expiry_label = "FRI-MON-3D"
            days_to_expiry = 3
            dte_group = "3D"
        else:
            continue

        direction = "GREEN" if day_t['Close'] > day_t['Open'] else "RED"
        option_type = "PUT" if direction == "GREEN" else "CALL"

        entry_price = day_t['Close']
        atr = day_t['ATR_14']
        if pd.isna(atr):
            continue

        strike = round(entry_price)

        # Trading DTE for T_entry
        trading_dte = days_to_expiry
        if day_of_week == 4:  # Friday -> Monday = 1 trading day
            trading_dte = 1
        T_entry = trading_dte / 252.0

        sigma_real = get_iv(date_t, vix_series) if use_vix_iv else sigma_dashboard
        entry_prem = bs_price(entry_price, strike, T_entry, r, sigma_real, option_type)
        if entry_prem < 0.01:
            continue

        # Expiry reference
        expiry_1600 = et_tz.localize(
            datetime(expiry_date.year, expiry_date.month, expiry_date.day, 16, 0)
        )

        # Scan ALL bars and track maximum premium
        max_prem = entry_prem  # Start at entry
        max_prem_time = None

        check_date = date_t
        while check_date <= expiry_date:
            intraday_file = f'data/{ticker}/intraday/{check_date.strftime("%Y-%m-%d")}.parquet'

            if os.path.exists(intraday_file):
                try:
                    df_intra = pd.read_parquet(intraday_file)
                    df_intra = normalize_intraday(df_intra, et_tz)

                    if check_date == date_t:
                        entry_dt = et_tz.localize(
                            datetime(date_t.year, date_t.month, date_t.day, 16, 0)
                        )
                        df_window = df_intra[df_intra.index >= entry_dt]
                    elif check_date == expiry_date:
                        end_dt = et_tz.localize(
                            datetime(expiry_date.year, expiry_date.month, expiry_date.day, 16, 0)
                        )
                        df_window = df_intra[df_intra.index <= end_dt]
                    else:
                        df_window = df_intra

                    if not df_window.empty:
                        for bar_time, bar in df_window.iterrows():
                            T_now = compute_T_remaining(
                                bar_time, expiry_1600, trading_dates_set
                            )

                            if option_type == 'CALL':
                                bar_price = bar['High']
                            else:
                                bar_price = bar['Low']

                            current_prem = bs_price(
                                bar_price, strike, T_now, r, sigma_real, option_type
                            )

                            if current_prem > max_prem:
                                max_prem = current_prem
                                max_prem_time = bar_time

                except Exception:
                    pass

            check_date = get_next_trading_day(check_date, df_daily)
            if check_date is None or check_date > expiry_date:
                break

        # Get expiry close for loss calculation
        expiry_underlying = None
        expiry_intra = f'data/{ticker}/intraday/{expiry_date.strftime("%Y-%m-%d")}.parquet'
        if os.path.exists(expiry_intra):
            try:
                df_exp = pd.read_parquet(expiry_intra)
                df_exp = normalize_intraday(df_exp, et_tz)
                close_1600 = et_tz.localize(
                    datetime(expiry_date.year, expiry_date.month, expiry_date.day, 16, 0)
                )
                rth_bars = df_exp[df_exp.index <= close_1600]
                if not rth_bars.empty:
                    expiry_underlying = rth_bars.iloc[-1]['Close']
            except Exception:
                pass

        if expiry_underlying is not None:
            expiry_prem = bs_price(expiry_underlying, strike, 0.0, r, sigma_real, option_type)
        else:
            expiry_prem = 0.0
            expiry_underlying = entry_price

        # Max premium gain ratio
        max_gain_pct = (max_prem - entry_prem) / entry_prem if entry_prem > 0 else 0
        # Expiry loss
        expiry_loss_pct = (expiry_prem - entry_prem) / entry_prem if entry_prem > 0 else -1.0

        setups.append({
            'date': date_t.strftime("%Y-%m-%d"),
            'day_of_week': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'][day_of_week],
            'expiry_label': expiry_label,
            'dte_group': dte_group,
            'days_to_expiry': days_to_expiry,
            'trading_dte': trading_dte,
            'direction': direction,
            'option_type': option_type,
            'entry_price': entry_price,
            'strike': strike,
            'atr': atr,
            'iv': sigma_real,
            'entry_prem': entry_prem,
            'max_prem': max_prem,
            'max_gain_pct': max_gain_pct,  # Best possible exit gain
            'expiry_underlying': expiry_underlying,
            'expiry_prem': expiry_prem,
            'expiry_loss_pct': expiry_loss_pct,  # Loss if held to expiry
            'magnitude': magnitude,
        })

        if len(setups) % 200 == 0:
            console.print(f"  {len(setups)} setups pre-computed...")

    console.print(f"[green]Total: {len(setups)} trade setups pre-computed[/green]")
    return setups


def evaluate_target(setups_df, target_pct, spread_pct=0.04, slippage_pct=0.01):
    """
    For a given target_pct, evaluate each trade:
    - WIN if max_gain_pct >= target_pct (limit would have filled)
    - LOSS otherwise (exit at expiry intrinsic)

    Returns dict with stats.
    """
    df = setups_df.copy()

    wins = df['max_gain_pct'] >= target_pct
    df['result'] = np.where(wins, 'WIN', 'LOSS')

    # P&L
    df['gross_pnl'] = np.where(wins, target_pct, df['expiry_loss_pct'])
    df['net_pnl'] = df['gross_pnl'] - spread_pct - slippage_pct
    df['net_pnl'] = df['net_pnl'].clip(lower=-1.0)

    n = len(df)
    n_wins = wins.sum()
    win_rate = n_wins / n * 100 if n > 0 else 0
    avg_win = df.loc[wins, 'net_pnl'].mean() * 100 if n_wins > 0 else 0
    avg_loss = df.loc[~wins, 'net_pnl'].mean() * 100 if (n - n_wins) > 0 else 0
    ev = df['net_pnl'].mean() * 100

    # Breakeven WR
    if avg_win - avg_loss != 0:
        be_wr = abs(avg_loss) / (avg_win + abs(avg_loss)) * 100
    else:
        be_wr = 50.0

    return {
        'target_pct': target_pct * 100,
        'trades': n,
        'wins': n_wins,
        'win_rate': win_rate,
        'be_wr': be_wr,
        'margin': win_rate - be_wr,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'ev': ev,
    }


def run_full_sweep(setups):
    """
    Run sweep across all combinations of:
      - Direction: RED, GREEN, ALL
      - Pattern: each expiry_label + ALL
      - Target %: 2% to 50%
    """
    df = pd.DataFrame(setups)

    target_pcts = [0.02, 0.03, 0.04, 0.05, 0.07, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]

    all_results = []

    # Define slices
    slices = []

    # Per direction x pattern
    for direction in ['RED', 'GREEN', 'ALL']:
        for pattern in sorted(df['expiry_label'].unique()) + ['ALL']:
            for dte_group_filter in [None]:  # We use pattern instead
                if direction == 'ALL':
                    sub = df
                else:
                    sub = df[df['direction'] == direction]

                if pattern != 'ALL':
                    sub = sub[sub['expiry_label'] == pattern]

                if len(sub) < 20:  # Skip tiny samples
                    continue

                slices.append((direction, pattern, sub))

    # Also add DTE group slices
    for direction in ['RED', 'GREEN', 'ALL']:
        for dte_g in ['1D', '2D', '3D']:
            if direction == 'ALL':
                sub = df[df['dte_group'] == dte_g]
            else:
                sub = df[(df['direction'] == direction) & (df['dte_group'] == dte_g)]

            if len(sub) < 20:
                continue

            slices.append((direction, f"ALL-{dte_g}", sub))

    console.print(f"\n[cyan]Testing {len(slices)} slices x {len(target_pcts)} targets = {len(slices)*len(target_pcts)} combinations...[/cyan]\n")

    for direction, pattern, sub in slices:
        for tgt in target_pcts:
            stats = evaluate_target(sub, tgt)
            stats['direction'] = direction
            stats['pattern'] = pattern
            all_results.append(stats)

    return pd.DataFrame(all_results)


def display_best_per_pattern(results_df):
    """Show the best target % for each direction x pattern combo."""
    console.print()
    console.print("=" * 90)
    console.print("[bold cyan]BEST TARGET % PER PATTERN (highest EV)[/bold cyan]")
    console.print("=" * 90)
    console.print()

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Direction", width=10)
    table.add_column("Pattern", width=14)
    table.add_column("Best Tgt%", justify="right", width=10)
    table.add_column("Trades", justify="right", width=8)
    table.add_column("Win%", justify="right", width=8)
    table.add_column("BE%", justify="right", width=8)
    table.add_column("Margin", justify="right", width=8)
    table.add_column("Avg Win", justify="right", width=9)
    table.add_column("Avg Loss", justify="right", width=9)
    table.add_column("EV", justify="right", width=10)

    # For each direction x pattern, find best target
    grouped = results_df.groupby(['direction', 'pattern'])

    rows = []
    for (direction, pattern), group in grouped:
        best = group.loc[group['ev'].idxmax()]
        rows.append(best)

    # Sort: positive EV first (descending), then by EV
    rows.sort(key=lambda x: -x['ev'])

    for row in rows:
        ev_style = "bold green" if row['ev'] > 0 else "red"
        margin_style = "green" if row['margin'] > 0 else "red"
        table.add_row(
            row['direction'],
            row['pattern'],
            f"{row['target_pct']:.0f}%",
            f"{row['trades']:,.0f}",
            f"{row['win_rate']:.1f}%",
            f"{row['be_wr']:.1f}%",
            f"[{margin_style}]{row['margin']:+.1f}pp[/{margin_style}]",
            f"{row['avg_win']:+.1f}%",
            f"{row['avg_loss']:+.1f}%",
            f"[{ev_style}]{row['ev']:+.2f}%[/{ev_style}]",
        )

    console.print(table)
    console.print()


def display_heatmap(results_df, direction_filter='RED'):
    """Show EV heatmap: patterns vs target %."""
    console.print()
    console.print(f"[bold cyan]EV HEATMAP — {direction_filter} days (Target% vs Pattern)[/bold cyan]")
    console.print()

    sub = results_df[results_df['direction'] == direction_filter]
    patterns = sorted(sub['pattern'].unique())
    targets = sorted(sub['target_pct'].unique())

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Target%", justify="right", width=8)
    for p in patterns:
        table.add_column(p, justify="right", width=13)

    for tgt in targets:
        row_data = [f"{tgt:.0f}%"]
        for p in patterns:
            cell = sub[(sub['pattern'] == p) & (sub['target_pct'] == tgt)]
            if len(cell) == 0:
                row_data.append("-")
            else:
                ev = cell.iloc[0]['ev']
                wr = cell.iloc[0]['win_rate']
                style = "green" if ev > 0 else "red"
                row_data.append(f"[{style}]{ev:+.1f}%[/{style}] ({wr:.0f}%)")
        table.add_row(*row_data)

    console.print(table)
    console.print()


def display_positive_only(results_df):
    """Show ONLY configurations with positive EV."""
    positive = results_df[results_df['ev'] > 0].sort_values('ev', ascending=False)

    console.print()
    console.print("=" * 90)
    if len(positive) == 0:
        console.print("[bold red]NO CONFIGURATIONS WITH POSITIVE EV FOUND[/bold red]")
    else:
        console.print(f"[bold green]POSITIVE EV CONFIGURATIONS: {len(positive)} found[/bold green]")
    console.print("=" * 90)
    console.print()

    if len(positive) == 0:
        # Show top 10 closest to breakeven
        console.print("[yellow]Top 10 closest to breakeven:[/yellow]")
        closest = results_df.nlargest(10, 'ev')
        table = Table(show_header=True, header_style="bold yellow")
        table.add_column("Direction", width=10)
        table.add_column("Pattern", width=14)
        table.add_column("Target%", justify="right", width=9)
        table.add_column("Trades", justify="right", width=8)
        table.add_column("Win%", justify="right", width=8)
        table.add_column("BE%", justify="right", width=8)
        table.add_column("Margin", justify="right", width=8)
        table.add_column("EV", justify="right", width=10)

        for _, row in closest.iterrows():
            margin_style = "green" if row['margin'] > 0 else "red"
            table.add_row(
                row['direction'], row['pattern'], f"{row['target_pct']:.0f}%",
                f"{row['trades']:,.0f}", f"{row['win_rate']:.1f}%",
                f"{row['be_wr']:.1f}%",
                f"[{margin_style}]{row['margin']:+.1f}pp[/{margin_style}]",
                f"[red]{row['ev']:+.2f}%[/red]",
            )
        console.print(table)
    else:
        table = Table(show_header=True, header_style="bold green")
        table.add_column("Direction", width=10)
        table.add_column("Pattern", width=14)
        table.add_column("Target%", justify="right", width=9)
        table.add_column("Trades", justify="right", width=8)
        table.add_column("Win%", justify="right", width=8)
        table.add_column("BE%", justify="right", width=8)
        table.add_column("Margin", justify="right", width=8)
        table.add_column("Avg Win", justify="right", width=9)
        table.add_column("Avg Loss", justify="right", width=9)
        table.add_column("EV", justify="right", width=10)

        for _, row in positive.iterrows():
            table.add_row(
                row['direction'], row['pattern'], f"{row['target_pct']:.0f}%",
                f"{row['trades']:,.0f}", f"{row['win_rate']:.1f}%",
                f"{row['be_wr']:.1f}%",
                f"[green]{row['margin']:+.1f}pp[/green]",
                f"{row['avg_win']:+.1f}%",
                f"{row['avg_loss']:+.1f}%",
                f"[bold green]{row['ev']:+.2f}%[/bold green]",
            )
        console.print(table)

    console.print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    console.print("=" * 90)
    console.print("[bold blue]IG WEEKLY OPTIONS — PER-PATTERN OPTIMIZED SWEEP[/bold blue]")
    console.print("=" * 90)
    console.print()
    console.print("Testing every combination of Direction × Pattern × Target%")
    console.print("to find optimal rules for each DTE pattern independently.")
    console.print()
    console.print("Uses corrected 16:00 ET expiry model (IG weekly cash settlement).")
    console.print()

    vix_series = load_vix_data()
    console.print(f"[green]VIX data: {vix_series.index.min().date()} to {vix_series.index.max().date()}[/green]")
    console.print()

    # Pre-compute all setups (scan bars once, reuse for all targets)
    console.print("[bold]Phase 1: Pre-computing trade setups (scanning all bars)...[/bold]")
    setups = precompute_trade_setups('SPY', vix_series, use_vix_iv=True)

    if not setups:
        console.print("[red]No setups computed. Check data.[/red]")
        return

    # Distribution of max gains
    df_setups = pd.DataFrame(setups)
    console.print()
    console.print("[bold]Max premium gain distribution (best possible exit per trade):[/bold]")
    for pct in [0.02, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50]:
        count = (df_setups['max_gain_pct'] >= pct).sum()
        console.print(f"  >={pct*100:.0f}% gain achievable: {count}/{len(df_setups)} trades ({count/len(df_setups)*100:.1f}%)")
    console.print()

    # Run full sweep
    console.print("[bold]Phase 2: Evaluating all Direction × Pattern × Target combinations...[/bold]")
    results = run_full_sweep(setups)

    # Save raw results
    results.to_csv('results/ig_weekly_pattern_sweep.csv', index=False)
    console.print(f"[green]Full sweep saved to: results/ig_weekly_pattern_sweep.csv[/green]")

    # Display results
    display_positive_only(results)
    display_best_per_pattern(results)

    # Heatmaps
    display_heatmap(results, 'RED')
    display_heatmap(results, 'GREEN')
    display_heatmap(results, 'ALL')

    # Summary
    console.print("=" * 90)
    console.print("[bold white]INTERPRETATION[/bold white]")
    console.print("=" * 90)
    console.print()
    console.print("Positive margin (Win% > BE%) means the strategy has edge at that target.")
    console.print("Positive EV means average trade is profitable after costs (4% spread + 1% slippage).")
    console.print()
    console.print("If NO positive EV configs exist:")
    console.print("  -> The option strategy is fundamentally unprofitable")
    console.print("  -> Consider spread bets on underlying (linear P&L, no theta)")
    console.print()
    console.print("If SOME positive EV configs exist:")
    console.print("  -> Those specific pattern + direction + target combos have edge")
    console.print("  -> Only trade those exact configurations")
    console.print("  -> Beware of overfitting: >50 trades per config for statistical significance")
    console.print()
    console.print("=" * 90)

    # Save setups for further analysis
    df_setups.to_csv('results/ig_weekly_precomputed_setups.csv', index=False)
    console.print(f"[green]Pre-computed setups saved to: results/ig_weekly_precomputed_setups.csv[/green]")


if __name__ == "__main__":
    main()
