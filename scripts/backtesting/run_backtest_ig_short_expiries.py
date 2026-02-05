"""
Backtest for IG.com SHORT EXPIRIES Strategy

Shorter expiries to minimize theta decay:
- Monday 16:00 ET -> Wednesday expiry (2 days)
- Tuesday 16:00 ET -> Wednesday expiry (1 day)
- Wednesday 16:00 ET -> Friday expiry (2 days)
- Thursday 16:00 ET -> Friday expiry (1 day)
- Friday 16:00 ET -> Monday expiry (3 days)

NO Friday -> Next Friday (removes 7-day theta decay issue)

Key: All trades close within 1-3 days = Less overlapping positions!
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
    """Get next Wednesday from date (this week if Mon/Tue)."""
    if date.weekday() in [0, 1]:  # Mon or Tue
        days_ahead = 2 - date.weekday()  # This week's Wednesday
    else:
        days_ahead = 7 - date.weekday() + 2  # Next week's Wednesday
    return date + timedelta(days=days_ahead)

def get_next_friday(date):
    """Get next Friday from date (this week if Mon-Thu)."""
    if date.weekday() < 4:  # Mon-Thu
        days_ahead = 4 - date.weekday()  # This week's Friday
    else:
        days_ahead = 7  # Next Friday
    return date + timedelta(days=days_ahead)

def get_next_monday(date):
    """Get next Monday from date."""
    if date.weekday() == 4:  # Friday
        days_ahead = 3  # Next Monday (over weekend)
    else:
        days_ahead = 7 - date.weekday()  # Next week's Monday
    return date + timedelta(days=days_ahead)

def run_ig_short_backtest(ticker, config):
    """
    Run backtest with SHORT expiries (1-3 days).
    """
    console.print(f"[cyan]Running SHORT EXPIRIES backtest for {ticker}...[/cyan]")

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
            expiry_date = get_next_wednesday(date_t)
            expiry_label = "MON-WED-2D"
            days_to_expiry = 2
        elif day_of_week == 1:  # Tuesday
            expiry_date = get_next_wednesday(date_t)
            expiry_label = "TUE-WED-1D"
            days_to_expiry = 1
        elif day_of_week == 2:  # Wednesday
            expiry_date = get_next_friday(date_t)
            expiry_label = "WED-FRI-2D"
            days_to_expiry = 2
        elif day_of_week == 3:  # Thursday
            expiry_date = get_next_friday(date_t)
            expiry_label = "THU-FRI-1D"
            days_to_expiry = 1
        elif day_of_week == 4:  # Friday
            expiry_date = get_next_monday(date_t)
            expiry_label = "FRI-MON-3D"
            days_to_expiry = 3
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

        # Check target hit in window
        target_hit = False

        import pytz
        et_tz = pytz.timezone('America/New_York')

        # Check from entry to expiry (16:00 Day T -> 09:30 expiry day)
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
                        # Expiry day: until 09:30
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
    console.print("[bold blue]BACKTEST: IG.COM SHORT EXPIRIES STRATEGY[/bold blue]")
    console.print("="*80)
    console.print()
    console.print("SHORT expiries to minimize theta decay (1-3 days)")
    console.print("Mon->Wed, Tue->Wed, Wed->Fri, Thu->Fri, Fri->Mon")
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
        result = run_ig_short_backtest(ticker, config)
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

    # Check overlapping positions
    console.print("="*80)
    console.print("[bold white]OVERLAPPING POSITIONS CHECK[/bold white]")
    console.print("="*80)
    console.print()

    # Sample first 2 weeks
    first_date = df['Date'].min()
    two_weeks = first_date + timedelta(days=14)
    df_sample = df[df['Date'] <= two_weeks].copy()

    max_open = 0
    for date in df_sample['Date'].unique()[:10]:
        open_positions = df_sample[
            (df_sample['Date'] <= date) &
            (pd.to_datetime(df_sample['Expiry_Date']) >= date)
        ]
        max_open = max(max_open, len(open_positions))
        console.print(f"{date.strftime('%Y-%m-%d')}: {len(open_positions)} open positions")

    console.print()
    console.print(f"[bold]Max simultaneous positions: {max_open}[/bold]")
    console.print(f"[bold]Capital required: ${max_open * 1000:,}[/bold]")
    console.print()

    # Display results
    console.print("="*80)
    console.print("[bold white]RESULTS (SHORT EXPIRIES STRATEGY)[/bold white]")
    console.print("="*80)
    console.print()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold white")

    table.add_row("Strategy", "IG.com Short Expiries (1-3 days)")
    table.add_row("Trade Days", "Every weekday, short expiries")
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
    output_file = 'results/ig_short_expiries_backtest.csv'
    df.to_csv(output_file, index=False)
    console.print(f"[green]Results saved to: {output_file}[/green]")
    console.print()

    # Comprehensive comparison
    console.print("="*80)
    console.print("[bold white]COMPREHENSIVE COMPARISON - ALL STRATEGIES[/bold white]")
    console.print("="*80)
    console.print()

    strategies = []

    # Load all previous backtests
    backtest_files = [
        ('Original (Mon-Thu)', 'results/phase3_option_c_detailed_results.csv'),
        ('IG Weekly (Tue/Thu/Fri)', 'results/ig_weekly_expiry_backtest.csv'),
        ('IG All Days (Mixed)', 'results/ig_all_days_backtest.csv'),
        ('IG Weekly Long (6-7d)', 'results/ig_weekly_long_backtest.csv')
    ]

    for name, filepath in backtest_files:
        try:
            df_temp = pd.read_csv(filepath)
            temp_final = df_temp['Equity'].iloc[-1]
            temp_years = (pd.to_datetime(df_temp['Date']).max() - pd.to_datetime(df_temp['Date']).min()).days / 365.25
            temp_cagr = (pow(temp_final / starting_capital, 1/temp_years) - 1) * 100
            temp_wr = (df_temp['Result'] == 'WIN').sum() / len(df_temp) * 100
            temp_dd = ((df_temp['Equity'] - df_temp['Equity'].expanding().max()).min() / df_temp['Equity'].expanding().max().max()) * 100

            strategies.append({
                'Strategy': name,
                'Trades': len(df_temp),
                'Win_Rate': temp_wr,
                'Final_Equity': temp_final,
                'CAGR': temp_cagr,
                'Max_DD': temp_dd
            })
        except:
            pass

    # Add current strategy
    strategies.append({
        'Strategy': 'IG Short (1-3d)',
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

    # Critical issues analysis
    console.print("="*80)
    console.print("[bold yellow]CRITICAL ISSUES: Why This May Still Fail[/bold yellow]")
    console.print("="*80)
    console.print()

    issues = [
        ("1. Overlapping Positions", f"Max {max_open} positions = ${max_open*1000:,} needed (vs $10k start)"),
        ("2. Options Spreads", "IWM/DIA bid/ask 5-15%, SPY/QQQ 2-5%"),
        ("3. Theta Decay (Reduced)", "1-3 days = ~5-10% theta vs 30-50% for weekly"),
        ("4. IG.com Execution", "Unverified: Can you actually trade at 16:00 ET?"),
        ("5. Slippage Reality", "After-hours fills, requotes, rejections"),
        ("6. Market Regimes", "Untested on 2008 crash, COVID, high volatility"),
        ("7. Operational Burden", "Trade every day at 16:00 ET for years"),
        ("8. Tax Burden", f"{len(df)/years:.0f} trades/year = massive short-term cap gains")
    ]

    issue_table = Table(show_header=False, box=None)
    issue_table.add_column("Issue", style="yellow", width=30)
    issue_table.add_column("Details", style="white", width=50)

    for issue, detail in issues:
        issue_table.add_row(issue, detail)

    console.print(issue_table)
    console.print()

    console.print("[bold]Realistic Expectation:[/bold]")
    console.print(f"  Backtest CAGR: {cagr:.1f}%")
    console.print(f"  Real-world CAGR (with issues): [bold yellow]{cagr * 0.5:.1f}%-{cagr * 0.7:.1f}%[/bold yellow]")
    console.print(f"  Capital needed: [bold yellow]${max_open * 1000:,}[/bold yellow] (not $10k)")
    console.print()

    console.print("="*80)
    console.print("[bold green]SHORT EXPIRIES BACKTEST COMPLETE![/bold green]")
    console.print("="*80)

if __name__ == "__main__":
    main()
