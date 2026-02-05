"""
IG.com Auto-Trader - Phase 1: Dry-Run Mode (SHORT EXPIRIES STRATEGY)

This script automates the entire workflow from data fetch to order calculation:
1. Checks day of week and determines expiry (Mon->Wed, Tue->Wed, Wed->Fri, Thu->Fri, Fri->Mon)
2. Polls Polygon.io until 16:00 ET close data is available
3. Fetches latest data via DataManager
4. Generates signals using dashboard logic
5. Calculates strikes, expiries, and limit orders
6. Applies REALITY ADJUSTMENTS to show expected P&L outcomes
7. Logs everything to CSV for verification

SHORT EXPIRIES STRATEGY (1-3 day expiries):
- Monday -> Wednesday (2 days)
- Tuesday -> Wednesday (1 day)
- Wednesday -> Friday (2 days)
- Thursday -> Friday (1 day)
- Friday -> Monday (3 days)

REALITY ADJUSTMENTS:
- Incorporates bid/ask spreads, slippage, theta decay, commission
- Shows both "Backtest Assumption" (+45%) and "Realistic Expectation"
- Based on Black-Scholes modeling (to be calibrated with paper trading)

Phase 1: DRY-RUN MODE (No actual IG.com API calls)
- Calculates what orders WOULD be placed
- Logs all details for verification
- Shows realistic P&L expectations

Usage:
    python auto_trade_ig.py                    # Dry-run mode (default)
    python auto_trade_ig.py --force-run        # Force run any day of week
    python auto_trade_ig.py --tickers SPY QQQ  # Specify tickers (default: SPY QQQ)
"""
import os
import sys

# Enable network access for DataManager
os.environ['ALLOW_NETWORK'] = '1'
import json
import time
import pandas as pd
from datetime import datetime, timedelta, date
from pathlib import Path
import pytz

# Add src to path
sys.path.insert(0, 'src')

from data_manager import DataManager
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

# Timezones
TZ_UK = pytz.timezone('Europe/London')
TZ_ET = pytz.timezone('America/New_York')

# Day names mapping
DAY_NAMES = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday", 4: "Friday", 5: "Saturday", 6: "Sunday"}

# Polling configuration
MAX_POLL_ATTEMPTS = 15  # Max 15 minutes of polling
POLL_INTERVAL_SECONDS = 60  # Check every 60 seconds

# Ensure logs directory exists
LOGS_DIR = Path("logs")
LOGS_DIR.mkdir(exist_ok=True)


def load_config():
    """Load main config."""
    with open("config/config.json", "r") as f:
        return json.load(f)


def save_config(config):
    """Save main config."""
    with open("config/config.json", "w") as f:
        json.dump(config, f, indent=2)


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


def check_trading_day():
    """
    Determine today's trading info and expiry type for SHORT EXPIRIES STRATEGY.

    Strategy:
    - Monday -> Wednesday (2 days)
    - Tuesday -> Wednesday (1 day)
    - Wednesday -> Friday (2 days)
    - Thursday -> Friday (1 day)
    - Friday -> Monday (3 days)

    Returns:
        tuple: (day_name, expiry_target_day, days_to_expiry)
    """
    now_uk = datetime.now(TZ_UK)
    today = now_uk.date()
    day_of_week = today.weekday()  # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri

    day_name = DAY_NAMES[day_of_week]

    # Determine expiry based on SHORT EXPIRIES STRATEGY
    if day_of_week == 0:  # Monday
        expiry_target_day = "Wednesday"
        days_to_expiry = 2
    elif day_of_week == 1:  # Tuesday
        expiry_target_day = "Wednesday"
        days_to_expiry = 1
    elif day_of_week == 2:  # Wednesday
        expiry_target_day = "Friday"
        days_to_expiry = 2
    elif day_of_week == 3:  # Thursday
        expiry_target_day = "Friday"
        days_to_expiry = 1
    elif day_of_week == 4:  # Friday
        expiry_target_day = "Monday"
        days_to_expiry = 3
    else:
        # Weekend (shouldn't happen)
        expiry_target_day = "N/A"
        days_to_expiry = 0

    return day_name, expiry_target_day, days_to_expiry


def calculate_expiry_date(today, days_to_expiry):
    """
    Calculate expiry date based on days to expiry.

    Args:
        today: date object
        days_to_expiry: Number of days to expiry (1, 2, or 3)

    Returns:
        date: Expiry date
    """
    expiry = today + timedelta(days=days_to_expiry)

    # Skip weekends (should not happen with SHORT EXPIRIES STRATEGY, but safety check)
    while expiry.weekday() >= 5:  # Sat=5, Sun=6
        expiry += timedelta(days=1)

    return expiry


def wait_for_polygon_data(ticker, target_date_str, max_attempts=MAX_POLL_ATTEMPTS):
    """
    Poll Polygon.io until target date data is available.

    Args:
        ticker: Ticker symbol
        target_date_str: Target date string (YYYY-MM-DD)
        max_attempts: Maximum polling attempts

    Returns:
        bool: True if data available, False if timeout
    """
    console.print(f"[cyan]Waiting for {ticker} data for {target_date_str}...[/cyan]")

    config = load_config()
    original_ticker = config["ticker"]
    config["ticker"] = ticker
    save_config(config)

    try:
        for attempt in range(1, max_attempts + 1):
            try:
                dm = DataManager()

                # Check if daily file exists and has target date
                daily_file = os.path.join(dm.ticker_dir, "daily_OHLCV.parquet")

                if os.path.exists(daily_file):
                    df = pd.read_parquet(daily_file)

                    if not df.empty:
                        last_date = df.index[-1].strftime("%Y-%m-%d")

                        if last_date >= target_date_str:
                            console.print(f"[green]OK {ticker} data available (last: {last_date})[/green]")
                            return True

                if attempt < max_attempts:
                    console.print(f"[yellow]  Attempt {attempt}/{max_attempts}: Data not ready, waiting {POLL_INTERVAL_SECONDS}s...[/yellow]")
                    time.sleep(POLL_INTERVAL_SECONDS)
                else:
                    console.print(f"[red]X {ticker} data not available after {max_attempts} attempts[/red]")
                    return False

            except Exception as e:
                console.print(f"[red]Error checking data: {e}[/red]")
                if attempt < max_attempts:
                    time.sleep(POLL_INTERVAL_SECONDS)
                else:
                    return False

    finally:
        # Restore original ticker
        config["ticker"] = original_ticker
        save_config(config)

    return False


def fetch_latest_data(ticker):
    """
    Fetch latest data for ticker using DataManager.

    Args:
        ticker: Ticker symbol

    Returns:
        bool: True if successful
    """
    config = load_config()
    original_ticker = config["ticker"]
    config["ticker"] = ticker
    save_config(config)

    try:
        console.print(f"[cyan]Fetching latest data for {ticker}...[/cyan]")
        dm = DataManager()
        dm.update_daily_data()
        console.print(f"[green]OK {ticker} data updated[/green]")
        return True

    except Exception as e:
        console.print(f"[red]X Failed to fetch {ticker}: {e}[/red]")
        return False

    finally:
        config["ticker"] = original_ticker
        save_config(config)


def get_ticker_context(ticker):
    """
    Get market context for ticker (last trading day).

    Args:
        ticker: Ticker symbol

    Returns:
        dict: Market context or None
    """
    config = load_config()
    original_ticker = config["ticker"]
    config["ticker"] = ticker
    save_config(config)

    try:
        daily_file = os.path.join("data", ticker, "daily_OHLCV.parquet")

        if not os.path.exists(daily_file):
            return None

        df = pd.read_parquet(daily_file)

        if df.empty:
            return None

        # Get last row
        last_date = df.index[-1]
        day_data = df.iloc[-1]

        context = {
            "Ticker": ticker,
            "Date": last_date,
            "Close": day_data["Close"],
            "Open": day_data["Open"],
            "High": day_data["High"],
            "Low": day_data["Low"],
            "Direction": day_data["Direction"],
            "Magnitude": day_data["Magnitude"],
            "ATR": day_data["ATR_14"]
        }

        return context

    except Exception as e:
        console.print(f"[red]Error loading {ticker} context: {e}[/red]")
        return None

    finally:
        config["ticker"] = original_ticker
        save_config(config)


def generate_signal(context, flat_threshold=0.10):
    """
    Generate signal using dashboard logic (unfiltered strategy).

    Args:
        context: Market context dict
        flat_threshold: Flat day threshold percentage

    Returns:
        tuple: (signal, reason)
    """
    # Filter 1: Flat day
    if abs(context["Magnitude"]) < flat_threshold:
        return "NO_TRADE", f"Flat day (magnitude < {flat_threshold}%)"

    # Generate signal based on direction
    if context["Direction"] == "GREEN":
        return "BUY PUT", f"GREEN day +{context['Magnitude']:.2f}% (Fade Down)"
    elif context["Direction"] == "RED":
        return "BUY CALL", f"RED day {context['Magnitude']:.2f}% (Fade Up)"

    return "NO_TRADE", "Unknown direction"


def calculate_expected_pnl(ticker, days_to_expiry, adjustments):
    """
    Calculate expected P&L outcomes with reality adjustments.

    Args:
        ticker: Ticker symbol (SPY, QQQ, IWM, DIA)
        days_to_expiry: 1, 2, or 3 days
        adjustments: Reality adjustments dict

    Returns:
        dict: Expected P&L metrics
    """
    # Backtest base assumption
    backtest_win_pnl_pct = 45.0
    backtest_loss_pnl_pct = -100.0  # Assuming total loss on losing trades

    # Get adjustment factors
    expiry_key = f"{days_to_expiry}_day"
    pnl_adj = adjustments["pnl_adjustments"][expiry_key].get(ticker, 0.50)
    spread_cost = adjustments["spread_costs"].get(ticker, 0.05)
    slippage = adjustments["slippage_pct"].get(ticker, 0.01)
    commission = adjustments["commission_per_contract"]

    # Realistic WIN P&L (apply adjustment, subtract costs)
    realistic_win_pnl_pct = (backtest_win_pnl_pct * pnl_adj) - (spread_cost * 100) - (slippage * 100)

    # Assume $1000 position size for commission calculation
    commission_pct = (commission * 2 / 1000) * 100  # Entry + exit commission as %

    realistic_win_pnl_pct -= commission_pct

    # Realistic LOSS P&L (losses may be slightly worse due to spreads)
    realistic_loss_pnl_pct = backtest_loss_pnl_pct - (spread_cost * 100) - commission_pct

    return {
        "backtest_win_pct": backtest_win_pnl_pct,
        "realistic_win_pct": realistic_win_pnl_pct,
        "backtest_loss_pct": backtest_loss_pnl_pct,
        "realistic_loss_pct": realistic_loss_pnl_pct,
        "adjustment_factor": pnl_adj,
        "spread_cost_pct": spread_cost * 100,
        "slippage_pct": slippage * 100,
        "commission_pct": commission_pct
    }


def calculate_strike_us500(close_price):
    """
    Calculate ATM strike for US 500 (5-point increments).

    Args:
        close_price: Close price (SPY * 10)

    Returns:
        int: Strike price
    """
    return int(round(close_price / 5) * 5)


def calculate_strike_iwm(close_price):
    """
    Calculate ATM strike for IWM (1-point increments).

    Args:
        close_price: Close price

    Returns:
        int: Strike price
    """
    return int(round(close_price))


def calculate_order_details(ticker, context, signal, expiry_date, days_to_expiry, adjustments, atr_mult=0.1):
    """
    Calculate complete order details with reality adjustments.

    Args:
        ticker: Display ticker (US 500, QQQ, IWM, DIA)
        context: Market context
        signal: Trading signal
        expiry_date: Expiry date
        days_to_expiry: Days to expiry (1, 2, or 3)
        adjustments: Reality adjustments dict
        atr_mult: ATR multiplier for target

    Returns:
        dict: Order details with expected P&L
    """
    close = context["Close"]
    atr = context["ATR"]

    # For SPY, convert to US 500
    if context["Ticker"] == "SPY":
        display_ticker = "US 500"
        close_us500 = close * 10
        atr_us500 = atr * 10
        strike = calculate_strike_us500(close_us500)
        current_price = close_us500
        current_atr = atr_us500
    elif context["Ticker"] == "IWM":
        display_ticker = "IWM"
        strike = calculate_strike_iwm(close)
        current_price = close
        current_atr = atr
    else:
        # QQQ, DIA, or other
        display_ticker = context["Ticker"]
        strike = int(round(close))
        current_price = close
        current_atr = atr

    # Determine option type
    option_type = "PUT" if "PUT" in signal else "CALL"

    # Calculate target and limit
    target_move = current_atr * atr_mult

    if option_type == "PUT":
        limit_price = current_price - target_move
        limit_pts = -target_move
        direction = "DOWN"
    else:
        limit_price = current_price + target_move
        limit_pts = target_move
        direction = "UP"

    # Calculate expected P&L with reality adjustments
    expected_pnl = calculate_expected_pnl(context["Ticker"], days_to_expiry, adjustments)

    return {
        "Display_Ticker": display_ticker,
        "Source_Ticker": context["Ticker"],
        "Date": context["Date"].strftime("%Y-%m-%d"),
        "Signal": signal,
        "Option_Type": option_type,
        "Direction": direction,
        "Current_Price": current_price,
        "ATR": current_atr,
        "Strike": strike,
        "Expiry_Date": expiry_date.strftime("%Y-%m-%d"),
        "Days_To_Expiry": days_to_expiry,
        "Limit_Price": limit_price,
        "Limit_Pts": limit_pts,
        "Target_Move": target_move,
        "Magnitude": context["Magnitude"],
        "Day_Direction": context["Direction"],
        "Expected_PnL": expected_pnl
    }


def log_order_to_csv(order_details, log_file="logs/ig_orders_dryrun.csv"):
    """
    Log order details to CSV for audit trail.

    Args:
        order_details: Order details dict
        log_file: Path to log file
    """
    log_path = Path(log_file)

    # Create DataFrame
    df = pd.DataFrame([order_details])

    # Append or create
    if log_path.exists():
        df.to_csv(log_path, mode='a', header=False, index=False)
    else:
        df.to_csv(log_path, mode='w', header=True, index=False)


def display_order_summary(orders):
    """
    Display summary of calculated orders with reality-adjusted P&L expectations.
    Shows details for BOTH IG.com and IBKR.

    Args:
        orders: List of order detail dicts
    """
    if not orders:
        console.print(Panel(
            "[bold yellow]NO TRADES TODAY[/bold yellow]\n\nAll tickers filtered (flat days)",
            title="DRY-RUN SUMMARY",
            border_style="yellow"
        ))
        return

    console.print()
    console.print("=" * 80)
    console.print("[bold green]DRY-RUN: ORDERS THAT WOULD BE PLACED[/bold green]")
    console.print("=" * 80)
    console.print()
    console.print("[cyan]Showing details for BOTH IG.com and Interactive Brokers (IBKR)[/cyan]")
    console.print()

    table = Table(show_header=True, header_style="bold cyan", title="Order Summary (IG.com format)")
    table.add_column("IG Ticker", style="cyan", width=10)
    table.add_column("IBKR Ticker", style="cyan", width=10)
    table.add_column("Signal", width=10)
    table.add_column("Strike (IG)", justify="right", width=12)
    table.add_column("Strike (IBKR)", justify="right", width=12)
    table.add_column("Expiry", width=12)
    table.add_column("DTE", justify="right", width=5)
    table.add_column("Realistic P&L", justify="right", width=15)

    for order in orders:
        signal_style = "green" if "BUY" in order["Signal"] else "yellow"
        pnl = order["Expected_PnL"]
        source_ticker = order["Source_Ticker"]

        # Calculate IBKR values
        if source_ticker == "SPY":
            ig_ticker = "US 500"
            ibkr_ticker = "SPY"
            ig_strike = order['Strike']
            ibkr_strike = int(round(order['Current_Price'] / 10))
        else:
            ig_ticker = source_ticker
            ibkr_ticker = source_ticker
            ig_strike = order['Strike']
            ibkr_strike = order['Strike']

        # Color code realistic P&L (green if good, yellow if marginal, red if poor)
        if pnl["realistic_win_pct"] > 30:
            pnl_style = "green"
        elif pnl["realistic_win_pct"] > 15:
            pnl_style = "yellow"
        else:
            pnl_style = "red"

        table.add_row(
            ig_ticker,
            ibkr_ticker,
            f"[{signal_style}]{order['Option_Type']}[/{signal_style}]",
            str(ig_strike),
            str(ibkr_strike),
            order["Expiry_Date"],
            str(order["Days_To_Expiry"]),
            f"[{pnl_style}]{pnl['realistic_win_pct']:+.1f}%[/{pnl_style}]"
        )

    console.print(table)
    console.print()
    console.print("[cyan]IG.com: SPY trades as US 500 (SPY * 10, strike in 5-pt increments)[/cyan]")
    console.print("[cyan]IBKR: SPY trades as SPY (normal pricing, strike in $1 increments)[/cyan]")
    console.print()

    # Detailed breakdown
    for i, order in enumerate(orders, 1):
        pnl = order["Expected_PnL"]
        source_ticker = order["Source_Ticker"]

        console.print(f"[bold cyan]Order {i}: {order['Display_Ticker']} {order['Option_Type']}[/bold cyan]")
        console.print(f"  Date: {order['Date']}")
        console.print(f"  Day Direction: {order['Day_Direction']} ({order['Magnitude']:+.2f}%)")
        console.print(f"  Signal: {order['Signal']}")
        console.print()

        # IG.COM DETAILS
        console.print(f"  [bold white]IG.com Order Details:[/bold white]")
        if source_ticker == "SPY":
            # IG.com uses US 500 (SPY * 10)
            ig_ticker = "US 500"
            ig_strike = order['Strike']  # Already calculated for US 500
            ig_current = order['Current_Price']
            ig_limit = order['Limit_Price']
            console.print(f"    Ticker: [bold]{ig_ticker}[/bold] (SPY * 10)")
        else:
            # Other tickers trade directly
            ig_ticker = source_ticker
            ig_strike = order['Strike']
            ig_current = order['Current_Price']
            ig_limit = order['Limit_Price']
            console.print(f"    Ticker: [bold]{ig_ticker}[/bold]")

        console.print(f"    Option Type: {order['Option_Type']}")
        console.print(f"    Strike: {ig_strike} (ATM)")
        console.print(f"    Expiry: {order['Expiry_Date']} ({order['Days_To_Expiry']}-day)")
        console.print(f"    Underlying at Entry: {ig_current:.2f}")
        console.print(f"    Target (Limit): {ig_limit:.2f} ({order['Direction']} {abs(order['Limit_Pts']):.2f} pts)")
        console.print(f"    Order Type: Limit order at {ig_limit:.2f}")
        console.print()

        # IBKR DETAILS
        console.print(f"  [bold white]IBKR (Interactive Brokers) Order Details:[/bold white]")
        if source_ticker == "SPY":
            # IBKR uses SPY directly (not multiplied by 10)
            ibkr_ticker = "SPY"
            ibkr_strike = int(round(order['Current_Price'] / 10))  # Convert back to SPY strike
            ibkr_current = order['Current_Price'] / 10
            ibkr_limit = order['Limit_Price'] / 10
            ibkr_limit_pts = order['Limit_Pts'] / 10
            console.print(f"    Ticker: [bold]{ibkr_ticker}[/bold]")
        else:
            # Other tickers trade directly (same as IG)
            ibkr_ticker = source_ticker
            ibkr_strike = order['Strike']
            ibkr_current = order['Current_Price']
            ibkr_limit = order['Limit_Price']
            ibkr_limit_pts = order['Limit_Pts']
            console.print(f"    Ticker: [bold]{ibkr_ticker}[/bold]")

        console.print(f"    Option Type: {order['Option_Type']}")
        console.print(f"    Strike: {ibkr_strike} (ATM)")
        console.print(f"    Expiry: {order['Expiry_Date']} ({order['Days_To_Expiry']}-day)")
        console.print(f"    Underlying at Entry: {ibkr_current:.2f}")
        console.print(f"    Target (Limit): {ibkr_limit:.2f} ({order['Direction']} {abs(ibkr_limit_pts):.2f} pts)")
        console.print(f"    Order Type: Limit order at {ibkr_limit:.2f}")
        console.print()

        # P&L EXPECTATIONS (same for both brokers)
        console.print(f"  [bold white]Expected P&L (with reality adjustments):[/bold white]")
        console.print(f"    Backtest Assumption (WIN): [dim]+{pnl['backtest_win_pct']:.0f}%[/dim]")
        console.print(f"    Realistic Expectation (WIN): [bold green]+{pnl['realistic_win_pct']:.1f}%[/bold green]")
        console.print(f"    Realistic Expectation (LOSS): [bold red]{pnl['realistic_loss_pct']:.1f}%[/bold red]")
        console.print()
        console.print(f"    Adjustment Factor: {pnl['adjustment_factor']:.2f}x")
        console.print(f"    Spread Cost: -{pnl['spread_cost_pct']:.1f}%")
        console.print(f"    Slippage: -{pnl['slippage_pct']:.1f}%")
        console.print(f"    Commission: -{pnl['commission_pct']:.2f}%")
        console.print()
        console.print(f"  [bold yellow]NOTE:[/bold yellow] {order['Days_To_Expiry']}-day expiries available on both IG.com and IBKR")
        console.print()


def main(force_run=False, tickers=None):
    """
    Main execution.

    Args:
        force_run: Force run even on non-trading days
        tickers: List of tickers to trade (default: ["SPY", "QQQ"])
    """
    console.clear()
    console.print(Panel.fit(
        "[bold blue]IG.com Auto-Trader - Phase 1: DRY-RUN MODE (SHORT EXPIRIES)[/bold blue]",
        border_style="blue"
    ))
    console.print()

    # Load reality adjustments
    console.print("[cyan]Loading reality adjustment factors...[/cyan]")
    adjustments = load_reality_adjustments()

    if not adjustments.get("calibration_status", {}).get("is_calibrated", False):
        console.print("[yellow]NOTE: Using ESTIMATED adjustment factors (not yet calibrated with paper trading)[/yellow]")
        console.print("[yellow]      See REALITY_CALIBRATION_GUIDE.md for 3-month calibration process[/yellow]")
    else:
        weeks = adjustments["calibration_status"]["weeks_of_data"]
        console.print(f"[green]Using calibrated adjustment factors ({weeks} weeks of paper trading data)[/green]")

    console.print()

    # Default tickers
    if tickers is None:
        tickers = ["SPY"]  # SPY only - best expected value (+8.7% per trade)

    console.print(f"[cyan]Tickers to trade: {', '.join(tickers)}[/cyan]")

    # Show recommendations if trading non-recommended tickers
    recommended = ["SPY"]  # Only SPY has positive expected value
    avoid = ["QQQ", "IWM", "DIA"]  # All have negative expected value

    for ticker in tickers:
        if ticker in avoid:
            if ticker == "QQQ":
                console.print(f"[yellow]WARNING: {ticker} has negative expected value (-5.8%). Recommend SPY only.[/yellow]")
            else:
                console.print(f"[red]WARNING: {ticker} has very poor expected value. Strongly recommend avoiding.[/red]")

    console.print()

    # Get current time
    now_uk = datetime.now(TZ_UK)
    now_et = datetime.now(TZ_ET)

    console.print(f"[cyan]Current Time (UK): {now_uk.strftime('%Y-%m-%d %H:%M:%S %Z')}[/cyan]")
    console.print(f"[cyan]Current Time (ET): {now_et.strftime('%Y-%m-%d %H:%M:%S %Z')}[/cyan]")
    console.print()

    # Get trading day info (SHORT EXPIRIES STRATEGY)
    day_name, expiry_target_day, days_to_expiry = check_trading_day()

    console.print(f"[bold cyan]Trading Day: {day_name}[/bold cyan]")
    console.print(f"[bold cyan]Expiry Target: {expiry_target_day} ({days_to_expiry}-day)[/bold cyan]")
    console.print(f"[bold cyan]Strategy: SHORT EXPIRIES (1-3 day options)[/bold cyan]")
    console.print()

    # Display trading strategy note
    today = now_uk.date()
    day_of_week = today.weekday()

    if day_of_week in [0, 1, 2, 3, 4]:  # Mon-Fri
        console.print(f"[green]Trading day: {day_name} -> {expiry_target_day} ({days_to_expiry}-day expiry)[/green]")

        if day_of_week == 4:  # Friday
            console.print("[yellow]NOTE: Friday trades (Fri->Mon 3-day) have lower performance (69% WR, +5.3% avg win)[/yellow]")
            console.print("[yellow]      Recommended to skip for live trading, but OK for paper trading practice[/yellow]")
    else:
        console.print(f"[yellow]Weekend: Markets closed[/yellow]")

    console.print()

    # Calculate expiry date
    today = now_uk.date()
    expiry_date = calculate_expiry_date(today, days_to_expiry)
    console.print(f"[cyan]Expiry Date: {expiry_date.strftime('%Y-%m-%d (%A)')}[/cyan]")
    console.print()

    # Target date for data
    # If before 16:05 ET, use yesterday's close (today's not ready yet)
    # If after 16:05 ET, wait for today's close
    market_close_time = TZ_ET.localize(datetime.combine(today, datetime.strptime("16:05", "%H:%M").time()))

    if now_et < market_close_time:
        # Before market close + 5 min buffer, use yesterday's data
        target_date = today - timedelta(days=1)
        console.print(f"[yellow]Before 16:05 ET - Using yesterday's data ({target_date.strftime('%Y-%m-%d')})[/yellow]")
    else:
        # After market close, wait for today's data
        target_date = today
        console.print(f"[cyan]After 16:05 ET - Waiting for today's data ({target_date.strftime('%Y-%m-%d')})[/cyan]")

    target_date_str = target_date.strftime("%Y-%m-%d")
    console.print()

    console.print("=" * 80)
    console.print("[bold white]STEP 1: WAIT FOR POLYGON.IO DATA[/bold white]")
    console.print("=" * 80)
    console.print()

    # Wait for data availability
    data_ready = {}
    for ticker in tickers:
        ready = wait_for_polygon_data(ticker, target_date_str)
        data_ready[ticker] = ready

    if not all(data_ready.values()):
        console.print()
        console.print(Panel(
            "[bold red]DATA TIMEOUT[/bold red]\n\n"
            "Polygon.io data not available after 15 minutes.\n"
            "Try running again in a few minutes.",
            border_style="red"
        ))
        return

    console.print()
    console.print("=" * 80)
    console.print("[bold white]STEP 2: FETCH LATEST DATA[/bold white]")
    console.print("=" * 80)
    console.print()

    # Fetch latest data
    fetch_success = {}
    for ticker in tickers:
        success = fetch_latest_data(ticker)
        fetch_success[ticker] = success

    if not all(fetch_success.values()):
        console.print()
        console.print(Panel(
            "[bold red]FETCH FAILED[/bold red]\n\n"
            "Failed to fetch data for one or more tickers.",
            border_style="red"
        ))
        return

    console.print()
    console.print("=" * 80)
    console.print("[bold white]STEP 3: GENERATE SIGNALS[/bold white]")
    console.print("=" * 80)
    console.print()

    # Generate signals
    orders = []

    for ticker in tickers:
        console.print(f"[cyan]Analyzing {ticker}...[/cyan]")

        # Get context
        ctx = get_ticker_context(ticker)

        if not ctx:
            console.print(f"[red]  X Failed to load context[/red]")
            continue

        # Generate signal
        signal, reason = generate_signal(ctx)

        console.print(f"  Date: {ctx['Date'].strftime('%Y-%m-%d')}")
        console.print(f"  Close: ${ctx['Close']:.2f}")
        console.print(f"  Direction: {ctx['Direction']} ({ctx['Magnitude']:+.2f}%)")
        console.print(f"  ATR: ${ctx['ATR']:.2f}")
        console.print(f"  Signal: [bold]{signal}[/bold]")
        console.print(f"  Reason: {reason}")

        if signal != "NO_TRADE":
            # Calculate order details with reality adjustments
            display_ticker = "US 500" if ticker == "SPY" else ticker
            order = calculate_order_details(display_ticker, ctx, signal, expiry_date, days_to_expiry, adjustments)
            orders.append(order)

            # Log to CSV
            log_order_to_csv(order)

            console.print(f"  [green]OK Order calculated and logged[/green]")

        console.print()

    console.print()
    console.print("=" * 80)
    console.print("[bold white]STEP 4: ORDER SUMMARY[/bold white]")
    console.print("=" * 80)

    # Display summary
    display_order_summary(orders)

    # Final message
    console.print("=" * 80)
    console.print("[bold blue]DRY-RUN COMPLETE[/bold blue]")
    console.print("=" * 80)
    console.print()
    console.print(f"[cyan]Orders logged to: logs/ig_orders_dryrun.csv[/cyan]")
    console.print()
    console.print("[bold white]REALITY ADJUSTMENTS APPLIED:[/bold white]")
    console.print("  - P&L expectations include spreads, slippage, theta decay, and commissions")
    console.print("  - Adjustment factors based on Black-Scholes modeling")

    if not adjustments.get("calibration_status", {}).get("is_calibrated", False):
        console.print("  - [yellow]NOT YET CALIBRATED[/yellow]: Start paper trading to refine these estimates")
        console.print("  - See REALITY_CALIBRATION_GUIDE.md for full 3-month calibration process")

    console.print()
    console.print("[yellow]Phase 1: No actual orders placed (dry-run mode)[/yellow]")
    console.print("[yellow]Next: Phase 2 will add broker API integration (IG.com or IBKR)[/yellow]")
    console.print()
    console.print("[bold white]Trading Days:[/bold white]")
    console.print("  Mon-Thu: Recommended (2-day and 1-day expiries, 78-90% WR)")
    console.print("  Friday: Included for paper trading (3-day expiry, 69% WR, lower performance)")
    console.print()
    console.print("[cyan]For paper trading workflow: See DAILY_PAPER_TRADING_CHECKLIST.md[/cyan]")
    console.print()
    console.print("[bold white]Broker Differences:[/bold white]")
    console.print("  IG.com: SPY trades as 'US 500' (SPY * 10), strikes in 5-pt increments")
    console.print("  IBKR: SPY trades as 'SPY' (normal), strikes in $1 increments")
    console.print("  Both platforms have Mon/Wed/Fri expiries available")
    console.print()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="IG.com Auto-Trader - Phase 1: Dry-Run Mode (SHORT EXPIRIES STRATEGY)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python auto_trade_ig.py                           # Default: SPY and QQQ
  python auto_trade_ig.py --tickers SPY             # SPY only
  python auto_trade_ig.py --tickers SPY QQQ IWM DIA # All tickers
  python auto_trade_ig.py --force-run               # Force run any day
        """
    )
    parser.add_argument(
        '--force-run',
        action='store_true',
        help='Force run even on weekends'
    )
    parser.add_argument(
        '--tickers',
        nargs='+',
        default=None,
        choices=['SPY', 'QQQ', 'IWM', 'DIA'],
        help='Tickers to trade (default: SPY QQQ)'
    )

    args = parser.parse_args()

    try:
        main(force_run=args.force_run, tickers=args.tickers)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        import traceback
        traceback.print_exc()
