# Folder Organization Guide

## What Changed

All files have been organized into appropriate folders. The root directory is now clean!

---

## New Folder Structure

```
OvernightFade/
│
├── 📄 README.md                 # Project overview and quick start
├── 📄 .gitignore                # Keep repository clean
├── 📄 requirements.txt          # Python dependencies
├── 📄 project_setup.py          # Project setup script
│
├── 📁 config/                   # Configuration files
│   └── config.json              # Trading parameters
│
├── 📁 src/                      # Source code (your main code)
│   ├── backtester.py            # Main backtesting engine
│   ├── dashboard.py             # Trading dashboard
│   ├── data_manager.py          # Data management
│   └── ...
│
├── 📁 results/                  # 🆕 All backtest outputs go here
│   ├── trade_log_ORIGINAL.csv   # Strategy 1 results
│   ├── trade_log_0935ET.csv     # Strategy 2 results
│   └── backtest_*.csv           # Various comparison results
│
├── 📁 docs/                     # 🆕 All documentation goes here
│   ├── UNDERSTANDING_SCRATCHES.md
│   ├── FINAL_CORRECTED_ANALYSIS.md
│   ├── FOLDER_ORGANIZATION.md   # This file!
│   └── ...
│
├── 📁 scripts/                  # 🆕 Utility scripts
│   └── analysis/                # Analysis and comparison scripts
│       ├── compare_exit_times.py
│       ├── analyze_losses.py
│       └── ...
│
├── 📁 data/                     # Market data
│   └── SPY/
│
├── 📁 logs/                     # Application logs
├── 📁 tests/                    # Unit tests
└── 📁 experiments/              # Experimental code
```

---

## Where Things Go Now

| File Type | Location | Example |
|-----------|----------|---------|
| **Backtest Results** | `results/` | `trade_log_0935ET.csv` |
| **Analysis Reports** | `docs/` | `FINAL_CORRECTED_ANALYSIS.md` |
| **Comparison Scripts** | `scripts/analysis/` | `compare_exit_times.py` |
| **Source Code** | `src/` | `backtester.py` |
| **Configuration** | `config/` | `config.json` |
| **Logs** | `logs/` | `app.log` |

---

## Automatic File Placement

The backtester now **automatically** saves outputs to the correct folders:

```python
# When you run:
python src/backtester.py

# Files are saved to:
results/trade_log_ORIGINAL.csv
results/trade_log_0935ET.csv
```

No more clutter in the root directory! 🎉

---

## Files Moved

### Moved to `results/`
- ✅ All `*.csv` files (backtest results)
- ✅ `trade_log*.csv` files
- ✅ `backtest_*.csv` files
- ✅ `optimization_results.csv`

### Moved to `docs/`
- ✅ All `*.md` files (documentation)
- ✅ Analysis reports
- ✅ Strategy comparisons

### Moved to `scripts/analysis/`
- ✅ `analyze_*.py` (analysis scripts)
- ✅ `compare_*.py` (comparison scripts)
- ✅ `test_*.py` (test scripts)
- ✅ `verify_*.py` (verification scripts)
- ✅ `show_*.py` (display scripts)

### Stayed in Root
- ✅ `README.md` (project overview)
- ✅ `requirements.txt` (dependencies)
- ✅ `project_setup.py` (setup script)
- ✅ `.env` (environment variables)
- ✅ `.gitignore` (git ignore rules)

---

## Benefits

### 1. Clean Root Directory
```
# Before (30+ files) ❌
OvernightFade/
├── backtest_exit_08uk.csv
├── backtest_exit_14uk.csv
├── compare_exit_times.py
├── ANALYSIS.md
├── ... (25 more files!)

# After (5 files) ✅
OvernightFade/
├── README.md
├── requirements.txt
├── project_setup.py
├── .env
└── .gitignore
```

### 2. Easy Navigation
- Know exactly where to find results
- Documentation in one place
- Scripts organized by purpose

### 3. Future-Proof
- New CSV outputs → automatically go to `results/`
- New analysis scripts → put in `scripts/analysis/`
- New docs → put in `docs/`

---

## Quick Commands

### Run Backtest
```bash
python src/backtester.py
# Results automatically saved to results/ folder
```

### Find Latest Results
```bash
ls -lt results/  # See most recent results
```

### Read Analysis
```bash
cat docs/FINAL_CORRECTED_ANALYSIS.md
```

### Run Analysis Script
```bash
python scripts/analysis/compare_exit_times.py
```

---

## Maintenance

### Adding New Files

**For backtest results:**
```python
# In your code:
import os
df.to_csv(os.path.join("results", "my_new_backtest.csv"))
```

**For documentation:**
```bash
# Save markdown files to:
docs/MY_NEW_ANALYSIS.md
```

**For analysis scripts:**
```bash
# Save Python scripts to:
scripts/analysis/my_new_script.py
```

### Cleaning Up

If files accidentally appear in root, run:
```bash
# Move CSV files
mv *.csv results/

# Move markdown files
mv *.md docs/

# Move analysis scripts
mv analyze_*.py compare_*.py scripts/analysis/
```

---

## .gitignore

A `.gitignore` file has been added to prevent temporary files from cluttering the repository:

- Ignores Python cache files (`__pycache__/`)
- Ignores temporary results
- Ignores log files
- Ignores IDE files
- Keeps large data files local

---

## Summary

✅ **Root directory is now clean**
✅ **All files organized by type**
✅ **Backtester automatically saves to results/**
✅ **Future files will stay organized**
✅ **Easy to find what you need**

**Your project is now properly organized!** 🎉

---

*Last updated: 2026-02-04*
