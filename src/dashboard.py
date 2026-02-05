"""
Overnight Fade Trading Dashboard

Interactive command-line dashboard for analyzing market conditions and generating
trade recommendations for the overnight fade options strategy.

Features:
- Auto-fetches latest market data when stale
- Applies LastHourVeto filter to reduce false signals
- Displays execution plans for both IBKR (SPY) and IG.com (SPX/US 500)
- Shows historical backtest statistics for confidence assessment
- Rich terminal UI with color-coded signals and tables

Usage:
    python dashboard.py                    # Show recommendations with existing data
    ALLOW_NETWORK=1 python dashboard.py    # Enable auto-fetch of latest data
"""

import os
import json
import pandas as pd
from datetime import datetime, timedelta
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.status import Status
from backtester import Backtester
from strategies import LastHourVeto
from session_utils import TZ_ET, TZ_UTC, is_after_cash_close_et

# Rich Console for formatted terminal output
console = Console()

# Strategy Parameters
VETO_THRESHOLD = 0.2  # LastHourVeto filter threshold (validated in walk-forward testing)
DEFAULT_SPY_TO_SPX = 10.0  # SPY to SPX multiplier fallback


def is_network_allowed():
    """
    Check if network access is enabled via environment variable.

    Returns:
        bool: True if ALLOW_NETWORK=1, False otherwise
    """
    return os.getenv("ALLOW_NETWORK") == "1"


def fetch_spx_data_from_yfinance(target_date=None):
    """
    Fetch SPX (S&P 500 Index) close price and ATR from Yahoo Finance.

    Args:
        target_date: Date string (YYYY-MM-DD) to fetch SPX for.
                     If None, fetches most recent available.

    Returns:
        tuple: (spx_close, spx_atr) or (None, None) if unavailable
    """
    try:
        import yfinance as yf
        spx = yf.Ticker('^GSPC')

        # Fetch 30 days to ensure we have enough data for ATR calculation
        hist = spx.history(period='30d')
        if hist.empty or len(hist) < 15:
            return None, None

        # Calculate ATR using same method as backtester
        hist['H-L'] = hist['High'] - hist['Low']
        hist['H-PC'] = abs(hist['High'] - hist['Close'].shift(1))
        hist['L-PC'] = abs(hist['Low'] - hist['Close'].shift(1))
        hist['TR'] = hist[['H-L', 'H-PC', 'L-PC']].max(axis=1)
        hist['ATR_14'] = hist['TR'].rolling(window=14).mean()

        if target_date:
            # Try to find exact date match
            for idx in hist.index:
                if idx.strftime('%Y-%m-%d') == target_date:
                    return hist.loc[idx, 'Close'], hist.loc[idx, 'ATR_14']

            # Fall back to most recent date before target
            hist_filtered = hist[hist.index.strftime('%Y-%m-%d') <= target_date]
            if not hist_filtered.empty:
                row = hist_filtered.iloc[-1]
                return row['Close'], row['ATR_14']

        # Fall back to most recent available
        row = hist.iloc[-1]
        return row['Close'], row['ATR_14']
    except Exception:
        # Silent fail - caller will handle fallback
        pass
    return None, None


def get_spx_data(spy_close, spy_atr, config=None, target_date=None):
    """
    Get SPX (S&P 500 Index) close price and ATR with fallback cascade.

    Priority order:
    1. Manual override from config.json ("spx_value" and "spx_atr")
    2. Live fetch from Yahoo Finance (yfinance) for matching date
    3. Estimated from SPY using 10x multiplier

    Args:
        spy_close: SPY close price (for fallback estimation)
        spy_atr: SPY ATR (for fallback estimation)
        config: Config dict (optional, for manual override check)
        target_date: Date string (YYYY-MM-DD) to match SPX date with SPY date

    Returns:
        tuple: (spx_close, spx_atr, source_description)
    """
    # Priority 1: Check config.json for manual SPX values
    if config and config.get("spx_value") and config.get("spx_atr"):
        try:
            spx_close = float(config["spx_value"])
            spx_atr = float(config["spx_atr"])
            return spx_close, spx_atr, "config manual"
        except (ValueError, TypeError):
            pass

    # Priority 2: Try fetching actual SPX data from Yahoo Finance
    spx_close, spx_atr = fetch_spx_data_from_yfinance(target_date)
    if spx_close and spx_atr:
        return spx_close, spx_atr, "yfinance"

    # Priority 3: Estimate from SPY using default multiplier
    ratio = DEFAULT_SPY_TO_SPX
    return spy_close * ratio, spy_atr * ratio, f"estimated ({ratio:.0f}x SPY)"


class Dashboard:
    """
    Trading dashboard for overnight fade strategy.

    Provides market analysis, signal generation with filters, and execution plans
    for both IBKR (SPY options) and IG.com (SPX/US 500 options).
    """

    def __init__(self):
        self.config = self._load_config()
        self.ticker = self.config["ticker"]
        self.data_dir = os.path.join(self.config["directories"]["data"], self.ticker)
        self.intraday_dir = os.path.join(self.data_dir, "intraday")
        self.daily_path = os.path.join(self.data_dir, "daily_OHLCV.parquet")

        self.FLAT_THRESHOLD_PCT = 0.10  # Minimum daily move to generate signal

    def _load_config(self):
        """Load trading configuration from config.json"""
        config_path = os.path.join("config", "config.json")
        with open(config_path, "r") as f:
            return json.load(f)

    def _get_today_et(self):
        """
        Get today's date in ET timezone.

        Returns:
            date: Today's date in America/New_York timezone
        """
        now_utc = datetime.now(TZ_UTC)
        now_et = now_utc.astimezone(TZ_ET)
        return now_et.date()

    def _is_data_stale(self):
        """
        Check if local data is missing today's market data.

        Returns:
            tuple: (is_stale, today_str, reason)
                - is_stale: True if data needs updating
                - today_str: Today's date string (YYYY-MM-DD)
                - reason: Human-readable explanation
        """
        today = self._get_today_et()
        today_str = today.strftime("%Y-%m-%d")

        # Only check staleness if market has closed (after 4:05 PM ET)
        if not is_after_cash_close_et():
            return False, today_str, "Market still open or just closed"

        # Weekends don't have trading data
        if today.weekday() >= 5:  # Saturday=5, Sunday=6
            return False, today_str, "Weekend - no trading"

        # Check if today's intraday data exists
        intra_path = os.path.join(self.intraday_dir, f"{today_str}.parquet")
        if not os.path.exists(intra_path):
            return True, today_str, "Missing today's intraday data"

        # Check if daily data includes today
        if os.path.exists(self.daily_path):
            daily_df = pd.read_parquet(self.daily_path)
            if not daily_df.empty:
                last_date = daily_df.index[-1].strftime("%Y-%m-%d")
                if last_date < today_str:
                    return True, today_str, "Daily data doesn't include today"

        return False, today_str, "Data is current"

    def _auto_fetch_if_stale(self):
        """
        Auto-fetch today's data if stale and network access is enabled.

        Returns:
            bool: True if data was successfully fetched/updated, False otherwise
        """
        is_stale, today_str, reason = self._is_data_stale()

        if not is_stale:
            console.print(f"[dim]Data check: {reason}[/dim]")
            return False

        console.print(f"[yellow]Data is stale: {reason}[/yellow]")

        if not is_network_allowed():
            console.print("[yellow]Network disabled. Set ALLOW_NETWORK=1 to auto-fetch.[/yellow]")
            console.print("[dim]Showing last available data instead.[/dim]")
            return False

        # Import data_manager for fetching
        try:
            from data_manager import DataManager, fetch_yfinance_intraday
        except ImportError as e:
            console.print(f"[red]Cannot import data_manager: {e}[/red]")
            return False

        console.print(f"[cyan]Auto-fetching data for {today_str}...[/cyan]")

        try:
            # Initialize DataManager with network enabled
            dm = DataManager(require_network=True)

            # Fetch today's intraday data
            intra_path = os.path.join(self.intraday_dir, f"{today_str}.parquet")
            df = pd.DataFrame()

            if not os.path.exists(intra_path):
                # Try Polygon API first
                with console.status("[bold green]Trying Polygon API...", spinner="dots"):
                    try:
                        df = dm.fetch_poly_aggs(self.ticker, today_str, today_str, 1, "minute")
                    except Exception as e:
                        console.print(f"[yellow]Polygon failed: {e}[/yellow]")
                        df = pd.DataFrame()

                # Fall back to yfinance if Polygon failed or returned empty
                if df.empty:
                    with console.status("[bold green]Trying yfinance fallback...", spinner="dots"):
                        df = fetch_yfinance_intraday(self.ticker, today_str)

                # Save if we successfully got data from either source
                if not df.empty:
                    temp_path = intra_path + ".tmp"
                    df.to_parquet(temp_path)
                    os.replace(temp_path, intra_path)
                    console.print(f"[green]Fetched intraday data for {today_str}[/green]")
                else:
                    console.print(f"[yellow]No intraday data available from any source for {today_str}[/yellow]")
                    return False

            # Derive daily bar from intraday data
            with console.status("[bold green]Deriving daily bar...", spinner="dots"):
                dm.derive_daily_from_intraday(today_str)

            console.print("[green]Data updated successfully.[/green]\n")
            return True

        except Exception as e:
            console.print(f"[red]Auto-fetch failed: {e}[/red]")
            console.print("[dim]Showing last available data instead.[/dim]")
            return False

    def _load_data(self):
        """Load backtester and strategy components after potential data update."""
        self.bt = Backtester()
        self.strategy = LastHourVeto(self.config, self.intraday_dir, veto_threshold=VETO_THRESHOLD)

    def get_market_context(self):
        """
        Get market context from the most recent completed day in the database.

        Returns:
            dict: Market context with Date, Close, Direction, Magnitude, ATR
                  or None if no data available
        """
        if self.bt.daily_df.empty:
            return None

        # Get last available day from database
        last_date = self.bt.daily_df.index[-1]
        day_data = self.bt.daily_df.iloc[-1]

        return {
            "Date": last_date,
            "Close": day_data["Close"],
            "Direction": day_data["Direction"],
            "Magnitude": day_data["Magnitude"],
            "ATR": day_data["ATR_14"]
        }

    def analyze_last_hour(self, date_str, day_data):
        """
        Analyze the last trading hour (15:00-16:00 ET) trend.

        Used by LastHourVeto filter to detect trend continuation that might
        indicate further momentum rather than a reversal opportunity.

        Args:
            date_str: Date string (YYYY-MM-DD)
            day_data: Daily bar data Series

        Returns:
            dict: Last hour analysis with move, ratio, direction, veto status
                  or None if insufficient data
        """
        intra_path = os.path.join(self.intraday_dir, f"{date_str}.parquet")
        if not os.path.exists(intra_path):
            return None

        df_intra = pd.read_parquet(intra_path)
        if df_intra.empty:
            return None

        # Ensure timezone aware
        if df_intra.index.tz is None:
            df_intra.index = df_intra.index.tz_localize('UTC')

        # Convert to ET for time filtering
        df_et = df_intra.copy()
        df_et.index = df_et.index.tz_convert(TZ_ET)

        # Extract last trading hour
        last_hour = df_et.between_time('15:00', '16:00')
        if len(last_hour) < 5:
            return None

        # Calculate last hour move and day move
        last_hour_move = last_hour["Close"].iloc[-1] - last_hour["Open"].iloc[0]
        day_move = day_data["Close"] - day_data["Open"]

        # Calculate continuation ratio
        continuation_ratio = 0
        if day_move != 0:
            continuation_ratio = last_hour_move / day_move

        return {
            "last_hour_move": last_hour_move,
            "day_move": day_move,
            "continuation_ratio": continuation_ratio,
            "direction": "same" if continuation_ratio > 0 else "opposite",
            "vetoed": continuation_ratio > VETO_THRESHOLD
        }

    def generate_signal(self, context, day_data):
        """
        Generate trade recommendation applying all filters.

        Filter cascade:
        1. Flat day filter (< 0.10% move)
        2. Friday exclusion (no Friday trades)
        3. Direction-based signal (GREEN -> BUY PUT, RED -> BUY CALL)
        4. LastHourVeto filter (continuation > 20% threshold)

        Args:
            context: Market context dict from get_market_context()
            day_data: Daily bar data Series

        Returns:
            tuple: (signal, reason, last_hour_info)
                - signal: "BUY PUT (Fade Green)", "BUY CALL (Fade Red)", or "NO_TRADE"
                - reason: Human-readable explanation
                - last_hour_info: Last hour analysis dict or None
        """
        last_hour_info = None

        # Filter 1: Noise/Flat Day
        if abs(context["Magnitude"]) < self.FLAT_THRESHOLD_PCT:
            return "NO_TRADE", f"Flat Day (Move < {self.FLAT_THRESHOLD_PCT}%)", None

        # Filter 2: Day of Week (No Fridays)
        if context["Date"].dayofweek == 4:
            return "NO_TRADE", "Friday Exclusion", None

        # Filter 3: Get base direction signal
        if context["Direction"] == "GREEN":
            if not self.config["filters"]["enable_fade_green"]:
                return "NO_TRADE", "Fade Green Disabled in Config", None
            base_signal = "BUY PUT (Fade Green)"
            base_reason = f"Session Up +{context['Magnitude']:.2f}%"

        elif context["Direction"] == "RED":
            if not self.config["filters"]["enable_fade_red"]:
                return "NO_TRADE", "Fade Red Disabled in Config", None
            base_signal = "BUY CALL (Fade Red)"
            base_reason = f"Session Down {context['Magnitude']:.2f}%"
        else:
            return "NO_TRADE", "Unknown Direction", None

        # Filter 4: Apply LastHourVeto filter
        date_str = context["Date"].strftime("%Y-%m-%d")
        last_hour_info = self.analyze_last_hour(date_str, day_data)

        if last_hour_info and last_hour_info["vetoed"]:
            veto_reason = (
                f"VETOED: Last hour continued {context['Direction'].lower()} trend "
                f"({last_hour_info['continuation_ratio']:.0%} > {VETO_THRESHOLD:.0%} threshold)"
            )
            return "NO_TRADE", veto_reason, last_hour_info

        # Signal passes all filters
        return base_signal, base_reason, last_hour_info

    def run(self):
        """
        Main dashboard execution.

        Displays:
        1. Market context (SPY and SPX data)
        2. LastHourVeto filter analysis
        3. Trade recommendation
        4. Execution plans for IBKR and IG.com
        5. Historical backtest statistics
        """
        console.clear()
        console.print(Panel.fit(
            "Overnight Fade | Decision Support System v3.1 + Auto-Fetch",
            style="bold blue"
        ))

        # Auto-fetch if data is stale
        self._auto_fetch_if_stale()

        # Load data (after potential fetch)
        self._load_data()

        # Get market context
        ctx = self.get_market_context()
        if not ctx:
            console.print("[red]Error: No Data Found. Run data_manager.py with ALLOW_NETWORK=1 first.[/red]")
            return

        # Check data freshness and warn if not current
        data_date = ctx["Date"].strftime("%Y-%m-%d")
        today_str = self._get_today_et().strftime("%Y-%m-%d")
        if data_date < today_str:
            console.print(f"[yellow]Warning: Showing data from {data_date}, not today ({today_str})[/yellow]\n")

        # Get full day data for LastHourVeto analysis
        day_data = self.bt.daily_df.iloc[-1]

        # Run backtest for historical context
        history = pd.DataFrame()
        with console.status("[bold green]Running live backtest analysis...", spinner="dots"):
            history = self.bt.run(take_profit_atr_mult=self.config["default_take_profit_atr"])

        # Calculate backtest statistics
        if not history.empty and len(history) > 0:
            win_rate = len(history[history['Result']=='WIN']) / len(history) * 100
            avg_pnl = history['PnL_Mult'].mean()
            recent_pnl = history['PnL_Dollar'].tail(5).sum()
        else:
            win_rate = 0.0
            avg_pnl = 0.0
            recent_pnl = 0.0

        # Generate today's signal with all filters
        signal, reason, last_hour_info = self.generate_signal(ctx, day_data)

        # ========================================
        # DISPLAY: Section A - Market Context
        # ========================================

        # Get SPX data for the same date as SPY data
        target_date = ctx["Date"].strftime("%Y-%m-%d")
        spx_close, spx_atr, spx_source = get_spx_data(ctx['Close'], ctx['ATR'], self.config, target_date)

        table_ctx = Table(show_header=False, box=None)
        table_ctx.add_row("Analysis Date:", f"[bold]{ctx['Date'].strftime('%Y-%m-%d')}[/bold]")
        table_ctx.add_row("SPY Close:", f"[bold]{ctx['Close']:.2f}[/bold]")
        table_ctx.add_row("SPY ATR:", f"{ctx['ATR']:.2f}")
        table_ctx.add_row("SPX/US 500:", f"[bold]{spx_close:.2f}[/bold] [dim]({spx_source})[/dim]")
        table_ctx.add_row("SPX ATR:", f"{spx_atr:.2f}")
        table_ctx.add_row("Direction:", f"[{'green' if ctx['Direction']=='GREEN' else 'red'}]{ctx['Direction']} ({ctx['Magnitude']:.2f}%)[/]")

        console.print(Panel(table_ctx, title="Market Context", border_style="cyan"))

        # ========================================
        # DISPLAY: Section A2 - Last Hour Analysis
        # ========================================

        if last_hour_info:
            lh_color = "red" if last_hour_info["vetoed"] else "green"
            lh_status = "VETO" if last_hour_info["vetoed"] else "PASS"

            # Show both SPY and SPX scale moves
            lh_move_spy = last_hour_info['last_hour_move']
            actual_ratio = spx_close / ctx['Close'] if ctx['Close'] > 0 else 10.0
            lh_move_spx = lh_move_spy * actual_ratio
            lh_spy_str = f"+{lh_move_spy:.2f}" if lh_move_spy > 0 else f"{lh_move_spy:.2f}"
            lh_spx_str = f"+{lh_move_spx:.2f}" if lh_move_spx > 0 else f"{lh_move_spx:.2f}"

            table_lh = Table(show_header=False, box=None)
            table_lh.add_row("Last Hour (15:00-16:00 ET):", f"SPY {lh_spy_str} / SPX {lh_spx_str}")
            table_lh.add_row("Continuation Ratio:", f"{last_hour_info['continuation_ratio']:.0%} of day move")
            table_lh.add_row("Veto Threshold:", f"{VETO_THRESHOLD:.0%}")
            table_lh.add_row("Filter Result:", f"[bold {lh_color}]{lh_status}[/]")

            console.print(Panel(table_lh, title="LastHourVeto Filter", border_style=lh_color))

        # ========================================
        # DISPLAY: Section B - Trade Signal
        # ========================================

        color = "green" if "BUY" in signal else "yellow"
        text_sig = Text(f"\n{signal}\n", style=f"bold {color} reverse", justify="center")
        text_reason = Text(f"REASON: {reason}", style="bold white", justify="center")

        console.print(Panel(text_sig, title="RECOMMENDATION", subtitle=text_reason))

        # ========================================
        # DISPLAY: Section C - Execution Plans
        # ========================================

        if "BUY" in signal:
            atr_mult = self.config["default_take_profit_atr"]
            option_type = "PUT" if "PUT" in signal else "CALL"

            # Calculate SPY execution plan (IBKR)
            spy_close = ctx['Close']
            spy_atr = ctx['ATR']
            spy_target_move = spy_atr * atr_mult

            if option_type == "PUT":
                spy_limit = spy_close - spy_target_move
                spy_strike = int(round(spy_close))
                spy_strike_rec = f"{spy_strike} or {spy_strike - 1} PUT"
            else:
                spy_limit = spy_close + spy_target_move
                spy_strike = int(round(spy_close))
                spy_strike_rec = f"{spy_strike} or {spy_strike + 1} CALL"

            table_spy = Table(title="[bold blue]IBKR - SPY Options[/bold blue]", show_lines=True)
            table_spy.add_column("Parameter", style="cyan")
            table_spy.add_column("Value", style="bold white")

            table_spy.add_row("Instrument", "SPY Options")
            table_spy.add_row("Expiry", "0-DTE or 1-DTE")
            table_spy.add_row("Strike Zone", spy_strike_rec)
            table_spy.add_row("Take Profit Target", f"[bold]{spy_limit:.2f}[/bold]")
            table_spy.add_row("Target Move", f"${spy_target_move:.2f} ({atr_mult}x ATR)")
            table_spy.add_row("Risk", f"Premium Paid")

            console.print(table_spy)

            # Calculate SPX execution plan (IG.com)
            spx_target_move = spx_atr * atr_mult

            if option_type == "PUT":
                spx_limit = spx_close - spx_target_move
                spx_strike = int(round(spx_close / 5) * 5)  # Round to nearest 5
                spx_strike_rec = f"{spx_strike} or {spx_strike - 5} PUT"
            else:
                spx_limit = spx_close + spx_target_move
                spx_strike = int(round(spx_close / 5) * 5)
                spx_strike_rec = f"{spx_strike} or {spx_strike + 5} CALL"

            table_ig = Table(title="[bold magenta]IG.com - US 500 Options[/bold magenta]", show_lines=True)
            table_ig.add_column("Parameter", style="cyan")
            table_ig.add_column("Value", style="bold white")

            table_ig.add_row("Instrument", "US 500 (24h Options)")
            table_ig.add_row("Expiry", "Daily (1-DTE)")
            table_ig.add_row("Strike Zone", spx_strike_rec)
            table_ig.add_row("Take Profit Target", f"[bold]{spx_limit:.2f}[/bold]")
            table_ig.add_row("Target Move", f"{spx_target_move:.2f} pts ({atr_mult}x ATR)")
            table_ig.add_row("Risk", f"Premium Paid (${self.config['premium_budget']})")

            console.print(table_ig)

            console.print("\n[bold yellow]Monitoring:[/bold yellow] Set GTC Limit Order. Check at [bold]08:00 UTC[/bold] (Pre-Market).")

        # ========================================
        # DISPLAY: Section D - Historical Confidence
        # ========================================

        table_stats = Table(box=None)
        table_stats.add_column("Metric")
        table_stats.add_column("Value")

        table_stats.add_row("Historical Trades", str(len(history)))
        table_stats.add_row("Win Rate", f"{win_rate:.1f}%")
        table_stats.add_row("Avg Net PnL", f"{avg_pnl:+.2f}R")
        table_stats.add_row("Last 5 PnL", f"[bold {'green' if recent_pnl>0 else 'red'}]${recent_pnl:,.2f}[/]")

        console.print(Panel(table_stats, title="Confidence Check (History)", border_style="magenta"))


if __name__ == "__main__":
    dash = Dashboard()
    dash.run()
