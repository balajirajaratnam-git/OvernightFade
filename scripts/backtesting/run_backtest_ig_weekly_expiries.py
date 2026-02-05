"""
Backtest for IG.com Weekly Expiry Strategy

Trading Days:
- Tuesday 16:00 ET -> Wednesday expiry (overnight)
- Thursday 16:00 ET -> Friday expiry (overnight)
- Friday 16:00 ET -> TWO TRADES PER TICKER:
  1. Monday expiry (3-day weekend)
  2. Next Friday expiry (7-day weekly)

Skip: Monday & Wednesday (unless end-of-month expiry)

Key: Can place orders at 16:00 ET close since weekly expiries open 7 days ahead!
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

def is_end_of_month(date):
    """Check if date is last trading day of month"""
    next_day = date + timedelta(days=1)
    return next_day.month != date.month

def get_next_trading_day(date, daily_df):
    """Get next trading day after given date"""
    future_days = daily_df[daily_df.index > date]
    if len(future_days) > 0:
        return future_days.index[0]
    return None

def get_next_friday(date):
    """Get next Friday from given date"""
    # If today is Friday, get next Friday (7 days)
    # Otherwise get the upcoming Friday
    days_ahead = 4 - date.weekday()  # Friday is 4
    if days_ahead <= 0:  # Today is Friday or later in week
        days_ahead += 7
    return date + timedelta(days=days_ahead)

def run_ig_weekly_backtest(ticker, config):
    """
    Run backtest with IG.com weekly expiry strategy

    Trade on: Tuesday, Thursday, Friday
    Skip: Monday, Wednesday (unless end-of-month)
    """
    console.print(f"[cyan]Running IG weekly backtest for {ticker}...[/cyan]")

    # Load daily data
    daily_file = f'data/{ticker}/daily_OHLCV.parquet'
    if not os.path.exists(daily_file):
        console.print(f"[red]No daily data for {ticker}[/red]")
        return None

    df_daily = pd.read_parquet(daily_file)

    # Filter to Tue, Thu, Fri only (1, 3, 4)
    # BUT also include Tue/Thu if they're end-of-month
    valid_days = df_daily[
        (df_daily.index.dayofweek.isin([1, 3, 4])) |  # Tue, Thu, Fri
        ((df_daily.index.dayofweek.isin([1, 3])))  # Tue, Thu for end-of-month
    ]

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

        # Determine if we should trade this day and which expiry types
        expiry_types = []

        if day_of_week == 1:  # Tuesday
            expiry_types = ["NEXT_DAY"]  # Wednesday expiry
        elif day_of_week == 3:  # Thursday
            expiry_types = ["NEXT_DAY"]  # Friday expiry
        elif day_of_week == 4:  # Friday
            # TWO TRADES: Monday expiry (3-day) AND Next Friday expiry (7-day)
            expiry_types = ["NEXT_DAY", "WEEKLY"]  # Monday expiry + Next Friday expiry
        # Monday/Wednesday: Only if end-of-month (feature for future implementation)

        if not expiry_types:
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

        # Process each expiry type (Friday will have TWO: NEXT_DAY and WEEKLY)
        for expiry_type in expiry_types:
            # Determine target window based on expiry type
            if expiry_type == "WEEKLY":
                # Friday -> Next Friday (7 days)
                expiry_date = get_next_friday(date_t)
                # Check from 16:00 Friday -> 16:00 next Friday (7 days)
            else:
                # Overnight (16:00 Day T -> 09:30 Day T+1)
                expiry_date = get_next_trading_day(date_t, df_daily)

            if expiry_date is None:
                missing_data += 1
                continue

            # Load intraday data for the entry day and check target
            target_hit = False
            win_time_str = "N/A"

            # For WEEKLY expiry, check multiple days
            if expiry_type == "WEEKLY":
                # Check 7 days of trading
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

                            # Check if target hit
                            if signal == "FADE_GREEN":  # PUT
                                if df_intra['Low'].min() <= target_price:
                                    target_hit = True
                                    break
                            else:  # CALL
                                if df_intra['High'].max() >= target_price:
                                    target_hit = True
                                    break

                        except Exception:
                            pass

                    # Move to next trading day
                    check_date = get_next_trading_day(check_date, df_daily)
                    if check_date is None or check_date > expiry_date:
                        break

            else:
                # NEXT_DAY expiry (standard overnight)
                intraday_file = f'data/{ticker}/intraday/{date_str}.parquet'

                if os.path.exists(intraday_file):
                    try:
                        df_intra = pd.read_parquet(intraday_file)

                        if df_intra.index.tz is not None:
                            df_intra.index = df_intra.index.tz_convert('America/New_York')
                        else:
                            df_intra.index = df_intra.index.tz_localize('UTC').tz_convert('America/New_York')

                        # From 16:00 onwards Day T
                        import pytz
                        et_tz = pytz.timezone('America/New_York')
                        entry_dt = et_tz.localize(datetime(date_t.year, date_t.month, date_t.day, 16, 0))
                        window_day_t = df_intra[df_intra.index >= entry_dt]

                        # Next day until 09:30
                        next_date_str = expiry_date.strftime("%Y-%m-%d")
                        next_intraday_file = f'data/{ticker}/intraday/{next_date_str}.parquet'

                        if os.path.exists(next_intraday_file):
                            df_next = pd.read_parquet(next_intraday_file)
                            if df_next.index.tz is not None:
                                df_next.index = df_next.index.tz_convert('America/New_York')
                            else:
                                df_next.index = df_next.index.tz_localize('UTC').tz_convert('America/New_York')

                            market_open = et_tz.localize(datetime(expiry_date.year, expiry_date.month, expiry_date.day, 9, 30))
                            window_day_next = df_next[df_next.index < market_open]

                            window = pd.concat([window_day_t, window_day_next])
                        else:
                            window = window_day_t

                        if not window.empty:
                            if signal == "FADE_GREEN":  # PUT
                                target_hit = window['Low'].min() <= target_price
                            else:  # CALL
                                target_hit = window['High'].max() >= target_price

                    except Exception:
                        missing_data += 1
                        continue
                else:
                    missing_data += 1
                    continue

            # Record trade
            result = "WIN" if target_hit else "LOSS"
            pnl_mult = 0.45 if target_hit else -1.05

            trades.append({
                'Date': date_str,
                'Ticker': ticker,
                'Day_of_Week': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'][day_of_week],
                'Expiry_Type': expiry_type,
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
    console.print("[bold blue]IG.COM WEEKLY EXPIRY BACKTEST[/bold blue]")
    console.print("="*80)
    console.print()
    console.print("Trade Days: Tuesday, Thursday, Friday at 16:00 ET")
    console.print("Tuesday -> Wednesday expiry (overnight)")
    console.print("Thursday -> Friday expiry (overnight)")
    console.print("Friday -> NEXT Friday expiry (7 days)")
    console.print()

    config = load_config()
    tickers = config.get('tickers', ['SPY', 'QQQ', 'IWM', 'DIA'])

    # Run backtest for each ticker
    all_results = []

    for ticker in tickers:
        result = run_ig_weekly_backtest(ticker, config)
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

    # Breakdown by day of week
    console.print("Trades by Day of Week:")
    for day in ['Tue', 'Thu', 'Fri']:
        count = len(df[df['Day_of_Week'] == day])
        console.print(f"  {day}: {count:,}")
    console.print()

    # Calculate equity curve
    starting_capital = 10000
    kelly_pct = 0.0523
    max_position = 1000

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
    console.print("[bold white]RESULTS (IG.COM WEEKLY EXPIRY STRATEGY)[/bold white]")
    console.print("="*80)
    console.print()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold white")

    table.add_row("Strategy", "IG.com Weekly Expiries")
    table.add_row("Trade Days", "Tue, Thu, Fri (Fri = 2 trades per ticker)")
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

    # Breakdown by day and expiry type
    console.print("="*80)
    console.print("[bold white]BREAKDOWN BY DAY AND EXPIRY TYPE[/bold white]")
    console.print("="*80)
    console.print()

    # Tuesday (Wed expiry)
    df_tue = df[(df['Day_of_Week'] == 'Tue') & (df['Expiry_Type'] == 'NEXT_DAY')]
    if len(df_tue) > 0:
        tue_wins = (df_tue['Result'] == 'WIN').sum()
        tue_wr = tue_wins / len(df_tue) * 100
        console.print(f"Tuesday -> Wednesday expiry (overnight):")
        console.print(f"  Trades: {len(df_tue):,}")
        console.print(f"  Wins: {tue_wins:,} ({tue_wr:.1f}%)")
        console.print()

    # Thursday (Fri expiry)
    df_thu = df[(df['Day_of_Week'] == 'Thu') & (df['Expiry_Type'] == 'NEXT_DAY')]
    if len(df_thu) > 0:
        thu_wins = (df_thu['Result'] == 'WIN').sum()
        thu_wr = thu_wins / len(df_thu) * 100
        console.print(f"Thursday -> Friday expiry (overnight):")
        console.print(f"  Trades: {len(df_thu):,}")
        console.print(f"  Wins: {thu_wins:,} ({thu_wr:.1f}%)")
        console.print()

    # Friday -> Monday (3-day)
    df_fri_mon = df[(df['Day_of_Week'] == 'Fri') & (df['Expiry_Type'] == 'NEXT_DAY')]
    if len(df_fri_mon) > 0:
        fri_mon_wins = (df_fri_mon['Result'] == 'WIN').sum()
        fri_mon_wr = fri_mon_wins / len(df_fri_mon) * 100
        console.print(f"Friday -> Monday expiry (3-day weekend):")
        console.print(f"  Trades: {len(df_fri_mon):,}")
        console.print(f"  Wins: {fri_mon_wins:,} ({fri_mon_wr:.1f}%)")
        console.print()

    # Friday -> Next Friday (7-day)
    df_fri_fri = df[(df['Day_of_Week'] == 'Fri') & (df['Expiry_Type'] == 'WEEKLY')]
    if len(df_fri_fri) > 0:
        fri_fri_wins = (df_fri_fri['Result'] == 'WIN').sum()
        fri_fri_wr = fri_fri_wins / len(df_fri_fri) * 100
        console.print(f"Friday -> Next Friday expiry (7-day weekly):")
        console.print(f"  Trades: {len(df_fri_fri):,}")
        console.print(f"  Wins: {fri_fri_wins:,} ({fri_fri_wr:.1f}%)")
        console.print()

    # Save results
    output_file = 'results/ig_weekly_expiry_backtest.csv'
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
        comp_table.add_column("IG Weekly\n(Tue/Thu/Fri)", justify="right", width=20)
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
        console.print(f"[bold]Performance vs Original: {pct_diff:+.1f}%[/bold]")

    except FileNotFoundError:
        console.print("[yellow]Original backtest not found for comparison[/yellow]")

    console.print()
    console.print("="*80)
    console.print("[bold green]IG.COM WEEKLY EXPIRY BACKTEST COMPLETE![/bold green]")
    console.print("="*80)

if __name__ == "__main__":
    main()
