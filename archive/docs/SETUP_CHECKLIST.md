# Setup Checklist: Automated Trading System

**Complete these steps to get started**

---

## ☐ Step 1: Read Documentation (5 min)

Start with ONE of these (pick based on your style):

- **Quick overview?** → `AUTOMATED_TRADING_SUMMARY.md` (what was built, why, how it works)
- **Get started fast?** → `QUICK_START_AUTOMATED.md` (5-min setup, no theory)
- **Full details?** → `AUTOMATED_TRADING_GUIDE.md` (complete guide with examples)

---

## ☐ Step 2: Install Dependencies (2 min)

```bash
pip install trading-ig yfinance scipy rich
```

**Verify:**
```bash
python -c "import trading_ig; import yfinance; import scipy; print('✅ All dependencies installed')"
```

---

## ☐ Step 3: Get IG.com API Credentials (5 min)

**Where to find:**
1. Log into IG.com
2. Click your name (top right) → Settings
3. Navigate to: API → API Keys
4. Click "Create new key" or use existing
5. Note down:
   - API Key (long string)
   - Username (your login)
   - Password (your login password)
   - Account Number (found under My Accounts)

**Security note:** Demo account credentials are separate from live account.

---

## ☐ Step 4: Configure Credentials File (2 min)

Edit file: `config/ig_api_credentials.json`

```json
{
  "demo": {
    "api_key": "PASTE_YOUR_API_KEY_HERE",
    "username": "YOUR_IG_USERNAME",
    "password": "YOUR_IG_PASSWORD",
    "acc_type": "DEMO",
    "acc_number": "YOUR_DEMO_ACCOUNT_NUMBER"
  },
  "live": {
    "api_key": "LEAVE_THIS_FOR_NOW",
    "username": "LEAVE_THIS_FOR_NOW",
    "password": "LEAVE_THIS_FOR_NOW",
    "acc_type": "LIVE",
    "acc_number": "LEAVE_THIS_FOR_NOW"
  },
  "settings": {
    "use_demo": true,
    "target_premium_gbp": 100,
    "retry_attempts": 3,
    "retry_delay_seconds": 5
  }
}
```

**Important:**
- Only fill in the "demo" section for now
- Leave "live" section empty
- Keep `"use_demo": true`

---

## ☐ Step 5: Test Connection (1 min)

```bash
python scripts/trading/ig_connector.py
```

**Expected output:**
```
Connecting to IG.com DEMO account...
✅ Connected to IG.com DEMO successfully
Found market: US Tech 100 (Epic: IX.D.NASDAQ.IFE.IP)
✅ Market search PASSED
Disconnected from IG.com
✅ Connection test PASSED
```

**If it fails:**
- Check credentials are correct
- Verify demo account is active on IG.com
- Check internet connection
- See troubleshooting in `AUTOMATED_TRADING_GUIDE.md`

---

## ☐ Step 6: Test Trade Logger (1 min)

```bash
python scripts/trading/trade_logger.py
```

**Expected output:**
```
✅ Trade #1 logged to ig_paper_trades_2050.json
Retrieved trade: {...}

Trade Summary: IG_PAPER_2050
Total Trades:     1
✅ Logger test PASSED
```

---

## ☐ Step 7: Dry Run (Optional, 3 min)

Test the main script without actually trading:

```bash
python scripts/trading/auto_trade_ig_collect_data.py
```

**What happens:**
- Generates today's signal
- Shows theoretical prices
- Shows IBKR instructions
- Might show "IG option placement needs implementation" (OK for now)

**Purpose:** Make sure everything runs without errors.

---

## ☐ Step 8: Schedule First Real Trade (Tomorrow)

**When:** Tomorrow at 20:50 UK (if trading day)

**Command:**
```bash
python scripts/trading/auto_trade_ig_collect_data.py
```

**Set a reminder** on your phone/calendar for 20:50 UK.

---

## ☐ Step 9: Daily Routine (Next 2-3 Weeks)

**Every trading day at 20:50 UK:**
1. Run: `python scripts/trading/auto_trade_ig_collect_data.py`
2. Follow IBKR instructions if trading there
3. Enter IBKR fill data when prompted
4. Verify trades logged (check console output)

**Time:** 5-10 minutes per day

---

## ☐ Step 10: Calibration (After 10 Trades)

**When:** Script will tell you "Ready for calibration!"

**Command:**
```bash
python scripts/analysis/auto_calibrate_from_trades.py
```

**What it does:**
- Analyzes all trades
- Calculates your actual costs
- Determines if strategy is viable
- Updates config (with your approval)
- Re-runs backtest

**Decision point:** Go live or don't based on costs.

---

## Troubleshooting

### ❌ "Connection test FAILED"
→ Check credentials in `config/ig_api_credentials.json`
→ Verify demo account is active on IG.com

### ❌ "ModuleNotFoundError: trading_ig"
→ Run: `pip install trading-ig`

### ❌ "IG option placement needs implementation"
→ This is expected (see `AUTOMATED_TRADING_SUMMARY.md`)
→ System still works for IBKR and calibration

### ❌ "Market is closed"
→ Can only trade during market hours (09:30-16:00 ET)
→ Try again on a trading day

### ❌ Other errors
→ Check `AUTOMATED_TRADING_GUIDE.md` → Troubleshooting section
→ Or ask for help

---

## Security Checklist

☐ `config/ig_api_credentials.json` is in `.gitignore` (already done)
☐ Never commit credentials to git
☐ Using demo account (not live)
☐ Understand: Paper trading = no real money

---

## What's Next?

After completing this checklist:

**Immediate (Today):**
- ✅ All systems tested
- ✅ Ready for first trade tomorrow

**Short term (2-3 weeks):**
- Execute 10-15 paper trades
- Collect execution data
- Run calibration

**Medium term (After calibration):**
- Decide: Go live or not
- If viable: Start with £100-200 positions
- Scale up slowly

**Long term:**
- Build track record
- Optimize execution
- Scale position sizing

---

## Quick Reference

**Daily script:**
```bash
python scripts/trading/auto_trade_ig_collect_data.py
```

**Calibration (after 10 trades):**
```bash
python scripts/analysis/auto_calibrate_from_trades.py
```

**View trade logs:**
```bash
# IG.com 20:50 entries
cat logs/ig_paper_trades_2050.json

# IG.com 21:00 entries
cat logs/ig_paper_trades_2100.json

# IBKR entries
cat logs/ibkr_trades.json
```

**Check trade count:**
```bash
python -c "from scripts.trading.trade_logger import TradeLogger; logger = TradeLogger(); print(f'IG trades: {logger.get_trade_count(\"ig_paper_2050\")}'); print(f'IBKR trades: {logger.get_trade_count(\"ibkr\")}')"
```

---

## Status Tracking

Mark off as you complete:

- [ ] Step 1: Read documentation
- [ ] Step 2: Install dependencies
- [ ] Step 3: Get IG.com credentials
- [ ] Step 4: Configure credentials file
- [ ] Step 5: Test connection ✅
- [ ] Step 6: Test trade logger ✅
- [ ] Step 7: Dry run (optional)
- [ ] Step 8: Schedule first trade
- [ ] Step 9: Daily routine (ongoing)
- [ ] Step 10: Calibration (after 10 trades)

---

**Questions?** Check the guides or ask for help.

**Ready?** Start with Step 1!

Good luck! 🚀
