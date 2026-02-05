#!/usr/bin/env python3
"""
Convenience wrapper for auto-trader.

Usage:
    python trade.py                     # Run auto-trader (SPY only, default)
    python trade.py --force-run         # Force run any day
    python trade.py --tickers SPY QQQ   # Specify tickers
"""
import sys
import subprocess

def main():
    # Forward all arguments to auto_trade_ig.py
    args = sys.argv[1:]
    script = 'scripts/trading/auto_trade_ig.py'

    print(f"Running: {script} {' '.join(args)}")
    subprocess.run([sys.executable, script] + args)

if __name__ == '__main__':
    main()
