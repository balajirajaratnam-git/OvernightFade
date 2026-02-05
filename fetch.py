#!/usr/bin/env python3
"""
Convenience wrapper for data fetching.

Usage:
    python fetch.py                     # Fetch all tickers (SPY, QQQ, IWM, DIA)
    python fetch.py --ticker SPY        # Fetch single ticker
    python fetch.py --verify            # Verify data completeness
"""
import sys
import subprocess

def main():
    if '--verify' in sys.argv:
        script = 'scripts/data/verify_multi_ticker_data.py'
    elif '--ticker' in sys.argv:
        script = 'scripts/data/fetch_one_ticker.py'
    else:
        script = 'scripts/data/fetch_multi_ticker_data.py'

    # Forward all arguments
    args = [arg for arg in sys.argv[1:] if arg != '--verify']

    print(f"Running: {script} {' '.join(args)}")
    subprocess.run([sys.executable, script] + args)

if __name__ == '__main__':
    main()
