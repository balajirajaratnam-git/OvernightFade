# Automated Trading & Data Collection Guide

## Overview

This system automates paper trading, collects execution data, and calibrates the strategy with real market costs.

**Daily workflow:** Run one script at 20:50 UK → System handles everything → Calibrates after 10 trades

---

## Setup (One-Time)

### 1. Install Dependencies

```bash
pip install trading-ig yfinance scipy rich
```

### 2. Configure IG.com API Credentials

Edit `config/ig_api_credentials.json`:

```json
{
  "demo": {
    "api_key": "YOUR_DEMO_API_KEY",
    "username": "YOUR_DEMO_USERNAME",
    "password": "YOUR_DEMO_PASSWORD",
    "acc_type": "DEMO",
    "acc_number": "YOUR_DEMO_ACCOUNT_NUMBER"
  }
}
```

**How to get IG.com API credentials:**
1. Log into IG.com
2. Go to Settings → API
3. Create new API key
4. Note your username, password, API key, account number

### 3. Test Connection

```bash
python scripts/trading/ig_connector.py
```

Should see: `Connection test PASSED`

---

## Daily Usage

### At 20:50 UK (15:50 ET) - Every Trading Day

```bash
python scripts/trading/auto_trade_ig_collect_data.py
```

**What it does:**
1. ✅ Generates today's signal (CALL/PUT based on previous day)
2. ✅ Calculates theoretical option price (Black-Scholes)
3. ✅ Connects to IG.com demo account
4. ✅ Places paper trade at 20:50 UK
5. ⏱️  Schedules second paper trade at 21:00 UK (measures timing penalty)
6. ✅ Shows IBKR instructions for manual trading
7. ✅ Records all data (bid/ask, fills, spreads, slippage)
8. ✅ Logs everything for calibration

**Example output:**
```
================================================================================
AUTOMATED TRADING & DATA COLLECTION
================================================================================

Generating today's signal...

Today's Signal:
  CALL at strike 6800
  Expiry: 2026-02-07 (THU-FRI-1D)
  Days to expiry: 1
  Underlying: $6798.45

Theoretical option price: $21.45

IG.com Demo Account Trading
Placing order at 20:50 UK (NOW)...
Order ACCEPTED - Deal ID: ABC123
Fill price: £22.10
Spread: 3.0%

IBKR MANUAL TRADING INSTRUCTIONS
--------------------------------------------------------------------------------
Signal:             CALL
Strike:             $679.80
Expiry:             2026-02-07
Theoretical Price:  $2.15
Action:             BUY TO OPEN
Order Type:         LIMIT (between mid and ask)

Enter IBKR fill details:
Bid: $2.05
Ask: $2.25
Fill: $2.18
Size: 1

IBKR trade recorded successfully
Spread: 3.81%

Done! See you tomorrow at 20:50 UK.
```

---

## After 10 Trades: Auto-Calibration

### Run Calibration

```bash
python scripts/analysis/auto_calibrate_from_trades.py
```

**What it does:**
1. ✅ Analyzes all logged trades
2. ✅ Calculates average spreads, slippage, costs
3. ✅ Compares 20:50 vs 21:00 entries (measures timing penalty)
4. ✅ Calculates actual win rates and P&L
5. ✅ Determines if strategy is viable
6. ✅ Proposes updated `reality_adjustments.json` values
7. ✅ Asks for confirmation before updating
8. ✅ Re-runs backtest with calibrated values

**Example output:**
```
================================================================================
CALIBRATION ANALYSIS
================================================================================

IG.com Demo (20:50 entries)

Trades Analyzed:    10
Win Rate:           80.0%
Avg Spread:         5.20%
Avg Slippage:       1.80%
Avg Total Cost:     7.00%
Avg Win:            18.5%
Avg Loss:           -98.2%

Timing Penalty Analysis (20:50 vs 21:00)

Trade Pairs:        8
Avg Cost @ 20:50:   7.00%
Avg Cost @ 21:00:   9.50%
Timing Penalty:     2.50%

================================================================================
RECOMMENDATIONS
================================================================================

Verdict: GOOD
Total costs: 7.00%
Strategy viable with good execution (15-25% CAGR)

Recommended Position Sizing:
  - Start with 5% of account (Half Kelly)
  - Max drawdown: 30-40%
  - Expected CAGR: 10-20%

================================================================================
PROPOSED CONFIG UPDATES
================================================================================

Parameter                    Current    Calibrated
spread_costs.SPY             0.0500     0.0520
slippage_pct.SPY             0.0150     0.0180
close_timing_penalty.SPY     0.0200     0.0250
pnl_adjustments.1_day.SPY    0.6300     0.6150

Update config/reality_adjustments.json? (yes/no): yes

Backed up current config to config/reality_adjustments.json.backup
Updated config/reality_adjustments.json

Run backtest with calibrated values? (yes/no): yes

Running backtest...
[Backtest results with YOUR actual costs]
```

---

## Files Created

### Configuration
- `config/ig_api_credentials.json` - Your API credentials (never commit to git!)

### Trade Logs
- `logs/ig_paper_trades_2050.json` - IG.com entries at 20:50 UK
- `logs/ig_paper_trades_2100.json` - IG.com entries at 21:00 UK (timing comparison)
- `logs/ibkr_trades.json` - IBKR manual entries

### Scripts
- `scripts/trading/ig_connector.py` - IG.com API wrapper
- `scripts/trading/trade_logger.py` - Data logging system
- `scripts/trading/auto_trade_ig_collect_data.py` - Main daily script
- `scripts/analysis/auto_calibrate_from_trades.py` - Calibration system

---

## Data Collected Per Trade

For each trade, the system logs:

**Entry Data:**
- Date, time, timestamp
- Signal (CALL/PUT)
- Strike price
- Expiry date
- Days to expiry
- Theoretical price (Black-Scholes)
- Market bid/ask at entry
- Mid price
- Actual fill price
- Position size
- Premium paid
- Spread % = (fill - mid) / mid
- Slippage % = additional cost beyond spread

**Exit Data (added at expiry):**
- Exit timestamp
- Market bid/ask at exit
- Exit price
- P&L in dollars
- P&L %
- Win/loss

**Calculated Metrics:**
- Total cost % (spread + slippage)
- Timing penalty (20:50 vs 21:00 comparison)
- Actual vs predicted P&L

---

## Calibration Logic

### How Costs Are Measured

**Spread:**
```
Spread % = (Fill Price - Mid Price) / Mid Price
```

**Slippage:**
```
Slippage % = (Fill Price - Theoretical Price) / Theoretical Price - Spread %
```

**Total Cost:**
```
Total Cost % = Spread % + Slippage %
```

**Timing Penalty:**
```
Timing Penalty = Avg Cost @ 21:00 - Avg Cost @ 20:50
```

### How PnL Multiplier Is Calculated

Backtest assumes +45% average win.

Reality = (Backtest * Multiplier) - Costs

Given actual results:
```
Multiplier = (Actual Avg Win + Costs) / 0.45
```

Example:
- Actual avg win: 18.5%
- Costs: 7.0%
- Multiplier = (0.185 + 0.07) / 0.45 = 0.567

### Viability Thresholds

| Total Costs | Verdict | Expected CAGR | Action |
|-------------|---------|---------------|--------|
| < 5% | Excellent | 25-35% | Go live with 5% position sizing |
| 5-8% | Good | 15-25% | Go live with 5% position sizing |
| 8-10% | Marginal | 10-15% | Consider if you can handle drawdowns |
| > 10% | Not Viable | Negative | DON'T TRADE - find better execution |

---

## Timing Analysis

### Why 20:50 UK and 21:00 UK?

**20:50 UK (15:50 ET):**
- 10 minutes before market close
- Better liquidity
- Tighter spreads
- Higher fill success rate
- **Recommended entry time**

**21:00 UK (16:00 ET):**
- Exactly at market close
- Worse liquidity
- Wider spreads
- May not get fills
- **Measures close timing penalty**

By comparing both entries, we directly measure the cost of entering at close vs 10 minutes before.

---

## IBKR Manual Trading

The script shows instructions for IBKR but doesn't automate (as requested).

**Why?**
- IBKR options have wider spreads
- Harder to get fills
- Better to manually optimize execution

**Workflow:**
1. Script shows strike, expiry, theoretical price
2. You manually place order on IBKR
3. You enter fill details into script
4. Script logs data for comparison

**Note:** IBKR uses SPY (not SPX), so prices are 1/10 of IG.com.

---

## Troubleshooting

### "Connection test FAILED"
- Check `config/ig_api_credentials.json` is filled in
- Verify API key is correct
- Check username/password
- Ensure demo account is active

### "No trades found"
- Run `auto_trade_ig_collect_data.py` first to log trades
- Need at least 10 trades before calibration

### "IG.com option placement needs implementation"
- IG.com option epic search needs customization for your account
- Contact support if unclear on epic format

### "Market is closed"
- Can only trade during market hours (09:30-16:00 ET)
- Weekend trades won't execute

### Script errors with imports
- Run: `pip install trading-ig yfinance scipy rich`
- Ensure Python 3.7+

---

## Security Notes

### API Credentials
- **NEVER commit `ig_api_credentials.json` to git**
- Add to `.gitignore`
- Use demo account for paper trading
- Keep live credentials separate

### Git Ignore
Add to `.gitignore`:
```
config/ig_api_credentials.json
logs/ig_paper_trades_*.json
logs/ibkr_trades.json
```

---

## Next Steps

### Phase 1: Paper Trading (Current)
- [x] Setup complete
- [ ] Run daily at 20:50 UK for 2-3 weeks
- [ ] Collect 10-15 trades
- [ ] Run calibration

### Phase 2: Go/No-Go Decision
- [ ] Analyze calibration results
- [ ] Check total costs < 8%
- [ ] Decide: Go live or continue paper

### Phase 3: Live Trading (Future)
- [ ] Start with tiny positions (£100-200)
- [ ] Scale up after 10 successful live trades
- [ ] Never exceed 5% position sizing

---

## Contact & Support

Issues with:
- **IG.com API:** Check IG.com developer docs
- **trading-ig library:** https://github.com/ig-python/trading-ig
- **Strategy logic:** See `SYSTEM.md`
- **Backtest:** See `SCRIPTS_GUIDE.md`

---

**Remember:** This is paper trading for calibration. DO NOT go live until:
1. You have 10+ paper trades
2. Calibration shows costs < 8%
3. You understand the execution process
4. You can handle 30-50% drawdowns

Good luck! 🚀
