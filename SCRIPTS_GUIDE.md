# OvernightFade Scripts Guide

**Last Updated**: 2026-02-05
**Version**: 5.0 (Post-Cleanup)

**This guide tells you exactly which scripts to use and when.**

---

## 🎯 PRIMARY SCRIPTS (Use These)

These are the actively maintained scripts you should use for daily trading and analysis.

### **Daily Trading**

#### **auto_trade_ig.py** ⭐ MAIN SCRIPT
```bash
python scripts/trading/auto_trade_ig.py
```

**Use when**: Every trading day at 16:00 ET
**What it does**:
- Generates trading signals for SPY
- Shows order details for IG.com (US 500) and IBKR (SPY)
- Displays reality-adjusted P&L expectations
- Warns if trading non-recommended tickers

**Output**: Order details, entry price, strike price, expected P&L

---

### **Monthly Backtesting**

#### **run_backtest_ig_short_expiries_reality.py** ⭐ MAIN BACKTEST
```bash
python scripts/backtesting/run_backtest_ig_short_expiries_reality.py
```

**Use when**: Monthly (or when verifying strategy performance)
**What it does**:
- Runs 10-year backtest with SHORT expiries (1-3 days)
- Applies reality adjustments (spreads, slippage, theta, commission)
- Shows SPY-only performance: 34.3% CAGR
- Compares backtest vs reality

**Output**:
- Results file: `results/ig_short_expiries_reality_backtest.csv`
- Performance metrics, win rate, CAGR

---

### **Data Management**

#### **fetch_multi_ticker_data.py**
```bash
python scripts/data/fetch_multi_ticker_data.py
```

**Use when**: Initial setup or quarterly data updates
**What it does**: Fetches 10 years of historical data for all tickers
**Time**: 2-4 hours (rate-limited API)

#### **verify_multi_ticker_data.py**
```bash
python scripts/data/verify_multi_ticker_data.py
```

**Use when**: After data fetch or to check data completeness
**What it does**: Verifies all tickers have complete data
**Output**: Status report for each ticker

---

### **Paper Trading Analysis**

#### **measure_reality_framework.py**
```bash
python scripts/analysis/measure_reality_framework.py
```

**Use when**: Calculating option prices for reality adjustments
**What it does**: Black-Scholes option pricing calculator

#### **paper_trading_log.py**
```bash
python scripts/analysis/paper_trading_log.py
```

**Use when**: Logging paper trade results
**What it does**: Compare actual vs predicted performance

---

## 🔬 RESEARCH SCRIPTS (Occasional Use)

These scripts are for parameter optimization and strategy validation. Use sparingly.

### **Parameter Optimization**

#### **parameter_optimizer.py**
```bash
python scripts/analysis/parameter_optimizer.py
```

**Use when**: Quarterly or when optimizing ATR targets
**What it does**: Grid search for optimal ATR multiplier
**Time**: 30-60 minutes
**Output**: `results/optimization_results.csv`

---

### **Strategy Validation**

#### **walk_forward_validation.py**
```bash
python scripts/analysis/walk_forward_validation.py
```

**Use when**: Validating strategy on out-of-sample data
**What it does**: Rolling train/test splits with 19 folds
**Time**: 10-20 minutes
**Output**: Walk-forward performance metrics

#### **validation_holdout.py**
```bash
python scripts/analysis/validation_holdout.py
```

**Use when**: Final one-time validation on holdout data
**What it does**: Tests strategy on reserved 3-month period
**Time**: < 1 minute
**Output**: Holdout performance

#### **strategy_comparison.py**
```bash
python scripts/analysis/strategy_comparison.py
```

**Use when**: Comparing different strategy variants
**What it does**: Tests baseline vs filters (LastHourVeto, etc.)
**Time**: 10-20 minutes
**Output**: Strategy comparison table

---

## 📦 ARCHIVED SCRIPTS (Reference Only)

These scripts are kept for historical reference but are NOT recommended for use. They've been superseded by better implementations.

**Location**: `src/archive/`

### **backtester_old_exit_comparison.py** (was: backtester.py)
- **Why archived**: Compares old exit strategies (09:35 ET vs expiry)
- **Superseded by**: `run_backtest_ig_short_expiries_reality.py`
- **Use**: Historical reference only

### **dashboard_legacy.py** (was: dashboard.py)
- **Why archived**: Old dashboard without reality adjustments
- **Superseded by**: `scripts/trading/auto_trade_ig.py`
- **Use**: Historical reference only

### **backtester_multi_ticker.py**
- **Why archived**: Multi-ticker comparison (now we know SPY-only is best)
- **Superseded by**: Current backtests focus on SPY
- **Use**: Educational/comparison purposes only

---

## 📋 QUICK REFERENCE

### **What to Run and When**

| Frequency | Script | Purpose |
|-----------|--------|---------|
| **Daily** | `auto_trade_ig.py` | Get trading signals |
| **Monthly** | `run_backtest_ig_short_expiries_reality.py` | Verify strategy performance |
| **Quarterly** | `fetch_multi_ticker_data.py` | Update market data |
| **As needed** | `verify_multi_ticker_data.py` | Check data completeness |
| **Rarely** | `parameter_optimizer.py` | Optimize ATR targets |
| **Rarely** | `walk_forward_validation.py` | Validate strategy |

---

## 🗂️ FOLDER STRUCTURE

```
OvernightFade/
│
├── scripts/
│   ├── trading/
│   │   └── auto_trade_ig.py              ⭐ PRIMARY - Daily signals
│   │
│   ├── backtesting/
│   │   └── run_backtest_ig_short_expiries_reality.py  ⭐ PRIMARY - Monthly backtest
│   │
│   ├── data/
│   │   ├── fetch_multi_ticker_data.py    ⭐ PRIMARY - Data fetching
│   │   └── verify_multi_ticker_data.py   ⭐ PRIMARY - Data verification
│   │
│   └── analysis/
│       ├── measure_reality_framework.py  ⭐ Paper trading analysis
│       ├── paper_trading_log.py          ⭐ Paper trading logging
│       ├── parameter_optimizer.py        🔬 Research - Optimization
│       ├── walk_forward_validation.py    🔬 Research - Validation
│       ├── validation_holdout.py         🔬 Research - Holdout test
│       └── strategy_comparison.py        🔬 Research - Compare strategies
│
└── src/
    ├── data_manager.py                   📚 Library - Data fetching
    ├── rate_limiter.py                   📚 Library - API rate limiting
    ├── session_utils.py                  📚 Library - Timezone utilities
    ├── strategies.py                     📚 Library - Strategy filters
    │
    └── archive/                          📦 ARCHIVED - Reference only
        ├── backtester_old_exit_comparison.py
        ├── dashboard_legacy.py
        └── backtester_multi_ticker.py
```

---

## 💡 Common Questions

### **Q: Which script should I run daily?**
A: `python scripts/trading/auto_trade_ig.py`

### **Q: How do I backtest the current strategy?**
A: `python scripts/backtesting/run_backtest_ig_short_expiries_reality.py`

### **Q: Do I need to run the archived scripts?**
A: No. They're kept for reference only.

### **Q: What about src/backtester.py?**
A: It's been moved to `src/archive/backtester_old_exit_comparison.py`. Don't use it.

### **Q: When should I run parameter_optimizer.py?**
A: Quarterly, or when you want to optimize ATR targets. Not needed for daily trading.

### **Q: How often should I update data?**
A: Quarterly is fine. The strategy uses 10 years of data, so daily updates aren't critical.

---

## ⚠️ Important Notes

**Scripts you should NEVER run randomly:**
- ❌ Anything in `src/archive/` - Outdated
- ❌ `parameter_optimizer.py` - Unless you know why
- ❌ `strategy_comparison.py` - Research tool only

**Scripts you should run regularly:**
- ✅ `auto_trade_ig.py` - Daily at 16:00 ET
- ✅ `run_backtest_ig_short_expiries_reality.py` - Monthly verification

---

## 🎯 Recommended Workflow

### **Daily Trading**
```bash
# 1. Get today's signal (15 minutes before close)
python scripts/trading/auto_trade_ig.py

# 2. Place trade on IG.com or IBKR at 16:00 ET

# 3. Log results next day (optional, for calibration)
python scripts/analysis/paper_trading_log.py
```

### **Monthly Review**
```bash
# Verify strategy performance
python scripts/backtesting/run_backtest_ig_short_expiries_reality.py

# Check if results match expectations (34.3% CAGR)
```

### **Quarterly Maintenance**
```bash
# Update market data
python scripts/data/fetch_multi_ticker_data.py

# Verify completeness
python scripts/data/verify_multi_ticker_data.py

# Optional: Re-optimize parameters
python scripts/analysis/parameter_optimizer.py
```

---

**Remember: Focus on the PRIMARY scripts. Everything else is optional or archived.**

**For daily trading, you only need ONE script: `auto_trade_ig.py`** ✅
