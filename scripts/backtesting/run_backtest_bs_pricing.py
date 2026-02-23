"""
Backtest with ACTUAL Black-Scholes Option Pricing

This replaces the hardcoded +45% / -105% P&L model with per-trade option pricing.

For each trade:
1. Entry: Compute BS option premium at close (ATM strike, VIX-derived IV, DTE)
2. Exit: If target hit - compute BS premium at the bar where target is hit
         If not hit - option expires, value = max(intrinsic, 0)
3. P&L = (exit_premium - entry_premium) / entry_premium

IV source: VIX daily close from yfinance (downloaded once, cached).
If VIX unavailable, falls back to 20-day realized vol * sqrt(252).

Cost models:
  --cost-model pct   (default) percentage-based spread + slippage from reality_adjustments.json
  --cost-model fixed fixed-point spread loaded from config/cost_model_fixed.json if present,
                     otherwise falls back to pct model
"""
import sys
sys.path.insert(0, 'src')

import pandas as pd
import numpy as np
import json
import os
import math
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from rich.console import Console
from rich.table import Table

from pricing import black_scholes, load_vix_data, get_iv_for_date, FixedPointCosts

console = Console()


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_config():
    with open("config/config.json", "r") as f:
        return json.load(f)


def load_reality_adjustments():
    """Load spread/slippage/commission costs (NOT the pnl_adjustments - those are the old fudge factors)."""
    adjustments_file = Path("config/reality_adjustments.json")
    if adjustments_file.exists():
        with open(adjustments_file, "r") as f:
            return json.load(f)
    return {
        "spread_costs": {"SPY": 0.05, "QQQ": 0.07, "IWM": 0.12, "DIA": 0.18},
        "slippage_pct": {"SPY": 0.015, "QQQ": 0.02, "IWM": 0.03, "DIA": 0.04},
        "close_timing_penalty": {"SPY": 0.02, "QQQ": 0.03, "IWM": 0.04, "DIA": 0.05},
        "commission_per_contract": 0.65,
    }


# ---------------------------------------------------------------------------
# Date helpers (same as existing backtest)
# ---------------------------------------------------------------------------

def get_next_trading_day(date, df_daily):
    next_date = date + timedelta(days=1)
    for _ in range(10):
        if next_date in df_daily.index:
            return next_date
        next_date += timedelta(days=1)
    return None

def get_next_wednesday(date):
    if date.weekday() in [0, 1]:
        days_ahead = 2 - date.weekday()
    else:
        days_ahead = 7 - date.weekday() + 2
    return date + timedelta(days=days_ahead)

def get_next_friday(date):
    if date.weekday() < 4:
        days_ahead = 4 - date.weekday()
    else:
        days_ahead = 7
    return date + timedelta(days=days_ahead)

def get_next_monday(date):
    if date.weekday() == 4:
        days_ahead = 3
    else:
        days_ahead = 7 - date.weekday()
    return date + timedelta(days=days_ahead)


# ---------------------------------------------------------------------------
# Core backtest with BS pricing
# ---------------------------------------------------------------------------

def run_bs_backtest(ticker, config, vix_series, adjustments, risk_free_rate=0.045,
                    cost_model='pct', fixed_costs=None):
    """
    Run backtest with actual Black-Scholes option pricing per trade.
    """
    console.print(f"\n[cyan]Running BS-PRICED backtest for {ticker}...[/cyan]")

    daily_file = f'data/{ticker}/daily_OHLCV.parquet'
    if not os.path.exists(daily_file):
        console.print(f"[red]No daily data for {ticker}[/red]")
        return None

    df_daily = pd.read_parquet(daily_file)
    valid_days = df_daily[df_daily.index.dayofweek < 5]

    import pytz
    et_tz = pytz.timezone('America/New_York')

    # Cost parameters from reality_adjustments (real friction, not fudge factors)
    spread_cost_pct = adjustments.get("spread_costs", {}).get(ticker, 0.05)
    slippage_pct = adjustments.get("slippage_pct", {}).get(ticker, 0.015)
    timing_penalty_pct = adjustments.get("close_timing_penalty", {}).get(ticker, 0.02)
    commission = adjustments.get("commission_per_contract", 0.65)

    trades = []
    skipped_no_iv = 0
    skipped_zero_premium = 0

    for i in range(len(valid_days)):
        day_t = valid_days.iloc[i]
        date_t = valid_days.index[i]
        date_str = date_t.strftime("%Y-%m-%d")
        day_of_week = date_t.dayofweek

        # Skip flat days (same as original)
        magnitude = abs((day_t['Close'] - day_t['Open']) / day_t['Open']) * 100
        if magnitude < 0.10:
            continue

        # Skip Fridays if configured
        if day_of_week == 4 and config.get('filters', {}).get('exclude_fridays', False):
            continue

        # Determine expiry
        if day_of_week == 0:
            expiry_date = get_next_wednesday(date_t)
            expiry_label = "MON-WED-2D"
            days_to_expiry = 2
        elif day_of_week == 1:
            expiry_date = get_next_wednesday(date_t)
            expiry_label = "TUE-WED-1D"
            days_to_expiry = 1
        elif day_of_week == 2:
            expiry_date = get_next_friday(date_t)
            expiry_label = "WED-FRI-2D"
            days_to_expiry = 2
        elif day_of_week == 3:
            expiry_date = get_next_friday(date_t)
            expiry_label = "THU-FRI-1D"
            days_to_expiry = 1
        elif day_of_week == 4:
            expiry_date = get_next_monday(date_t)
            expiry_label = "FRI-MON-3D"
            days_to_expiry = 3
        else:
            continue

        # Signal
        direction = "GREEN" if day_t['Close'] > day_t['Open'] else "RED"
        if direction == "GREEN":
            signal = "FADE_GREEN"
            option_type = "PUT"
        else:
            signal = "FADE_RED"
            option_type = "CALL"

        filters = config.get('filters', {})
        if signal == "FADE_GREEN" and not filters.get('enable_fade_green', True):
            continue
        if signal == "FADE_RED" and not filters.get('enable_fade_red', True):
            continue

        entry_price = day_t['Close']
        atr = day_t['ATR_14']
        if pd.isna(atr):
            continue

        strike = round(entry_price)

        # Target (0.1 * ATR in underlying terms)
        target_mult = config.get('default_take_profit_atr', 0.1)
        target_dist = atr * target_mult

        if signal == "FADE_GREEN":
            target_price = entry_price - target_dist
        else:
            target_price = entry_price + target_dist

        # ----- ENTRY: BS price -----
        iv_entry = get_iv_for_date(date_t, vix_series, df_daily)
        if iv_entry is None or iv_entry <= 0:
            skipped_no_iv += 1
            continue

        T_entry = days_to_expiry / 365.0
        entry_premium = black_scholes(entry_price, strike, T_entry, risk_free_rate, iv_entry, option_type)['price']

        if entry_premium <= 0.001:
            skipped_zero_premium += 1
            continue

        # ----- EXIT: Scan intraday bars for target hit -----
        target_hit = False
        exit_underlying = None
        exit_T_remaining = 0.0
        exit_time = None

        check_date = date_t
        while check_date <= expiry_date:
            intraday_file = f'data/{ticker}/intraday/{check_date.strftime("%Y-%m-%d")}.parquet'

            if os.path.exists(intraday_file):
                try:
                    df_intra = pd.read_parquet(intraday_file)

                    if df_intra.index.tz is not None:
                        df_intra.index = df_intra.index.tz_convert('America/New_York')
                    else:
                        df_intra.index = df_intra.index.tz_localize('UTC').tz_convert('America/New_York')

                    # Filter time window
                    if check_date == date_t:
                        entry_dt = et_tz.localize(datetime(date_t.year, date_t.month, date_t.day, 16, 0))
                        df_window = df_intra[df_intra.index >= entry_dt]
                    elif check_date == expiry_date:
                        end_dt = et_tz.localize(datetime(expiry_date.year, expiry_date.month, expiry_date.day, 9, 30))
                        df_window = df_intra[df_intra.index <= end_dt]
                    else:
                        df_window = df_intra

                    if not df_window.empty:
                        # Scan bar-by-bar to find WHEN target is hit
                        for bar_time, bar in df_window.iterrows():
                            if signal == "FADE_GREEN":  # PUT - need price to drop
                                if bar['Low'] <= target_price:
                                    target_hit = True
                                    exit_underlying = target_price  # assume fill at target
                                    exit_time = bar_time
                                    break
                            else:  # CALL - need price to rise
                                if bar['High'] >= target_price:
                                    target_hit = True
                                    exit_underlying = target_price
                                    exit_time = bar_time
                                    break

                        if target_hit:
                            break

                except Exception:
                    pass

            check_date = get_next_trading_day(check_date, df_daily)
            if check_date is None or check_date > expiry_date:
                break

        # ----- Compute exit premium -----
        if target_hit and exit_time is not None:
            # Calculate remaining time to expiry from exit_time
            # Expiry is market open (9:30 ET) on expiry_date
            expiry_dt = et_tz.localize(datetime(expiry_date.year, expiry_date.month, expiry_date.day, 9, 30))

            # Handle tz-aware comparison
            if exit_time.tzinfo is None:
                exit_time_aware = et_tz.localize(exit_time)
            else:
                exit_time_aware = exit_time

            remaining_seconds = (expiry_dt - exit_time_aware).total_seconds()
            exit_T_remaining = max(remaining_seconds / (365.25 * 24 * 3600), 0.0)

            # Use same IV for exit (conservative: no IV crush assumption)
            exit_premium = black_scholes(exit_underlying, strike, exit_T_remaining, risk_free_rate, iv_entry, option_type)['price']
        else:
            # Option expires: could still have intrinsic value at open on expiry day
            # Use last known price or entry if no intraday data
            # For simplicity: check what underlying was at expiry open
            expiry_open = None
            expiry_intra = f'data/{ticker}/intraday/{expiry_date.strftime("%Y-%m-%d")}.parquet'
            if os.path.exists(expiry_intra):
                try:
                    df_exp = pd.read_parquet(expiry_intra)
                    if df_exp.index.tz is not None:
                        df_exp.index = df_exp.index.tz_convert('America/New_York')
                    else:
                        df_exp.index = df_exp.index.tz_localize('UTC').tz_convert('America/New_York')

                    end_dt = et_tz.localize(datetime(expiry_date.year, expiry_date.month, expiry_date.day, 9, 30))
                    morning = df_exp[df_exp.index <= end_dt]
                    if not morning.empty:
                        expiry_open = morning.iloc[-1]['Close']
                except Exception:
                    pass

            if expiry_open is not None:
                exit_underlying = expiry_open
            else:
                # Fallback: use daily close of last available day before expiry
                # This means we couldn't find intraday data for expiry
                last_date = date_t
                for d_offset in range(1, days_to_expiry + 3):
                    check = date_t + timedelta(days=d_offset)
                    if check in df_daily.index:
                        last_date = check
                        if check >= expiry_date:
                            break
                if last_date in df_daily.index:
                    exit_underlying = df_daily.loc[last_date, 'Close']
                else:
                    exit_underlying = entry_price

            # At expiry, T=0, premium = intrinsic only
            exit_premium = black_scholes(exit_underlying, strike, 0.0, risk_free_rate, iv_entry, option_type)['price']

        # ----- P&L calculation -----
        # Gross P&L (theoretical, mid-to-mid)
        gross_pnl_pct = (exit_premium - entry_premium) / entry_premium

        if cost_model == 'fixed' and fixed_costs is not None:
            # Fixed-point model: deduct absolute point amounts from entry cost / exit proceeds
            actual_entry_cost = entry_premium + fixed_costs.total_one_side_pts
            actual_exit_proceeds = max(exit_premium - fixed_costs.total_one_side_pts, 0.0)
            net_pnl_dollars = actual_exit_proceeds - actual_entry_cost
            net_pnl_pct = net_pnl_dollars / actual_entry_cost if actual_entry_cost > 0 else 0.0
            pnl_mult = net_pnl_pct
        else:
            # Percentage model (default): multiply entry up, multiply exit down
            entry_cost_mult = 1.0 + (spread_cost_pct / 2.0) + slippage_pct + timing_penalty_pct
            exit_cost_mult = 1.0 - (spread_cost_pct / 2.0) - slippage_pct

            actual_entry_cost = entry_premium * entry_cost_mult
            actual_exit_proceeds = exit_premium * max(exit_cost_mult, 0.0)

            commission_total = commission * 2  # entry + exit

            net_pnl_dollars = actual_exit_proceeds - actual_entry_cost
            net_pnl_pct = net_pnl_dollars / actual_entry_cost if actual_entry_cost > 0 else 0.0

            # Commission as pct of contract cost (100-share lot)
            commission_impact_pct = commission_total / (actual_entry_cost * 100) if actual_entry_cost > 0 else 0

            pnl_mult = net_pnl_pct - commission_impact_pct

        # Also record the OLD hardcoded multiplier for comparison
        old_pnl_mult = 0.45 if target_hit else -1.05

        result = "WIN" if pnl_mult > 0 else "LOSS"

        trades.append({
            'Date': date_str,
            'Ticker': ticker,
            'Day_of_Week': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'][day_of_week],
            'Expiry_Label': expiry_label,
            'Expiry_Date': expiry_date.strftime("%Y-%m-%d"),
            'Days_To_Expiry': days_to_expiry,
            'Signal': signal,
            'Option_Type': option_type,
            'Entry_Price': entry_price,
            'Strike': strike,
            'Target_Price': target_price,
            'Target_Dist': target_dist,
            'ATR': atr,
            'IV_Entry': iv_entry,
            'Entry_Premium': entry_premium,
            'Exit_Premium': exit_premium,
            'Exit_Underlying': exit_underlying,
            'Exit_T_Remaining': exit_T_remaining * 365.0,  # store as days
            'Gross_PnL_Pct': gross_pnl_pct * 100,
            'Net_PnL_Pct': pnl_mult * 100,
            'Result': result,
            'PnL_Mult_BS': pnl_mult,
            'PnL_Mult_Old': old_pnl_mult,
            'Direction': direction,
            'Magnitude': magnitude,
            'Target_Hit': target_hit,
        })

    if skipped_no_iv > 0:
        console.print(f"[yellow]Skipped {skipped_no_iv} trades (no IV)[/yellow]")
    if skipped_zero_premium > 0:
        console.print(f"[yellow]Skipped {skipped_zero_premium} trades (zero premium)[/yellow]")

    console.print(f"[green]{ticker}: {len(trades)} trades generated[/green]")
    return pd.DataFrame(trades)


# ---------------------------------------------------------------------------
# Equity curve
# ---------------------------------------------------------------------------

def calculate_equity_curve(df, starting_capital, kelly_pct, max_position, pnl_column):
    equity = starting_capital
    equity_curve = []
    position_sizes = []
    daily_pnls = []

    for idx, row in df.iterrows():
        position = min(equity * kelly_pct, max_position)
        pnl = position * row[pnl_column]
        equity += pnl

        # Floor at $1 to avoid negative equity artifacts
        equity = max(equity, 1.0)

        equity_curve.append(equity)
        position_sizes.append(position)
        daily_pnls.append(pnl)

    return equity_curve, position_sizes, daily_pnls


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Backtest with Black-Scholes option pricing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--cost-model',
        choices=['pct', 'fixed'],
        default='pct',
        help=(
            "Cost model: 'pct' = percentage-based spread+slippage (default, baseline-compatible); "
            "'fixed' = fixed-point spread loaded from config/cost_model_fixed.json "
            "(use after spread sampling calibration)."
        ),
    )
    args = parser.parse_args()

    cost_model = args.cost_model

    console.print("=" * 80)
    console.print("[bold blue]BACKTEST: BLACK-SCHOLES OPTION PRICING (per-trade)[/bold blue]")
    console.print("=" * 80)
    console.print()
    console.print("[bold]What's different from the old backtest:[/bold]")
    console.print("  OLD: Every win = +45%, every loss = -105% (hardcoded)")
    console.print("  NEW: Each trade priced via Black-Scholes using that day's VIX")
    console.print("       Entry premium at close, exit premium at target hit (or expiry)")
    console.print("       Real spread, slippage, timing penalty, commission applied")
    console.print()
    console.print(f"[bold]Cost model:[/bold] {cost_model}")
    console.print()

    config = load_config()
    adjustments = load_reality_adjustments()

    # Resolve fixed_costs when --cost-model fixed is requested
    fixed_costs = None
    if cost_model == 'fixed':
        fixed_cfg = Path("config/cost_model_fixed.json")
        if fixed_cfg.exists():
            fixed_costs = FixedPointCosts.from_config(str(fixed_cfg))
            console.print(
                f"[green]Loaded fixed cost model: half_spread={fixed_costs.half_spread_pts:.3f} pts, "
                f"slippage={fixed_costs.slippage_pts:.3f} pts[/green]"
            )
        else:
            console.print(
                "[yellow]WARNING: config/cost_model_fixed.json not found. "
                "Run spread sampling first (scripts/data/collect_ig_spreads.py). "
                "Falling back to pct model.[/yellow]"
            )
            cost_model = 'pct'
    console.print()

    # Load VIX data
    vix_series = load_vix_data()
    if vix_series is None:
        console.print("[yellow]WARNING: No VIX data. Using realised vol fallback for all trades.[/yellow]")
    else:
        console.print(f"[green]VIX data: {vix_series.index.min().date()} to {vix_series.index.max().date()}[/green]")
    console.print()

    # Only run SPY (the only viable ticker per prior analysis)
    # Can expand to config['tickers'] if desired
    tickers = config.get('tickers', ['SPY'])
    console.print(f"[cyan]Tickers: {', '.join(tickers)}[/cyan]")
    console.print()

    all_results = []
    for ticker in tickers:
        result = run_bs_backtest(ticker, config, vix_series, adjustments,
                                  cost_model=cost_model, fixed_costs=fixed_costs)
        if result is not None and not result.empty:
            all_results.append(result)

    if not all_results:
        console.print("[red]No results generated[/red]")
        return

    df = pd.concat(all_results, ignore_index=True)
    df = df.sort_values('Date').reset_index(drop=True)

    console.print(f"\n[green]Total: {len(df):,} trades[/green]\n")

    # ----- Equity curves: BS-priced vs Old hardcoded -----
    starting_capital = 10000
    kelly_pct = 0.0523
    max_position = 1000

    eq_bs, pos_bs, pnl_bs = calculate_equity_curve(df, starting_capital, kelly_pct, max_position, 'PnL_Mult_BS')
    eq_old, pos_old, pnl_old = calculate_equity_curve(df, starting_capital, kelly_pct, max_position, 'PnL_Mult_Old')

    df['Equity_BS'] = eq_bs
    df['Equity_Old'] = eq_old
    df['PnL_Dollar_BS'] = pnl_bs
    df['PnL_Dollar_Old'] = pnl_old

    # ----- Statistics -----
    df['Date'] = pd.to_datetime(df['Date'])
    years = (df['Date'].max() - df['Date'].min()).days / 365.25

    final_bs = eq_bs[-1]
    final_old = eq_old[-1]

    cagr_bs = (pow(final_bs / starting_capital, 1 / years) - 1) * 100 if final_bs > starting_capital else -((1 - pow(final_bs / starting_capital, 1 / years)) * 100)
    cagr_old = (pow(final_old / starting_capital, 1 / years) - 1) * 100

    wins = (df['Result'] == 'WIN').sum()
    losses = (df['Result'] == 'LOSS').sum()
    win_rate = wins / len(df) * 100

    # Drawdown for BS
    running_max_bs = pd.Series(eq_bs).expanding().max()
    drawdown_bs = (pd.Series(eq_bs) - running_max_bs) / running_max_bs * 100
    max_dd_bs = drawdown_bs.min()

    # ----- Display comparison -----
    console.print("=" * 80)
    console.print("[bold white]RESULTS: BS-PRICED vs OLD HARDCODED[/bold white]")
    console.print("=" * 80)
    console.print()

    comp = Table(show_header=True, header_style="bold cyan")
    comp.add_column("Metric", style="white", width=30)
    comp.add_column("BS-Priced (NEW)", justify="right", width=22)
    comp.add_column("Hardcoded (OLD)", justify="right", width=22)

    comp.add_row("Period", f"{df['Date'].min().strftime('%Y-%m-%d')} to {df['Date'].max().strftime('%Y-%m-%d')}", "")
    comp.add_row("Years", f"{years:.1f}", "")
    comp.add_row("Total Trades", f"{len(df):,}", f"{len(df):,}")
    comp.add_row("Win Rate", f"{win_rate:.1f}%", f"{win_rate:.1f}%")
    comp.add_row("", "", "")
    comp.add_row("Starting Capital", f"${starting_capital:,.0f}", f"${starting_capital:,.0f}")
    comp.add_row("Final Equity", f"[bold]${final_bs:,.0f}[/bold]", f"${final_old:,.0f}")
    comp.add_row("CAGR", f"[bold]{cagr_bs:+.1f}%[/bold]", f"{cagr_old:.1f}%")
    comp.add_row("Max Drawdown", f"{max_dd_bs:.1f}%", "N/A")

    console.print(comp)
    console.print()

    # ----- P&L distribution for BS-priced trades -----
    console.print("=" * 80)
    console.print("[bold white]BS-PRICED P&L DISTRIBUTION[/bold white]")
    console.print("=" * 80)
    console.print()

    wins_df = df[df['Result'] == 'WIN']
    losses_df = df[df['Result'] == 'LOSS']

    if len(wins_df) > 0:
        console.print(f"[green]WINS ({len(wins_df)} trades):[/green]")
        console.print(f"  Avg gross P&L:  {wins_df['Gross_PnL_Pct'].mean():+.1f}%")
        console.print(f"  Avg net P&L:    {wins_df['Net_PnL_Pct'].mean():+.1f}%")
        console.print(f"  Median net P&L: {wins_df['Net_PnL_Pct'].median():+.1f}%")
        console.print(f"  Min net P&L:    {wins_df['Net_PnL_Pct'].min():+.1f}%")
        console.print(f"  Max net P&L:    {wins_df['Net_PnL_Pct'].max():+.1f}%")
        console.print(f"  Std dev:        {wins_df['Net_PnL_Pct'].std():.1f}%")
        console.print()

    if len(losses_df) > 0:
        console.print(f"[red]LOSSES ({len(losses_df)} trades):[/red]")
        console.print(f"  Avg gross P&L:  {losses_df['Gross_PnL_Pct'].mean():+.1f}%")
        console.print(f"  Avg net P&L:    {losses_df['Net_PnL_Pct'].mean():+.1f}%")
        console.print(f"  Median net P&L: {losses_df['Net_PnL_Pct'].median():+.1f}%")
        console.print()

    # Overall expected value
    ev = df['Net_PnL_Pct'].mean()
    console.print(f"[bold]Overall expected value per trade: {ev:+.2f}%[/bold]")
    console.print()

    # ----- Breakdown: BS win returns vs the old hardcoded +45% -----
    console.print("=" * 80)
    console.print("[bold white]KEY INSIGHT: What do winning trades ACTUALLY return?[/bold white]")
    console.print("=" * 80)
    console.print()

    console.print(f"  Old backtest assumed: [bold]+45.0%[/bold] for EVERY win")
    if len(wins_df) > 0:
        console.print(f"  BS-priced average:   [bold]{wins_df['Gross_PnL_Pct'].mean():+.1f}%[/bold] (gross, before costs)")
        console.print(f"  BS-priced average:   [bold]{wins_df['Net_PnL_Pct'].mean():+.1f}%[/bold] (net, after costs)")
        console.print()

        # Distribution buckets
        console.print("  Win return distribution (net):")
        buckets = [(-100, 0), (0, 10), (10, 20), (20, 30), (30, 50), (50, 100), (100, 500)]
        for lo, hi in buckets:
            count = ((wins_df['Net_PnL_Pct'] >= lo) & (wins_df['Net_PnL_Pct'] < hi)).sum()
            pct = count / len(wins_df) * 100
            bar = "#" * int(pct / 2)
            console.print(f"    {lo:+4d}% to {hi:+4d}%: {count:4d} ({pct:5.1f}%) {bar}")

    console.print()

    # ----- Breakdown by IV regime -----
    console.print("=" * 80)
    console.print("[bold white]RETURNS BY IV REGIME[/bold white]")
    console.print("=" * 80)
    console.print()

    iv_table = Table(show_header=True, header_style="bold cyan")
    iv_table.add_column("VIX Range", width=15)
    iv_table.add_column("Trades", justify="right", width=10)
    iv_table.add_column("Win Rate", justify="right", width=10)
    iv_table.add_column("Avg Win (net)", justify="right", width=15)
    iv_table.add_column("Avg Loss (net)", justify="right", width=15)
    iv_table.add_column("EV/trade", justify="right", width=12)

    iv_buckets = [(0, 0.15, "VIX < 15"), (0.15, 0.20, "VIX 15-20"), (0.20, 0.25, "VIX 20-25"),
                  (0.25, 0.35, "VIX 25-35"), (0.35, 1.0, "VIX > 35")]

    for lo, hi, label in iv_buckets:
        mask = (df['IV_Entry'] >= lo) & (df['IV_Entry'] < hi)
        sub = df[mask]
        if len(sub) > 0:
            wr = (sub['Result'] == 'WIN').sum() / len(sub) * 100
            wins_sub = sub[sub['Result'] == 'WIN']['Net_PnL_Pct']
            losses_sub = sub[sub['Result'] == 'LOSS']['Net_PnL_Pct']
            avg_win = wins_sub.mean() if len(wins_sub) > 0 else 0
            avg_loss = losses_sub.mean() if len(losses_sub) > 0 else 0
            ev_trade = sub['Net_PnL_Pct'].mean()

            ev_style = "green" if ev_trade > 0 else "red"
            iv_table.add_row(label, f"{len(sub):,}", f"{wr:.1f}%",
                             f"{avg_win:+.1f}%" if len(wins_sub) > 0 else "N/A",
                             f"{avg_loss:+.1f}%" if len(losses_sub) > 0 else "N/A",
                             f"[{ev_style}]{ev_trade:+.2f}%[/{ev_style}]")

    console.print(iv_table)
    console.print()

    # ----- Breakdown by expiry type -----
    console.print("=" * 80)
    console.print("[bold white]RETURNS BY EXPIRY PATTERN[/bold white]")
    console.print("=" * 80)
    console.print()

    exp_table = Table(show_header=True, header_style="bold cyan")
    exp_table.add_column("Pattern", width=15)
    exp_table.add_column("Trades", justify="right", width=10)
    exp_table.add_column("Win Rate", justify="right", width=10)
    exp_table.add_column("Avg Win (net)", justify="right", width=15)
    exp_table.add_column("Avg Loss (net)", justify="right", width=15)
    exp_table.add_column("EV/trade", justify="right", width=12)

    for label in sorted(df['Expiry_Label'].unique()):
        sub = df[df['Expiry_Label'] == label]
        if len(sub) > 0:
            wr = (sub['Result'] == 'WIN').sum() / len(sub) * 100
            wins_sub = sub[sub['Result'] == 'WIN']['Net_PnL_Pct']
            losses_sub = sub[sub['Result'] == 'LOSS']['Net_PnL_Pct']
            avg_win = wins_sub.mean() if len(wins_sub) > 0 else 0
            avg_loss = losses_sub.mean() if len(losses_sub) > 0 else 0
            ev_trade = sub['Net_PnL_Pct'].mean()

            ev_style = "green" if ev_trade > 0 else "red"
            exp_table.add_row(label, f"{len(sub):,}", f"{wr:.1f}%",
                              f"{avg_win:+.1f}%" if len(wins_sub) > 0 else "N/A",
                              f"{avg_loss:+.1f}%" if len(losses_sub) > 0 else "N/A",
                              f"[{ev_style}]{ev_trade:+.2f}%[/{ev_style}]")

    console.print(exp_table)
    console.print()

    # ----- Sample trades -----
    console.print("=" * 80)
    console.print("[bold white]SAMPLE TRADES (first 10 wins, first 5 losses)[/bold white]")
    console.print("=" * 80)
    console.print()

    sample_table = Table(show_header=True, header_style="bold cyan", show_lines=True)
    sample_table.add_column("Date", width=12)
    sample_table.add_column("Type", width=6)
    sample_table.add_column("Spot", justify="right", width=8)
    sample_table.add_column("Strike", justify="right", width=8)
    sample_table.add_column("ATR", justify="right", width=7)
    sample_table.add_column("IV", justify="right", width=6)
    sample_table.add_column("DTE", justify="right", width=4)
    sample_table.add_column("Entry$", justify="right", width=8)
    sample_table.add_column("Exit$", justify="right", width=8)
    sample_table.add_column("Gross%", justify="right", width=8)
    sample_table.add_column("Net%", justify="right", width=8)
    sample_table.add_column("Old%", justify="right", width=7)
    sample_table.add_column("Result", width=6)

    sample = pd.concat([wins_df.head(10), losses_df.head(5)])
    for _, row in sample.iterrows():
        res_style = "green" if row['Result'] == 'WIN' else "red"
        sample_table.add_row(
            row['Date'] if isinstance(row['Date'], str) else row['Date'].strftime('%Y-%m-%d'),
            row['Option_Type'],
            f"${row['Entry_Price']:.0f}",
            f"${row['Strike']:.0f}",
            f"{row['ATR']:.1f}",
            f"{row['IV_Entry']:.0%}",
            f"{row['Days_To_Expiry']}",
            f"${row['Entry_Premium']:.2f}",
            f"${row['Exit_Premium']:.2f}",
            f"{row['Gross_PnL_Pct']:+.1f}%",
            f"{row['Net_PnL_Pct']:+.1f}%",
            f"{row['PnL_Mult_Old'] * 100:+.0f}%",
            f"[{res_style}]{row['Result']}[/{res_style}]",
        )

    console.print(sample_table)
    console.print()

    # ----- Save results -----
    output_file = 'results/bs_priced_backtest.csv'
    df.to_csv(output_file, index=False)
    console.print(f"[green]Full results saved to: {output_file}[/green]")
    console.print()

    # ----- Final verdict -----
    console.print("=" * 80)
    console.print("[bold white]VERDICT[/bold white]")
    console.print("=" * 80)
    console.print()

    console.print(f"  Old backtest CAGR (hardcoded +45%/-105%): [bold]{cagr_old:.1f}%[/bold]")
    console.print(f"  BS-priced CAGR (actual option dynamics):  [bold]{cagr_bs:+.1f}%[/bold]")
    console.print()

    if cagr_bs > 15:
        console.print("[bold green]Strategy appears VIABLE with proper option pricing.[/bold green]")
    elif cagr_bs > 5:
        console.print("[bold yellow]Strategy is MARGINAL with proper option pricing.[/bold yellow]")
    elif cagr_bs > 0:
        console.print("[bold yellow]Strategy is BARELY POSITIVE — likely not worth the effort.[/bold yellow]")
    else:
        console.print("[bold red]Strategy is NEGATIVE with proper option pricing.[/bold red]")
        console.print("[bold red]The old +45% hardcoded wins were masking the true dynamics.[/bold red]")

    console.print()
    console.print("=" * 80)


if __name__ == "__main__":
    main()
