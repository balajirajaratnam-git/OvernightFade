# Massive.com Upgrade Instructions (Updated 2026)

## 🎯 Plan Comparison

| Plan | Cost | Historical Data | API Calls | Best For |
|------|------|----------------|-----------|----------|
| **Stocks Basic** | FREE | 2 years | 5/min | Current plan |
| **Stocks Starter** ⭐ | $29/mo | **5 years** | Unlimited | **RECOMMENDED** |
| **Stocks Developer** | $79/mo | 10 years | Unlimited | Maximum confidence |

---

## 🎯 Recommended: **Stocks Starter ($29/month)**

### Why Starter?

**What you get:**
- ✅ 5 years of historical data (2020-2026)
- ✅ ~820 trades (vs 328 currently)
- ✅ Unlimited API calls (fast fetch, ~5-10 minutes)
- ✅ Covers COVID crash, 2022 bear market, bull markets
- ✅ **Best value for money**

**What you miss vs Developer ($79):**
- 2015-2019 data (additional 5 years)
- Real-time data (backtesting doesn't need this)
- Tick data and WebSockets (not needed for your strategy)

**Verdict:** Starter gives you everything needed at lowest cost.

---

## 📋 Step-by-Step Upgrade Process

### 1. Subscribe to Stocks Starter

1. Go to: **https://massive.com/pricing**
2. Find **"Stocks Starter"** plan
3. Price: **$29/month**
4. Features: 5 years historical, unlimited API calls
5. Click "Subscribe" and complete payment

---

### 2. Get Your New API Key

After subscribing:
1. Go to: **https://massive.com/dashboard**
2. Look for "API Keys" section
3. You should see your **Stocks Starter API key**
4. Copy it (long string like: `abc123xyz...`)

**Note:** This is different from your free tier key.

---

### 3. Update .env File

**Open:** `C:\Users\balaj\OneDrive\Trading\OvernightFade\.env`

**Edit line 3:**
```
# FROM (current free tier key):
POLYGON_API_KEY=0S8bHXwo6eGTRZOWLTunYrlZWE89_thy

# TO (your new Stocks Starter key):
POLYGON_API_KEY=paste_your_new_starter_key_here
```

**Save the file.**

---

### 4. Backup and Delete Old Data

**Backup current 2-year data:**
```bash
cd C:\Users\balaj\OneDrive\Trading\OvernightFade
copy data\SPY\daily_OHLCV.parquet data\SPY\daily_OHLCV.parquet.backup_2year
```

**Delete to force fresh 5-year fetch:**
```bash
del data\SPY\daily_OHLCV.parquet
```

---

### 5. Run Data Fetch

**Execute:**
```bash
python run_data_fetch.py
```

**What happens:**
- Fetches daily data from 2020-01-01 to 2026-02-04
- Fetches minute data for ~1,250 trading days (5 years)
- With unlimited API calls: ~5-10 minutes total
- Progress shown in console

**Expected output:**
```
Checking Daily Data for SPY...
Fetching Daily from 2020-01-01...
Updated Daily Data. Last: 2026-02-03

Checking Intraday Data...
Fetching: [===================>] 1250/1250 days
Intraday sync complete.

Sync complete.
```

---

### 6. Verify Data

**Check daily data range:**
```bash
python -c "import pandas as pd; df = pd.read_parquet('data/SPY/daily_OHLCV.parquet'); print(f'Range: {df.index[0].year} to {df.index[-1].year}'); print(f'Total days: {len(df)}')"
```

**Expected:** 2020 to 2026, ~1,250 days

**Check intraday files:**
```bash
dir data\SPY\intraday /B | find /C ".parquet"
```

**Expected:** ~1,250 files

---

### 7. Run 5-Year Backtest

```bash
python src/backtester.py
```

**Expected results:**
- ~820 trades (vs 328 on 2 years)
- Date range: 2020 to 2026
- Should cover COVID crash, bear/bull markets

**Check results:**
```bash
python -c "import pandas as pd; df = pd.read_csv('results/trade_log_ORIGINAL.csv'); print(f'Total trades: {len(df)}'); print(f'Date range: {df.Date.min()} to {df.Date.max()}'); print(f'Win rate: {(df.Result==\"WIN\").sum()/len(df)*100:.1f}%'); print(f'Total P/L: ${df.PnL_Dollar.sum():,.2f}'); print(f'Avg per trade: ${df.PnL_Dollar.mean():.2f}')"
```

---

### 8. Analyze Results

**Questions to ask:**

1. **Is win rate consistent?**
   - Similar to your current 88.4%?
   - Any periods of bad performance?

2. **How did it perform during COVID (March 2020)?**
   - Check trades around 2020-03-15 to 2020-04-01
   - High volatility period

3. **How about 2022 bear market?**
   - Check trades in 2022
   - Sustained downtrend

4. **Is total P/L positive?**
   - Should be ~2.5x your current $9,060 = ~$22,650
   - If similar per-trade profit

**View specific periods:**
```python
import pandas as pd
df = pd.read_csv('results/trade_log_ORIGINAL.csv')

# COVID crash period
covid = df[(df['Date'] >= '2020-03-01') & (df['Date'] <= '2020-04-30')]
print(f"COVID period: {len(covid)} trades, Win rate: {(covid.Result=='WIN').sum()/len(covid)*100:.1f}%")

# 2022 bear market
bear = df[(df['Date'] >= '2022-01-01') & (df['Date'] <= '2022-12-31')]
print(f"2022 bear: {len(bear)} trades, Win rate: {(bear.Result=='WIN').sum()/len(bear)*100:.1f}%")
```

---

### 9. Cancel Subscription

**After verifying data is good:**

1. Go to: **https://massive.com/dashboard**
2. Navigate to "Billing" or "Subscription"
3. Click "Cancel Subscription"
4. Confirm cancellation

**Important:**
- ✅ You keep all downloaded data permanently
- ✅ Data is stored locally in your `data/SPY/` folder
- ✅ No more charges after current billing cycle ends
- ✅ You can re-run backtests anytime with this data

**When to cancel:**
- Immediately after fetch (you've paid for the month)
- Or wait until end of month (already paid anyway)
- Either way, cancel before next billing cycle

---

## 🔧 Configuration Files (Already Updated)

**src/data_manager.py:**
- Daily data: Fetches from 2020-01-01 ✅
- Intraday data: Fetches 5 years (1,825 days) ✅

**config/config.json:**
- lookback_years: 5 ✅
- max_requests_per_run: 5000 ✅
- max_requests_per_minute: 90 ✅

**No code changes needed** - just update your API key!

---

## 📊 Expected Results

| Metric | Before (Free) | After (Starter) |
|--------|---------------|-----------------|
| Time period | 2024-2026 | 2020-2026 |
| Years | 2 | 5 |
| Trading days | ~500 | ~1,250 |
| Total trades | 328 | ~820 |
| Win rate | 88.4% | TBD (should be similar) |
| Total P/L | $9,060 | TBD (~$22,650 if consistent) |
| Data size | 75 MB | ~190 MB |
| Fetch time | N/A | ~5-10 minutes |
| **Cost** | $0 | **$29 one-time** |

---

## ⚠️ Troubleshooting

### "NOT_AUTHORIZED" Error

**Problem:** Still getting 403 errors after subscribing

**Solutions:**
1. ✅ Verify you subscribed to **Stocks Starter** (not Basic)
2. ✅ Double-check you copied the correct API key from dashboard
3. ✅ Verify `.env` file was saved after editing
4. ✅ Try restarting terminal/command prompt
5. ✅ Check spelling of API key (no extra spaces)

---

### Data Fetch Interrupted

**Problem:** Script stopped mid-fetch (power loss, etc)

**Solution:**
- Progress is automatically saved in `logs/state.json`
- Just run again: `python run_data_fetch.py`
- Will resume from last successful fetch
- Already-fetched files are skipped automatically

---

### Rate Limit Errors (Unlikely with Unlimited)

**Problem:** "Rate limit exceeded" message

**Solution:**
- Stocks Starter has unlimited calls
- If you see this, you might still be using free tier key
- Re-check your `.env` has the new Starter key

---

### Missing Data for Some Days

**Normal:** Market holidays, weekends, half-days
- Your backtester automatically skips these
- Not an error

---

## 🤔 Should I Upgrade to Developer ($79)?

**Consider Developer if:**
- ✅ You want 10 years instead of 5 (2015-2026)
- ✅ You're trading with $50k+ capital
- ✅ Extra $50 is trivial to you
- ✅ You want maximum statistical confidence
- ✅ 5-year results look good and you want more validation

**Stick with Starter if:**
- ✅ 5 years is sufficient for validation
- ✅ You want to save $50
- ✅ 820 trades is enough statistical significance
- ✅ You can always upgrade later if needed

**My take:** Start with Starter, see results, then decide if Developer is worth extra $50.

---

## 📋 Quick Command Reference

**Subscribe:**
```
https://massive.com/pricing → Stocks Starter ($29/mo)
```

**Update API key:**
```
Edit: .env (line 3)
```

**Fetch 5 years of data:**
```
python run_data_fetch.py
```

**Run backtest:**
```
python src/backtester.py
```

**View results:**
```
results/trade_log_ORIGINAL.csv
```

**Cancel subscription:**
```
https://massive.com/dashboard → Billing → Cancel
```

---

## ✅ Final Checklist

Before canceling subscription:

- [ ] Subscribed to Stocks Starter ($29/mo)
- [ ] Updated .env with new API key
- [ ] Deleted old daily_OHLCV.parquet
- [ ] Ran: `python run_data_fetch.py`
- [ ] Verified: Daily data from 2020-2026
- [ ] Verified: ~1,250 intraday files
- [ ] Ran: `python src/backtester.py`
- [ ] Verified: ~820 trades in results
- [ ] Analyzed: Results look reasonable
- [ ] Ready to cancel subscription

---

## 💰 Total Cost Summary

**Stocks Starter Plan:**
- Monthly cost: $29
- Duration needed: 1 month
- Data retained: Forever (stored locally)
- **Total investment: $29**

**What you get:**
- 5 years of professional market data
- 820 trades worth of backtest validation
- Coverage of COVID, bear/bull markets
- Confidence in strategy before live trading
- Can rerun backtests anytime

**Worth it?** If you're planning to trade this strategy with real money, $29 to validate 5 years is an excellent investment.

---

*Keep this file for reference during the upgrade process*
