# Understanding SCRATCHES and Why We Still See -$105 Losses

## Quick Answer

**SCRATCH = A trade where target wasn't hit, but we recovered SOME value by closing at 09:35 ET**

**Why still -$105?** = Most losing trades have NO intrinsic value at 09:35 ET, so they still lose full premium

---

## Three Possible Outcomes

| Outcome | Condition | P/L Range | What Happens |
|---------|-----------|-----------|--------------|
| **WIN** | Target hit before 09:35 ET | **+$45** | Position closes automatically at target |
| **SCRATCH** | Target not hit, but option ITM at 09:35 ET | **-$44 to +$39** | Close manually, recover intrinsic value |
| **LOSS** | Target not hit, option OTM at 09:35 ET | **-$105** | Close manually, no intrinsic value = full loss |

---

## Real Examples from Your Backtest

### Example 1: SCRATCH (Turned Profitable!)

**Date: 2024-12-31**
- Signal: FADE_RED (bought CALL)
- Original Strategy: Would lose -$55.35
- 09:35 ET Strategy: Made +$32.92 profit!
- **Difference: +$88.27 improvement**

**Why?**
- Target wasn't hit by 09:35 ET
- But SPY moved in the RIGHT direction (up)
- CALL had intrinsic value at 09:35 ET
- We closed and recovered MORE than the premium!

### Example 2: SCRATCH (Good Recovery)

**Date: 2024-12-24**
- Signal: FADE_GREEN (bought PUT)
- Original Strategy: Would lose -$76.81
- 09:35 ET Strategy: Lost only -$26.69
- **Difference: +$50.12 saved**

**Why?**
- Target wasn't hit by 09:35 ET
- SPY moved somewhat in right direction (down)
- PUT had SOME intrinsic value
- We recovered $50 instead of losing it all!

### Example 3: LOSS (Still -$105)

**Date: 2026-01-27**
- Signal: FADE_GREEN (bought PUT)
- Original Strategy: Lost -$105
- 09:35 ET Strategy: Lost -$105
- **Difference: $0 (no change)**

**Why?**
- Target wasn't hit by 09:35 ET
- SPY moved AGAINST us (went UP instead of down)
- PUT had ZERO intrinsic value (Out-of-Money)
- Nothing to recover = full loss

---

## The Math Behind Scratches

### For PUT Options (FADE_GREEN)

**Setup:**
- Entry SPY: $580
- Strike: $580 PUT
- Estimated Premium: ~$0.40 (40% of target distance)

**At 09:35 ET:**

| SPY Price | PUT Intrinsic | Calculation | P/L | Result |
|-----------|---------------|-------------|-----|--------|
| $577 | $3.00 | ($3/$0.40) - 1 = +650% → capped at +50% | **+$45** | SCRATCH (profit!) |
| $579 | $1.00 | ($1/$0.40) - 1 = +150% → capped at +50% | **+$30** | SCRATCH (good!) |
| $579.60 | $0.40 | ($0.40/$0.40) - 1 = 0% | **-$5** | SCRATCH (break-even) |
| $579.80 | $0.20 | ($0.20/$0.40) - 1 = -50% | **-$55** | LOSS |
| $580 | $0 | No intrinsic | **-$105** | LOSS (full) |
| $581 | $0 | No intrinsic | **-$105** | LOSS (full) |

### For CALL Options (FADE_RED)

Same logic, but:
- Intrinsic = max(0, SPY - Strike)
- Profits when SPY goes UP

---

## Your Actual Results

### Strategy 1: ORIGINAL (Hold until expiration)
```
Wins:     259 (79.0%)
Losses:   68 (full -$105 each)
Scratches: 1
Total P/L: $4,634
```

### Strategy 2: 09:35 ET EXIT (Close early)
```
Wins:     259 (79.0%) - Same!
Losses:   63 (full -$105 each) - 5 fewer!
Scratches: 6 (recovered value) - 5 more!
Total P/L: $5,032
```

**The 6 Scratches:**

| Date | Signal | P/L | Status |
|------|--------|-----|--------|
| 2024-12-31 | FADE_RED | **+$32.92** | Turned profitable! |
| 2025-07-03 | FADE_GREEN | **+$38.50** | Turned profitable! |
| 2024-08-19 | FADE_GREEN | -$9.25 | Saved $96 |
| 2024-12-24 | FADE_GREEN | -$26.69 | Saved $78 |
| 2025-04-17 | FADE_RED | -$30.76 | Saved $74 |
| 2025-04-14 | FADE_RED | -$43.96 | Saved $61 |

**Total value of scratches: +$398 improvement**

---

## Why Most Losses Stay at -$105

Out of 328 trades:
- **259 wins** (79%) - Target hit
- **63 losses** (19.2%) - No intrinsic value at 09:35 ET
- **6 scratches** (1.8%) - Had intrinsic value at 09:35 ET

**Only 9% of non-winning trades (6 out of 69) had intrinsic value worth recovering!**

### Why So Few Scratches?

1. **Small Target (0.1x ATR)**: Only looking for small moves
2. **Overnight Gap Risk**: SPY often gaps against the position
3. **Binary Outcome**: Either hits target (WIN) or moves against us (LOSS)
4. **Time Decay**: By 09:35 ET, options have decayed significantly

---

## Visual Example

```
Trade: FADE_GREEN (Buy PUT at $580 strike)

Scenario A: WIN
SPY: $580 -> $579 (hit target)
Result: +$45 ✓

Scenario B: SCRATCH
SPY: $580 -> $579.50 (close but didn't hit)
At 09:35 ET: PUT worth $0.50 intrinsic
Result: -$30 (recovered $75!)

Scenario C: LOSS
SPY: $580 -> $581 (moved against us)
At 09:35 ET: PUT worth $0 (OTM)
Result: -$105 (full loss)
```

---

## Key Takeaways

### 1. We CAN Predict Losses (We Do!)

The backtester DOES calculate exact losses based on:
- SPY price at 09:35 ET
- Intrinsic value at that time
- Formula: (Intrinsic / Premium) - 1

### 2. Most Losses Are Still -$105 Because...

- Option is Out-of-The-Money at 09:35 ET
- Zero intrinsic value
- Nothing to recover

### 3. Scratches Are Rare But Valuable

- Only 6 out of 328 trades (1.8%)
- But they add $398 to total P/L
- 2 turned profitable (+$32, +$38)
- 4 reduced losses significantly

### 4. The 09:35 ET Exit Strategy Works

- Same wins (259)
- Fewer full losses (63 vs 68)
- More scratches (6 vs 1)
- **Better total P/L: $5,032 vs $4,634 (+$398)**

---

## Bottom Line

**Yes, we predict actual losses!**
- If option has intrinsic value at 09:35 ET → P/L is calculated based on that value (SCRATCH)
- If option has NO intrinsic value at 09:35 ET → Still -$105 (LOSS)

**Most losses stay at -$105 because:**
- Most losing trades have moved AGAINST the position
- Option is Out-of-The-Money
- No intrinsic value to recover

**But the few that DO have value make a BIG difference: +$398 over 2 years!**

---

*The 09:35 ET exit strategy captures the occasional partial recoveries that would otherwise be lost to time decay, improving overall P/L without changing the win rate.*
