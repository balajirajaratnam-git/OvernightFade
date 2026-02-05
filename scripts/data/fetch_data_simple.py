"""
Simple Multi-Ticker Data Fetcher (No fancy output, just works)
"""
import os
import sys

# Set environment variable
os.environ['ALLOW_NETWORK'] = '1'

# Add src to path
sys.path.insert(0, 'src')

from data_manager import DataManager
import json

def load_config():
    with open("config/config.json", "r") as f:
        return json.load(f)

def fetch_ticker(ticker, dm):
    """Fetch data for one ticker."""
    print(f"\n{'='*80}")
    print(f"Fetching {ticker}")
    print(f"{'='*80}")

    # Override ticker
    original_ticker = dm.ticker
    original_ticker_dir = dm.ticker_dir
    original_intraday_dir = dm.intraday_dir

    dm.ticker = ticker
    dm.ticker_dir = os.path.join(dm.base_dir, ticker)
    dm.intraday_dir = os.path.join(dm.ticker_dir, "intraday")
    os.makedirs(dm.intraday_dir, exist_ok=True)

    try:
        print(f"Fetching {ticker} daily data...")
        dm.update_daily_data()
        print(f"Fetching {ticker} minute data...")
        dm.update_intraday_data()
        print(f"OK {ticker} complete")
    finally:
        dm.ticker = original_ticker
        dm.ticker_dir = original_ticker_dir
        dm.intraday_dir = original_intraday_dir

def main():
    print("="*80)
    print("MULTI-TICKER DATA FETCH - 10 YEARS")
    print("="*80)

    config = load_config()
    tickers = config.get("tickers", ["SPY"])
    vix = config.get("vix_ticker", "VIX")
    sectors = config.get("sector_etfs", [])

    all_tickers = tickers + [vix] + sectors
    print(f"\nFetching {len(all_tickers)} tickers: {', '.join(all_tickers)}")
    print(f"This will take 30-45 minutes with unlimited API calls.\n")

    dm = DataManager(require_network=True)

    for idx, ticker in enumerate(all_tickers, 1):
        print(f"\nProgress: {idx}/{len(all_tickers)}")
        try:
            fetch_ticker(ticker, dm)
        except Exception as e:
            print(f"ERROR fetching {ticker}: {e}")
            continue

    print("\n" + "="*80)
    print("DATA FETCH COMPLETE!")
    print("="*80)
    print(f"\nNext step: python verify_multi_ticker_data.py")

if __name__ == "__main__":
    main()
