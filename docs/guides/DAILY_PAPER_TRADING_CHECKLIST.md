# Daily Paper Trading Checklist

## 📅 Daily Workflow for IG.com Paper Trading

**Goal:** Track every trade to calibrate backtest with reality

**Time Required:** 15-20 minutes per day

---

## ☀️ MORNING (Before 15:00 UK / 10:00 ET)

### Step 1: Check Day of Week
```
Date: __________
Day: [ ] Mon  [ ] Tue  [ ] Wed  [ ] Thu  [ ] Fri

Trading Today?
[ ] YES (All days trade for IG Short 1-3d strategy)
[ ] NO (Weekend/Holiday)
```

### Step 2: Get Backtest Prediction

**Run prediction script:**
```bash
cd C:/Users/balaj/OneDrive/Trading/OvernightFade
python daily_backtest_predictor.py --date TODAY
```

**OR manually check backtest for today's date:**
- Open `results/ig_short_expiries_backtest.csv`
- Find today's date
- Note predictions

**Record Predictions:**
```
Ticker: _______
Signal: [ ] BUY CALL  [ ] BUY PUT  [ ] NO TRADE
Entry Price (predicted): $_______
Strike: _______
Target Price: $_______
Expiry Date: _______
Days to Expiry: [ ] 1  [ ] 2  [ ] 3
Predicted Win Prob: _____%
Predicted P&L: +45%
```

**Log to system:**
```python
from paper_trading_log import log_backtest_prediction

log_backtest_prediction(
    date='YYYY-MM-DD',
    ticker='____',
    signal='____',
    entry_price=____,
    strike=____,
    target_price=____,
    expiry_date='YYYY-MM-DD',
    days_to_expiry=_,
    predicted_win_prob=__,
    predicted_pnl_pct=45
)
```

✅ **Morning prep complete!**

---

## 🕐 PRE-MARKET (15:45-15:59 UK / 10:45-10:59 ET)

### Step 3: Review Market Conditions

**Check for abnormal events:**
```
[ ] Earnings announcements today?
[ ] Fed announcement / major economic data?
[ ] Unusual pre-market volatility (>1%)?
[ ] Any news affecting SPY/QQQ/IWM/DIA?

If YES to any: [ ] Consider skipping today
                [ ] Proceed with caution
```

**Set Alerts:**
```
[ ] IG.com platform open and logged in
[ ] Demo account selected
[ ] Alarm set for 15:58 UK (10:58 ET)
[ ] This checklist ready
```

✅ **Pre-market prep complete!**

---

## 🎯 EXECUTION (16:00 UK / 21:00 ET) - CRITICAL TIMING!

### Step 4: Record Live Market Data (16:00:00 - 16:00:30)

**At exactly 16:00 ET, record:**

**Underlying Prices:**
```
Time: 16:00:__ ET

SPY: $_______
QQQ: $_______
IWM: $_______
DIA: $_______
```

**Option Quotes (for tickers you're trading):**
```
Ticker: _______
Strike: _______
Expiry: _______

Bid: $_______
Ask: $_______
Mid: $_______
Spread: ____% [(Ask-Bid)/Mid * 100]
```

### Step 5: Place Paper Trade (16:00:30 - 16:02:00)

**Order Entry:**
```
Ticker: _______
Option Type: [ ] CALL  [ ] PUT
Strike: _______
Expiry: _______
Order Type: [ ] Market  [ ] Limit at $_____
Contracts: 1 (or _____)
```

**Order Submission:**
```
[ ] Double-check: Right ticker, right strike, right expiry
[ ] Submit order
[ ] Screenshot of order confirmation
```

**Order Fill:**
```
Order ID: __________
Fill Time: 16:__:__ ET
Filled Price: $_______
Underlying at Fill: $_______
Slippage: $______ [(Filled - Mid)]
Slippage %: ____% [(Filled - Mid) / Mid * 100]
Commission: $_______
```

### Step 6: Log Actual Entry

**Immediate logging:**
```python
from paper_trading_log import log_paper_trade_entry

log_paper_trade_entry(
    date='YYYY-MM-DD',
    ticker='____',
    signal='BUY CALL/PUT',
    option_type='CALL/PUT',
    strike=____,
    expiry_date='YYYY-MM-DD',
    days_to_expiry=_,
    entry_time='16:00:__',
    underlying_price_at_entry=____,
    bid=____,
    ask=____,
    mid=____,
    filled_price=____,
    contracts=1,
    commission=0.65,
    order_id='____',
    notes='____'
)
```

**Quick Check:**
```
[ ] Entry logged successfully
[ ] Screenshots saved
[ ] Spreadsheet updated (if using manual tracking)
```

✅ **Execution complete!**

---

## 📊 MONITORING (16:01 ET - Next Day 09:30 ET)

### Step 7: Set Position Alerts

**In IG.com platform:**
```
[ ] Set alert at target price: $_______
[ ] Set alert at -50% loss (optional)
[ ] Set alert for expiry day 09:00 ET
```

**Monitoring schedule:**
```
DAY 1 (Today):
[ ] 17:00 ET: Quick check (30 min after entry)
[ ] 20:00 ET: Evening check
[ ] Before bed: Final check

DAY 2+ (Holding period):
[ ] 09:00 ET: Pre-market check
[ ] 12:00 ET: Midday check
[ ] 15:30 ET: Pre-close check

Notes:
_________________________________
_________________________________
```

### Step 8: Track Target Progress

**Daily updates (if holding overnight):**
```
Date: ____ Time: ____
Underlying: $_______
Current Option Bid/Ask: $_____ / $_____
Progress to Target: ____%
Status: [ ] On track  [ ] Target hit  [ ] Fading
```

✅ **Monitoring active!**

---

## 🏁 EXIT (When Target Hit or Expiry)

### Step 9: Close Position

**Exit Decision:**
```
Exit Trigger:
[ ] Target hit (underlying reached target price)
[ ] Time stop (expiry approaching, no target hit)
[ ] Manual close (your discretion)

Exit Time: ____:__:__ ET
Exit Date: __________
```

**Record Exit Quotes:**
```
Underlying at Exit: $_______
Option Bid: $_______
Option Ask: $_______
Option Mid: $_______
```

**Exit Order:**
```
[ ] Sell to close - Market order
[ ] Sell to close - Limit at $_____
```

**Exit Fill:**
```
Fill Time: ____:__:__ ET
Fill Price: $_______
Exit Commission: $_______
```

### Step 10: Calculate Actual P&L

**P&L Calculation:**
```
Entry Premium: $_______ (filled price)
Exit Premium: $_______ (filled price)
Gross P&L: $_______ [(Exit - Entry) × Contracts]
Total Commission: $_______ (Entry + Exit)
Net P&L: $_______
Net P&L %: _____% [(Net P&L / Entry Cost) × 100]

Result: [ ] WIN  [ ] LOSS
```

### Step 11: Log Exit

**Log to system:**
```python
from paper_trading_log import log_paper_trade_exit

log_paper_trade_exit(
    trade_id='YYYY-MM-DD_TICKER_CALL/PUT',
    exit_time='YYYY-MM-DD HH:MM:SS',
    exit_price=____,
    exit_underlying=____,
    result='WIN/LOSS',
    exit_commission=0.65,
    notes='Target hit at ____ / Expired worthless / etc'
)
```

✅ **Exit logged!**

---

## 🌙 EVENING REVIEW (After Trade Closes)

### Step 12: Compare Actual vs Predicted

**Run comparison:**
```python
from paper_trading_log import compare_actual_vs_backtest

compare_actual_vs_backtest('YYYY-MM-DD')
```

**Manual comparison:**
```
BACKTEST PREDICTION:
- Entry: $_______
- Target: $_______
- Win Prob: _____%
- P&L: +45%

ACTUAL RESULT:
- Entry: $_______ (diff: $_______)
- Exit: $_______
- Result: WIN/LOSS
- P&L: _____% (diff: _____pp)

DISCREPANCIES:
- Spread cost: ____%
- Slippage: ____%
- Theta impact: Estimated ____%
- Other: _________________
```

### Step 13: Lessons Learned

**Daily reflection:**
```
What went well:
_________________________________
_________________________________

What went wrong:
_________________________________
_________________________________

Adjustments for tomorrow:
_________________________________
_________________________________

Questions/Issues:
_________________________________
_________________________________
```

### Step 14: Update Tracking Sheet

**Spreadsheet update (if manual tracking):**
```
[ ] Trade details entered
[ ] P&L calculated
[ ] Comparison notes added
[ ] Screenshots attached/referenced
```

✅ **Daily review complete!**

---

## 📅 WEEKLY REVIEW (Every Sunday)

### Step 15: Calculate Adjustment Factors

**Run weekly analysis:**
```python
from paper_trading_log import calculate_adjustment_factors

calculate_adjustment_factors()
```

**Review metrics:**
```
WEEK OF: __________

Total Trades: ____
Wins: ____ (___%)
Losses: ____ (___%)

Average Spread:
- SPY: ____%
- QQQ: ____%
- IWM: ____%
- DIA: ____%

Average Slippage: ____%

Average P&L (Wins): ____%
Average P&L (Losses): ____%

Predicted WR: ____%
Actual WR: ____%
Difference: _____pp

Predicted Avg P&L: +45%
Actual Avg P&L: ____%
Adjustment Factor: ____x
```

### Step 16: Identify Patterns

**Weekly patterns:**
```
Best performing:
[ ] 1-day expiries (Tue/Thu)
[ ] 2-day expiries (Mon/Wed)
[ ] 3-day expiries (Fri)

Best ticker: _______
Worst ticker: _______

Common issues this week:
_________________________________
_________________________________

Action items for next week:
_________________________________
_________________________________
```

### Step 17: Update Strategy (If Needed)

**Strategy adjustments:**
```
Based on this week's data:

[ ] Consider dropping ticker: _______
    Reason: _______________________

[ ] Focus on: _______
    Reason: _______________________

[ ] Spread costs higher than expected
    [ ] Measured: ____%
    [ ] Update backtest assumption

[ ] Slippage pattern identified
    [ ] Time of day issue
    [ ] Platform issue
    [ ] Other: _______
```

✅ **Weekly review complete!**

---

## 📊 MONTHLY REVIEW (End of Month)

### Step 18: Comprehensive Analysis

**Month: __________**

```
Total Trading Days: ____
Total Trades: ____
Win Rate: ____%

By Expiry Type:
- 1-day: ____ trades, ___% WR
- 2-day: ____ trades, ___% WR
- 3-day: ____ trades, ___% WR

By Ticker:
- SPY: ____ trades, ___% WR, ___% avg P&L
- QQQ: ____ trades, ___% WR, ___% avg P&L
- IWM: ____ trades, ___% WR, ___% avg P&L
- DIA: ____ trades, ___% WR, ___% avg P&L

Backtest Accuracy:
- Predicted CAGR: ____%
- Actual CAGR: ____%
- Accuracy: ____%
```

### Step 19: Update Backtest Parameters

**Create/update:** `config/reality_adjustments.json`

```json
{
  "month": "____",
  "spread_costs": {
    "SPY": ____,
    "QQQ": ____,
    "IWM": ____,
    "DIA": ____
  },
  "pnl_adjustments": {
    "1_day": {"SPY": ____, "QQQ": ____, "IWM": ____, "DIA": ____},
    "2_day": {"SPY": ____, "QQQ": ____, "IWM": ____, "DIA": ____},
    "3_day": {"SPY": ____, "QQQ": ____, "IWM": ____, "DIA": ____}
  }
}
```

### Step 20: Decision Point

**After Month 1:**
```
[ ] Continue paper trading (need more data)
[ ] Adjust strategy (drop low-performing tickers)
[ ] Ready for live trading (results match backtest within 10%)
```

✅ **Monthly review complete!**

---

## 📱 Quick Reference Card

**Print this and keep at your desk:**

```
┌─────────────────────────────────────────────┐
│  DAILY PAPER TRADING - QUICK CHECKLIST      │
├─────────────────────────────────────────────┤
│                                             │
│  MORNING (Before 15:00 UK):                 │
│  [ ] Get backtest prediction                │
│  [ ] Log prediction to system               │
│                                             │
│  15:58 UK / 10:58 ET:                       │
│  [ ] IG.com open, demo account              │
│  [ ] Checklist ready                        │
│                                             │
│  16:00:00 ET (EXACTLY!):                    │
│  [ ] Record underlying prices               │
│  [ ] Record option bid/ask/mid              │
│  [ ] Calculate spread %                     │
│                                             │
│  16:00:30 ET:                               │
│  [ ] Place paper trade order                │
│  [ ] Screenshot confirmation                │
│  [ ] Record fill price & slippage           │
│  [ ] Log entry to system                    │
│                                             │
│  MONITORING:                                │
│  [ ] Set alerts at target price             │
│  [ ] Check progress 2-3x daily              │
│                                             │
│  AT EXIT:                                   │
│  [ ] Record exit bid/ask/fill               │
│  [ ] Calculate P&L                          │
│  [ ] Log exit to system                     │
│                                             │
│  EVENING:                                   │
│  [ ] Run comparison script                  │
│  [ ] Note discrepancies                     │
│  [ ] Update tracking sheet                  │
│                                             │
│  SUNDAY:                                    │
│  [ ] Calculate weekly adjustment factors    │
│  [ ] Review patterns                        │
│  [ ] Plan next week                         │
│                                             │
└─────────────────────────────────────────────┘
```

---

## 🎯 Success Metrics

**After 1 Week:**
- [ ] 5 trades completed
- [ ] All trades logged correctly
- [ ] Spread costs measured

**After 1 Month:**
- [ ] 20+ trades completed
- [ ] Adjustment factors calculated
- [ ] Backtest accuracy within 20%

**After 3 Months:**
- [ ] 60+ trades completed
- [ ] Stable adjustment factors
- [ ] Backtest accuracy within 10%
- [ ] Ready for live trading decision

---

## 📞 Troubleshooting

**If you miss 16:00 ET entry:**
```
[ ] Don't panic
[ ] Record what happened
[ ] Note time of actual entry
[ ] Log as "LATE ENTRY" in notes
[ ] Still complete the trade for data
```

**If order is rejected:**
```
[ ] Screenshot rejection message
[ ] Note reason (if given)
[ ] Try once more (max 2 attempts total)
[ ] If still rejected, log as "REJECTED"
[ ] Document in notes for weekly review
```

**If you forget to log:**
```
[ ] Reconstruct from IG.com history
[ ] Note as "RECONSTRUCTED" in logs
[ ] Better late than never!
```

---

**Print this checklist and follow daily for 3 months to achieve backtest-reality alignment!**
