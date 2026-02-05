"""
Simple Multi-Ticker Backtest - No Unicode, No Rich
Just runs the backtest and saves results
"""
import os
import sys
import json
import pandas as pd
from datetime import datetime

sys.path.insert(0, 'src')
from backtester import Backtester

def load_config():
    with open("config/config.json", "r") as f:
        return json.load(f)

def save_config(config):
    with open("config/config.json", "w") as f:
        json.dump(config, f, indent=2)

def run_ticker_backtest(ticker):
    """Run backtest for one ticker by temporarily updating config."""
    print(f"\n{'='*80}")
    print(f"Running backtest for {ticker}")
    print(f"{'='*80}")

    # Load config
    config = load_config()
    original_ticker = config["ticker"]

    # Set ticker
    config["ticker"] = ticker
    save_config(config)

    try:
        bt = Backtester()
        results = bt.run()

        if results is None or results.empty:
            print(f"No results for {ticker}")
            return None, None

        # Stats
        total_trades = len(results)
        wins = (results["Result"] == "WIN").sum()
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        total_pnl = results["PnL_Dollar"].sum()

        print(f"{ticker}: {total_trades} trades, {win_rate:.1f}% win rate, ${total_pnl:,.2f} P/L")

        # Save
        output_file = f"results/trade_log_{ticker}_10year.csv"
        results.to_csv(output_file, index=False)
        print(f"Saved to: {output_file}")

        return results, {
            "ticker": ticker,
            "trades": total_trades,
            "wins": wins,
            "win_rate": win_rate,
            "total_pnl": total_pnl
        }
    except Exception as e:
        print(f"ERROR backtesting {ticker}: {e}")
        import traceback
        traceback.print_exc()
        return None, None
    finally:
        # Restore original
        config["ticker"] = original_ticker
        save_config(config)

def main():
    print("="*80)
    print("MULTI-TICKER BACKTEST - 10 YEARS")
    print("="*80)

    config = load_config()
    tickers = config.get("tickers", ["SPY"])

    print(f"\nBacktesting: {', '.join(tickers)}")
    print(f"Time period: 2015-2026 (10 years)\n")

    all_results = []
    all_stats = []

    for ticker in tickers:
        results, stats = run_ticker_backtest(ticker)
        if results is not None:
            results_copy = results.copy()
            results_copy["Ticker"] = ticker
            all_results.append(results_copy)
            all_stats.append(stats)

    # Combine
    if all_results:
        combined = pd.concat(all_results, ignore_index=True)
        combined = combined.sort_values("Date").reset_index(drop=True)

        # Save combined
        output_file = "results/trade_log_MULTI_TICKER_10year.csv"
        combined.to_csv(output_file, index=False)

        print(f"\n{'='*80}")
        print("COMBINED RESULTS")
        print(f"{'='*80}")

        total_trades = len(combined)
        wins = (combined["Result"] == "WIN").sum()
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        total_pnl = combined["PnL_Dollar"].sum()
        avg_pnl = combined["PnL_Dollar"].mean()

        print(f"\nTotal Trades: {total_trades:,}")
        print(f"Wins: {wins:,} ({win_rate:.1f}%)")
        print(f"Total P/L: ${total_pnl:,.2f}")
        print(f"Avg P/L per trade: ${avg_pnl:.2f}")

        print(f"\nResults saved to: {output_file}")

        # Per-ticker summary
        print(f"\n{'='*80}")
        print("PER-TICKER SUMMARY")
        print(f"{'='*80}")
        for stat in all_stats:
            print(f"{stat['ticker']}: {stat['trades']} trades, {stat['win_rate']:.1f}% WR, ${stat['total_pnl']:,.2f}")

        print(f"\n{'='*80}")
        print("BACKTEST COMPLETE!")
        print(f"{'='*80}")

        return True
    else:
        print("\nNo results generated")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
