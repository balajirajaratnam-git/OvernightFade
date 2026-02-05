# Source Code Directory

**Last Updated**: 2026-02-05
**Version**: 5.0 (Post-Cleanup)

Core Python library modules for the OvernightFade trading system.

**This folder contains ONLY core library code. All executable scripts are in `scripts/`.**

---

## 📚 Core Library Modules

### **data_manager.py**
Polygon.io API integration for market data fetching.

**Features**:
- Rate limiting with token bucket algorithm
- Minute and daily bar fetching
- Network kill switch (requires ALLOW_NETWORK=1)
- State persistence

**Used by**: All data fetching scripts

---

### **rate_limiter.py**
Token bucket rate limiter for API calls.

**Features**:
- Request budget management
- Exponential backoff on errors
- State persistence in `logs/state.json`

**Used by**: data_manager.py

---

### **session_utils.py**
DST-safe timezone utilities for US market hours.

**Functions**:
- `get_overnight_window_utc()` - 16:00 ET to 09:30 ET next day
- `get_cash_session_window_utc()` - 09:30 ET to 16:00 ET
- `is_after_cash_close_et()` - Check if market closed

**Constants**:
- `TZ_ET` - America/New_York timezone
- `TZ_UTC` - UTC timezone

**Used by**: All trading and backtesting scripts

---

### **strategies.py**
Strategy filters and signal logic.

**Includes**:
- Baseline fade signal generation (RED→CALL, GREEN→PUT)
- LastHourVeto filter
- Flat day exclusion (< 0.10% move)
- Strategy variant implementations

**Used by**: Trading and analysis scripts

---

## 📦 Archive Folder

**Location**: `src/archive/`

Contains legacy/superseded scripts kept for historical reference:
- `backtester_old_exit_comparison.py` - Old exit strategy comparison
- `dashboard_legacy.py` - Pre-SHORT expiries dashboard
- `backtester_multi_ticker.py` - Multi-ticker comparison

**See**: `src/archive/README.md` for details

**DO NOT USE** archived scripts for production trading.

---

## 🔧 Usage

### **Import Pattern**

All scripts that use these modules follow this pattern:

```python
import sys
sys.path.insert(0, 'src')

from data_manager import DataManager
from session_utils import get_overnight_window_utc, TZ_ET
from strategies import LastHourVeto
from rate_limiter import RateLimiter
```

**Important**: Scripts must be run from project root directory.

### **Example**

```python
# In a script in scripts/trading/
import sys
sys.path.insert(0, 'src')

from session_utils import TZ_ET, get_overnight_window_utc
from data_manager import DataManager

# Use the modules
dm = DataManager()
overnight_start, overnight_end = get_overnight_window_utc(trade_date)
```

---

## 📂 Module Dependencies

```
data_manager.py
├── rate_limiter.py
└── session_utils.py

strategies.py
└── session_utils.py

rate_limiter.py
└── (standalone, uses logs/state.json)

session_utils.py
└── (standalone, pure utilities)
```

---

## ⚠️ Important Notes

**This folder should contain**:
- ✅ Core library modules (imported by scripts)
- ✅ Utility functions (timezone, rate limiting)
- ✅ Strategy logic (filters, signals)

**This folder should NOT contain**:
- ❌ Executable scripts (those go in `scripts/`)
- ❌ Backtesting scripts (those go in `scripts/backtesting/`)
- ❌ Trading scripts (those go in `scripts/trading/`)

**If a file has a `if __name__ == "__main__":` block that's meant for regular use, it probably belongs in `scripts/`, not here.**

---

## 🎯 For Daily Trading

You don't need to interact with these modules directly. Use the scripts instead:

**Daily trading**: `python scripts/trading/auto_trade_ig.py`
**Backtesting**: `python scripts/backtesting/run_backtest_ig_short_expiries_reality.py`

**See**: `SCRIPTS_GUIDE.md` in project root for complete usage guide.

---

## Related Documentation

- `SCRIPTS_GUIDE.md` - Which scripts to use and when
- `src/archive/README.md` - Why certain scripts were archived
- `scripts/README.md` - Overview of all executable scripts
- Main `README.md` - Project overview

---

**Remember**: This is the LIBRARY. For daily use, see `SCRIPTS_GUIDE.md`.
