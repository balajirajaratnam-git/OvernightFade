# Corrected Strategy Analysis

## Critical Issue Found and Fixed

**Date:** 2026-02-04

**Problem:** The original backtester was incorrectly modeling the "hold until expiry" strategy. It only checked the overnight window (16:00 ET to 09:30 ET next day) and assumed any position not winning by 09:30 ET would expire worthless. This is incorrect - options don't expire until market close (16:00 ET) the next trading day.

**Impact:** This caused the backtester to miss 31 wins that occurred during the regular trading session (09:30-16:00 ET) on the next trading day.

---

## Corrected Results

### Strategy 1: Original (Hold Until Options Expire at 16:00 ET)

- **Total Trades:** 328
- **Wins:** 290 (88.4%)
- **Losses:** 38
- **Scratches:** 0
- **Total P/L:** **$9,060.00**
- **Avg Trade:** $27.62

### Strategy 2: 09:35 ET Exit (Close Non-Winners at 09:35 ET / 14:35 UK)

- **Total Trades:** 328
- **Wins:** 259 (79.0%)
- **Losses:** 63
- **Scratches:** 6
- **Total P/L:** **$5,032.36**
- **Avg Trade:** $15.34

---

## Key Finding

**The original "hold until expiry" strategy performs BETTER by $4,027.64 (44% improvement)!**

### Why the Difference?

The 09:35 ET exit strategy closes all non-winning positions at market open, missing 31 trades that would have hit their profit targets during the regular trading session (09:30-16:00 ET).

**Example wins during cash session:**
- 2026-01-27: FADE_GREEN wins at 16:44 UTC (~11:44 ET)
- 2026-02-02: FADE_GREEN wins at 16:51 UTC (~11:51 ET)
- 2025-12-17: FADE_RED wins at 15:58 UTC (~10:58 ET)

These positions didn't hit target overnight but reached profitability during regular market hours.

---

## Win Distribution

### When Do Wins Occur?

**Overnight Window (16:00 ET to 09:30 ET next day):** 259 wins (89% of total wins)
- Evening (16:00-20:00 ET same day): ~120 wins
- Overnight (20:00-04:00 ET): ~80 wins
- Pre-market (04:00-09:30 ET): ~60 wins

**Cash Session (09:30-16:00 ET next day):** 31 wins (11% of total wins)
- These are the wins MISSED by early exit strategy

---

## Recommendation

### ✅ **Use the Original Strategy: Hold Until Expiry**

**Reasons:**
1. **Better Performance:** $9,060 vs $5,032 (+$4,028 or +80% improvement)
2. **Higher Win Rate:** 88.4% vs 79.0%
3. **Fewer Losses:** 38 vs 63 losses
4. **Simpler Execution:** No need to manually close positions at 09:35 ET
5. **Captures Full Potential:** Allows positions to reach target throughout the trading day

### ❌ **Do NOT Use Early Exit Strategy**

The early exit strategy sacrifices 31 profitable trades to recover minor amounts (~$10-40) from a few scratches. The cost (-$4,028) far outweighs the benefit.

---

## Trading Implementation

### Recommended Approach

1. **Entry Signal:** Generated at 16:00 ET market close based on daily candle direction
2. **Entry Execution:** Place trade at 21:05 UK (approximately 16:05 ET)
3. **Take Profit:** Set limit order at target price (entry ± ATR * multiplier)
4. **Exit:** **Let the limit order work until expiry** - do NOT manually close positions early
5. **Expiry:** Options expire automatically at 16:00 ET if limit not hit

### What This Means Practically

- **Do NOT** set an alarm for 09:35 ET (14:35 UK) to close positions
- **Do NOT** manually close non-winning positions in the morning
- **Simply** let your limit orders work throughout the full trading day
- **Trust** that 11% of your wins will come during regular market hours

---

## Technical Changes Made

### Code Fixes

1. **Added `_get_cash_session_window()` method** to get 09:30-16:00 ET window
2. **Added `_get_next_trading_day_file()` method** to load next day's intraday data
3. **Modified win detection logic:**
   - First check overnight window (16:00 ET to 09:30 ET)
   - If not hit, load next trading day's file
   - Check cash session window (09:30 ET to 16:00 ET)
   - Only declare LOSS if target not hit by 16:00 ET expiry

### Files Updated

- `src/backtester.py` - Fixed to check full trading day until expiry
- `src/backtester_OLD.py` - Backup of incorrect version
- `results/trade_log_ORIGINAL.csv` - Updated with correct results
- `results/trade_log_0935ET.csv` - Updated with correct results

---

## Historical Context

### Previous (Incorrect) Analysis

The earlier analysis showed:
- Original strategy: $4,634 (79% win rate)
- 09:35 ET exit: $5,032 (79% win rate)
- Conclusion: Early exit better by $398

**This was WRONG** because we weren't checking the full trading day for the original strategy.

### Corrected Analysis

- Original strategy: **$9,060** (88.4% win rate)
- 09:35 ET exit: $5,032 (79% win rate)
- Conclusion: **Original strategy better by $4,028**

The corrected analysis completely reverses the recommendation!

---

## Verification

To verify these results, the analysis script `scripts/analysis/analyze_win_times_v2.py` confirms:
- All 259 wins in the early exit strategy occur before 09:35 ET ✓
- The original strategy finds 31 additional wins during 09:35-16:00 ET ✓
- No wins occur after 16:00 ET (expiry time) ✓

---

## Summary

**Key Takeaway:** Always model reality accurately in backtests. The assumption that "positions not winning by 09:30 ET will expire worthless" cost us $4,028 in foregone profits. Proper modeling shows that holding positions until expiry is the superior strategy.

**Action Item:** Remove any alerts or plans to manually close positions at 09:35 ET. Simply let your limit orders work throughout the full trading day.

---

*Analysis completed: 2026-02-04*
*Backtester version: Fixed*
*Data period: 2024-02-22 to 2026-02-04*
