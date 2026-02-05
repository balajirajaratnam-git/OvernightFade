# Git Setup Guide - Step-by-Step

**Project**: OvernightFade Trading Strategy
**Version**: 5.0
**Date**: 2026-02-05

---

## 🎯 Overview

This guide will walk you through setting up Git version control for the OvernightFade project. Follow these steps exactly as written.

---

## ✅ Prerequisites

- Git installed on your system
- Project reorganization complete (see REORGANIZATION_SUMMARY.md)
- `.gitignore` file in place

**Check Git installation:**
```bash
git --version
```

If not installed, download from: https://git-scm.com/downloads

---

## 📋 Step-by-Step Instructions

### **Step 1: Navigate to Project Directory**

```bash
cd /c/Users/balaj/OneDrive/Trading/OvernightFade
```

Verify you're in the correct directory:
```bash
pwd
ls README.md  # Should exist
```

---

### **Step 2: Initialize Git Repository**

```bash
git init
```

**Expected output:**
```
Initialized empty Git repository in /c/Users/balaj/OneDrive/Trading/OvernightFade/.git/
```

**What this does**: Creates a `.git` folder to track version history.

---

### **Step 3: Configure Git (First Time Only)**

If you haven't configured Git globally, set your name and email:

```bash
# Set your name (visible in commits)
git config --global user.name "Your Name"

# Set your email (visible in commits)
git config --global user.email "your.email@example.com"

# Verify configuration
git config --global --list
```

**For this project only** (optional, if you want different identity):
```bash
git config user.name "Your Name"
git config user.email "your.email@example.com"
```

---

### **Step 4: Check Git Status**

```bash
git status
```

**Expected output**:
```
On branch master

No commits yet

Untracked files:
  (use "git add <file>..." to include in what will be committed)
        .gitignore
        README.md
        backtest.py
        config/
        docs/
        fetch.py
        requirements.txt
        scripts/
        src/
        tests/
        trade.py
        ...

nothing added to commit but untracked files present (use "git add" to track)
```

**What you should NOT see**:
- `data/` (should be ignored)
- `logs/` (should be ignored)
- `results/` (should be ignored)
- `__pycache__/` (should be ignored)
- `.env` (should be ignored if exists)

If you see these, your `.gitignore` is not working correctly.

---

### **Step 5: Stage All Files**

```bash
git add .
```

**What this does**: Stages all files for commit (respects .gitignore).

**Verify what was staged:**
```bash
git status
```

**Expected output**:
```
On branch master

No commits yet

Changes to be committed:
  (use "git rm --cached <file>..." to unstage)
        new file:   .gitignore
        new file:   README.md
        new file:   backtest.py
        new file:   config/config.json
        new file:   config/reality_adjustments.json
        new file:   docs/backtests/AUTO_TRADER_REALITY_ADJUSTMENTS.md
        ...
        new file:   scripts/trading/auto_trade_ig.py
        new file:   scripts/trading/dashboard_pro.py
        new file:   src/backtester.py
        new file:   src/data_manager.py
        ...
```

**Check file count:**
```bash
git status --short | wc -l
```

Should be around 50-70 files (not thousands).

---

### **Step 6: Create Initial Commit**

```bash
git commit -m "Initial commit: OvernightFade v5.0 - SHORT Expiries strategy

- Complete project reorganization following Python best practices
- SHORT expiries backtest (1-3 day options)
- Reality-adjusted P&L calculations with Black-Scholes
- SPY-only configuration (34.3% CAGR expected)
- IG.com and IBKR platform support
- Comprehensive documentation and user guides
- Paper trading framework with logging
- Convenience wrapper scripts for ease of use

Project Structure:
- src/: Core library code
- scripts/: Organized by category (trading, backtesting, data, analysis)
- docs/: Comprehensive documentation
- config/: Strategy configuration and reality adjustments

Status: Ready for paper trading"
```

**Expected output:**
```
[master (root-commit) abc1234] Initial commit: OvernightFade v5.0 - SHORT Expiries strategy
 XX files changed, XXXX insertions(+)
 create mode 100644 .gitignore
 create mode 100644 README.md
 ...
```

---

### **Step 7: Verify Commit**

```bash
git log
```

**Expected output:**
```
commit abc1234567890... (HEAD -> master)
Author: Your Name <your.email@example.com>
Date:   Wed Feb 5 XX:XX:XX 2026 +0000

    Initial commit: OvernightFade v5.0 - SHORT Expiries strategy

    - Complete project reorganization following Python best practices
    ...
```

**Check commit stats:**
```bash
git show --stat
```

---

### **Step 8: Create .gitattributes** (Cross-Platform Compatibility)

```bash
cat > .gitattributes << 'EOF'
# Auto detect text files and perform LF normalization
* text=auto

# Python files
*.py text eol=lf
*.pyx text eol=lf

# Shell scripts
*.sh text eol=lf

# Windows scripts
*.bat text eol=crlf
*.cmd text eol=crlf

# Markdown
*.md text eol=lf

# JSON
*.json text eol=lf

# CSV
*.csv text eol=lf

# Binary files
*.parquet binary
*.feather binary
*.pkl binary
*.pickle binary
EOF
```

**Commit .gitattributes:**
```bash
git add .gitattributes
git commit -m "Add .gitattributes for cross-platform line ending compatibility"
```

---

### **Step 9: Create Annotated Tag for v5.0**

```bash
git tag -a v5.0 -m "Version 5.0: SHORT Expiries Strategy

Features:
- SHORT expiries (1-3 day options) to minimize theta decay
- Reality-adjusted backtesting with Black-Scholes modeling
- SPY-only recommendation (34.3% CAGR expected)
- IG.com (US 500) and IBKR (SPY) platform support
- Comprehensive paper trading framework with calibration
- Complete project reorganization
- Convenience wrapper scripts (trade.py, backtest.py, fetch.py)

Performance:
- Backtest CAGR (idealized): 64.8%
- Reality CAGR (SPY only): 34.3%
- Expected value per trade: +8.7%
- Win rate: 86.3%

Status: Ready for paper trading and reality calibration"
```

**Verify tag:**
```bash
git tag -l
git show v5.0
```

---

### **Step 10: Create Branches (Optional but Recommended)**

Create a development branch for testing:

```bash
# Create dev branch
git branch dev

# List branches
git branch -a

# Stay on master for now (production-ready code)
```

**Branch strategy:**
- `master`: Production-ready code (paper trading)
- `dev`: Development and testing
- `feature/*`: Specific features (if needed)

---

## 🌐 Optional: Push to Remote Repository (GitHub/GitLab)

### **Option A: GitHub**

**Step 1**: Create repository on GitHub
- Go to https://github.com/new
- Repository name: `OvernightFade`
- Description: "SHORT Expiries Options Trading Strategy"
- **Private** (recommended for trading systems)
- Do NOT initialize with README, .gitignore, or license
- Click "Create repository"

**Step 2**: Add remote and push
```bash
# Add remote
git remote add origin https://github.com/<your-username>/OvernightFade.git

# Rename branch to main (GitHub default)
git branch -M main

# Push to GitHub
git push -u origin main

# Push tags
git push --tags
```

### **Option B: GitLab**

```bash
git remote add origin https://gitlab.com/<your-username>/OvernightFade.git
git branch -M main
git push -u origin main
git push --tags
```

### **Option C: Keep Local Only**

If you want to keep the repository local only (recommended for private trading systems):

```bash
# No remote needed
# Just use git locally for version control
```

---

## 📊 Verify Everything

### **Check Repository Status**

```bash
# Should show "nothing to commit, working tree clean"
git status

# View commit history
git log --oneline

# View tags
git tag -l

# View branches
git branch -a

# Check what's NOT tracked (should be data/, logs/, results/)
git status --ignored

# Check repository size
du -sh .git
```

### **Test .gitignore**

```bash
# Create a test file in ignored directory
echo "test" > data/test.txt

# Check git status (should NOT show data/test.txt)
git status

# Clean up
rm data/test.txt
```

---

## 🔄 Daily Git Workflow (After Setup)

### **Making Changes**

```bash
# 1. Make changes to files
vim scripts/trading/auto_trade_ig.py

# 2. Check what changed
git status
git diff

# 3. Stage changes
git add scripts/trading/auto_trade_ig.py

# 4. Commit with descriptive message
git commit -m "Add Friday trade warning to auto-trader

- Display warning for Friday trades (lower win rate)
- Recommend skipping for live trading
- Keep for paper trading data collection"

# 5. Push to remote (if using)
git push
```

### **Viewing History**

```bash
# View commit log
git log --oneline --graph --all

# View recent changes
git log -p -2

# View changes by author
git log --author="Your Name"

# View changes to specific file
git log --follow scripts/trading/auto_trade_ig.py
```

### **Creating New Version Tags**

```bash
# When ready for new version
git tag -a v5.1 -m "Version 5.1: Paper trading calibration updates

- Updated reality_adjustments.json with real data
- Improved slippage calculations
- Added IBKR-specific order details"

# Push tag
git push --tags
```

---

## 🚨 Important Notes

### **What Should NEVER Be Committed**

- ❌ `data/` folder (too large, proprietary market data)
- ❌ `logs/` folder (contains personal trading activity)
- ❌ `results/` folder (generated files, can be reproduced)
- ❌ `.env` file (contains API keys)
- ❌ `config/ig_credentials.json` (contains sensitive credentials)
- ❌ `__pycache__/` folders (Python cache)
- ❌ Personal notes with sensitive information

### **What SHOULD Be Committed**

- ✅ Source code (`src/`, `scripts/`)
- ✅ Documentation (`docs/`, `README.md`)
- ✅ Configuration (`config/config.json`, `config/reality_adjustments.json`)
- ✅ Requirements (`requirements.txt`)
- ✅ Tests (`tests/`)
- ✅ Wrappers (`trade.py`, `backtest.py`, `fetch.py`)

### **If You Accidentally Commit Sensitive Data**

**Stop immediately and:**

```bash
# Remove file from git (keeps local copy)
git rm --cached config/ig_credentials.json

# Commit the removal
git commit -m "Remove sensitive credentials file"

# If already pushed to remote, you may need to:
# 1. Change the exposed credentials
# 2. Force push (dangerous, only if no collaborators)
git push --force
```

**Better**: Prevent this by maintaining a good `.gitignore`.

---

## ✅ Checklist

- [ ] Git installed and configured
- [ ] Repository initialized (`git init`)
- [ ] .gitignore in place and working
- [ ] Initial commit created
- [ ] .gitattributes added
- [ ] v5.0 tag created
- [ ] Remote repository added (optional)
- [ ] Pushed to remote (optional)
- [ ] Verified no sensitive data committed
- [ ] Verified data/, logs/, results/ are ignored

---

## 🎯 Next Steps

1. **Test the setup**: Make a small change and commit it
2. **Start paper trading**: Use the system daily
3. **Commit improvements**: As you calibrate and improve
4. **Create new versions**: Tag major milestones (v5.1, v5.2, etc.)
5. **Maintain documentation**: Keep docs updated with code changes

---

## 📚 Git Resources

- **Git Documentation**: https://git-scm.com/doc
- **GitHub Guides**: https://guides.github.com/
- **GitLab Docs**: https://docs.gitlab.com/
- **Atlassian Git Tutorial**: https://www.atlassian.com/git/tutorials

---

**Last Updated**: 2026-02-05
**Version**: 1.0
**Status**: Ready to use
