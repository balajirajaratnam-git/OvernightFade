"""
Backtest: OVERNIGHT FADE — close-to-open option strategy on IG Weekly US 500

CANONICAL RUNNER — single entrypoint for all validated backtests.
Use CLI flags to control IV mode, direction filter, and output paths.

STRATEGY:
  Entry : 16:00 ET (US cash close) — buy ATM option
  Exit  : 09:30 ET next trading day (US cash open) — sell option at market
  Skip  : Friday entries (no weekend risk)

  This is a pure overnight trade. NOT a multi-day directional bet.

PRICING (separate from strategy timing):
  exit_dt   = next 09:30 ET  (strategy exit — when you close the trade)
  expiry_dt = contract settlement on expiry date at 16:00 ET

  At exit, the option premium is:
    BS(underlying_at_0930, strike, T_remaining_to_expiry, r, IV)
  where T_remaining = trading time from 09:30 exit to contract's 16:00 settlement.

  The option retains significant time value at 09:30 because settlement
  is hours or days away, depending on the contract.

IG WEEKLY CONTRACTS USED:
  MON entry -> exit TUE 09:30 -> contract expires WED 16:00 ET (T_rem ~ 1.5 td)
  TUE entry -> exit WED 09:30 -> contract expires WED 16:00 ET (T_rem ~ 0.5 td)
  WED entry -> exit THU 09:30 -> contract expires FRI 16:00 ET (T_rem ~ 1.5 td)
  THU entry -> exit FRI 09:30 -> contract expires FRI 16:00 ET (T_rem ~ 0.5 td)

TIMEZONE HANDLING:
  - All computation in America/New_York
  - Convert to UTC for slicing minute bars
  - UK time only for display

USAGE:
  python scripts/backtesting/run_backtest_overnight_fade.py \\
      --iv-mode vix --direction all \\
      --output results/overnight_fade_canonical_vix.csv \\
      --summary results/overnight_fade_canonical_vix_summary.json \\
      --overwrite
"""
import sys
sys.path.insert(0, 'src')

import argparse
import pandas as pd
import numpy as np
import json
import os
from pathlib import Path
import math
from datetime import datetime, timedelta, date as date_type
from rich.console import Console
from rich.table import Table
import pytz

from pricing import black_scholes, year_fraction  # canonical BS + time basis
from cost_models import (
    PercentPremiumCostModel, FixedPointCostModel, CalibratedFixedPointCostModel,
)
from trading_calendar import (
    get_nyse_schedule, build_trading_dates_set,
    is_trading_day, next_trading_day as cal_next_trading_day,
    weekly_expiry_date,
    make_entry_dt, make_exit_dt, make_exit_dt_at, make_expiry_dt,
    uk_time_to_et, TZ_UK as CAL_TZ_UK,
)

console = Console()

# Timezones
TZ_ET = pytz.timezone('America/New_York')
TZ_UTC = pytz.utc
TZ_UK = pytz.timezone('Europe/London')


# ---------------------------------------------------------------------------
# Black-Scholes — thin wrapper keeps call sites unchanged
# ---------------------------------------------------------------------------

def bs_price(S, K, T, r, sigma, option_type):
    """Delegates to src/pricing.black_scholes(). option_type: 'CALL' or 'PUT'."""
    return black_scholes(S, K, T, r, sigma, option_type)['price']


# ---------------------------------------------------------------------------
# VIX
# ---------------------------------------------------------------------------

def load_vix_data():
    cache_file = Path("data/vix_daily_cache.parquet")
    if not cache_file.exists():
        raise FileNotFoundError("data/vix_daily_cache.parquet not found.")
    df = pd.read_parquet(cache_file)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    # Ensure sorted ascending — required for .loc[:date] prior-only slicing
    series = df['Close'].sort_index()
    return series

def get_vix_iv_prior_only(vix_series, lookup_date):
    """
    Strictly causal VIX IV lookup — never uses a future VIX value.

    Rules:
      1. Normalize lookup_date to a date (no time component).
      2. If VIX has a value for that exact date, return it.
      3. Otherwise return the latest VIX value strictly before that date.
      4. If no prior date exists, return (None, None).

    Args:
        vix_series: pd.Series indexed by tz-naive dates, values = VIX close.
        lookup_date: date, Timestamp, or string for the entry date.

    Returns:
        (iv_value: float or None, iv_date_used: str or None)
        iv_value is annualised IV as a fraction (VIX / 100).
    """
    if vix_series is None or vix_series.empty:
        return None, None

    lookup = pd.Timestamp(lookup_date).normalize()

    # Slice: all VIX dates on or before the lookup date
    prior = vix_series.loc[:lookup]
    if prior.empty:
        return None, None

    iv_date = prior.index[-1]
    iv_value = float(prior.iloc[-1]) / 100.0
    return iv_value, iv_date.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RTH_MINUTES_PER_DAY = 390  # 09:30 - 16:00
TRADING_MINUTES_PER_YEAR = 252 * RTH_MINUTES_PER_DAY  # 98,280


def get_next_trading_day(date, df_daily):
    """Get the next trading day after `date`."""
    nd = date + timedelta(days=1)
    for _ in range(10):
        if nd in df_daily.index:
            return nd
        nd += timedelta(days=1)
    return None


def get_expiry_date(entry_dow):
    """
    Given entry day-of-week (0=Mon..4=Fri), return:
      - expiry weekday offset (days from entry to expiry)
      - expiry_label string

    IG weekly contracts:
      MON entry -> WED expiry (2 calendar days)
      TUE entry -> WED expiry (1 calendar day)
      WED entry -> FRI expiry (2 calendar days)
      THU entry -> FRI expiry (1 calendar day)
      FRI       -> SKIP (no trade)
    """
    if entry_dow == 0:  # Monday
        return 2, "MON-WED"
    elif entry_dow == 1:  # Tuesday
        return 1, "TUE-WED"
    elif entry_dow == 2:  # Wednesday
        return 2, "WED-FRI"
    elif entry_dow == 3:  # Thursday
        return 1, "THU-FRI"
    else:
        return None, None  # Friday — skip


def compute_T_remaining(from_dt_et, expiry_1600_et, trading_dates_set):
    """
    Compute trading-time remaining from `from_dt_et` to `expiry_1600_et`.

    Uses trading-minutes model:
      - 1 trading day = 390 RTH minutes (09:30-16:00 ET)
      - 1 year = 252 * 390 = 98,280 trading minutes

    Args:
        from_dt_et: datetime in America/New_York (e.g., 09:30 exit time)
        expiry_1600_et: contract settlement datetime (16:00 ET on expiry day)
        trading_dates_set: set of pd.Timestamp trading dates

    Returns:
        float: T in years (trading-time basis)
    """
    from_date = from_dt_et.date()
    expiry_date = expiry_1600_et.date()

    from_hour, from_min = from_dt_et.hour, from_dt_et.minute

    # Minutes remaining in from_date's RTH
    rth_open_total = 9 * 60 + 30   # 570
    rth_close_total = 16 * 60       # 960
    from_total = from_hour * 60 + from_min

    if from_total < rth_open_total:
        today_minutes = RTH_MINUTES_PER_DAY  # full day ahead
    elif from_total >= rth_close_total:
        today_minutes = 0  # RTH over for today
    else:
        today_minutes = rth_close_total - from_total

    # Full intermediate trading days between from_date and expiry_date
    full_days = 0
    check = from_date + timedelta(days=1)
    while check < expiry_date:
        if pd.Timestamp(check) in trading_dates_set:
            full_days += 1
        check += timedelta(days=1)

    # Expiry day
    if from_date == expiry_date:
        total_minutes = today_minutes
    elif from_date < expiry_date:
        # today_minutes + intermediate + full expiry day (09:30-16:00 = 390)
        total_minutes = today_minutes + full_days * RTH_MINUTES_PER_DAY + RTH_MINUTES_PER_DAY
    else:
        total_minutes = 0

    return max(total_minutes / TRADING_MINUTES_PER_YEAR, 0.0)


def normalize_intraday(df_intra):
    """Convert intraday DataFrame index to ET, return as-is."""
    if df_intra.index.tz is not None:
        df_intra.index = df_intra.index.tz_convert(TZ_ET)
    else:
        df_intra.index = df_intra.index.tz_localize(TZ_UTC).tz_convert(TZ_ET)
    return df_intra


def get_bar_price_at(ticker, target_dt_et, df_daily):
    """
    Get the underlying price at a specific ET datetime from intraday data.

    Deterministic selection:
      1. Exact bar at target_dt_et
      2. Most recent prior bar within 5 minutes before target_dt_et
      3. None (no usable bar)

    Returns:
        float price or None
    """
    target_date = target_dt_et.date()
    intraday_file = f'data/{ticker}/intraday/{target_date.strftime("%Y-%m-%d")}.parquet'

    if not os.path.exists(intraday_file):
        return None

    try:
        df_intra = pd.read_parquet(intraday_file)
        df_intra = normalize_intraday(df_intra)

        # Bars within 5-min window at or before target time
        window_start = target_dt_et - timedelta(minutes=5)
        bars = df_intra[(df_intra.index >= window_start) & (df_intra.index <= target_dt_et)]
        if not bars.empty:
            return float(bars.iloc[-1]['Close'])

    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------

def compute_summary_metrics(df, run_params):
    """
    Compute summary metrics dict for JSON output.

    Args:
        df: DataFrame of trades (post direction-filter)
        run_params: dict of CLI parameters used for this run

    Returns:
        dict with all required summary fields
    """
    n = len(df)
    if n == 0:
        return {
            "run_parameters": run_params,
            "trade_counts": {"total": 0, "wins": 0, "losses": 0},
            "metrics": {"win_rate": 0.0, "ev": 0.0, "avg_win": 0.0, "avg_loss": 0.0},
            "percentiles": {},
            "date_range": {"start": None, "end": None},
            "files_written": {},
        }

    wins = df[df['Result'] == 'WIN']
    losses = df[df['Result'] == 'LOSS']
    n_wins = len(wins)
    n_losses = len(losses)

    win_rate = round(n_wins / n * 100, 2)
    ev = round(df['Net_PnL_Pct'].mean(), 4)
    avg_win = round(wins['Net_PnL_Pct'].mean(), 4) if n_wins > 0 else 0.0
    avg_loss = round(losses['Net_PnL_Pct'].mean(), 4) if n_losses > 0 else 0.0

    pnl = df['Net_PnL_Pct']
    percentiles = {}
    for p in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
        percentiles[f"P{p:02d}"] = round(float(np.percentile(pnl, p)), 4)

    date_start = df['Date'].iloc[0]
    date_end = df['Date'].iloc[-1]

    # Cost audit metrics
    avg_entry_cost_pct = round(float(df['Entry_Cost_Pct'].mean()), 4) if 'Entry_Cost_Pct' in df.columns else 0.0
    avg_exit_cost_pct = round(float(df['Exit_Cost_Pct'].mean()), 4) if 'Exit_Cost_Pct' in df.columns else 0.0
    avg_roundtrip_cost_pct = round(avg_entry_cost_pct + avg_exit_cost_pct, 4)
    avg_entry_cost_pts = round(float(df['Entry_Cost_Pts'].mean()), 6) if 'Entry_Cost_Pts' in df.columns else 0.0
    avg_exit_cost_pts = round(float(df['Exit_Cost_Pts'].mean()), 6) if 'Exit_Cost_Pts' in df.columns else 0.0
    avg_roundtrip_cost_pts = round(avg_entry_cost_pts + avg_exit_cost_pts, 6)

    return {
        "run_parameters": run_params,
        "trade_counts": {
            "total": n,
            "wins": n_wins,
            "losses": n_losses,
        },
        "metrics": {
            "win_rate": win_rate,
            "ev": ev,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
        },
        "cost_audit": {
            "avg_entry_cost_pct": avg_entry_cost_pct,
            "avg_exit_cost_pct": avg_exit_cost_pct,
            "avg_roundtrip_cost_pct": avg_roundtrip_cost_pct,
            "avg_entry_cost_pts": avg_entry_cost_pts,
            "avg_exit_cost_pts": avg_exit_cost_pts,
            "avg_roundtrip_cost_pts": avg_roundtrip_cost_pts,
        },
        "percentiles": percentiles,
        "date_range": {
            "start": date_start,
            "end": date_end,
        },
        "files_written": {},  # filled by caller
    }


# ---------------------------------------------------------------------------
# Exit-variant helpers (Step 11)
# ---------------------------------------------------------------------------

def _compute_iv_exit(iv_exit_mode, iv_crush_k, sigma, option_type,
                     exit_underlying, entry_price):
    """
    Compute exit IV based on mode.

    'same'  -> iv_exit = sigma (unchanged)
    'crush' -> direction-aware crush/expand:
               - CALL winner (exit > entry) => crush
               - PUT  winner (exit < entry) => crush
               - Otherwise => expand

    Returns:
        float: iv_exit clamped to [0.01, 1.50]
    """
    if iv_exit_mode == 'crush':
        if option_type == 'CALL':
            is_winner_direction = exit_underlying > entry_price
        else:
            is_winner_direction = exit_underlying < entry_price

        if is_winner_direction:
            iv_exit = sigma * (1.0 - iv_crush_k)
        else:
            iv_exit = sigma * (1.0 + iv_crush_k)

        iv_exit = max(iv_exit, 0.01)
        iv_exit = min(iv_exit, 1.50)
    else:
        iv_exit = sigma

    return iv_exit


def load_overnight_bars(ticker, entry_dt_et, max_exit_dt_et):
    """
    Load minute bars covering the overnight window [entry_dt_et, max_exit_dt_et].

    Loads:
      - Entry-date parquet: after-hours bars >= entry_dt_et
      - Exit-date parquet: pre-market bars <= max_exit_dt_et
    Concatenates, deduplicates, sorts, and slices to the window.

    Returns:
        pd.DataFrame with ET index or None if no bars available.
    """
    entry_date_str = entry_dt_et.date().strftime("%Y-%m-%d")
    exit_date_str = max_exit_dt_et.date().strftime("%Y-%m-%d")

    frames = []

    # Entry-date after-hours bars
    entry_file = f'data/{ticker}/intraday/{entry_date_str}.parquet'
    if os.path.exists(entry_file):
        try:
            df_e = pd.read_parquet(entry_file)
            df_e = normalize_intraday(df_e)
            ah_bars = df_e[df_e.index >= entry_dt_et]
            if not ah_bars.empty:
                frames.append(ah_bars)
        except Exception:
            pass

    # Exit-date pre-market bars (only if different date)
    if exit_date_str != entry_date_str:
        exit_file = f'data/{ticker}/intraday/{exit_date_str}.parquet'
        if os.path.exists(exit_file):
            try:
                df_x = pd.read_parquet(exit_file)
                df_x = normalize_intraday(df_x)
                pm_bars = df_x[df_x.index <= max_exit_dt_et]
                if not pm_bars.empty:
                    frames.append(pm_bars)
            except Exception:
                pass

    if not frames:
        return None

    combined = pd.concat(frames)
    combined = combined[~combined.index.duplicated(keep='first')]
    combined = combined.sort_index()
    combined = combined[(combined.index >= entry_dt_et) & (combined.index <= max_exit_dt_et)]

    return combined if not combined.empty else None


def _map_exit_time_bucket(dt_et, default_bucket):
    """
    Map an ET exit datetime to the nearest UK time bucket for cost resolution.

    Converts to UK time, then checks proximity (+-15 min) to known UK buckets:
      02:00 UK -> TIME_0200
      06:00 UK -> TIME_0600
      08:30 UK -> TIME_0830

    If no match, returns default_bucket.

    Args:
        dt_et: timezone-aware datetime in America/New_York
        default_bucket: fallback bucket name (e.g. 'EXIT')

    Returns:
        str: time bucket name
    """
    dt_uk = dt_et.astimezone(TZ_UK)
    uk_minutes = dt_uk.hour * 60 + dt_uk.minute

    known_buckets = [
        (2 * 60,       "TIME_0200"),   # 02:00 UK
        (6 * 60,       "TIME_0600"),   # 06:00 UK
        (8 * 60 + 30,  "TIME_0830"),   # 08:30 UK
    ]

    for target_min, bucket_name in known_buckets:
        if abs(uk_minutes - target_min) <= 15:
            return bucket_name

    return default_bucket


def _scan_tp_exit(bars, strike, option_type, sigma, iv_exit_mode, iv_crush_k,
                  entry_price, entry_fill, expiry_dt_et, time_basis,
                  trading_dates_ts, r, tp_threshold_pct, tp_check_freq_min,
                  cost_model, default_exit_bucket, baseline_exit_dt):
    """
    Scan overnight bars for a take-profit exit.

    Iterates bars at tp_check_freq_min intervals, computing BS premium and
    applying dynamic exit costs. If net P&L >= tp_threshold_pct, exits early.
    Otherwise falls back to the last bar at or before baseline_exit_dt.

    Args:
        bars: DataFrame of overnight minute bars (ET index, must have 'Close')
        strike: option strike price
        option_type: 'CALL' or 'PUT'
        sigma: entry IV (annualized)
        iv_exit_mode: 'same' or 'crush'
        iv_crush_k: crush/expand factor
        entry_price: underlying price at entry
        entry_fill: option fill price at entry (after costs)
        expiry_dt_et: contract expiry datetime (16:00 ET)
        time_basis: 'rth' or 'calendar'
        trading_dates_ts: set of pd.Timestamp trading dates
        r: risk-free rate
        tp_threshold_pct: take-profit threshold in percent (e.g. 30.0)
        tp_check_freq_min: check frequency in minutes (e.g. 1)
        cost_model: CalibratedFixedPointCostModel instance
        default_exit_bucket: default cost bucket for exit (e.g. 'EXIT')
        baseline_exit_dt: latest acceptable exit datetime (fallback)

    Returns:
        dict with keys: tp_hit, exit_dt_et, exit_underlying, exit_mid, exit_fill,
             exit_cost_bd, iv_exit, T_at_exit, peak_unrealized_pct, bars_scanned,
             exit_time_bucket_requested, exit_time_bucket_resolved,
             exit_time_bucket_fallback_used
        or None if no usable bars at all.
    """
    if bars is None or bars.empty:
        return None

    # Subsample at the requested frequency
    if tp_check_freq_min > 1:
        bars = bars.iloc[::tp_check_freq_min]

    peak_unrealized_pct = -999.0
    bars_scanned = 0
    last_valid = None  # last bar at or before baseline_exit_dt

    for bar_dt, bar_row in bars.iterrows():
        bars_scanned += 1
        bar_underlying = float(bar_row['Close'])

        # Time remaining from this bar to expiry
        T_at_bar = year_fraction(bar_dt, expiry_dt_et, time_basis,
                                 trading_dates_set=trading_dates_ts)

        # IV at this bar
        iv_at_bar = _compute_iv_exit(iv_exit_mode, iv_crush_k, sigma,
                                     option_type, bar_underlying, entry_price)

        # BS premium at this bar
        bar_mid = bs_price(bar_underlying, strike, T_at_bar, r,
                           iv_at_bar, option_type)

        # Dynamic exit cost resolution
        time_bucket = _map_exit_time_bucket(bar_dt, default_exit_bucket)
        if hasattr(cost_model, 'apply_exit_dynamic'):
            bar_fill, bar_cost_bd = cost_model.apply_exit_dynamic(bar_mid, time_bucket)
        else:
            bar_fill, bar_cost_bd = cost_model.apply_exit(bar_mid)

        # Net P&L
        net_pct = ((bar_fill - entry_fill) / entry_fill * 100
                   if entry_fill > 0 else 0.0)
        net_pct = max(net_pct, -100.0)

        # Track peak
        if net_pct > peak_unrealized_pct:
            peak_unrealized_pct = net_pct

        # Record as potential fallback if at or before baseline
        if bar_dt <= baseline_exit_dt:
            last_valid = {
                'tp_hit': False,
                'exit_dt_et': bar_dt,
                'exit_underlying': bar_underlying,
                'exit_mid': bar_mid,
                'exit_fill': bar_fill,
                'exit_cost_bd': bar_cost_bd,
                'iv_exit': iv_at_bar,
                'T_at_exit': T_at_bar,
                'peak_unrealized_pct': round(peak_unrealized_pct, 4),
                'bars_scanned': bars_scanned,
                'exit_time_bucket_requested': time_bucket,
                'exit_time_bucket_resolved': bar_cost_bd.get('source', ''),
                'exit_time_bucket_fallback_used': bar_cost_bd.get('fallback_used', False),
            }

        # Check TP threshold
        if net_pct >= tp_threshold_pct:
            return {
                'tp_hit': True,
                'exit_dt_et': bar_dt,
                'exit_underlying': bar_underlying,
                'exit_mid': bar_mid,
                'exit_fill': bar_fill,
                'exit_cost_bd': bar_cost_bd,
                'iv_exit': iv_at_bar,
                'T_at_exit': T_at_bar,
                'peak_unrealized_pct': round(peak_unrealized_pct, 4),
                'bars_scanned': bars_scanned,
                'exit_time_bucket_requested': time_bucket,
                'exit_time_bucket_resolved': bar_cost_bd.get('source', ''),
                'exit_time_bucket_fallback_used': bar_cost_bd.get('fallback_used', False),
            }

    # TP not hit — return last valid bar (at or before baseline)
    if last_valid is not None:
        last_valid['peak_unrealized_pct'] = round(peak_unrealized_pct, 4)
        last_valid['bars_scanned'] = bars_scanned
        return last_valid

    return None


# ---------------------------------------------------------------------------
# Main backtest
# ---------------------------------------------------------------------------

def run_overnight_fade(ticker, vix_series, use_vix_iv=True, fixed_iv=0.15,
                       time_basis='rth', cost_model=None,
                       iv_exit_mode='same', iv_crush_k=0.10,
                       exit_mode='fixed', exit_fixed_et='09:30',
                       tp_threshold_pct=30.0, tp_check_frequency_min=1,
                       tp_max_exit_et='09:30', exit_uk_time=None,
                       exit_time_bucket_exit='EXIT',
                       overnight_source='spy_only', ig_gap_dir=None,
                       ig_gap_resample='ffill'):
    """
    Backtest: Overnight fade, close-to-open.

    For each valid NYSE trading day (Mon-Thu):
      1. ENTRY at 16:00 ET: buy ATM option, priced via BS
         - T_entry = year_fraction(entry_dt, expiry_dt, basis)
      2. EXIT depends on exit_mode:
         - 'fixed': exit at exit_fixed_et next trading day (default 09:30)
         - 'tp_anytime': scan overnight bars for TP, fallback to tp_max_exit_et
         - 'uk_time': exit at specific UK time on next trading day
         Exit premium = BS(underlying, strike, T_remaining, r, iv_exit)
      3. P&L = (exit_fill - entry_fill) / entry_fill with costs via cost_model

    Calendar: Uses NYSE schedule from pandas_market_calendars for holiday-aware
    trading day lookups, expiry mapping, and DST-safe timestamp construction.

    Args:
        time_basis: 'rth' or 'calendar' — passed to year_fraction()
        cost_model: PercentPremiumCostModel or FixedPointCostModel instance.
                    If None, defaults to PercentPremiumCostModel(0.02, 0.005).
        iv_exit_mode: 'same' (iv_exit = iv_entry) or 'crush' (direction-aware)
        iv_crush_k: float, IV crush/expand factor (only used if mode='crush')
        exit_mode: 'fixed', 'tp_anytime', or 'uk_time'
        exit_fixed_et: HH:MM string for fixed exit time in ET
        tp_threshold_pct: take-profit threshold in percent
        tp_check_frequency_min: bar check interval in minutes for TP scan
        tp_max_exit_et: HH:MM string for latest TP fallback exit time in ET
        exit_uk_time: HH:MM string for UK exit time (required for uk_time mode)
        exit_time_bucket_exit: cost bucket for exit (e.g. 'EXIT')

    Returns:
        (DataFrame of trades, audit_counters dict)
    """
    daily_file = f'data/{ticker}/daily_OHLCV.parquet'
    if not os.path.exists(daily_file):
        console.print(f"[red]Missing {daily_file}[/red]")
        return None, {
            "skipped_trades_missing_iv": 0, "forward_iv_count": 0,
            "skipped_non_trading_days": 0, "skipped_missing_intraday_file": 0,
            "skipped_missing_required_bars": 0, "expiry_holiday_rolls": 0,
        }

    df_daily = pd.read_parquet(daily_file)

    # Date-keyed daily open lookup (handles non-midnight index timestamps)
    # df_daily.index may have non-00:00 times (e.g. 05:00 from UTC midnight),
    # so pd.Timestamp(date) won't match. Normalize once here.
    _daily_open = pd.Series(
        df_daily['Open'].values,
        index=df_daily.index.normalize(),
    )

    # --- NYSE trading calendar (authoritative) ---
    data_start = df_daily.index.min().date()
    data_end = df_daily.index.max().date()
    nyse_schedule = get_nyse_schedule(data_start, data_end)
    nyse_trading_dates = build_trading_dates_set(nyse_schedule)

    # Also build pd.Timestamp set for year_fraction() compatibility
    trading_dates_ts = set(pd.Timestamp(d) for d in nyse_trading_dates)

    # Expiry labels by entry day-of-week
    _EXPIRY_LABELS = {0: "MON-WED", 1: "TUE-WED", 2: "WED-FRI", 3: "THU-FRI"}

    r = 0.05

    # Cost model — all cost logic goes through this, nothing else
    if cost_model is None:
        cost_model = PercentPremiumCostModel(half_spread_pct=0.02, slippage_pct=0.005)

    # Audit counters
    skipped_missing_iv = 0
    forward_iv_count = 0
    skipped_non_trading_days = 0
    skipped_missing_intraday_file = 0
    skipped_missing_required_bars = 0
    expiry_holiday_rolls = 0

    # Step 11 audit counters
    tp_hit_count = 0
    tp_fallback_count = 0
    tp_no_data_count = 0
    uk_time_exit_count = 0
    missing_exit_bar_skips = 0
    exit_bucket_fallback_count = 0
    entry_bucket_fallback_count = 0

    # Overnight data source counters
    trades_using_ig_gap_at_exit = 0
    ig_gap_days_missing_count = 0
    ig_gap_scan_bars_total = 0  # total IG bars used during TP scans

    # Load IG gap module if needed
    _ig_gap_available = False
    if overnight_source == 'ig_gap_5m':
        try:
            from overnight_data import (
                get_ig_gap_price_at, stitch_overnight_bars, load_ig_gap_5m_for_uk_date,
                compute_ig_spy_scale_factor,
            )
            _ig_gap_dir = Path(ig_gap_dir) if ig_gap_dir else Path("data/IG_US500_spy_gap_5m")
            _ig_gap_available = _ig_gap_dir.exists() and any(_ig_gap_dir.glob("*.parquet"))
            if not _ig_gap_available:
                console.print(f"[yellow]Warning: ig_gap_5m requested but {_ig_gap_dir} is empty or missing[/yellow]")
        except ImportError as e:
            console.print(f"[red]Failed to import overnight_data: {e}[/red]")

    # Parse exit time strings into (hour, minute) tuples
    exit_fixed_h, exit_fixed_m = [int(x) for x in exit_fixed_et.split(':')]
    tp_max_h, tp_max_m = [int(x) for x in tp_max_exit_et.split(':')]
    if exit_uk_time is not None:
        uk_exit_h, uk_exit_m = [int(x) for x in exit_uk_time.split(':')]
    else:
        uk_exit_h, uk_exit_m = None, None

    trades = []

    # Iterate over data days that are also NYSE trading days
    for i in range(len(df_daily)):
        date_t = df_daily.index[i]
        trade_date = date_t.date()

        # Must be an NYSE trading day
        if trade_date not in nyse_trading_dates:
            skipped_non_trading_days += 1
            continue

        entry_dow = trade_date.weekday()

        # Skip Friday (and any weekends that somehow appear)
        if entry_dow >= 4:
            continue

        day_t = df_daily.iloc[i]

        # Skip tiny moves
        magnitude = abs((day_t['Close'] - day_t['Open']) / day_t['Open']) * 100
        if magnitude < 0.10:
            continue

        # --- Expiry mapping (holiday-aware via trading calendar) ---
        expiry_label = _EXPIRY_LABELS.get(entry_dow)
        if expiry_label is None:
            continue

        expiry_date = weekly_expiry_date(trade_date, nyse_trading_dates)
        if expiry_date is None:
            continue

        # Detect if expiry was holiday-rolled (target weekday didn't match)
        if entry_dow in (0, 1):
            naive_target_dow = 2  # Wed
        else:
            naive_target_dow = 4  # Fri
        naive_target_date = trade_date + timedelta(days=(naive_target_dow - entry_dow))
        if expiry_date != naive_target_date:
            expiry_holiday_rolls += 1

        # Direction & signal
        direction = "GREEN" if day_t['Close'] > day_t['Open'] else "RED"
        option_type = "PUT" if direction == "GREEN" else "CALL"
        signal = f"FADE_{direction}"

        entry_price = day_t['Close']
        atr = day_t['ATR_14']
        if pd.isna(atr):
            continue

        strike = round(entry_price)

        # --- Timestamps (DST-safe via trading_calendar) ---
        entry_dt_et = make_entry_dt(trade_date)
        try:
            exit_dt_et = make_exit_dt(trade_date, nyse_trading_dates)
        except ValueError:
            continue  # no next trading day within 10 days (shouldn't happen)
        expiry_dt_et = make_expiry_dt(expiry_date)

        # UTC master timestamps (Rule 1 & 3: convert once, carry UTC)
        entry_dt_utc = entry_dt_et.astimezone(TZ_UTC)
        # Display-only UK time for entry
        entry_dt_uk = entry_dt_et.astimezone(TZ_UK)

        # Next trading day date (for daily fallback lookup)
        next_td_date = exit_dt_et.date()
        next_td_ts = pd.Timestamp(next_td_date)

        # Sanity assertion: entry ET must be 16:00 (Rule 6)
        assert entry_dt_et.hour == 16 and entry_dt_et.minute == 0, (
            f"Entry time sanity failed: expected 16:00 ET, got "
            f"{entry_dt_et.strftime('%H:%M')} ET on {trade_date}"
        )

        # IV — strictly causal lookup
        iv_date_used_str = None
        if use_vix_iv:
            sigma, iv_date_used_str = get_vix_iv_prior_only(vix_series, date_t)
            if sigma is None:
                skipped_missing_iv += 1
                continue
            # Hard assertion: IV date must not be after the entry date
            lookup_date_str = date_t.strftime("%Y-%m-%d")
            if iv_date_used_str > lookup_date_str:
                forward_iv_count += 1
        else:
            sigma = fixed_iv
            iv_date_used_str = None  # not applicable for fixed IV

        # --- ENTRY PRICING ---
        # T_entry = time from 16:00 ET (entry) to expiry 16:00 ET
        T_entry = year_fraction(entry_dt_et, expiry_dt_et, time_basis,
                                trading_dates_set=trading_dates_ts)

        entry_mid = bs_price(entry_price, strike, T_entry, r, sigma, option_type)
        if entry_mid < 0.01:
            continue

        # Apply cost model to entry
        entry_fill, entry_cost_bd = cost_model.apply_entry(entry_mid)

        # Resolve entry bucket path (CalibratedFixedPointCostModel only)
        entry_bucket_requested = ''
        entry_bucket_resolved = ''
        entry_bucket_fallback_used = False
        entry_resolution_path = ''
        if hasattr(cost_model, 'resolve_params'):
            _hs, _src, _fb, _path = cost_model.resolve_params(
                cost_model.expiry_pattern_filter,
                cost_model.time_bucket_entry,
                cost_model.bucket)
            entry_bucket_requested = cost_model.time_bucket_entry
            entry_bucket_resolved = _src
            entry_bucket_fallback_used = _fb
            entry_resolution_path = _path
            if _fb:
                entry_bucket_fallback_count += 1

        # --- Compute IG → SPY scale factor (if IG gap data is used) ---
        _ig_scale = 1.0
        if overnight_source == 'ig_gap_5m' and _ig_gap_available:
            _ig_scale = compute_ig_spy_scale_factor(
                entry_dt_et, entry_price, _ig_gap_dir)

        # --- EXIT PRICING (three-way branch by exit_mode) ---

        # Initialise exit-variant tracking fields
        exit_reason = ''
        actual_exit_dt_et = None
        exit_underlying = None
        exit_mid = None
        exit_fill = None
        exit_cost_bd = None
        iv_exit = None
        T_at_exit = None
        tp_hit_ts = ''
        peak_pct = ''
        exit_bucket_requested = exit_time_bucket_exit
        exit_bucket_resolved = ''
        exit_bucket_fallback = False
        exit_resolution_path = ''
        underlying_source_at_exit = 'SPY'
        underlying_source_scan_usage = ''

        if exit_mode == 'fixed':
            # ---------------------------------------------------------------
            # FIXED MODE
            # ---------------------------------------------------------------
            if exit_fixed_et == '09:30':
                # Bit-identical to pre-Step 11: use original exit_dt_et
                actual_exit_dt_et = exit_dt_et
            else:
                actual_exit_dt_et = make_exit_dt_at(
                    trade_date, exit_fixed_h, exit_fixed_m, nyse_trading_dates)

            exit_underlying = get_bar_price_at(ticker, actual_exit_dt_et, df_daily)
            underlying_source_at_exit = 'SPY'
            if exit_underlying is None:
                # Try IG gap data before falling back to daily open
                if overnight_source == 'ig_gap_5m' and _ig_gap_available:
                    ig_price, ig_src = get_ig_gap_price_at(
                        actual_exit_dt_et, _ig_gap_dir, lookback_minutes=10,
                        scale_factor=_ig_scale)
                    if ig_price is not None:
                        exit_underlying = ig_price
                        underlying_source_at_exit = ig_src
                        trades_using_ig_gap_at_exit += 1
            if exit_underlying is None:
                exit_date_str = actual_exit_dt_et.date().strftime("%Y-%m-%d")
                intraday_file = f'data/{ticker}/intraday/{exit_date_str}.parquet'
                if not os.path.exists(intraday_file):
                    skipped_missing_intraday_file += 1
                else:
                    skipped_missing_required_bars += 1
                # Fallback: daily open of next trading day
                if next_td_ts in _daily_open.index:
                    exit_underlying = float(_daily_open.loc[next_td_ts])
                    underlying_source_at_exit = 'DAILY_OPEN_FALLBACK'
                else:
                    continue

            T_at_exit = year_fraction(actual_exit_dt_et, expiry_dt_et, time_basis,
                                      trading_dates_set=trading_dates_ts)
            iv_exit = _compute_iv_exit(iv_exit_mode, iv_crush_k, sigma,
                                       option_type, exit_underlying, entry_price)
            exit_mid = bs_price(exit_underlying, strike, T_at_exit, r,
                                iv_exit, option_type)
            exit_fill, exit_cost_bd = cost_model.apply_exit(exit_mid)
            exit_reason = 'FIXED'
            exit_bucket_requested = exit_time_bucket_exit
            exit_bucket_resolved = exit_cost_bd.get('source', '')
            exit_bucket_fallback = exit_cost_bd.get('fallback_used', False)
            if hasattr(cost_model, 'resolve_params'):
                _, _, _, exit_resolution_path = cost_model.resolve_params(
                    cost_model.expiry_pattern_filter,
                    exit_time_bucket_exit,
                    cost_model.bucket)

        elif exit_mode == 'tp_anytime':
            # ---------------------------------------------------------------
            # TP-ANYTIME MODE
            # ---------------------------------------------------------------
            baseline_exit_dt = make_exit_dt_at(
                trade_date, tp_max_h, tp_max_m, nyse_trading_dates)

            overnight_bars = load_overnight_bars(
                ticker, entry_dt_et, baseline_exit_dt)

            # Stitch IG gap data if available
            _ig_scan_bar_ct = 0
            underlying_source_scan_usage = 'SPY_ONLY'
            if overnight_source == 'ig_gap_5m' and _ig_gap_available:
                stitched, _ig_scan_bar_ct = stitch_overnight_bars(
                    overnight_bars, entry_dt_et, baseline_exit_dt,
                    _ig_gap_dir, ig_gap_resample, scale_factor=_ig_scale)
                if not stitched.empty:
                    overnight_bars = stitched
                    ig_gap_scan_bars_total += _ig_scan_bar_ct
                    if _ig_scan_bar_ct > 0:
                        underlying_source_scan_usage = f'SPY+IG_GAP({_ig_scan_bar_ct})'
                    else:
                        underlying_source_scan_usage = 'SPY_ONLY'

            if overnight_bars is None or overnight_bars.empty:
                # No bar data — fall back to daily open at baseline time
                tp_no_data_count += 1
                actual_exit_dt_et = baseline_exit_dt
                if next_td_ts in _daily_open.index:
                    exit_underlying = float(_daily_open.loc[next_td_ts])
                    underlying_source_at_exit = 'DAILY_OPEN_FALLBACK'
                else:
                    continue
                T_at_exit = year_fraction(actual_exit_dt_et, expiry_dt_et, time_basis,
                                          trading_dates_set=trading_dates_ts)
                iv_exit = _compute_iv_exit(iv_exit_mode, iv_crush_k, sigma,
                                           option_type, exit_underlying, entry_price)
                exit_mid = bs_price(exit_underlying, strike, T_at_exit, r,
                                    iv_exit, option_type)
                exit_fill, exit_cost_bd = cost_model.apply_exit(exit_mid)
                exit_reason = 'TP_NO_DATA'
                exit_bucket_requested = exit_time_bucket_exit
                exit_bucket_resolved = exit_cost_bd.get('source', '')
                exit_bucket_fallback = exit_cost_bd.get('fallback_used', False)
                if hasattr(cost_model, 'resolve_params'):
                    _, _, _, exit_resolution_path = cost_model.resolve_params(
                        cost_model.expiry_pattern_filter,
                        exit_time_bucket_exit,
                        cost_model.bucket)
            else:
                tp_result = _scan_tp_exit(
                    overnight_bars, strike, option_type, sigma,
                    iv_exit_mode, iv_crush_k, entry_price, entry_fill,
                    expiry_dt_et, time_basis, trading_dates_ts, r,
                    tp_threshold_pct, tp_check_frequency_min,
                    cost_model, exit_time_bucket_exit, baseline_exit_dt,
                )

                if tp_result is None:
                    # Bars existed but none usable (shouldn't happen often)
                    tp_no_data_count += 1
                    actual_exit_dt_et = baseline_exit_dt
                    if next_td_ts in _daily_open.index:
                        exit_underlying = float(_daily_open.loc[next_td_ts])
                        underlying_source_at_exit = 'DAILY_OPEN_FALLBACK'
                    else:
                        continue
                    T_at_exit = year_fraction(actual_exit_dt_et, expiry_dt_et, time_basis,
                                              trading_dates_set=trading_dates_ts)
                    iv_exit = _compute_iv_exit(iv_exit_mode, iv_crush_k, sigma,
                                               option_type, exit_underlying, entry_price)
                    exit_mid = bs_price(exit_underlying, strike, T_at_exit, r,
                                        iv_exit, option_type)
                    exit_fill, exit_cost_bd = cost_model.apply_exit(exit_mid)
                    exit_reason = 'TP_NO_DATA'
                    exit_bucket_requested = exit_time_bucket_exit
                    exit_bucket_resolved = exit_cost_bd.get('source', '')
                    exit_bucket_fallback = exit_cost_bd.get('fallback_used', False)
                    if hasattr(cost_model, 'resolve_params'):
                        _, _, _, exit_resolution_path = cost_model.resolve_params(
                            cost_model.expiry_pattern_filter,
                            exit_time_bucket_exit,
                            cost_model.bucket)
                elif tp_result['tp_hit']:
                    tp_hit_count += 1
                    actual_exit_dt_et = tp_result['exit_dt_et']
                    exit_underlying = tp_result['exit_underlying']
                    exit_mid = tp_result['exit_mid']
                    exit_fill = tp_result['exit_fill']
                    exit_cost_bd = tp_result['exit_cost_bd']
                    iv_exit = tp_result['iv_exit']
                    T_at_exit = tp_result['T_at_exit']
                    exit_reason = 'TP_HIT'
                    tp_hit_ts = actual_exit_dt_et.strftime("%Y-%m-%d %H:%M ET")
                    peak_pct = tp_result['peak_unrealized_pct']
                    exit_bucket_requested = tp_result['exit_time_bucket_requested']
                    exit_bucket_resolved = tp_result['exit_time_bucket_resolved']
                    exit_bucket_fallback = tp_result['exit_time_bucket_fallback_used']
                    # Look up source from stitched bars for the exit bar
                    if hasattr(overnight_bars, 'columns') and 'source' in overnight_bars.columns:
                        _src_at = overnight_bars.index.get_indexer([actual_exit_dt_et], method='ffill')
                        if _src_at[0] >= 0:
                            underlying_source_at_exit = overnight_bars.iloc[_src_at[0]]['source']
                            if underlying_source_at_exit == 'IG_GAP_5M':
                                trades_using_ig_gap_at_exit += 1
                    if hasattr(cost_model, 'resolve_params'):
                        _, _, _, exit_resolution_path = cost_model.resolve_params(
                            cost_model.expiry_pattern_filter,
                            exit_bucket_requested,
                            cost_model.bucket)
                else:
                    tp_fallback_count += 1
                    actual_exit_dt_et = tp_result['exit_dt_et']
                    exit_underlying = tp_result['exit_underlying']
                    exit_mid = tp_result['exit_mid']
                    exit_fill = tp_result['exit_fill']
                    exit_cost_bd = tp_result['exit_cost_bd']
                    iv_exit = tp_result['iv_exit']
                    T_at_exit = tp_result['T_at_exit']
                    exit_reason = 'TP_FALLBACK'
                    peak_pct = tp_result['peak_unrealized_pct']
                    exit_bucket_requested = tp_result['exit_time_bucket_requested']
                    exit_bucket_resolved = tp_result['exit_time_bucket_resolved']
                    exit_bucket_fallback = tp_result['exit_time_bucket_fallback_used']
                    # Look up source from stitched bars for the exit bar
                    if hasattr(overnight_bars, 'columns') and 'source' in overnight_bars.columns:
                        _src_at = overnight_bars.index.get_indexer([actual_exit_dt_et], method='ffill')
                        if _src_at[0] >= 0:
                            underlying_source_at_exit = overnight_bars.iloc[_src_at[0]]['source']
                            if underlying_source_at_exit == 'IG_GAP_5M':
                                trades_using_ig_gap_at_exit += 1
                    if hasattr(cost_model, 'resolve_params'):
                        _, _, _, exit_resolution_path = cost_model.resolve_params(
                            cost_model.expiry_pattern_filter,
                            exit_bucket_requested,
                            cost_model.bucket)

        elif exit_mode == 'uk_time':
            # ---------------------------------------------------------------
            # UK TIME MODE — "first occurrence of exit_uk_time AFTER entry"
            # ---------------------------------------------------------------
            # Build UK exit as first occurrence of uk_exit_h:uk_exit_m AFTER entry
            _entry_uk = entry_dt_et.astimezone(TZ_UK)
            _cand_date = _entry_uk.date()
            _cand_naive = datetime(_cand_date.year, _cand_date.month, _cand_date.day,
                                   uk_exit_h, uk_exit_m)
            _exit_dt_uk = TZ_UK.localize(_cand_naive)
            if _exit_dt_uk <= _entry_uk:
                _next_date = _cand_date + timedelta(days=1)
                _cand_naive = datetime(_next_date.year, _next_date.month, _next_date.day,
                                       uk_exit_h, uk_exit_m)
                _exit_dt_uk = TZ_UK.localize(_cand_naive)
            actual_exit_dt_et = _exit_dt_uk.astimezone(TZ_ET)

            exit_underlying = get_bar_price_at(ticker, actual_exit_dt_et, df_daily)
            underlying_source_at_exit = 'SPY'

            # If SPY has no bar, try IG gap data
            if exit_underlying is None and overnight_source == 'ig_gap_5m' and _ig_gap_available:
                ig_price, ig_src = get_ig_gap_price_at(
                    actual_exit_dt_et, _ig_gap_dir, lookback_minutes=10,
                    scale_factor=_ig_scale)
                if ig_price is not None:
                    exit_underlying = ig_price
                    underlying_source_at_exit = ig_src
                    trades_using_ig_gap_at_exit += 1

            if exit_underlying is None:
                # No bar from SPY or IG → MISSING_EXIT_BAR
                missing_exit_bar_skips += 1
                continue
            else:
                uk_time_exit_count += 1
                T_at_exit = year_fraction(actual_exit_dt_et, expiry_dt_et, time_basis,
                                          trading_dates_set=trading_dates_ts)
                iv_exit = _compute_iv_exit(iv_exit_mode, iv_crush_k, sigma,
                                           option_type, exit_underlying, entry_price)
                exit_mid = bs_price(exit_underlying, strike, T_at_exit, r,
                                    iv_exit, option_type)
                # Map UK exit time to cost bucket, use dynamic resolution
                exit_bucket_requested = _map_exit_time_bucket(
                    actual_exit_dt_et, exit_time_bucket_exit)
                if hasattr(cost_model, 'apply_exit_dynamic'):
                    exit_fill, exit_cost_bd = cost_model.apply_exit_dynamic(
                        exit_mid, exit_bucket_requested)
                else:
                    exit_fill, exit_cost_bd = cost_model.apply_exit(exit_mid)
                exit_reason = 'UK_TIME'
                exit_bucket_resolved = exit_cost_bd.get('source', '')
                exit_bucket_fallback = exit_cost_bd.get('fallback_used', False)
                if hasattr(cost_model, 'resolve_params'):
                    _, _, _, exit_resolution_path = cost_model.resolve_params(
                        cost_model.expiry_pattern_filter,
                        exit_bucket_requested,
                        cost_model.bucket)

        # Track exit bucket fallback usage
        if exit_bucket_fallback:
            exit_bucket_fallback_count += 1

        # --- P&L (costs applied exactly once, via fills) ---
        gross_pnl_pct = (exit_mid - entry_mid) / entry_mid if entry_mid > 0 else 0
        net_pnl_pct = (exit_fill - entry_fill) / entry_fill if entry_fill > 0 else 0
        net_pnl_pct = max(net_pnl_pct, -1.0)  # Cap at -100%

        result = "WIN" if net_pnl_pct > 0 else "LOSS"

        # UTC master timestamp for actual exit (Rule 3)
        actual_exit_dt_utc = actual_exit_dt_et.astimezone(TZ_UTC)
        # Display-only: derive ET and UK from UTC for logging
        actual_exit_dt_uk = actual_exit_dt_utc.astimezone(TZ_UK)

        # Sanity assertions for exit (Rule 6)
        if exit_mode == 'fixed' and exit_reason == 'FIXED':
            assert actual_exit_dt_et.hour == exit_fixed_h and actual_exit_dt_et.minute == exit_fixed_m, (
                f"Fixed exit time sanity failed: expected {exit_fixed_et} ET, got "
                f"{actual_exit_dt_et.strftime('%H:%M')} ET on {trade_date}"
            )
        if exit_mode == 'uk_time' and exit_reason == 'UK_TIME':
            assert actual_exit_dt_uk.hour == uk_exit_h and actual_exit_dt_uk.minute == uk_exit_m, (
                f"UK exit time sanity failed: expected {exit_uk_time} UK, got "
                f"{actual_exit_dt_uk.strftime('%H:%M')} UK on {trade_date}"
            )

        # DTE group
        if expiry_label in ("MON-WED", "WED-FRI"):
            dte_group = "2D-expiry"
        else:
            dte_group = "1D-expiry"

        trades.append({
            'Date': trade_date.strftime("%Y-%m-%d"),
            'Day': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'][entry_dow],
            'Pattern': expiry_label,
            'DTE_Group': dte_group,
            'Direction': direction,
            'Signal': signal,
            'Option_Type': option_type,
            'Entry_Price': round(entry_price, 2),
            'Exit_Underlying': round(exit_underlying, 2),
            'Strike': strike,
            'ATR': round(atr, 2),
            'IV': round(sigma, 4),
            'IV_Exit': round(iv_exit, 4),
            'IV_Exit_Mode': iv_exit_mode,
            'IV_Date_Used': iv_date_used_str,
            'T_Entry': round(T_entry, 6),
            'T_At_Exit': round(T_at_exit, 6),
            'Entry_Mid': round(entry_mid, 4),
            'Entry_Fill': round(entry_fill, 4),
            'Exit_Mid': round(exit_mid, 4),
            'Exit_Fill': round(exit_fill, 4),
            'Entry_Cost_Pts': round(entry_cost_bd['total_cost_pts'], 4),
            'Entry_Cost_Pct': round(entry_cost_bd['total_cost_pct'], 2),
            'Exit_Cost_Pts': round(exit_cost_bd['total_cost_pts'], 4),
            'Exit_Cost_Pct': round(exit_cost_bd['total_cost_pct'], 2),
            'Gross_PnL_Pct': round(gross_pnl_pct * 100, 2),
            'Net_PnL_Pct': round(net_pnl_pct * 100, 2),
            'PnL_Mult': round(net_pnl_pct, 6),
            'Result': result,
            'Magnitude': round(magnitude, 2),
            # Triple-logged timestamps: UTC (master), ET (display), UK (display)
            'Entry_TS_UTC': entry_dt_utc.isoformat(),
            'Entry_TS_ET': entry_dt_et.isoformat(),
            'Entry_TS_UK': entry_dt_uk.isoformat(),
            'Exit_TS_UTC': actual_exit_dt_utc.isoformat(),
            'Exit_TS_ET': actual_exit_dt_et.isoformat(),
            'Exit_TS_UK': actual_exit_dt_uk.isoformat(),
            'Expiry_ET': expiry_dt_et.strftime("%Y-%m-%d %H:%M ET"),
            # Legacy display columns (kept for backward compat)
            'Entry_ET': entry_dt_et.strftime("%Y-%m-%d %H:%M ET"),
            'Exit_ET': actual_exit_dt_et.strftime("%Y-%m-%d %H:%M ET"),
            'Entry_UK': entry_dt_uk.strftime("%Y-%m-%d %H:%M UK"),
            'Exit_UK': actual_exit_dt_uk.strftime("%Y-%m-%d %H:%M UK"),
            # Step 11 columns
            'Exit_Mode': exit_mode,
            'Exit_Reason': exit_reason,
            # Entry bucket resolution
            'Entry_Time_Bucket_Requested': entry_bucket_requested,
            'Entry_Time_Bucket_Resolved': entry_bucket_resolved,
            'Entry_Time_Bucket_FallbackUsed': entry_bucket_fallback_used,
            'Entry_Resolution_Path': entry_resolution_path,
            # Exit bucket resolution
            'Exit_Time_Bucket_Requested': exit_bucket_requested,
            'Exit_Time_Bucket_Resolved': exit_bucket_resolved,
            'Exit_Time_Bucket_FallbackUsed': exit_bucket_fallback,
            'Exit_Resolution_Path': exit_resolution_path,
            'TP_Threshold_Pct': tp_threshold_pct if exit_mode == 'tp_anytime' else '',
            'TP_Hit': (exit_reason == 'TP_HIT') if exit_mode == 'tp_anytime' else '',
            'TP_Hit_TS_ET': tp_hit_ts if exit_mode == 'tp_anytime' and exit_reason == 'TP_HIT' else '',
            'Peak_Unrealized_Pct': peak_pct if exit_mode == 'tp_anytime' else '',
            # Overnight data source columns
            'Underlying_Source_At_Exit': underlying_source_at_exit,
            'Underlying_Source_Scan_Usage': underlying_source_scan_usage if exit_mode == 'tp_anytime' else '',
        })

    # Hard assertion: zero forward IV lookups
    if forward_iv_count > 0:
        raise RuntimeError(
            f"LOOKAHEAD BUG: {forward_iv_count} trades used a future VIX date. "
            f"This must be zero."
        )

    audit_counters = {
        "skipped_trades_missing_iv": skipped_missing_iv,
        "forward_iv_count": forward_iv_count,
        "skipped_non_trading_days": skipped_non_trading_days,
        "skipped_missing_intraday_file": skipped_missing_intraday_file,
        "skipped_missing_required_bars": skipped_missing_required_bars,
        "expiry_holiday_rolls": expiry_holiday_rolls,
        # Step 11 counters
        "tp_hit_count": tp_hit_count,
        "tp_fallback_count": tp_fallback_count,
        "tp_no_data_count": tp_no_data_count,
        "uk_time_exit_count": uk_time_exit_count,
        "missing_exit_bar_skips": missing_exit_bar_skips,
        "exit_bucket_fallback_count": exit_bucket_fallback_count,
        "entry_bucket_fallback_count": entry_bucket_fallback_count,
        # Overnight data source counters
        "overnight_source": overnight_source,
        "trades_using_ig_gap_at_exit": trades_using_ig_gap_at_exit,
        "ig_gap_days_missing_count": ig_gap_days_missing_count,
        "ig_gap_scan_bars_total": ig_gap_scan_bars_total,
    }

    console.print(f"[green]{ticker}: {len(trades)} trades generated (exit_mode={exit_mode})[/green]")
    if skipped_missing_iv > 0:
        console.print(f"[yellow]  Skipped {skipped_missing_iv} trades: missing IV[/yellow]")
    if skipped_non_trading_days > 0:
        console.print(f"[yellow]  Skipped {skipped_non_trading_days} non-NYSE trading days in data[/yellow]")
    if skipped_missing_intraday_file > 0:
        console.print(f"[yellow]  Missing intraday files (used daily open fallback): {skipped_missing_intraday_file}[/yellow]")
    if skipped_missing_required_bars > 0:
        console.print(f"[yellow]  Missing required bars (used daily open fallback): {skipped_missing_required_bars}[/yellow]")
    if expiry_holiday_rolls > 0:
        console.print(f"[yellow]  Expiry holiday rolls (forward): {expiry_holiday_rolls}[/yellow]")
    if missing_exit_bar_skips > 0:
        console.print(f"[yellow]  Missing exit bar skips: {missing_exit_bar_skips}[/yellow]")
    console.print(f"[cyan]  Forward IV lookups: {forward_iv_count} (must be 0)[/cyan]")
    # Step 11 exit variant stats
    if exit_mode == 'tp_anytime':
        console.print(f"[cyan]  TP hits: {tp_hit_count}, TP fallbacks: {tp_fallback_count}, TP no data: {tp_no_data_count}[/cyan]")
    if exit_mode == 'uk_time':
        console.print(f"[cyan]  UK time exits: {uk_time_exit_count}, missing exit bars: {missing_exit_bar_skips}[/cyan]")
    if exit_bucket_fallback_count > 0:
        console.print(f"[yellow]  Exit bucket fallbacks: {exit_bucket_fallback_count}[/yellow]")
    if entry_bucket_fallback_count > 0:
        console.print(f"[yellow]  Entry bucket fallbacks: {entry_bucket_fallback_count}[/yellow]")
    # Overnight data source stats
    if overnight_source == 'ig_gap_5m':
        console.print(f"[cyan]  Overnight source: ig_gap_5m (available={_ig_gap_available})[/cyan]")
        console.print(f"[cyan]  Trades using IG gap at exit: {trades_using_ig_gap_at_exit}[/cyan]")
        if exit_mode == 'tp_anytime':
            console.print(f"[cyan]  IG gap scan bars (total): {ig_gap_scan_bars_total}[/cyan]")

    return pd.DataFrame(trades), audit_counters


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def display_results(df, label):
    """Display comprehensive results."""
    if df is None or df.empty:
        console.print("[red]No results[/red]")
        return

    wins = df[df['Result'] == 'WIN']
    losses = df[df['Result'] == 'LOSS']
    n = len(df)
    n_wins = len(wins)
    win_rate = n_wins / n * 100

    avg_win = wins['Net_PnL_Pct'].mean() if len(wins) > 0 else 0
    avg_loss = losses['Net_PnL_Pct'].mean() if len(losses) > 0 else 0
    ev = df['Net_PnL_Pct'].mean()

    # Equity curve
    equity = 10000
    kelly = 0.10  # 10% of equity per trade
    eq_list = []
    for _, row in df.iterrows():
        pos = equity * kelly
        equity += pos * row['PnL_Mult']
        equity = max(equity, 1.0)
        eq_list.append(equity)

    final = eq_list[-1] if eq_list else 10000
    years = (pd.to_datetime(df['Date'].iloc[-1]) - pd.to_datetime(df['Date'].iloc[0])).days / 365.25
    cagr = (pow(final / 10000, 1 / years) - 1) * 100 if years > 0 and final > 0 else 0

    running_max = pd.Series(eq_list).expanding().max()
    max_dd = ((pd.Series(eq_list) - running_max) / running_max * 100).min()

    console.print(f"\n  [bold]{label}[/bold]")
    console.print(f"  Trades:     {n:,} ({n_wins:,} wins, {n - n_wins:,} losses)")
    console.print(f"  Win Rate:   [bold]{win_rate:.1f}%[/bold]")
    console.print(f"  Avg Win:    [green]{avg_win:+.2f}%[/green]")
    console.print(f"  Avg Loss:   [red]{avg_loss:+.2f}%[/red]")
    console.print(f"  [bold]EV/trade:   {ev:+.2f}%[/bold]")
    console.print(f"  Equity:     ${10000:,} -> ${final:,.0f}  (CAGR {cagr:+.1f}%)")
    console.print(f"  Max DD:     {max_dd:.1f}%")
    console.print()

    # Breakdown by pattern
    table = Table(show_header=True, header_style="bold cyan", title=f"{label} - By Pattern")
    table.add_column("Pattern", width=12)
    table.add_column("Trades", justify="right", width=8)
    table.add_column("Win%", justify="right", width=8)
    table.add_column("Avg Win", justify="right", width=9)
    table.add_column("Avg Loss", justify="right", width=9)
    table.add_column("EV", justify="right", width=10)
    table.add_column("Avg T_exit", justify="right", width=10)

    for pattern in ["MON-WED", "TUE-WED", "WED-FRI", "THU-FRI"]:
        sub = df[df['Pattern'] == pattern]
        if len(sub) == 0:
            continue
        wr = (sub['Result'] == 'WIN').sum() / len(sub) * 100
        aw = sub[sub['Result'] == 'WIN']['Net_PnL_Pct'].mean() if (sub['Result'] == 'WIN').sum() > 0 else 0
        al = sub[sub['Result'] == 'LOSS']['Net_PnL_Pct'].mean() if (sub['Result'] == 'LOSS').sum() > 0 else 0
        ev_p = sub['Net_PnL_Pct'].mean()
        avg_t = sub['T_At_Exit'].mean()
        style = "green" if ev_p > 0 else "red"
        table.add_row(
            pattern, f"{len(sub):,}", f"{wr:.1f}%",
            f"{aw:+.1f}%", f"{al:+.1f}%",
            f"[{style}]{ev_p:+.2f}%[/{style}]",
            f"{avg_t:.5f}",
        )

    # ALL row
    avg_t_all = df['T_At_Exit'].mean()
    all_style = "green" if ev > 0 else "red"
    table.add_row(
        "[bold]ALL[/bold]", f"[bold]{n:,}[/bold]", f"[bold]{win_rate:.1f}%[/bold]",
        f"[bold]{avg_win:+.1f}%[/bold]", f"[bold]{avg_loss:+.1f}%[/bold]",
        f"[bold][{all_style}]{ev:+.2f}%[/{all_style}][/bold]",
        f"{avg_t_all:.5f}",
    )

    console.print(table)
    console.print()

    # Breakdown by direction
    for dir_label in ["RED", "GREEN"]:
        sub = df[df['Direction'] == dir_label]
        if len(sub) == 0:
            continue
        wr = (sub['Result'] == 'WIN').sum() / len(sub) * 100
        ev_d = sub['Net_PnL_Pct'].mean()
        style = "green" if ev_d > 0 else "red"
        console.print(f"  {dir_label} days: {len(sub)} trades, {wr:.1f}% win, [{style}]{ev_d:+.2f}% EV[/{style}]")

    console.print()


def display_sample_trades(df, n=5):
    """Show sample trades with full timestamp detail."""
    console.print("[bold]Sample trades (first 5):[/bold]")
    sample = df.head(n)
    for _, row in sample.iterrows():
        style = "green" if row['Result'] == 'WIN' else "red"
        console.print(
            f"  {row['Date']} {row['Day']} | {row['Signal']:12s} | "
            f"Entry: {row['Entry_UK']} | Exit: {row['Exit_UK']} | "
            f"Expiry: {row['Expiry_ET']} | "
            f"Mid: {row['Entry_Mid']:.2f} -> {row['Exit_Mid']:.2f} | "
            f"T_exit: {row['T_At_Exit']:.5f} | "
            f"[{style}]{row['Net_PnL_Pct']:+.1f}%[/{style}]"
        )
    console.print()


# ---------------------------------------------------------------------------
# Output path resolution
# ---------------------------------------------------------------------------

def resolve_output_path(path_str, overwrite):
    """
    If overwrite is True, return path as-is.
    If file exists and overwrite is False, insert a timestamp before the extension.
    """
    p = Path(path_str)
    if overwrite or not p.exists():
        return p
    # Insert timestamp
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return p.parent / f"{p.stem}_{ts}{p.suffix}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Canonical overnight-fade backtest runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--ticker", default="SPY",
        help="Ticker symbol (default: SPY)",
    )
    parser.add_argument(
        "--iv-mode", choices=["vix", "fixed"], default="vix",
        help="IV source: 'vix' (VIX proxy) or 'fixed' (constant IV) (default: vix)",
    )
    parser.add_argument(
        "--fixed-iv", type=float, default=0.15,
        help="Fixed IV value, used only if --iv-mode fixed (default: 0.15)",
    )
    parser.add_argument(
        "--direction", choices=["red", "green", "all"], default="all",
        help="Direction filter applied post-generation (default: all)",
    )
    parser.add_argument(
        "--time-basis", choices=["rth", "calendar"], default="rth",
        help="Time-to-expiry basis: 'rth' (trading minutes only) or 'calendar' (wall-clock) (default: rth)",
    )
    # IV at exit
    parser.add_argument(
        "--iv-exit-mode", choices=["same", "crush"], default="same",
        help="IV at exit: 'same' (iv_exit=iv_entry) or 'crush' (direction-aware) (default: same)",
    )
    parser.add_argument(
        "--iv-crush-k", type=float, default=0.10,
        help="IV crush/expand factor for crush mode (default: 0.10 = 10%%)",
    )
    # Cost model flags
    parser.add_argument(
        "--cost-model", choices=["percent", "fixed", "calibrated"], default="percent",
        help="Cost model: 'percent', 'fixed', or 'calibrated' (JSON-driven) (default: percent)",
    )
    parser.add_argument(
        "--cost-calibration-file", type=str, default=None,
        help="Path to calibration JSON file (required if --cost-model calibrated)",
    )
    parser.add_argument(
        "--cost-bucket", type=str, default="ATM",
        help="Cost bucket name / strike_type from calibration file (default: ATM)",
    )
    parser.add_argument(
        "--expiry-pattern-filter", type=str, default=None,
        help="Expiry pattern filter for v2 calibration (e.g. SPXWED, SPXEMO). "
             "If omitted, uses global defaults.",
    )
    parser.add_argument(
        "--cost-time-bucket-entry", type=str, default="ENTRY",
        help="Time bucket for entry spread in v2 calibration (default: ENTRY)",
    )
    parser.add_argument(
        "--cost-time-bucket-exit", type=str, default="EXIT",
        help="Time bucket for exit spread in v2 calibration (default: EXIT)",
    )
    parser.add_argument(
        "--half-spread-pct", type=float, default=0.02,
        help="Half bid-ask spread as fraction of mid (percent model only, default: 0.02 = 2%%)",
    )
    parser.add_argument(
        "--slippage-pct", type=float, default=0.005,
        help="Slippage as fraction of mid per side (percent model only, default: 0.005 = 0.5%%)",
    )
    parser.add_argument(
        "--half-spread-pts", type=float, default=0.10,
        help="Half bid-ask spread in points (fixed model only, default: 0.10)",
    )
    parser.add_argument(
        "--slippage-pts", type=float, default=0.00,
        help="Slippage in points per side (fixed model only, default: 0.00)",
    )
    parser.add_argument(
        "--output", default="results/overnight_fade_canonical.csv",
        help="Path for CSV trade log (default: results/overnight_fade_canonical.csv)",
    )
    parser.add_argument(
        "--summary", default="results/overnight_fade_canonical_summary.json",
        help="Path for JSON summary (default: results/overnight_fade_canonical_summary.json)",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Overwrite existing output files (default: write timestamped filenames)",
    )
    parser.add_argument(
        "--calendar-audit", action="store_true",
        help="Run calendar validation on DST transition weeks and holiday weeks, "
             "writing results/calendar_audit.csv",
    )
    # --- Exit variant flags (Step 11) ---
    parser.add_argument(
        "--exit-mode", choices=["fixed", "tp_anytime", "uk_time"], default="fixed",
        help="Exit mode: 'fixed' (default 09:30 or custom ET), 'tp_anytime' (scan bars for TP), "
             "'uk_time' (exit at specific UK time) (default: fixed)",
    )
    parser.add_argument(
        "--exit-fixed-et", type=str, default="09:30",
        help="Fixed exit time in ET for fixed mode, format HH:MM (default: 09:30)",
    )
    parser.add_argument(
        "--tp-threshold-pct", type=float, default=30.0,
        help="Take-profit threshold in percent for tp_anytime mode (default: 30.0)",
    )
    parser.add_argument(
        "--tp-check-frequency-min", type=int, default=1,
        help="Bar check frequency in minutes for tp_anytime mode (default: 1)",
    )
    parser.add_argument(
        "--tp-max-exit-et", type=str, default="09:30",
        help="Latest exit time in ET for tp_anytime fallback, format HH:MM (default: 09:30)",
    )
    parser.add_argument(
        "--exit-uk-time", type=str, default=None,
        help="Exit time in UK time for uk_time mode, format HH:MM (required if --exit-mode uk_time)",
    )
    # --- Overnight data source flags ---
    parser.add_argument(
        "--overnight-source", choices=["spy_only", "ig_gap_5m"], default="spy_only",
        help="Underlying data source for exits: 'spy_only' (SPY intraday only) or "
             "'ig_gap_5m' (stitch IG US500 5m gap data where SPY is missing) (default: spy_only)",
    )
    parser.add_argument(
        "--ig-gap-dir", type=str, default=None,
        help="Directory with IG gap 5m parquet files (default: data/IG_US500_spy_gap_5m)",
    )
    parser.add_argument(
        "--ig-gap-resample", choices=["ffill", "none"], default="ffill",
        help="IG gap resample mode: 'ffill' (forward-fill 5m to 1m) or 'none' (keep 5m) (default: ffill)",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Calendar audit
# ---------------------------------------------------------------------------

def _find_audit_dates(df):
    """
    Pick 'known tricky' trade dates from the backtest DataFrame for audit:
      - US DST spring-forward weeks (second Sunday of March)
      - US DST fall-back weeks (first Sunday of November)
      - UK DST spring-forward weeks (last Sunday of March)
      - UK DST fall-back weeks (last Sunday of October)
      - Thanksgiving weeks
      - Christmas weeks
    Returns list of (trade_date_str, notes) tuples.
    """
    from dateutil.easter import easter  # noqa: F401

    all_dates = sorted(df['Date'].unique())
    all_dates_set = set(all_dates)
    audit = []

    # Get year range
    years = sorted(set(d[:4] for d in all_dates))

    for year_str in years:
        yr = int(year_str)

        # --- US DST spring forward: second Sunday of March ---
        # Find trading Mon-Fri around that Sunday
        mar1 = date_type(yr, 3, 1)
        # second Sunday: first Sunday + 7
        first_sun = mar1 + timedelta(days=(6 - mar1.weekday()) % 7)
        us_spring = first_sun + timedelta(days=7)
        # Get Mon-Fri of that week
        mon = us_spring - timedelta(days=us_spring.weekday())
        for d_off in range(5):
            ds = (mon + timedelta(days=d_off)).strftime("%Y-%m-%d")
            if ds in all_dates_set:
                audit.append((ds, f"US DST spring-forward week (Sun {us_spring})"))

        # --- US DST fall back: first Sunday of November ---
        nov1 = date_type(yr, 11, 1)
        us_fall = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
        mon = us_fall - timedelta(days=us_fall.weekday())
        for d_off in range(5):
            ds = (mon + timedelta(days=d_off)).strftime("%Y-%m-%d")
            if ds in all_dates_set:
                audit.append((ds, f"US DST fall-back week (Sun {us_fall})"))

        # --- UK DST spring forward: last Sunday of March ---
        mar31 = date_type(yr, 3, 31)
        uk_spring = mar31 - timedelta(days=(mar31.weekday() + 1) % 7)
        mon = uk_spring - timedelta(days=uk_spring.weekday())
        for d_off in range(5):
            ds = (mon + timedelta(days=d_off)).strftime("%Y-%m-%d")
            if ds in all_dates_set and (ds, f"US DST spring-forward week (Sun {us_spring})") not in audit:
                audit.append((ds, f"UK DST spring-forward week (Sun {uk_spring})"))

        # --- UK DST fall back: last Sunday of October ---
        oct31 = date_type(yr, 10, 31)
        uk_fall = oct31 - timedelta(days=(oct31.weekday() + 1) % 7)
        mon = uk_fall - timedelta(days=uk_fall.weekday())
        for d_off in range(5):
            ds = (mon + timedelta(days=d_off)).strftime("%Y-%m-%d")
            if ds in all_dates_set:
                audit.append((ds, f"UK DST fall-back week (Sun {uk_fall})"))

        # --- Thanksgiving week (4th Thursday of Nov) ---
        nov1 = date_type(yr, 11, 1)
        first_thu = nov1 + timedelta(days=(3 - nov1.weekday()) % 7)
        thanksgiving = first_thu + timedelta(days=21)  # 4th Thursday
        mon = thanksgiving - timedelta(days=thanksgiving.weekday())
        for d_off in range(5):
            ds = (mon + timedelta(days=d_off)).strftime("%Y-%m-%d")
            if ds in all_dates_set:
                audit.append((ds, f"Thanksgiving week ({thanksgiving})"))

        # --- Christmas week ---
        xmas = date_type(yr, 12, 25)
        mon = xmas - timedelta(days=xmas.weekday())
        for d_off in range(5):
            ds = (mon + timedelta(days=d_off)).strftime("%Y-%m-%d")
            if ds in all_dates_set:
                audit.append((ds, f"Christmas week"))

    # Deduplicate: keep first note per date
    seen = {}
    for ds, note in audit:
        if ds not in seen:
            seen[ds] = note
        else:
            seen[ds] += f"; {note}"
    return sorted(seen.items())


def _run_calendar_audit(df, ticker):
    """
    Generate results/calendar_audit.csv with DST/holiday validation rows.

    For each audit date found in the backtest trades, confirm:
      - Entry is always 16:00 ET
      - Exit is always 09:30 ET on the next NYSE trading day
      - Expiry mapping hits the correct trading day
      - UK times shift appropriately during DST weeks
    """
    audit_dates = _find_audit_dates(df)
    console.print(f"  Audit dates found: {len(audit_dates)}")

    # Build schedule once for validation
    data_start = df['Date'].min()
    data_end = df['Date'].max()
    nyse_schedule = get_nyse_schedule(data_start, data_end)
    nyse_td = build_trading_dates_set(nyse_schedule)

    rows = []
    for trade_date_str, notes in audit_dates:
        trade_date = date_type.fromisoformat(trade_date_str)

        # Is it an NYSE trading day?
        is_td = is_trading_day(trade_date, nyse_td)

        # Only build timestamps if it's a trading day
        if is_td:
            entry_dt = make_entry_dt(trade_date)
            try:
                exit_dt = make_exit_dt(trade_date, nyse_td)
            except ValueError:
                exit_dt = None
            exp_date = weekly_expiry_date(trade_date, nyse_td)
            exp_dt = make_expiry_dt(exp_date) if exp_date else None

            # Check if expiry was holiday-rolled
            dow = trade_date.weekday()
            if dow in (0, 1):
                naive_target = trade_date + timedelta(days=(2 - dow))
            elif dow in (2, 3):
                naive_target = trade_date + timedelta(days=(4 - dow))
            else:
                naive_target = None
            if naive_target and exp_date and exp_date != naive_target:
                notes += f"; EXPIRY ROLLED from {naive_target} to {exp_date}"

            rows.append({
                'Trade_Date': trade_date_str,
                'Is_Trading_Day': is_td,
                'Entry_DT_ET': entry_dt.strftime("%Y-%m-%d %H:%M %Z") if entry_dt else '',
                'Entry_DT_UK': entry_dt.astimezone(TZ_UK).strftime("%Y-%m-%d %H:%M %Z") if entry_dt else '',
                'Exit_DT_ET': exit_dt.strftime("%Y-%m-%d %H:%M %Z") if exit_dt else '',
                'Exit_DT_UK': exit_dt.astimezone(TZ_UK).strftime("%Y-%m-%d %H:%M %Z") if exit_dt else '',
                'Expiry_Date': str(exp_date) if exp_date else 'FRIDAY-SKIP',
                'Expiry_DT_ET': exp_dt.strftime("%Y-%m-%d %H:%M %Z") if exp_dt else '',
                'Notes': notes,
            })
        else:
            rows.append({
                'Trade_Date': trade_date_str,
                'Is_Trading_Day': False,
                'Entry_DT_ET': '',
                'Entry_DT_UK': '',
                'Exit_DT_ET': '',
                'Exit_DT_UK': '',
                'Expiry_Date': '',
                'Expiry_DT_ET': '',
                'Notes': f"NOT A TRADING DAY; {notes}",
            })

    audit_df = pd.DataFrame(rows)
    audit_path = Path("results/calendar_audit.csv")
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_df.to_csv(audit_path, index=False)
    console.print(f"  [green]Calendar audit saved: {audit_path} ({len(audit_df)} rows)[/green]")

    # Print summary of key checks
    if not audit_df.empty:
        td_rows = audit_df[audit_df['Is_Trading_Day'] == True]
        if not td_rows.empty:
            # All entries at 16:00
            entry_times = td_rows['Entry_DT_ET'].apply(lambda x: x[11:16] if x else '')
            all_1600 = (entry_times == '16:00').all()
            # All exits at 09:30
            exit_times = td_rows['Exit_DT_ET'].apply(lambda x: x[11:16] if x else '')
            all_0930 = (exit_times == '09:30').all()
            console.print(f"  Entry always 16:00 ET: {'PASS' if all_1600 else 'FAIL'}")
            console.print(f"  Exit always 09:30 ET:  {'PASS' if all_0930 else 'FAIL'}")

            # Check for expiry rolls
            rolls = audit_df[audit_df['Notes'].str.contains('EXPIRY ROLLED', na=False)]
            if len(rolls) > 0:
                console.print(f"  Expiry holiday rolls found: {len(rolls)}")
                for _, r in rolls.iterrows():
                    console.print(f"    {r['Trade_Date']}: {r['Notes']}")

        non_td = audit_df[audit_df['Is_Trading_Day'] == False]
        if len(non_td) > 0:
            console.print(f"  Non-trading days in audit: {len(non_td)}")
            for _, r in non_td.iterrows():
                console.print(f"    {r['Trade_Date']}: {r['Notes']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    # --- Step 11 validation ---
    if args.exit_mode == 'uk_time' and args.exit_uk_time is None:
        console.print("[red]--exit-uk-time is required when --exit-mode uk_time[/red]")
        sys.exit(1)

    # Validate time format strings
    for time_arg, name in [(args.exit_fixed_et, '--exit-fixed-et'),
                           (args.tp_max_exit_et, '--tp-max-exit-et')]:
        try:
            parts = time_arg.split(':')
            assert len(parts) == 2
            h, m = int(parts[0]), int(parts[1])
            assert 0 <= h <= 23 and 0 <= m <= 59
        except (ValueError, AssertionError):
            console.print(f"[red]{name} must be HH:MM format (got '{time_arg}')[/red]")
            sys.exit(1)

    if args.exit_uk_time is not None:
        try:
            parts = args.exit_uk_time.split(':')
            assert len(parts) == 2
            h, m = int(parts[0]), int(parts[1])
            assert 0 <= h <= 23 and 0 <= m <= 59
        except (ValueError, AssertionError):
            console.print(f"[red]--exit-uk-time must be HH:MM format (got '{args.exit_uk_time}')[/red]")
            sys.exit(1)

    console.print("=" * 90)
    console.print("[bold blue]OVERNIGHT FADE: Canonical Backtest Runner[/bold blue]")
    console.print("=" * 90)
    console.print()

    # Build cost model from args
    if args.cost_model == "percent":
        cost_model = PercentPremiumCostModel(
            half_spread_pct=args.half_spread_pct,
            slippage_pct=args.slippage_pct,
        )
    elif args.cost_model == "calibrated":
        if not args.cost_calibration_file:
            console.print("[red]--cost-calibration-file required when --cost-model calibrated[/red]")
            sys.exit(1)
        cost_model = CalibratedFixedPointCostModel(
            calibration_path=args.cost_calibration_file,
            bucket=args.cost_bucket,
            expiry_pattern_filter=args.expiry_pattern_filter,
            time_bucket_entry=args.cost_time_bucket_entry,
            time_bucket_exit=args.cost_time_bucket_exit,
        )
    else:
        cost_model = FixedPointCostModel(
            half_spread_pts=args.half_spread_pts,
            slippage_pts=args.slippage_pts,
        )

    # Display run parameters
    console.print("[bold]Run parameters:[/bold]")
    console.print(f"  Ticker:     {args.ticker}")
    console.print(f"  IV mode:    {args.iv_mode}")
    if args.iv_mode == "fixed":
        console.print(f"  Fixed IV:   {args.fixed_iv}")
    console.print(f"  Direction:  {args.direction}")
    console.print(f"  Time basis: {args.time_basis}")
    console.print(f"  IV exit:    {args.iv_exit_mode}", end="")
    if args.iv_exit_mode == "crush":
        console.print(f" (k={args.iv_crush_k})")
    else:
        console.print()
    console.print(f"  Cost model: {args.cost_model}")
    cost_desc = cost_model.describe()
    for k, v in cost_desc.items():
        if k != "type":
            console.print(f"    {k}: {v}")
    console.print(f"  Exit mode:  {args.exit_mode}")
    if args.exit_mode == 'fixed':
        console.print(f"    exit time: {args.exit_fixed_et} ET")
    elif args.exit_mode == 'tp_anytime':
        console.print(f"    TP threshold: {args.tp_threshold_pct}%")
        console.print(f"    TP check freq: {args.tp_check_frequency_min} min")
        console.print(f"    TP max exit: {args.tp_max_exit_et} ET")
    elif args.exit_mode == 'uk_time':
        console.print(f"    UK exit time: {args.exit_uk_time} UK")
    console.print(f"  Overnight:  {args.overnight_source}")
    if args.overnight_source == 'ig_gap_5m':
        _gap_dir = args.ig_gap_dir or 'data/IG_US500_spy_gap_5m'
        console.print(f"    IG gap dir: {_gap_dir}")
        console.print(f"    IG gap resample: {args.ig_gap_resample}")
    console.print(f"  Output:     {args.output}")
    console.print(f"  Summary:    {args.summary}")
    console.print(f"  Overwrite:  {args.overwrite}")
    console.print()

    # Load VIX data (needed even for fixed IV to keep function signature stable)
    use_vix = args.iv_mode == "vix"
    vix_series = None
    if use_vix:
        vix_series = load_vix_data()
        console.print(f"[green]VIX loaded: {vix_series.index.min().date()} to {vix_series.index.max().date()}[/green]")
    else:
        console.print(f"[yellow]Using fixed IV = {args.fixed_iv}[/yellow]")
    console.print()

    # Run backtest (single run)
    result = run_overnight_fade(
        args.ticker,
        vix_series,
        use_vix_iv=use_vix,
        fixed_iv=args.fixed_iv,
        time_basis=args.time_basis,
        cost_model=cost_model,
        iv_exit_mode=args.iv_exit_mode,
        iv_crush_k=args.iv_crush_k,
        exit_mode=args.exit_mode,
        exit_fixed_et=args.exit_fixed_et,
        tp_threshold_pct=args.tp_threshold_pct,
        tp_check_frequency_min=args.tp_check_frequency_min,
        tp_max_exit_et=args.tp_max_exit_et,
        exit_uk_time=args.exit_uk_time,
        exit_time_bucket_exit=args.cost_time_bucket_exit,
        overnight_source=args.overnight_source,
        ig_gap_dir=args.ig_gap_dir,
        ig_gap_resample=args.ig_gap_resample,
    )
    df, audit_counters = result

    if df is None or df.empty:
        console.print("[red]No trades generated. Exiting.[/red]")
        sys.exit(1)

    # Sort by Date ascending for determinism
    df = df.sort_values('Date').reset_index(drop=True)

    # Apply direction filter
    direction_filter = args.direction.upper()
    pre_filter_count = len(df)
    if direction_filter != "ALL":
        df = df[df['Direction'] == direction_filter].reset_index(drop=True)
    console.print(f"[cyan]Direction filter '{args.direction}': {pre_filter_count} -> {len(df)} trades[/cyan]")
    console.print()

    if df.empty:
        console.print("[red]No trades after direction filter. Exiting.[/red]")
        sys.exit(1)

    # Display results
    iv_label = f"VIX IV" if use_vix else f"Fixed IV ({args.fixed_iv})"
    label = f"{args.ticker} | {iv_label} | {args.direction.upper()}"
    display_sample_trades(df)
    display_results(df, label)

    # Resolve output paths
    csv_path = resolve_output_path(args.output, args.overwrite)
    json_path = resolve_output_path(args.summary, args.overwrite)

    # Ensure parent directories exist
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)

    # Write CSV
    df.to_csv(csv_path, index=False)
    console.print(f"[green]CSV saved: {csv_path}[/green]")

    # Build and write JSON summary
    run_params = {
        "ticker": args.ticker,
        "iv_mode": args.iv_mode,
        "fixed_iv": args.fixed_iv if args.iv_mode == "fixed" else None,
        "direction": args.direction,
        "time_basis": args.time_basis,
        "iv_exit_mode": args.iv_exit_mode,
        "iv_crush_k": args.iv_crush_k if args.iv_exit_mode == "crush" else None,
        "cost_model": cost_model.describe()["type"],
        "cost_parameters": {k: v for k, v in cost_model.describe().items() if k != "type"},
        # Step 11 exit variant parameters
        "exit_mode": args.exit_mode,
        "exit_fixed_et": args.exit_fixed_et if args.exit_mode == 'fixed' else None,
        "tp_threshold_pct": args.tp_threshold_pct if args.exit_mode == 'tp_anytime' else None,
        "tp_check_frequency_min": args.tp_check_frequency_min if args.exit_mode == 'tp_anytime' else None,
        "tp_max_exit_et": args.tp_max_exit_et if args.exit_mode == 'tp_anytime' else None,
        "exit_uk_time": args.exit_uk_time if args.exit_mode == 'uk_time' else None,
        # Overnight data source
        "overnight_source": args.overnight_source,
        "ig_gap_dir": args.ig_gap_dir if args.overnight_source == 'ig_gap_5m' else None,
        "ig_gap_resample": args.ig_gap_resample if args.overnight_source == 'ig_gap_5m' else None,
    }
    summary = compute_summary_metrics(df, run_params)
    summary["files_written"] = {
        "csv": str(csv_path),
        "json": str(json_path),
    }
    # All audit counters from the backtest
    for key, val in audit_counters.items():
        summary[key] = val
    # Exit mode breakdown from trade data
    if 'Exit_Reason' in df.columns:
        summary["exit_mode_counts"] = df['Exit_Reason'].value_counts().to_dict()
    if 'Underlying_Source_At_Exit' in df.columns:
        summary["underlying_source_counts"] = df['Underlying_Source_At_Exit'].value_counts().to_dict()
    summary["calendar_name"] = "NYSE"
    summary["run_timestamp"] = datetime.now().isoformat()

    with open(json_path, 'w') as f:
        json.dump(summary, f, indent=2)
    console.print(f"[green]JSON saved: {json_path}[/green]")

    # ------------------------------------------------------------------
    # Calendar audit (optional — writes results/calendar_audit.csv)
    # ------------------------------------------------------------------
    if getattr(args, 'calendar_audit', False):
        console.print()
        console.print("[bold yellow]--- Calendar Audit ---[/bold yellow]")
        _run_calendar_audit(df, args.ticker)

    console.print()
    console.print("=" * 90)
    console.print("[bold green]Done.[/bold green]")
    console.print("=" * 90)


if __name__ == "__main__":
    main()
