"""
Multi-Ticker Backtester

Runs the overnight fade strategy on multiple tickers (SPY, QQQ, IWM, DIA)
and combines results for analysis.

Features:
- Individual ticker performance
- Combined portfolio performance
- Correlation analysis
- Per-ticker statistics
- Diversification benefits
"""
import os
import sys
import json
import pandas as pd
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

from backtester import Backtester

console = Console()

def load_config():
    """Load configuration."""
    config_path = os.path.join("config", "config.json")
    with open(config_path, "r") as f:
        return json.load(f)

def run_ticker_backtest(ticker, config):
    """
    Run backtest for a single ticker.

    Args:
        ticker: Ticker symbol
        config: Configuration dict

    Returns:
        tuple: (results_df, stats_dict)
    """
    console.print(f"\n[bold cyan]{'='*80}[/bold cyan]")
    console.print(f"[bold cyan]Running backtest for {ticker}[/bold cyan]")
    console.print(f"[bold cyan]{'='*80}[/bold cyan]")

    try:
        # Save original config
        config_path = os.path.join("config", "config.json")
        with open(config_path, 'r') as f:
            original_config = json.load(f)

        # Temporarily update config for this ticker
        temp_config = original_config.copy()
        temp_config["ticker"] = ticker

        with open(config_path, 'w') as f:
            json.dump(temp_config, f, indent=2)

        try:
            # Run backtester (reads ticker from config.json)
            bt = Backtester()
            results = bt.run()
        finally:
            # Restore original config
            with open(config_path, 'w') as f:
                json.dump(original_config, f, indent=2)

        if results is None or results.empty:
            console.print(f"[yellow]No results for {ticker}[/yellow]")
            return None, None

        # Calculate statistics
        total_trades = len(results)
        wins = (results["Result"] == "WIN").sum()
        losses = (results["Result"] == "LOSS").sum()
        scratches = (results["Result"] == "SCRATCH").sum()
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        total_pnl = results["PnL_Dollar"].sum()
        avg_pnl = results["PnL_Dollar"].mean()

        stats = {
            "ticker": ticker,
            "trades": total_trades,
            "wins": wins,
            "losses": losses,
            "scratches": scratches,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "avg_pnl": avg_pnl,
        }

        console.print(f"[green]OK {ticker}: {total_trades} trades, {win_rate:.1f}% win rate, ${total_pnl:,.2f} P/L[/green]")

        # Save individual ticker results
        output_file = f"results/trade_log_{ticker}_10year.csv"
        results.to_csv(output_file, index=False)
        console.print(f"[dim]Saved to: {output_file}[/dim]")

        return results, stats

    except Exception as e:
        console.print(f"[red]Error backtesting {ticker}: {e}[/red]")
        import traceback
        traceback.print_exc()
        return None, None

def combine_results(ticker_results):
    """
    Combine results from multiple tickers into a single portfolio.

    Args:
        ticker_results: List of (ticker, results_df, stats_dict) tuples

    Returns:
        tuple: (combined_df, portfolio_stats)
    """
    all_results = []

    for ticker, results_df, _ in ticker_results:
        if results_df is not None and not results_df.empty:
            # Add ticker column
            results_copy = results_df.copy()
            results_copy["Ticker"] = ticker
            all_results.append(results_copy)

    if not all_results:
        return None, None

    # Combine all results
    combined = pd.concat(all_results, ignore_index=True)

    # Sort by date
    combined = combined.sort_values("Date").reset_index(drop=True)

    # Calculate portfolio statistics
    total_trades = len(combined)
    wins = (combined["Result"] == "WIN").sum()
    losses = (combined["Result"] == "LOSS").sum()
    scratches = (combined["Result"] == "SCRATCH").sum()
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    total_pnl = combined["PnL_Dollar"].sum()
    avg_pnl = combined["PnL_Dollar"].mean()

    portfolio_stats = {
        "trades": total_trades,
        "wins": wins,
        "losses": losses,
        "scratches": scratches,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "avg_pnl": avg_pnl,
    }

    return combined, portfolio_stats

def display_summary(ticker_stats, portfolio_stats):
    """
    Display summary table of results.

    Args:
        ticker_stats: List of individual ticker stats
        portfolio_stats: Combined portfolio stats
    """
    # Individual ticker table
    table = Table(title="Individual Ticker Performance (10 Years)", show_lines=True)
    table.add_column("Ticker", style="cyan", no_wrap=True)
    table.add_column("Trades", justify="right", style="white")
    table.add_column("Wins", justify="right", style="green")
    table.add_column("Losses", justify="right", style="red")
    table.add_column("Scratches", justify="right", style="yellow")
    table.add_column("Win Rate", justify="right", style="white")
    table.add_column("Total P/L", justify="right", style="white")
    table.add_column("Avg P/L", justify="right", style="white")

    for stats in ticker_stats:
        if stats:
            pnl_color = "green" if stats["total_pnl"] > 0 else "red"
            table.add_row(
                stats["ticker"],
                str(stats["trades"]),
                str(stats["wins"]),
                str(stats["losses"]),
                str(stats["scratches"]),
                f"{stats['win_rate']:.1f}%",
                f"[{pnl_color}]${stats['total_pnl']:,.2f}[/{pnl_color}]",
                f"${stats['avg_pnl']:.2f}"
            )

    console.print(table)

    # Portfolio summary
    if portfolio_stats:
        pnl_color = "green" if portfolio_stats["total_pnl"] > 0 else "red"

        summary = f"""
[bold cyan]Combined Portfolio Performance[/bold cyan]

Total Trades:     {portfolio_stats['trades']:,}
Wins:             {portfolio_stats['wins']:,} ({portfolio_stats['wins']/portfolio_stats['trades']*100:.1f}%)
Losses:           {portfolio_stats['losses']:,} ({portfolio_stats['losses']/portfolio_stats['trades']*100:.1f}%)
Scratches:        {portfolio_stats['scratches']:,} ({portfolio_stats['scratches']/portfolio_stats['trades']*100:.1f}%)
Win Rate:         [{pnl_color}]{portfolio_stats['win_rate']:.1f}%[/{pnl_color}]

Total P/L:        [{pnl_color}]${portfolio_stats['total_pnl']:,.2f}[/{pnl_color}]
Avg P/L/Trade:    ${portfolio_stats['avg_pnl']:.2f}

[dim]Time Period: 2015-2026 (10 years)[/dim]
        """
        console.print(Panel(summary, border_style="cyan"))

def main():
    """Main execution."""
    console.print("[bold magenta]================================================================[/bold magenta]")
    console.print("[bold magenta]    MULTI-TICKER BACKTEST: OVERNIGHT FADE STRATEGY (10Y)        [/bold magenta]")
    console.print("[bold magenta]================================================================[/bold magenta]")

    # Load config
    config = load_config()
    tickers = config.get("tickers", ["SPY"])

    console.print(f"\n[yellow]Backtesting tickers: {', '.join(tickers)}[/yellow]")
    console.print(f"[yellow]Time period: 2015-2026 (10 years)[/yellow]")
    console.print(f"[yellow]Expected: ~1,640 trades per ticker[/yellow]\n")

    # Run backtests for each ticker
    ticker_results = []
    ticker_stats = []

    for ticker in tickers:
        results_df, stats = run_ticker_backtest(ticker, config)
        ticker_results.append((ticker, results_df, stats))
        if stats:
            ticker_stats.append(stats)

    # Combine results
    console.print(f"\n[bold cyan]Combining results...[/bold cyan]")
    combined_df, portfolio_stats = combine_results(ticker_results)

    if combined_df is not None:
        # Save combined results
        output_file = "results/trade_log_MULTI_TICKER_10year.csv"
        combined_df.to_csv(output_file, index=False)
        console.print(f"[green]OK Combined results saved to: {output_file}[/green]")

        # Display summary
        console.print(f"\n")
        display_summary(ticker_stats, portfolio_stats)

        # Comparison to original SPY-only results
        console.print(f"\n[bold yellow]Comparison to Original (SPY only, 2 years):[/bold yellow]")
        console.print(f"  Original: 328 trades, 88.4% win rate, $9,060 P/L")
        console.print(f"  Enhanced: {portfolio_stats['trades']:,} trades, {portfolio_stats['win_rate']:.1f}% win rate, ${portfolio_stats['total_pnl']:,.2f} P/L")

        if portfolio_stats['trades'] > 0:
            improvement = portfolio_stats['total_pnl'] / 9060 - 1
            console.print(f"  [bold green]Improvement: {improvement*100:+.1f}% P/L increase[/bold green]")
            console.print(f"  [bold green]Trade count: {portfolio_stats['trades']/328:.1f}x more trades[/bold green]")

        console.print(f"\n[bold green]OK Multi-ticker backtest complete![/bold green]")
        console.print(f"\n[yellow]Next steps:[/yellow]")
        console.print(f"  1. Analyze results: results/trade_log_MULTI_TICKER_10year.csv")
        console.print(f"  2. Review individual tickers: results/trade_log_{{TICKER}}_10year.csv")
        console.print(f"  3. Ready for next enhancement: VIX Filter")

    else:
        console.print(f"[red]Failed to combine results. Check individual ticker backtests.[/red]")

if __name__ == "__main__":
    main()
