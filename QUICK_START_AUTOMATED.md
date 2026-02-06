# Quick Start: Automated Trading System

**5-minute setup guide**

---

## 1. Install Dependencies (2 min)

```bash
pip install trading-ig yfinance scipy rich
```

---

## 2. Add Your IG.com Credentials (2 min)

Edit: `config/ig_api_credentials.json`

```json
{
  "demo": {
    "api_key": "YOUR_API_KEY_HERE",
    "username": "YOUR_USERNAME",
    "password": "YOUR_PASSWORD",
    "acc_type": "DEMO",
    "acc_number": "YOUR_ACCOUNT_NUMBER"
  }
}
```

Get these from: IG.com → Settings → API

---

## 3. Test Connection (1 min)

```bash
python scripts/trading/ig_connector.py
```

Should see: ✅ `Connection test PASSED`

---

## 4. Daily Trading (Every trading day at 20:50 UK)

```bash
python scripts/trading/auto_trade_ig_collect_data.py
```

**What it does:**
- Generates signal (CALL/PUT)
- Places IG demo orders (20:50 + 21:00)
- Shows IBKR instructions
- Records everything

**Time:** 5-10 minutes per day

---

## 5. Calibration (After 10 trades, ~2-3 weeks)

```bash
python scripts/analysis/auto_calibrate_from_trades.py
```

**What it does:**
- Analyzes all trades
- Measures actual costs
- Tells you if strategy is viable
- Updates config with YOUR costs
- Re-runs backtest

**Result:** Clear yes/no answer on profitability

---

## What Happens After Calibration?

### If Costs < 8%:
✅ Strategy works!
✅ Go live with 5% position sizing
✅ Expected CAGR: 15-25%

### If Costs > 10%:
❌ Strategy doesn't work
❌ Don't trade
❌ Find better execution or different strategy

---

## That's It!

For full details: `AUTOMATED_TRADING_GUIDE.md`

For background: `AUTOMATED_TRADING_SUMMARY.md`

---

**Questions?** Check the guides or ask me.

**Ready?** Run step 1 now, then execute first trade tomorrow at 20:50 UK.

Good luck! 🚀
