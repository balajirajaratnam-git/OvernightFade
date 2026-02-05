"""
Phase 2: VXX Filter Enhancement
Backtest with VXX volatility filtering at multiple thresholds
"""
import os
import sys
import json
import pandas as pd
import numpy as np
from datetime import datetime

sys.path.insert(0, 'src')
from backtester import Backtester

def load_config():
    with open("config/config.json", "r") as f:
        return json.load(f)

def save_config(config):
    with open("config/config.json", "w") as f:
        json.dump(config, f, indent=2)

def load_vxx_data():
    """Load VXX daily data and calculate percentile thresholds."""
    vxx = pd.read_parquet('data/VXX/daily_OHLCV.parquet')

    # Calculate percentile thresholds
    thresholds = {
        'p33': np.percentile(vxx['Close'], 33),
        'p50': np.percentile(vxx['Close'], 50),
        'p67': np.percentile(vxx['Close'], 67),
        'p75': np.percentile(vxx['Close'], 75),
        'p90': np.percentile(vxx['Close'], 90),
    }

    # Convert index to date only for easier matching
    vxx.index = vxx.index.date

    return vxx, thresholds

def run_backtest_with_vxx_filter(ticker, vxx_df, vxx_threshold=None, threshold_name="No Filter"):
    """Run backtest with optional VXX filter."""
    print(f"\n{'='*80}")
    print(f"Running: {ticker} - {threshold_name}")
    print(f"{'='*80}")

    # Load config and set ticker
    config = load_config()
    original_ticker = config["ticker"]
    config["ticker"] = ticker
    save_config(config)

    try:
        # Run backtest
        bt = Backtester()
        results = bt.run()

        if results is None or results.empty:
            print(f"No results for {ticker}")
            return None

        # Apply VXX filter if threshold provided
        if vxx_threshold is not None and vxx_df is not None:
            # Convert Date column to date for matching
            results['Date_obj'] = pd.to_datetime(results['Date']).dt.date

            # Merge with VXX data
            results = results.merge(
                vxx_df[['Close']].rename(columns={'Close': 'VXX_Close'}),
                left_on='Date_obj',
                right_index=True,
                how='left'
            )

            # Filter trades where VXX >= threshold
            before_count = len(results)
            results = results[results['VXX_Close'] >= vxx_threshold].copy()
            after_count = len(results)

            print(f"VXX Filter: {before_count} trades -> {after_count} trades ({after_count/before_count*100:.1f}% kept)")

        if results.empty:
            print(f"No trades after VXX filter for {ticker}")
            return None

        # Calculate stats
        total_trades = len(results)
        wins = (results["Result"] == "WIN").sum()
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        total_pnl = results["PnL_Dollar"].sum()
        avg_pnl = results["PnL_Dollar"].mean()

        print(f"{ticker}: {total_trades} trades, {win_rate:.1f}% WR, ${total_pnl:,.2f} P/L, ${avg_pnl:.2f} avg")

        return {
            "ticker": ticker,
            "filter": threshold_name,
            "trades": total_trades,
            "wins": wins,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "avg_pnl": avg_pnl,
            "results_df": results
        }

    except Exception as e:
        print(f"ERROR backtesting {ticker}: {e}")
        import traceback
        traceback.print_exc()
        return None
    finally:
        # Restore original ticker
        config["ticker"] = original_ticker
        save_config(config)

def main():
    print("="*80)
    print("PHASE 2: VXX VOLATILITY FILTER ANALYSIS")
    print("="*80)
    print("\nComparing baseline (no filter) vs VXX percentile filters")
    print("Testing filters: No Filter, 33rd, 50th, 67th, 75th, 90th percentile\n")

    config = load_config()
    tickers = config.get("tickers", ["SPY", "QQQ", "IWM", "DIA"])

    # Load VXX data
    print("Loading VXX data...")
    vxx_df, thresholds = load_vxx_data()

    print(f"\nVXX Percentile Thresholds:")
    print(f"  33rd percentile: ${thresholds['p33']:,.2f}")
    print(f"  50th percentile: ${thresholds['p50']:,.2f}")
    print(f"  67th percentile: ${thresholds['p67']:,.2f}")
    print(f"  75th percentile: ${thresholds['p75']:,.2f}")
    print(f"  90th percentile: ${thresholds['p90']:,.2f}")

    # Define filters to test
    filters = [
        (None, "No Filter"),
        (thresholds['p33'], "VXX > 33rd %ile"),
        (thresholds['p50'], "VXX > 50th %ile"),
        (thresholds['p67'], "VXX > 67th %ile"),
        (thresholds['p75'], "VXX > 75th %ile"),
        (thresholds['p90'], "VXX > 90th %ile"),
    ]

    all_results = []

    # Run backtests for each ticker and filter combination
    for ticker in tickers:
        print(f"\n{'='*80}")
        print(f"TICKER: {ticker}")
        print(f"{'='*80}")

        for threshold, filter_name in filters:
            result = run_backtest_with_vxx_filter(ticker, vxx_df, threshold, filter_name)
            if result:
                all_results.append(result)

    # Create comparison summary
    if all_results:
        summary_df = pd.DataFrame([
            {
                'Ticker': r['ticker'],
                'Filter': r['filter'],
                'Trades': r['trades'],
                'Win_Rate_%': round(r['win_rate'], 1),
                'Total_PnL': round(r['total_pnl'], 2),
                'Avg_PnL': round(r['avg_pnl'], 2)
            }
            for r in all_results
        ])

        print(f"\n{'='*80}")
        print("PHASE 2 RESULTS SUMMARY")
        print(f"{'='*80}\n")

        # Group by filter and sum across tickers
        filter_summary = summary_df.groupby('Filter').agg({
            'Trades': 'sum',
            'Win_Rate_%': 'mean',
            'Total_PnL': 'sum',
            'Avg_PnL': 'mean'
        }).round(2)

        # Calculate vs baseline
        baseline = filter_summary.loc['No Filter']
        filter_summary['WR_vs_Baseline'] = filter_summary['Win_Rate_%'] - baseline['Win_Rate_%']
        filter_summary['PnL_vs_Baseline'] = filter_summary['Total_PnL'] - baseline['Total_PnL']

        # Reorder columns
        filter_summary = filter_summary[[
            'Trades', 'Win_Rate_%', 'WR_vs_Baseline',
            'Total_PnL', 'Avg_PnL', 'PnL_vs_Baseline'
        ]]

        print("COMBINED (All 4 Tickers):")
        print(filter_summary.to_string())

        print(f"\n{'='*80}")
        print("PER-TICKER BREAKDOWN")
        print(f"{'='*80}\n")

        for ticker in tickers:
            ticker_data = summary_df[summary_df['Ticker'] == ticker].set_index('Filter')
            print(f"\n{ticker}:")
            print(ticker_data.to_string())

        # Save detailed results
        output_file = "results/phase2_vxx_filter_comparison.csv"
        summary_df.to_csv(output_file, index=False)
        print(f"\n\nDetailed results saved to: {output_file}")

        # Find best filter
        best_wr = filter_summary['Win_Rate_%'].idxmax()
        best_pnl = filter_summary['Total_PnL'].idxmax()

        print(f"\n{'='*80}")
        print("RECOMMENDATION")
        print(f"{'='*80}")
        print(f"\nBest Win Rate: {best_wr}")
        print(f"  Win Rate: {filter_summary.loc[best_wr, 'Win_Rate_%']:.1f}% (+{filter_summary.loc[best_wr, 'WR_vs_Baseline']:.1f}%)")
        print(f"  Trades: {int(filter_summary.loc[best_wr, 'Trades'])}")
        print(f"  Total P/L: ${filter_summary.loc[best_wr, 'Total_PnL']:,.2f}")

        if best_pnl != best_wr:
            print(f"\nBest Total P/L: {best_pnl}")
            print(f"  Total P/L: ${filter_summary.loc[best_pnl, 'Total_PnL']:,.2f}")
            print(f"  Win Rate: {filter_summary.loc[best_pnl, 'Win_Rate_%']:.1f}%")
            print(f"  Trades: {int(filter_summary.loc[best_pnl, 'Trades'])}")

        print(f"\n{'='*80}")
        print("PHASE 2 COMPLETE!")
        print(f"{'='*80}")

        return True
    else:
        print("\nNo results generated")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
