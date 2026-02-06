# OvernightFade Trading System - Architecture

**Purpose**: Stable reference document for system architecture, design decisions, and technical specifications. Update only when architecture changes.

---

## 🏗️ System Overview

### What This System Does
Automates SPY options trading using an "overnight fade" strategy that profits from mean reversion after daily moves. Generates signals at market close (16:00 ET), holds options for 1-3 days until expiry.

### Trading Flow
```
16:00 ET Market Close
    ↓
Fetch latest data (Polygon.io or yfinance)
    ↓
Calculate signal (RED day → CALL, GREEN day → PUT)
    ↓
Apply filters (flat day exclusion, optional LastHourVeto)
    ↓
Calculate ATM strike and option premiums (Black-Scholes)
    ↓
Display order details for IG.com (SPX) and IBKR (SPY)
    ↓
Manual execution on broker platform (Phase 1)
    ↓
Hold until expiry (1-3 days)
    ↓
Log results for calibration
```

---

## 📦 Component Architecture

### Layer 1: Data Management
**Purpose**: Fetch and maintain historical and real-time market data

**Components**:
- `src/data_manager.py` - Main data orchestrator
- `src/rate_limiter.py` - API rate limiting (token bucket algorithm)
- `src/session_utils.py` - Timezone calculations (DST-safe)

**Data Sources**:
1. **Polygon.io** (Primary): 10 years daily + intraday data
   - Requires API key in `.env` file
   - Rate limited: 5 calls/minute (basic plan)
   - Data: 2015-01-01 to present
2. **yfinance** (Fallback): Same-day data when Polygon lags
   - No API key needed
   - Used for SPX (^GSPC) live prices
   - Faster updates at market close

**Storage**:
```
data/
├── SPY/
│   ├── daily_OHLCV.parquet        # Pandas DataFrame, DatetimeIndex (UTC)
│   │                               # Columns: Open, Close, High, Low, Volume, vw,
│   │                               #          Direction, Magnitude, ATR_14
│   └── intraday/
│       ├── 2015-01-02.parquet     # One file per trading day
│       ├── 2015-01-05.parquet     # Columns: Open, Close, High, Low, Volume
│       └── ...                     # Index: Datetime (UTC), 1-minute bars
```

**Data Schema**:
```python
# Daily OHLCV
{
    'index': pd.DatetimeIndex (UTC, 00:00 timestamp for each trading day)
    'Open': float64,          # Opening price
    'Close': float64,         # Closing price (16:00 ET)
    'High': float64,          # Intraday high
    'Low': float64,           # Intraday low
    'Volume': int64,          # Total volume
    'vw': float64,            # Volume-weighted average price
    'n': int64,               # Number of transactions
    'Direction': str,         # 'GREEN' if Close > Open else 'RED'
    'Magnitude': float64,     # abs((Close - Open) / Open) * 100 (percentage)
    'ATR_14': float64         # 14-day Average True Range
}
```

**Data Freshness Logic** (Fixed 2026-02-05):
```python
# In data_manager.py update_daily_data()
if last_date_in_file == today:
    if is_after_cash_close_et():  # After 16:00 ET
        # Re-fetch today to get final close (was incomplete)
        start_date = today
    else:
        # Data is current
        return
```

### Layer 2: Strategy Engine
**Purpose**: Generate trading signals based on overnight fade logic

**Components**:
- `src/strategies.py` - Signal generation and filters
- `scripts/trading/auto_trade_ig.py` - Main trading script

**Signal Logic**:
```python
def generate_signal(daily_bar):
    """
    Overnight Fade Strategy:
    - RED day (Close < Open) → BUY CALL (expect bounce)
    - GREEN day (Close > Open) → BUY PUT (expect pullback)
    """
    if abs(daily_bar['Magnitude']) < 0.10:
        return "NO_TRADE"  # Flat day filter

    if daily_bar['Direction'] == 'RED':
        return "BUY CALL"
    else:
        return "BUY PUT"
```

**Filters Available**:
1. **Flat Day Exclusion** (Active): Skip if abs(move) < 0.10%
2. **LastHourVeto** (Optional): Skip if price reverses in last hour
3. **VIX Filter** (Future): Skip if VIX > threshold

**Expiry Calculation**:
```python
# Short expiries (1-3 days)
day_of_week = {
    0: "Monday" → Wednesday (2 days),
    1: "Tuesday" → Wednesday (1 day),
    2: "Wednesday" → Friday (2 days),
    3: "Thursday" → Friday (1 day),
    4: "Friday" → Monday (3 days)
}
```

### Layer 3: Option Pricing
**Purpose**: Calculate option premiums and limit prices

**Components**:
- `scripts/analysis/measure_reality_framework.py` - Black-Scholes implementation

**Black-Scholes Inputs**:
```python
S = current_price        # Underlying price (SPY or SPX)
K = strike_price        # ATM strike (rounded appropriately)
T = days_to_expiry / 365.0  # Time in years (e.g., 1/365 for 1-day)
r = 0.05                # Risk-free rate (5% annual)
sigma = 0.15            # Implied volatility (15% assumption, needs calibration)
```

**Black-Scholes Outputs**:
```python
{
    'price': float,      # Option premium
    'delta': float,      # Δ (sensitivity to underlying)
    'gamma': float,      # Γ (sensitivity of delta)
    'vega': float,       # ν (sensitivity to IV)
    'theta': float       # Θ (time decay per day)
}
```

**Premium Calculation Flow**:
```python
# 1. Calculate entry premium
entry_premium = black_scholes_call(S, K, T, r, sigma)['price']

# 2. Apply reality adjustment
pnl_multiplier = 0.72  # For 1-day SPY (from config)
realistic_win_pct = pnl_multiplier * 45  # 45% backtest → 32.4% realistic

# 3. Calculate target premium
target_premium = entry_premium * (1 + realistic_win_pct / 100)

# 4. For display
limit_price = target_premium
limit_pts = target_premium - entry_premium
```

### Layer 4: Broker Adaptation
**Purpose**: Generate correct order details for different brokers

**IG.com (US 500 = SPX)**:
```python
# Fetch live SPX price
spx = yfinance.Ticker('^GSPC')
spx_close = spx.history(period='1d')['Close'].iloc[-1]  # e.g., 6798.40

# Calculate ATM strike (5-point increments)
strike = round(spx_close / 5) * 5  # e.g., 6800

# Calculate premiums using SPX price
entry_premium_spx = black_scholes_call(
    S = spx_close / 10,      # Convert to SPY-equivalent for B-S
    K = strike / 10,
    T = days / 365.0,
    r = 0.05,
    sigma = 0.15
)['price'] * 10  # Scale back to SPX points
```

**IBKR (SPY = ETF)**:
```python
# Use actual SPY price (from backtest data)
spy_close = daily_bar['Close']  # e.g., 677.62

# Calculate ATM strike (1-dollar increments)
strike = round(spy_close)  # e.g., 678

# Calculate premiums using SPY price
entry_premium_spy = black_scholes_call(
    S = spy_close,
    K = strike,
    T = days / 365.0,
    r = 0.05,
    sigma = 0.15
)['price']
```

**Key Insight**: SPX ≠ SPY * 10
```
SPX: 6798.40 (S&P 500 Index)
SPY: 677.62 (S&P 500 ETF)
SPY * 10: 6776.20
Difference: 22.20 points (persistent ~20-25 point spread)
```

---

## 🗄️ File Structure & Responsibilities

### Root Directory
```
OvernightFade/
├── .git/                    # Git repository
├── .gitignore               # Excludes /data/, /logs/, /results/
├── .gitattributes           # Line ending normalization
├── .env                     # API keys (POLYGON_API_KEY=xxx)
├── README.md                # Project overview
├── SCRIPTS_GUIDE.md         # Which scripts to run when
├── STATUS.md                # Current status (living document)
├── SYSTEM.md                # This file (stable reference)
├── NEXT.md                  # Action queue
├── GIT_SETUP_GUIDE.md       # Git initialization guide
├── requirements.txt         # Python dependencies
├── config/                  # Configuration files
├── data/                    # Market data (ignored by git)
├── logs/                    # Log files (ignored by git)
├── results/                 # Backtest results (ignored by git)
├── scripts/                 # Executable scripts
└── src/                     # Library modules
```

### scripts/ - Executable Scripts

#### scripts/trading/
- **auto_trade_ig.py** ⭐ PRIMARY SCRIPT
  - **Purpose**: Generate daily trading signals
  - **When**: Run at 16:00 ET (21:00 UK) after market close
  - **Output**: Order details for IG.com and IBKR, logs to CSV
  - **Dependencies**: data_manager, session_utils, strategies, measure_reality_framework
  - **Key Functions**:
    - `check_trading_day()` - Determine expiry based on day of week
    - `wait_for_polygon_data()` - Poll until close data available
    - `generate_signal()` - RED/GREEN → CALL/PUT logic
    - `calculate_order_details()` - SPX/SPY handling, Black-Scholes premiums
    - `display_order_summary()` - Format output for both brokers

#### scripts/backtesting/
- **run_backtest_ig_short_expiries_reality.py**
  - **Purpose**: 10-year backtest with reality adjustments
  - **When**: Monthly to verify strategy performance
  - **Output**: results/ig_short_expiries_reality_backtest.csv
  - **Expected**: 34.3% CAGR, 86% WR, 1671 trades
  - **Key Features**: Applies spreads, slippage, theta, commission

#### scripts/data/
- **fetch_multi_ticker_data.py**
  - **Purpose**: Fetch 10 years of historical data
  - **When**: Initial setup, quarterly updates
  - **Time**: 2-4 hours (rate-limited)
  - **Tickers**: SPY, VIX, XLK, XLF, XLE, XLV, XLY, XLU, XLRE, XLI, XLB

- **verify_multi_ticker_data.py**
  - **Purpose**: Check data completeness
  - **When**: After fetch, or to diagnose data issues
  - **Output**: Table showing date ranges, file counts, status

#### scripts/analysis/
- **measure_reality_framework.py**
  - **Purpose**: Black-Scholes option pricing
  - **Functions**: `black_scholes_call()`, `black_scholes_put()`
  - **Used by**: auto_trade_ig.py for premium calculations

- **paper_trading_log.py**
  - **Purpose**: Log paper trade results vs predictions
  - **When**: After each paper trade closes
  - **Output**: Calibration data for reality adjustments

- **parameter_optimizer.py**
  - **Purpose**: Grid search for optimal ATR multiplier
  - **When**: Quarterly or when optimizing strategy
  - **Time**: 30-60 minutes

- **strategy_comparison.py**
  - **Purpose**: Compare baseline vs filtered strategies
  - **When**: Evaluating new filters (e.g., LastHourVeto)

- **walk_forward_validation.py**
  - **Purpose**: Out-of-sample validation with rolling windows
  - **When**: Validating strategy robustness

- **validation_holdout.py**
  - **Purpose**: Final one-time test on reserved holdout period
  - **When**: Before going live (done once)

### src/ - Core Library Modules

#### data_manager.py
**Purpose**: Centralized data fetching and caching

**Key Classes/Functions**:
```python
class DataManager:
    def __init__(self, ticker="SPY"):
        self.ticker = ticker
        self.ticker_dir = f"data/{ticker}"
        self.rate_limiter = RateLimiter()

    def update_daily_data(self):
        """Fetch/update daily OHLCV with staleness detection"""

    def update_intraday_data(self, date_range):
        """Fetch minute bars for specified dates"""

    def derive_daily_from_intraday(self, date_obj):
        """Create daily bar from minute data (same-evening dashboard)"""
```

**Network Kill Switch**:
```python
def assert_network_allowed():
    if os.getenv('ALLOW_NETWORK') != '1':
        raise RuntimeError("Set ALLOW_NETWORK=1 to enable data fetching")
```

#### rate_limiter.py
**Purpose**: Token bucket rate limiting for Polygon.io API

**Algorithm**:
```python
class RateLimiter:
    def __init__(self, max_calls=5, time_window=60):
        self.max_calls = 5       # 5 calls per minute (basic plan)
        self.time_window = 60    # 60 seconds
        self.tokens = 5          # Current token count
        self.last_refill = time.time()

    def acquire(self):
        """Block until token available, then consume"""
        # Refill tokens based on time elapsed
        # Wait if no tokens available
        # Consume token
```

**State Persistence**:
- Saves state to `logs/state.json` after each call
- Restores state on restart (prevents rate limit violations)

#### session_utils.py
**Purpose**: DST-safe timezone utilities for US market hours

**Key Functions**:
```python
TZ_ET = pytz.timezone('America/New_York')   # Handles DST automatically
TZ_UTC = pytz.timezone('UTC')

def get_overnight_window_utc(trade_date):
    """Returns (start, end) in UTC for overnight session
    16:00 ET on trade_date → 09:30 ET next day"""

def get_cash_session_window_utc(trade_date):
    """Returns (start, end) in UTC for regular hours
    09:30 ET → 16:00 ET on trade_date"""

def is_after_cash_close_et():
    """True if current time > 16:00 ET (market closed)"""
```

**Why UTC for storage?**
- DST transitions happen automatically
- No ambiguity with 2am hour (spring forward)
- Consistent with Polygon.io timestamps

#### strategies.py
**Purpose**: Signal generation and strategy filters

**Key Functions**:
```python
def generate_baseline_signal(context):
    """RED → CALL, GREEN → PUT with flat filter"""

class LastHourVeto:
    """Filter trades where price reverses in last hour"""
    def should_veto(self, intraday_df, signal):
        # Check if price moved against signal in final hour
        # Return True to veto, False to allow
```

---

## 🔧 Configuration System

### config/config.json
**Purpose**: Main strategy parameters

```json
{
  "ticker": "SPY",
  "lookback_years": 10,
  "premium_budget": 1000,
  "vix_ticker": "VIX",
  "sector_etfs": ["XLK", "XLF", "XLE", "XLV", "XLY", "XLU", "XLRE", "XLI", "XLB"],
  "tickers": ["SPY"]
}
```

**Parameters**:
- `ticker`: Primary ticker for backtesting (always "SPY")
- `lookback_years`: Backtest period (10 years = 2015-2025)
- `premium_budget`: Position sizing basis ($1000 per trade)
- `vix_ticker`: For future VIX filtering
- `sector_etfs`: For future correlation analysis
- `tickers`: Multi-ticker list (SPY-only recommended)

### config/reality_adjustments.json
**Purpose**: Reality adjustment factors for option pricing

```json
{
  "spread_costs": {
    "SPY": 0.03,      // 3% of premium lost to bid/ask spread
    "QQQ": 0.05,
    "IWM": 0.10,
    "DIA": 0.15
  },
  "slippage_pct": {
    "SPY": 0.008,     // 0.8% slippage on fills
    "QQQ": 0.015,
    "IWM": 0.023,
    "DIA": 0.031
  },
  "commission_per_contract": 0.65,  // $0.65 per contract
  "pnl_adjustments": {
    "1_day": {
      "SPY": 0.72,    // 72% of backtest expectation
      "QQQ": 0.58,
      "IWM": 0.28,
      "DIA": 0.12
    },
    "2_day": {
      "SPY": 0.65,
      "QQQ": 0.51,
      "IWM": 0.24,
      "DIA": 0.09
    },
    "3_day": {
      "SPY": 0.58,
      "QQQ": 0.45,
      "IWM": 0.20,
      "DIA": 0.06
    }
  }
}
```

**How Adjustments Are Applied**:
```python
# Backtest assumes +45% profit on wins
backtest_win_pct = 45

# Reality adjustment for 1-day SPY
pnl_multiplier = 0.72

# Realistic expectation
realistic_win_pct = backtest_win_pct * pnl_multiplier  # 32.4%

# Premium calculation
entry_premium = 30  # Example
target_premium = entry_premium * (1 + realistic_win_pct / 100)  # 39.72
```

**Why These Values?**
- Derived from Black-Scholes modeling with typical spreads
- Short expiries have higher theta decay → lower multiplier
- Less liquid tickers have higher spreads → lower multiplier
- **NOT YET CALIBRATED** - needs 10-20 real trades to verify

---

## 🎯 Key Algorithms

### 1. ATM Strike Calculation

**IG.com (US 500) - 5-Point Increments**:
```python
def calculate_strike_us500(spx_close):
    """
    Round SPX price to nearest 5-point increment
    Examples:
        6798.40 → 6800
        6802.50 → 6805
        6797.00 → 6795
    """
    return int(round(spx_close / 5) * 5)
```

**IBKR (SPY) - 1-Dollar Increments**:
```python
def calculate_strike_spy(spy_close):
    """
    Round SPY price to nearest dollar
    Examples:
        677.62 → 678
        677.20 → 677
        677.50 → 678
    """
    return int(round(spy_close))
```

### 2. ATR Target Calculation

**Purpose**: Exit when underlying moves X * ATR from entry

```python
# ATR is 14-day Average True Range
# Default multiplier: 0.1 (10% of ATR)

spy_atr = 52.34  # From historical data
target_move = spy_atr * 0.1  # 5.23 points

if signal == "BUY CALL":
    underlying_target = current_price + target_move
else:  # BUY PUT
    underlying_target = current_price - target_move
```

**Why 0.1 ATR?**
- Optimized via grid search (parameter_optimizer.py)
- Balance between hit rate and profit per trade
- Lower = higher hit rate but smaller profits
- Higher = lower hit rate but larger profits

### 3. Position Sizing (Kelly Criterion)

**Backtest Uses Fixed $1000 per Trade**:
```python
premium_budget = 1000  # Per trade
account_size = 19200   # Scaled to match 5.23% sizing

# Example:
entry_premium = 30  # $30 per option
contracts = premium_budget / (entry_premium * 100)  # 0.33 contracts

# Max loss per trade
max_loss_dollars = premium_budget  # $1000 (100% of premium)
max_loss_pct = max_loss_dollars / account_size  # 5.23%
```

**Kelly Formula** (for reference, not actively used):
```python
f = (p * b - q) / b

# Where:
# f = fraction of capital to risk
# p = win probability (0.86)
# q = loss probability (0.14)
# b = win/loss ratio (28.5% / 103% ≈ 0.276)

# Result: f ≈ 5-7% (matches our 5.23% sizing)
```

---

## 🔐 Security & Best Practices

### API Key Management
- Store in `.env` file (ignored by git)
- Format: `POLYGON_API_KEY=your_key_here`
- Never commit to repository
- Use environment variables only

### Data Integrity
- Parquet files for efficient storage
- Atomic writes (temp file → rename)
- Corruption detection (try/catch on read)
- Automatic re-download if corrupt

### Rate Limiting
- Token bucket algorithm prevents API bans
- State persistence across restarts
- Exponential backoff on errors
- Network kill switch (ALLOW_NETWORK=1)

### Git Exclusions
```gitignore
/data/           # Large files, user-specific
/logs/           # Runtime logs, not source
/results/        # Backtest outputs, regenerable
__pycache__/     # Python cache
*.pyc            # Compiled Python
.env             # API keys and secrets
```

---

## 🧪 Testing Strategy

### Manual Testing (Current)
- Run auto_trade_ig.py daily, verify output
- Compare calculated vs actual broker prices
- Log paper trades for calibration

### Automated Testing (Future)
```python
# Unit tests for core functions
def test_strike_calculation():
    assert calculate_strike_us500(6798.40) == 6800
    assert calculate_strike_spy(677.62) == 678

def test_signal_generation():
    red_day = {'Direction': 'RED', 'Magnitude': 0.5}
    assert generate_signal(red_day) == 'BUY CALL'

def test_flat_day_filter():
    flat_day = {'Direction': 'RED', 'Magnitude': 0.05}
    assert generate_signal(flat_day) == 'NO_TRADE'
```

### Backtesting Validation
- Walk-forward: 19 folds, train/test split
- Holdout: Reserved 3-month period
- Reality check: Paper trade 10-20 times before live

---

## 📊 Performance Metrics

### Backtest Metrics
- **CAGR**: Compound Annual Growth Rate
- **Win Rate**: % of profitable trades
- **Max Drawdown**: Largest peak-to-trough decline
- **Sharpe Ratio**: Risk-adjusted returns
- **Trade Count**: Total trades over period

### Expected Performance (SPY, 1-Day Expiry, Reality-Adjusted)
```
CAGR: 34.3%
Win Rate: 86.3%
Avg Win: +28.5%
Avg Loss: -103.1% (of premium, not account)
Max Loss: -5.23% (of account, per trade)
Trades/Year: ~160 (Mon-Fri, minus flat days)
```

### Reality Adjustment Impact
```
Idealized Backtest:     48.8% CAGR, 89% WR
Reality-Adjusted:       34.3% CAGR, 86% WR
Degradation:            -14.5% CAGR, -3% WR

Factors:
- Bid/ask spreads:      -3% per trade
- Slippage:             -0.8% per trade
- Theta decay:          Captured in 0.72x multiplier
- Commission:           -0.13% per trade ($0.65 / $500 premium)
```

---

## 🔄 Data Flow Diagram

```
┌─────────────────────────────────────────────────────────┐
│ User runs: python scripts/trading/auto_trade_ig.py     │
└────────────────────────┬────────────────────────────────┘
                         │
                         ↓
┌─────────────────────────────────────────────────────────┐
│ 1. Check current time & day of week                     │
│    - Determine expiry (Mon→Wed, Tue→Wed, Wed→Fri, etc) │
│    - Verify after 16:00 ET (market close)               │
└────────────────────────┬────────────────────────────────┘
                         │
                         ↓
┌─────────────────────────────────────────────────────────┐
│ 2. Fetch/Update Data (data_manager.py)                  │
│    ┌──────────────────────────────────────────────┐    │
│    │ Check if daily data is stale:                │    │
│    │ - Last date = today AND after_close?         │    │
│    │   → Re-fetch (Polygon or yfinance)           │    │
│    │ - Else: Data is current                      │    │
│    └──────────────────────────────────────────────┘    │
│    Output: data/SPY/daily_OHLCV.parquet                │
└────────────────────────┬────────────────────────────────┘
                         │
                         ↓
┌─────────────────────────────────────────────────────────┐
│ 3. Generate Signal (strategies.py)                      │
│    ┌──────────────────────────────────────────────┐    │
│    │ Read latest daily bar:                       │    │
│    │ - Direction: RED or GREEN                    │    │
│    │ - Magnitude: abs((Close - Open) / Open)      │    │
│    │ - ATR: 14-day average true range             │    │
│    └──────────────────────────────────────────────┘    │
│    ┌──────────────────────────────────────────────┐    │
│    │ Apply filters:                               │    │
│    │ - Flat day? (Magnitude < 0.10%) → NO_TRADE   │    │
│    │ - LastHourVeto? (optional)                   │    │
│    └──────────────────────────────────────────────┘    │
│    ┌──────────────────────────────────────────────┐    │
│    │ Generate signal:                             │    │
│    │ - RED → BUY CALL                             │    │
│    │ - GREEN → BUY PUT                            │    │
│    └──────────────────────────────────────────────┘    │
│    Output: "BUY CALL" or "BUY PUT" or "NO_TRADE"       │
└────────────────────────┬────────────────────────────────┘
                         │
                         ↓
┌─────────────────────────────────────────────────────────┐
│ 4. Calculate Order Details (calculate_order_details)    │
│    ┌──────────────────────────────────────────────┐    │
│    │ IG.com (US 500 = SPX):                       │    │
│    │ - Fetch live SPX: yfinance.Ticker('^GSPC')   │    │
│    │ - Strike: round(SPX / 5) * 5                 │    │
│    │ - ATR: SPY_ATR * 10                          │    │
│    └──────────────────────────────────────────────┘    │
│    ┌──────────────────────────────────────────────┐    │
│    │ IBKR (SPY = ETF):                            │    │
│    │ - Use actual SPY close from data             │    │
│    │ - Strike: round(SPY)                         │    │
│    │ - ATR: SPY_ATR                               │    │
│    └──────────────────────────────────────────────┘    │
│    ┌──────────────────────────────────────────────┐    │
│    │ Calculate Premiums (Black-Scholes):          │    │
│    │ - Entry: BS_call/put(S, K, T, r=0.05, σ=0.15)│    │
│    │ - Target: Entry * (1 + realistic_win% / 100) │    │
│    │ - realistic_win% = 0.72 * 45% = 32.4%        │    │
│    └──────────────────────────────────────────────┘    │
└────────────────────────┬────────────────────────────────┘
                         │
                         ↓
┌─────────────────────────────────────────────────────────┐
│ 5. Display Output (display_order_summary)               │
│    ┌──────────────────────────────────────────────┐    │
│    │ Table Summary:                               │    │
│    │ | IG     | IBKR | Strike(IG) | Strike(IBKR)| │    │
│    │ | US 500 | SPY  | 6800       | 678         | │    │
│    └──────────────────────────────────────────────┘    │
│    ┌──────────────────────────────────────────────┐    │
│    │ IG.com Details:                              │    │
│    │ - Entry Premium (BUY): 21 pts                │    │
│    │ - Target Premium (SELL Limit): 28 pts        │    │
│    │ - Action: BUY at ~21, SELL limit at 28       │    │
│    └──────────────────────────────────────────────┘    │
│    ┌──────────────────────────────────────────────┐    │
│    │ IBKR Details:                                │    │
│    │ - Entry Premium (BUY): 2 pts                 │    │
│    │ - Target Premium (SELL Limit): 2.6 pts       │    │
│    │ - Action: BUY at ~2, SELL limit at 2.6       │    │
│    └──────────────────────────────────────────────┘    │
│    ┌──────────────────────────────────────────────┐    │
│    │ Expected P&L:                                │    │
│    │ - Win: +28.5% (86% probability)              │    │
│    │ - Loss: -103.1% (14% probability)            │    │
│    │ - Expected Value: +8.7% per trade            │    │
│    └──────────────────────────────────────────────┘    │
└────────────────────────┬────────────────────────────────┘
                         │
                         ↓
┌─────────────────────────────────────────────────────────┐
│ 6. Log to CSV (logs/ig_orders_dryrun.csv)               │
│    Columns: Date, Ticker, Signal, Strike, Entry, Target,│
│             Expiry, Expected_PnL, etc.                   │
└────────────────────────┬────────────────────────────────┘
                         │
                         ↓
┌─────────────────────────────────────────────────────────┐
│ 7. User Manual Execution (Phase 1)                      │
│    - Open IG.com or IBKR platform                       │
│    - Find US 500 (IG) or SPY (IBKR) options             │
│    - BUY CALL/PUT at calculated strike                  │
│    - Set SELL limit order at target premium             │
│    - Hold until expiry (1-3 days)                       │
└─────────────────────────────────────────────────────────┘
```

---

## 🚀 Future Enhancements

### Phase 2: API Integration
- IG.com API for automated order placement
- IBKR API as alternative
- Live IV fetching instead of 15% assumption
- Real-time position monitoring

### Phase 3: Advanced Features
- VIX filtering (skip trades when VIX > threshold)
- Correlation analysis with sector ETFs
- Multi-timeframe confirmation
- Machine learning for entry timing

### Phase 4: Risk Management
- Maximum drawdown limits
- Dynamic position sizing based on volatility
- Portfolio-level risk controls
- Stop-loss automation

---

**Architecture Version**: 1.0 (2026-02-05)
**Last Major Update**: SPX vs SPY correction, option premium display
