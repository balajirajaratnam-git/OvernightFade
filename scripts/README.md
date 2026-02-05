# Scripts Directory

This directory contains utility scripts and analysis tools.

---

## Directory Structure

```
scripts/
└── analysis/               # Analysis and comparison scripts
    ├── compare_exit_times.py
    ├── analyze_losses.py
    ├── verify_scratches.py
    ├── show_trade_details.py
    └── archive/            # Archived temporary scripts
        ├── test_backtester_simple.py
        └── test_uk_exit.py
```

---

## Analysis Scripts

### compare_exit_times.py
**Purpose:** Compare strategy performance with different exit times

**Usage:**
```bash
python scripts/analysis/compare_exit_times.py
```

**Outputs:**
- Performance comparison table
- Win distribution analysis
- Optimal exit time recommendation

**When to use:**
- Testing new exit strategies
- Validating exit time parameters
- Understanding win timing patterns

---

### analyze_losses.py
**Purpose:** Deep dive into losing trades

**Usage:**
```bash
python scripts/analysis/analyze_losses.py
```

**Analyzes:**
- What percentage of losses could be avoided
- Common patterns in losing trades
- MFE (Maximum Favorable Excursion) analysis

**When to use:**
- Improving strategy filters
- Understanding loss sources
- Identifying risk management opportunities

---

### verify_scratches.py
**Purpose:** Analyze "SCRATCH" outcomes and intrinsic value recovery

**Usage:**
```bash
python scripts/analysis/verify_scratches.py
```

**Shows:**
- Which trades recovered value via early exit
- How much intrinsic value was captured
- Scratch vs full loss comparison

**When to use:**
- Validating early exit logic
- Understanding partial loss recovery
- Comparing exit strategies

---

### show_trade_details.py
**Purpose:** Display detailed trade information with formatting

**Usage:**
```bash
python scripts/analysis/show_trade_details.py
```

**Displays:**
- Trade-by-trade breakdown
- Signal types and outcomes
- P/L distribution

**When to use:**
- Quick results overview
- Generating reports
- Debugging specific trades

---

## Archive Directory

**Location:** `scripts/analysis/archive/`

**Purpose:** Storage for temporary test scripts and debugging code

**Contents:**
- `test_backtester_simple.py` - Basic backtester import test
- `test_uk_exit.py` - UK exit time debugging script

**When to clean:**
- Monthly review and deletion of old test files
- Keep only if actively referenced

---

## Creating New Analysis Scripts

### Template

```python
"""
Script Purpose and Description

Brief explanation of what this script analyzes and why.
"""

import os
import sys
import pandas as pd

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from backtester import Backtester

def main():
    """Main analysis logic."""
    # Load data
    bt = Backtester()
    results = bt.run()

    # Analyze
    # ...

    # Display results
    print("Analysis Results:")
    print("-" * 80)
    # ...

if __name__ == "__main__":
    main()
```

### Naming Conventions

**Analysis scripts:**
- `analyze_*.py` - In-depth analysis of specific aspects
- `compare_*.py` - Comparison between strategies or parameters
- `show_*.py` - Display/reporting scripts
- `verify_*.py` - Validation and sanity checks

**Temporary scripts:**
- `test_*.py` - Testing and debugging
- `temp_*.py` - Temporary exploration
- `scratch_*.py` - Quick experiments

---

## Running Scripts

### From Project Root
```bash
# Recommended approach
python scripts/analysis/compare_exit_times.py
```

### Direct Execution
```bash
# Also works if imports are set up correctly
cd scripts/analysis
python compare_exit_times.py
```

---

## Output Locations

**Console output:** Default for quick analysis
**File output:** Use `results/` directory for persistent results

```python
# Save analysis results
output_path = os.path.join("results", "analysis_output.csv")
df.to_csv(output_path, index=False)
print(f"Results saved to {output_path}")
```

---

## Common Imports

```python
import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

# Import modules
from backtester import Backtester
from strategies import LastHourVeto
from session_utils import get_overnight_window_utc, TZ_ET, TZ_UTC
```

---

## Best Practices

### Script Structure
✅ Clear purpose in docstring
✅ Import statements at top
✅ Main function with logic
✅ `if __name__ == "__main__"` guard
✅ Helpful print statements

### Documentation
✅ Explain what is analyzed
✅ Document assumptions
✅ Show example output in comments
✅ Link to related analysis

### File Management
✅ Save important results to `results/`
✅ Archive old test scripts
✅ Delete temporary files after use
✅ Use descriptive filenames

---

## Maintenance

### Monthly Cleanup

**Review archive:**
```bash
# List archived files
ls -lh scripts/analysis/archive/

# Delete files older than 2 months
find scripts/analysis/archive/ -name "*.py" -mtime +60 -delete
```

**Organize results:**
```bash
# Move old analysis outputs
mkdir -p results/archive/analysis/$(date +%Y-%m)
mv results/analysis_*.csv results/archive/analysis/$(date +%Y-%m)/
```

---

## Related Documentation

- **docs/CORRECTED_STRATEGY_ANALYSIS.md** - Key analysis findings
- **results/README.md** - Where to save analysis outputs
- **src/README.md** - Source code reference

---

## Troubleshooting

**Import errors:**
```python
# Ensure src is in path
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
```

**Data not found:**
```bash
# Run from project root
cd /path/to/OvernightFade
python scripts/analysis/script_name.py
```

**Missing dependencies:**
```bash
# Install requirements
pip install -r requirements.txt
```

---

*Last updated: 2026-02-04*
