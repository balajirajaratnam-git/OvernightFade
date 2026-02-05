@echo off
echo ================================================================================
echo COMPLETING PHASE 1: MULTI-TICKER PIPELINE
echo ================================================================================
echo.
echo This will:
echo   1. Wait for QQQ to complete (currently fetching)
echo   2. Fetch IWM
echo   3. Fetch DIA
echo   4. Run Backtest on all 4 tickers
echo   5. Fetch VIX + sectors
echo.
echo Press Ctrl+C to cancel, or wait for automatic execution...
echo.
timeout /t 10

REM Wait for QQQ (check every 30 seconds)
:wait_qqq
python check_fetch_progress.py > temp_status.txt
findstr /C:"QQQ" temp_status.txt | findstr /C:"COMPLETE" > nul
if errorlevel 1 (
    echo [%time%] Waiting for QQQ to complete...
    timeout /t 30 /nobreak
    goto wait_qqq
)
echo [%time%] QQQ COMPLETE!
del temp_status.txt

REM Fetch IWM
echo.
echo ================================================================================
echo FETCHING IWM
echo ================================================================================
python fetch_one_ticker.py IWM
if errorlevel 1 echo ERROR fetching IWM

REM Fetch DIA
echo.
echo ================================================================================
echo FETCHING DIA
echo ================================================================================
python fetch_one_ticker.py DIA
if errorlevel 1 echo ERROR fetching DIA

REM Run Backtest #1
echo.
echo ================================================================================
echo BACKTEST #1: 4 MAIN TICKERS (SPY, QQQ, IWM, DIA)
echo ================================================================================
python src\backtester_multi_ticker.py
if errorlevel 1 echo ERROR in backtest

REM Fetch VIX
echo.
echo ================================================================================
echo FETCHING VIX (for Phase 2)
echo ================================================================================
python fetch_one_ticker.py VIX
if errorlevel 1 echo ERROR fetching VIX

REM Fetch sectors
echo.
echo ================================================================================
echo FETCHING SECTOR ETFs (for Phase 4)
echo ================================================================================
for %%S in (XLK XLF XLE XLV XLY XLU XLRE XLI XLB) do (
    echo.
    echo Fetching %%S...
    python fetch_one_ticker.py %%S
)

REM Run Backtest #2
echo.
echo ================================================================================
echo BACKTEST #2: COMPLETE (with VIX data available)
echo ================================================================================
python src\backtester_multi_ticker.py

echo.
echo ================================================================================
echo PIPELINE COMPLETE!
echo ================================================================================
echo.
echo Results saved in results/ folder
echo Next: Review results and move to Phase 2 (VIX Filter)
echo.
pause
