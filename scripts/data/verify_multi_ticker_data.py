"""
Verify Multi-Ticker Data Quality

Checks:
- Date ranges for all tickers
- Data completeness
- Missing dates
- File counts
- Data quality
"""
import os
import pandas as pd
from rich.console import Console
from rich.table import Table
import json

console = Console()

def load_config():
    """Load configuration."""
    with open("config/config.json", "r") as f:
        return json.load(f)

def check_ticker_data(ticker):
    """
    Check data availability for a ticker.

    Returns:
        dict: Data statistics
    """
    daily_path = f"data/{ticker}/daily_OHLCV.parquet"
    intraday_dir = f"data/{ticker}/intraday"

    stats = {
        "ticker": ticker,
        "daily_exists": os.path.exists(daily_path),
        "daily_start": None,
        "daily_end": None,
        "daily_days": 0,
        "intraday_files": 0,
        "intraday_start": None,
        "intraday_end": None,
        "status": "X"
    }

    # Check daily data
    if stats["daily_exists"]:
        try:
            df = pd.read_parquet(daily_path)
            if not df.empty:
                stats["daily_start"] = df.index[0].strftime("%Y-%m-%d")
                stats["daily_end"] = df.index[-1].strftime("%Y-%m-%d")
                stats["daily_days"] = len(df)
        except Exception as e:
            console.print(f"[red]Error reading {ticker} daily data: {e}[/red]")

    # Check intraday data
    if os.path.exists(intraday_dir):
        files = [f for f in os.listdir(intraday_dir) if f.endswith(".parquet")]
        stats["intraday_files"] = len(files)

        if files:
            # Get date range from filenames
            dates = sorted([f.replace(".parquet", "") for f in files])
            stats["intraday_start"] = dates[0]
            stats["intraday_end"] = dates[-1]

    # Determine status
    if stats["daily_exists"] and stats["daily_days"] > 2000 and stats["intraday_files"] > 2000:
        stats["status"] = "OK Complete"
    elif stats["daily_exists"] and stats["daily_days"] > 1000:
        stats["status"] = "! Partial"
    else:
        stats["status"] = "X Missing"

    return stats

def main():
    """Main verification."""
    console.print("[bold cyan]============================================================[/bold cyan]")
    console.print("[bold cyan]       MULTI-TICKER DATA VERIFICATION                       [/bold cyan]")
    console.print("[bold cyan]============================================================[/bold cyan]")

    # Load config
    config = load_config()
    main_tickers = config.get("tickers", ["SPY"])
    vix_ticker = config.get("vix_ticker", "VIX")
    sector_etfs = config.get("sector_etfs", [])

    all_tickers = main_tickers + [vix_ticker] + sector_etfs

    console.print(f"\n[yellow]Checking {len(all_tickers)} tickers...[/yellow]\n")

    # Check each ticker
    results = []
    for ticker in all_tickers:
        stats = check_ticker_data(ticker)
        results.append(stats)

    # Display results table
    table = Table(title="Data Availability Summary", show_lines=True)
    table.add_column("Ticker", style="cyan", no_wrap=True)
    table.add_column("Daily Data", style="white")
    table.add_column("Daily Range", style="white")
    table.add_column("Intraday Files", style="white")
    table.add_column("Intraday Range", style="white")
    table.add_column("Status", style="bold")

    for stat in results:
        # Daily info
        if stat["daily_exists"]:
            daily_info = f"{stat['daily_days']} days"
            daily_range = f"{stat['daily_start']} to {stat['daily_end']}"
        else:
            daily_info = "Missing"
            daily_range = "N/A"

        # Intraday info
        if stat["intraday_files"] > 0:
            intraday_info = f"{stat['intraday_files']} files"
            intraday_range = f"{stat['intraday_start']} to {stat['intraday_end']}"
        else:
            intraday_info = "Missing"
            intraday_range = "N/A"

        # Status color
        if "OK" in stat["status"]:
            status_style = "bold green"
        elif "!" in stat["status"]:
            status_style = "bold yellow"
        else:
            status_style = "bold red"

        table.add_row(
            stat["ticker"],
            daily_info,
            daily_range,
            intraday_info,
            intraday_range,
            f"[{status_style}]{stat['status']}[/{status_style}]"
        )

    console.print(table)

    # Summary
    complete = sum(1 for r in results if "OK" in r["status"])
    partial = sum(1 for r in results if "!" in r["status"])
    missing = sum(1 for r in results if "X" in r["status"])

    console.print(f"\n[bold]Summary:[/bold]")
    console.print(f"  [green]OK Complete: {complete}[/green]")
    console.print(f"  [yellow]! Partial:  {partial}[/yellow]")
    console.print(f"  [red]X Missing:  {missing}[/red]")

    # Recommendations
    console.print(f"\n[bold cyan]Expected for 10-year data (2015-2026):[/bold cyan]")
    console.print(f"  • Daily: ~2,500 trading days")
    console.print(f"  • Intraday: ~2,500 files")

    if complete == len(all_tickers):
        console.print(f"\n[bold green]OK All data ready for backtesting![/bold green]")
        console.print(f"\n[yellow]Next step:[/yellow]")
        console.print(f"  python src/backtester_multi_ticker.py")
    elif partial > 0:
        console.print(f"\n[yellow]! Some tickers have partial data.[/yellow]")
        console.print(f"[yellow]You can still backtest, but results may be limited.[/yellow]")
    else:
        console.print(f"\n[red]X Data fetch incomplete. Re-run:[/red]")
        console.print(f"  python fetch_multi_ticker_data.py")

if __name__ == "__main__":
    main()
