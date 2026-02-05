# Scripts Directory

**Last Updated**: 2026-02-05
**Version**: 5.0

Organized executable scripts for trading, backtesting, data management, and analysis.

---

## Directory Structure

```
scripts/
├── trading/          # Live/paper trading
├── backtesting/      # Strategy backtests
├── data/             # Data fetching & verification
├── analysis/         # Performance analysis
└── utils/            # Utility scripts
```

---

## Trading Scripts (`trading/`)

### **auto_trade_ig.py** ⭐ PRIMARY
Daily auto-trader with SHORT expiries strategy.

**Usage**:
```bash
# Run daily at 16:00 ET
python scripts/trading/auto_trade_ig.py

# Force run any day (testing)
python scripts/trading/auto_trade_ig.py --force-run
```

**Outputs**:
- Order details for IG.com (US 500) and IBKR (SPY)
- Reality-adjusted P/L expectations
- SPY-only signals (warns if other tickers configured)

### **dashboard_pro.py**
Legacy multi-ticker dashboard (use auto_trade_ig.py instead).

---

## Backtesting Scripts (`backtesting/`)

### **run_backtest_ig_short_expiries_reality.py** ⭐ PRIMARY
Reality-adjusted backtest with SHORT expiries (1-3 days).

**Usage**:
```bash
python scripts/backtesting/run_backtest_ig_short_expiries_reality.py
```

**Shows**:
- SPY only: 34.3% CAGR (reality-adjusted)
- Backtest vs reality comparison
- Why SPY only is recommended

### **run_backtest_ig_short_expiries.py**
Idealized backtest (no reality adjustments).

**Result**: 48.8% CAGR (idealized)

### **run_backtest_ig_weekly_long.py**
Weekly long expiries (6-7 days).

### **run_backtest_ig_all_days.py**
All days mixed expiries backtest.

### **run_backtest_ig_weekly_expiries.py**
Tuesday/Thursday/Friday weekly expiries.

### **run_backtest_ig_timing.py**
Different entry timing analysis.

### **run_backtest_simple.py**
Simple single-ticker backtest.

### **run_phase2_vxx_filter.py**
VIX filter backtest.

### **run_phase3_position_sizing.py**
Position sizing optimization.

### **phase3_option_c_complete_analysis.py**
Complete phase 3 analysis.

---

## Data Scripts (`data/`)

### **fetch_multi_ticker_data.py**
Fetch historical data for all tickers (10 years).

**Usage**:
```bash
python scripts/data/fetch_multi_ticker_data.py
```

### **verify_multi_ticker_data.py**
Verify data completeness and ranges.

**Usage**:
```bash
python scripts/data/verify_multi_ticker_data.py
```

### **fetch_one_ticker.py**
Fetch single ticker data.

**Usage**:
```bash
python scripts/data/fetch_one_ticker.py SPY
```

### **fetch_data_simple.py**
Simple data fetch script.

### **check_fetch_progress.py**
Check ongoing fetch progress.

### **run_data_fetch.py**
Run full data fetch pipeline.

---

## Analysis Scripts (`analysis/`)

### **measure_reality_framework.py**
Black-Scholes option pricing calculator for reality adjustments.

**Usage**:
```bash
python scripts/analysis/measure_reality_framework.py
```

### **paper_trading_log.py**
Paper trading logging framework.

### **analyze_kelly_equity.py**
Kelly sizing and equity curve analysis.

### **monthly_pnl_table.py**
Monthly P&L breakdown table.

### **stress_test_scenarios.py**
Strategy stress testing.

### **withdrawal_analysis.py**
Withdrawal strategy analysis.

### **analyze_losses.py**
Losing trade analysis.

---

## Utility Scripts (`utils/`)

### **project_setup.py**
Initial project setup.

### **auto_complete_phase1.py**
Auto-complete phase 1 tasks.

### **run_full_pipeline.py**
Run complete analysis pipeline.

---

## Running Scripts

### **From Project Root** (Recommended)
```bash
python scripts/trading/auto_trade_ig.py
python scripts/backtesting/run_backtest_ig_short_expiries_reality.py
python scripts/data/fetch_multi_ticker_data.py
```

### **Important Notes**
- All scripts use `sys.path.insert(0, 'src')` to import modules
- Must run from project root directory
- Scripts will fail if run from within scripts/ subdirectory

---

## Common Tasks

**Daily Trading**:
```bash
python scripts/trading/auto_trade_ig.py
```

**Run Backtest**:
```bash
python scripts/backtesting/run_backtest_ig_short_expiries_reality.py
```

**Fetch Data**:
```bash
python scripts/data/fetch_multi_ticker_data.py
```

**Verify Data**:
```bash
python scripts/data/verify_multi_ticker_data.py
```

---

## Related Documentation

- Main `README.md` - Project overview and quick start
- `src/README.md` - Core modules documentation
- `docs/guides/DAILY_PAPER_TRADING_CHECKLIST.md` - Daily workflow

---

**Note**: All scripts tested and working. See main README for detailed usage examples.
