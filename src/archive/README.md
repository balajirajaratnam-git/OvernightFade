# Archive - Legacy Scripts

**These scripts are archived for historical reference only.**

---

## Why These Scripts Are Archived

These scripts represent earlier versions of the strategy or functionality that has been superseded by better implementations. They are kept for:
- Historical reference
- Understanding strategy evolution
- Comparing old vs new approaches

**DO NOT USE these scripts for daily trading or analysis.**

---

## Archived Scripts

### **backtester_old_exit_comparison.py** (was: `backtester.py`)

**Original Purpose**: Compare two exit strategies:
1. Original: Hold until expiry (16:00 ET)
2. 09:35 ET Exit: Manual close at market open

**Why Archived**:
- Experiment concluded: Holding until expiry is better
- Does NOT use SHORT expiries (current strategy)
- Does NOT apply reality adjustments
- Name was misleading (sounded generic)

**Superseded By**: `scripts/backtesting/run_backtest_ig_short_expiries_reality.py`

---

### **dashboard_legacy.py** (was: `dashboard.py`)

**Original Purpose**: Interactive trading dashboard for signal generation

**Why Archived**:
- Pre-dates SHORT expiries strategy
- Does NOT include reality adjustments
- Does NOT focus on SPY-only
- Functionality absorbed into auto_trade_ig.py

**Superseded By**: `scripts/trading/auto_trade_ig.py`

---

### **backtester_multi_ticker.py**

**Original Purpose**: Backtest multiple tickers simultaneously for comparison

**Why Archived**:
- Analysis showed SPY-only is optimal (34.3% CAGR)
- QQQ, IWM, DIA have negative expected value
- Multi-ticker is no longer recommended strategy

**Current Use**: Educational/reference only - shows why SPY-only is best

**Superseded By**: SPY-only focus in all current backtests

---

## If You Need These Scripts

If you need to reference these for historical analysis:

```bash
# Run old exit comparison (NOT RECOMMENDED)
python src/archive/backtester_old_exit_comparison.py

# Run legacy dashboard (NOT RECOMMENDED)
python src/archive/dashboard_legacy.py

# Run multi-ticker comparison (EDUCATIONAL ONLY)
python src/archive/backtester_multi_ticker.py
```

**Warning**: These scripts may not reflect current strategy parameters or reality adjustments.

---

## Migration Notes

**If you were using `backtester.py`**:
- Switch to: `python scripts/backtesting/run_backtest_ig_short_expiries_reality.py`
- This includes SHORT expiries and reality adjustments

**If you were using `dashboard.py`**:
- Switch to: `python scripts/trading/auto_trade_ig.py`
- This is actively maintained with current strategy

**If you were using `backtester_multi_ticker.py`**:
- Note: We now recommend SPY-only trading
- For comparison: Script still available here
- For current backtest: Use reality-adjusted SHORT expiries backtest

---

**See SCRIPTS_GUIDE.md in project root for current scripts and usage.**
