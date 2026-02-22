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

# Add scripts/analysis to path for Black-Scholes
sys.path.insert(0, 'scripts/analysis')
from measure_reality_framework import black_scholes_call, black_scholes_put

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


def calculate_expected_pnl(ticker, days_to_expiry, adjustments, bs_win_pct=None):
    """
    Calculate expected P&L outcomes using BS-derived win % and reality costs.

    Args:
        ticker: Ticker symbol (SPY, QQQ, IWM, DIA)
        days_to_expiry: 1, 2, or 3 days
        adjustments: Reality adjustments dict
        bs_win_pct: BS-computed win percentage (exit_premium/entry_premium - 1) * 100
                    If None, falls back to old fudge-factor method.

    Returns:
        dict: Expected P&L metrics
    """
    # Get cost factors
    spread_cost = adjustments["spread_costs"].get(ticker, 0.05)
    slippage = adjustments["slippage_pct"].get(ticker, 0.01)
    commission = adjustments["commission_per_contract"]

    # Commission as % of assumed $1000 position (entry + exit)
    commission_pct = (commission * 2 / 1000) * 100

    if bs_win_pct is not None:
        # NEW: Use actual BS-computed win %
        gross_win_pct = bs_win_pct
    else:
        # FALLBACK: Old fudge-factor method
        expiry_key = f"{days_to_expiry}_day"
        pnl_adj = adjustments["pnl_adjustments"][expiry_key].get(ticker, 0.50)
        gross_win_pct = 45.0 * pnl_adj

    # Net WIN P&L = gross BS gain - spread - slippage - commission
    realistic_win_pnl_pct = gross_win_pct - (spread_cost * 100) - (slippage * 100) - commission_pct

    # LOSS P&L: option expires worthless = -100% - costs
    realistic_loss_pnl_pct = -100.0 - (spread_cost * 100) - commission_pct

    return {
        "backtest_win_pct": gross_win_pct,
        "realistic_win_pct": realistic_win_pnl_pct,
        "backtest_loss_pct": -100.0,
        "realistic_loss_pct": realistic_loss_pnl_pct,
        "adjustment_factor": gross_win_pct / 45.0 if gross_win_pct else 0,
        "spread_cost_pct": spread_cost * 100,
        "slippage_pct": slippage * 100,
        "commission_pct": commission_pct
    }


def fetch_vix_iv(max_retries=3):
    """
    Fetch current VIX level from yfinance and convert to implied volatility.
    VIX / 100 = annualized IV (e.g., VIX=20 -> sigma=0.20).
    Falls back to 0.15 if all retries fail.

    Returns:
        float: implied volatility (e.g., 0.20)
    """
    import yfinance as yf

    for attempt in range(1, max_retries + 1):
        try:
            console.print(f"[dim]Fetching VIX from yfinance (attempt {attempt}/{max_retries})...[/dim]")
            vix = yf.Ticker('^VIX')
            vix_hist = vix.history(period='5d')

            if vix_hist.empty:
                raise ValueError("No VIX data returned from yfinance")

            vix_close = float(vix_hist['Close'].iloc[-1])
            sigma = vix_close / 100.0

            # Sanity check: VIX should be between 5 and 100
            if sigma < 0.05 or sigma > 1.0:
                console.print(f"[yellow]Warning: VIX={vix_close:.1f} seems unusual, using anyway[/yellow]")

            console.print(f"[green]VIX fetched: {vix_close:.2f} -> IV={sigma:.4f}[/green]")
            return sigma

        except Exception as e:
            console.print(f"[red]VIX attempt {attempt}/{max_retries} failed: {e}[/red]")
            if attempt < max_retries:
                import time as _time
                _time.sleep(2)

    console.print("[yellow]Warning: Could not fetch VIX. Falling back to sigma=0.15[/yellow]")
    return 0.15


def fetch_spx_data(max_retries=3):
    """
    Fetch live SPX (S&P 500 Index) data from yfinance.
    Retries up to max_retries times on failure, then aborts.

    Returns:
        dict: {'close': float, 'atr': float} with SPX values
    Raises:
        SystemExit: If all retries fail
    """
    import yfinance as yf

    for attempt in range(1, max_retries + 1):
        try:
            console.print(f"[dim]Fetching SPX data from yfinance (attempt {attempt}/{max_retries})...[/dim]")
            spx = yf.Ticker('^GSPC')
            spx_hist = spx.history(period='30d')

            if spx_hist.empty:
                raise ValueError("No SPX data returned from yfinance")

            spx_close = spx_hist['Close'].iloc[-1]

            # Calculate ATR_14 directly on SPX data
            high = spx_hist['High']
            low = spx_hist['Low']
            close_prev = spx_hist['Close'].shift(1)
            tr = pd.concat([
                high - low,
                (high - close_prev).abs(),
                (low - close_prev).abs()
            ], axis=1).max(axis=1)
            spx_atr = tr.rolling(14, min_periods=1).mean().iloc[-1]

            console.print(f"[green]SPX data fetched: Close={spx_close:.2f}, ATR_14={spx_atr:.2f}[/green]")

            return {
                'close': float(spx_close),
                'atr': float(spx_atr)
            }

        except Exception as e:
            console.print(f"[red]Attempt {attempt}/{max_retries} failed: {e}[/red]")
            if attempt < max_retries:
                import time as _time
                _time.sleep(2)

    console.print("[bold red]FATAL: Could not fetch SPX data after 3 attempts. Aborting.[/bold red]")
    sys.exit(1)


def calculate_strike_us500(close_price):
    """
    Calculate ATM strike for US 500 (5-point increments).

    Args:
        close_price: SPX close price (e.g., ~6000)

    Returns:
        int: Strike price rounded to nearest 5
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


def calculate_order_details(ticker, context, signal, expiry_date, days_to_expiry, adjustments, spx_data=None, atr_mult=0.1, live_iv=None):
    """
    Calculate complete order details with reality adjustments.

    For SPY signals: uses live SPX data directly from yfinance.
    NO conversions between SPY and SPX. SPX prices, ATR, strike, and
    BS premiums are all computed natively on SPX data.

    The signal (BUY PUT / BUY CALL) comes from SPY backtest data,
    but ALL pricing is done on SPX (US 500) directly.

    Args:
        ticker: Display ticker (US 500, QQQ, IWM, DIA)
        context: Market context (from SPY data - used for signal only)
        signal: Trading signal
        expiry_date: Expiry date
        days_to_expiry: Days to expiry (1, 2, or 3)
        adjustments: Reality adjustments dict
        spx_data: Dict with 'close' and 'atr' from fetch_spx_data() (for SPY ticker)
        atr_mult: ATR multiplier for target

    Returns:
        dict: Order details with expected P&L
    """
    close = context["Close"]
    atr = context["ATR"]

    # For SPY signals, use SPX data directly (no conversion)
    if context["Ticker"] == "SPY":
        display_ticker = "US 500"

        if spx_data is None:
            console.print("[bold red]ERROR: SPX data required for SPY signal. Aborting.[/bold red]")
            sys.exit(1)

        # Use SPX data directly - no conversion from SPY
        current_price = spx_data['close']
        current_atr = spx_data['atr']
        strike = calculate_strike_us500(current_price)

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

    # Calculate underlying target price (for reference)
    target_move = current_atr * atr_mult

    if option_type == "PUT":
        underlying_target = current_price - target_move
        direction = "DOWN"
    else:
        underlying_target = current_price + target_move
        direction = "UP"

    # Calculate option premiums using Black-Scholes
    # BS computed directly on the trading instrument's price (SPX for US 500)
    T = max(days_to_expiry, 1) / 365.0  # Time to expiry in years (min 1 day)
    r = 0.05  # Risk-free rate (5%)
    sigma = live_iv if live_iv is not None else 0.15  # VIX-derived IV (fallback 0.15)

    # Calculate entry premium - directly on current_price and strike (no conversion)
    if option_type == "CALL":
        entry_option = black_scholes_call(current_price, strike, T, r, sigma)
    else:
        entry_option = black_scholes_put(current_price, strike, T, r, sigma)

    # Handle both dict return (T>0) and scalar return (T<=0) from BS
    if isinstance(entry_option, dict):
        entry_premium = entry_option['price']
    else:
        entry_premium = float(entry_option)

    # Calculate EXIT premium via Black-Scholes at underlying target price
    # This is the CONSISTENT approach: same BS pricing used in backtests
    # (run_backtest_option_limit.py, run_backtest_bs_pricing.py)
    #
    # Assumption: if underlying hits target, we assume it happens ~halfway through
    # the holding period (conservative estimate for time remaining at exit).
    T_exit = T * 0.5  # Assume target hit halfway through holding period

    if option_type == "CALL":
        exit_option = black_scholes_call(underlying_target, strike, T_exit, r, sigma)
    else:
        exit_option = black_scholes_put(underlying_target, strike, T_exit, r, sigma)

    if isinstance(exit_option, dict):
        exit_premium = exit_option['price']
    else:
        exit_premium = float(exit_option)

    # Target premium = BS-computed exit premium at underlying target price
    target_premium = exit_premium

    # Limit price and points are in option premium terms (SPX scale for US 500)
    limit_price = target_premium
    limit_pts = target_premium - entry_premium

    # Also compute the realistic gain % for display
    if entry_premium > 0:
        realistic_win_pct = (exit_premium - entry_premium) / entry_premium * 100
    else:
        realistic_win_pct = 0.0

    # Apply spread/slippage costs to get net expectation
    dte_for_lookup = max(days_to_expiry, 1)
    expected_pnl = calculate_expected_pnl(context["Ticker"], dte_for_lookup, adjustments, bs_win_pct=realistic_win_pct)

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
        "Underlying_Target": underlying_target,
        "Entry_Premium": entry_premium,
        "Magnitude": context["Magnitude"],
        "Day_Direction": context["Direction"],
        "Expected_PnL": expected_pnl,
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
    All values shown are for IG.com (US 500 = SPX).

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

    table = Table(show_header=True, header_style="bold cyan", title="Order Summary (IG.com)")
    table.add_column("Ticker", style="cyan", width=10)
    table.add_column("Signal", width=10)
    table.add_column("Strike", justify="right", width=10)
    table.add_column("Expiry", width=12)
    table.add_column("DTE", justify="right", width=5)
    table.add_column("Entry Prem", justify="right", width=12)
    table.add_column("Limit Pts", justify="right", width=12)
    table.add_column("Realistic P&L", justify="right", width=15)

    for order in orders:
        signal_style = "green" if "BUY" in order["Signal"] else "yellow"
        pnl = order["Expected_PnL"]

        # Color code realistic P&L
        if pnl["realistic_win_pct"] > 30:
            pnl_style = "green"
        elif pnl["realistic_win_pct"] > 15:
            pnl_style = "yellow"
        else:
            pnl_style = "red"

        table.add_row(
            order['Display_Ticker'],
            f"[{signal_style}]{order['Option_Type']}[/{signal_style}]",
            str(order['Strike']),
            order["Expiry_Date"],
            str(order["Days_To_Expiry"]),
            f"{order['Entry_Premium']:.2f}",
            f"{order['Limit_Pts']:.2f}",
            f"[{pnl_style}]{pnl['realistic_win_pct']:+.1f}%[/{pnl_style}]"
        )

    console.print(table)
    console.print()

    # Detailed breakdown
    for i, order in enumerate(orders, 1):
        pnl = order["Expected_PnL"]

        console.print(f"[bold cyan]Order {i}: {order['Display_Ticker']} {order['Option_Type']}[/bold cyan]")
        console.print(f"  Date: {order['Date']}")
        console.print(f"  Day Direction: {order['Day_Direction']} ({order['Magnitude']:+.2f}%)")
        console.print(f"  Signal: {order['Signal']}")
        console.print()

        console.print(f"  [bold white]IG.com Order Details:[/bold white]")
        console.print(f"    Ticker: [bold]{order['Display_Ticker']}[/bold]")
        console.print(f"    Option Type: {order['Option_Type']}")
        console.print(f"    Strike: {order['Strike']} (ATM)")
        console.print(f"    Expiry: {order['Expiry_Date']} ({order['Days_To_Expiry']}-day)")
        console.print(f"    Underlying at Entry: {order['Current_Price']:.2f}")
        console.print(f"    Underlying Target: {order['Underlying_Target']:.2f} ({order['Direction']} {abs(order['Target_Move']):.2f} pts)")
        console.print()
        console.print(f"    [bold green]Option Premium Details:[/bold green]")
        console.print(f"    Entry Premium (BUY): {order['Entry_Premium']:.2f} pts")
        console.print(f"    Target Premium (SELL Limit): {order['Limit_Price']:.2f} pts")
        console.print(f"    [bold]Limit Pts: {order['Limit_Pts']:.2f} pts[/bold]")
        console.print(f"    Expected Profit: +{abs(order['Limit_Pts']):.2f} pts ({pnl['realistic_win_pct']:+.1f}%)")
        console.print()
        console.print(f"    [bold]Action: BUY option at ~{order['Entry_Premium']:.2f} pts, set SELL limit at {order['Limit_Price']:.2f} pts (Limit_Pts = {order['Limit_Pts']:.2f})[/bold]")
        console.print()

        # P&L EXPECTATIONS
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

    # Fetch SPX data from yfinance (for SPY -> US 500 pricing)
    # This is done ONCE upfront - no SPY/SPX conversions anywhere
    console.print()
    console.print("=" * 80)
    console.print("[bold white]STEP 3: FETCH SPX (US 500) DATA FROM YFINANCE[/bold white]")
    console.print("=" * 80)
    console.print()

    spx_data = None
    if "SPY" in tickers:
        spx_data = fetch_spx_data(max_retries=3)  # Aborts if all retries fail
        console.print(f"  SPX Close: {spx_data['close']:.2f}")
        console.print(f"  SPX ATR_14: {spx_data['atr']:.2f}")
        console.print()

    # Fetch VIX-derived implied volatility (replaces hardcoded sigma=0.15)
    live_iv = fetch_vix_iv(max_retries=3)
    console.print(f"  [bold]Using IV (from VIX): {live_iv:.4f} ({live_iv*100:.1f}%)[/bold]")
    console.print()

    console.print()
    console.print("=" * 80)
    console.print("[bold white]STEP 4: GENERATE SIGNALS[/bold white]")
    console.print("=" * 80)
    console.print()

    # Generate signals
    orders = []

    for ticker in tickers:
        console.print(f"[cyan]Analyzing {ticker}...[/cyan]")

        # Get context (SPY data used for signal generation only)
        ctx = get_ticker_context(ticker)

        if not ctx:
            console.print(f"[red]  X Failed to load context[/red]")
            continue

        # Generate signal (based on SPY direction/magnitude)
        signal, reason = generate_signal(ctx)

        console.print(f"  Date: {ctx['Date'].strftime('%Y-%m-%d')}")
        console.print(f"  SPY Close: ${ctx['Close']:.2f}")
        console.print(f"  Direction: {ctx['Direction']} ({ctx['Magnitude']:+.2f}%)")
        console.print(f"  Signal: [bold]{signal}[/bold]")
        console.print(f"  Reason: {reason}")

        if signal != "NO_TRADE":
            # Calculate order details using SPX data directly (no conversion)
            display_ticker = "US 500" if ticker == "SPY" else ticker
            order = calculate_order_details(
                display_ticker, ctx, signal, expiry_date, days_to_expiry,
                adjustments, spx_data=spx_data, live_iv=live_iv
            )
            orders.append(order)

            # Log to CSV
            log_order_to_csv(order)

            console.print(f"  [green]OK Order calculated and logged[/green]")

        console.print()

    console.print()
    console.print("=" * 80)
    console.print("[bold white]STEP 5: ORDER SUMMARY[/bold white]")
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
    console.print("  - All pricing computed directly on SPX (US 500) data from yfinance")
    console.print("  - NO SPY-to-SPX conversions anywhere")

    if not adjustments.get("calibration_status", {}).get("is_calibrated", False):
        console.print("  - [yellow]NOT YET CALIBRATED[/yellow]: Start paper trading to refine these estimates")
        console.print("  - See REALITY_CALIBRATION_GUIDE.md for full 3-month calibration process")

    console.print()
    console.print("[yellow]Phase 1: No actual orders placed (dry-run mode)[/yellow]")
    console.print()
    console.print("[bold white]Trading Days:[/bold white]")
    console.print("  Mon-Thu: Recommended (2-day and 1-day expiries, 78-90% WR)")
    console.print("  Friday: Included for paper trading (3-day expiry, 69% WR, lower performance)")
    console.print()
    console.print("[bold white]Data Sources:[/bold white]")
    console.print("  Signal: SPY daily data (Polygon.io) - direction/magnitude for fade signal")
    console.print("  Pricing: SPX (^GSPC) from yfinance - strike, premium, limit_pts for IG.com US 500")
    console.print("  NO conversions between SPY and SPX")
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
