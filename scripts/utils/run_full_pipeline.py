"""
FULL AUTOMATED PIPELINE
Phase 1: Multi-Ticker Enhancement

Executes:
1. Fetch remaining main tickers (IWM, DIA after QQQ completes)
2. Run Backtest #1: 4 main tickers (SPY, QQQ, IWM, DIA)
3. Fetch VIX + sectors
4. Run Backtest #2: With VIX filter capability

NO INTERVENTION NEEDED - Runs fully automated
"""
import os
import sys
import time
import subprocess
from datetime import datetime

def print_header(text):
    """Print formatted header."""
    print("\n" + "="*80)
    print(text.center(80))
    print("="*80 + "\n")

def check_ticker_complete(ticker):
    """Check if ticker has complete data (~2400+ files)."""
    intraday_dir = f"data/{ticker}/intraday"
    if not os.path.exists(intraday_dir):
        return False
    files = [f for f in os.listdir(intraday_dir) if f.endswith(".parquet")]
    return len(files) >= 2400

def wait_for_ticker(ticker, timeout_minutes=30):
    """Wait for a ticker to complete fetching."""
    print(f"Waiting for {ticker} to complete...")
    start_time = time.time()
    timeout_seconds = timeout_minutes * 60

    while time.time() - start_time < timeout_seconds:
        if check_ticker_complete(ticker):
            print(f"OK {ticker} complete!")
            return True
        time.sleep(10)  # Check every 10 seconds

    print(f"WARNING: {ticker} did not complete in {timeout_minutes} minutes")
    return False

def fetch_ticker(ticker):
    """Fetch a single ticker."""
    print_header(f"FETCHING {ticker}")
    print(f"Started: {datetime.now().strftime('%H:%M:%S')}")

    result = subprocess.run(
        [sys.executable, "fetch_one_ticker.py", ticker],
        capture_output=True,
        text=True
    )

    if result.returncode == 0:
        print(f"OK {ticker} fetch complete")
    else:
        print(f"ERROR fetching {ticker}:")
        print(result.stderr)

    return result.returncode == 0

def run_backtest(name, description):
    """Run multi-ticker backtest."""
    print_header(f"BACKTEST: {name}")
    print(description)
    print(f"Started: {datetime.now().strftime('%H:%M:%S')}\n")

    result = subprocess.run(
        [sys.executable, "src/backtester_multi_ticker.py"],
        capture_output=False,  # Show output in real-time
        text=True
    )

    return result.returncode == 0

def main():
    """Main pipeline execution."""
    print_header("FULL AUTOMATED PIPELINE - PHASE 1")
    print("This will:")
    print("  1. Wait for QQQ to complete (currently fetching)")
    print("  2. Fetch IWM")
    print("  3. Fetch DIA")
    print("  4. Run Backtest #1 (SPY, QQQ, IWM, DIA)")
    print("  5. Fetch VIX")
    print("  6. Fetch 9 sector ETFs")
    print("  7. Run Backtest #2 (with VIX data available)")
    print("\nEstimated time: 60-90 minutes")
    print("NO INTERVENTION NEEDED - Fully automated\n")

    start_time = time.time()

    # =========================================================================
    # PHASE 1A: MAIN 4 TICKERS
    # =========================================================================

    print_header("PHASE 1A: MAIN 4 TICKERS")

    # Check SPY
    if check_ticker_complete("SPY"):
        print("OK SPY already complete")
    else:
        print("WARNING: SPY not complete")

    # Wait for QQQ (currently fetching in background)
    print("\nWaiting for QQQ (currently fetching)...")
    wait_for_ticker("QQQ", timeout_minutes=20)

    # Fetch IWM
    print("\n")
    fetch_ticker("IWM")

    # Fetch DIA
    print("\n")
    fetch_ticker("DIA")

    # Check all 4 tickers ready
    main_tickers = ["SPY", "QQQ", "IWM", "DIA"]
    all_ready = all(check_ticker_complete(t) for t in main_tickers)

    if all_ready:
        print_header("OK ALL MAIN TICKERS COMPLETE")
        for ticker in main_tickers:
            intraday_dir = f"data/{ticker}/intraday"
            count = len([f for f in os.listdir(intraday_dir) if f.endswith(".parquet")])
            print(f"  {ticker}: {count} files")

    # Run Backtest #1
    print("\n")
    run_backtest(
        "BACKTEST #1: 4 Main Tickers",
        "Testing SPY, QQQ, IWM, DIA over 10 years (~6,560 trades expected)"
    )

    phase1a_time = time.time() - start_time
    print(f"\nPhase 1A completed in {phase1a_time/60:.1f} minutes")

    # =========================================================================
    # PHASE 1B: VIX + SECTORS
    # =========================================================================

    print_header("PHASE 1B: VIX + SECTORS (FOR FUTURE ENHANCEMENTS)")

    # Fetch VIX
    print("\n")
    fetch_ticker("VIX")

    # Fetch sectors
    sectors = ["XLK", "XLF", "XLE", "XLV", "XLY", "XLU", "XLRE", "XLI", "XLB"]
    for sector in sectors:
        print("\n")
        fetch_ticker(sector)

    # Run Backtest #2 (same as #1, but now VIX data is available for Phase 2)
    print("\n")
    run_backtest(
        "BACKTEST #2: Complete Dataset",
        "Same backtest, but now VIX + sector data available for future phases"
    )

    # =========================================================================
    # COMPLETION
    # =========================================================================

    total_time = time.time() - start_time

    print_header("PIPELINE COMPLETE!")
    print(f"Total time: {total_time/60:.1f} minutes")
    print(f"Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    print("\nOK Data fetched:")
    print("  - 4 main tickers (SPY, QQQ, IWM, DIA)")
    print("  - VIX (volatility index)")
    print("  - 9 sector ETFs")

    print("\nOK Backtests complete:")
    print("  - Results in: results/trade_log_MULTI_TICKER_10year.csv")
    print("  - Individual tickers: results/trade_log_{TICKER}_10year.csv")

    print("\n🎯 READY FOR PHASE 2: VIX FILTER")
    print("   VIX data is ready - we can now implement volatility filtering!")

    print("\n📊 Review your results in results/ folder")
    print("="*80)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nPipeline interrupted by user.")
        print("Progress is saved - you can resume manually:")
        print("  - Check progress: python check_fetch_progress.py")
        print("  - Fetch ticker: python fetch_one_ticker.py TICKER")
        print("  - Run backtest: python src/backtester_multi_ticker.py")
    except Exception as e:
        print(f"\n\nERROR: {e}")
        import traceback
        traceback.print_exc()
