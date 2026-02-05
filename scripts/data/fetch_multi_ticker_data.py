"""
Enhanced Multi-Ticker Data Fetcher for Stocks Developer Plan

Fetches 10 years of data for:
- SPY, QQQ, IWM, DIA (main trading tickers)
- VIX (volatility filter)
- Sector ETFs (XLK, XLF, XLE, XLV, XLY, XLU, XLRE, XLI, XLB)

Stocks Developer: 10 years historical, unlimited API calls
"""
import os
import sys
import json

# Set environment variable
os.environ['ALLOW_NETWORK'] = '1'

# Add src to path
sys.path.insert(0, 'src')

from data_manager import DataManager
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn

console = Console()

def load_config():
    """Load configuration."""
    with open("config/config.json", "r") as f:
        return json.load(f)

def fetch_ticker_data(ticker, dm, description=""):
    """
    Fetch data for a single ticker.

    Args:
        ticker: Ticker symbol
        dm: DataManager instance
        description: Description for progress display
    """
    console.print(f"\n[bold cyan]{'='*80}[/bold cyan]")
    console.print(f"[bold cyan]Fetching {ticker} - {description}[/bold cyan]")
    console.print(f"[bold cyan]{'='*80}[/bold cyan]")

    # Temporarily override ticker
    original_ticker = dm.ticker
    original_ticker_dir = dm.ticker_dir
    original_intraday_dir = dm.intraday_dir

    dm.ticker = ticker
    dm.ticker_dir = os.path.join(dm.base_dir, ticker)
    dm.intraday_dir = os.path.join(dm.ticker_dir, "intraday")

    # Ensure directories exist
    os.makedirs(dm.intraday_dir, exist_ok=True)

    try:
        # Fetch daily data
        console.print(f"[cyan]Step 1/2: Fetching {ticker} daily data (2015-2026)...[/cyan]")
        dm.update_daily_data()

        # Fetch intraday data
        console.print(f"[cyan]Step 2/2: Fetching {ticker} minute data (10 years)...[/cyan]")
        dm.update_intraday_data()

        console.print(f"[bold green]OK {ticker} data fetch complete![/bold green]")

    finally:
        # Restore original ticker
        dm.ticker = original_ticker
        dm.ticker_dir = original_ticker_dir
        dm.intraday_dir = original_intraday_dir

def main():
    """Main execution."""
    console.print("[bold magenta]================================================================[/bold magenta]")
    console.print("[bold magenta]   STOCKS DEVELOPER: MULTI-TICKER DATA FETCH (10 YEARS)         [/bold magenta]")
    console.print("[bold magenta]================================================================[/bold magenta]")

    # Load config
    config = load_config()

    # Get tickers
    main_tickers = config.get("tickers", ["SPY"])
    vix_ticker = config.get("vix_ticker", "VIX")
    sector_etfs = config.get("sector_etfs", [])

    console.print(f"\n[yellow]Plan: Stocks Developer ($79)[/yellow]")
    console.print(f"[yellow]Historical: 10 years (2015-2026)[/yellow]")
    console.print(f"[yellow]API Calls: Unlimited[/yellow]")

    console.print(f"\n[cyan]Main Tickers: {', '.join(main_tickers)}[/cyan]")
    console.print(f"[cyan]VIX Ticker: {vix_ticker}[/cyan]")
    console.print(f"[cyan]Sector ETFs: {', '.join(sector_etfs)}[/cyan]")
    console.print(f"[cyan]Total: {len(main_tickers) + 1 + len(sector_etfs)} tickers[/cyan]")

    # Confirm
    console.print(f"\n[bold yellow]This will fetch ~50,000+ API calls over ~30-45 minutes.[/bold yellow]")
    console.print(f"[bold yellow]With Stocks Developer unlimited calls, this is no problem![/bold yellow]")

    # Initialize data manager
    console.print(f"\n[cyan]Initializing data manager...[/cyan]")
    try:
        dm = DataManager(require_network=True)
    except Exception as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        console.print(f"[yellow]Make sure you've updated .env with your Stocks Developer API key![/yellow]")
        return

    # Track overall progress
    all_tickers = main_tickers + [vix_ticker] + sector_etfs
    total_tickers = len(all_tickers)

    console.print(f"\n[bold cyan]Starting fetch for {total_tickers} tickers...[/bold cyan]")

    for idx, ticker in enumerate(all_tickers, 1):
        # Determine description
        if ticker in main_tickers:
            desc = "Main Trading Ticker"
        elif ticker == vix_ticker:
            desc = "Volatility Index (VIX Filter)"
        else:
            desc = "Sector ETF (Rotation Analysis)"

        console.print(f"\n[bold white]Progress: {idx}/{total_tickers} tickers[/bold white]")

        try:
            fetch_ticker_data(ticker, dm, desc)
        except KeyboardInterrupt:
            console.print(f"\n[yellow]Fetch cancelled by user.[/yellow]")
            console.print(f"[cyan]Progress saved. Run again to resume.[/cyan]")
            break
        except Exception as e:
            console.print(f"[red]Error fetching {ticker}: {e}[/red]")
            console.print(f"[yellow]Continuing to next ticker...[/yellow]")
            continue

    # Summary
    console.print(f"\n[bold magenta]{'='*80}[/bold magenta]")
    console.print(f"[bold green]OK MULTI-TICKER DATA FETCH COMPLETE![/bold green]")
    console.print(f"[bold magenta]{'='*80}[/bold magenta]")

    console.print(f"\n[cyan]Fetched data for:[/cyan]")
    console.print(f"  • Main tickers: {', '.join(main_tickers)}")
    console.print(f"  • VIX: {vix_ticker}")
    console.print(f"  • Sectors: {', '.join(sector_etfs)}")

    console.print(f"\n[yellow]Next steps:[/yellow]")
    console.print(f"  1. Verify data: python verify_multi_ticker_data.py")
    console.print(f"  2. Run backtest: python src/backtester_multi_ticker.py")
    console.print(f"  3. Compare results across tickers")

    console.print(f"\n[bold green]Ready to backtest 10 years across {len(main_tickers)} tickers![/bold green]")
    console.print(f"[bold green]Expected: ~{len(main_tickers) * 1640} total trades[/bold green]")

if __name__ == "__main__":
    main()
