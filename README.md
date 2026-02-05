# OvernightFade Trading Strategy - SHORT EXPIRIES

**Automated short-term options fade strategy with reality-adjusted backtesting**

## 🎯 Overview

This system implements a short-term mean-reversion options strategy:
- **Signal**: Fade previous day's direction (RED day → BUY CALL, GREEN day → BUY PUT)
- **Entry**: Market close (16:00 ET exactly)
- **Expiry**: 1-3 days (Mon→Wed, Tue→Wed, Wed→Fri, Thu→Fri, Fri→Mon)
- **Ticker**: **SPY ONLY** (best expected value: +8.7% per trade)
- **Position Sizing**: Kelly 5.23%, capped at $1,000
- **Backtest Period**: 10 years (2016-2026)
- **Strategy**: SHORT expiries to minimize theta decay

## 📊 Performance (Reality-Adjusted Backtest)

| Metric | Backtest (Idealized) | Reality (Adjusted) | SPY Only |
|--------|----------------------|--------------------|----------|
| **CAGR** | 64.8% | -99.8% (all tickers) | **34.3%** ✅ |
| Starting Capital | $10,000 | $10,000 | $10,000 |
| Final Equity (10y) | $1,439,425 | $0 | ~$470,000 |
| Win Rate | 80.9% | 80.9% | 86.3% |
| Total Trades | 8,728 | 8,728 | 2,097 |

**Key Finding**: Trading all 4 tickers (SPY, QQQ, IWM, DIA) results in account blowout due to wide bid/ask spreads on IWM (10%) and DIA (15%). **Trade SPY only for profitable results.**

---

## 🚀 Quick Start

### **1. Auto-Trader (Recommended for Daily Use)**

```bash
# Run daily at 16:00 ET
python trade.py

# Force run any day (for testing)
python trade.py --force-run

# Add other tickers (not recommended, shows warnings)
python trade.py --tickers SPY QQQ
```

**Outputs**: Order details for **BOTH** IG.com and IBKR platforms

### **2. Run Backtest**

```bash
# Short expiries with reality adjustments (recommended)
python backtest.py --reality

# Short expiries (idealized)
python backtest.py

# Weekly long expiries
python backtest.py --weekly
```

### **3. Fetch Data**

```bash
# Download/update all ticker data (10 years)
python fetch.py

# Verify data completeness
python fetch.py --verify

# Fetch single ticker
python fetch.py --ticker SPY
```

### **4. Paper Trading**

```bash
# Run analysis tools
python scripts/analysis/measure_reality_framework.py
python scripts/analysis/paper_trading_log.py
```

See `docs/guides/DAILY_PAPER_TRADING_CHECKLIST.md` for complete workflow.

---

## 📁 Project Structure

```
OvernightFade/
│
├── README.md                    # This file
├── requirements.txt             # Python dependencies
├── LICENSE                      # License file
│
├── trade.py                     # Wrapper: Auto-trader
├── backtest.py                  # Wrapper: Run backtests
├── fetch.py                     # Wrapper: Fetch data
│
├── config/                      # Configuration files
│   ├── config.json              # Strategy parameters
│   └── reality_adjustments.json # Reality adjustment factors
│
├── src/                         # Core library code
│   ├── __init__.py
│   ├── backtester.py            # Backtesting engine
│   ├── data_manager.py          # Polygon.io data fetching
│   ├── indicators.py            # Technical indicators
│   ├── strategies.py            # Strategy logic
│   └── dashboard.py             # Dashboard utilities
│
├── scripts/                     # Executable scripts
│   ├── trading/                 # Trading scripts
│   │   ├── auto_trade_ig.py    # Main auto-trader
│   │   └── dashboard_pro.py    # Dashboard
│   │
│   ├── backtesting/             # Backtest scripts
│   │   ├── run_backtest_ig_short_expiries_reality.py  # Reality-adjusted
│   │   ├── run_backtest_ig_short_expiries.py          # Idealized
│   │   └── ...
│   │
│   ├── data/                    # Data fetching scripts
│   │   ├── fetch_multi_ticker_data.py
│   │   ├── verify_multi_ticker_data.py
│   │   └── ...
│   │
│   ├── analysis/                # Analysis tools
│   │   ├── measure_reality_framework.py    # Black-Scholes calculator
│   │   ├── paper_trading_log.py            # Paper trading logger
│   │   └── ...
│   │
│   └── utils/                   # Utility scripts
│       └── ...
│
├── docs/                        # Documentation
│   ├── guides/                  # User guides
│   │   ├── DAILY_PAPER_TRADING_CHECKLIST.md
│   │   ├── REALITY_CALIBRATION_GUIDE.md
│   │   └── DASHBOARD_GUIDE.md
│   │
│   ├── backtests/               # Backtest documentation
│   │   ├── BACKTEST_REALITY_RESULTS_SUMMARY.md
│   │   ├── AUTO_TRADER_REALITY_ADJUSTMENTS.md
│   │   └── CRITICAL_ISSUES.md
│   │
│   └── archive/                 # Old documentation
│       └── ...
│
├── data/                        # Market data (gitignored)
│   ├── SPY/
│   ├── QQQ/
│   ├── IWM/
│   └── DIA/
│
├── logs/                        # Log files (gitignored)
│   ├── paper_trades.csv
│   ├── backtest_predictions.csv
│   └── ...
│
├── results/                     # Backtest outputs (gitignored)
│   └── ...
│
├── tests/                       # Unit tests
│   └── __init__.py
│
└── notebooks/                   # Jupyter notebooks (optional)
```

---

## 📖 Key Files Explained

### **Convenience Wrappers (Root Directory)**

| File | Purpose | Usage |
|------|---------|-------|
| `trade.py` | Run auto-trader | `python trade.py` |
| `backtest.py` | Run backtests | `python backtest.py --reality` |
| `fetch.py` | Fetch data | `python fetch.py` |

### **Trading Scripts**

| File | Purpose |
|------|---------|
| `scripts/trading/auto_trade_ig.py` | Main auto-trader (Phase 1 dry-run) |
| `scripts/trading/dashboard_pro.py` | Dashboard (legacy, multi-ticker) |

### **Backtest Scripts**

| File | Purpose |
|------|---------|
| `scripts/backtesting/run_backtest_ig_short_expiries_reality.py` | **MAIN** backtest with reality adjustments |
| `scripts/backtesting/run_backtest_ig_short_expiries.py` | Idealized backtest (no adjustments) |
| `scripts/backtesting/run_backtest_ig_weekly_long.py` | Weekly long expiries (6-7 days) |

### **Analysis Scripts**

| File | Purpose |
|------|---------|
| `scripts/analysis/measure_reality_framework.py` | Black-Scholes option pricing calculator |
| `scripts/analysis/paper_trading_log.py` | Paper trading logging framework |
| `scripts/analysis/analyze_kelly_equity.py` | Kelly sizing analysis |

### **Core Library (src/)**

| File | Purpose |
|------|---------|
| `src/backtester.py` | Backtesting engine |
| `src/data_manager.py` | Polygon.io API integration |
| `src/indicators.py` | ATR and other indicators |
| `src/strategies.py` | Strategy filters |

---

## 💡 Strategy Details

### **SHORT EXPIRIES Strategy**

| Day | Entry | Expiry | Days | Win Rate | Avg Win | Trade? |
|-----|-------|--------|------|----------|---------|--------|
| **Monday** | 16:00 ET | Wednesday | 2 | 89.7% | +6.7% | ✅ YES |
| **Tuesday** | 16:00 ET | Wednesday | 1 | 78.4% | +9.8% | ✅ YES |
| **Wednesday** | 16:00 ET | Friday | 2 | 89.8% | +6.5% | ✅ YES |
| **Thursday** | 16:00 ET | Friday | 1 | 78.2% | +9.2% | ✅ YES |
| **Friday** | 16:00 ET | Monday | 3 | 69.1% | +5.3% | ⚠️ Paper trading only |

**Why short expiries?**
- Less theta decay (time value loss)
- 1-3 days vs 6-7 days reduces theta impact by 50-70%
- Capital efficiency (max 12 overlapping positions vs 22 for weekly)

### **Signal Generation**

1. Check if previous day moved > 0.10% (exclude flat days)
2. **RED day** → BUY CALL (fade down)
3. **GREEN day** → BUY PUT (fade up)
4. Entry at 16:00 ET close price
5. Target: Close ± (ATR × 0.1)

### **Position Sizing**

- **Formula**: `Position = min(Equity × 5.23%, $1,000)`
- **Starting at $10k**: $523 per trade
- **At $20k+**: $1,000 per trade (capped)
- **Kelly sizing**: Optimizes long-term growth

### **Reality Adjustments**

**Why backtest shows 64.8% but reality is 34.3%?**

| Component | Impact |
|-----------|--------|
| **Theta decay** | Options lose time value daily (adjustment factor 0.65x for SPY 2-day) |
| **Bid/ask spread** | SPY: 3%, QQQ: 5%, IWM: 10%, DIA: 15% |
| **Slippage** | SPY: 0.8%, QQQ: 1.5%, IWM: 2.3%, DIA: 3.1% |
| **Commission** | $0.65 per contract × 2 (entry + exit) |

**Net Effect:**
```
Backtest WIN: +45%
Reality WIN (SPY): +45% × 0.65 - 3% - 0.8% - 0.13% = +26.0%
```

---

## 🔧 Platform-Specific Trading

### **IG.com (UK/Europe)**

**Available**: SPY (as "US 500"), QQQ, IWM, DIA

**US 500 Conversion**:
- US 500 = SPY × 10
- Example: SPY $681 → US 500 6810
- Strikes: 5-point increments (6805, 6810, 6815)

```bash
python trade.py  # Shows US 500 details
```

### **IBKR (Interactive Brokers)**

**Available**: SPY, QQQ, IWM, DIA

**Direct Trading**:
- SPY trades as SPY (normal)
- Strikes: $1 increments (680, 681, 682)

```bash
python trade.py  # Shows SPY details
```

**Auto-trader outputs details for BOTH platforms.**

---

## 🎯 Ticker Recommendations

### **Based on Reality-Adjusted Backtest:**

| Ticker | Expected Value | Avg Win | Recommendation |
|--------|----------------|---------|----------------|
| **SPY** | **+8.7%** | **+26.0%** | ✅ **TRADE THIS** |
| QQQ | -5.8% | +17.1% | ⚠️ Negative EV - Avoid |
| IWM | -20.2% | +-1.2% | ❌ Very poor - Avoid |
| DIA | -34.3% | +-13.9% | ❌ Extremely poor - Avoid |

**Why avoid QQQ, IWM, DIA?**
- Wide bid/ask spreads (5-15%) eat into profits
- Even on "wins", you barely break even or lose money
- Negative expected value over time

**Trade SPY only for 34.3% CAGR (realistic and profitable)**

---

## 📚 Documentation

### **Guides** (docs/guides/)

- `DAILY_PAPER_TRADING_CHECKLIST.md` - Complete paper trading workflow
- `REALITY_CALIBRATION_GUIDE.md` - 3-month calibration process
- `DASHBOARD_GUIDE.md` - Dashboard usage guide

### **Backtest Reports** (docs/backtests/)

- `BACKTEST_REALITY_RESULTS_SUMMARY.md` - **READ THIS FIRST** - Complete analysis
- `AUTO_TRADER_REALITY_ADJUSTMENTS.md` - Auto-trader modifications
- `AUTO_TRADER_SPY_ONLY_UPDATE.md` - SPY-only configuration
- `CRITICAL_ISSUES.md` - Why strategies may fail in reality

---

## 🔄 Workflow

### **Daily Trading (Paper Trading)**

1. **Morning (before 15:00 UK / 10:00 ET)**
   ```bash
   python trade.py
   ```
   Get today's prediction and order details

2. **16:00 ET (Market Close)**
   - Place paper trade on IG.com or IBKR
   - Use order details from auto-trader output

3. **Next Day (After Exit)**
   - Log actual fills and exits
   ```python
   from scripts.analysis import paper_trading_log
   paper_trading_log.log_paper_trade_exit(...)
   ```

4. **Evening (After Trade Closes)**
   - Compare actual vs predicted
   ```python
   paper_trading_log.compare_actual_vs_backtest('2026-02-05')
   ```

### **Weekly Review (Every Sunday)**

```python
from scripts.analysis import paper_trading_log
paper_trading_log.calculate_adjustment_factors()
```

Update `config/reality_adjustments.json` with real data.

### **Monthly Re-Backtest**

```bash
python backtest.py --reality
```

Verify predicted CAGR matches actual paper trading results.

---

## 🛠️ Installation

### **1. Clone Repository**

```bash
git clone <your-repo-url>
cd OvernightFade
```

### **2. Install Dependencies**

```bash
pip install -r requirements.txt
```

### **3. Configure Polygon.io API Key**

Create `.env` file:
```
POLYGON_API_KEY=your_key_here
```

Or set in `config/config.json`.

### **4. Fetch Data**

```bash
python fetch.py
```

Downloads 10 years of SPY, QQQ, IWM, DIA data.

---

## 🧪 Testing

Run tests:
```bash
pytest tests/
```

---

## 📦 Dependencies

See `requirements.txt`:
- pandas
- numpy
- pytz
- requests
- rich (for terminal UI)

---

## 🤝 Contributing

This is a private trading system. Not for redistribution.

---

## 📄 License

Private. All rights reserved.

---

## 🎯 Next Steps

1. **Start Paper Trading**: Run `python trade.py` daily
2. **Follow Checklist**: See `docs/guides/DAILY_PAPER_TRADING_CHECKLIST.md`
3. **Calibrate**: After 3-4 weeks, update reality adjustments
4. **Verify**: Compare backtest predictions with actual results
5. **Go Live**: After calibration shows <10% difference

---

**Last Updated**: 2026-02-05
**Version**: 5.0 (Reorganized, SHORT Expiries, Reality-Adjusted, SPY Only)
**Status**: Ready for paper trading
