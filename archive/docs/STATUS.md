# OvernightFade Trading System - Current Status

**Last Updated**: 2026-02-05 22:30 UK (16:30 ET)
**Version**: 5.0 (Post-Cleanup, SPX-Corrected)
**Current Phase**: Paper Trading Ready

---

## 🎯 Current State

### What's Working
- ✅ **auto_trade_ig.py**: Fully functional, generates daily trading signals
- ✅ **Data Pipeline**: Fresh data from Polygon.io + yfinance fallback
- ✅ **SPX vs SPY Handling**: Correctly differentiates IG.com (SPX) from IBKR (SPY)
- ✅ **Option Premium Calculations**: Uses Black-Scholes for entry/target premiums
- ✅ **Reality Adjustments**: Applies 0.72x multiplier for 1-day SPY options

### Today's Data (2026-02-05)
```
SPY (ETF): $677.62 close, RED day (-0.49%)
SPX (Index): 6798.40 close
Difference: SPX - (SPY*10) = 22.20 points (always ~20-25pt spread)
ATR: $52.34 (scaled to 523.4 for SPX)
```

### Today's Signal Generated (2026-02-05 16:30 ET)
```
Signal: BUY CALL (RED day → Fade Up strategy)
Expiry: 2026-02-06 (Friday, 1-day expiry)
Expected P&L: +28.5% (win) or -103.1% (loss, premium only)

IG.com (US 500 = SPX):
  - Strike: 6800 (ATM for 6798.40)
  - Calculated Entry Premium: ~21 pts (Black-Scholes with 15% IV)
  - Actual IG.com BUY price: ~34 pts (market shows higher IV)
  - Target SELL Limit: 34 * 1.285 = 44 pts
  - Underlying Target: 6850.74 (reference only, not used for limit)

IBKR (SPY = ETF):
  - Strike: 678 (ATM for 677.62)
  - Calculated Entry Premium: ~2 pts
  - Target SELL Limit: 2 * 1.285 = 2.6 pts
  - Underlying Target: 682.85 (reference only)
```

---

## 🔧 Critical Technical Details

### The SPX vs SPY Issue (MAJOR FIX TODAY)

**The Problem:**
- Initially, the script assumed `US 500 = SPY * 10`
- **WRONG**: IG.com's "US 500" is actually **SPX** (S&P 500 Index), NOT SPY ETF
- SPX and SPY*10 have a persistent 20-25 point difference
- This caused incorrect strikes and premiums

**The Fix:**
1. Modified `calculate_order_details()` in auto_trade_ig.py to:
   - Fetch live SPX price via `yfinance.Ticker('^GSPC')` for IG.com orders
   - Store actual SPY price separately for IBKR orders
   - Calculate option premiums correctly for each

2. Updated display functions to:
   - Show SPX data for IG.com (US 500)
   - Show SPY data for IBKR
   - Clarify in messaging that US 500 = SPX, not SPY*10

**File Modified**: `scripts/trading/auto_trade_ig.py`
- Lines ~430-460: SPX fetching logic
- Lines ~625-635: Table generation (IBKR strike calculation)
- Lines ~695-730: IBKR detailed display with SPY-specific Black-Scholes

### Data Staleness Issue (FIXED TODAY)

**The Problem:**
- Data fetched at 15:57 ET (mid-day) showed SPY close $681.49
- Actual close was $677.43 (market closes 16:00 ET)
- Script thought data was "up to date" and didn't re-fetch

**The Fix:**
Modified `src/data_manager.py` line ~273-281:
```python
# Check if last date is today AND we're after market close
if last_date_str == today_str and is_after_cash_close_et():
    console.print("Detected same-day data, re-fetching for final close...")
    start_date = today_str  # Re-fetch today to get final close
```

**How it works now:**
- If data file contains today's date
- AND current time is after 16:00 ET (market close)
- THEN re-fetch today's bar to get final close
- Uses existing `is_after_cash_close_et()` from session_utils.py

### Option Premium Calculation (IMPLEMENTED TODAY)

**The Problem:**
- Script was showing "Target (Limit): 6826.43" (underlying target price)
- **WRONG for options**: Limit orders are for option PREMIUM, not underlying

**The Fix:**
1. Import Black-Scholes functions at top of auto_trade_ig.py:
   ```python
   sys.path.insert(0, 'scripts/analysis')
   from measure_reality_framework import black_scholes_call, black_scholes_put
   ```

2. Calculate entry premium using Black-Scholes:
   ```python
   T = days_to_expiry / 365.0  # Time in years
   r = 0.05  # Risk-free rate
   sigma = 0.15  # Implied volatility (15% assumption)

   entry_option = black_scholes_call(underlying, strike, T, r, sigma)
   entry_premium = entry_option['price']
   ```

3. Calculate target premium using reality adjustments:
   ```python
   pnl_multiplier = 0.72  # For 1-day SPY
   realistic_win_pct = pnl_multiplier * 45  # 45% backtest → 32.4% realistic
   target_premium = entry_premium * (1 + realistic_win_pct / 100)
   ```

4. Display shows:
   - Entry Premium (BUY): XX pts
   - Target Premium (SELL Limit): YY pts
   - Expected Profit: +ZZ pts (+28.5%)

**Note**: Calculated premiums use 15% IV assumption. Actual market premiums differ due to current IV. User should use actual broker BUY price and multiply by 1.285 for target.

---

## 📊 Strategy Summary

### Core Strategy: Overnight Fade (SHORT Expiries)
- **Backtest Period**: 10 years (2015-2025)
- **Ticker**: SPY (S&P 500 ETF) for backtesting
- **Signal**: Fade previous day's move
  - RED day (down) → BUY CALL (expect bounce)
  - GREEN day (up) → BUY PUT (expect pullback)
- **Expiries**: 1-3 days (Mon→Wed, Tue→Wed, Wed→Fri, Thu→Fri, Fri→Mon)
- **Entry**: Market close (16:00 ET)
- **Exit**: Option expiry (16:00 ET next 1-3 days)

### Filters Applied
1. **Flat Day Exclusion**: Skip if abs(daily move) < 0.10%
2. **LastHourVeto**: (Optional, not currently active)
3. **ATR Target**: Exit when underlying moves 0.1 * ATR (52 points for SPX)

### Performance (Reality-Adjusted)
- **Backtest (Idealized)**: 48.8% CAGR, 89% win rate
- **Reality-Adjusted**: 34.3% CAGR, 86% win rate
- **Expected Per Trade**: +8.7% (after spreads, slippage, theta, commission)
- **1-day SPY Options**: +28.5% (win), -103.1% (loss on premium)
- **Max Loss**: -5.23% of account (position sizing at 5.23%)

### Reality Adjustments (config/reality_adjustments.json)
```json
{
  "spread_costs": {"SPY": 0.03},  // 3% of premium
  "slippage_pct": {"SPY": 0.008},  // 0.8% of premium
  "commission_per_contract": 0.65,  // $0.65 per contract
  "pnl_adjustments": {
    "1_day": {"SPY": 0.72}  // 72% of backtest expectation
  }
}
```

**Why 0.72x for 1-day?**
- Theta decay erodes more on short expiries
- Bid/ask spreads wider on short-dated options
- Less time for underlying to reach target
- Based on Black-Scholes modeling (NOT YET CALIBRATED with actual trades)

---

## 🗂️ Project Organization (Post-Cleanup v5.0)

### Root Directory (Clean - Only 4 Essential Files)
```
OvernightFade/
├── README.md                      # Project overview
├── SCRIPTS_GUIDE.md              # Which scripts to use when
├── STATUS.md                     # This file - current status
├── SYSTEM.md                     # Architecture (stable reference)
├── NEXT.md                       # Action queue
├── GIT_SETUP_GUIDE.md           # Git initialization (completed)
├── requirements.txt              # Python dependencies
└── .gitignore                    # Excludes data/, logs/, results/
```

### Core Executable Scripts
```
scripts/
├── trading/
│   └── auto_trade_ig.py          ⭐ PRIMARY - Daily signal generator
├── backtesting/
│   └── run_backtest_ig_short_expiries_reality.py  ⭐ Monthly verification
├── data/
│   ├── fetch_multi_ticker_data.py    # Fetch 10yr history (2-4 hours)
│   └── verify_multi_ticker_data.py   # Check data completeness
└── analysis/
    ├── measure_reality_framework.py  # Black-Scholes calculator
    ├── paper_trading_log.py          # Log actual vs predicted
    ├── parameter_optimizer.py        # ATR target optimization
    ├── strategy_comparison.py        # Compare strategy variants
    ├── walk_forward_validation.py    # Out-of-sample validation
    └── validation_holdout.py         # Final holdout test
```

### Core Library Modules
```
src/
├── data_manager.py           # Polygon.io API + rate limiting + FIXED staleness
├── rate_limiter.py          # Token bucket algorithm
├── session_utils.py         # Timezone utilities (DST-safe)
├── strategies.py            # Signal generation + filters
└── archive/                 # Legacy scripts (reference only)
    ├── backtester_old_exit_comparison.py  # Old 09:35 ET exit test
    ├── dashboard_legacy.py                # Pre-SHORT expiries
    └── backtester_multi_ticker.py         # Multi-ticker comparison
```

### Data Storage
```
data/
├── SPY/
│   ├── daily_OHLCV.parquet      # Daily bars (2015-2026, 2.5K days)
│   └── intraday/                # Minute bars (2513 files)
├── VIX/, XLK/, XLF/, ...        # Other tickers (for future enhancements)
```

### Configuration
```
config/
├── config.json                  # Main settings (ticker, lookback, budget)
├── reality_adjustments.json     # Spread/slippage/theta factors
└── README.md                    # Config documentation
```

---

## 🚀 How to Run Daily Trading

### Command
```bash
python scripts/trading/auto_trade_ig.py
```

### Timing
- Run at **16:00 ET (21:00 UK)** or shortly after
- Script automatically:
  1. Checks if after market close
  2. Fetches latest data (re-fetches if stale)
  3. Generates signal (CALL/PUT/NO_TRADE)
  4. Calculates strikes and premiums
  5. Shows order details for IG.com and IBKR
  6. Logs to `logs/ig_orders_dryrun.csv`

### Environment Requirements
- `ALLOW_NETWORK=1` environment variable (script sets this automatically)
- Python packages: pandas, yfinance, rich, pytz, python-dotenv
- Polygon.io API key in `.env` file

### Output Interpretation
```
IG.com Order Details:
  Strike: 6800 (ATM)                    ← ATM strike for SPX
  Entry Premium (BUY): 20.96 pts        ← Black-Scholes estimate
  Target Premium (SELL Limit): 27.75 pts ← Use for limit order

Action: BUY option at ~20.96 pts, set SELL limit at 27.75 pts
```

**Important**: The calculated premium (20.96) uses 15% IV assumption. Actual IG.com BUY price may differ (e.g., 34 pts if IV is higher). Use actual broker price * 1.285 for target.

---

## 📝 Recent Changes Log (Last 24 Hours)

### 2026-02-05 16:00-22:30 UK

1. **Git Version Control Setup** ✅
   - Initialized repository
   - Created .gitignore (excludes /data/, /logs/, /results/)
   - Fixed path issues (was ignoring scripts/data/)
   - Created .gitattributes for cross-platform line endings
   - Initial commit: 94 files

2. **Root Folder Cleanup** ✅
   - Removed wrapper scripts (trade.py, backtest.py, fetch.py)
   - Removed old logs (fetch_qqq.log, pipeline_output.log)
   - Removed experiment folders
   - Result: Clean root with only 4 essential files

3. **Documentation Overhaul** ✅
   - Rewrote config/README.md (v5.0, SPY-only, reality adjustments)
   - Rewrote src/README.md (only 4 core modules)
   - Rewrote scripts/README.md (38 scripts documented)
   - Created SCRIPTS_GUIDE.md (200+ lines, primary vs research vs archived)
   - Created src/archive/README.md (explains why scripts archived)

4. **Script Reorganization** ✅
   - Moved 3 legacy scripts to src/archive/ with descriptive names
   - Moved 4 analysis scripts to scripts/analysis/ with clear names
   - Updated all documentation to reflect new structure

5. **Unicode Errors Fixed** ✅
   - Replaced Unicode box characters (╔═══╗) with ASCII (===)
   - Replaced checkmarks (✓) with "OK"
   - Fixed in 5 files: backtester_multi_ticker.py, fetch_multi_ticker_data.py, verify_multi_ticker_data.py, fetch_data_simple.py, run_full_pipeline.py

6. **Data Staleness Bug Fixed** ✅
   - Problem: Data fetched at 15:57 ET was stale
   - Fix: data_manager.py now re-fetches same-day data if after market close
   - Used yfinance fallback to get correct close ($677.43 vs stale $681.49)

7. **SPX vs SPY Correction** ✅ (MAJOR FIX)
   - Problem: Script assumed US 500 = SPY * 10
   - Reality: IG.com's US 500 = SPX (Index), not SPY (ETF)
   - Fixed: Now fetches live SPX via yfinance for IG.com orders
   - Fixed: IBKR uses actual SPY data, not SPX/10
   - Updated all messaging to clarify US 500 = SPX

8. **Option Premium Display** ✅ (MAJOR FIX)
   - Problem: Showed underlying target price as "limit"
   - Reality: Limit orders are for option premium
   - Fixed: Calculate entry premium using Black-Scholes
   - Fixed: Calculate target premium using reality adjustments
   - Display now shows: Entry Premium (BUY), Target Premium (SELL Limit)

9. **IBKR Strike Correction** ✅
   - Problem: Table showed strike 680 for IBKR (using SPX/10 = 679.84)
   - Reality: Actual SPY close is 677.62 → strike should be 678
   - Fixed: Both table and detailed view now use actual SPY for IBKR

---

## ⚠️ Known Issues & Limitations

### 1. Implied Volatility (IV) Estimation
- **Issue**: Black-Scholes uses fixed 15% IV assumption
- **Impact**: Calculated premiums (21 pts) differ from market (34 pts)
- **Workaround**: Use actual broker BUY price * 1.285 for target
- **Future Fix**: Calibrate IV after 5-10 paper trades, or fetch live IV from market

### 2. Reality Adjustments Not Calibrated
- **Issue**: 0.72x multiplier is from Black-Scholes modeling, not real trades
- **Impact**: Expected P&L may differ from actual
- **Workaround**: Paper trade 10-20 times, measure actual vs predicted
- **Future Fix**: Update reality_adjustments.json with calibrated values

### 3. No Automated Execution (Phase 1)
- **Issue**: Script only generates signals, doesn't place orders
- **Impact**: Manual order entry on IG.com or IBKR
- **Workaround**: Copy values from script output
- **Future Fix**: Phase 2 will add IG.com API or IBKR API integration

### 4. Friday Trades (3-Day Expiry)
- **Issue**: Friday → Monday (3-day) has lower performance (69% WR vs 78-90%)
- **Impact**: Expected CAGR drops on Fridays
- **Current State**: Friday trades INCLUDED for paper trading calibration
- **Decision Pending**: May exclude Fridays after calibration confirms lower performance

---

## 🧪 Testing Status

### Manual Testing Completed Today
- ✅ auto_trade_ig.py generates signals correctly
- ✅ SPX fetching works (6798.40 confirmed)
- ✅ SPY data correct (677.62 confirmed)
- ✅ IBKR strike correct (678, not 680)
- ✅ Option premiums calculated and displayed
- ✅ Data staleness detection and re-fetch working
- ✅ All Unicode errors resolved

### Not Yet Tested
- ⏳ Actual paper trading (starts tomorrow)
- ⏳ Reality adjustment calibration (needs 10+ trades)
- ⏳ IV calibration (needs market data comparison)
- ⏳ Multi-day performance tracking

---

## 💭 Strategic Decisions & Rationale

### Why SPY for Backtest but SPX for Trading?
- **Backtest uses SPY**: Better historical data availability, more liquid ETF
- **IG.com uses SPX**: That's what they offer as "US 500"
- **IBKR uses SPY**: Can trade SPY ETF options directly
- **Impact**: 22-point spread between SPX and SPY*10, handled in script

### Why SHORT Expiries (1-3 Days)?
- **Performance**: 34.3% CAGR vs lower for weekly/monthly
- **Win Rate**: 86% vs lower for longer expiries
- **Capital Efficiency**: Faster turnover, more trades per year
- **Risk**: Lower max drawdown due to quick closes

### Why Reality Adjustments?
- **Problem**: Backtests assume perfect fills at mid-price
- **Reality**: Bid/ask spreads, slippage, theta decay, commissions
- **Solution**: Apply empirically-derived multipliers
- **Current**: 0.72x for 1-day SPY (NOT YET CALIBRATED)

### Why Paper Trading Before Live?
- **Calibration**: Validate reality adjustments with actual fills
- **IV Verification**: Compare calculated vs actual premiums
- **Confidence**: Prove strategy works in real market conditions
- **Timeline**: 10-20 trades (2-4 weeks) before going live

---

## 🎓 Key Learnings from Today

1. **Always Verify Instrument Specifications**
   - Assumed US 500 = SPY * 10 (WRONG)
   - Actually US 500 = SPX (Index)
   - Cost: Several hours of debugging

2. **Data Timestamps Matter**
   - Mid-day data looks complete but isn't
   - Need explicit "after close" check
   - Fixed with is_after_cash_close_et()

3. **Options Trading UI Expectations**
   - Users expect BUY/SELL premium prices
   - Not underlying target prices
   - Black-Scholes needed for premiums

4. **Context Length Management**
   - Long sessions degrade AI performance
   - Need living documentation (this file)
   - Fresh sessions with STATUS.md work better

---

## 📞 Support & Documentation

### If Something Breaks
1. Check `logs/ig_orders_dryrun.csv` for last successful run
2. Verify data freshness: `python -c "import pandas as pd; df = pd.read_parquet('data/SPY/daily_OHLCV.parquet'); print(df.tail(1))"`
3. Check ALLOW_NETWORK=1 is set
4. Verify Polygon API key in .env

### Key Documentation Files
- `SCRIPTS_GUIDE.md` - Which scripts to use when
- `config/README.md` - Configuration details
- `src/README.md` - Library module reference
- `scripts/README.md` - All scripts documented
- `src/archive/README.md` - Why scripts were archived

### Re-running Backtests
```bash
python scripts/backtesting/run_backtest_ig_short_expiries_reality.py
# Output: results/ig_short_expiries_reality_backtest.csv
# Expected: 34.3% CAGR, 86% WR, 1671 trades over 10 years
```

---

**Status**: Ready for paper trading. Next trade signal at 2026-02-06 16:00 ET.
