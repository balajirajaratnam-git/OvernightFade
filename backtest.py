#!/usr/bin/env python3
"""
Convenience wrapper for running backtests.

Usage:
    python backtest.py                  # Run SHORT expiries backtest
    python backtest.py --reality        # Run with reality adjustments
    python backtest.py --weekly         # Run weekly long backtest
    python backtest.py --all-days       # Run all-days backtest
"""
import sys
import subprocess
from pathlib import Path

def main():
    if len(sys.argv) > 1:
        if '--reality' in sys.argv:
            script = 'scripts/backtesting/run_backtest_ig_short_expiries_reality.py'
        elif '--weekly' in sys.argv:
            script = 'scripts/backtesting/run_backtest_ig_weekly_long.py'
        elif '--all-days' in sys.argv:
            script = 'scripts/backtesting/run_backtest_ig_all_days.py'
        else:
            script = 'scripts/backtesting/run_backtest_ig_short_expiries.py'
    else:
        # Default: short expiries backtest
        script = 'scripts/backtesting/run_backtest_ig_short_expiries.py'

    print(f"Running: {script}")
    subprocess.run([sys.executable, script])

if __name__ == '__main__':
    main()
