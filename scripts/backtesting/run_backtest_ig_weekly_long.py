"""
Backtest for IG.com WEEKLY LONG Strategy

Every trade gets ~7 days to hit target using IG.com weekly expiries (Mon/Wed/Fri):

- Monday 16:00 ET -> Next Monday expiry (7 days)
- Tuesday 16:00 ET -> Next Monday expiry (6 days)
- Wednesday 16:00 ET -> Next Wednesday expiry (7 days)
- Thursday 16:00 ET -> Next Wednesday expiry (6 days)
- Friday 16:00 ET -> Next Friday expiry (7 days)

Position Sizing: Capped at $1000 per ticker

Key: All trades get a full week to hit target = Maximum win rate!
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

def get_next_monday(date):
    """Get next Monday from date (always next week, not today)."""
    days_ahead = (7 - date.weekday()) % 7  # Days until next Monday
    if days_ahead == 0:  # Today is Monday
        days_ahead = 7  # Get next Monday
    return date + timedelta(days=days_ahead)

def get_next_wednesday(date):
    """Get next Wednesday from date (always next week if today is Wed/Thu)."""
    # If Mon/Tue: get this week's Wednesday
    # If Wed/Thu: get next week's Wednesday
    if date.weekday() in [2, 3]:  # Wed or Thu
        days_ahead = 7 - date.weekday() + 2  # Next week's Wednesday
    else:
        days_ahead = (2 - date.weekday()) % 7  # This week's Wednesday
        if days_ahead == 0:
            days_ahead = 7
    return date + timedelta(days=days_ahead)

def get_next_friday(date):
    """Get next Friday from date (always next week if today is Friday)."""
    days_ahead = (4 - date.weekday()) % 7  # 4 = Friday
    if days_ahead == 0:  # Today is Friday
        days_ahead = 7  # Get next Friday
    return date + timedelta(days=days_ahead)

def run_ig_weekly_long_backtest(ticker, config):
    """
    Run backtest where ALL trades have ~7 days to hit target.
    """
    console.print(f"[cyan]Running WEEKLY LONG backtest for {ticker}...[/cyan]")

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

        # Determine expiry based on day of week
        if day_of_week == 0:  # Monday
            expiry_date = get_next_monday(date_t)
            expiry_label = "MON-MON-7D"
            days_to_expiry = 7
        elif day_of_week == 1:  # Tuesday
            expiry_date = get_next_monday(date_t)
            expiry_label = "TUE-MON-6D"
            days_to_expiry = 6
        elif day_of_week == 2:  # Wednesday
            expiry_date = get_next_wednesday(date_t)
            expiry_label = "WED-WED-7D"
            days_to_expiry = 7
        elif day_of_week == 3:  # Thursday
            expiry_date = get_next_wednesday(date_t)
            expiry_label = "THU-WED-6D"
            days_to_expiry = 6
        elif day_of_week == 4:  # Friday
            expiry_date = get_next_friday(date_t)
            expiry_label = "FRI-FRI-7D"
            days_to_expiry = 7
        else:
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

        # Check target hit in 6-7 day window
        target_hit = False

        import pytz
        et_tz = pytz.timezone('America/New_York')

        # Check each day from entry to expiry
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
                        # Expiry day: until 16:00 (full week)
                        end_dt = et_tz.localize(datetime(expiry_date.year, expiry_date.month, expiry_date.day, 16, 0))
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
            'Expiry_Label': expiry_label,
            'Expiry_Date': expiry_date.strftime("%Y-%m-%d"),
            'Days_To_Expiry': days_to_expiry,
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
    console.print("[bold blue]BACKTEST: IG.COM WEEKLY LONG STRATEGY[/bold blue]")
    console.print("="*80)
    console.print()
    console.print("ALL trades get ~7 days to hit target")
    console.print("Mon->Mon, Tue->Mon, Wed->Wed, Thu->Wed, Fri->Fri")
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
        result = run_ig_weekly_long_backtest(ticker, config)
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
    console.print("[bold white]RESULTS (WEEKLY LONG STRATEGY)[/bold white]")
    console.print("="*80)
    console.print()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold white")

    table.add_row("Strategy", "IG.com Weekly Long (6-7 days)")
    table.add_row("Trade Days", "Every weekday, ~7 day expiries")
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
    console.print("[bold white]BREAKDOWN BY EXPIRY PATTERN[/bold white]")
    console.print("="*80)
    console.print()

    for expiry_label in sorted(df['Expiry_Label'].unique()):
        df_type = df[df['Expiry_Label'] == expiry_label]
        if len(df_type) > 0:
            type_wins = (df_type['Result'] == 'WIN').sum()
            type_wr = type_wins / len(df_type) * 100

            console.print(f"{expiry_label}:")
            console.print(f"  Trades: {len(df_type):,}")
            console.print(f"  Wins: {type_wins:,} ({type_wr:.1f}%)")
            console.print()

    # Save results
    output_file = 'results/ig_weekly_long_backtest.csv'
    df.to_csv(output_file, index=False)
    console.print(f"[green]Results saved to: {output_file}[/green]")
    console.print()

    # Compare with ALL previous backtests
    console.print("="*80)
    console.print("[bold white]COMPREHENSIVE COMPARISON - ALL STRATEGIES[/bold white]")
    console.print("="*80)
    console.print()

    strategies = []

    # Original
    try:
        df_orig = pd.read_csv('results/phase3_option_c_detailed_results.csv')
        orig_final = df_orig['Equity'].iloc[-1]
        orig_years = (pd.to_datetime(df_orig['Date']).max() - pd.to_datetime(df_orig['Date']).min()).days / 365.25
        orig_cagr = (pow(orig_final / starting_capital, 1/orig_years) - 1) * 100
        orig_wr = (df_orig['Result'] == 'WIN').sum() / len(df_orig) * 100
        orig_dd = ((df_orig['Equity'] - df_orig['Equity'].expanding().max()).min() / df_orig['Equity'].expanding().max().max()) * 100

        strategies.append({
            'Strategy': 'Original (Mon-Thu)',
            'Trades': len(df_orig),
            'Win_Rate': orig_wr,
            'Final_Equity': orig_final,
            'CAGR': orig_cagr,
            'Max_DD': orig_dd
        })
    except:
        pass

    # IG Weekly (Tue/Thu/Fri with Fri 2x)
    try:
        df_ig_weekly = pd.read_csv('results/ig_weekly_expiry_backtest.csv')
        ig_w_final = df_ig_weekly['Equity'].iloc[-1]
        ig_w_years = (pd.to_datetime(df_ig_weekly['Date']).max() - pd.to_datetime(df_ig_weekly['Date']).min()).days / 365.25
        ig_w_cagr = (pow(ig_w_final / starting_capital, 1/ig_w_years) - 1) * 100
        ig_w_wr = (df_ig_weekly['Result'] == 'WIN').sum() / len(df_ig_weekly) * 100
        ig_w_dd = ((df_ig_weekly['Equity'] - df_ig_weekly['Equity'].expanding().max()).min() / df_ig_weekly['Equity'].expanding().max().max()) * 100

        strategies.append({
            'Strategy': 'IG Weekly (Tue/Thu/Fri)',
            'Trades': len(df_ig_weekly),
            'Win_Rate': ig_w_wr,
            'Final_Equity': ig_w_final,
            'CAGR': ig_w_cagr,
            'Max_DD': ig_w_dd
        })
    except:
        pass

    # IG All Days
    try:
        df_all_days = pd.read_csv('results/ig_all_days_backtest.csv')
        all_d_final = df_all_days['Equity'].iloc[-1]
        all_d_years = (pd.to_datetime(df_all_days['Date']).max() - pd.to_datetime(df_all_days['Date']).min()).days / 365.25
        all_d_cagr = (pow(all_d_final / starting_capital, 1/all_d_years) - 1) * 100
        all_d_wr = (df_all_days['Result'] == 'WIN').sum() / len(df_all_days) * 100
        all_d_dd = ((df_all_days['Equity'] - df_all_days['Equity'].expanding().max()).min() / df_all_days['Equity'].expanding().max().max()) * 100

        strategies.append({
            'Strategy': 'IG All Days (Mixed)',
            'Trades': len(df_all_days),
            'Win_Rate': all_d_wr,
            'Final_Equity': all_d_final,
            'CAGR': all_d_cagr,
            'Max_DD': all_d_dd
        })
    except:
        pass

    # Current (Weekly Long)
    strategies.append({
        'Strategy': 'IG Weekly Long (6-7d)',
        'Trades': len(df),
        'Win_Rate': win_rate,
        'Final_Equity': final_equity,
        'CAGR': cagr,
        'Max_DD': max_dd_pct
    })

    # Create comparison table
    comp_table = Table(show_header=True, header_style="bold cyan")
    comp_table.add_column("Strategy", style="white", width=25)
    comp_table.add_column("Trades", justify="right", width=10)
    comp_table.add_column("Win Rate", justify="right", width=10)
    comp_table.add_column("Final Equity", justify="right", width=15)
    comp_table.add_column("CAGR", justify="right", width=10)
    comp_table.add_column("Max DD", justify="right", width=10)

    for s in strategies:
        comp_table.add_row(
            s['Strategy'],
            f"{s['Trades']:,}",
            f"{s['Win_Rate']:.1f}%",
            f"${s['Final_Equity']:,.0f}",
            f"[bold]{s['CAGR']:.1f}%[/bold]",
            f"{s['Max_DD']:.1f}%"
        )

    console.print(comp_table)
    console.print()

    # Find best strategy
    best_cagr = max(strategies, key=lambda x: x['CAGR'])
    console.print(f"[bold green]Best CAGR: {best_cagr['Strategy']} at {best_cagr['CAGR']:.1f}%[/bold green]")

    best_wr = max(strategies, key=lambda x: x['Win_Rate'])
    console.print(f"[bold green]Best Win Rate: {best_wr['Strategy']} at {best_wr['Win_Rate']:.1f}%[/bold green]")

    console.print()
    console.print("="*80)
    console.print("[bold green]WEEKLY LONG BACKTEST COMPLETE![/bold green]")
    console.print("="*80)

if __name__ == "__main__":
    main()
