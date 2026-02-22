"""
Backtest: SPREAD BET on underlying index (v2 - corrected costs)

This models a PURE SPREAD BET on the underlying index (US 500),
NOT options. P&L is purely linear.

KEY FIXES vs v1 (run_backtest_spread_bet.py):
1. Costs are scaled correctly: IG spread is 0.4pts on SPX (~6000),
   so on SPY (~600) that's 0.04 pts. Previous version applied 0.4 to SPY.
2. Losses are PARTIAL (actual price move), not 100% like options.
3. Added proper leverage analysis (IG margin = 5% = 20x leverage).
4. Added detailed breakdown by day pattern, direction, magnitude bins.
5. Consistent with auto_trade_ig.py signal logic.

SPREAD BET P&L MODEL:
    P&L (per point of stake) = exit_price - entry_price (LONG)
    P&L (per point of stake) = entry_price - exit_price (SHORT)

    As % of underlying: P&L / entry_price
    As % of margin (5%): (P&L / entry_price) * 20

COSTS (IG.com US 500 spread bet, expressed as % of price - scale invariant):
    - IG spread: 0.4 pts on ~6000 = 0.00667% per side
    - Slippage: 0.2 pts on ~6000 per side = 0.00333% per side
    - Overnight funding: ~0.02% per night (of notional)
    - Round-trip spread+slippage: ~0.013% of price
    Note: expressed as fractions, these are identical on SPY or SPX scale.

THE STRATEGY:
    1. At close: if GREEN day, go SHORT (fade); if RED day, go LONG (fade)
    2. Target: 0.1 * ATR move in fade direction
    3. If target hit within window (by expiry morning 9:30): take profit
    4. If not hit: close at whatever price is at end of window (partial loss OR gain)
"""
import sys
sys.path.insert(0, 'src')

import pandas as pd
import numpy as np
import json
import os
import math
from datetime import datetime, timedelta
from pathlib import Path
from rich.console import Console
from rich.table import Table

console = Console()


def load_config():
    with open("config/config.json", "r") as f:
        return json.load(f)


def get_next_trading_day(date, df_daily):
    nd = date + timedelta(days=1)
    for _ in range(10):
        if nd in df_daily.index:
            return nd
        nd += timedelta(days=1)
    return None


def get_next_wednesday(d):
    if d.weekday() in [0, 1]:
        return d + timedelta(days=2 - d.weekday())
    return d + timedelta(days=7 - d.weekday() + 2)


def get_next_friday(d):
    if d.weekday() < 4:
        return d + timedelta(days=4 - d.weekday())
    return d + timedelta(days=7)


def get_next_monday(d):
    if d.weekday() == 4:
        return d + timedelta(days=3)
    return d + timedelta(days=7 - d.weekday())


def run_spread_bet_backtest(ticker, config, target_mult=0.1, exclude_fridays=False):
    """
    Backtest with spread bet (linear) P&L model - CORRECTED costs.

    All costs are computed as a FRACTION of underlying price,
    ensuring SPY and SPX give identical results.
    """
    console.print(f"\n[cyan]Running SPREAD BET v2 backtest for {ticker}...[/cyan]")

    daily_file = f'data/{ticker}/daily_OHLCV.parquet'
    if not os.path.exists(daily_file):
        console.print(f"[red]No daily data for {ticker}[/red]")
        return None

    df_daily = pd.read_parquet(daily_file)
    valid_days = df_daily[df_daily.index.dayofweek < 5]

    import pytz
    et_tz = pytz.timezone('America/New_York')

    # IG.com US 500 spread bet costs (as FRACTION of underlying price)
    # These are SCALE-INVARIANT: same whether computed on SPY or SPX
    ig_spread_frac = 0.4 / 6000   # 0.4 pts on SPX 6000 = 0.00667%
    slippage_frac = 0.2 / 6000    # 0.2 pts per side on SPX 6000 = 0.00333%
    overnight_funding_frac = 0.0002  # 0.02% per night

    # Total round-trip entry+exit cost (excluding funding)
    # Spread is paid once (built into bid/ask), slippage on each side
    round_trip_cost_frac = ig_spread_frac + (slippage_frac * 2)
    # = 0.00667% + 0.00667% = 0.01333% of price

    trades = []

    for i in range(len(valid_days)):
        day_t = valid_days.iloc[i]
        date_t = valid_days.index[i]
        date_str = date_t.strftime("%Y-%m-%d")
        day_of_week = date_t.dayofweek

        magnitude = abs((day_t['Close'] - day_t['Open']) / day_t['Open']) * 100
        if magnitude < 0.10:
            continue

        # Expiry schedule (consistent with all other files)
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
            if exclude_fridays:
                continue
            expiry_date = get_next_monday(date_t)
            expiry_label = "FRI-MON-3D"
            days_to_expiry = 3
        else:
            continue

        direction = "GREEN" if day_t['Close'] > day_t['Open'] else "RED"
        if direction == "GREEN":
            signal = "FADE_GREEN"
            bet_direction = "SHORT"
        else:
            signal = "FADE_RED"
            bet_direction = "LONG"

        entry_price = day_t['Close']
        atr = day_t['ATR_14']
        if pd.isna(atr):
            continue

        target_dist = atr * target_mult

        if bet_direction == "SHORT":
            target_price = entry_price - target_dist
        else:
            target_price = entry_price + target_dist

        # Scan intraday bars for target hit + find exit price if not hit
        target_hit = False
        exit_price = None
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

                    if check_date == date_t:
                        entry_dt = et_tz.localize(datetime(date_t.year, date_t.month, date_t.day, 16, 0))
                        df_window = df_intra[df_intra.index >= entry_dt]
                    elif check_date == expiry_date:
                        end_dt = et_tz.localize(datetime(expiry_date.year, expiry_date.month, expiry_date.day, 9, 30))
                        df_window = df_intra[df_intra.index <= end_dt]
                    else:
                        df_window = df_intra

                    if not df_window.empty:
                        if bet_direction == "SHORT":
                            if df_window['Low'].min() <= target_price:
                                target_hit = True
                                exit_price = target_price
                                exit_time = "target_hit"
                                break
                        else:
                            if df_window['High'].max() >= target_price:
                                target_hit = True
                                exit_price = target_price
                                exit_time = "target_hit"
                                break

                        # Track last known price for non-hit exit
                        exit_price = df_window.iloc[-1]['Close']
                        exit_time = str(df_window.index[-1])

                except Exception:
                    pass

            check_date = get_next_trading_day(check_date, df_daily)
            if check_date is None or check_date > expiry_date:
                break

        if exit_price is None:
            exit_price = entry_price  # no data

        # ----- SPREAD BET P&L (LINEAR) -----
        if bet_direction == "LONG":
            gross_move = exit_price - entry_price
        else:
            gross_move = entry_price - exit_price

        gross_pnl_frac = gross_move / entry_price

        # Costs (all as fractions of price, scale-invariant)
        trading_cost = round_trip_cost_frac
        funding_cost = overnight_funding_frac * days_to_expiry
        total_cost_frac = trading_cost + funding_cost

        net_pnl_frac = gross_pnl_frac - total_cost_frac

        result = "WIN" if target_hit else "LOSS"

        # For comparison: old hardcoded model
        old_pnl_mult = 0.45 if target_hit else -1.05

        trades.append({
            'Date': date_str,
            'Ticker': ticker,
            'Day_of_Week': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'][day_of_week],
            'Expiry_Label': expiry_label,
            'Days_To_Expiry': days_to_expiry,
            'Signal': signal,
            'Bet_Direction': bet_direction,
            'Direction': direction,
            'Entry_Price': entry_price,
            'Target_Price': target_price,
            'Exit_Price': exit_price,
            'Target_Dist_Pts': target_dist,
            'Target_Dist_Pct': (target_dist / entry_price) * 100,
            'ATR': atr,
            'Magnitude': magnitude,
            'Gross_Move_Pts': gross_move,
            'Gross_PnL_Pct': gross_pnl_frac * 100,
            'Trading_Cost_Pct': trading_cost * 100,
            'Funding_Cost_Pct': funding_cost * 100,
            'Total_Cost_Pct': total_cost_frac * 100,
            'Net_PnL_Pct': net_pnl_frac * 100,
            'Net_PnL_Frac': net_pnl_frac,
            'Net_PnL_Margin_Pct': net_pnl_frac * 20 * 100,  # 5% margin = 20x
            'Old_PnL_Mult': old_pnl_mult,
            'Result': result,
        })

    console.print(f"[green]{ticker}: {len(trades)} trades[/green]")
    return pd.DataFrame(trades)


def calculate_equity_curve(df, starting_capital, fraction_per_trade, pnl_column):
    """
    Calculate equity curve.

    fraction_per_trade: fraction of equity allocated per trade (e.g., 0.05 = 5%)
    P&L = equity * fraction * pnl_column_value (which is net_pnl as fraction of price)

    With 20x leverage (5% margin), allocating 5% of equity means:
      margin_used = equity * 0.05
      notional = margin_used * 20 = equity * 1.0
      P&L = notional * net_pnl_frac = equity * net_pnl_frac

    So fraction_per_trade * leverage = effective exposure.
    """
    equity = starting_capital
    equity_curve = []
    for _, row in df.iterrows():
        notional = equity * fraction_per_trade
        pnl = notional * row[pnl_column]
        equity += pnl
        equity = max(equity, 1.0)
        equity_curve.append(equity)
    return equity_curve


def main():
    console.print("=" * 80)
    console.print("[bold blue]BACKTEST v2: SPREAD BET ON UNDERLYING (corrected costs)[/bold blue]")
    console.print("=" * 80)
    console.print()
    console.print("[bold]Instrument: Spread bet on US 500 (IG.com)[/bold]")
    console.print("[bold]P&L model: LINEAR (no options, no theta, no Greeks)[/bold]")
    console.print()
    console.print("[bold]Key fixes vs v1:[/bold]")
    console.print("  1. Costs correctly scaled (0.4pts/6000 = 0.0067%, not 0.4pts/600)")
    console.print("  2. Losses are PARTIAL (actual move), not 100%")
    console.print("  3. Proper leverage analysis (5% margin = 20x)")
    console.print()
    console.print("[bold]Costs modelled:[/bold]")
    console.print("  IG spread: 0.4 pts on SPX 6000 = 0.0067% per side")
    console.print("  Slippage: 0.2 pts on SPX per side = 0.0033% per side")
    console.print("  Round-trip (spread + 2x slippage): 0.0133%")
    console.print("  Overnight funding: 0.02% per night")
    console.print()

    config = load_config()

    # =========================================================================
    # Run main backtest
    # =========================================================================
    df = run_spread_bet_backtest('SPY', config, target_mult=0.1, exclude_fridays=False)
    if df is None or df.empty:
        console.print("[red]No results[/red]")
        return

    df['Date_dt'] = pd.to_datetime(df['Date'])
    years = (df['Date_dt'].max() - df['Date_dt'].min()).days / 365.25

    console.print(f"\n[green]Total: {len(df):,} trades over {years:.1f} years[/green]\n")

    # =========================================================================
    # Core statistics
    # =========================================================================
    wins = df[df['Result'] == 'WIN']
    losses = df[df['Result'] == 'LOSS']
    win_rate = len(wins) / len(df) * 100

    console.print("=" * 80)
    console.print("[bold white]CORE STATISTICS[/bold white]")
    console.print("=" * 80)
    console.print()

    console.print(f"  Total trades:   {len(df):,}")
    console.print(f"  Wins:           {len(wins):,} ({win_rate:.1f}%)")
    console.print(f"  Losses:         {len(losses):,} ({100-win_rate:.1f}%)")
    console.print()

    # P&L as % of underlying price (unleveraged)
    console.print("[bold]P&L as % of underlying price (unleveraged):[/bold]")
    if len(wins) > 0:
        console.print(f"  Avg WIN gross:   {wins['Gross_PnL_Pct'].mean():+.4f}%")
        console.print(f"  Avg WIN net:     {wins['Net_PnL_Pct'].mean():+.4f}%")
    if len(losses) > 0:
        console.print(f"  Avg LOSS gross:  {losses['Gross_PnL_Pct'].mean():+.4f}%")
        console.print(f"  Avg LOSS net:    {losses['Net_PnL_Pct'].mean():+.4f}%")
    ev_unleveraged = df['Net_PnL_Pct'].mean()
    console.print(f"  [bold]EV per trade:  {ev_unleveraged:+.4f}%[/bold]")
    console.print()

    # P&L as % of margin (5% margin = 20x leverage)
    console.print("[bold]P&L as % of MARGIN (5% margin = 20x leverage):[/bold]")
    if len(wins) > 0:
        console.print(f"  Avg WIN:   {wins['Net_PnL_Margin_Pct'].mean():+.2f}%")
    if len(losses) > 0:
        console.print(f"  Avg LOSS:  {losses['Net_PnL_Margin_Pct'].mean():+.2f}%")
    ev_margin = df['Net_PnL_Margin_Pct'].mean()
    console.print(f"  [bold]EV per trade (on margin):  {ev_margin:+.2f}%[/bold]")
    console.print()

    # Cost analysis
    avg_cost = df['Total_Cost_Pct'].mean()
    avg_gross = df['Gross_PnL_Pct'].mean()
    console.print(f"  Avg total cost per trade: {avg_cost:.4f}%")
    console.print(f"  Avg gross P&L per trade:  {avg_gross:+.4f}%")
    console.print(f"  Cost as % of gross win:   {avg_cost / wins['Gross_PnL_Pct'].mean() * 100:.1f}%" if len(wins) > 0 and wins['Gross_PnL_Pct'].mean() > 0 else "")
    console.print()

    # =========================================================================
    # Equity curves at different leverage/position sizing
    # =========================================================================
    console.print("=" * 80)
    console.print("[bold white]EQUITY CURVES (different position sizing)[/bold white]")
    console.print("=" * 80)
    console.print()

    starting_capital = 10000

    # Scenario 1: Full Kelly on notional (aggressive)
    # Kelly = (p*b - q) / b where p=win_rate, b=avg_win/avg_loss, q=1-p
    p = win_rate / 100
    avg_win_frac = wins['Net_PnL_Frac'].mean() if len(wins) > 0 else 0
    avg_loss_frac = abs(losses['Net_PnL_Frac'].mean()) if len(losses) > 0 else 1
    if avg_loss_frac > 0 and avg_win_frac > 0:
        b = avg_win_frac / avg_loss_frac
        kelly = max((p * b - (1 - p)) / b, 0)
    else:
        kelly = 0

    scenarios = [
        ("Conservative (5% of equity as notional)", 0.05),
        ("Moderate (20% of equity as notional)", 0.20),
        ("Aggressive (50% of equity as notional)", 0.50),
        ("Full notional (100% = all equity exposed)", 1.0),
        (f"Kelly ({kelly*100:.1f}% of equity as notional)", kelly),
    ]

    eq_table = Table(show_header=True, header_style="bold cyan")
    eq_table.add_column("Scenario", width=45)
    eq_table.add_column("Fraction", justify="right", width=10)
    eq_table.add_column("Final Equity", justify="right", width=15)
    eq_table.add_column("CAGR", justify="right", width=10)
    eq_table.add_column("Max DD", justify="right", width=10)

    best_eq = None
    for label, frac in scenarios:
        if frac <= 0:
            eq_table.add_row(label, f"{frac:.1%}", "N/A", "N/A", "N/A")
            continue
        eq = calculate_equity_curve(df, starting_capital, frac, 'Net_PnL_Frac')
        final = eq[-1]
        if final > starting_capital:
            cagr = (pow(final / starting_capital, 1 / years) - 1) * 100
        else:
            cagr = -((1 - pow(max(final, 0.01) / starting_capital, 1 / years)) * 100)

        running_max = pd.Series(eq).expanding().max()
        max_dd = ((pd.Series(eq) - running_max) / running_max * 100).min()

        style = "green" if cagr > 0 else "red"
        eq_table.add_row(
            label, f"{frac:.1%}",
            f"${final:,.0f}",
            f"[{style}]{cagr:+.1f}%[/{style}]",
            f"{max_dd:.1f}%"
        )

        if label.startswith("Full notional"):
            best_eq = eq

    console.print(eq_table)
    console.print()

    # Also show with the old hardcoded model for comparison
    eq_old = calculate_equity_curve(df, starting_capital, 0.0523, 'Old_PnL_Mult')
    final_old = eq_old[-1]
    cagr_old = (pow(final_old / starting_capital, 1 / years) - 1) * 100

    console.print(f"  [dim]Old hardcoded model (5.23% Kelly, +45%/-105%): ${final_old:,.0f} ({cagr_old:+.1f}% CAGR)[/dim]")
    console.print()

    # =========================================================================
    # Breakdown by expiry pattern
    # =========================================================================
    console.print("=" * 80)
    console.print("[bold white]RETURNS BY EXPIRY PATTERN[/bold white]")
    console.print("=" * 80)
    console.print()

    exp_table = Table(show_header=True, header_style="bold cyan")
    exp_table.add_column("Pattern", width=15)
    exp_table.add_column("Trades", justify="right", width=8)
    exp_table.add_column("Win Rate", justify="right", width=10)
    exp_table.add_column("Avg Win", justify="right", width=12)
    exp_table.add_column("Avg Loss", justify="right", width=12)
    exp_table.add_column("EV (price)", justify="right", width=12)
    exp_table.add_column("EV (margin)", justify="right", width=12)

    for label in sorted(df['Expiry_Label'].unique()):
        sub = df[df['Expiry_Label'] == label]
        wr = (sub['Result'] == 'WIN').sum() / len(sub) * 100
        avg_win = sub[sub['Result'] == 'WIN']['Net_PnL_Pct'].mean() if (sub['Result'] == 'WIN').sum() > 0 else 0
        avg_loss = sub[sub['Result'] == 'LOSS']['Net_PnL_Pct'].mean() if (sub['Result'] == 'LOSS').sum() > 0 else 0
        ev_trade = sub['Net_PnL_Pct'].mean()
        ev_margin = sub['Net_PnL_Margin_Pct'].mean()
        style = "green" if ev_trade > 0 else "red"

        exp_table.add_row(
            label, f"{len(sub):,}", f"{wr:.1f}%",
            f"{avg_win:+.4f}%", f"{avg_loss:+.4f}%",
            f"[{style}]{ev_trade:+.4f}%[/{style}]",
            f"[{style}]{ev_margin:+.2f}%[/{style}]"
        )

    console.print(exp_table)
    console.print()

    # =========================================================================
    # Breakdown by day direction (GREEN vs RED)
    # =========================================================================
    console.print("=" * 80)
    console.print("[bold white]RETURNS BY DAY DIRECTION[/bold white]")
    console.print("=" * 80)
    console.print()

    for dir_val in ["GREEN", "RED"]:
        sub = df[df['Direction'] == dir_val]
        if len(sub) == 0:
            continue
        wr = (sub['Result'] == 'WIN').sum() / len(sub) * 100
        ev = sub['Net_PnL_Pct'].mean()
        ev_m = sub['Net_PnL_Margin_Pct'].mean()
        style = "green" if ev > 0 else "red"
        action = "SHORT (fade down)" if dir_val == "GREEN" else "LONG (fade up)"
        console.print(f"  {dir_val} days ({action}): {len(sub):,} trades, {wr:.1f}% WR, EV=[{style}]{ev:+.4f}%[/{style}] (margin: [{style}]{ev_m:+.2f}%[/{style}])")

    console.print()

    # =========================================================================
    # Breakdown by magnitude bins
    # =========================================================================
    console.print("=" * 80)
    console.print("[bold white]RETURNS BY MAGNITUDE BIN[/bold white]")
    console.print("=" * 80)
    console.print()

    mag_bins = [(0.1, 0.3), (0.3, 0.5), (0.5, 0.8), (0.8, 1.2), (1.2, 2.0), (2.0, 10.0)]
    mag_table = Table(show_header=True, header_style="bold cyan")
    mag_table.add_column("Magnitude", width=15)
    mag_table.add_column("Trades", justify="right", width=8)
    mag_table.add_column("Win Rate", justify="right", width=10)
    mag_table.add_column("EV (price)", justify="right", width=12)
    mag_table.add_column("EV (margin)", justify="right", width=12)

    for lo, hi in mag_bins:
        sub = df[(df['Magnitude'] >= lo) & (df['Magnitude'] < hi)]
        if len(sub) == 0:
            continue
        wr = (sub['Result'] == 'WIN').sum() / len(sub) * 100
        ev = sub['Net_PnL_Pct'].mean()
        ev_m = sub['Net_PnL_Margin_Pct'].mean()
        style = "green" if ev > 0 else "red"
        mag_table.add_row(
            f"{lo:.1f}% - {hi:.1f}%", f"{len(sub):,}", f"{wr:.1f}%",
            f"[{style}]{ev:+.4f}%[/{style}]",
            f"[{style}]{ev_m:+.2f}%[/{style}]"
        )

    console.print(mag_table)
    console.print()

    # =========================================================================
    # Breakeven analysis
    # =========================================================================
    console.print("=" * 80)
    console.print("[bold white]BREAKEVEN ANALYSIS[/bold white]")
    console.print("=" * 80)
    console.print()

    if len(wins) > 0 and len(losses) > 0:
        avg_w = wins['Net_PnL_Frac'].mean()
        avg_l = abs(losses['Net_PnL_Frac'].mean())
        breakeven_wr = avg_l / (avg_w + avg_l) * 100

        console.print(f"  Avg net WIN (as frac of price):   {avg_w:+.6f}")
        console.print(f"  Avg net LOSS (as frac of price):  {-avg_l:+.6f}")
        console.print(f"  Win/Loss ratio:                   {avg_w/avg_l:.3f}")
        console.print()
        console.print(f"  [bold]Breakeven win rate:  {breakeven_wr:.1f}%[/bold]")
        console.print(f"  [bold]Actual win rate:     {win_rate:.1f}%[/bold]")
        console.print()

        if win_rate > breakeven_wr:
            console.print(f"  [bold green]POSITIVE EDGE: actual WR ({win_rate:.1f}%) > breakeven ({breakeven_wr:.1f}%)[/bold green]")
            edge = (win_rate - breakeven_wr)
            console.print(f"  [bold green]Edge: +{edge:.1f} percentage points above breakeven[/bold green]")
        else:
            console.print(f"  [bold red]NEGATIVE EDGE: actual WR ({win_rate:.1f}%) < breakeven ({breakeven_wr:.1f}%)[/bold red]")
            deficit = (breakeven_wr - win_rate)
            console.print(f"  [bold red]Deficit: -{deficit:.1f} percentage points below breakeven[/bold red]")

    console.print()

    # =========================================================================
    # TRADING SIZE ANALYSIS
    # =========================================================================
    console.print("=" * 80)
    console.print("[bold white]TRADING SIZE ANALYSIS[/bold white]")
    console.print("=" * 80)
    console.print()

    # Backtest runs on SPY data. All values below are in SPY scale.
    # For IG.com US 500 (SPX), the live auto_trade_ig.py fetches SPX
    # prices directly from yfinance — no conversion needed here.

    avg_atr = df['ATR'].mean()
    avg_target = avg_atr * 0.1

    console.print(f"  Average ATR (SPY): {avg_atr:.2f} pts")
    console.print(f"  Average target (0.1*ATR): {avg_target:.2f} pts")
    console.print(f"  Average target as % of price: {df['Target_Dist_Pct'].mean():.4f}%")
    console.print()

    if len(wins) > 0:
        avg_win_pts = wins['Gross_Move_Pts'].mean()
        console.print(f"  Avg WIN move: +{avg_win_pts:.2f} pts ({wins['Gross_PnL_Pct'].mean():+.4f}% of price)")

    if len(losses) > 0:
        avg_loss_pts = losses['Gross_Move_Pts'].mean()
        console.print(f"  Avg LOSS move: {avg_loss_pts:.2f} pts ({losses['Gross_PnL_Pct'].mean():+.4f}% of price)")

    console.print()
    console.print("[bold]Key advantage over options:[/bold]")
    console.print("  - Losses are PARTIAL (actual price move), not 100% of premium")
    console.print("  - No theta decay eating your profits")
    console.print("  - 86% directional accuracy applies directly")
    console.print("  - Note: For IG US 500 (SPX), use auto_trade_ig.py for live prices")
    console.print()

    # =========================================================================
    # Target size sweep (on same data)
    # =========================================================================
    console.print("=" * 80)
    console.print("[bold white]TARGET SIZE SWEEP (same data, different targets)[/bold white]")
    console.print("=" * 80)
    console.print()

    sweep_table = Table(show_header=True, header_style="bold cyan")
    sweep_table.add_column("Target", width=12)
    sweep_table.add_column("Trades", justify="right", width=8)
    sweep_table.add_column("Win Rate", justify="right", width=10)
    sweep_table.add_column("EV (price)", justify="right", width=12)
    sweep_table.add_column("EV (margin)", justify="right", width=12)
    sweep_table.add_column("CAGR (100% exp)", justify="right", width=14)

    for target in [0.05, 0.075, 0.1, 0.125, 0.15, 0.2, 0.3]:
        df_sweep = run_spread_bet_backtest('SPY', config, target_mult=target, exclude_fridays=False)
        if df_sweep is None or df_sweep.empty:
            continue
        wr = (df_sweep['Result'] == 'WIN').sum() / len(df_sweep) * 100
        ev = df_sweep['Net_PnL_Pct'].mean()
        ev_m = df_sweep['Net_PnL_Margin_Pct'].mean()

        eq = calculate_equity_curve(df_sweep, starting_capital, 1.0, 'Net_PnL_Frac')
        final = eq[-1]
        yrs = (pd.to_datetime(df_sweep['Date']).max() - pd.to_datetime(df_sweep['Date']).min()).days / 365.25
        if final > starting_capital:
            cagr = (pow(final / starting_capital, 1 / yrs) - 1) * 100
        else:
            cagr = -((1 - pow(max(final, 0.01) / starting_capital, 1 / yrs)) * 100)

        style = "green" if ev > 0 else "red"
        sweep_table.add_row(
            f"{target:.3f}xATR", f"{len(df_sweep):,}", f"{wr:.1f}%",
            f"[{style}]{ev:+.4f}%[/{style}]",
            f"[{style}]{ev_m:+.2f}%[/{style}]",
            f"[{style}]{cagr:+.1f}%[/{style}]"
        )

    console.print(sweep_table)
    console.print()

    # =========================================================================
    # Exclude Fridays analysis
    # =========================================================================
    console.print("=" * 80)
    console.print("[bold white]WITH vs WITHOUT FRIDAYS[/bold white]")
    console.print("=" * 80)
    console.print()

    df_no_fri = run_spread_bet_backtest('SPY', config, target_mult=0.1, exclude_fridays=True)
    if df_no_fri is not None and not df_no_fri.empty:
        wr_nf = (df_no_fri['Result'] == 'WIN').sum() / len(df_no_fri) * 100
        ev_nf = df_no_fri['Net_PnL_Pct'].mean()
        ev_m_nf = df_no_fri['Net_PnL_Margin_Pct'].mean()

        eq_nf = calculate_equity_curve(df_no_fri, starting_capital, 1.0, 'Net_PnL_Frac')
        final_nf = eq_nf[-1]
        yrs_nf = (pd.to_datetime(df_no_fri['Date']).max() - pd.to_datetime(df_no_fri['Date']).min()).days / 365.25
        if final_nf > starting_capital:
            cagr_nf = (pow(final_nf / starting_capital, 1 / yrs_nf) - 1) * 100
        else:
            cagr_nf = -((1 - pow(max(final_nf, 0.01) / starting_capital, 1 / yrs_nf)) * 100)

        console.print(f"  With Fridays:    {len(df):,} trades, {win_rate:.1f}% WR, EV={ev_unleveraged:+.4f}%")
        console.print(f"  Without Fridays: {len(df_no_fri):,} trades, {wr_nf:.1f}% WR, EV={ev_nf:+.4f}%")
        console.print()

    # =========================================================================
    # Save results
    # =========================================================================
    output_file = 'results/spread_bet_v2_backtest.csv'
    os.makedirs('results', exist_ok=True)
    df.to_csv(output_file, index=False)
    console.print(f"[green]Full results saved to: {output_file}[/green]")
    console.print()

    # =========================================================================
    # VERDICT
    # =========================================================================
    console.print("=" * 80)
    console.print("[bold white]VERDICT: SPREAD BET ON UNDERLYING[/bold white]")
    console.print("=" * 80)
    console.print()

    if ev_unleveraged > 0:
        console.print(f"[bold green]POSITIVE EXPECTED VALUE: {ev_unleveraged:+.4f}% per trade (unleveraged)[/bold green]")
        console.print(f"[bold green]On 5% margin (20x leverage): {ev_unleveraged * 20:+.2f}% per trade[/bold green]")
        console.print()
        console.print("[bold]Why spread bets work when options don't:[/bold]")
        console.print("  1. NO theta decay - the 86% directional accuracy translates directly")
        console.print("  2. Losses are PARTIAL (actual price move), not 100% of premium")
        console.print("  3. Costs are tiny (0.013% round-trip vs 5%+ on option spreads)")
        console.print("  4. Win/loss RATIO is favorable (win ~ target, loss ~ partial move)")
    else:
        console.print(f"[bold red]NEGATIVE EXPECTED VALUE: {ev_unleveraged:+.4f}% per trade (unleveraged)[/bold red]")
        console.print(f"[bold red]On 5% margin: {ev_unleveraged * 20:+.2f}% per trade[/bold red]")
        console.print()
        console.print("[bold]The strategy does not have positive edge even with linear P&L.[/bold]")

    console.print()

    # Compare with option results
    console.print("[bold]Comparison with option-based execution:[/bold]")
    console.print(f"  Spread bet: {win_rate:.1f}% WR, EV = {ev_unleveraged:+.4f}% per trade")
    console.print(f"  Options:    ~55% limit-hit rate, EV = -26% per trade (from option_limit backtest)")
    console.print(f"  Old model:  86% WR, +45%/-105% hardcoded -> massively overstated")
    console.print()
    console.print("=" * 80)


if __name__ == "__main__":
    main()
