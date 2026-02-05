"""
Check Data Fetch Progress

Run this anytime to see:
- Which tickers are complete
- How many files fetched
- Estimated completion
"""
import os
from datetime import datetime

def count_files(ticker):
    """Count files for a ticker."""
    daily_path = f"data/{ticker}/daily_OHLCV.parquet"
    intraday_dir = f"data/{ticker}/intraday"

    daily_exists = os.path.exists(daily_path)

    intraday_count = 0
    if os.path.exists(intraday_dir):
        intraday_count = len([f for f in os.listdir(intraday_dir) if f.endswith(".parquet")])

    return daily_exists, intraday_count

def main():
    print("="*80)
    print("DATA FETCH PROGRESS CHECK")
    print("="*80)
    print(f"Time: {datetime.now().strftime('%H:%M:%S')}\n")

    # Tickers being fetched
    main_tickers = ["SPY", "QQQ", "IWM", "DIA"]
    vix = ["VIX"]
    sectors = ["XLK", "XLF", "XLE", "XLV", "XLY", "XLU", "XLRE", "XLI", "XLB"]

    all_tickers = main_tickers + vix + sectors

    print(f"{'Ticker':<10} {'Daily':<10} {'Intraday Files':<20} {'Status'}")
    print("-"*80)

    complete = 0
    in_progress = 0
    pending = 0

    for ticker in all_tickers:
        daily, intraday = count_files(ticker)

        # Status determination
        if intraday >= 2400:  # ~10 years
            status = "COMPLETE"
            complete += 1
        elif intraday > 0:
            status = f"IN PROGRESS ({intraday/2500*100:.0f}%)"
            in_progress += 1
        else:
            status = "PENDING"
            pending += 1

        daily_str = "YES" if daily else "NO"
        print(f"{ticker:<10} {daily_str:<10} {intraday:<20} {status}")

    print("="*80)
    print(f"\nSummary:")
    print(f"  Complete:     {complete}/14")
    print(f"  In Progress:  {in_progress}/14")
    print(f"  Pending:      {pending}/14")

    if complete == 14:
        print(f"\n*** ALL DATA FETCHED! Ready to backtest! ***")
        print(f"\nNext step: python verify_multi_ticker_data.py")
    elif in_progress > 0:
        pct = (complete / 14) * 100
        print(f"\nFetch in progress... {pct:.0f}% complete")
        print(f"Estimated time remaining: {(14-complete)*3} minutes")
    else:
        print(f"\nFetch not started yet.")
        print(f"Run: python fetch_data_simple.py")

if __name__ == "__main__":
    main()
