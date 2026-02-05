# Reality Calibration Guide: Backtest → Paper Trading → Refinement

## 🎯 Goal
Make backtest predictions match real-world paper trading results within 5-10%

## 📋 The Process (3 Phases)

### Phase 1: Baseline Measurement (Week 1)
### Phase 2: Data Collection (Weeks 2-4)
### Phase 3: Backtest Refinement (Ongoing)

---

## Phase 1: Baseline Measurement (Week 1)

### Day 1-2: Measure Bid/Ask Spreads

**Task:** Open IG.com at 16:00 ET and record option quotes

**Spreadsheet Template:**
```
Date,Ticker,Strike,Expiry,Bid,Ask,Mid,Spread%
2026-02-05,SPY,600,2026-02-07,3.20,3.30,3.25,3.1%
2026-02-05,QQQ,520,2026-02-07,4.10,4.30,4.20,4.8%
2026-02-05,IWM,220,2026-02-07,1.80,2.00,1.90,10.5%
2026-02-05,DIA,430,2026-02-07,3.00,3.40,3.20,12.5%
```

**Calculate Average Spreads:**
- SPY: 2-4% (expected)
- QQQ: 4-6% (expected)
- IWM: 8-12% (expected)
- DIA: 10-15% (expected)

### Day 3-5: Measure Fill Quality

**Task:** Place SMALL paper trades and track fills

**Log Template:**
```
Time,Ticker,Order_Type,Limit_Price,Filled_Price,Slippage,Time_To_Fill
16:00:15,SPY,BUY,3.25,3.27,+0.02,5 seconds
16:00:18,QQQ,BUY,4.20,4.25,+0.05,3 seconds
```

**Metrics to Track:**
- Average slippage (expected: 1-3% above mid)
- Fill rate (expected: 95%+)
- Time to fill (expected: < 10 seconds)

### Day 6-7: Test Full Cycle

**Task:** Place a complete trade (entry + exit)

1. **Entry (Day 1, 16:00 ET):**
   - Record: Underlying price, bid/ask, filled price, commission
   - Calculate: Entry slippage

2. **Monitor (Days 1-2):**
   - Check if target hit
   - Record: Time of target hit, underlying price at that time

3. **Exit (Day 2 or 3):**
   - Close position manually or at expiry
   - Record: Exit bid/ask, filled price, commission
   - Calculate: Actual P&L

---

## Phase 2: Data Collection (Weeks 2-4)

### Daily Workflow

#### Morning (Before 16:00 ET)

**1. Run backtest for today's date:**
```bash
python daily_backtest_predictor.py --date 2026-02-05
```

Output:
```
2026-02-05 Predictions:
- SPY: BUY CALL, Strike 600, Target 606, Win Prob 89%, Expected P&L +45%
- IWM: NO TRADE (flat day)
```

**2. Log prediction:**
```python
from paper_trading_log import log_backtest_prediction

log_backtest_prediction(
    date='2026-02-05',
    ticker='SPY',
    signal='BUY CALL',
    entry_price=600,
    strike=600,
    target_price=606,
    expiry_date='2026-02-07',
    days_to_expiry=2,
    predicted_win_prob=89,
    predicted_pnl_pct=45
)
```

#### At 16:00 ET (Trade Entry)

**1. Get actual IG.com quotes:**
- SPY $600 CALL Feb 7: Bid 3.20, Ask 3.30

**2. Place order:**
- Buy 1 contract at market
- Record fill: $3.27

**3. Log actual entry:**
```python
from paper_trading_log import log_paper_trade_entry

log_paper_trade_entry(
    date='2026-02-05',
    ticker='SPY',
    signal='BUY CALL',
    option_type='CALL',
    strike=600,
    expiry_date='2026-02-07',
    days_to_expiry=2,
    entry_time='16:00:15',
    underlying_price_at_entry=600.15,
    bid=3.20,
    ask=3.30,
    mid=3.25,
    filled_price=3.27,
    contracts=1,
    commission=0.65,
    order_id='IG12345',
    notes='Clean fill, no issues'
)
```

#### Next Day (Monitor & Exit)

**1. Check if target hit:**
- SPY reaches 606 at 10:30 AM next day

**2. Close position:**
- SPY $600 CALL: Bid 6.40, Ask 6.50
- Sell at market: Filled $6.42

**3. Log exit:**
```python
from paper_trading_log import log_paper_trade_exit

log_paper_trade_exit(
    trade_id='2026-02-05_SPY_CALL',
    exit_time='2026-02-06 10:30:00',
    exit_price=6.42,
    exit_underlying=606.20,
    result='WIN',
    exit_commission=0.65,
    notes='Target hit, sold at bid+$0.02'
)
```

Output:
```
Logged paper trade exit: 2026-02-05_SPY_CALL
  P&L: $2.50 (76.5%)
  Result: WIN
```

#### Evening (Compare Results)

**Run comparison:**
```python
from paper_trading_log import compare_actual_vs_backtest

compare_actual_vs_backtest('2026-02-05')
```

Output:
```
COMPARISON: 2026-02-05
========================================

SPY BUY CALL:
  Entry Price:
    Predicted: $600.00
    Actual: $600.15 (diff: $0.15)
  Spread: 3.1%
  Slippage: 0.6%
  Result:
    Predicted: WIN (89%)
    Actual: WIN
  P&L:
    Predicted: 45.0%
    Actual: 76.5% (diff: +31.5pp)

INSIGHT: Actual P&L HIGHER than predicted!
  - 2-day expiry had less theta decay than assumed
  - Spread cost was only 3.1% (assumed 5%)
```

### Weekly Review (Every Sunday)

**Calculate adjustment factors:**
```python
from paper_trading_log import calculate_adjustment_factors

calculate_adjustment_factors()
```

Output:
```
ADJUSTMENT FACTORS (from paper trading)
========================================

Overall:
  Predicted P&L (avg): 45.0%
  Actual P&L (avg): 58.3%
  ADJUSTMENT FACTOR: 1.30x

By Ticker:
  SPY:
    Predicted: 45.0%
    Actual: 65.2%
    Adjustment: 1.45x
    Avg Spread: 3.2%
    Avg Slippage: 0.8%

  IWM:
    Predicted: 45.0%
    Actual: 28.5%
    Adjustment: 0.63x
    Avg Spread: 10.8%
    Avg Slippage: 2.3%

Win Rate:
  Predicted: 89.0%
  Actual: 85.7%
  Difference: -3.3pp
```

---

## Phase 3: Backtest Refinement

### Week 4: Update Backtest Parameters

Based on 3 weeks of data, update backtest with REAL adjustment factors:

**File:** `config/reality_adjustments.json`
```json
{
  "spread_costs": {
    "SPY": 0.032,
    "QQQ": 0.051,
    "IWM": 0.108,
    "DIA": 0.142
  },
  "slippage_pct": {
    "SPY": 0.008,
    "QQQ": 0.015,
    "IWM": 0.023,
    "DIA": 0.031
  },
  "commission_per_contract": 0.65,
  "pnl_adjustments": {
    "1_day": {
      "SPY": 1.20,
      "QQQ": 0.95,
      "IWM": 0.60,
      "DIA": 0.45
    },
    "2_day": {
      "SPY": 1.45,
      "QQQ": 1.15,
      "IWM": 0.63,
      "DIA": 0.38
    },
    "3_day": {
      "SPY": 1.10,
      "QQQ": 0.85,
      "IWM": 0.45,
      "DIA": 0.25
    }
  },
  "win_rate_adjustments": {
    "overall": -0.033,
    "by_ticker": {
      "SPY": -0.02,
      "QQQ": -0.03,
      "IWM": -0.05,
      "DIA": -0.06
    }
  }
}
```

### Apply Adjustments to Backtest

Modify backtest code to use real-world factors:

```python
# Load reality adjustments
with open('config/reality_adjustments.json') as f:
    adjustments = json.load(f)

# For each trade:
if result == "WIN":
    # OLD: pnl_mult = 0.45
    # NEW: Apply ticker-specific and expiry-specific adjustment

    base_pnl = 0.45
    ticker_adj = adjustments['pnl_adjustments'][f'{days_to_expiry}_day'][ticker]
    spread_cost = adjustments['spread_costs'][ticker]
    slippage = adjustments['slippage_pct'][ticker]
    commission = adjustments['commission_per_contract']

    # Adjust P&L
    adjusted_pnl = base_pnl * ticker_adj
    adjusted_pnl -= spread_cost  # Subtract spread cost
    adjusted_pnl -= slippage     # Subtract slippage
    adjusted_pnl -= (commission / 1000)  # Commission on $1k position

    pnl_mult = adjusted_pnl
```

### Re-run Backtest with Adjustments

```bash
python run_backtest_ig_short_expiries_v2.py --use-reality-adjustments
```

Expected Output:
```
BACKTEST RESULTS (WITH REALITY ADJUSTMENTS)
============================================

Original Backtest:
  CAGR: 64.8%
  Win Rate: 80.9%
  Final Equity: $1,439,425

Adjusted Backtest (using paper trading data):
  CAGR: 42.3%
  Win Rate: 77.6%
  Final Equity: $687,250

Adjustment Impact: -34.7% CAGR
  - Spread costs: -8.2%
  - Slippage: -3.5%
  - Theta decay (real): -12.1%
  - Win rate adjustment: -5.3%
  - Commission: -1.9%
```

---

## 🔄 Continuous Improvement Loop

### Monthly Process

1. **Month 1:** Collect 20-30 paper trades
2. **Month 2:** Update adjustments, re-run backtest
3. **Month 3:** Compare Month 2 predictions vs Month 2 actuals
4. **Month 4:** Fine-tune adjustments

### Target Accuracy

**Goal:** Backtest predicts actual results within ±10%

**Metrics:**
- CAGR prediction: Within ±3pp
- Win rate prediction: Within ±2pp
- P&L per trade: Within ±10%

---

## 📊 Critical Measurements to Track

### 1. Bid/Ask Spreads (Daily)
**Importance:** HIGH
**Impact:** 3-15% of trade cost

**Measurement:**
- Record at 16:00 ET daily
- Calculate: (Ask - Bid) / Mid
- Track by ticker and expiry

**Expected Ranges:**
- SPY: 2-4%
- QQQ: 4-6%
- IWM: 8-12%
- DIA: 10-15%

### 2. Fill Quality (Per Trade)
**Importance:** HIGH
**Impact:** 1-5% of trade cost

**Measurement:**
- Filled price vs mid price
- Time to fill
- Partial fills / rejections

**Expected:**
- Fill within 1-2% of mid
- Fill within 10 seconds
- 95%+ fill rate

### 3. Theta Decay (Per Trade)
**Importance:** CRITICAL
**Impact:** 10-30% of P&L

**Measurement:**
- Option price change vs underlying price change
- Time decay component
- Compare actual option P&L vs intrinsic value

**Expected:**
- 1-day: 5-10% theta
- 2-day: 8-15% theta
- 3-day: 12-20% theta

### 4. Win Rate Accuracy (Weekly)
**Importance:** HIGH
**Impact:** Overall strategy viability

**Measurement:**
- Predicted wins vs actual wins
- By ticker, by expiry type

**Expected:**
- Backtest: 78-90% WR
- Actual: 70-85% WR
- Difference: -3 to -5pp

### 5. P&L Accuracy (Weekly)
**Importance:** CRITICAL
**Impact:** Overall returns

**Measurement:**
- Predicted P&L% vs actual P&L%
- By ticker, by result type

**Expected:**
- Wins: Actual 50-70% of predicted
- Losses: Actual 100-120% of predicted

---

## 🚨 Red Flags (Stop & Investigate)

### If Actual Results Are:

**Much WORSE than predicted:**
- Actual WR < Predicted WR - 10pp → CHECK: Are you entering at wrong time?
- Actual P&L < Predicted P&L - 30% → CHECK: Are spreads wider than measured?
- Many rejected orders → CHECK: Is IG.com platform issue?

**Much BETTER than predicted:**
- Actual WR > Predicted WR + 5pp → CHECK: Are you cherry-picking trades?
- Actual P&L > Predicted P&L + 20% → CHECK: Are you exiting at optimal time vs expiry?

### Common Issues:

1. **Entry Timing:** Not entering exactly at 16:00 (slippage)
2. **Spread Widening:** After-hours spreads wider than market hours
3. **Partial Fills:** Not getting full position
4. **Platform Issues:** IG.com downtime or requotes
5. **Human Error:** Wrong strike, wrong expiry, wrong size

---

## ✅ Success Criteria

### After 3 Months Paper Trading

**Acceptable if:**
- Actual CAGR is 60-80% of backtest CAGR
- Actual WR is within 5pp of backtest WR
- Can explain all major discrepancies
- Adjustment factors are stable (±10% month-to-month)

**Excellent if:**
- Actual CAGR is 70-90% of backtest CAGR
- Actual WR is within 3pp of backtest WR
- Discrepancies are predictable and accounted for
- Ready for live trading with confidence

**Warning if:**
- Actual CAGR < 50% of backtest CAGR
- Actual WR < Backtest WR - 10pp
- Large unexplained discrepancies
- High variance in adjustment factors

---

## 📁 Files & Tools Summary

### Created Tools:
1. **measure_reality_framework.py** - Option pricing calculator
2. **paper_trading_log.py** - Logging and comparison framework
3. **config/reality_adjustments.json** - Adjustment parameters

### Logs Generated:
1. **logs/paper_trades.csv** - All actual paper trades
2. **logs/backtest_predictions.csv** - Backtest predictions
3. **logs/discrepancies.csv** - Actual vs predicted differences

### Usage Flow:
```
Morning → log_backtest_prediction()
16:00 ET → log_paper_trade_entry()
Next Day → log_paper_trade_exit()
Evening → compare_actual_vs_backtest()
Sunday → calculate_adjustment_factors()
Monthly → Update backtest with adjustments
```

---

## 🎯 Final Notes

### For IG.com Integration (Phase 2/3):

**Order Retry Logic:**
```python
def place_order_with_retry(order_params, max_retries=2):
    """
    Place order on IG.com with automatic retry on rejection

    Retry reasons:
    - Temporary platform issue
    - Price moved (requote)
    - Insufficient margin (edge case)
    """
    for attempt in range(max_retries + 1):
        try:
            result = ig_api.place_order(**order_params)

            if result['status'] == 'ACCEPTED':
                log_success(result)
                return result
            else:
                log_rejection(result, attempt)
                if attempt < max_retries:
                    time.sleep(1)  # Wait 1 second before retry
                    continue
                else:
                    log_final_failure(result)
                    return None

        except Exception as e:
            log_error(e, attempt)
            if attempt < max_retries:
                time.sleep(2)
                continue
            else:
                return None
```

**Document all rejections** in separate log for analysis.

---

**Bottom Line:** After 3 months of calibration, you'll have a backtest that predicts reality within 10%, giving you confidence for live trading!
