"""
Backtest for IG.com ALL DAYS Strategy

Trading Schedule (using weekly expiries: Mon/Wed/Fri):
- Monday 16:00 ET -> Wednesday expiry (2-day)
- Tuesday 16:00 ET -> Wednesday expiry (1-day overnight)
- Wednesday 16:00 ET -> Friday expiry (2-day)
- Thursday 16:00 ET -> Friday expiry (1-day overnight)
- Friday 16:00 ET -> TWO TRADES:
  1. Monday expiry (3-day weekend)
  2. Next Friday expiry (7-day weekly)

Position Sizing: Capped at $1000 per ticker (Friday has 2 positions per ticker)

Key: Maximize trading frequency using all available IG.com weekly expiries!
"""
import sys
sys.path.insert(0, 'src')

import pandas as pd
import numpy as np
import json
import os
from datetime import datetime, timedelta
from rich.console import Console
from rich.table import Table

console = Console()

def load_config():
    with open("config/config.json", "r") as f:
        return json.load(f)

def get_next_trading_day(date, df_daily):
    """Get next trading day from date."""
    next_date = date + timedelta(days=1)
    for _ in range(10):
        if next_date in df_daily.index:
            return next_date
        next_date += timedelta(days=1)
    return None

def get_next_wednesday(date):
    """Get next Wednesday from date."""
    # If today is Mon/Tue, get this week's Wednesday
    # If today is Wed/Thu/Fri, get next week's Wednesday
    days_ahead = (2 - date.weekday()) % 7  # 2 = Wednesday
    if days_ahead == 0:  # Today is Wednesday
        days_ahead = 7  # Get next Wednesday
    return date + timedelta(days=days_ahead)

def get_next_friday(date):
    """Get next Friday from date."""
    days_ahead = (4 - date.weekday()) % 7  # 4 = Friday
    if days_ahead == 0:  # Today is Friday
        days_ahead = 7  # Get next Friday
    return date + timedelta(days=days_ahead)

def get_next_monday(date):
    """Get next Monday from date."""
    days_ahead = (0 - date.weekday()) % 7  # 0 = Monday
    if days_ahead == 0:  # Today is Monday
        days_ahead = 7  # Get next Monday
    return date + timedelta(days=days_ahead)

def run_ig_all_days_backtest(ticker, config):
    """
    Run backtest trading ALL weekdays with appropriate expiries.
    """
    console.print(f"[cyan]Running ALL DAYS backtest for {ticker}...[/cyan]")

    # Load daily data
    daily_file = f'data/{ticker}/daily_OHLCV.parquet'
    if not os.path.exists(daily_file):
        console.print(f"[red]No daily data for {ticker}[/red]")
        return None

    df_daily = pd.read_parquet(daily_file)

    # All weekdays (Mon-Fri)
    valid_days = df_daily[df_daily.index.dayofweek < 5]

    trades = []
    missing_data = 0

    for i in range(len(valid_days)):
        day_t = valid_days.iloc[i]
        date_t = valid_days.index[i]
        date_str = date_t.strftime("%Y-%m-%d")
        day_of_week = date_t.dayofweek  # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri

        # Skip flat days
        magnitude = abs((day_t['Close'] - day_t['Open']) / day_t['Open']) * 100
        if magnitude < 0.10:
            continue

        # Determine expiry types for each day
        expiry_configs = []

        if day_of_week == 0:  # Monday
            expiry_configs = [("WED", "2-DAY")]  # Monday -> Wednesday
        elif day_of_week == 1:  # Tuesday
            expiry_configs = [("WED", "1-DAY")]  # Tuesday -> Wednesday
        elif day_of_week == 2:  # Wednesday
            expiry_configs = [("FRI", "2-DAY")]  # Wednesday -> Friday
        elif day_of_week == 3:  # Thursday
            expiry_configs = [("FRI", "1-DAY")]  # Thursday -> Friday
        elif day_of_week == 4:  # Friday
            # TWO TRADES
            expiry_configs = [("MON", "3-DAY"), ("FRI", "7-DAY")]

        if not expiry_configs:
            continue

        # Determine signal
        direction = "GREEN" if day_t['Close'] > day_t['Open'] else "RED"

        if direction == "GREEN":
            signal = "FADE_GREEN"  # BUY PUT
        elif direction == "RED":
            signal = "FADE_RED"  # BUY CALL
        else:
            continue

        # Entry price (16:00 close)
        entry_price = day_t['Close']

        # Use ATR from daily data
        atr = day_t['ATR_14']
        if pd.isna(atr):
            continue

        # Calculate strike (ATM at close)
        strike = round(entry_price)

        # Calculate target
        target_mult = config.get('default_take_profit_atr', 0.1)
        target_dist = atr * target_mult

        if signal == "FADE_GREEN":
            target_price = entry_price - target_dist
        else:  # FADE_RED
            target_price = entry_price + target_dist

        # Process each expiry config
        for expiry_target, expiry_label in expiry_configs:
            # Determine expiry date
            if expiry_target == "WED":
                expiry_date = get_next_wednesday(date_t)
            elif expiry_target == "FRI":
                expiry_date = get_next_friday(date_t)
            elif expiry_target == "MON":
                expiry_date = get_next_monday(date_t)
            else:
                continue

            # Check target hit in window
            target_hit = False

            # Build window: from entry (16:00 Day T) to expiry (09:30 or 16:00)
            import pytz
            et_tz = pytz.timezone('America/New_York')

            # For 7-DAY, check until 16:00 expiry day
            # For others, check until 09:30 expiry day
            check_until_eod = (expiry_label == "7-DAY")

            # Check each day in the window
            check_date = date_t
            while check_date <= expiry_date:
                intraday_file = f'data/{ticker}/intraday/{check_date.strftime("%Y-%m-%d")}.parquet'

                if os.path.exists(intraday_file):
                    try:
                        df_intra = pd.read_parquet(intraday_file)

                        # Convert to ET
                        if df_intra.index.tz is not None:
                            df_intra.index = df_intra.index.tz_convert('America/New_York')
                        else:
                            df_intra.index = df_intra.index.tz_localize('UTC').tz_convert('America/New_York')

                        # Filter by time window
                        if check_date == date_t:
                            # Entry day: from 16:00 onwards
                            entry_dt = et_tz.localize(datetime(date_t.year, date_t.month, date_t.day, 16, 0))
                            df_window = df_intra[df_intra.index >= entry_dt]
                        elif check_date == expiry_date:
                            # Expiry day: until 09:30 (or 16:00 for 7-day)
                            if check_until_eod:
                                end_dt = et_tz.localize(datetime(expiry_date.year, expiry_date.month, expiry_date.day, 16, 0))
                            else:
                                end_dt = et_tz.localize(datetime(expiry_date.year, expiry_date.month, expiry_date.day, 9, 30))
                            df_window = df_intra[df_intra.index <= end_dt]
                        else:
                            # Middle days: full day
                            df_window = df_intra

                        if not df_window.empty:
                            # Check if target hit
                            if signal == "FADE_GREEN":  # PUT
                                if df_window['Low'].min() <= target_price:
                                    target_hit = True
                                    break
                            else:  # CALL
                                if df_window['High'].max() >= target_price:
                                    target_hit = True
                                    break

                    except Exception:
                        pass

                # Move to next trading day
                check_date = get_next_trading_day(check_date, df_daily)
                if check_date is None or check_date > expiry_date:
                    break

            # Record trade
            result = "WIN" if target_hit else "LOSS"
            pnl_mult = 0.45 if target_hit else -1.05

            trades.append({
                'Date': date_str,
                'Ticker': ticker,
                'Day_of_Week': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'][day_of_week],
                'Expiry_Target': expiry_target,
                'Expiry_Label': expiry_label,
                'Expiry_Date': expiry_date.strftime("%Y-%m-%d"),
                'Signal': signal,
                'Entry_Price': entry_price,
                'Strike': strike,
                'Target_Price': target_price,
                'Target_Dist': target_dist,
                'ATR': atr,
                'Result': result,
                'PnL_Mult': pnl_mult,
                'Direction': direction,
                'Magnitude': magnitude
            })

    if missing_data > 0:
        console.print(f"[yellow]Missing intraday data for {missing_data} days[/yellow]")

    return pd.DataFrame(trades)

def main():
    console.print("="*80)
    console.print("[bold blue]BACKTEST: IG.COM ALL DAYS STRATEGY[/bold blue]")
    console.print("="*80)
    console.print()
    console.print("Trading EVERY weekday using IG.com weekly expiries")
    console.print("Mon -> Wed, Tue -> Wed, Wed -> Fri, Thu -> Fri")
    console.print("Fri -> Mon + Next Fri (2 trades)")
    console.print()

    config = load_config()
    tickers = config.get('tickers', ['SPY', 'QQQ', 'IWM', 'DIA'])

    console.print(f"[cyan]Tickers: {', '.join(tickers)}[/cyan]")
    console.print(f"[cyan]Target: 0.1x ATR[/cyan]")
    console.print(f"[cyan]Max Position: $1,000 per ticker[/cyan]")
    console.print()

    # Run backtest for each ticker
    all_results = []

    for ticker in tickers:
        result = run_ig_all_days_backtest(ticker, config)
        if result is not None and not result.empty:
            all_results.append(result)

    if not all_results:
        console.print("[red]No results generated[/red]")
        return

    # Combine results
    df = pd.concat(all_results, ignore_index=True)
    df = df.sort_values('Date').reset_index(drop=True)

    console.print(f"[green]Generated {len(df):,} trades[/green]")
    console.print()

    # Count by day
    console.print("[bold]Trades by Day:[/bold]")
    for day in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']:
        day_count = len(df[df['Day_of_Week'] == day])
        console.print(f"  {day}: {day_count:,}")
    console.print()

    # Calculate equity curve with $1000 cap per ticker
    starting_capital = 10000
    kelly_pct = 0.0523
    max_position = 1000  # Cap per ticker

    equity = starting_capital
    equity_curve = []
    position_sizes = []
    daily_pnls = []

    for idx, row in df.iterrows():
        position = min(equity * kelly_pct, max_position)
        pnl = position * row['PnL_Mult']
        equity += pnl

        equity_curve.append(equity)
        position_sizes.append(position)
        daily_pnls.append(pnl)

    df['Position_Size'] = position_sizes
    df['PnL_Actual'] = daily_pnls
    df['Equity'] = equity_curve

    # Calculate statistics
    final_equity = equity
    total_pnl = final_equity - starting_capital
    total_roi = (total_pnl / starting_capital) * 100

    wins = (df['Result'] == 'WIN').sum()
    win_rate = wins / len(df) * 100

    df['Date'] = pd.to_datetime(df['Date'])
    years = (df['Date'].max() - df['Date'].min()).days / 365.25
    cagr = (pow(final_equity / starting_capital, 1/years) - 1) * 100

    # Drawdown
    df['Running_Max'] = df['Equity'].expanding().max()
    df['Drawdown'] = df['Equity'] - df['Running_Max']
    df['Drawdown_Pct'] = (df['Drawdown'] / df['Running_Max']) * 100

    max_dd = df['Drawdown'].min()
    max_dd_pct = df['Drawdown_Pct'].min()

    # Display results
    console.print("="*80)
    console.print("[bold white]RESULTS (ALL DAYS STRATEGY)[/bold white]")
    console.print("="*80)
    console.print()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold white")

    table.add_row("Strategy", "IG.com All Days (Mon-Fri)")
    table.add_row("Trade Days", "Every weekday + Fri has 2 trades per ticker")
    table.add_row("Period", f"{df['Date'].min().strftime('%Y-%m-%d')} to {df['Date'].max().strftime('%Y-%m-%d')}")
    table.add_row("Years", f"{years:.1f}")
    table.add_row("", "")
    table.add_row("Starting Capital", f"${starting_capital:,.2f}")
    table.add_row("Final Equity", f"${final_equity:,.2f}")
    table.add_row("Total P/L", f"${total_pnl:,.2f}")
    table.add_row("Total ROI", f"{total_roi:,.1f}%")
    table.add_row("CAGR", f"[bold green]{cagr:.1f}%[/bold green]")
    table.add_row("", "")
    table.add_row("Total Trades", f"{len(df):,}")
    table.add_row("Wins", f"{wins:,} ({win_rate:.1f}%)")
    table.add_row("Losses", f"{len(df) - wins:,} ({100-win_rate:.1f}%)")
    table.add_row("", "")
    table.add_row("Max Drawdown", f"${max_dd:,.2f} ({max_dd_pct:.1f}%)")

    console.print(table)
    console.print()

    # Breakdown by expiry type
    console.print("="*80)
    console.print("[bold white]BREAKDOWN BY EXPIRY TYPE[/bold white]")
    console.print("="*80)
    console.print()

    for expiry_label in ['1-DAY', '2-DAY', '3-DAY', '7-DAY']:
        df_type = df[df['Expiry_Label'] == expiry_label]
        if len(df_type) > 0:
            type_wins = (df_type['Result'] == 'WIN').sum()
            type_wr = type_wins / len(df_type) * 100

            # Get day breakdown
            days = df_type['Day_of_Week'].unique()
            day_desc = ', '.join(sorted(days))

            console.print(f"{expiry_label} ({day_desc}):")
            console.print(f"  Trades: {len(df_type):,}")
            console.print(f"  Wins: {type_wins:,} ({type_wr:.1f}%)")
            console.print()

    # Save results
    output_file = 'results/ig_all_days_backtest.csv'
    df.to_csv(output_file, index=False)
    console.print(f"[green]Results saved to: {output_file}[/green]")
    console.print()

    # Compare with original
    console.print("="*80)
    console.print("[bold white]COMPARISON WITH ORIGINAL BACKTEST[/bold white]")
    console.print("="*80)
    console.print()

    try:
        df_original = pd.read_csv('results/phase3_option_c_detailed_results.csv')
        orig_final = df_original['Equity'].iloc[-1]
        orig_pnl = orig_final - starting_capital
        orig_roi = (orig_pnl / starting_capital) * 100
        orig_wins = (df_original['Result'] == 'WIN').sum()
        orig_wr = orig_wins / len(df_original) * 100
        orig_years = (pd.to_datetime(df_original['Date']).max() - pd.to_datetime(df_original['Date']).min()).days / 365.25
        orig_cagr = (pow(orig_final / starting_capital, 1/orig_years) - 1) * 100

        comp_table = Table(show_header=True, header_style="bold cyan")
        comp_table.add_column("Metric", width=20)
        comp_table.add_column("Original\n(Mon-Thu)", justify="right", width=20)
        comp_table.add_column("All Days\n(Mon-Fri)", justify="right", width=20)
        comp_table.add_column("Difference", justify="right", width=20)

        comp_table.add_row(
            "Trades",
            f"{len(df_original):,}",
            f"{len(df):,}",
            f"{len(df) - len(df_original):+,}"
        )
        comp_table.add_row(
            "Win Rate",
            f"{orig_wr:.1f}%",
            f"{win_rate:.1f}%",
            f"{win_rate - orig_wr:+.1f}pp"
        )
        comp_table.add_row(
            "Final Equity",
            f"${orig_final:,.0f}",
            f"${final_equity:,.0f}",
            f"${final_equity - orig_final:+,.0f}"
        )
        comp_table.add_row(
            "CAGR",
            f"{orig_cagr:.1f}%",
            f"{cagr:.1f}%",
            f"{cagr - orig_cagr:+.1f}pp"
        )

        console.print(comp_table)
        console.print()

        pct_diff = ((final_equity - orig_final) / orig_final) * 100
        console.print(f"[bold]Performance difference: {pct_diff:+.1f}%[/bold]")

    except FileNotFoundError:
        console.print("[yellow]Original backtest not found for comparison[/yellow]")

    console.print()
    console.print("="*80)
    console.print("[bold green]ALL DAYS BACKTEST COMPLETE![/bold green]")
    console.print("="*80)

if __name__ == "__main__":
    main()
