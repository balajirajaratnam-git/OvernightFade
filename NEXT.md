# OvernightFade - Next Actions

**Last Updated**: 2026-02-05 22:30 UK
**Current Phase**: Paper Trading (Pre-Live)

---

## 🎯 Immediate Actions (Today/Tomorrow)

### 1. Paper Trade Tomorrow's Signal ⚡ PRIORITY
**Status**: Ready to execute
**When**: 2026-02-06 09:30 ET (14:30 UK) market open

**Today's Signal (Generated 2026-02-05 16:30 ET)**:
```
Signal: BUY CALL (RED day fade)
Expiry: 2026-02-06 (Friday, 1-day)
Expected: +28.5% win or -103.1% loss

IG.com (US 500):
  Strike: 6800
  Entry: ~34 pts (actual IG.com BUY price)
  Target: 44 pts (34 * 1.285)

IBKR (SPY):
  Strike: 678
  Entry: ~2 pts (actual IBKR BUY price)
  Target: 2.6 pts (2 * 1.285)
```

**Steps**:
1. ✅ Signal generated (completed)
2. ⏳ Open IG.com or IBKR at market open (09:30 ET)
3. ⏳ Check actual BUY price (may differ from calculated ~21/34 pts)
4. ⏳ BUY CALL at market price
5. ⏳ Set SELL limit order: actual_entry_price * 1.285
6. ⏳ Monitor until expiry (16:00 ET tomorrow)
7. ⏳ Log results using paper_trading_log.py

**Commands**:
```bash
# After trade closes, log results:
python scripts/analysis/paper_trading_log.py

# It will prompt for:
# - Entry price (actual paid)
# - Exit price (actual received)
# - Entry time, exit time
# - Compare vs predicted (+28.5%)
```

---

## 📊 This Week (2026-02-06 to 2026-02-12)

### 2. Execute 5 Paper Trades
**Goal**: Collect initial calibration data
**Timeline**: 5 trading days (Mon-Fri)

**Process**:
```bash
# Daily at 16:00 ET (21:00 UK):
python scripts/trading/auto_trade_ig.py

# Next day after close:
python scripts/analysis/paper_trading_log.py
```

**Track**:
- Actual entry vs calculated premium (IV calibration)
- Actual P&L vs predicted +28.5%
- Win rate (expected 86%)
- Execution quality (slippage, fills)

**Decision Point After 5 Trades**:
- If actual > predicted: Strategy is better than modeled ✅
- If actual ≈ predicted: Calibration is accurate ✅
- If actual < predicted: Need to adjust reality factors ⚠️

### 3. Create Paper Trading Spreadsheet
**Purpose**: Track all trades in one place

**Template** (Create in Excel/Google Sheets):
```
| Date       | Signal | Strike | Entry | Exit | P&L% | Predicted | Diff | Notes |
|------------|--------|--------|-------|------|------|-----------|------|-------|
| 2026-02-06 | CALL   | 6800   | 34    | ?    | ?    | +28.5%    | ?    |       |
| 2026-02-07 | ?      | ?      | ?     | ?    | ?    | ?         | ?    |       |
```

**Include**:
- Execution notes (slippage, fill quality)
- Market conditions (VIX level, major news)
- Timing (filled at open? mid-day?)
- Broker (IG.com vs IBKR comparison)

---

## 🔧 This Month (February 2026)

### 4. Calibrate Implied Volatility
**Current Problem**: Using fixed 15% IV, but market shows ~25-30%
**Impact**: Calculated premiums (21 pts) vs actual (34 pts)

**After 5-10 Trades**:
```python
# Analyze actual vs calculated premiums
actual_premiums = [34, 36, 32, 35, 33]  # From trades
calculated_premiums = [21, 22, 20, 21, 20]  # From Black-Scholes

# Average ratio
iv_adjustment = mean(actual / calculated)  # ~1.6x

# Update auto_trade_ig.py:
sigma = 0.15 * iv_adjustment  # ~0.24 (24% IV)
```

**File to Modify**: `scripts/trading/auto_trade_ig.py` line ~489
**Change**: `sigma = 0.15` → `sigma = 0.24` (or calibrated value)

### 5. Calibrate Reality Adjustments
**Current Values** (in config/reality_adjustments.json):
```json
{
  "pnl_adjustments": {
    "1_day": {"SPY": 0.72}  // 72% of backtest (45% → 32.4%)
  }
}
```

**After 10-20 Trades**:
```python
# Calculate actual vs backtest performance
backtest_expectation = 45  # 45% per win
actual_wins = [30, 25, 35, 28, 32, ...]  # % gains from real trades
actual_avg = mean(actual_wins)  # e.g., 28%

# New multiplier
new_multiplier = actual_avg / backtest_expectation  # e.g., 28/45 = 0.62

# Update config/reality_adjustments.json:
"1_day": {"SPY": 0.62}  # Instead of 0.72
```

**Decision Point**:
- If 0.72 is accurate (actual ≈ 32%): Keep current ✅
- If needs adjustment: Update and re-run backtests

### 6. Friday Trade Decision
**Question**: Should we trade Friday → Monday (3-day expiry)?

**Current State**: Included but with lower expected performance
- Backtest: 69% WR (vs 78-90% for 1-2 day)
- Theory: Weekend theta decay hurts performance

**Action**: Track Friday trades separately
```bash
# In paper trading log, tag Friday trades
# After 5-10 Friday trades, compare:
friday_win_rate = count(wins) / count(total_friday)
other_days_win_rate = count(wins) / count(total_other)

# If friday_win_rate < 70%: Exclude Fridays
# If friday_win_rate > 75%: Keep Fridays
```

**File to Modify If Excluding**: `scripts/trading/auto_trade_ig.py` line ~720
```python
# Add check:
if day_name == "Friday":
    console.print("[yellow]Friday (3-day expiry) - Lower expected performance[/yellow]")
    return  # Skip Friday trades
```

### 7. Monthly Backtest Verification
**Purpose**: Ensure strategy still performs as expected

**Command**:
```bash
python scripts/backtesting/run_backtest_ig_short_expiries_reality.py
```

**Expected Output**:
```
Trades: 1,671 over 10 years
Win Rate: 86.3%
CAGR: 34.3%
Max Drawdown: -15.2%
Sharpe: 2.1
```

**Check**:
- ✅ CAGR still 30-35%
- ✅ Win rate still 85-87%
- ⚠️ If CAGR < 25%: Investigate parameter drift
- ⚠️ If Win rate < 80%: Strategy may be degrading

---

## 🚀 Next 3 Months (Q1 2026)

### 8. Decide: Go Live or Continue Paper Trading
**Timeline**: After 15-25 paper trades (mid-March 2026)

**Go/No-Go Criteria**:
```
✅ Go Live If:
  - Actual win rate > 80% (target: 86%)
  - Actual avg win > 20% (target: 28.5%)
  - Max single loss < 110% of premium (expected: 103%)
  - Execution quality good (fills, slippage acceptable)
  - Calibration complete (IV, reality factors)

⚠️ Continue Paper If:
  - Win rate 70-80% (needs more data)
  - Large discrepancy between predicted/actual
  - Execution issues (bad fills, high slippage)

❌ Stop/Revise If:
  - Win rate < 70%
  - Losses larger than expected
  - Strategy not performing as backtested
```

**Checklist Before Going Live**:
- [ ] 15+ paper trades completed
- [ ] IV calibrated (sigma adjusted)
- [ ] Reality adjustments calibrated
- [ ] Paper trading P&L matches backtest expectations
- [ ] Risk management understood (max -5.23% per trade)
- [ ] Broker platform mastered (order entry, limit orders)
- [ ] Emergency procedures defined (how to close position fast)

### 9. Implement API Integration (Phase 2)
**Only If Going Live**

**Options**:
1. **IG.com API**:
   - Pros: Direct SPX trading
   - Cons: Complex API, limited documentation
   - Effort: 2-3 weeks development

2. **IBKR API** (Recommended):
   - Pros: Well-documented, Python library (ib_insync)
   - Cons: SPY not SPX (but same strategy)
   - Effort: 1-2 weeks development

**Steps**:
```bash
# 1. Install IBKR API
pip install ib_insync

# 2. Connect to IBKR paper trading
from ib_insync import IB
ib = IB()
ib.connect('127.0.0.1', 7497, clientId=1)

# 3. Place order
contract = Option('SPY', '20260206', 678, 'C', 'SMART')
order = LimitOrder('BUY', 1, limit_price=2.0)
trade = ib.placeOrder(contract, order)

# 4. Monitor position
position = ib.positions()[0]
if position.avgCost * 1.285 < current_price:
    ib.placeOrder(contract, LimitOrder('SELL', 1, target_price))
```

**New Files to Create**:
- `scripts/trading/auto_trade_ibkr_api.py` - IBKR automated execution
- `scripts/trading/monitor_positions.py` - Position monitoring
- `scripts/trading/close_position.py` - Emergency close

### 10. Advanced Risk Management
**After Going Live**

**Features to Add**:
1. **Max Daily Loss**: Stop trading if down > 15% in one day
2. **Max Drawdown**: Stop if total portfolio down > 25%
3. **Position Limits**: Max 1 position at a time (current)
4. **Win Streak Management**: Reduce size after 5+ wins (avoid overconfidence)
5. **Loss Streak Management**: Reduce size after 3+ losses (preserve capital)

**Implementation**:
```python
# Add to auto_trade_ig.py
def check_risk_limits():
    """Check if risk limits exceeded"""
    # Load trade history
    history = pd.read_csv('logs/trade_history.csv')

    # Today's P&L
    today_pnl = history[history['date'] == today]['pnl'].sum()
    if today_pnl < -0.15:  # -15% daily loss
        return "STOP_TRADING_TODAY"

    # Drawdown
    cumulative_pnl = history['pnl'].cumsum()
    max_dd = (cumulative_pnl - cumulative_pnl.cummax()).min()
    if max_dd < -0.25:  # -25% drawdown
        return "STOP_TRADING_PORTFOLIO"

    # Loss streak
    last_5 = history.tail(5)['pnl']
    if (last_5 < 0).sum() >= 3:  # 3+ losses in last 5
        return "REDUCE_SIZE"

    return "OK"
```

---

## 📚 Learning & Optimization (Ongoing)

### 11. VIX Filter Research
**Question**: Should we skip trades when VIX > threshold?

**Hypothesis**: High volatility → lower win rate

**Test**:
```bash
# Modify backtest to test VIX filter
python scripts/backtesting/run_backtest_ig_short_expiries_reality.py --vix-threshold 25

# Compare:
# - No filter: 86% WR, 34.3% CAGR
# - VIX < 25: ?% WR, ?% CAGR
# - VIX < 20: ?% WR, ?% CAGR
```

**Decision**:
- If higher CAGR with filter: Implement
- If lower CAGR: Don't filter

### 12. Intraday Entry Timing
**Question**: Does entry time matter? (09:30 vs 10:00 vs 11:00)

**Current**: Enter at market open (09:30 ET)
**Alternative**: Wait for first hour to settle

**Test**:
- Log actual entry time in paper trades
- Compare performance by entry time
- Analyze if delayed entry improves fills

### 13. Parameter Re-Optimization
**When**: Quarterly (every 3 months)

**Command**:
```bash
python scripts/analysis/parameter_optimizer.py
```

**What It Does**:
- Grid search ATR multipliers (0.05 to 0.20)
- Tests each on 10-year backtest
- Finds optimal risk/reward balance

**Expected Stability**:
- Current optimal: 0.10 (10% of ATR)
- Should remain 0.08-0.12 over time
- If drifts outside: Market regime change

---

## 🐛 Known Issues to Fix (Low Priority)

### Issue 1: Unicode in Old Scripts
**Status**: Fixed in main scripts, but archived scripts still have it
**Impact**: None (archived scripts not used)
**Fix If Needed**:
```bash
# If you need to run archived scripts:
sed -i 's/✓/OK/g' src/archive/*.py
sed -i 's/╔/=/g' src/archive/*.py
```

### Issue 2: Multi-Ticker Support Incomplete
**Status**: System designed for SPY-only, but config allows multiple
**Impact**: If someone adds QQQ to config, may not work correctly
**Fix**:
- Either remove multi-ticker from config (recommend)
- Or fully implement multi-ticker support (not recommended)

### Issue 3: Data Fetch Timing
**Status**: Polygon data sometimes lags 5-10 minutes after close
**Workaround**: yfinance fallback (implemented)
**Better Fix**:
- Wait 10 minutes after close before fetching
- Or use websocket API for real-time data

---

## 📝 Documentation Updates Needed

### Update When Going Live
1. **README.md**: Add "Phase 2: Live Trading" section
2. **SCRIPTS_GUIDE.md**: Add API automation scripts
3. **STATUS.md**: Update to "Live Trading" phase
4. **NEXT.md**: This file - archive completed items

### Update After Calibration
1. **config/reality_adjustments.json**: Calibrated values
2. **auto_trade_ig.py**: Calibrated IV (sigma)
3. **SYSTEM.md**: Document calibrated parameters
4. **STATUS.md**: Note calibration completion date

---

## 🎯 Success Metrics

### Paper Trading Phase (Current)
- **Primary**: Win rate > 80%
- **Secondary**: Avg win > 25%
- **Tertiary**: Max loss < 110% premium

### Live Trading Phase (Future)
- **Monthly**: CAGR > 25% (target: 34%)
- **Quarterly**: Sharpe > 1.5 (target: 2.1)
- **Annually**: Max DD < 20% (target: 15%)

### Risk Limits (Always)
- **Daily**: Max -15% loss in one day
- **Weekly**: Max -25% loss in one week
- **Portfolio**: Max -25% drawdown from peak

---

## 💡 Ideas for Future (Backlog)

### Low Effort, High Impact
- [ ] Add Telegram/SMS alerts for signals
- [ ] Create web dashboard for monitoring
- [ ] Export trade log to Google Sheets
- [ ] Add email notifications for fills

### Medium Effort, Medium Impact
- [ ] Mobile app for order entry
- [ ] Voice alerts ("RED day, BUY CALL at 6800")
- [ ] Integration with broker portfolio view
- [ ] Automated position monitoring

### High Effort, Uncertain Impact
- [ ] Machine learning for entry timing
- [ ] Sentiment analysis from news
- [ ] Multi-strategy portfolio (add other strategies)
- [ ] Options Greeks-based adjustments

---

## 🚦 Current Status Summary

**Phase**: Paper Trading (Pre-Live)
**Next Milestone**: 5 paper trades completed (by 2026-02-12)
**Decision Point**: Go live or continue paper (mid-March)

**Immediate Actions**:
1. ⚡ Execute tomorrow's CALL signal (6800 strike, 44pt target)
2. 📊 Log result in paper_trading_log.py
3. 🔁 Repeat for 5 trades total

**Blockers**: None - system is ready
**Risks**: Normal trading risks (strategy may not work as backtested)
**Confidence**: High (86% win rate expected, validated on 10 years)

---

**Last Updated**: 2026-02-05 22:30 UK
**Next Update**: After first paper trade (2026-02-06)
