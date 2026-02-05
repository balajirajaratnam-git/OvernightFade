# Source Code Directory

This directory contains all Python source code for the Overnight Fade trading system.

---

## Core Modules

### Trading & Analysis

**backtester.py** ⭐⭐⭐⭐⭐
- **Purpose:** Main backtesting engine
- **Features:**
  - Dual strategy comparison (original vs early exit)
  - DST-safe timezone handling
  - Checks full trading day until options expire (16:00 ET)
  - Intrinsic value calculation for early exits
- **Usage:** `python src/backtester.py`
- **Outputs:** `results/trade_log_ORIGINAL.csv`, `results/trade_log_0935ET.csv`
- **Documentation:** World-class, comprehensive docstrings

**dashboard.py** ⭐⭐⭐⭐⭐
- **Purpose:** Interactive trading dashboard and signal generator
- **Features:**
  - Auto-fetches latest market data
  - Applies LastHourVeto filter
  - Displays execution plans for IBKR and IG.com
  - Shows backtest confidence metrics
- **Usage:** `python src/dashboard.py`
- **Documentation:** World-class, comprehensive docstrings

**strategies.py** ⭐⭐⭐⭐⭐
- **Purpose:** Strategy variants and filters
- **Includes:**
  - BaselineStrategy (original fade logic)
  - ExhaustionFilter (skip momentum days)
  - LastHourVeto (skip late-day continuation)
  - ATRRegimeFilter (skip low volatility)
- **Documentation:** World-class with strategy rationales

---

### Data Management

**data_manager.py** ⭐⭐⭐⭐
- **Purpose:** Fetch and manage market data
- **Features:**
  - Polygon.io API integration
  - Rate limiting with state persistence
  - yfinance fallback
  - Daily bar derivation from intraday data
- **Usage:** `ALLOW_NETWORK=1 python src/data_manager.py`

**session_utils.py** ⭐⭐⭐⭐⭐
- **Purpose:** DST-safe timezone utilities
- **Functions:**
  - `get_overnight_window_utc()` - 16:00 ET to 09:30 ET next day
  - `get_cash_session_window_utc()` - 09:30 ET to 16:00 ET
  - `is_after_cash_close_et()` - Check if market closed
- **Documentation:** World-class, comprehensive

**rate_limiter.py** ⭐⭐⭐⭐⭐
- **Purpose:** API rate limiting and request budget management
- **Features:**
  - Token bucket algorithm
  - Exponential backoff on 429 errors
  - State persistence across runs
  - Cooldown tracking
- **State File:** `logs/state.json`
- **Documentation:** World-class

---

### Optimization & Validation

**optimizer.py**
- **Purpose:** Parameter optimization via grid search
- **Features:**
  - ATR multiple optimization
  - Walk-forward validation
  - Performance metrics calculation
- **Usage:** `python src/optimizer.py`
- **Outputs:** `results/optimization_results.csv`

**strategy_eval.py**
- **Purpose:** Strategy comparison and evaluation
- **Features:**
  - Multi-strategy backtesting
  - Performance statistics
  - Filter effectiveness analysis
- **Usage:** `python src/strategy_eval.py`

**walk_forward.py**
- **Purpose:** Walk-forward validation of parameters
- **Features:**
  - Rolling window optimization
  - Out-of-sample testing
  - Overfitting detection
- **Usage:** `python src/walk_forward.py`

**final_holdout.py**
- **Purpose:** Final validation on holdout data
- **Features:**
  - Last validation before live trading
  - Performance on unseen data
- **Usage:** `python src/final_holdout.py`

---

## Module Dependencies

```
backtester.py
├── session_utils.py (timezone handling)
└── config/config.json (parameters)

dashboard.py
├── backtester.py (historical stats)
├── strategies.py (LastHourVeto filter)
├── session_utils.py (timezone handling)
└── data_manager.py (auto-fetch)

data_manager.py
├── rate_limiter.py (API throttling)
├── session_utils.py (timezone handling)
└── logs/state.json (persistent state)

strategies.py
├── session_utils.py (timezone handling)
└── (standalone, no heavy dependencies)

optimizer.py
├── backtester.py (run backtests)
└── strategies.py (strategy variants)
```

---

## Documentation Status

| File | Lines | Documentation | Status |
|------|-------|---------------|--------|
| backtester.py | 535 | ⭐⭐⭐⭐⭐ | Production |
| dashboard.py | 501 | ⭐⭐⭐⭐⭐ | Production |
| strategies.py | 348 | ⭐⭐⭐⭐⭐ | Production |
| session_utils.py | 92 | ⭐⭐⭐⭐⭐ | Production |
| rate_limiter.py | 220 | ⭐⭐⭐⭐⭐ | Production |
| data_manager.py | 503 | ⭐⭐⭐⭐ | Production |
| optimizer.py | 180 | ⭐⭐⭐⭐ | Production |
| strategy_eval.py | 165 | ⭐⭐⭐⭐ | Production |
| walk_forward.py | 145 | ⭐⭐⭐⭐ | Production |
| final_holdout.py | 98 | ⭐⭐⭐⭐ | Production |

**Total:** ~2,780 lines of production code

---

## Backup Files

**Location:** `src/backup/`

**Contains:**
- Historical versions of modified files
- Pre-fix versions for reference
- Intermediate cleaned versions

**Purpose:** Reference and rollback capability

**Retention:** Keep for 1-2 weeks after verification, then delete

---

## Common Tasks

### Run Full Backtest
```bash
python src/backtester.py
```

### Check Today's Signal
```bash
python src/dashboard.py
```

### Fetch Latest Data
```bash
ALLOW_NETWORK=1 python src/data_manager.py
```

### Optimize Parameters
```bash
python src/optimizer.py
```

### Validate Strategy
```bash
python src/walk_forward.py
python src/final_holdout.py
```

---

## Code Standards

### Documentation
- ✅ Module-level docstrings
- ✅ Class docstrings with purpose
- ✅ Method docstrings with Args/Returns
- ✅ Inline comments explain WHY not WHAT

### Style
- ✅ PEP 8 compliant (mostly)
- ✅ Clear variable names
- ✅ Section markers for long functions
- ✅ Error handling with helpful messages

### Testing
- ✅ Manual testing after changes
- ✅ Regression testing on critical modules
- ⚠️ No automated unit tests (future improvement)

---

## Import Structure

### External Dependencies
```python
import os, json
import pandas as pd
import numpy as np
from datetime import datetime, time, timedelta
import pytz  # Timezone handling
from rich import Console, Table, Panel  # Terminal UI
import requests  # API calls
from dotenv import load_dotenv  # Environment variables
```

### Internal Imports
```python
from backtester import Backtester
from strategies import LastHourVeto, ...
from session_utils import get_overnight_window_utc, TZ_ET, TZ_UTC
from data_manager import DataManager
from rate_limiter import RateLimiter
```

---

## Adding New Modules

### Template Structure
```python
"""
Module Purpose

Brief description of what this module does.

Features:
- Feature 1
- Feature 2

Usage:
    python src/new_module.py
"""

import os
import pandas as pd
# ... other imports

# Module constants
CONSTANT_NAME = value

class ClassName:
    """Class purpose and description."""

    def __init__(self, param1):
        """
        Initialize ClassName.

        Args:
            param1: Description
        """
        pass

    def method_name(self, arg1):
        """
        Method purpose.

        Args:
            arg1: Description

        Returns:
            Description of return value
        """
        pass

if __name__ == "__main__":
    # Entry point for direct execution
    pass
```

---

## Troubleshooting

**Import errors:**
```bash
# Ensure you're in project root
cd /path/to/OvernightFade
python src/module.py
```

**Module not found:**
```bash
# Check Python path
python -c "import sys; print('\n'.join(sys.path))"
```

**Rate limit errors:**
- Check `logs/state.json` for cooldown status
- See `logs/README.md` for state file documentation

---

## Related Documentation

- **config/README.md** - Configuration parameters
- **data/README.md** - Data structure and management
- **logs/README.md** - State file and logging
- **docs/DOCUMENTATION_CLEANUP_SUMMARY.md** - Code quality report

---

*Last updated: 2026-02-04*
