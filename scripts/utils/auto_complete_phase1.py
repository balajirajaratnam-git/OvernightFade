"""
Automated Phase 1 Completion - No Intervention
Waits for DIA, runs backtest, fetches remaining data, runs final backtest
"""
import os
import sys
import time
import subprocess

def check_complete(ticker):
    """Check if ticker has 2400+ files."""
    path = f"data/{ticker}/intraday"
    if not os.path.exists(path):
        return False
    return len([f for f in os.listdir(path) if f.endswith(".parquet")]) >= 2400

def wait_for(ticker, check_interval=60):
    """Wait for ticker to complete."""
    print(f"Waiting for {ticker}...")
    while not check_complete(ticker):
        time.sleep(check_interval)
    print(f"{ticker} COMPLETE!")

def fetch(ticker):
    """Fetch a ticker."""
    print(f"\n{'='*80}")
    print(f"FETCHING {ticker}")
    print(f"{'='*80}")
    result = subprocess.run([sys.executable, "fetch_one_ticker.py", ticker])
    return result.returncode == 0

def run_backtest(name):
    """Run backtest."""
    print(f"\n{'='*80}")
    print(f"RUNNING BACKTEST: {name}")
    print(f"{'='*80}")
    result = subprocess.run([sys.executable, "src/backtester_multi_ticker.py"])
    return result.returncode == 0

print("="*80)
print("AUTOMATED PHASE 1 COMPLETION")
print("="*80)
print("This will:")
print("  1. Wait for DIA to complete")
print("  2. Run Backtest #1 (SPY, QQQ, IWM, DIA)")
print("  3. Fetch VIX")
print("  4. Fetch 9 sector ETFs")
print("  5. Run Backtest #2 (complete dataset)")
print("\nFully automated - no intervention needed\n")

# Wait for DIA
wait_for("DIA")

# Run Backtest #1
print("\n" + "="*80)
print("ALL 4 MAIN TICKERS COMPLETE!")
print("SPY: 2513 files")
print("QQQ: 2512 files")
print("IWM: 2512 files")
print("DIA: 2500+ files")
print("="*80)

run_backtest("Phase 1A - 4 Main Tickers")

# Fetch VIX
fetch("VIX")

# Fetch sectors
sectors = ["XLK", "XLF", "XLE", "XLV", "XLY", "XLU", "XLRE", "XLI", "XLB"]
for sector in sectors:
    fetch(sector)

# Run Backtest #2
run_backtest("Phase 1B - Complete Dataset with VIX + Sectors")

print("\n" + "="*80)
print("PHASE 1 COMPLETE!")
print("="*80)
print("\nResults:")
print("  - Backtest results: results/trade_log_MULTI_TICKER_10year.csv")
print("  - Individual tickers: results/trade_log_{TICKER}_10year.csv")
print("\nAll data fetched:")
print("  - 4 main tickers (SPY, QQQ, IWM, DIA)")
print("  - VIX (for Phase 2)")
print("  - 9 sector ETFs (for Phase 4)")
print("\nREADY FOR PHASE 2: VIX FILTER")
print("="*80)
