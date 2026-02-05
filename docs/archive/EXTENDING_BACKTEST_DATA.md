# Extending Backtest Data Beyond 2 Years

**Complete guide to fetching historical data for extended backtesting**

*Created: 2026-02-04*

---

## 📊 Current Setup

**Data range:** 2 years (2023-01-01 to present)
**Data source:** Polygon.io Free Tier
**API limits:** 5 calls/minute, ~250 calls/day
**Storage used:** ~75 MB

---

## 🎯 Recommended Approach by Budget

### FREE ($0) - Use Polygon Free Tier
- **Daily data:** 5-10 years available
- **Minute data:** 2 years max (API restriction)
- **Time required:** 3-5 days (due to rate limits)
- **Best for:** Extending daily data, testing with 2 years of minute data

### FAST ($29 one-time) - Polygon Starter for 1 Month
- **Daily data:** 10+ years
- **Minute data:** 5-10 years
- **Time required:** 1-2 hours
- **Best for:** Need 5+ years of minute-level data quickly
- **How:** Subscribe, fetch all data, cancel immediately

---

## 🔍 Understanding Data Types

### Daily Data (Most Important)
**What:** Open, High, Low, Close, Volume per trading day
**Used for:** Signal generation (fade green/red days)
**Polygon free tier limit:** Unlimited history
**Storage:** ~250 KB per year

### Minute Data (Secondary)
**What:** OHLCV for each minute of trading
**Used for:** Precise entry/exit timing, win time analysis
**Polygon free tier limit:** ~2 years max
**Storage:** ~37 MB per year

### Which Do You Actually Need?

**For overnight fade strategy:**
1. ✅ **Daily bars** - CRITICAL (signal generation)
2. ⚠️ **Minute bars** - OPTIONAL (precise timing)

**You can backtest with daily data only** if you:
- Accept approximate entry/exit times
- Don't need exact win time analysis
- Want to test 5+ years quickly on free tier

---

## 📋 Step-by-Step: Free Tier (0-2 Years of Minute Data)

### Step 1: Modify data_manager.py

**Current code** (line 262):
```python
start_date = "2023-01-01"
```

**Change to:**
```python
# For 10 years of daily data (free tier)
start_date = "2015-01-01"  # Or earlier

# Note: Minute data still limited to ~2 years
# Daily data has no limit on free tier
```

### Step 2: Run Daily Data Fetch

```bash
ALLOW_NETWORK=1 python src/data_manager.py
```

**Expected:**
- Fetches daily bars from 2015 (or earlier) to present
- Takes ~1 API call total
- Completes in seconds
- Updates `data/SPY/daily_OHLCV.parquet`

### Step 3: Update config.json

```json
{
    "lookback_years": 10,
    ...
}
```

This tells the backtester to use 10 years of data.

### Step 4: Run Backtest

```bash
python src/backtester.py
```

**Result:** Backtest runs on 10 years of daily signal data (using daily open/close for entries/exits)

### Limitations

**Free tier minute data restriction:**
- Intraday data limited to last ~2 years
- Can get 10+ years of daily, but only 2 years of minute bars
- Older trades will use daily-level approximations

---

## 📋 Step-by-Step: Paid Tier (5-10 Years of Minute Data)

**Cost:** $29/month (cancel after fetching)

### Step 1: Upgrade Polygon Plan

1. Go to https://polygon.io/pricing
2. Subscribe to "Starter" plan ($29/month)
3. Copy your new API key
4. Update `.env`:
   ```
   POLYGON_API_KEY=your_new_starter_api_key
   ```

### Step 2: Update Rate Limits in config.json

```json
{
    "max_requests_per_run": 5000,
    "max_requests_per_minute": 90,
    ...
}
```

**Starter tier limits:** 100 calls/minute (we use 90 for safety)

### Step 3: Modify data_manager.py for Historical Intraday

**Current code** (line 326):
```python
start_date = datetime.now() - timedelta(days=60)
```

**Change to:**
```python
# For 5 years of minute data (paid tier)
start_date = datetime.now() - timedelta(days=365 * 5)
```

**And update range** (line 329):
```python
for i in range(365 * 5):  # Changed from 60 to 5 years
```

### Step 4: Run Full Historical Fetch

```bash
ALLOW_NETWORK=1 python src/data_manager.py
```

**Expected:**
- Fetches ~1,250 trading days of minute data
- At 90 calls/min: ~15 minutes total
- Storage: ~187 MB for 5 years

### Step 5: Verify Data

```python
import os
files = os.listdir("data/SPY/intraday")
print(f"Intraday files: {len(files)}")
print(f"Date range: {min(files)} to {max(files)}")
```

**Should show:** ~1,250 files spanning 5 years

### Step 6: Cancel Subscription

1. Go to Polygon.io account settings
2. Cancel subscription
3. **Keep the data** - it's yours permanently

**Total cost:** $29 one-time

---

## 💾 Storage Requirements

| Data Range | Daily Data | Minute Data | Total |
|------------|-----------|-------------|-------|
| 2 years | ~500 KB | ~75 MB | ~75 MB |
| 5 years | ~1.2 MB | ~187 MB | ~188 MB |
| 10 years | ~2.5 MB | ~375 MB | ~377 MB |

**Recommendation:** Ensure 500 MB+ free disk space

---

## 🔧 Code Modifications Summary

### For Daily Data Only (Free Tier, 10+ Years)

**File:** `src/data_manager.py`

**Change line 262:**
```python
# FROM:
start_date = "2023-01-01"

# TO:
start_date = "2015-01-01"  # Or "2010-01-01" for 15 years
```

**File:** `config/config.json`

**Change:**
```json
{
    "lookback_years": 10
}
```

**That's it!** Run: `ALLOW_NETWORK=1 python src/data_manager.py`

---

### For Minute Data (Paid Tier, 5+ Years)

**File:** `src/data_manager.py`

**Change line 262:** (same as above)
```python
start_date = "2015-01-01"
```

**Change line 326-329:**
```python
# FROM:
start_date = datetime.now() - timedelta(days=60)
for i in range(60):

# TO:
start_date = datetime.now() - timedelta(days=365 * 5)  # 5 years
for i in range(365 * 5):
```

**File:** `config/config.json`

**Change:**
```json
{
    "lookback_years": 10,
    "max_requests_per_run": 5000,
    "max_requests_per_minute": 90
}
```

**Run:** `ALLOW_NETWORK=1 python src/data_manager.py`

---

## ⚠️ Important Notes

### API Key Permissions

**Free tier keys** may be restricted from accessing data older than 2 years for minute bars. If you get errors:
- Polygon may return empty results for old dates
- This is expected on free tier
- Daily data should work regardless

### Rate Limit Safety

**Free tier:**
- Fetching 5+ years of minute data will take **multiple days**
- Rate limit: 5 calls/min = 300 calls/hour = 7,200 calls/day
- 1,250 trading days = 1,250 API calls = ~2 days minimum
- `logs/state.json` tracks progress - safe to stop/resume

**Paid tier:**
- Can fetch 5 years in ~15 minutes
- Much more efficient if you need data quickly

### Incremental Fetching

Your code already supports **incremental fetching**:
- Already cached files are skipped
- Can stop/resume anytime
- Progress saved in `logs/state.json`

**Safe workflow:**
```bash
# Day 1: Fetch until rate limit
ALLOW_NETWORK=1 python src/data_manager.py

# Day 2: Resume from where you left off
ALLOW_NETWORK=1 python src/data_manager.py

# Day 3: Continue...
ALLOW_NETWORK=1 python src/data_manager.py
```

---

## 🧪 Testing the Extended Data

### Step 1: Verify Daily Data Range

```python
import pandas as pd

df = pd.read_parquet("data/SPY/daily_OHLCV.parquet")
print(f"Daily data range: {df.index[0]} to {df.index[-1]}")
print(f"Total trading days: {len(df)}")
```

**Expected:** Should match your target range (e.g., 2015 to 2026)

### Step 2: Verify Intraday Data Files

```bash
# Windows
dir data\SPY\intraday /B | find /C ".parquet"

# Count expected: ~250 per year
```

### Step 3: Run Backtest on Extended Data

```bash
python src/backtester.py
```

**Check results:**
- Should show more trades (proportional to years added)
- Results saved to `results/trade_log_ORIGINAL.csv`

```python
import pandas as pd
df = pd.read_csv("results/trade_log_ORIGINAL.csv")
print(f"Total trades: {len(df)}")
print(f"Date range: {df['Date'].min()} to {df['Date'].max()}")
```

---

## 🎓 Advanced: Mixing Daily and Minute Data

**Strategy for maximum free tier usage:**

1. **Fetch 10 years of daily data** (free, unlimited)
2. **Fetch 2 years of minute data** (free, limited)
3. **Backtest logic:**
   - Use minute data when available (last 2 years)
   - Use daily approximations for older trades (2-10 years ago)

**Implementation:**
Your code already handles this automatically:
- If intraday file exists → use minute-level timing
- If intraday file missing → approximate with daily OHLC

**No code changes needed** - it works out of the box!

---

## 💡 Recommended Workflow

### Option A: Free Forever

1. Modify `start_date = "2015-01-01"` in data_manager.py
2. Set `lookback_years: 10` in config.json
3. Run: `ALLOW_NETWORK=1 python src/data_manager.py`
4. Get 10 years of daily data instantly
5. Backtest with daily-level precision (good enough for strategy validation)
6. **Cost:** $0

### Option B: One-Time $29 for Perfect Data

1. Subscribe to Polygon Starter ($29/month)
2. Modify code for 5 years of minute data (see above)
3. Run: `ALLOW_NETWORK=1 python src/data_manager.py`
4. Wait 15 minutes for full fetch
5. Cancel Polygon subscription
6. Keep the data forever
7. **Cost:** $29 one-time

### Option C: Gradual Free Approach

1. Set up for 5 years of data
2. Run fetcher daily for a week
3. Let rate limits naturally throttle
4. Progress saved automatically
5. After 1 week, you'll have 5 years of minute data
6. **Cost:** $0, **Time:** 1 week

---

## 📊 Data Quality Considerations

### SPY Data Availability

**Polygon.io has:**
- Daily SPY data back to ~1993
- Minute SPY data back to ~2004

**Practical limits:**
- Free tier: 2 years of minute data
- Paid tier: 10+ years of minute data
- Daily data: Unlimited on both tiers

### Data Completeness

**Expect gaps for:**
- Market holidays
- Weekends
- Half-days (early close)
- Historical data quality issues pre-2010

Your backtester **already handles missing data** gracefully - it skips days with no data.

---

## 🔍 Troubleshooting

### Error: "No results returned"

**Cause:** Free tier doesn't allow minute data beyond 2 years
**Solution:**
- Either upgrade to paid tier
- Or accept 2 years of minute data only

### Error: "Rate limit exceeded"

**Cause:** Hit daily API limit
**Solution:**
- Wait 24 hours
- Or upgrade to paid tier
- Progress is saved - just resume tomorrow

### Error: "Insufficient data for backtest"

**Cause:** Missing daily data file
**Solution:**
```bash
ALLOW_NETWORK=1 python src/data_manager.py
```

### Storage Issues

**Problem:** Running out of disk space
**Solution:**
- Archive old intraday files (see `data/README.md`)
- Keep only daily data (250 KB vs 375 MB for minute data)
- Daily-only backtesting is still valuable

---

## 📈 Expected Results by Data Range

| Data Range | Expected Trades | Storage | Fetch Time (Free) | Fetch Time (Paid) |
|------------|----------------|---------|-------------------|-------------------|
| 2 years | ~328 | 75 MB | 1 hour | 5 min |
| 5 years | ~820 | 188 MB | 2 days | 15 min |
| 10 years | ~1,640 | 377 MB | 4 days | 30 min |

**Your current results:** 328 trades on 2 years (matches expectation ✅)

---

## 🎯 Quick Decision Matrix

**I want...**

**"5+ years of data for $0"**
→ Use Option A (daily data only, free tier)

**"5+ years with exact minute-level timing"**
→ Use Option B ($29 one-time, paid tier for 1 month)

**"Maximum free data with patience"**
→ Use Option C (gradual fetch over 1 week)

**"Test if strategy works on more data first"**
→ Start with Option A (free daily data)
→ If strategy looks good, upgrade to Option B

---

## 📋 Quick Start Checklist

### For Free Daily-Only Approach (Recommended First Step)

- [ ] Backup current data: `cp -r data/SPY data/SPY.backup`
- [ ] Edit `src/data_manager.py` line 262: `start_date = "2015-01-01"`
- [ ] Edit `config/config.json`: `"lookback_years": 10`
- [ ] Run: `ALLOW_NETWORK=1 python src/data_manager.py`
- [ ] Verify: Check daily_OHLCV.parquet goes back to 2015
- [ ] Backtest: `python src/backtester.py`
- [ ] Review results in `results/trade_log_ORIGINAL.csv`

**Time required:** 10 minutes
**Cost:** $0

---

## 🔗 Related Documentation

- **data/README.md** - Data structure and storage
- **config/README.md** - Rate limit configuration
- **logs/README.md** - Understanding state.json and rate limiting

---

*This guide will get you from 2 years to 10+ years of backtest data using the most cost-effective methods*
