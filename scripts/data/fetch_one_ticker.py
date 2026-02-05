"""
Fetch data for ONE ticker at a time (avoids state file conflicts)
Usage: python fetch_one_ticker.py QQQ
"""
import os
import sys

if len(sys.argv) < 2:
    print("Usage: python fetch_one_ticker.py TICKER")
    print("Example: python fetch_one_ticker.py QQQ")
    sys.exit(1)

ticker = sys.argv[1].upper()

# Set environment variable
os.environ['ALLOW_NETWORK'] = '1'

# Add src to path
sys.path.insert(0, 'src')

from data_manager import DataManager

print(f"="*80)
print(f"Fetching {ticker} (10 years)")
print(f"="*80)

# Initialize
dm = DataManager(require_network=True)

# Override ticker
dm.ticker = ticker
dm.ticker_dir = os.path.join(dm.base_dir, ticker)
dm.intraday_dir = os.path.join(dm.ticker_dir, "intraday")
os.makedirs(dm.intraday_dir, exist_ok=True)

# Fetch
try:
    print(f"\nStep 1/2: Fetching {ticker} daily data...")
    dm.update_daily_data()

    print(f"\nStep 2/2: Fetching {ticker} minute data...")
    dm.update_intraday_data()

    print(f"\n{'='*80}")
    print(f"{ticker} COMPLETE!")
    print(f"{'='*80}")
except Exception as e:
    print(f"\nERROR: {e}")
    import traceback
    traceback.print_exc()
