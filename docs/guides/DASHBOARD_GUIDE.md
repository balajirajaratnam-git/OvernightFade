# Dashboard Pro - Complete Usage Guide

**Professional multi-ticker trading dashboard with platform-specific outputs**

---

## Quick Reference

```bash
# IG.com (default) - US 500 + IWM only
python dashboard_pro.py -o compact

# IBKR - All 4 tickers
python dashboard_pro.py -o compact -p ibkr

# Full detailed view
python dashboard_pro.py
```

---

## Command-Line Options

| Option | Values | Default | Description |
|--------|--------|---------|-------------|
| `-o, --output` | `detailed`, `compact` | `detailed` | Output mode |
| `-p, --platform` | `ig`, `ibkr` | `ig` | Trading platform (compact mode only) |

---

## Output Modes

### 1. Compact Mode (Recommended for Daily Trading)

**Purpose**: Quick daily trading signals in tabular format

#### IG.com Mode (Default)
```bash
python dashboard_pro.py -o compact
```

**Shows**: US 500 (SPX) + IWM only

**Output Example**:
```
Overnight Fade | Trading Signals (IG.com)
Date: 2026-02-04

+--------------------------------------------------------------------------+
| Ticker   | Signal      | Strike | Current  |      Limit Pts            |
|----------+-------------+--------+----------+---------------------------|
| US 500   | BUY CALL    |   6860 |  6861.90 |     +51.96 pts            |
| IWM      | BUY CALL    |    261 |   260.52 |      +0.47 pts            |
+--------------------------------------------------------------------------+

IG.com options: US 500 (SPX) + IWM only
US 500 = SPY * 10 (maintains backtest price/ATR ratio)
Target: 0.1x ATR | Strategy: Unfiltered
```

**Columns**:
- **Ticker**: US 500 (SPX) or IWM
- **Signal**: BUY CALL / BUY PUT
- **Strike**: ATM strike price
- **Current**: Current market price
- **Limit Pts**: Target move in points (take-profit level)

#### IBKR Mode
```bash
python dashboard_pro.py -o compact -p ibkr
```

**Shows**: All 4 tickers (SPY, QQQ, IWM, DIA)

**Output Example**:
```
Overnight Fade | Trading Signals (IBKR)
Date: 2026-02-04

+--------------------------------------------------------------------------+
| Ticker | Signal   | Strike | Current  | Limit Price | Limit Pts       |
|--------+----------+--------+----------+-------------+-----------------|
| SPY    | BUY CALL |    686 |  $686.19 |    $691.39  |  +5.20 pts      |
| QQQ    | BUY CALL |    606 |  $605.75 |    $606.79  |  +1.04 pts      |
| IWM    | BUY CALL |    261 |  $260.52 |    $260.99  |  +0.47 pts      |
| DIA    | BUY PUT  |    495 |  $494.75 |    $494.17  |  -0.58 pts      |
+--------------------------------------------------------------------------+

IBKR: All 4 tickers available
Target: 0.1x ATR | Strategy: Unfiltered
```

**Columns**:
- **Ticker**: SPY, QQQ, IWM, DIA
- **Signal**: BUY CALL / BUY PUT
- **Strike**: ATM strike price
- **Current**: Current market price
- **Limit Price**: Take-profit price level
- **Limit Pts**: Target move in points

---

### 2. Detailed Mode (Full Analysis)

**Purpose**: Comprehensive analysis with backtest statistics

```bash
python dashboard_pro.py
```

**Shows**:
- Individual ticker analysis (all 4 tickers)
- Detailed signal reasoning
- Strike calculations
- Risk/reward breakdown
- Historical backtest performance
- Per-ticker statistics
- Overall portfolio metrics

**Use When**:
- First-time setup
- Weekly review
- Verifying strategy parameters
- Understanding backtest results

---

## Platform Differences

### IG.com UK

**Available Options**: US 500 (SPX), IWM
- QQQ and DIA are NOT available for options on IG.com UK
- Dashboard automatically shows only tradable instruments

**US 500 Conversion**:
- Source: SPY historical data
- Conversion: SPY × 10
- Rationale: Maintains backtest price/ATR ratios
- Example: SPY $686.19 → US 500 6861.90
- Strike format: 5-point increments (6860, 6865, 6870...)

**Why SPY × 10 Instead of Real SPX?**
- Polygon.io "Stocks Developer" plan doesn't include index data
- SPY × 10 maintains identical volatility ratios as backtest
- Price difference (~$20) is negligible (0.3%)
- Backtest expectations remain valid

### IBKR (Interactive Brokers)

**Available Options**: All 4 tickers (SPY, QQQ, IWM, DIA)
- Uses direct ETF data
- Strike format: 1-point increments
- Shows both Limit Price and Limit Pts for convenience

---

## Reading the Signals

### Signal Types

| Signal | Meaning | Action |
|--------|---------|--------|
| **BUY CALL** | Previous day was RED (down) | Buy ATM CALL options, fade the decline |
| **BUY PUT** | Previous day was GREEN (up) | Buy ATM PUT options, fade the rally |
| **NO TRADE** | Flat day or Friday | Skip trading today |

### Understanding Limit Pts

**Limit Pts** = Target move in points from current price

**Example (SPY)**:
- Current: $686.19
- Signal: BUY CALL
- Limit Pts: +5.20 pts
- **Interpretation**: Set take-profit at $686.19 + $5.20 = $691.39

**Example (US 500)**:
- Current: 6861.90
- Signal: BUY CALL
- Limit Pts: +51.96 pts
- **Interpretation**: Set take-profit at 6861.90 + 51.96 = 6913.86

### Why These Targets?

- **Formula**: Target = Close ± (ATR_14 × 0.1)
- **ATR**: 14-day Average True Range (volatility measure)
- **Multiplier**: 0.1 (validated through 10-year backtest)
- **Success Rate**: 85.7% of trades hit this target overnight

---

## Daily Trading Workflow

### Morning Routine (Before Market Close)

1. **Check Dashboard** (15:30-15:45 ET):
```bash
python dashboard_pro.py -o compact
```

2. **Review Signals**:
   - Note ticker, signal type, strike, and limit pts
   - Verify date matches today

3. **Prepare Orders** (15:45-15:55 ET):
   - Buy ATM options at market close (15:59-16:00 ET)
   - Set take-profit at (Current + Limit Pts)
   - Set expiry for next trading day (0DTE)

4. **Monitor Overnight**:
   - Target typically hits during overnight/pre-market session
   - Let limit orders execute automatically
   - Check positions at open (09:30 ET next day)

### Example Trade (IG.com)

**Dashboard Output**:
```
US 500 | BUY CALL | 6860 | 6861.90 | +51.96 pts
```

**Your Actions**:
1. At 16:00 ET: Buy US 500 CALL option, strike 6860, expiry next day
2. Set take-profit: 6861.90 + 51.96 = 6913.86
3. Overnight: Wait for target to hit
4. If hit: +50% profit on premium (backtest expectation: +45% after slippage)
5. If not hit: -100% loss on premium (backtest expectation: -105% after slippage)

---

## Strike Selection

### ATM (At-The-Money) Strikes

**Dashboard calculates ATM automatically**:
- **SPY, QQQ, IWM, DIA**: Round current price to nearest dollar
- **US 500 (SPX)**: Round to nearest 5-point increment

**Examples**:
- SPY current $686.19 → Strike **686**
- QQQ current $605.75 → Strike **606**
- US 500 current 6861.90 → Strike **6860** (5-pt increment)

**Why ATM?**
- Backtest assumes ATM entry
- Simplifies execution
- Highest delta for target distance

---

## Data Freshness

Dashboard uses **most recent data** from:
- `data/{TICKER}/daily_OHLCV.parquet`

**To update data**:
```bash
python fetch_multi_ticker_data.py
```

**Update Frequency**:
- Daily: Recommended before trading
- Weekly: Minimum acceptable
- After gaps: Always update after market holidays

---

## Troubleshooting

### No Trade Signal

**Possible Reasons**:
1. **Flat Day**: Previous day moved < 0.10%
2. **Friday**: Strategy excludes Fridays (no weekend overnight)
3. **Missing Data**: Run `python fetch_multi_ticker_data.py`

### Wrong Date Displayed

**Solution**: Update data
```bash
python fetch_multi_ticker_data.py
```

### "Error: No data available"

**Causes**:
1. Data directory missing
2. Files corrupted
3. Wrong ticker in config

**Solution**: Re-download data
```bash
python fetch_multi_ticker_data.py
```

---

## Integration with Backtest

**Dashboard uses IDENTICAL logic to backtest**:
- Same ATR calculation (14-day)
- Same target multiplier (0.1x)
- Same flat day threshold (0.10%)
- Same Friday exclusion
- **No LastHourVeto filter** (unfiltered strategy)

**Expected Performance**:
- Win Rate: 85.7%
- Average trades per day: ~2-3 (across all tickers)
- CAGR: 67.2% (backtest, may vary in live trading)

---

## Advanced Usage

### Scripting / Automation

```python
# Example: Parse dashboard output programmatically
import subprocess
import json

result = subprocess.run(
    ['python', 'dashboard_pro.py', '-o', 'compact'],
    capture_output=True,
    text=True
)

# Parse result.stdout for signals
# Automate order placement via broker API
```

### Multiple Accounts

```bash
# IG.com account
python dashboard_pro.py -o compact > ig_signals.txt

# IBKR account
python dashboard_pro.py -o compact -p ibkr > ibkr_signals.txt
```

---

## Best Practices

1. **Check Daily**: Run dashboard before market close (15:30-15:45 ET)
2. **Verify Strikes**: Ensure ATM strikes shown are actually available
3. **Track Performance**: Log actual vs expected results
4. **Update Weekly**: Keep data fresh with `fetch_multi_ticker_data.py`
5. **Follow Signals**: Don't override strategy logic arbitrarily
6. **Size Properly**: Use $1,000 max per ticker as backtest assumes

---

## Key Reminders

- ✅ **Dashboard = Backtest Logic**: Same calculations, same expectations
- ✅ **Unfiltered Strategy**: No momentum filters, maximum performance
- ✅ **ATM Strikes**: Always at-the-money as shown
- ✅ **0.1x ATR**: Target is always 0.1 × ATR_14
- ✅ **Platform-Specific**: IG.com shows US 500+IWM, IBKR shows all 4
- ✅ **SPY × 10 = US 500**: Maintains backtest alignment

---

## Support

**Documentation**:
- `README.md` - Project overview
- `docs/BACKTEST_TRUST_REPORT.md` - Backtest validation
- `docs/STRATEGY_COMPARISON.md` - Strategy details

**Questions?** Refer to documentation in `docs/` folder.

---

**Last Updated**: 2026-02-05
**Dashboard Version**: 4.0 (Multi-Ticker, Platform-Specific)
