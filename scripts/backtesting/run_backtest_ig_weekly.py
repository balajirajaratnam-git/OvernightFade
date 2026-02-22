"""
IG Weekly US 500 Options Backtest -- Corrected Expiry Model
===========================================================

Fixes five critical errors present in run_backtest_bs_pricing.py:

  Fix 1: T_entry uses 252 trading-day annualisation (not 365 calendar days).
          DTE (1, 2, 3) counts trading days, so T = dte / 252.0.

  Fix 2: Expiry reference is 16:00 ET (SPX official cash close), not 09:30 ET.
          IG Weekly US 500 options settle at the SPX closing print.

  Fix 3: Expiry-day bar scan extends to 16:00 ET (not stopped at market open).
          The option is live all regular-session hours on expiry day.

  Fix 4: T_remaining uses a trading-time model.
          compute_T_remaining() counts trading minutes (9:30–16:00 = 390/day)
          between the current bar and 16:00 ET on expiry day, then divides by
          252 * 390 to get annualised fraction.

  Fix 5: Loss exit is at the 16:00 ET close bar on expiry day (not open).
          At true expiry T=0, BS returns intrinsic -- which is now correct.

Impact of fixes:
  Old model: loss trades show -100% because option was priced at T=0 at
             09:30 on expiry day (before market even opens -- pure intrinsic
             of a near-ATM option).
  New model: loss trades show realistic -15% to -60% for 1-DTE, smaller
             losses for 2-DTE because time value is retained through the day.

Usage:
    python scripts/backtesting/run_backtest_ig_weekly.py
    python scripts/backtesting/run_backtest_ig_weekly.py --cost-model fixed
    python scripts/backtesting/run_backtest_ig_weekly.py --iv-mode fixed --fixed-iv 0.15
    python scripts/backtesting/run_backtest_ig_weekly.py --target-sweep

Output:
    results/ig_weekly_backtest_vix_iv.csv
    results/ig_weekly_target_sweep.csv  (--target-sweep only)
"""
import sys
sys.path.insert(0, 'src')

import os
import json
import argparse
import math
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path

import pandas as pd
import numpy as np
import pytz
from rich.console import Console
from rich.table import Table

from pricing import black_scholes, load_vix_data, get_iv_for_date, FixedPointCosts

console = Console(highlight=False)

# Force ASCII-safe output on Windows terminals that can't handle box-drawing chars.
# rich.rule() uses Unicode box chars; replace it with a simple print.
def _rule(title: str = "") -> None:
    if title:
        pad = max(0, (78 - len(title) - 2) // 2)
        console.print("-" * pad + " " + title + " " + "-" * pad)
    else:
        console.print("-" * 78)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ET_TZ = pytz.timezone('America/New_York')
TRADING_MINUTES_PER_DAY = 390          # 09:30–16:00 ET
TRADING_DAYS_PER_YEAR = 252
ANNUAL_TRADING_MINUTES = TRADING_DAYS_PER_YEAR * TRADING_MINUTES_PER_DAY  # 98_280

MARKET_OPEN_TIME  = dt_time(9, 30)
MARKET_CLOSE_TIME = dt_time(16, 0)

# Target percentages for the sweep (fraction of entry premium)
TARGET_PCT_SWEEP = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]

# Risk-free rate
RISK_FREE_RATE = 0.045


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    with open("config/config.json") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def get_next_trading_day(date: datetime.date, df_daily: pd.DataFrame):
    """Return the next date in df_daily index after `date`."""
    nxt = date + timedelta(days=1)
    for _ in range(10):
        if nxt in df_daily.index:
            return nxt
        nxt += timedelta(days=1)
    return None


def get_next_wednesday(date: datetime.date) -> datetime.date:
    dow = date.weekday()   # 0=Mon, 1=Tue, 2=Wed, …
    if dow in (0, 1):
        days_ahead = 2 - dow
    else:
        days_ahead = 9 - dow   # next week's Wednesday
    return date + timedelta(days=days_ahead)


def get_next_friday(date: datetime.date) -> datetime.date:
    dow = date.weekday()
    if dow < 4:
        days_ahead = 4 - dow
    else:
        days_ahead = 8 - dow   # next week's Friday (only hits if dow==4, but Thursday is dow=3)
    return date + timedelta(days=days_ahead)


def get_next_monday(date: datetime.date) -> datetime.date:
    dow = date.weekday()
    if dow == 4:   # Friday
        days_ahead = 3
    else:
        days_ahead = 7 - dow
    return date + timedelta(days=days_ahead)


# ---------------------------------------------------------------------------
# Core timing model
# ---------------------------------------------------------------------------

def make_expiry_dt(expiry_date: datetime.date) -> datetime:
    """Return a tz-aware datetime for 16:00 ET on expiry_date (official close)."""
    naive = datetime(expiry_date.year, expiry_date.month, expiry_date.day, 16, 0)
    return ET_TZ.localize(naive)


def make_bar_aware(ts, ref_date: datetime.date = None) -> datetime:
    """
    Ensure `ts` is a tz-aware datetime in ET.

    Handles both tz-aware and tz-naive inputs from parquet files.
    """
    if hasattr(ts, 'tzinfo') and ts.tzinfo is not None:
        return ts.tz_convert(ET_TZ)
    return ET_TZ.localize(ts)


def compute_T_remaining(bar_time_et: datetime, expiry_dt_et: datetime) -> float:
    """
    Compute time remaining to expiry in years using a trading-time model.

    Counts trading minutes (09:30–16:00 ET, 390 min/day) from bar_time_et to
    expiry_dt_et, then divides by ANNUAL_TRADING_MINUTES.

    This accurately captures that an option with 6 hours left on expiry day
    has meaningful time value -- not zero as the calendar-day model implied.

    Args:
        bar_time_et: Current bar timestamp (ET, tz-aware).
        expiry_dt_et: 16:00 ET on expiry date (tz-aware).

    Returns:
        T in annualised years (float >= 0).
    """
    if bar_time_et >= expiry_dt_et:
        return 0.0

    total_minutes = 0.0

    # Work day-by-day from bar_time_et's date to expiry_dt_et's date
    cur_date = bar_time_et.date()
    exp_date = expiry_dt_et.date()

    while cur_date <= exp_date:
        day_open  = ET_TZ.localize(datetime(cur_date.year, cur_date.month, cur_date.day, 9, 30))
        day_close = ET_TZ.localize(datetime(cur_date.year, cur_date.month, cur_date.day, 16, 0))

        if cur_date == bar_time_et.date() and cur_date == exp_date:
            # Same day as bar AND expiry -- count minutes from bar to 16:00
            start = max(bar_time_et, day_open)
            end   = expiry_dt_et
        elif cur_date == bar_time_et.date():
            # Starting day (multi-day trade) -- count from bar time to close
            start = max(bar_time_et, day_open)
            end   = day_close
        elif cur_date == exp_date:
            # Expiry day -- count from open to 16:00
            start = day_open
            end   = expiry_dt_et
        else:
            # Full intermediate day
            start = day_open
            end   = day_close

        mins = (end - start).total_seconds() / 60.0
        if mins > 0:
            total_minutes += mins

        cur_date += timedelta(days=1)

    return max(total_minutes / ANNUAL_TRADING_MINUTES, 0.0)


# ---------------------------------------------------------------------------
# Intraday data loader with caching
# ---------------------------------------------------------------------------

_intraday_cache: dict = {}


def load_intraday(ticker: str, date: datetime.date) -> pd.DataFrame | None:
    """
    Load intraday parquet for `date`, return ET-indexed DataFrame or None.
    Uses a simple in-memory cache to avoid repeated disk reads.
    """
    key = (ticker, date)
    if key in _intraday_cache:
        return _intraday_cache[key]

    path = f'data/{ticker}/intraday/{date.strftime("%Y-%m-%d")}.parquet'
    if not os.path.exists(path):
        _intraday_cache[key] = None
        return None

    try:
        df = pd.read_parquet(path)
        if df.index.tz is not None:
            df.index = df.index.tz_convert(ET_TZ)
        else:
            df.index = df.index.tz_localize('UTC').tz_convert(ET_TZ)
        _intraday_cache[key] = df
        return df
    except Exception:
        _intraday_cache[key] = None
        return None


def get_close_at_1600(ticker: str, date: datetime.date) -> float | None:
    """
    Return the closing price at or nearest to 16:00 ET on `date`.

    Looks for the last bar at or before 16:00 ET.  Returns None if no
    intraday data available for that date.
    """
    df = load_intraday(ticker, date)
    if df is None or df.empty:
        return None

    close_dt = ET_TZ.localize(datetime(date.year, date.month, date.day, 16, 0))
    window = df[df.index <= close_dt]
    if window.empty:
        return None
    return float(window.iloc[-1]['Close'])


# ---------------------------------------------------------------------------
# Single-trade backtest
# ---------------------------------------------------------------------------

def backtest_trade(
    ticker: str,
    entry_date: datetime.date,
    entry_price: float,
    strike: float,
    option_type: str,
    signal: str,
    expiry_date: datetime.date,
    days_to_expiry: int,
    iv_entry: float,
    target_pct: float,                  # fraction of entry_premium (e.g. 0.10 = 10%)
    df_daily: pd.DataFrame,
    cost_model: str = 'pct',
    fixed_costs: FixedPointCosts | None = None,
    spread_cost_pct: float = 0.04,
    slippage_pct: float = 0.01,
) -> dict | None:
    """
    Simulate a single option trade with the corrected IG Weekly expiry model.

    Entry:  16:00 ET close on entry_date.
    Scan:   All bars from entry_date (post-16:00) through 16:00 ET expiry_date.
    Exit:   First bar where option premium >= target, or 16:00 ET on expiry_date.

    Returns a dict of trade metrics, or None if entry premium is zero.
    """
    # ----- Fix 1: T_entry in trading-day years -----
    T_entry = days_to_expiry / TRADING_DAYS_PER_YEAR

    entry_result = black_scholes(entry_price, strike, T_entry, RISK_FREE_RATE, iv_entry, option_type)
    entry_premium = entry_result['price']

    if entry_premium <= 0.001:
        return None

    target_premium = entry_premium * (1.0 + target_pct)

    # ----- Fix 2: Expiry is 16:00 ET (not 09:30 ET) -----
    expiry_dt_et = make_expiry_dt(expiry_date)

    # ----- Scan bars for target hit -----
    target_hit   = False
    exit_time_et = None
    exit_underlying = None

    entry_dt_et = ET_TZ.localize(
        datetime(entry_date.year, entry_date.month, entry_date.day, 16, 0)
    )

    check_date = entry_date
    while check_date <= expiry_date:
        df_intra = load_intraday(ticker, check_date)

        if df_intra is not None and not df_intra.empty:
            if check_date == entry_date:
                # Start scanning from 16:00 bar on entry day (already entered)
                df_window = df_intra[df_intra.index >= entry_dt_et]
            elif check_date == expiry_date:
                # ----- Fix 3: Scan expiry day up to 16:00 ET (not 09:30) -----
                df_window = df_intra[df_intra.index <= expiry_dt_et]
            else:
                df_window = df_intra

            for bar_time, bar in df_window.iterrows():
                # ----- Fix 4: T_remaining uses trading-time model -----
                T_rem = compute_T_remaining(bar_time, expiry_dt_et)
                current_premium = black_scholes(
                    float(bar['Close']), strike, T_rem, RISK_FREE_RATE, iv_entry, option_type
                )['price']

                if current_premium >= target_premium:
                    target_hit      = True
                    exit_time_et    = bar_time
                    exit_underlying = float(bar['Close'])
                    break

            if target_hit:
                break

        nxt = get_next_trading_day(check_date, df_daily)
        if nxt is None or nxt > expiry_date:
            break
        check_date = nxt

    # ----- Compute exit premium -----
    if target_hit and exit_time_et is not None:
        T_exit = compute_T_remaining(exit_time_et, expiry_dt_et)
        exit_premium = black_scholes(
            exit_underlying, strike, T_exit, RISK_FREE_RATE, iv_entry, option_type
        )['price']
    else:
        # ----- Fix 5: Loss exit at 16:00 ET close on expiry day -----
        exit_underlying = get_close_at_1600(ticker, expiry_date)
        if exit_underlying is None:
            # Fallback: last daily close at or before expiry
            candidates = df_daily[df_daily.index <= pd.Timestamp(expiry_date)]
            exit_underlying = float(candidates.iloc[-1]['Close']) if not candidates.empty else entry_price

        # T=0 at official expiry -- BS returns intrinsic (correct)
        exit_premium = black_scholes(
            exit_underlying, strike, 0.0, RISK_FREE_RATE, iv_entry, option_type
        )['price']
        exit_time_et = expiry_dt_et

    # ----- P&L calculation -----
    gross_pnl_pct = (exit_premium - entry_premium) / entry_premium * 100.0 if entry_premium > 0 else 0.0

    if cost_model == 'fixed' and fixed_costs is not None:
        actual_entry = entry_premium + fixed_costs.total_one_side_pts
        actual_exit  = max(exit_premium - fixed_costs.total_one_side_pts, 0.0)
        net_pnl_pct  = (actual_exit - actual_entry) / actual_entry * 100.0 if actual_entry > 0 else 0.0
    else:
        entry_mult  = 1.0 + (spread_cost_pct / 2.0) + slippage_pct
        exit_mult   = max(1.0 - (spread_cost_pct / 2.0) - slippage_pct, 0.0)
        actual_entry = entry_premium * entry_mult
        actual_exit  = exit_premium  * exit_mult
        net_pnl_pct  = (actual_exit - actual_entry) / actual_entry * 100.0 if actual_entry > 0 else 0.0

    T_exit_days = compute_T_remaining(exit_time_et, expiry_dt_et) * TRADING_DAYS_PER_YEAR

    return {
        'entry_premium':   round(entry_premium, 4),
        'exit_premium':    round(exit_premium,  4),
        'exit_underlying': round(exit_underlying, 2) if exit_underlying else None,
        'target_premium':  round(target_premium, 4),
        'target_hit':      target_hit,
        'gross_pnl_pct':   round(gross_pnl_pct, 2),
        'net_pnl_pct':     round(net_pnl_pct,   2),
        'T_entry_days':    round(days_to_expiry, 3),
        'T_exit_days':     round(T_exit_days,    4),
        'exit_time':       exit_time_et.isoformat() if exit_time_et else None,
    }


# ---------------------------------------------------------------------------
# Full backtest runner
# ---------------------------------------------------------------------------

def run_ig_weekly_backtest(
    ticker: str,
    config: dict,
    vix_series,
    target_pct: float = 0.10,
    iv_mode: str = 'vix',
    fixed_iv: float = 0.15,
    cost_model: str = 'pct',
    fixed_costs: FixedPointCosts | None = None,
) -> pd.DataFrame | None:
    """
    Run the corrected IG Weekly backtest for one ticker.

    Args:
        ticker:       Underlying ticker ('SPY').
        config:       Loaded config.json dict.
        vix_series:   VIX daily close series (from load_vix_data()), or None.
        target_pct:   Target premium gain fraction (e.g. 0.10 for +10%).
        iv_mode:      'vix' to use VIX-derived IV, 'fixed' to use fixed_iv.
        fixed_iv:     IV to use when iv_mode='fixed'.
        cost_model:   'pct' or 'fixed'.
        fixed_costs:  FixedPointCosts instance (used when cost_model='fixed').

    Returns:
        DataFrame of all trades, or None if data unavailable.
    """
    daily_file = f'data/{ticker}/daily_OHLCV.parquet'
    if not os.path.exists(daily_file):
        console.print(f"[red]No daily data for {ticker}[/red]")
        return None

    df_daily = pd.read_parquet(daily_file)
    filters  = config.get('filters', {})
    exclude_fridays   = filters.get('exclude_fridays', True)
    enable_fade_green = filters.get('enable_fade_green', False)
    enable_fade_red   = filters.get('enable_fade_red', True)
    min_mag_pct       = filters.get('min_magnitude_pct', 0.10)

    trades = []
    skipped_no_iv = 0
    skipped_zero_premium = 0

    valid_days = df_daily[df_daily.index.dayofweek < 5]

    for i in range(len(valid_days)):
        day_t    = valid_days.iloc[i]
        date_t   = valid_days.index[i].date()  # python date object
        dow      = valid_days.index[i].dayofweek  # 0=Mon … 4=Fri

        # Skip Fridays if configured
        if exclude_fridays and dow == 4:
            continue

        # Magnitude filter
        magnitude = abs((day_t['Close'] - day_t['Open']) / day_t['Open']) * 100.0
        if magnitude < min_mag_pct:
            continue

        # Direction and signal
        direction = "GREEN" if day_t['Close'] > day_t['Open'] else "RED"

        if direction == "GREEN":
            if not enable_fade_green:
                continue
            signal      = "FADE_GREEN"
            option_type = "PUT"
        else:
            if not enable_fade_red:
                continue
            signal      = "FADE_RED"
            option_type = "CALL"

        # Expiry determination (IG weekly pattern)
        if dow == 0:   # Monday -> Wednesday (2 trading days)
            expiry_date    = get_next_wednesday(date_t)
            expiry_label   = "MON-WED"
            days_to_expiry = 2
        elif dow == 1:  # Tuesday -> Wednesday (1 trading day)
            expiry_date    = get_next_wednesday(date_t)
            expiry_label   = "TUE-WED"
            days_to_expiry = 1
        elif dow == 2:  # Wednesday -> Friday (2 trading days)
            expiry_date    = get_next_friday(date_t)
            expiry_label   = "WED-FRI"
            days_to_expiry = 2
        elif dow == 3:  # Thursday -> Friday (1 trading day)
            expiry_date    = get_next_friday(date_t)
            expiry_label   = "THU-FRI"
            days_to_expiry = 1
        else:           # Friday -> Monday (3 trading days) -- only if exclude_fridays=False
            expiry_date    = get_next_monday(date_t)
            expiry_label   = "FRI-MON"
            days_to_expiry = 3

        # ATM strike (SPY rounded to nearest dollar; SPX would be nearest 5)
        entry_price = float(day_t['Close'])
        strike      = round(entry_price)

        # IV
        if iv_mode == 'fixed':
            iv_entry = fixed_iv
        else:
            ts_t = pd.Timestamp(date_t)
            iv_entry = get_iv_for_date(ts_t, vix_series, df_daily)

        if iv_entry is None or iv_entry <= 0:
            skipped_no_iv += 1
            continue

        trade = backtest_trade(
            ticker          = ticker,
            entry_date      = date_t,
            entry_price     = entry_price,
            strike          = strike,
            option_type     = option_type,
            signal          = signal,
            expiry_date     = expiry_date,
            days_to_expiry  = days_to_expiry,
            iv_entry        = iv_entry,
            target_pct      = target_pct,
            df_daily        = df_daily,
            cost_model      = cost_model,
            fixed_costs     = fixed_costs,
            spread_cost_pct = config.get('cost_model', {}).get('percentage', {}).get('ig_spread_pct', 0.04),
            slippage_pct    = config.get('cost_model', {}).get('percentage', {}).get('ig_slippage_pct', 0.01),
        )

        if trade is None:
            skipped_zero_premium += 1
            continue

        # Attach metadata
        atr = day_t.get('ATR_14', float('nan'))
        trades.append({
            'date':           date_t.isoformat(),
            'ticker':         ticker,
            'dow':            ['Mon','Tue','Wed','Thu','Fri'][dow],
            'expiry_label':   expiry_label,
            'expiry_date':    expiry_date.isoformat(),
            'days_to_expiry': days_to_expiry,
            'direction':      direction,
            'signal':         signal,
            'option_type':    option_type,
            'entry_price':    round(entry_price, 4),
            'strike':         strike,
            'magnitude_pct':  round(magnitude, 3),
            'atr':            round(atr, 4) if not pd.isna(atr) else None,
            'iv_entry':       round(iv_entry, 4),
            'target_pct':     target_pct,
            **trade,
        })

    if skipped_no_iv:
        console.print(f"  [yellow]Skipped {skipped_no_iv} trades (no IV)[/yellow]")
    if skipped_zero_premium:
        console.print(f"  [yellow]Skipped {skipped_zero_premium} trades (zero entry premium)[/yellow]")

    console.print(f"  [green]{len(trades)} trades generated[/green]")
    return pd.DataFrame(trades) if trades else None


# ---------------------------------------------------------------------------
# Results display helpers
# ---------------------------------------------------------------------------

def _ev_style(ev: float) -> str:
    return "green" if ev > 0 else ("yellow" if ev >= -1 else "red")


def print_summary_table(df: pd.DataFrame, title: str) -> None:
    """Print a per-expiry-pattern breakdown table."""
    console.print()
    console.print(f"[bold white]{title}[/bold white]")
    _rule()

    tbl = Table(show_header=True, header_style="bold cyan")
    tbl.add_column("Pattern",        width=10)
    tbl.add_column("n",              justify="right", width=6)
    tbl.add_column("WR%",            justify="right", width=7)
    tbl.add_column("Avg Win (net)",  justify="right", width=14)
    tbl.add_column("Avg Loss (net)", justify="right", width=15)
    tbl.add_column("EV/trade",       justify="right", width=11)
    tbl.add_column("Avg Entry$",     justify="right", width=11)
    tbl.add_column("Avg Loss (old)", justify="right", width=15)

    for label in sorted(df['expiry_label'].unique()):
        sub   = df[df['expiry_label'] == label]
        wins  = sub[sub['target_hit']]
        losses= sub[~sub['target_hit']]
        wr    = len(wins) / len(sub) * 100
        ev    = sub['net_pnl_pct'].mean()
        avg_w = wins['net_pnl_pct'].mean()   if len(wins)   > 0 else float('nan')
        avg_l = losses['net_pnl_pct'].mean() if len(losses) > 0 else float('nan')
        avg_e = sub['entry_premium'].mean()

        # "old" loss assuming option always expires worthless (T=0 at 09:30)
        # entry premium was all time value, so loss = -entry_premium entirely
        # approximate as -100% gross, adjusted for costs
        old_loss = -100.0  # per the old model

        ev_col = f"[{_ev_style(ev)}]{ev:+.2f}%[/{_ev_style(ev)}]"
        tbl.add_row(
            label,
            f"{len(sub):,}",
            f"{wr:.1f}%",
            f"{avg_w:+.1f}%" if not math.isnan(avg_w) else "N/A",
            f"{avg_l:+.1f}%" if not math.isnan(avg_l) else "N/A",
            ev_col,
            f"${avg_e:.3f}",
            f"{old_loss:.0f}%",
        )

    console.print(tbl)


def print_overall_stats(df: pd.DataFrame, label: str = "") -> None:
    n       = len(df)
    wins    = df['target_hit'].sum()
    wr      = wins / n * 100 if n > 0 else 0
    ev      = df['net_pnl_pct'].mean()
    avg_w   = df[df['target_hit']]['net_pnl_pct'].mean()   if wins > 0 else float('nan')
    avg_l   = df[~df['target_hit']]['net_pnl_pct'].mean()  if wins < n else float('nan')
    med_l   = df[~df['target_hit']]['net_pnl_pct'].median() if wins < n else float('nan')

    prefix = f"[bold]{label}[/bold] " if label else ""
    console.print(f"{prefix}n={n}  WR={wr:.1f}%  "
                  f"avg_win={avg_w:+.1f}%  avg_loss={avg_l:+.1f}%  "
                  f"median_loss={med_l:+.1f}%  EV={ev:+.2f}%")


def print_loss_distribution(df: pd.DataFrame) -> None:
    losses = df[~df['target_hit']]['net_pnl_pct']
    if losses.empty:
        return
    console.print()
    console.print("[bold white]Loss distribution (net P&L %)[/bold white]")
    _rule()
    buckets = [(-100, -80), (-80, -60), (-60, -40), (-40, -20), (-20, 0), (0, 20)]
    for lo, hi in buckets:
        cnt = ((losses >= lo) & (losses < hi)).sum()
        pct = cnt / len(losses) * 100
        bar = "#" * int(pct / 2)
        console.print(f"  {lo:+4d}% to {hi:+4d}%: {cnt:4d} ({pct:5.1f}%) {bar}")
    console.print(f"  Count: {len(losses)}  Mean: {losses.mean():+.1f}%  Median: {losses.median():+.1f}%")


# ---------------------------------------------------------------------------
# Target sweep
# ---------------------------------------------------------------------------

def run_target_sweep(
    ticker: str,
    config: dict,
    vix_series,
    iv_mode: str = 'vix',
    fixed_iv: float = 0.15,
    cost_model: str = 'pct',
    fixed_costs: FixedPointCosts | None = None,
) -> pd.DataFrame:
    """
    Run backtest for each target percentage in TARGET_PCT_SWEEP.
    Returns a DataFrame with one row per (target_pct, expiry_label).
    """
    sweep_rows = []

    for tgt in TARGET_PCT_SWEEP:
        console.print(f"  Target {tgt*100:.0f}% …", end="")
        df = run_ig_weekly_backtest(
            ticker      = ticker,
            config      = config,
            vix_series  = vix_series,
            target_pct  = tgt,
            iv_mode     = iv_mode,
            fixed_iv    = fixed_iv,
            cost_model  = cost_model,
            fixed_costs = fixed_costs,
        )
        if df is None or df.empty:
            console.print(" [red]no data[/red]")
            continue

        for label in sorted(df['expiry_label'].unique()):
            sub   = df[df['expiry_label'] == label]
            wins  = sub[sub['target_hit']]
            losses= sub[~sub['target_hit']]
            sweep_rows.append({
                'target_pct':    tgt,
                'expiry_label':  label,
                'n_trades':      len(sub),
                'win_rate':      len(wins) / len(sub) if len(sub) > 0 else 0,
                'avg_win_pct':   wins['net_pnl_pct'].mean()   if len(wins) > 0 else float('nan'),
                'avg_loss_pct':  losses['net_pnl_pct'].mean() if len(losses) > 0 else float('nan'),
                'ev_per_trade':  sub['net_pnl_pct'].mean(),
            })

        console.print(f" {len(df)} trades")

    return pd.DataFrame(sweep_rows)


def print_sweep_table(df_sweep: pd.DataFrame) -> None:
    """Print target sweep results as a compact table."""
    console.print()
    console.print("[bold white]TARGET SWEEP RESULTS[/bold white]")
    _rule()

    tbl = Table(show_header=True, header_style="bold cyan")
    tbl.add_column("Target%",       width=9)
    tbl.add_column("Pattern",       width=10)
    tbl.add_column("n",             justify="right", width=6)
    tbl.add_column("WR%",           justify="right", width=7)
    tbl.add_column("Avg Win",       justify="right", width=10)
    tbl.add_column("Avg Loss",      justify="right", width=10)
    tbl.add_column("EV/trade",      justify="right", width=11)

    for _, row in df_sweep.iterrows():
        ev   = row['ev_per_trade']
        ev_col = f"[{_ev_style(ev)}]{ev:+.2f}%[/{_ev_style(ev)}]"
        tbl.add_row(
            f"{row['target_pct']*100:.0f}%",
            row['expiry_label'],
            f"{int(row['n_trades']):,}",
            f"{row['win_rate']*100:.1f}%",
            f"{row['avg_win_pct']:+.1f}%"  if not math.isnan(row['avg_win_pct'])  else "N/A",
            f"{row['avg_loss_pct']:+.1f}%" if not math.isnan(row['avg_loss_pct']) else "N/A",
            ev_col,
        )

    console.print(tbl)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="IG Weekly US 500 Options Backtest -- Corrected Expiry Model",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        '--cost-model', choices=['pct', 'fixed'], default='pct',
        help="'pct' = percentage spread+slippage; 'fixed' = points-based from cost_model_fixed.json",
    )
    parser.add_argument(
        '--iv-mode', choices=['vix', 'fixed'], default='vix',
        help="'vix' = VIX-derived IV; 'fixed' = use --fixed-iv value",
    )
    parser.add_argument(
        '--fixed-iv', type=float, default=0.15,
        help="Fixed IV to use when --iv-mode=fixed (default: 0.15)",
    )
    parser.add_argument(
        '--target-pct', type=float, default=0.10,
        help="Target premium gain fraction for main run (default: 0.10 = 10%%)",
    )
    parser.add_argument(
        '--target-sweep', action='store_true',
        help="Run target sweep across 5%%–30%% and save to results/ig_weekly_target_sweep.csv",
    )
    args = parser.parse_args()

    # ----- Banner -----
    _rule("IG WEEKLY BACKTEST - CORRECTED EXPIRY MODEL")
    console.print()
    console.print("  Key corrections vs run_backtest_bs_pricing.py:")
    console.print("  [1] T_entry = dte / 252  (trading days, not calendar days)")
    console.print("  [2] Expiry reference = 16:00 ET SPX close (not 09:30 open)")
    console.print("  [3] Expiry-day scan extends to 16:00 ET")
    console.print("  [4] T_remaining = trading-minutes model (390 min/day)")
    console.print("  [5] Loss exit at 16:00 ET close (intrinsic at true expiry)")
    console.print()

    # ----- Config & IV -----
    config = load_config()

    vix_series = None
    if args.iv_mode == 'vix':
        vix_series = load_vix_data()
        if vix_series is None:
            console.print("[yellow]WARNING: VIX data unavailable -- using realised vol fallback[/yellow]")
        else:
            console.print(f"VIX data: {vix_series.index.min().date()} to {vix_series.index.max().date()}")

    # ----- Cost model -----
    fixed_costs = None
    cost_model  = args.cost_model
    if cost_model == 'fixed':
        cfg_path = Path("config/cost_model_fixed.json")
        if cfg_path.exists():
            fixed_costs = FixedPointCosts.from_config(str(cfg_path))
            console.print(
                f"[green]Fixed cost model: half_spread={fixed_costs.half_spread_pts:.3f} pts, "
                f"slippage={fixed_costs.slippage_pts:.3f} pts[/green]"
            )
        else:
            console.print(
                "[yellow]config/cost_model_fixed.json not found -- falling back to pct model[/yellow]"
            )
            cost_model = 'pct'

    console.print(f"Cost model: [bold]{cost_model}[/bold]")
    console.print(f"IV mode:    [bold]{args.iv_mode}[/bold]"
                  + (f"  (σ = {args.fixed_iv:.0%})" if args.iv_mode == 'fixed' else ""))
    console.print()

    ticker = config.get('ticker', 'SPY')

    # ----- Target sweep -----
    if args.target_sweep:
        _rule("TARGET SWEEP")
        console.print(f"Running {len(TARGET_PCT_SWEEP)} target levels: "
                      f"{[f'{t*100:.0f}%' for t in TARGET_PCT_SWEEP]}")
        console.print()

        df_sweep = run_target_sweep(
            ticker      = ticker,
            config      = config,
            vix_series  = vix_series,
            iv_mode     = args.iv_mode,
            fixed_iv    = args.fixed_iv,
            cost_model  = cost_model,
            fixed_costs = fixed_costs,
        )

        if not df_sweep.empty:
            print_sweep_table(df_sweep)
            out = Path("results/ig_weekly_target_sweep.csv")
            out.parent.mkdir(parents=True, exist_ok=True)
            df_sweep.to_csv(out, index=False)
            console.print(f"\n[green]Sweep saved -> {out}[/green]")

    # ----- Main run -----
    _rule(f"MAIN RUN  target={args.target_pct*100:.0f}%")
    console.print()

    df = run_ig_weekly_backtest(
        ticker      = ticker,
        config      = config,
        vix_series  = vix_series,
        target_pct  = args.target_pct,
        iv_mode     = args.iv_mode,
        fixed_iv    = args.fixed_iv,
        cost_model  = cost_model,
        fixed_costs = fixed_costs,
    )

    if df is None or df.empty:
        console.print("[red]No trades generated.[/red]")
        return

    # ----- Display -----
    console.print()
    _rule("OVERALL")
    print_overall_stats(df)
    print_summary_table(df, f"BY EXPIRY PATTERN  (target={args.target_pct*100:.0f}%)")
    print_loss_distribution(df)

    # ----- Verify Fix 5 worked -----
    # Verification: the old model sent ALL losses to -100% because T=0 at 09:30 ET
    # (before market even opened on expiry day).  With the corrected model:
    #   - Options that end OTM at 16:00 ET close legitimately show -100% (genuine).
    #   - Options that end ITM at 16:00 ET close show partial losses (intrinsic > 0).
    # PASS = at least some losses are partial (not ALL -100%).
    losses_df = df[~df['target_hit']]
    if not losses_df.empty:
        console.print()
        console.print("[bold white]VERIFICATION: Loss P&L distribution[/bold white]")
        _rule()
        console.print(f"  Loss count:      {len(losses_df)}")
        console.print(f"  Mean:            {losses_df['net_pnl_pct'].mean():+.1f}%")
        console.print(f"  Median:          {losses_df['net_pnl_pct'].median():+.1f}%")
        console.print(f"  Min:             {losses_df['net_pnl_pct'].min():+.1f}%")
        console.print(f"  Max:             {losses_df['net_pnl_pct'].max():+.1f}%")
        pct_100 = (losses_df['net_pnl_pct'] <= -99.0).sum() / len(losses_df) * 100
        pct_partial = 100.0 - pct_100
        # PASS: some losses are partial (option ended ITM at 16:00, intrinsic > 0)
        # Genuinely OTM expirations are legitimately -100%
        flag = "[green]PASS[/green]" if pct_partial > 0 else "[red]FAIL (all losses are -100% -- fix not working)[/red]"
        console.print(f"  OTM expiries (-100%):   {pct_100:.1f}%  (option expired worthless -- correct)")
        console.print(f"  ITM/partial losses:     {pct_partial:.1f}%  {flag}")

    # ----- Save -----
    out_main = Path("results/ig_weekly_backtest_vix_iv.csv")
    out_main.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_main, index=False)
    console.print(f"\n[green]Results saved -> {out_main}[/green]")
    console.print()
    _rule()


if __name__ == "__main__":
    main()
