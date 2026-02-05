# Configuration Directory

This directory contains trading strategy configuration and parameters.

---

## config.json

**Purpose:** Central configuration file for all trading parameters

### Structure and Parameters

```json
{
    // === Trading Instrument ===
    "ticker": "SPY",                    // Stock ticker to trade (default: SPY)

    // === Backtest Settings ===
    "lookback_years": 2,                // Years of historical data to analyze

    // === Risk Management ===
    "premium_budget": 100,              // Max premium to pay per option contract ($)
    "default_take_profit_atr": 0.1,     // Take profit target (0.1 = 10% of ATR)

    // === API Rate Limiting ===
    "rate_limit_seconds": 14,           // Seconds between API requests
    "max_requests_per_run": 250,        // Max API calls per run (Polygon limit)
    "max_requests_per_minute": 4,       // Max API calls per minute (free tier: 5/min)

    // === Market Data (Optional Overrides) ===
    "spx_value": null,                  // Manual SPX close override (null = auto-fetch)
    "spx_ratio": null,                  // SPY to SPX ratio override (null = use 10.0)

    // === Trading Filters ===
    "filters": {
        "exclude_fridays": true,        // Skip Friday signals (avoid weekend risk)
        "exclude_flat_days": true,      // Skip days with < 0.10% move
        "enable_fade_green": true,      // Trade green day fades (BUY PUT)
        "enable_fade_red": true         // Trade red day fades (BUY CALL)
    },

    // === Directory Structure ===
    "directories": {
        "data": "data",                 // Market data storage
        "logs": "logs"                  // Logs and state files
    }
}
```

---

## Parameter Explanations

### Trading Parameters

**ticker**
- Default: `"SPY"`
- Description: The stock ticker to trade and backtest
- Examples: `"SPY"`, `"QQQ"`, `"IWM"`
- Note: Data must be available for the chosen ticker

**premium_budget**
- Default: `100`
- Description: Maximum dollar amount to risk per trade
- Range: Typically $50-$500 depending on account size
- Example: With $100 budget, you'll buy options costing ≤$100

**default_take_profit_atr**
- Default: `0.1` (10% of ATR)
- Description: Target profit as multiple of Average True Range
- Range: 0.05-0.5 (5%-50% of ATR)
- Lower = easier to hit, but smaller profit
- Higher = harder to hit, but larger profit
- **Current optimal:** 0.1 based on backtest ($9,060 total P/L)

### Backtest Settings

**lookback_years**
- Default: `2`
- Description: How many years of historical data to analyze
- Range: 1-5 years (limited by data availability)
- More years = more trades = better statistical confidence

### Rate Limiting

**max_requests_per_run**
- Default: `250`
- Description: Maximum API calls allowed per execution
- Prevents accidental API limit exhaustion
- Polygon free tier: ~250 requests/day practical limit

**max_requests_per_minute**
- Default: `4`
- Description: API calls per minute (with safety margin)
- Polygon free tier: 5 calls/minute actual limit
- Set to 4 for safety margin

**rate_limit_seconds**
- Default: `14`
- Description: Minimum seconds between API requests
- Calculation: 60 seconds / 4 requests = 15 seconds
- Set to 14 for slight safety margin

### Trading Filters

**exclude_fridays**
- Default: `true`
- Rationale: Avoid weekend gap risk with 0DTE/1DTE options
- Set to `false` only if comfortable with weekend risk

**exclude_flat_days**
- Default: `true`
- Rationale: Flat days (< 0.10% move) have poor fade opportunity
- Hardcoded threshold: 0.10% (see `backtester.py`)

**enable_fade_green**
- Default: `true`
- Description: Trade green day fades (buy PUT when market up)
- Set to `false` to disable bearish fades

**enable_fade_red**
- Default: `true`
- Description: Trade red day fades (buy CALL when market down)
- Set to `false` to disable bullish fades

### Market Data Overrides

**spx_value** (Optional)
- Default: `null` (auto-fetch from Yahoo Finance)
- Description: Manually override SPX close price
- Use case: When Yahoo Finance data is stale or incorrect
- Example: `5900.25` to set SPX close to 5900.25

**spx_ratio** (Optional)
- Default: `null` (uses 10.0 as SPY-to-SPX multiplier)
- Description: Manual SPY to SPX conversion ratio
- Use case: For more accurate SPX calculations
- Example: `10.03` if current ratio is 10.03

---

## Modifying Configuration

### Safe to Change

✅ **premium_budget** - Adjust based on account size
✅ **default_take_profit_atr** - Optimize via backtesting
✅ **exclude_fridays** - Based on risk tolerance
✅ **enable_fade_green/red** - Test directional bias

### Change with Caution

⚠️ **max_requests_per_minute** - Don't exceed API limits
⚠️ **rate_limit_seconds** - Must respect API tier limits
⚠️ **ticker** - Ensure data availability first

### Don't Change Unless You Know Why

🔒 **directories** - Breaking change (code expects these paths)
🔒 **lookback_years** - May require re-downloading data
🔒 **exclude_flat_days** - Validated threshold

---

## Configuration by API Tier

### Polygon Free Tier (Default)
```json
{
    "max_requests_per_run": 250,
    "max_requests_per_minute": 4,
    "rate_limit_seconds": 14
}
```

### Polygon Starter ($29/month - 100 calls/min)
```json
{
    "max_requests_per_run": 5000,
    "max_requests_per_minute": 95,
    "rate_limit_seconds": 0.6
}
```

### Polygon Developer ($99/month - Unlimited)
```json
{
    "max_requests_per_run": 50000,
    "max_requests_per_minute": 500,
    "rate_limit_seconds": 0.1
}
```

---

## Backup and Versioning

### Before Major Changes

```bash
# Backup current config
cp config/config.json config/config.backup.$(date +%Y%m%d).json

# Make changes
nano config/config.json

# Test changes
python src/backtester.py
```

### Version Control

This file **is tracked by git** (not in .gitignore)
- Commit changes with clear messages
- Document parameter changes in commit message

---

## Troubleshooting

**Problem:** "API rate limit exceeded"
- Check `max_requests_per_minute` is ≤ your tier limit
- Verify `rate_limit_seconds` calculation

**Problem:** "No trading signals generated"
- Check `enable_fade_green` and `enable_fade_red` are `true`
- Verify `exclude_flat_days` isn't too restrictive

**Problem:** "Backtest returns $0 P/L"
- Check `premium_budget` is set (not 0 or null)
- Verify `default_take_profit_atr` is reasonable (0.05-0.5)

---

## Related Files

- **src/backtester.py** - Reads config for backtest parameters
- **src/dashboard.py** - Reads config for signal generation
- **src/data_manager.py** - Reads config for API rate limits
- **logs/state.json** - Runtime state (separate from config)

---

*Last updated: 2026-02-04*
