# Phase 1: Dry-Run Auto-Trader Guide

## Overview

Phase 1 implements a **dry-run wrapper** that automates the entire workflow from data polling to order calculation **WITHOUT** placing actual orders on IG.com.

**Purpose**: Validate 100% alignment between backtest logic and live order calculations.

## What Phase 1 Does

1. ✓ Checks day of week (Tue/Thu/Fri only for IG.com weekly expiry strategy)
2. ✓ Polls Polygon.io until 16:00 ET close data is available (max 15 minutes)
3. ✓ Fetches latest data via DataManager
4. ✓ Generates signals using dashboard logic (unfiltered strategy)
5. ✓ Calculates strikes (ATM with correct rounding)
6. ✓ Calculates expiry dates (Wed/Fri/Next Fri)
7. ✓ Calculates limit orders (close ± ATR × 0.1)
8. ✓ Logs all order details to CSV
9. ✗ Does NOT connect to IG.com API
10. ✗ Does NOT place actual orders

## Trading Schedule

**IG.com Weekly Expiry Strategy**:
- **Tuesday**: Trade for Wednesday expiry (overnight)
- **Thursday**: Trade for Friday expiry (overnight)
- **Friday**: Trade for Next Friday expiry (7 days!)

**No trading on Monday/Wednesday** (unless month-end, future enhancement)

## Usage

### Basic Usage (Tue/Thu/Fri only)

```bash
python auto_trade_ig.py
```

**Recommended Run Time**: 21:00-21:05 UK time

The script will:
- Wait for Polygon.io data (polls every 60 seconds, max 15 minutes)
- Fetch latest data
- Generate signals
- Calculate orders
- Log to `logs/ig_orders_dryrun.csv`

### Force Run (Any Day - For Testing)

```bash
python auto_trade_ig.py --force-run
```

Use this for testing the script on non-trading days (Mon/Wed/Sat/Sun).

## Output Files

### 1. Order Log: `logs/ig_orders_dryrun.csv`

**Columns**:
- `Display_Ticker`: US 500 or IWM
- `Source_Ticker`: SPY or IWM (data source)
- `Date`: Trading date
- `Signal`: BUY PUT or BUY CALL
- `Option_Type`: PUT or CALL
- `Direction`: UP or DOWN
- `Current_Price`: Close price (US 500 = SPY × 10)
- `ATR`: 14-day ATR
- `Strike`: ATM strike price
- `Expiry_Date`: Option expiry date
- `Expiry_Type`: NEXT_DAY or WEEKLY
- `Limit_Price`: Limit order price
- `Limit_Pts`: Limit order distance in points
- `Target_Move`: ATR × 0.1
- `Magnitude`: Day's magnitude percentage
- `Day_Direction`: GREEN or RED

**Purpose**: Audit trail for verifying backtest alignment.

## Validation Checklist

After running Phase 1, verify the following:

### ✓ Day of Week Check
- [ ] Script only runs on Tue/Thu/Fri (unless --force-run)
- [ ] Correct expiry type assigned (NEXT_DAY or WEEKLY)

### ✓ Data Availability
- [ ] Script waits for Polygon.io data
- [ ] Fetches latest data successfully
- [ ] No missing data errors

### ✓ Signal Generation
- [ ] Matches dashboard_pro.py output for same date
- [ ] Flat days (< 0.1% magnitude) filtered correctly
- [ ] GREEN day → BUY PUT (Fade Down)
- [ ] RED day → BUY CALL (Fade Up)

### ✓ Strike Calculation
- [ ] **US 500**: Strike = round(close/5) × 5 (5-point increments)
  - Example: Close 60512.50 → Strike 60510
- [ ] **IWM**: Strike = round(close) (1-point increments)
  - Example: Close 219.87 → Strike 220

### ✓ Expiry Calculation
- [ ] **Tuesday**: Expiry = Wednesday (next day)
- [ ] **Thursday**: Expiry = Friday (next day)
- [ ] **Friday**: Expiry = Next Friday (7 days, not next Mon/Tue/Wed/Thu)

### ✓ Limit Calculation
- [ ] Limit distance = ATR × 0.1
- [ ] **PUT**: Limit = Close - (ATR × 0.1) [negative pts]
- [ ] **CALL**: Limit = Close + (ATR × 0.1) [positive pts]

### ✓ Logging
- [ ] All orders logged to `logs/ig_orders_dryrun.csv`
- [ ] CSV has all required columns
- [ ] Values match displayed summary

## Example Output

```
================================================================================
DRY-RUN: ORDERS THAT WOULD BE PLACED
================================================================================

Ticker      Signal    Strike    Expiry         Current      Limit      Limit Pts
US 500      PUT       60510     2026-02-06     60512.50     60451.21   -61.29
IWM         CALL      220       2026-02-06     219.87       222.00     +2.13

Order 1: US 500 PUT
  Date: 2026-02-05
  Day Direction: GREEN (+0.45%)
  Signal: BUY PUT
  Strike: 60510 (ATM)
  Expiry: 2026-02-06 (NEXT_DAY)
  Current: 60512.50
  ATR: 612.90
  Target Move: 61.29 (0.1x ATR)
  Limit Price: 60451.21 (DOWN)
  Limit Pts: -61.29

Order 2: IWM CALL
  Date: 2026-02-05
  Day Direction: RED (-0.35%)
  Signal: BUY CALL
  Strike: 220 (ATM)
  Expiry: 2026-02-06 (NEXT_DAY)
  Current: 219.87
  ATR: 21.30
  Target Move: 2.13 (0.1x ATR)
  Limit Price: 222.00 (UP)
  Limit Pts: +2.13
```

## Backtest Alignment Verification

To verify 100% alignment with backtest:

1. **Run the wrapper on a historical date** (using --force-run):
   ```bash
   # Modify code to use historical date for testing
   python auto_trade_ig.py --force-run
   ```

2. **Compare with backtest results**:
   - Open `results/ig_weekly_expiry_backtest.csv`
   - Find matching date in backtest
   - Compare:
     - Entry Price (should match Current_Price)
     - Strike (should match Strike)
     - Target Price (should match Limit_Price)
     - Target Dist (should match Target_Move)

3. **Verify calculations manually**:
   ```python
   # Example for US 500 on 2026-02-05
   SPY_close = 6051.25
   SPY_atr = 61.29

   US500_close = SPY_close * 10  # 60512.50
   US500_atr = SPY_atr * 10      # 612.90

   strike = round(US500_close / 5) * 5  # 60510
   target_move = US500_atr * 0.1        # 61.29
   limit = US500_close - target_move    # 60451.21 (PUT)
   ```

## Troubleshooting

### "NOT A TRADING DAY"
- **Cause**: Today is not Tue/Thu/Fri
- **Solution**: Wait until next trading day, or use `--force-run` for testing

### "DATA TIMEOUT"
- **Cause**: Polygon.io data not available after 15 minutes
- **Solution**:
  - Check if today is a market holiday
  - Try running again in a few minutes
  - Verify internet connection
  - Check Polygon.io API status

### "FETCH FAILED"
- **Cause**: DataManager failed to fetch data
- **Solution**:
  - Check Polygon.io API key in config
  - Verify API key has not expired
  - Check rate limits

### Strike/Limit Mismatch
- **Cause**: Calculation error
- **Solution**:
  - Verify ATR is calculated correctly
  - Check rounding logic matches backtest
  - Compare with dashboard_pro.py output

## Next Steps

Once Phase 1 is validated (run successfully for 3-5 trading days):

**Phase 2**: Add IG.com API integration (read-only)
- Authenticate with IG.com API
- Fetch option chains
- Verify strikes/expiries exist
- Still no order placement

**Phase 3**: Live paper trading
- Place actual orders on IG.com demo account
- Verify fills
- Compare with backtest expectations

## Important Notes

1. **This is dry-run mode**: No connection to IG.com yet
2. **Logs are append-only**: CSV grows with each run
3. **SPY → US 500 conversion**: Always multiply by 10
4. **Expiry logic is critical**: Friday uses WEEKLY (7 days), not NEXT_DAY
5. **Validation is manual**: You must compare logs with backtest results

## Support

For issues or questions:
1. Check logs in `logs/ig_orders_dryrun.csv`
2. Compare with backtest results
3. Verify calculations manually
4. Check error messages in console output
