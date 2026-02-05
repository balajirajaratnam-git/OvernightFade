"""
Professional Overnight Fade Trading Dashboard - UNFILTERED Strategy
Multi-ticker analysis with data-driven strike selection

Usage:
    python dashboard_pro.py                      # Full detailed output (all tickers)
    python dashboard_pro.py -o compact           # Compact table (all 4 tickers, IG.com default)
    python dashboard_pro.py -o compact -p ibkr   # Compact table (all 4 tickers, IBKR)
"""
import os
import sys
import json
import argparse
import pandas as pd
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

sys.path.insert(0, 'src')
from backtester import Backtester

console = Console()

def load_config():
    """Load configuration."""
    config_path = os.path.join("config", "config.json")
    with open(config_path, "r") as f:
        return json.load(f)

def save_config(config):
    """Save configuration."""
    config_path = os.path.join("config", "config.json")
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

def get_spx_from_spy(spy_close, spy_atr):
    """
    Convert SPY to SPX using 10x multiplier.

    This ensures consistency with backtest calculations which are based on SPY data.
    Using live SPX data would create ATR calculation mismatches.

    Returns:
        tuple: (spx_close, spx_atr, source)
    """
    # Always use 10x multiplier for consistency with backtest
    # Backtest is based on SPY data, so SPX targets must scale proportionally
    return spy_close * 10, spy_atr * 10, "10x SPY (backtest-aligned)"

class MultiTickerDashboard:
    """
    Professional dashboard for unfiltered overnight fade strategy.
    Shows recommendations for all 4 tickers.
    """

    def __init__(self):
        self.config = load_config()
        self.tickers = ["SPY", "QQQ", "IWM", "DIA"]
        self.FLAT_THRESHOLD_PCT = 0.10

    def get_ticker_context(self, ticker):
        """
        Get market context for a specific ticker.

        Returns:
            dict: Market data or None if unavailable
        """
        # Temporarily set ticker in config
        original_ticker = self.config["ticker"]
        self.config["ticker"] = ticker
        save_config(self.config)

        try:
            bt = Backtester()
            if bt.daily_df.empty:
                return None

            # Get last available day
            last_date = bt.daily_df.index[-1]
            day_data = bt.daily_df.iloc[-1]

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
            console.print(f"[red]Error loading {ticker}: {e}[/red]")
            return None
        finally:
            # Restore original ticker
            self.config["ticker"] = original_ticker
            save_config(self.config)

    def generate_signal_unfiltered(self, context):
        """
        Generate signal WITHOUT any filters (pure unfiltered strategy).

        Only applies:
        1. Flat day filter (< 0.10% magnitude)
        2. Friday exclusion (built into backtest)

        NO LastHourVeto or other filters.

        Returns:
            tuple: (signal, reason)
        """
        # Filter 1: Flat day
        if abs(context["Magnitude"]) < self.FLAT_THRESHOLD_PCT:
            return "NO_TRADE", f"Flat day (magnitude < {self.FLAT_THRESHOLD_PCT}%)"

        # Filter 2: Check if enabled in config
        if context["Direction"] == "GREEN":
            if not self.config["filters"]["enable_fade_green"]:
                return "NO_TRADE", "Fade Green disabled in config"
            return "BUY PUT", f"GREEN day +{context['Magnitude']:.2f}% (Fade Down)"

        elif context["Direction"] == "RED":
            if not self.config["filters"]["enable_fade_red"]:
                return "NO_TRADE", "Fade Red disabled in config"
            return "BUY CALL", f"RED day {context['Magnitude']:.2f}% (Fade Up)"

        return "NO_TRADE", "Unknown direction"

    def calculate_strike_spy(self, close_price, option_type):
        """
        Calculate ATM strike price for SPY/QQQ/IWM/DIA (1-point increments).

        Args:
            close_price: Current close price
            option_type: "PUT" or "CALL"

        Returns:
            int: Strike price
        """
        strike = int(round(close_price))
        return strike

    def calculate_strike_spx(self, close_price, option_type):
        """
        Calculate ATM strike price for SPX/US 500 (5-point increments).

        Args:
            close_price: Current close price
            option_type: "PUT" or "CALL"

        Returns:
            int: Strike price (rounded to nearest 5)
        """
        strike = int(round(close_price / 5) * 5)
        return strike

    def run_compact(self, platform="ig"):
        """
        Compact output mode - platform-specific ticker lists.

        IG.com: US 500 (SPX) + IWM only (options-tradable)
        IBKR: All 4 tickers (SPY, QQQ, IWM, DIA)

        Args:
            platform: "ig" for IG.com (default) or "ibkr" for IBKR
        """
        console.clear()
        platform_name = "IG.com" if platform == "ig" else "IBKR"
        console.print(f"[bold blue]Overnight Fade | Trading Signals ({platform_name})[/bold blue]")
        console.print()

        # Platform-specific ticker lists
        if platform == "ig":
            # IG.com: Only US 500 (SPX from SPY) and IWM have options
            display_tickers = ["SPY", "IWM"]  # Will convert SPY to SPX for display
        else:
            # IBKR: All 4 tickers
            display_tickers = self.tickers

        # Get date from SPY
        spy_ctx = self.get_ticker_context("SPY")
        if not spy_ctx:
            console.print("[red]Error: No SPY data available[/red]")
            return

        data_date = spy_ctx['Date'].strftime('%Y-%m-%d')
        console.print(f"[bold cyan]Date: {data_date}[/bold cyan]")
        console.print()

        # Calculate ATR multiplier (always 0.1x)
        atr_mult = 0.1

        # Collect data for platform-specific tickers
        rows = []
        for ticker in display_tickers:
            ctx = self.get_ticker_context(ticker)
            if not ctx:
                continue

            signal, reason = self.generate_signal_unfiltered(ctx)

            # For IG.com, convert SPY to SPX
            if platform == "ig" and ticker == "SPY":
                # Get SPX conversion
                spx_close, spx_atr, spx_source = get_spx_from_spy(ctx['Close'], ctx['ATR'])

                display_ticker = "US 500"
                current_price = spx_close
                current_atr = spx_atr

                # Use SPX strike calculation (5-point increments)
                option_type = "PUT" if "PUT" in signal else "CALL"
                strike = self.calculate_strike_spx(spx_close, option_type)
            else:
                # Regular ticker (IWM for IG.com, or all for IBKR)
                display_ticker = ticker
                current_price = ctx['Close']
                current_atr = ctx['ATR']

                option_type = "PUT" if "PUT" in signal else "CALL"
                strike = self.calculate_strike_spy(current_price, option_type)

            if signal == "NO_TRADE":
                rows.append({
                    'Ticker': display_ticker,
                    'Signal': 'NO TRADE',
                    'Strike': '-',
                    'Current': f"${current_price:.2f}",
                    'Limit_Price': '-',
                    'Limit_Pts': '-',
                    'Reason': reason
                })
                continue

            # Calculate target
            target_move = current_atr * atr_mult

            if option_type == "PUT":
                limit_price = current_price - target_move
                limit_pts = -target_move
            else:
                limit_price = current_price + target_move
                limit_pts = target_move

            rows.append({
                'Ticker': display_ticker,
                'Signal': signal,
                'Strike': f"{strike}",  # No $ for cleaner display
                'Current': f"{current_price:.2f}",
                'Limit_Price': f"{limit_price:.2f}",
                'Limit_Pts': f"{limit_pts:+.2f}",
                'Reason': reason
            })

        # Display table based on platform
        if platform == "ibkr":
            # IBKR: Show Ticker, Signal, Strike, Current, Limit Price, Limit Pts
            table = Table(show_header=True, header_style="bold cyan", box=box.ROUNDED)
            table.add_column("Ticker", style="cyan", width=8)
            table.add_column("Signal", style="white", width=22)
            table.add_column("Strike", justify="right", width=8)
            table.add_column("Current", justify="right", width=10)
            table.add_column("Limit Price", justify="right", width=12)
            table.add_column("Limit Pts", justify="right", width=12)

            for row in rows:
                signal_style = "yellow" if row['Signal'] == 'NO TRADE' else "green"
                table.add_row(
                    row['Ticker'],
                    f"[{signal_style}]{row['Signal']}[/{signal_style}]",
                    row['Strike'],
                    f"${row['Current']}",
                    f"${row['Limit_Price']}",
                    f"[bold]{row['Limit_Pts']} pts[/bold]"
                )

        else:  # IG.com
            # IG.com: Show Ticker, Signal, Strike, Current, Limit Pts (NO Limit Price)
            table = Table(show_header=True, header_style="bold magenta", box=box.ROUNDED)
            table.add_column("Ticker", style="magenta", width=10)
            table.add_column("Signal", style="white", width=22)
            table.add_column("Strike", justify="right", width=8)
            table.add_column("Current", justify="right", width=10)
            table.add_column("Limit Pts", justify="right", width=15)

            for row in rows:
                signal_style = "yellow" if row['Signal'] == 'NO TRADE' else "green"
                table.add_row(
                    row['Ticker'],
                    f"[{signal_style}]{row['Signal']}[/{signal_style}]",
                    row['Strike'],
                    f"{row['Current']}",
                    f"[bold]{row['Limit_Pts']} pts[/bold]"
                )

        console.print(table)
        console.print()

        if platform == "ig":
            console.print(f"[dim]IG.com options: US 500 (SPX) + IWM only[/dim]")
            console.print(f"[dim]US 500 = SPY * 10 (maintains backtest price/ATR ratio)[/dim]")
        else:
            console.print(f"[dim]IBKR: All 4 tickers available[/dim]")

        console.print(f"[dim]Target: 0.1x ATR | Strategy: Unfiltered[/dim]")

    def run_detailed(self):
        """Full detailed output with all tickers."""
        console.clear()
        console.print(Panel.fit(
            "Overnight Fade | Professional Dashboard v4.0 | UNFILTERED Strategy",
            style="bold blue"
        ))
        console.print()

        # Collect data for all tickers
        ticker_data = []
        for ticker in self.tickers:
            ctx = self.get_ticker_context(ticker)
            if ctx:
                signal, reason = self.generate_signal_unfiltered(ctx)
                ctx['Signal'] = signal
                ctx['Reason'] = reason
                ticker_data.append(ctx)

        if not ticker_data:
            console.print("[red]Error: No data available for any ticker.[/red]")
            return

        # Check data date
        data_date = ticker_data[0]['Date'].strftime('%Y-%m-%d')
        console.print(f"[bold cyan]Analysis Date: {data_date}[/bold cyan]")
        console.print()

        # ========================================
        # DISPLAY: Market Overview
        # ========================================

        table_overview = Table(title="Market Overview - All Tickers", show_header=True, header_style="bold cyan")
        table_overview.add_column("Ticker", style="cyan", width=8)
        table_overview.add_column("Close", justify="right", width=10)
        table_overview.add_column("Direction", width=10)
        table_overview.add_column("Magnitude", justify="right", width=10)
        table_overview.add_column("ATR", justify="right", width=8)

        for ctx in ticker_data:
            dir_color = "green" if ctx['Direction'] == "GREEN" else "red"
            table_overview.add_row(
                ctx['Ticker'],
                f"${ctx['Close']:.2f}",
                f"[{dir_color}]{ctx['Direction']}[/{dir_color}]",
                f"{ctx['Magnitude']:+.2f}%",
                f"${ctx['ATR']:.2f}"
            )

        console.print(table_overview)
        console.print()

        # ========================================
        # DISPLAY: Trade Recommendations
        # ========================================

        trade_count = sum(1 for ctx in ticker_data if ctx['Signal'] != "NO_TRADE")

        if trade_count == 0:
            console.print(Panel(
                Text("NO TRADES TODAY\n\nAll tickers filtered (flat days or disabled)",
                     style="bold yellow", justify="center"),
                title="RECOMMENDATION",
                border_style="yellow"
            ))
            return

        # Show summary
        summary_text = f"[bold green]{trade_count} TRADE SIGNAL{'S' if trade_count > 1 else ''}[/bold green]"
        console.print(Panel.fit(summary_text, style="bold green"))
        console.print()

        # ========================================
        # DISPLAY: Individual Ticker Execution Plans
        # ========================================

        atr_mult = self.config["default_take_profit_atr"]

        for ctx in ticker_data:
            if ctx['Signal'] == "NO_TRADE":
                continue

            ticker = ctx['Ticker']
            signal = ctx['Signal']
            reason = ctx['Reason']
            close = ctx['Close']
            atr = ctx['ATR']

            option_type = "PUT" if "PUT" in signal else "CALL"
            target_move = atr * atr_mult

            # Calculate strike (ATM)
            strike = self.calculate_strike_spy(close, option_type)

            # Calculate limit price and points
            if option_type == "PUT":
                limit_price = close - target_move
                limit_pts = -target_move
                direction = "DOWN"
            else:
                limit_price = close + target_move
                limit_pts = target_move
                direction = "UP"

            # Create execution table
            table = Table(
                title=f"[bold cyan]{ticker}[/bold cyan] - {signal}",
                show_lines=True,
                border_style="cyan"
            )
            table.add_column("Parameter", style="white", width=20)
            table.add_column("Value", style="bold yellow", width=40)

            table.add_row("Signal", f"[bold green]{signal}[/bold green]")
            table.add_row("Reason", reason)
            table.add_row("", "")  # Spacer
            table.add_row("Current Price", f"${close:.2f}")
            table.add_row("ATR (14)", f"${atr:.2f}")
            table.add_row("", "")  # Spacer
            table.add_row("[bold]EXECUTION[/bold]", "")
            table.add_row("Option Type", f"{option_type}")
            table.add_row("Strike Price", f"[bold magenta]${strike}[/bold magenta]")
            table.add_row("Expiry", "0-DTE or 1-DTE")
            table.add_row("", "")  # Spacer
            table.add_row("Limit Price", f"{direction} [bold green]${limit_price:.2f}[/bold green]")
            table.add_row("Limit Pts", f"[bold green]{limit_pts:+.2f} pts[/bold green] ({atr_mult}x ATR)")
            table.add_row("Risk", "Premium Paid (100% loss if target not hit)")

            console.print(table)
            console.print()

        # ========================================
        # DISPLAY: Historical Backtest Stats
        # ========================================

        console.print("="*79)
        console.print("[bold white]HISTORICAL BACKTEST PERFORMANCE (Unfiltered Strategy)[/bold white]")
        console.print("="*79)
        console.print()

        # Load backtest results for summary stats
        try:
            df = pd.read_csv('results/phase3_option_c_detailed_results.csv')

            total_trades = len(df)
            wins = (df['Result'] == 'WIN').sum()
            win_rate = wins / total_trades * 100

            final_equity = df['Equity'].iloc[-1]
            starting = 10000
            total_roi = (final_equity - starting) / starting * 100

            # Per-ticker stats
            ticker_stats = []
            for ticker in self.tickers:
                ticker_df = df[df['Ticker'] == ticker]
                if len(ticker_df) > 0:
                    ticker_wins = (ticker_df['Result'] == 'WIN').sum()
                    ticker_wr = ticker_wins / len(ticker_df) * 100
                    ticker_stats.append({
                        'Ticker': ticker,
                        'Trades': len(ticker_df),
                        'WinRate': ticker_wr
                    })

            # Display overall stats
            table_stats = Table(show_header=False, box=None, padding=(0, 2))
            table_stats.add_column("Metric", style="cyan")
            table_stats.add_column("Value", style="bold white")

            table_stats.add_row("Strategy", "Unfiltered (Max Performance)")
            table_stats.add_row("Total Trades", f"{total_trades:,}")
            table_stats.add_row("Win Rate", f"[bold green]{win_rate:.1f}%[/bold green]")
            table_stats.add_row("ROI (10 years)", f"[bold green]{total_roi:,.0f}%[/bold green]")
            table_stats.add_row("Final Equity", f"[bold green]${final_equity:,.0f}[/bold green]")

            console.print(Panel(table_stats, title="Overall Performance", border_style="green"))
            console.print()

            # Display per-ticker stats
            table_tickers = Table(title="Per-Ticker Breakdown", show_header=True, header_style="bold cyan")
            table_tickers.add_column("Ticker", style="cyan", width=10)
            table_tickers.add_column("Trades", justify="right", width=10)
            table_tickers.add_column("Win Rate", justify="right", width=12)

            for stat in ticker_stats:
                table_tickers.add_row(
                    stat['Ticker'],
                    f"{stat['Trades']:,}",
                    f"{stat['WinRate']:.1f}%"
                )

            console.print(table_tickers)

        except Exception as e:
            console.print(f"[yellow]Could not load historical stats: {e}[/yellow]")

        console.print()
        console.print("[bold yellow]IMPORTANT:[/bold yellow] This uses the UNFILTERED strategy (no LastHourVeto).")
        console.print("[bold yellow]Based on backtest: 6,960 trades, 85.7% WR, $1.64M final equity.[/bold yellow]")
        console.print()

    def run(self, output_mode="detailed", platform="ig"):
        """Main entry point."""
        if output_mode == "compact":
            self.run_compact(platform=platform)
        else:
            self.run_detailed()


if __name__ == "__main__":
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Professional Overnight Fade Trading Dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python dashboard_pro.py                      # Full detailed output (all tickers)
  python dashboard_pro.py -o compact           # Compact output (IG.com, SPX)
  python dashboard_pro.py -o compact -p ibkr   # Compact output (IBKR, SPY)
        """
    )
    parser.add_argument(
        '-o', '--output',
        choices=['detailed', 'compact'],
        default='detailed',
        help='Output mode: detailed (default) or compact'
    )
    parser.add_argument(
        '-p', '--platform',
        choices=['ig', 'ibkr'],
        default='ig',
        help='Trading platform: ig (default) or ibkr (only used with -o compact)'
    )

    args = parser.parse_args()

    dash = MultiTickerDashboard()
    dash.run(output_mode=args.output, platform=args.platform)
