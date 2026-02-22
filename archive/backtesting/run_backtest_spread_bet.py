"""
Backtest for SPREAD BET execution (IG.com US 500)

This is the CORRECT pricing model for how the strategy is actually traded.

Spread bet P&L is LINEAR:
    P&L = (exit_price - entry_price) * stake_per_point

No theta. No IV. No Greeks. Just direction and magnitude.

The key costs are:
    - IG spread (bid/ask gap on the underlying, not options)
    - Overnight funding (small, ~0.02% per night)
    - Slippage at entry/exit

The strategy:
    1. Enter at close (buy/sell spread bet on US 500)
    2. Set limit order at close +/- 0.1x ATR
    3. If limit hit -> take profit
    4. If not hit by expiry window -> close at market (loss)

We measure P&L as percentage of MARGIN required (not notional).
IG margin for US 500 spread bet: typically 5% of notional.

But the original backtest measured P&L as % of position size allocated.
To be comparable, we measure P&L as move / entry_price (same as stock return).
The LEVERAGE comes from position sizing (Kelly fraction of equity).
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


def run_spread_bet_backtest(ticker, config):
    """
    Backtest with spread bet (linear) P&L model.

    For each trade:
    - Entry at close price
    - Target = close +/- (0.1 * ATR)
    - Scan intraday bars for target hit
    - If hit: P&L = target_dist / entry_price (always positive)
    - If not hit: P&L = (close_at_expiry - entry) / entry (direction-dependent)

    Costs:
    - IG spread on US 500: ~0.4 points (bid/ask)
    - That's 0.4 / 6000 = 0.007% per side = ~0.013% round-trip
    - Overnight funding: ~0.02% per night
    - Slippage: assume 0.2 points per side = 0.003% per side
    """
    console.print(f"\n[cyan]Running SPREAD BET backtest for {ticker}...[/cyan]")

    daily_file = f'data/{ticker}/daily_OHLCV.parquet'
    if not os.path.exists(daily_file):
        console.print(f"[red]No daily data for {ticker}[/red]")
        return None

    df_daily = pd.read_parquet(daily_file)
    valid_days = df_daily[df_daily.index.dayofweek < 5]

    import pytz
    et_tz = pytz.timezone('America/New_York')

    # IG.com spread bet costs for US 500
    # Spread: 0.4 pts on US 500 (SPX ~6000) = 0.4/6000 per side
    # We'll calculate dynamically based on entry price
    ig_spread_pts = 0.4  # IG spread for US 500 in points
    overnight_funding_pct = 0.0002  # 0.02% per night
    slippage_pts = 0.2  # per side slippage estimate

    target_mult = config.get('default_take_profit_atr', 0.1)

    trades = []

    for i in range(len(valid_days)):
        day_t = valid_days.iloc[i]
        date_t = valid_days.index[i]
        date_str = date_t.strftime("%Y-%m-%d")
        day_of_week = date_t.dayofweek

        magnitude = abs((day_t['Close'] - day_t['Open']) / day_t['Open']) * 100
        if magnitude < 0.10:
            continue

        # Expiry schedule (same as original)
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

        direction = "GREEN" if day_t['Close'] > day_t['Open'] else "RED"
        if direction == "GREEN":
            signal = "FADE_GREEN"  # go SHORT the index
            bet_direction = "SHORT"
        else:
            signal = "FADE_RED"  # go LONG the index
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
                                break
                        else:
                            if df_window['High'].max() >= target_price:
                                target_hit = True
                                exit_price = target_price
                                break

                        # Track last known price for non-hit exit
                        exit_price = df_window.iloc[-1]['Close']

                except Exception:
                    pass

            check_date = get_next_trading_day(check_date, df_daily)
            if check_date is None or check_date > expiry_date:
                break

        # If no exit price found, use entry (no data available)
        if exit_price is None:
            exit_price = entry_price

        # ----- SPREAD BET P&L (LINEAR) -----
        if bet_direction == "LONG":
            gross_move = exit_price - entry_price
        else:  # SHORT
            gross_move = entry_price - exit_price

        gross_pnl_pct = gross_move / entry_price

        # Costs (all in price-point terms, then convert to %)
        # IG spread: half on entry, half on exit
        spread_cost = ig_spread_pts / entry_price  # as fraction of price
        # Slippage: both sides
        slippage_cost = (slippage_pts * 2) / entry_price
        # Overnight funding
        funding_cost = overnight_funding_pct * days_to_expiry

        total_costs = spread_cost + slippage_cost + funding_cost
        net_pnl_pct = gross_pnl_pct - total_costs

        result = "WIN" if target_hit else "LOSS"

        # ALSO compute what the old hardcoded model said
        old_pnl_mult = 0.45 if target_hit else -1.05

        trades.append({
            'Date': date_str,
            'Ticker': ticker,
            'Day_of_Week': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'][day_of_week],
            'Expiry_Label': expiry_label,
            'Days_To_Expiry': days_to_expiry,
            'Signal': signal,
            'Bet_Direction': bet_direction,
            'Entry_Price': entry_price,
            'Target_Price': target_price,
            'Exit_Price': exit_price,
            'Target_Dist': target_dist,
            'ATR': atr,
            'Gross_Move': gross_move,
            'Gross_PnL_Pct': gross_pnl_pct * 100,
            'Costs_Pct': total_costs * 100,
            'Net_PnL_Pct': net_pnl_pct * 100,
            'PnL_Mult_SpreadBet': net_pnl_pct,
            'PnL_Mult_Old': old_pnl_mult,
            'Result': result,
            'Direction': direction,
            'Magnitude': magnitude,
        })

    console.print(f"[green]{ticker}: {len(trades)} trades[/green]")
    return pd.DataFrame(trades)


def calculate_equity_curve(df, starting_capital, kelly_pct, max_position, pnl_column):
    equity = starting_capital
    equity_curve = []
    for _, row in df.iterrows():
        position = min(equity * kelly_pct, max_position)
        pnl = position * row[pnl_column]
        equity += pnl
        equity = max(equity, 1.0)
        equity_curve.append(equity)
    return equity_curve


def main():
    console.print("=" * 80)
    console.print("[bold blue]BACKTEST: SPREAD BET (LINEAR P&L) - How you actually trade[/bold blue]")
    console.print("=" * 80)
    console.print()
    console.print("[bold]This uses the CORRECT instrument model:[/bold]")
    console.print("  Spread bet on US 500 (IG.com)")
    console.print("  P&L = price_move * stake_per_point")
    console.print("  NO theta, NO IV, NO Greeks - purely linear")
    console.print()
    console.print("[bold]Costs modelled:[/bold]")
    console.print("  IG spread: ~0.4 pts on US 500")
    console.print("  Slippage: ~0.2 pts per side")
    console.print("  Overnight funding: ~0.02%/night")
    console.print()

    config = load_config()
    tickers = config.get('tickers', ['SPY'])

    all_results = []
    for ticker in tickers:
        result = run_spread_bet_backtest(ticker, config)
        if result is not None and not result.empty:
            all_results.append(result)

    if not all_results:
        console.print("[red]No results[/red]")
        return

    df = pd.concat(all_results, ignore_index=True)
    df = df.sort_values('Date').reset_index(drop=True)

    console.print(f"\n[green]Total: {len(df):,} trades[/green]\n")

    # Equity curves
    starting_capital = 10000
    kelly_pct = 0.0523
    max_position = 1000

    eq_sb = calculate_equity_curve(df, starting_capital, kelly_pct, max_position, 'PnL_Mult_SpreadBet')
    eq_old = calculate_equity_curve(df, starting_capital, kelly_pct, max_position, 'PnL_Mult_Old')

    df['Equity_SpreadBet'] = eq_sb
    df['Equity_Old'] = eq_old

    # Stats
    df['Date'] = pd.to_datetime(df['Date'])
    years = (df['Date'].max() - df['Date'].min()).days / 365.25

    final_sb = eq_sb[-1]
    final_old = eq_old[-1]

    cagr_sb = (pow(final_sb / starting_capital, 1 / years) - 1) * 100 if final_sb > starting_capital else -((1 - pow(final_sb / starting_capital, 1 / years)) * 100)
    cagr_old = (pow(final_old / starting_capital, 1 / years) - 1) * 100

    wins = (df['Result'] == 'WIN').sum()
    losses = (df['Result'] == 'LOSS').sum()
    win_rate = wins / len(df) * 100

    # Drawdown
    running_max = pd.Series(eq_sb).expanding().max()
    drawdown = (pd.Series(eq_sb) - running_max) / running_max * 100
    max_dd = drawdown.min()

    # ---- Results ----
    console.print("=" * 80)
    console.print("[bold white]RESULTS: SPREAD BET (actual) vs OLD HARDCODED[/bold white]")
    console.print("=" * 80)
    console.print()

    comp = Table(show_header=True, header_style="bold cyan")
    comp.add_column("Metric", style="white", width=30)
    comp.add_column("Spread Bet (ACTUAL)", justify="right", width=22)
    comp.add_column("Hardcoded (OLD)", justify="right", width=22)

    comp.add_row("Period", f"{df['Date'].min().strftime('%Y-%m-%d')} to {df['Date'].max().strftime('%Y-%m-%d')}", "")
    comp.add_row("Years", f"{years:.1f}", "")
    comp.add_row("Total Trades", f"{len(df):,}", f"{len(df):,}")
    comp.add_row("Wins (target hit)", f"{wins:,} ({win_rate:.1f}%)", f"{wins:,} ({win_rate:.1f}%)")
    comp.add_row("", "", "")
    comp.add_row("Starting Capital", f"${starting_capital:,.0f}", f"${starting_capital:,.0f}")
    comp.add_row("Final Equity", f"[bold]${final_sb:,.0f}[/bold]", f"${final_old:,.0f}")
    comp.add_row("CAGR", f"[bold]{cagr_sb:+.1f}%[/bold]", f"{cagr_old:.1f}%")
    comp.add_row("Max Drawdown", f"{max_dd:.1f}%", "N/A")

    console.print(comp)
    console.print()

    # P&L distribution
    console.print("=" * 80)
    console.print("[bold white]SPREAD BET P&L DISTRIBUTION[/bold white]")
    console.print("=" * 80)
    console.print()

    wins_df = df[df['Result'] == 'WIN']
    losses_df = df[df['Result'] == 'LOSS']

    if len(wins_df) > 0:
        console.print(f"[green]WINS ({len(wins_df)} trades):[/green]")
        console.print(f"  Avg gross P&L:  {wins_df['Gross_PnL_Pct'].mean():+.3f}%")
        console.print(f"  Avg costs:      {wins_df['Costs_Pct'].mean():.3f}%")
        console.print(f"  Avg net P&L:    {wins_df['Net_PnL_Pct'].mean():+.3f}%")
        console.print(f"  Median net:     {wins_df['Net_PnL_Pct'].median():+.3f}%")
        console.print()

    if len(losses_df) > 0:
        console.print(f"[red]LOSSES ({len(losses_df)} trades):[/red]")
        console.print(f"  Avg gross P&L:  {losses_df['Gross_PnL_Pct'].mean():+.3f}%")
        console.print(f"  Avg net P&L:    {losses_df['Net_PnL_Pct'].mean():+.3f}%")
        console.print(f"  Median net:     {losses_df['Net_PnL_Pct'].median():+.3f}%")
        console.print()

    ev = df['Net_PnL_Pct'].mean()
    console.print(f"[bold]Overall EV per trade: {ev:+.4f}%[/bold]")
    console.print()

    # KEY: Compare win size (spread bet) vs old assumption
    console.print("=" * 80)
    console.print("[bold white]KEY COMPARISON: What does a 'win' actually pay?[/bold white]")
    console.print("=" * 80)
    console.print()

    console.print(f"  Old backtest assumed:    WIN = [bold]+45.000%[/bold]")
    if len(wins_df) > 0:
        console.print(f"  Spread bet actual:       WIN = [bold]{wins_df['Net_PnL_Pct'].mean():+.3f}%[/bold]")
        console.print(f"  Old assumption is [bold]{0.45 / (wins_df['Net_PnL_Pct'].mean()/100):.0f}x[/bold] too high")
    console.print()

    if len(losses_df) > 0:
        console.print(f"  Old backtest assumed:    LOSS = [bold]-105.000%[/bold]")
        console.print(f"  Spread bet actual:       LOSS = [bold]{losses_df['Net_PnL_Pct'].mean():+.3f}%[/bold]")
        console.print()

    console.print("[bold yellow]IMPORTANT:[/bold yellow] The spread bet P&L per trade is MUCH smaller")
    console.print("than the old +45%/-105%, because it's a % of underlying price,")
    console.print("not a % of option premium. The LEVERAGE comes from position sizing")
    console.print("and IG margin requirements (typically 5% margin = 20x leverage).")
    console.print()

    # Breakdown by expiry
    console.print("=" * 80)
    console.print("[bold white]RETURNS BY EXPIRY PATTERN[/bold white]")
    console.print("=" * 80)
    console.print()

    exp_table = Table(show_header=True, header_style="bold cyan")
    exp_table.add_column("Pattern", width=15)
    exp_table.add_column("Trades", justify="right", width=8)
    exp_table.add_column("Win Rate", justify="right", width=10)
    exp_table.add_column("Avg Win", justify="right", width=10)
    exp_table.add_column("Avg Loss", justify="right", width=10)
    exp_table.add_column("EV/trade", justify="right", width=10)

    for label in sorted(df['Expiry_Label'].unique()):
        sub = df[df['Expiry_Label'] == label]
        wr = (sub['Result'] == 'WIN').sum() / len(sub) * 100
        avg_win = sub[sub['Result'] == 'WIN']['Net_PnL_Pct'].mean() if (sub['Result'] == 'WIN').sum() > 0 else 0
        avg_loss = sub[sub['Result'] == 'LOSS']['Net_PnL_Pct'].mean() if (sub['Result'] == 'LOSS').sum() > 0 else 0
        ev_trade = sub['Net_PnL_Pct'].mean()
        ev_style = "green" if ev_trade > 0 else "red"

        exp_table.add_row(
            label, f"{len(sub):,}", f"{wr:.1f}%",
            f"{avg_win:+.3f}%", f"{avg_loss:+.3f}%",
            f"[{ev_style}]{ev_trade:+.4f}%[/{ev_style}]"
        )

    console.print(exp_table)
    console.print()

    # ---- Leverage analysis ----
    console.print("=" * 80)
    console.print("[bold white]LEVERAGE ANALYSIS (IG.com margin)[/bold white]")
    console.print("=" * 80)
    console.print()

    console.print("On IG.com, US 500 spread bet margin = 5% of notional")
    console.print("So GBP 1/pt on US 500 at 6000 = GBP 300 margin")
    console.print()

    # Recalculate with 20x leverage (5% margin)
    leverage = 20
    if len(wins_df) > 0 and len(losses_df) > 0:
        avg_win_leveraged = wins_df['Net_PnL_Pct'].mean() * leverage
        avg_loss_leveraged = losses_df['Net_PnL_Pct'].mean() * leverage

        console.print(f"  Leveraged avg win (on margin):  {avg_win_leveraged:+.1f}%")
        console.print(f"  Leveraged avg loss (on margin): {avg_loss_leveraged:+.1f}%")
        console.print(f"  Leveraged EV (on margin):       {ev * leverage:+.2f}%")
        console.print()

        # THIS is what your 29% corresponds to
        console.print(f"  [bold]Your reported ~29% return on wins at 20x leverage:[/bold]")
        console.print(f"  [bold]Matches a {29/leverage:.2f}% move on underlying[/bold]")
        console.print(f"  [bold]= {29/leverage/100 * 6000:.1f} points on SPX 6000[/bold]")
        console.print()

    # Save
    output_file = 'results/spread_bet_backtest.csv'
    df.to_csv(output_file, index=False)
    console.print(f"[green]Full results saved to: {output_file}[/green]")
    console.print()

    # Verdict
    console.print("=" * 80)
    console.print("[bold white]VERDICT[/bold white]")
    console.print("=" * 80)
    console.print()

    if cagr_sb > 0:
        console.print(f"[bold green]Strategy is PROFITABLE as a spread bet: {cagr_sb:+.1f}% CAGR[/bold green]")
    else:
        console.print(f"[bold red]Strategy is NEGATIVE as a spread bet: {cagr_sb:+.1f}% CAGR[/bold red]")

    console.print()
    console.print(f"  Old backtest CAGR (hardcoded +45%/-105%): {cagr_old:.1f}%")
    console.print(f"  Spread bet CAGR (actual linear P&L):      {cagr_sb:+.1f}%")
    console.print()

    if abs(cagr_old - cagr_sb) > 10:
        console.print("[bold yellow]NOTE: The old hardcoded model significantly overstates returns.[/bold yellow]")
        console.print("[bold yellow]The +45% win / -105% loss assumption doesn't match spread bet reality.[/bold yellow]")
        console.print("[bold yellow]Actual win/loss sizes on the underlying are much smaller (~0.1% vs 45%).[/bold yellow]")
        console.print("[bold yellow]Leverage amplifies them, but the old model's numbers are still wrong.[/bold yellow]")

    console.print()
    console.print("=" * 80)


if __name__ == "__main__":
    main()
