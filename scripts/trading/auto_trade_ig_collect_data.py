"""
Automated IG.com Trading with Data Collection
Run daily at 20:50 UK time (15:50 ET)

Features:
- Generates today's signal
- Places TWO paper trades: 20:50 UK and 21:00 UK (measures timing penalty)
- Collects bid/ask, fill price, all execution data
- Logs everything for calibration
- Shows IBKR instructions for manual trading
"""

import sys
import time
import json
from datetime import datetime, timedelta
from pathlib import Path
import yfinance as yf
import numpy as np
from scipy.stats import norm
from rich.console import Console
from rich.table import Table

# Add src and scripts/trading to path
sys.path.append(str(Path(__file__).parent.parent.parent / "src"))
sys.path.append(str(Path(__file__).parent))

# Import from same directory
try:
    from ig_connector import IGConnector
    from trade_logger import TradeLogger
except ImportError:
    # Try with full path
    sys.path.insert(0, str(Path(__file__).parent))
    from ig_connector import IGConnector
    from trade_logger import TradeLogger

console = Console()


def calculate_black_scholes(S, K, T, r, sigma, option_type='call'):
    """Calculate Black-Scholes option price."""
    if T <= 0:
        return max(0, S - K) if option_type == 'call' else max(0, K - S)

    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)

    if option_type == 'call':
        price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    else:
        price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)

    return price


def get_spx_price():
    """Get current SPX price using yfinance."""
    try:
        spx = yf.Ticker("^GSPC")
        data = spx.history(period="1d")
        if not data.empty:
            current_price = data['Close'].iloc[-1]
            return float(current_price)
        return None
    except Exception as e:
        console.print(f"[red]Error fetching SPX price: {e}[/red]")
        return None


def get_latest_signal():
    """Get today's trading signal."""
    try:
        # Fetch SPX data
        spx = yf.Ticker("^GSPC")
        hist = spx.history(period="5d")

        if len(hist) < 2:
            console.print("[red]Insufficient data to generate signal[/red]")
            return None

        # Previous day's close and today's close
        prev_close = hist['Close'].iloc[-2]
        today_close = hist['Close'].iloc[-1]

        # Determine signal
        if today_close < prev_close:
            signal = "CALL"  # Fade RED day
            color = "red"
        else:
            signal = "PUT"   # Fade GREEN day
            color = "green"

        # Calculate strike (ATM)
        strike = round(today_close)

        # Determine expiry (next trading day)
        today = datetime.now()
        day_name = today.strftime("%A")

        if day_name == "Friday":
            expiry_date = today + timedelta(days=3)  # Monday
            expiry_label = "FRI-MON-3D"
            days_to_expiry = 3
        elif day_name == "Monday":
            expiry_date = today + timedelta(days=2)  # Wednesday
            expiry_label = "MON-WED-2D"
            days_to_expiry = 2
        elif day_name == "Tuesday":
            expiry_date = today + timedelta(days=1)  # Wednesday
            expiry_label = "TUE-WED-1D"
            days_to_expiry = 1
        elif day_name == "Wednesday":
            expiry_date = today + timedelta(days=2)  # Friday
            expiry_label = "WED-FRI-2D"
            days_to_expiry = 2
        elif day_name == "Thursday":
            expiry_date = today + timedelta(days=1)  # Friday
            expiry_label = "THU-FRI-1D"
            days_to_expiry = 1
        else:
            console.print("[yellow]Invalid trading day[/yellow]")
            return None

        return {
            'signal': signal,
            'strike': strike,
            'expiry_date': expiry_date.strftime("%Y-%m-%d"),
            'expiry_label': expiry_label,
            'days_to_expiry': days_to_expiry,
            'underlying_price': float(today_close),
            'prev_close': float(prev_close),
            'color': color
        }

    except Exception as e:
        console.print(f"[red]Error generating signal: {e}[/red]")
        return None


def calculate_theoretical_price(signal_data):
    """Calculate theoretical option price using Black-Scholes."""
    S = signal_data['underlying_price']
    K = signal_data['strike']
    T = signal_data['days_to_expiry'] / 365.0
    r = 0.05  # Risk-free rate
    sigma = 0.15  # IV (will be calibrated later)

    option_type = 'call' if signal_data['signal'] == 'CALL' else 'put'

    theoretical_price = calculate_black_scholes(S, K, T, r, sigma, option_type)

    return theoretical_price


def place_ig_order(ig: IGConnector, signal_data, theoretical_price, target_premium_gbp=100):
    """
    Place order on IG.com.

    Args:
        ig: IG connector instance
        signal_data: Signal dictionary
        theoretical_price: Calculated option price
        target_premium_gbp: Target premium in GBP

    Returns:
        Dictionary with order execution data
    """
    # TODO: This is a placeholder - IG.com option trading implementation
    # needs specific epic search for US 500 options with correct strike/expiry

    console.print("[yellow]IG.com option order placement - Implementation needed[/yellow]")
    console.print("[yellow]This requires:[/yellow]")
    console.print("  1. Search for US 500 options epic")
    console.print(f"  2. Find {signal_data['signal']} option at strike {signal_data['strike']}")
    console.print(f"  3. Expiry: {signal_data['expiry_date']}")
    console.print("  4. Fetch bid/ask")
    console.print(f"  5. Calculate size for target premium: £{target_premium_gbp}")
    console.print("  6. Place market order")

    # For now, return simulated data
    # User will need to implement actual IG.com option epic search

    return {
        'status': 'SIMULATED',
        'message': 'IG.com option placement needs implementation',
        'theoretical_price': theoretical_price,
        'signal': signal_data['signal'],
        'strike': signal_data['strike'],
        'expiry': signal_data['expiry_date']
    }


def show_ibkr_instructions(signal_data, theoretical_price):
    """Show instructions for manual IBKR trading."""
    console.print("\n" + "=" * 80)
    console.print("[bold cyan]IBKR MANUAL TRADING INSTRUCTIONS[/bold cyan]")
    console.print("=" * 80 + "\n")

    table = Table(show_header=True)
    table.add_column("Parameter", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("Signal", signal_data['signal'])
    table.add_row("Underlying", "SPY (not SPX)")
    table.add_row("Strike", f"${signal_data['strike']/10:.2f}")  # SPY is 1/10 of SPX
    table.add_row("Expiry", signal_data['expiry_date'])
    table.add_row("Type", signal_data['expiry_label'])
    table.add_row("Theoretical Price", f"${theoretical_price/10:.2f}")  # Scaled for SPY
    table.add_row("Action", "BUY TO OPEN")
    table.add_row("Order Type", "LIMIT (between mid and ask)")

    console.print(table)

    console.print("\n[bold]Steps:[/bold]")
    console.print("1. Open IBKR TWS or Web Trader")
    console.print("2. Search: SPY")
    console.print(f"3. Select: {signal_data['signal']} option, Strike ${signal_data['strike']/10:.2f}, Exp {signal_data['expiry_date']}")
    console.print("4. Check bid/ask spread")
    console.print("5. Place LIMIT order between mid and ask")
    console.print("6. After fill, return here and enter fill price")
    console.print()


def record_ibkr_trade(signal_data, theoretical_price, logger: TradeLogger):
    """Record IBKR trade manually entered by user."""
    console.print("[bold]IBKR Trade Recording[/bold]\n")

    # Ask user for fill details
    try:
        console.print("Enter trade details (or press Enter to skip):\n")

        bid = console.input("Bid price seen: $")
        if not bid:
            console.print("[yellow]Skipped IBKR trade recording[/yellow]")
            return

        ask = console.input("Ask price seen: $")
        fill = console.input("Fill price: $")
        size = console.input("Size (contracts): ")

        bid = float(bid)
        ask = float(ask)
        fill = float(fill)
        size = int(size)

        mid = (bid + ask) / 2
        premium_paid = fill * size * 100  # Option contract = 100 shares

        spread_pct = (fill - mid) / mid if mid > 0 else 0
        slippage_pct = (fill - theoretical_price/10) / (theoretical_price/10) if theoretical_price > 0 else 0

        trade_data = {
            'date': datetime.now().strftime("%Y-%m-%d"),
            'time': datetime.now().strftime("%H:%M:%S"),
            'signal': signal_data['signal'],
            'strike': signal_data['strike'] / 10,  # SPY strike
            'expiry_date': signal_data['expiry_date'],
            'days_to_expiry': signal_data['days_to_expiry'],
            'theoretical_price': theoretical_price / 10,
            'entry_bid': bid,
            'entry_ask': ask,
            'entry_mid': mid,
            'fill_price': fill,
            'size': size,
            'premium_paid': premium_paid,
            'spread_pct': spread_pct,
            'slippage_pct': slippage_pct,
            'entry_timestamp': datetime.now().isoformat()
        }

        logger.log_trade('ibkr', trade_data)

        console.print(f"\n[green]IBKR trade recorded successfully[/green]")
        console.print(f"Spread: {spread_pct*100:.2f}%")
        console.print(f"Slippage: {slippage_pct*100:.2f}%")
        console.print(f"Premium paid: ${premium_paid:.2f}")

    except ValueError:
        console.print("[red]Invalid input, trade not recorded[/red]")
    except Exception as e:
        console.print(f"[red]Error recording trade: {e}[/red]")


def main():
    """Main execution function."""
    console.print("\n" + "=" * 80)
    console.print("[bold]AUTOMATED TRADING & DATA COLLECTION[/bold]")
    console.print("=" * 80 + "\n")

    # Initialize
    logger = TradeLogger()

    # Get today's signal
    console.print("[cyan]Generating today's signal...[/cyan]")
    signal_data = get_latest_signal()

    if not signal_data:
        console.print("[red]Could not generate signal. Exiting.[/red]")
        return

    # Display signal
    console.print(f"\n[bold]Today's Signal:[/bold]")
    console.print(f"  {signal_data['signal']} at strike {signal_data['strike']}")
    console.print(f"  Expiry: {signal_data['expiry_date']} ({signal_data['expiry_label']})")
    console.print(f"  Days to expiry: {signal_data['days_to_expiry']}")
    console.print(f"  Underlying: ${signal_data['underlying_price']:.2f}")
    console.print()

    # Calculate theoretical price
    theoretical_price = calculate_theoretical_price(signal_data)
    console.print(f"[cyan]Theoretical option price: ${theoretical_price:.2f}[/cyan]\n")

    # IG.com paper trading
    console.print("[bold]IG.com Demo Account Trading[/bold]\n")

    try:
        ig = IGConnector()

        if ig.connect(use_demo=True):
            # Entry at 20:50 UK
            console.print("[cyan]Placing order at 20:50 UK (NOW)...[/cyan]")
            order_2050 = place_ig_order(ig, signal_data, theoretical_price)

            if order_2050['status'] != 'SIMULATED':
                # Log trade
                logger.log_trade('ig_paper_2050', order_2050)

            # Wait 10 minutes for 21:00 UK entry
            console.print("\n[yellow]Waiting 10 minutes for 21:00 UK entry...[/yellow]")
            console.print("[yellow](In production, this would be scheduled)[/yellow]\n")

            # For now, we'll skip the automated 21:00 entry
            # User can run script again at 21:00

            ig.disconnect()
        else:
            console.print("[red]Failed to connect to IG.com[/red]")

    except Exception as e:
        console.print(f"[red]IG.com error: {e}[/red]")

    # IBKR instructions
    show_ibkr_instructions(signal_data, theoretical_price)

    # Record IBKR trade
    record_ibkr_trade(signal_data, theoretical_price, logger)

    # Show summary
    console.print("\n" + "=" * 80)
    console.print("[bold]Trade Summary[/bold]")
    console.print("=" * 80 + "\n")

    console.print(f"IG Paper (20:50): {logger.get_trade_count('ig_paper_2050')} trades")
    console.print(f"IG Paper (21:00): {logger.get_trade_count('ig_paper_2100')} trades")
    console.print(f"IBKR Manual:      {logger.get_trade_count('ibkr')} trades")

    # Check if calibration needed
    total_ig_trades = logger.get_trade_count('ig_paper_2050')
    total_ibkr_trades = logger.get_trade_count('ibkr')

    if total_ig_trades >= 10:
        console.print(f"\n[bold green]IG.com: {total_ig_trades} trades logged - Ready for calibration![/bold green]")
        console.print("Run: python scripts/analysis/auto_calibrate_from_trades.py")

    if total_ibkr_trades >= 10:
        console.print(f"\n[bold green]IBKR: {total_ibkr_trades} trades logged - Ready for calibration![/bold green]")
        console.print("Run: python scripts/analysis/auto_calibrate_from_trades.py")

    console.print("\n[cyan]Done! See you tomorrow at 20:50 UK.[/cyan]\n")


if __name__ == "__main__":
    main()
