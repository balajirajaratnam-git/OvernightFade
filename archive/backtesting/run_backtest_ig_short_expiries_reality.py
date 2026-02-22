"""
Backtest for IG.com SHORT EXPIRIES Strategy WITH REALITY ADJUSTMENTS

Applies reality adjustment factors to P&L calculations:
- Bid/ask spreads (SPY: 3%, QQQ: 5%, IWM: 10%, DIA: 15%)
- Slippage (0.8% to 3.1% depending on ticker)
- Theta decay (via adjustment multipliers)
- Commission ($0.65 per contract, entry + exit)

This shows what to ACTUALLY expect from paper trading vs idealized backtest.
"""
import sys
sys.path.insert(0, 'src')

import pandas as pd
import numpy as np
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from rich.console import Console
from rich.table import Table

console = Console()

def load_config():
    with open("config/config.json", "r") as f:
        return json.load(f)

def load_reality_adjustments():
    """Load reality adjustment factors."""
    adjustments_file = Path("config/reality_adjustments.json")

    if not adjustments_file.exists():
        console.print("[yellow]WARNING: reality_adjustments.json not found. Using default values.[/yellow]")
        return {
            "spread_costs": {"SPY": 0.03, "QQQ": 0.05, "IWM": 0.10, "DIA": 0.15},
            "slippage_pct": {"SPY": 0.008, "QQQ": 0.015, "IWM": 0.023, "DIA": 0.031},
            "commission_per_contract": 0.65,
            "pnl_adjustments": {
                "1_day": {"SPY": 0.72, "QQQ": 0.58, "IWM": 0.28, "DIA": 0.12},
                "2_day": {"SPY": 0.65, "QQQ": 0.51, "IWM": 0.24, "DIA": 0.09},
                "3_day": {"SPY": 0.58, "QQQ": 0.45, "IWM": 0.20, "DIA": 0.06}
            }
        }

    with open(adjustments_file, "r") as f:
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

def calculate_reality_adjusted_pnl(ticker, days_to_expiry, target_hit, adjustments, position_size=1000):
    """
    Calculate P&L with reality adjustments.

    Args:
        ticker: Ticker symbol
        days_to_expiry: 1, 2, or 3 days
        target_hit: True if WIN, False if LOSS
        adjustments: Reality adjustments dict
        position_size: Position size in dollars

    Returns:
        tuple: (backtest_pnl_mult, reality_pnl_mult)
    """
    # Backtest base assumptions (matches original backtest)
    backtest_win_mult = 0.45
    backtest_loss_mult = -1.05  # Original backtest assumption (includes implied spread/slippage)

    commission_pct = (adjustments["commission_per_contract"] * 2 / position_size)

    if not target_hit:
        # LOSS: Option expires worthless = -100% loss
        # Reality: You can only lose 100% of premium paid, plus commission
        reality_loss_mult = -1.00 - commission_pct
        return backtest_loss_mult, reality_loss_mult

    # WIN: Apply full adjustments
    expiry_key = f"{days_to_expiry}_day"
    pnl_adj = adjustments["pnl_adjustments"][expiry_key].get(ticker, 0.50)
    spread_cost = adjustments["spread_costs"].get(ticker, 0.05)
    slippage = adjustments["slippage_pct"].get(ticker, 0.01)

    # Apply adjustment factor, subtract costs
    reality_win_mult = (backtest_win_mult * pnl_adj) - spread_cost - slippage - commission_pct

    return backtest_win_mult, reality_win_mult

def run_ig_short_backtest(ticker, config, adjustments):
    """
    Run backtest with SHORT expiries (1-3 days) and reality adjustments.
    """
    console.print(f"[cyan]Running SHORT EXPIRIES backtest for {ticker} (with reality adjustments)...[/cyan]")

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

        # Record trade with BOTH backtest and reality P&L
        result = "WIN" if target_hit else "LOSS"

        # Calculate both versions
        backtest_pnl_mult, reality_pnl_mult = calculate_reality_adjusted_pnl(
            ticker, days_to_expiry, target_hit, adjustments
        )

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
            'PnL_Mult_Backtest': backtest_pnl_mult,
            'PnL_Mult_Reality': reality_pnl_mult,
            'Direction': direction,
            'Magnitude': magnitude
        })

    if missing_data > 0:
        console.print(f"[yellow]Missing intraday data for {missing_data} days[/yellow]")

    return pd.DataFrame(trades)

def calculate_equity_curve(df, starting_capital, kelly_pct, max_position, pnl_column):
    """Calculate equity curve for a given P&L column."""
    equity = starting_capital
    equity_curve = []
    position_sizes = []
    daily_pnls = []

    for idx, row in df.iterrows():
        position = min(equity * kelly_pct, max_position)
        pnl = position * row[pnl_column]
        equity += pnl

        equity_curve.append(equity)
        position_sizes.append(position)
        daily_pnls.append(pnl)

    return equity_curve, position_sizes, daily_pnls

def main():
    console.print("="*80)
    console.print("[bold blue]BACKTEST: IG.COM SHORT EXPIRIES WITH REALITY ADJUSTMENTS[/bold blue]")
    console.print("="*80)
    console.print()
    console.print("SHORT expiries (1-3 days) + Reality adjustments")
    console.print("Mon->Wed, Tue->Wed, Wed->Fri, Thu->Fri, Fri->Mon")
    console.print()

    # Load config and reality adjustments
    config = load_config()
    adjustments = load_reality_adjustments()

    tickers = config.get('tickers', ['SPY', 'QQQ', 'IWM', 'DIA'])

    console.print(f"[cyan]Tickers: {', '.join(tickers)}[/cyan]")
    console.print(f"[cyan]Target: 0.1x ATR[/cyan]")
    console.print(f"[cyan]Max Position: $1,000 per ticker[/cyan]")
    console.print()

    console.print("[bold white]Reality Adjustments Loaded:[/bold white]")
    console.print(f"  Spread costs: SPY {adjustments['spread_costs']['SPY']*100:.1f}%, QQQ {adjustments['spread_costs']['QQQ']*100:.1f}%, IWM {adjustments['spread_costs']['IWM']*100:.1f}%, DIA {adjustments['spread_costs']['DIA']*100:.1f}%")
    console.print(f"  Commission: ${adjustments['commission_per_contract']:.2f} per contract")
    console.print()

    # Run backtest for each ticker
    all_results = []

    for ticker in tickers:
        result = run_ig_short_backtest(ticker, config, adjustments)
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

    # Calculate BOTH equity curves
    starting_capital = 10000
    kelly_pct = 0.0523
    max_position = 1000

    # BACKTEST equity curve (idealized)
    equity_backtest, positions_backtest, pnls_backtest = calculate_equity_curve(
        df, starting_capital, kelly_pct, max_position, 'PnL_Mult_Backtest'
    )

    # REALITY equity curve (adjusted)
    equity_reality, positions_reality, pnls_reality = calculate_equity_curve(
        df, starting_capital, kelly_pct, max_position, 'PnL_Mult_Reality'
    )

    df['Position_Size'] = positions_backtest  # Same position sizing
    df['PnL_Backtest'] = pnls_backtest
    df['PnL_Reality'] = pnls_reality
    df['Equity_Backtest'] = equity_backtest
    df['Equity_Reality'] = equity_reality

    # Calculate statistics for BOTH versions
    df['Date'] = pd.to_datetime(df['Date'])
    years = (df['Date'].max() - df['Date'].min()).days / 365.25

    # BACKTEST stats
    final_equity_backtest = equity_backtest[-1]
    total_pnl_backtest = final_equity_backtest - starting_capital
    total_roi_backtest = (total_pnl_backtest / starting_capital) * 100
    cagr_backtest = (pow(final_equity_backtest / starting_capital, 1/years) - 1) * 100

    # REALITY stats
    final_equity_reality = equity_reality[-1]
    total_pnl_reality = final_equity_reality - starting_capital
    total_roi_reality = (total_pnl_reality / starting_capital) * 100
    cagr_reality = (pow(final_equity_reality / starting_capital, 1/years) - 1) * 100

    # Win rate (same for both)
    wins = (df['Result'] == 'WIN').sum()
    win_rate = wins / len(df) * 100

    # Drawdown (for reality)
    df['Running_Max_Reality'] = df['Equity_Reality'].expanding().max()
    df['Drawdown_Reality'] = df['Equity_Reality'] - df['Running_Max_Reality']
    df['Drawdown_Pct_Reality'] = (df['Drawdown_Reality'] / df['Running_Max_Reality']) * 100

    max_dd_reality = df['Drawdown_Reality'].min()
    max_dd_pct_reality = df['Drawdown_Pct_Reality'].min()

    # Display COMPARISON
    console.print("="*80)
    console.print("[bold white]RESULTS: BACKTEST vs REALITY[/bold white]")
    console.print("="*80)
    console.print()

    comp_table = Table(show_header=True, header_style="bold cyan")
    comp_table.add_column("Metric", style="white", width=30)
    comp_table.add_column("Backtest (Idealized)", justify="right", width=20)
    comp_table.add_column("Reality (Adjusted)", justify="right", width=20)
    comp_table.add_column("Difference", justify="right", width=15)

    comp_table.add_row("Strategy", "SHORT Expiries", "SHORT Expiries", "")
    comp_table.add_row("Period", f"{df['Date'].min().strftime('%Y-%m-%d')} to {df['Date'].max().strftime('%Y-%m-%d')}", "", "")
    comp_table.add_row("Years", f"{years:.1f}", "", "")
    comp_table.add_row("", "", "", "")
    comp_table.add_row("Starting Capital", f"${starting_capital:,.0f}", f"${starting_capital:,.0f}", "")
    comp_table.add_row("Final Equity", f"${final_equity_backtest:,.0f}", f"${final_equity_reality:,.0f}", f"${final_equity_reality - final_equity_backtest:,.0f}")
    comp_table.add_row("Total P/L", f"${total_pnl_backtest:,.0f}", f"${total_pnl_reality:,.0f}", f"${total_pnl_reality - total_pnl_backtest:,.0f}")
    comp_table.add_row("Total ROI", f"{total_roi_backtest:.1f}%", f"{total_roi_reality:.1f}%", f"{total_roi_reality - total_roi_backtest:+.1f}pp")
    comp_table.add_row("CAGR", f"[bold]{cagr_backtest:.1f}%[/bold]", f"[bold green]{cagr_reality:.1f}%[/bold green]", f"[bold]{cagr_reality - cagr_backtest:+.1f}pp[/bold]")
    comp_table.add_row("", "", "", "")
    comp_table.add_row("Total Trades", f"{len(df):,}", f"{len(df):,}", "")
    comp_table.add_row("Win Rate", f"{win_rate:.1f}%", f"{win_rate:.1f}%", "Same")
    comp_table.add_row("", "", "", "")
    comp_table.add_row("Max Drawdown", "N/A", f"${max_dd_reality:,.0f} ({max_dd_pct_reality:.1f}%)", "")

    console.print(comp_table)
    console.print()

    # Reality percentage
    reality_pct = (cagr_reality / cagr_backtest) * 100 if cagr_backtest > 0 else 0
    console.print(f"[bold]Reality is {reality_pct:.1f}% of backtest CAGR[/bold]")
    console.print()

    # Breakdown by ticker
    console.print("="*80)
    console.print("[bold white]BREAKDOWN BY TICKER (Reality-Adjusted)[/bold white]")
    console.print("="*80)
    console.print()

    ticker_table = Table(show_header=True, header_style="bold cyan")
    ticker_table.add_column("Ticker", width=8)
    ticker_table.add_column("Trades", justify="right", width=10)
    ticker_table.add_column("Win Rate", justify="right", width=10)
    ticker_table.add_column("Avg Win (Reality)", justify="right", width=18)
    ticker_table.add_column("Avg Loss (Reality)", justify="right", width=18)
    ticker_table.add_column("Expected Value", justify="right", width=15)

    for ticker in tickers:
        df_ticker = df[df['Ticker'] == ticker]
        if len(df_ticker) > 0:
            ticker_wins = (df_ticker['Result'] == 'WIN').sum()
            ticker_wr = ticker_wins / len(df_ticker) * 100

            avg_win_reality = df_ticker[df_ticker['Result'] == 'WIN']['PnL_Mult_Reality'].mean() * 100
            avg_loss_reality = df_ticker[df_ticker['Result'] == 'LOSS']['PnL_Mult_Reality'].mean() * 100

            # Expected value per trade
            exp_value = (ticker_wr/100) * avg_win_reality + ((100-ticker_wr)/100) * avg_loss_reality

            # Color code based on expected value
            if exp_value > 10:
                ev_style = "green"
            elif exp_value > 0:
                ev_style = "yellow"
            else:
                ev_style = "red"

            ticker_table.add_row(
                ticker,
                f"{len(df_ticker):,}",
                f"{ticker_wr:.1f}%",
                f"+{avg_win_reality:.1f}%",
                f"{avg_loss_reality:.1f}%",
                f"[{ev_style}]{exp_value:+.1f}%[/{ev_style}]"
            )

    console.print(ticker_table)
    console.print()

    # Breakdown by expiry type
    console.print("="*80)
    console.print("[bold white]BREAKDOWN BY EXPIRY PATTERN (Reality-Adjusted)[/bold white]")
    console.print("="*80)
    console.print()

    expiry_table = Table(show_header=True, header_style="bold cyan")
    expiry_table.add_column("Expiry Pattern", width=15)
    expiry_table.add_column("Trades", justify="right", width=10)
    expiry_table.add_column("Win Rate", justify="right", width=10)
    expiry_table.add_column("Avg Win (Reality)", justify="right", width=18)
    expiry_table.add_column("Avg Loss (Reality)", justify="right", width=18)

    for expiry_label in sorted(df['Expiry_Label'].unique()):
        df_type = df[df['Expiry_Label'] == expiry_label]
        if len(df_type) > 0:
            type_wins = (df_type['Result'] == 'WIN').sum()
            type_wr = type_wins / len(df_type) * 100

            avg_win_reality = df_type[df_type['Result'] == 'WIN']['PnL_Mult_Reality'].mean() * 100
            avg_loss_reality = df_type[df_type['Result'] == 'LOSS']['PnL_Mult_Reality'].mean() * 100

            expiry_table.add_row(
                expiry_label,
                f"{len(df_type):,}",
                f"{type_wr:.1f}%",
                f"+{avg_win_reality:.1f}%",
                f"{avg_loss_reality:.1f}%"
            )

    console.print(expiry_table)
    console.print()

    # Save results
    output_file = 'results/ig_short_expiries_reality_backtest.csv'
    df.to_csv(output_file, index=False)
    console.print(f"[green]Results saved to: {output_file}[/green]")
    console.print()

    # RECOMMENDATIONS
    console.print("="*80)
    console.print("[bold white]RECOMMENDATIONS BASED ON REALITY ADJUSTMENTS[/bold white]")
    console.print("="*80)
    console.print()

    # Find best tickers
    best_tickers = []
    for ticker in tickers:
        df_ticker = df[df['Ticker'] == ticker]
        if len(df_ticker) > 0:
            avg_win_reality = df_ticker[df_ticker['Result'] == 'WIN']['PnL_Mult_Reality'].mean() * 100
            best_tickers.append((ticker, avg_win_reality))

    best_tickers.sort(key=lambda x: x[1], reverse=True)

    console.print("[bold]Best Tickers (by avg win P&L):[/bold]")
    for ticker, avg_win in best_tickers:
        if avg_win > 25:
            console.print(f"  [green]{ticker}: +{avg_win:.1f}% (GOOD - Trade this)[/green]")
        elif avg_win > 15:
            console.print(f"  [yellow]{ticker}: +{avg_win:.1f}% (OK - Consider)[/yellow]")
        else:
            console.print(f"  [red]{ticker}: +{avg_win:.1f}% (POOR - Avoid)[/red]")

    console.print()
    console.print("[bold]Recommended Strategy:[/bold]")
    good_tickers = [t for t, w in best_tickers if w > 25]
    if good_tickers:
        console.print(f"  Trade: {', '.join(good_tickers)}")

        # Recalculate with only good tickers
        df_good = df[df['Ticker'].isin(good_tickers)]
        equity_good, _, _ = calculate_equity_curve(
            df_good, starting_capital, kelly_pct, max_position, 'PnL_Mult_Reality'
        )
        years_good = (df_good['Date'].max() - df_good['Date'].min()).days / 365.25
        cagr_good = (pow(equity_good[-1] / starting_capital, 1/years_good) - 1) * 100

        console.print(f"  Expected CAGR (good tickers only): [bold green]{cagr_good:.1f}%[/bold green]")

    console.print()

    console.print("="*80)
    console.print("[bold green]REALITY-ADJUSTED BACKTEST COMPLETE![/bold green]")
    console.print("="*80)
    console.print()
    console.print("[bold white]Summary:[/bold white]")
    console.print(f"  Backtest CAGR (idealized): {cagr_backtest:.1f}%")
    console.print(f"  Reality CAGR (adjusted): [bold]{cagr_reality:.1f}%[/bold]")
    console.print(f"  Difference: {cagr_reality - cagr_backtest:+.1f}pp ({reality_pct:.1f}% of backtest)")
    console.print()
    console.print("Reality adjustments include:")
    console.print("  - Bid/ask spreads (3-15% depending on ticker)")
    console.print("  - Slippage (0.8-3.1%)")
    console.print("  - Theta decay (via adjustment factors)")
    console.print("  - Commission ($0.65 per contract x2)")
    console.print()

if __name__ == "__main__":
    main()
