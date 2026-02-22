"""
Backtest with IG.com Timing: Entry at 16:15 ET

This backtest uses 16:15 ET price as the entry point (instead of 16:00 close)
to accurately simulate IG.com trading where orders open at 16:15 ET.

Key Differences from Standard Backtest:
1. Entry price = 16:15 ET price (not 16:00 close)
2. Strike = ATM at 16:15 price
3. Target = 16:15 price +/- (ATR * 0.1)
4. Win window = 16:15 ET Day T -> 09:30 ET Day T+1
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

def save_config(config):
    with open("config/config.json", "w") as f:
        json.dump(config, f, indent=2)

def get_price_at_time(ticker, date_str, target_time_str):
    """
    Get price at specific time (e.g., 16:15 ET)

    Args:
        ticker: Ticker symbol
        date_str: Date string (YYYY-MM-DD)
        target_time_str: Time string (HH:MM format, e.g., "16:15")

    Returns:
        Price at that time, or None if not available
    """
    intraday_file = f'data/{ticker}/intraday/{date_str}.parquet'

    if not os.path.exists(intraday_file):
        return None

    try:
        df = pd.read_parquet(intraday_file)

        # Convert to ET timezone
        if df.index.tz is not None:
            df.index = df.index.tz_convert('America/New_York')
        else:
            df.index = df.index.tz_localize('UTC').tz_convert('America/New_York')

        # Parse target time
        hour, minute = map(int, target_time_str.split(':'))

        # Get all bars from target time onwards (for next 5 minutes)
        target_bars = df.between_time(
            f"{hour:02d}:{minute:02d}",
            f"{hour:02d}:{min(59, minute+5):02d}"
        )

        if len(target_bars) == 0:
            # Try getting any bar after market close (16:00+)
            after_close = df.between_time("16:00", "16:30")
            if len(after_close) > 0:
                # Return first available after-hours price
                return after_close['Close'].iloc[0]
            return None

        # Return close price of first bar at or after target time
        return target_bars['Close'].iloc[0]

    except Exception as e:
        return None

def run_ig_backtest(ticker, config, entry_time="16:15"):
    """
    Run backtest with custom entry time (e.g., 16:15 ET)

    Args:
        ticker: Ticker to backtest
        config: Config dict
        entry_time: Entry time in HH:MM format (e.g., "16:15")
    """
    console.print(f"[cyan]Running backtest for {ticker} with {entry_time} ET entry...[/cyan]")

    # Load daily data
    daily_file = f'data/{ticker}/daily_OHLCV.parquet'
    if not os.path.exists(daily_file):
        console.print(f"[red]No daily data for {ticker}[/red]")
        return None

    df_daily = pd.read_parquet(daily_file)

    # Filter Mon-Thu only (no Fridays)
    valid_days = df_daily[df_daily.index.dayofweek < 4]

    trades = []
    missing_data = 0

    for i in range(len(valid_days) - 1):
        day_t = valid_days.iloc[i]
        date_t = valid_days.index[i]
        date_str = date_t.strftime("%Y-%m-%d")

        # Skip flat days
        magnitude = abs((day_t['Close'] - day_t['Open']) / day_t['Open']) * 100
        if magnitude < 0.10:
            continue

        # Determine signal
        direction = "GREEN" if day_t['Close'] > day_t['Open'] else "RED"

        if direction == "GREEN":
            signal = "FADE_GREEN"  # BUY PUT
        elif direction == "RED":
            signal = "FADE_RED"  # BUY CALL
        else:
            continue

        # Get price at entry time (e.g., 16:15 ET)
        entry_price = get_price_at_time(ticker, date_str, entry_time)

        if entry_price is None:
            missing_data += 1
            continue

        # Use ATR from daily data
        atr = day_t['ATR_14']
        if pd.isna(atr):
            continue

        # Calculate strike (ATM at entry time)
        strike = round(entry_price)

        # Calculate target (from entry price)
        target_mult = config.get('default_take_profit_atr', 0.1)
        target_dist = atr * target_mult

        if signal == "FADE_GREEN":
            target_price = entry_price - target_dist
        else:  # FADE_RED
            target_price = entry_price + target_dist

        # Check if target hit in overnight session (entry_time Day T -> 09:30 Day T+1)
        intraday_file = f'data/{ticker}/intraday/{date_str}.parquet'

        if not os.path.exists(intraday_file):
            missing_data += 1
            continue

        try:
            df_intra = pd.read_parquet(intraday_file)

            # Convert to ET
            if df_intra.index.tz is not None:
                df_intra.index = df_intra.index.tz_convert('America/New_York')
            else:
                df_intra.index = df_intra.index.tz_localize('UTC').tz_convert('America/New_York')

            # Entry time onwards on Day T
            entry_hour, entry_minute = map(int, entry_time.split(':'))

            # Create timezone-aware datetime for comparison
            import pytz
            et_tz = pytz.timezone('America/New_York')
            entry_dt = et_tz.localize(datetime(date_t.year, date_t.month, date_t.day, entry_hour, entry_minute))

            # Filter to entry time onwards
            window_day_t = df_intra[df_intra.index >= entry_dt]

            # Next day data (pre-market until 09:30)
            next_date = date_t + timedelta(days=1)
            next_date_str = next_date.strftime("%Y-%m-%d")
            next_intraday_file = f'data/{ticker}/intraday/{next_date_str}.parquet'

            if os.path.exists(next_intraday_file):
                df_next = pd.read_parquet(next_intraday_file)
                if df_next.index.tz is not None:
                    df_next.index = df_next.index.tz_convert('America/New_York')
                else:
                    df_next.index = df_next.index.tz_localize('UTC').tz_convert('America/New_York')

                # Filter to before 09:30 ET
                market_open = et_tz.localize(datetime(next_date.year, next_date.month, next_date.day, 9, 30))
                window_day_next = df_next[df_next.index < market_open]

                # Combine windows
                window = pd.concat([window_day_t, window_day_next])
            else:
                window = window_day_t

            if window.empty:
                missing_data += 1
                continue

            # Check if target hit
            if signal == "FADE_GREEN":  # PUT - target below
                target_hit = window['Low'].min() <= target_price
            else:  # CALL - target above
                target_hit = window['High'].max() >= target_price

            # Record trade
            result = "WIN" if target_hit else "LOSS"
            pnl_mult = 0.45 if target_hit else -1.05  # After 5% slippage

            trades.append({
                'Date': date_str,
                'Ticker': ticker,
                'Signal': signal,
                'Entry_Time': entry_time,
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

        except Exception as e:
            missing_data += 1
            continue

    if missing_data > 0:
        console.print(f"[yellow]Missing intraday data for {missing_data} days[/yellow]")

    return pd.DataFrame(trades)

def main():
    console.print("="*80)
    console.print("[bold blue]BACKTEST WITH IG.COM TIMING (16:15 ET ENTRY)[/bold blue]")
    console.print("="*80)
    console.print()
    console.print("This backtest uses 16:15 ET price as entry point")
    console.print("to simulate IG.com's order opening time.")
    console.print()

    config = load_config()
    tickers = config.get('tickers', ['SPY', 'QQQ', 'IWM', 'DIA'])

    console.print(f"[cyan]Tickers: {', '.join(tickers)}[/cyan]")
    console.print(f"[cyan]Entry Time: 16:15 ET[/cyan]")
    console.print(f"[cyan]Target: 0.1x ATR[/cyan]")
    console.print()

    # Run backtest for each ticker
    all_results = []

    for ticker in tickers:
        result = run_ig_backtest(ticker, config, entry_time="16:15")
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
    console.print("[bold white]RESULTS (16:15 ET ENTRY)[/bold white]")
    console.print("="*80)
    console.print()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold white")

    table.add_row("Entry Time", "16:15 ET (IG.com timing)")
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

    # Save results
    output_file = 'results/ig_timing_backtest_results.csv'
    df.to_csv(output_file, index=False)
    console.print(f"[green]Results saved to: {output_file}[/green]")
    console.print()

    # Compare with original backtest
    console.print("="*80)
    console.print("[bold white]COMPARISON: 16:15 ET vs 16:00 ET Entry[/bold white]")
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
        comp_table.add_column("16:00 ET\n(Original)", justify="right", width=20)
        comp_table.add_column("16:15 ET\n(IG.com)", justify="right", width=20)
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
    console.print("[bold green]IG.COM TIMING BACKTEST COMPLETE![/bold green]")
    console.print("="*80)

if __name__ == "__main__":
    main()
