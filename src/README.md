# Source Code Directory

**Last Updated**: 2026-02-05
**Version**: 5.0

Core Python library modules for the OvernightFade trading system.

---

## Core Modules

### **backtester.py**
Single-ticker backtesting engine (legacy).

**Usage**: Called by backtest scripts in `scripts/backtesting/`

### **backtester_multi_ticker.py** ⭐
Multi-ticker backtesting with performance comparison.

**Usage**:
```bash
python src/backtester_multi_ticker.py
```

### **data_manager.py**
Polygon.io API integration for market data fetching.

**Features**:
- Rate limiting
- Minute and daily bars
- Network kill switch

### **session_utils.py**
DST-safe timezone utilities for US market hours.

**Functions**:
- `get_overnight_window_utc()` - 16:00 ET to 09:30 ET
- `get_cash_session_window_utc()` - 09:30 ET to 16:00 ET

### **strategies.py**
Strategy filters and signal logic.

**Includes**:
- Fade green/red logic
- LastHourVeto filter
- Flat day exclusion

### **rate_limiter.py**
Token bucket rate limiter for API calls.

**State file**: `logs/state.json`

### **dashboard.py**
Trading dashboard (legacy, use auto_trade_ig.py instead).

### **optimizer.py**
Parameter optimization via grid search.

### **strategy_eval.py**
Strategy comparison and evaluation.

### **walk_forward.py**
Walk-forward validation.

### **final_holdout.py**
Final holdout validation.

---

## Usage

### **Import from Scripts**

Scripts use this pattern:
```python
import sys
sys.path.insert(0, 'src')

from backtester import Backtester
from data_manager import DataManager
```

**Important**: Must run scripts from project root, not from scripts/ subdirectory.

### **Direct Execution**

Some modules can run standalone:
```bash
# Run multi-ticker backtest
python src/backtester_multi_ticker.py

# Fetch data
ALLOW_NETWORK=1 python src/data_manager.py
```

---

## Module Dependencies

```
backtester.py
├── session_utils.py
└── config/config.json

data_manager.py
├── rate_limiter.py
└── session_utils.py

All modules
└── pandas, numpy, pytz, rich
```

---

## Adding New Modules

Follow this structure:
```python
"""
Module purpose and description.
"""
import os
import pandas as pd

class ClassName:
    """Class purpose."""

    def __init__(self):
        """Initialize."""
        pass

if __name__ == "__main__":
    # Entry point
    main()
```

---

## Related Documentation

- `scripts/README.md` - Scripts that use these modules
- `config/README.md` - Configuration files
- Main `README.md` - Project overview

---

**Note**: All modules are tracked by git. Use proper imports as shown above.
