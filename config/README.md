# Configuration Directory

**Last Updated**: 2026-02-05
**Version**: 5.0 (SHORT Expiries, Reality-Adjusted)

---

## Files

### **config.json**
Main strategy configuration with multi-ticker support.

**Current Settings (v5.0)**:
```json
{
  "tickers": ["SPY"],  // SPY ONLY recommended (34.3% CAGR)
  "lookback_years": 10,  // 10 years historical data (2016-2026)
  "premium_budget": 1000,  // Max $1000 per position
  "default_take_profit_atr": 0.1  // 10% of ATR target
}
```

**Why SPY Only?**
Reality-adjusted backtest shows:
- SPY: +8.7% expected value per trade
- QQQ: -5.8% (wide spreads eat profits)
- IWM: -20.2% (10% bid/ask spread)
- DIA: -34.3% (15% bid/ask spread)

### **reality_adjustments.json**
Adjustment factors for realistic P&L calculations.

**Factors**:
- **P&L Adjustments**: Theta decay multipliers (0.65x for SPY 2-day)
- **Spread Costs**: SPY 3%, QQQ 5%, IWM 10%, DIA 15%
- **Slippage**: 0.8-3.1% depending on ticker
- **Commission**: $0.65 per contract × 2 (entry + exit)

---

## Modifying Configuration

### **To Add More Tickers** (Not Recommended)
```json
{
  "tickers": ["SPY", "QQQ"]  // Only do this for paper trading comparison
}
```
Note: Auto-trader will show warnings for non-SPY tickers.

### **To Adjust Position Size**
```json
{
  "premium_budget": 1500  // Increase max position size
}
```
Current: Kelly sizing min(equity × 5.23%, $1000)

### **To Change Backtest Period**
```json
{
  "lookback_years": 5  // Requires data availability
}
```

---

## Usage

**Read by**:
- `scripts/trading/auto_trade_ig.py` - Daily trading signals
- `scripts/backtesting/run_backtest_ig_short_expiries*.py` - Backtest scripts
- `scripts/data/fetch_multi_ticker_data.py` - Data fetching
- All analysis scripts

**Backup Before Changes**:
```bash
cp config/config.json config/config.backup.$(date +%Y%m%d).json
```

---

## Related Documentation

- `README.md` - Project overview
- `docs/backtests/BACKTEST_REALITY_RESULTS_SUMMARY.md` - Why SPY only
- `docs/guides/REALITY_CALIBRATION_GUIDE.md` - Calibration process

---

**Note**: This file IS tracked by git. Keep sensitive data in `.env` file (gitignored).
