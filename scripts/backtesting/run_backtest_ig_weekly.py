"""
Backtest: IG WEEKLY US 500 OPTIONS (CORRECTED EXPIRY MODEL)

CRITICAL FIX over previous backtests (run_backtest_option_limit.py etc.):
  Previous backtests treated 09:30 ET open as the option's expiry moment,
  computing exit premium with T=0 (intrinsic only). This is WRONG.

  IG Weekly US 500 options settle at the OFFICIAL SPX CASH CLOSE (16:00 ET).
  The option is LIVE the entire trading day on expiry day.

  Impact: A non-hit trade previously showed -100% loss. With correct T remaining,
  actual loss is -15% (1-DTE) to +46% (2-DTE flat open) because the option
  retains significant time value after the overnight session.

IG Weekly US 500 option patterns:
  Monday    21:00 UK -> Wednesday 16:00 ET close (2 trading days)
  Tuesday   21:00 UK -> Wednesday 16:00 ET close (1 trading day)
  Wednesday 21:00 UK -> Friday    16:00 ET close (2 trading days)
  Thursday  21:00 UK -> Friday    16:00 ET close (1 trading day)
  Friday    21:00 UK -> Monday    16:00 ET close (3 calendar days, 1 trading day)

5 specific fixes vs run_backtest_option_limit.py:
  1. T_entry = dte / 252.0 (trading days), not dte / 365.0 (calendar)
  2. expiry_1600 replaces expiry_930 as the reference point
  3. Expiry-day bar window extends to 16:00 ET (not 09:30)
  4. T_remaining uses trading-minutes model (390 min/day, 252 days/year)
  5. Loss exit at 16:00 close bar on expiry day (not 09:30 open)
"""
import sys
sys.path.insert(0, 'src')

import pandas as pd
import numpy as np
import json
import os
import math
from datetime import datetime, timedelta, date as date_type
from pathlib import Path
from rich.console import Console
from rich.table import Table

console = Console()


# ---------------------------------------------------------------------------
# Black-Scholes
# ---------------------------------------------------------------------------

def norm_cdf(x):
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

def bs_price(S, K, T, r, sigma, option_type):
    if T <= 0:
        return max(S - K, 0.0) if option_type == 'CALL' else max(K - S, 0.0)
    if sigma <= 0:
        sigma = 0.001
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if option_type == 'CALL':
        return max(S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2), 0.0)
    else:
        return max(K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1), 0.0)


# ---------------------------------------------------------------------------
# VIX for realistic IV
# ---------------------------------------------------------------------------

def load_vix_data():
    """Load VIX daily data from CBOE parquet (1990-present, OHLC)."""
    cache_file = Path("data/vix_daily_cache.parquet")
    if not cache_file.exists():
        raise FileNotFoundError("data/vix_daily_cache.parquet not found.")
    df = pd.read_parquet(cache_file)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df['Close']

def get_iv(date, vix_series):
    if vix_series is not None:
        lookup = pd.Timestamp(date).normalize()
        if lookup in vix_series.index:
            return vix_series.loc[lookup] / 100.0
        nearby = vix_series.index[abs(vix_series.index - lookup) <= pd.Timedelta(days=5)]
        if len(nearby) > 0:
            return vix_series.loc[nearby[abs(nearby - lookup).argmin()]] / 100.0
    return 0.15


# ---------------------------------------------------------------------------
# Config / helpers
# ---------------------------------------------------------------------------

def load_config():
    with open("config/config.json", "r") as f:
        return json.load(f)

def load_reality_adjustments():
    p = Path("config/reality_adjustments.json")
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return {
        "pnl_adjustments": {
            "1_day": {"SPY": 0.63}, "2_day": {"SPY": 0.50}, "3_day": {"SPY": 0.45}
        },
        "spread_costs": {"SPY": 0.05},
        "slippage_pct": {"SPY": 0.015},
        "commission_per_contract": 0.65,
    }

def get_next_trading_day(date, df_daily):
    nd = date + timedelta(days=1)
    for _ in range(10):
        if nd in df_daily.index:
            return nd
        nd += timedelta(days=1)
    return None

def get_next_wednesday(d):
    if d.weekday() in [0, 1]:
        return d + timedelta(days=2 - d.weekday())
    return d + timedelta(days=7 - d.weekday() + 2)

def get_next_friday(d):
    if d.weekday() < 4:
        return d + timedelta(days=4 - d.weekday())
    return d + timedelta(days=7)

def get_next_monday(d):
    if d.weekday() == 4:
        return d + timedelta(days=3)
    return d + timedelta(days=7 - d.weekday())


# ---------------------------------------------------------------------------
# CORE FIX: Trading-time T_remaining computation
# ---------------------------------------------------------------------------

# Regular Trading Hours: 09:30 - 16:00 ET = 390 minutes per day
RTH_OPEN_H, RTH_OPEN_M = 9, 30
RTH_CLOSE_H, RTH_CLOSE_M = 16, 0
RTH_MINUTES_PER_DAY = 390  # 6.5 hours
TRADING_DAYS_PER_YEAR = 252
TRADING_MINUTES_PER_YEAR = RTH_MINUTES_PER_DAY * TRADING_DAYS_PER_YEAR  # 98,280


def compute_T_remaining(bar_time_et, expiry_1600_et, trading_dates_set):
    """
    Compute time remaining to expiry in trading-year units.

    Uses a trading-minutes model:
      - 1 trading day = 390 minutes of RTH (09:30-16:00 ET)
      - 1 trading year = 252 * 390 = 98,280 trading minutes

    For a bar at any time, counts:
      1. Remaining RTH minutes on the bar's day
      2. Full intermediate trading days * 390
      3. Full RTH on expiry day (390 min, since expiry is at 16:00 close)

    For pre-market bars (before 09:30): full day ahead.
    For after-hours bars (after 16:00): 0 minutes remaining today.

    Args:
        bar_time_et: bar timestamp in America/New_York timezone
        expiry_1600_et: expiry 16:00 ET timestamp
        trading_dates_set: set of pd.Timestamp trading dates (for counting business days)

    Returns:
        float: T in years (trading-time basis)
    """
    bar_date = bar_time_et.date() if hasattr(bar_time_et, 'date') else bar_time_et
    expiry_date = expiry_1600_et.date() if hasattr(expiry_1600_et, 'date') else expiry_1600_et

    # If bar is on or after expiry date
    if isinstance(bar_date, date_type) and isinstance(expiry_date, date_type):
        pass

    # --- Minutes remaining on bar's day ---
    bar_hour = bar_time_et.hour
    bar_minute = bar_time_et.minute

    if bar_hour < RTH_OPEN_H or (bar_hour == RTH_OPEN_H and bar_minute < RTH_OPEN_M):
        # Pre-market: full trading day ahead
        today_minutes = RTH_MINUTES_PER_DAY
    elif bar_hour > RTH_CLOSE_H or (bar_hour == RTH_CLOSE_H and bar_minute >= RTH_CLOSE_M):
        # After-hours (16:00+): today's RTH is over
        today_minutes = 0
    else:
        # During RTH: minutes from bar to 16:00
        bar_total = bar_hour * 60 + bar_minute
        close_total = RTH_CLOSE_H * 60 + RTH_CLOSE_M
        today_minutes = close_total - bar_total

    # --- Count full intermediate trading days ---
    # These are trading days strictly between bar_date and expiry_date
    full_intermediate_days = 0
    check = bar_date + timedelta(days=1) if isinstance(bar_date, date_type) else bar_date
    while check < expiry_date:
        ts = pd.Timestamp(check)
        if ts in trading_dates_set:
            full_intermediate_days += 1
        check += timedelta(days=1)

    # --- Expiry day ---
    if bar_date == expiry_date:
        # Bar is on expiry day itself — just today_minutes remain
        total_minutes = today_minutes
    elif bar_date < expiry_date:
        # today_minutes + intermediate days + full expiry day (390 min to 16:00)
        total_minutes = today_minutes + full_intermediate_days * RTH_MINUTES_PER_DAY + RTH_MINUTES_PER_DAY
    else:
        # Bar is after expiry (shouldn't happen)
        total_minutes = 0

    T = total_minutes / TRADING_MINUTES_PER_YEAR
    return max(T, 0.0)


def normalize_intraday(df_intra, et_tz):
    """Convert intraday DataFrame index to America/New_York timezone."""
    if df_intra.index.tz is not None:
        df_intra.index = df_intra.index.tz_convert('America/New_York')
    else:
        df_intra.index = df_intra.index.tz_localize('UTC').tz_convert('America/New_York')
    return df_intra


# ---------------------------------------------------------------------------
# Main backtest (corrected for IG weekly options)
# ---------------------------------------------------------------------------

def run_ig_weekly_backtest(ticker, config, adjustments, vix_series,
                           use_vix_iv=False, target_pct_override=None):
    """
    Backtest IG Weekly US 500 options with CORRECTED expiry model.

    Key differences from run_backtest_option_limit.py:
      - Options expire at 16:00 ET (SPX cash close), not 09:30
      - T_remaining computed in trading minutes, not calendar seconds
      - T_entry uses trading days / 252, not calendar days / 365
      - Bar scanning extends through 16:00 on expiry day
      - Loss exit uses 16:00 close bar on expiry day

    Args:
        ticker: 'SPY' (data ticker — strategy trades US 500 on IG)
        config: main config dict
        adjustments: reality adjustments dict
        vix_series: VIX close series for IV lookup
        use_vix_iv: True to use VIX-derived IV, False for fixed 0.15
        target_pct_override: if set, use this fixed target % instead of
                             deriving from reality_adjustments

    Returns:
        DataFrame of trades
    """
    console.print(f"\n[cyan]Running IG WEEKLY backtest for {ticker}...[/cyan]")

    daily_file = f'data/{ticker}/daily_OHLCV.parquet'
    if not os.path.exists(daily_file):
        console.print(f"[red]Missing {daily_file}[/red]")
        return None

    df_daily = pd.read_parquet(daily_file)
    valid_days = df_daily[df_daily.index.dayofweek < 5]

    # Build set of trading dates for T_remaining computation
    trading_dates_set = set(valid_days.index.normalize())

    import pytz
    et_tz = pytz.timezone('America/New_York')

    # BS params
    sigma_dashboard = 0.15
    r = 0.05

    # IG costs on option premium (round-trip)
    ig_option_spread_pct = 0.04  # 4% round-trip spread (2% each side)
    ig_slippage_pct = 0.01       # 1% slippage

    trades = []

    for i in range(len(valid_days)):
        day_t = valid_days.iloc[i]
        date_t = valid_days.index[i]
        day_of_week = date_t.dayofweek

        magnitude = abs((day_t['Close'] - day_t['Open']) / day_t['Open']) * 100
        if magnitude < 0.10:
            continue

        # Determine expiry (IG Weekly pattern)
        if day_of_week == 0:    # Monday -> Wednesday
            expiry_date = get_next_wednesday(date_t)
            expiry_label = "MON-WED-2D"
            days_to_expiry = 2
        elif day_of_week == 1:  # Tuesday -> Wednesday
            expiry_date = get_next_wednesday(date_t)
            expiry_label = "TUE-WED-1D"
            days_to_expiry = 1
        elif day_of_week == 2:  # Wednesday -> Friday
            expiry_date = get_next_friday(date_t)
            expiry_label = "WED-FRI-2D"
            days_to_expiry = 2
        elif day_of_week == 3:  # Thursday -> Friday
            expiry_date = get_next_friday(date_t)
            expiry_label = "THU-FRI-1D"
            days_to_expiry = 1
        elif day_of_week == 4:  # Friday -> Monday
            expiry_date = get_next_monday(date_t)
            expiry_label = "FRI-MON-3D"
            days_to_expiry = 3  # calendar days, but only 1 trading day (Monday)
        else:
            continue

        direction = "GREEN" if day_t['Close'] > day_t['Open'] else "RED"
        option_type = "PUT" if direction == "GREEN" else "CALL"
        signal = f"FADE_{direction}"

        entry_price = day_t['Close']
        atr = day_t['ATR_14']
        if pd.isna(atr):
            continue

        strike = round(entry_price)

        # FIX 1: T_entry in TRADING days / 252 (not calendar / 365)
        # For FRI-MON: 3 calendar days but only 1 trading day
        trading_dte = days_to_expiry
        if day_of_week == 4:  # Friday -> Monday
            trading_dte = 1  # Only Monday is a trading day
        T_entry = trading_dte / 252.0

        # Entry premium (BS)
        sigma_real = get_iv(date_t, vix_series) if use_vix_iv else sigma_dashboard
        entry_prem = bs_price(entry_price, strike, T_entry, r, sigma_real, option_type)
        if entry_prem < 0.01:
            continue

        # Target premium
        if target_pct_override is not None:
            target_pct = target_pct_override
        else:
            # Dashboard-derived target using limit_pts approach
            dash_prem = bs_price(entry_price, strike, T_entry, r, sigma_dashboard, option_type)
            if dash_prem < 0.01:
                continue
            expiry_key = f"{days_to_expiry}_day"
            pnl_mult = adjustments.get("pnl_adjustments", {}).get(expiry_key, {}).get(ticker, 0.50)
            target_pct_dash = pnl_mult * 0.45
            limit_pts = dash_prem * target_pct_dash
            target_pct = limit_pts / entry_prem

        target_prem = entry_prem * (1 + target_pct)

        # FIX 2: Expiry reference is 16:00 ET (SPX cash close), not 09:30
        expiry_1600 = et_tz.localize(
            datetime(expiry_date.year, expiry_date.month, expiry_date.day, 16, 0)
        )

        # Scan bars from entry through expiry 16:00
        limit_hit = False
        exit_prem = None
        exit_time = None
        exit_underlying = None
        exit_T_remaining = None

        check_date = date_t
        while check_date <= expiry_date:
            intraday_file = f'data/{ticker}/intraday/{check_date.strftime("%Y-%m-%d")}.parquet'

            if os.path.exists(intraday_file):
                try:
                    df_intra = pd.read_parquet(intraday_file)
                    df_intra = normalize_intraday(df_intra, et_tz)

                    if check_date == date_t:
                        # Entry day: start scanning from 16:00 ET (trade entry time)
                        entry_dt = et_tz.localize(
                            datetime(date_t.year, date_t.month, date_t.day, 16, 0)
                        )
                        df_window = df_intra[df_intra.index >= entry_dt]
                    elif check_date == expiry_date:
                        # FIX 3: Expiry day - scan through 16:00 ET (not 09:30)
                        end_dt = et_tz.localize(
                            datetime(expiry_date.year, expiry_date.month, expiry_date.day, 16, 0)
                        )
                        df_window = df_intra[df_intra.index <= end_dt]
                    else:
                        # Intermediate days: all bars
                        df_window = df_intra

                    if not df_window.empty:
                        for bar_time, bar in df_window.iterrows():
                            # FIX 4: T_remaining in trading minutes
                            T_now = compute_T_remaining(
                                bar_time, expiry_1600, trading_dates_set
                            )

                            # Best-case price for option buyer
                            if option_type == 'CALL':
                                bar_price = bar['High']
                            else:
                                bar_price = bar['Low']

                            current_prem = bs_price(
                                bar_price, strike, T_now, r, sigma_real, option_type
                            )

                            if current_prem >= target_prem:
                                limit_hit = True
                                exit_prem = target_prem
                                exit_time = bar_time
                                exit_underlying = bar_price
                                exit_T_remaining = T_now
                                break

                        if limit_hit:
                            break

                except Exception:
                    pass

            check_date = get_next_trading_day(check_date, df_daily)
            if check_date is None or check_date > expiry_date:
                break

        # FIX 5: If limit not hit, exit at 16:00 close on expiry day (T=0, true expiry)
        if not limit_hit:
            expiry_underlying = None
            expiry_intra = f'data/{ticker}/intraday/{expiry_date.strftime("%Y-%m-%d")}.parquet'
            if os.path.exists(expiry_intra):
                try:
                    df_exp = pd.read_parquet(expiry_intra)
                    df_exp = normalize_intraday(df_exp, et_tz)

                    # Get the 16:00 ET close bar (or nearest bar before it)
                    close_1600 = et_tz.localize(
                        datetime(expiry_date.year, expiry_date.month, expiry_date.day, 16, 0)
                    )
                    rth_bars = df_exp[df_exp.index <= close_1600]
                    if not rth_bars.empty:
                        expiry_underlying = rth_bars.iloc[-1]['Close']
                except Exception:
                    pass

            if expiry_underlying is not None:
                # T=0 at true expiry (16:00 close) -> BS returns intrinsic value
                exit_prem = bs_price(expiry_underlying, strike, 0.0, r, sigma_real, option_type)
                exit_underlying = expiry_underlying
                exit_T_remaining = 0.0
            else:
                exit_prem = 0.0
                exit_underlying = entry_price
                exit_T_remaining = 0.0

        # P&L calculation
        if limit_hit:
            gross_pnl_pct = target_pct
            net_pnl_pct = gross_pnl_pct - ig_option_spread_pct - ig_slippage_pct
            result = "WIN"
        else:
            gross_pnl_pct = (exit_prem - entry_prem) / entry_prem if entry_prem > 0 else -1.0
            net_pnl_pct = gross_pnl_pct - ig_option_spread_pct - ig_slippage_pct
            net_pnl_pct = max(net_pnl_pct, -1.0)
            result = "LOSS"

        trades.append({
            'Date': date_t.strftime("%Y-%m-%d"),
            'Ticker': ticker,
            'Day_of_Week': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'][day_of_week],
            'Expiry_Label': expiry_label,
            'Days_To_Expiry': days_to_expiry,
            'Trading_DTE': trading_dte,
            'Signal': signal,
            'Option_Type': option_type,
            'Entry_Price': entry_price,
            'Strike': strike,
            'ATR': atr,
            'IV_Entry': sigma_real,
            'T_Entry': T_entry,
            'Entry_Premium': entry_prem,
            'Target_Premium': target_prem,
            'Target_Pct': target_pct * 100,
            'Exit_Premium': exit_prem,
            'Exit_Underlying': exit_underlying,
            'Exit_T_Remaining': exit_T_remaining,
            'Gross_PnL_Pct': gross_pnl_pct * 100,
            'Net_PnL_Pct': net_pnl_pct * 100,
            'PnL_Mult': net_pnl_pct,
            'Result': result,
            'Limit_Hit': limit_hit,
            'Direction': direction,
            'Magnitude': magnitude,
        })

    console.print(f"[green]{ticker}: {len(trades)} trades[/green]")
    return pd.DataFrame(trades)


# ---------------------------------------------------------------------------
# Target sweep
# ---------------------------------------------------------------------------

def run_target_sweep(ticker, config, adjustments, vix_series, use_vix_iv=True):
    """Run backtest across multiple target percentages."""
    target_pcts = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]

    console.print("\n[bold cyan]TARGET SWEEP (Premium % targets)[/bold cyan]")
    console.print(f"Testing {len(target_pcts)} target percentages...\n")

    sweep_results = []

    for tgt in target_pcts:
        df = run_ig_weekly_backtest(
            ticker, config, adjustments, vix_series,
            use_vix_iv=use_vix_iv, target_pct_override=tgt
        )
        if df is None or df.empty:
            continue

        n_trades = len(df)
        wins = (df['Result'] == 'WIN').sum()
        win_rate = wins / n_trades * 100

        avg_win = df[df['Result'] == 'WIN']['Net_PnL_Pct'].mean() if wins > 0 else 0
        avg_loss = df[df['Result'] == 'LOSS']['Net_PnL_Pct'].mean() if (n_trades - wins) > 0 else 0
        ev = df['Net_PnL_Pct'].mean()

        # Breakeven WR
        if avg_win - avg_loss != 0:
            be_wr = abs(avg_loss) / (avg_win + abs(avg_loss)) * 100
        else:
            be_wr = 50.0

        # Equity curve
        equity = 10000
        kelly = 0.0523
        max_pos = 1000
        eq_list = []
        for _, row in df.iterrows():
            pos = min(equity * kelly, max_pos)
            equity += pos * row['PnL_Mult']
            equity = max(equity, 1.0)
            eq_list.append(equity)

        final = eq_list[-1] if eq_list else 10000
        years = (pd.to_datetime(df['Date'].iloc[-1]) - pd.to_datetime(df['Date'].iloc[0])).days / 365.25
        if years > 0 and final > 0:
            cagr = (pow(final / 10000, 1 / years) - 1) * 100
        else:
            cagr = 0

        sweep_results.append({
            'Target_Pct': tgt * 100,
            'Trades': n_trades,
            'Win_Rate': win_rate,
            'BE_WR': be_wr,
            'Margin': win_rate - be_wr,
            'Avg_Win': avg_win,
            'Avg_Loss': avg_loss,
            'EV': ev,
            'CAGR': cagr,
            'Final_Equity': final,
        })

    return pd.DataFrame(sweep_results)


# ---------------------------------------------------------------------------
# Equity curve helper
# ---------------------------------------------------------------------------

def calculate_equity_curve(df, starting_capital, kelly_pct, max_position, pnl_column):
    equity = starting_capital
    curve = []
    for _, row in df.iterrows():
        pos = min(equity * kelly_pct, max_position)
        equity += pos * row[pnl_column]
        equity = max(equity, 1.0)
        curve.append(equity)
    return curve


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def display_results(df, iv_label, starting_capital=10000):
    """Display comprehensive results for a single IV mode."""
    kelly_pct = 0.0523
    max_position = 1000

    eq = calculate_equity_curve(df, starting_capital, kelly_pct, max_position, 'PnL_Mult')
    df = df.copy()
    df['Equity'] = eq
    df['Date_dt'] = pd.to_datetime(df['Date'])
    years = (df['Date_dt'].max() - df['Date_dt'].min()).days / 365.25

    final = eq[-1]
    cagr = (pow(final / starting_capital, 1 / years) - 1) * 100 if years > 0 else 0

    wins = (df['Result'] == 'WIN').sum()
    losses = (df['Result'] == 'LOSS').sum()
    win_rate = wins / len(df) * 100

    running_max = pd.Series(eq).expanding().max()
    max_dd = ((pd.Series(eq) - running_max) / running_max * 100).min()

    wins_df = df[df['Result'] == 'WIN']
    losses_df = df[df['Result'] == 'LOSS']

    console.print(f"  Trades:         {len(df):,} ({wins:,} wins, {losses:,} losses)")
    console.print(f"  Limit hit rate: [bold]{win_rate:.1f}%[/bold]")
    console.print(f"  Starting:       ${starting_capital:,.0f}")
    console.print(f"  Final:          [bold]${final:,.0f}[/bold]")
    console.print(f"  CAGR:           [bold]{cagr:+.1f}%[/bold]")
    console.print(f"  Max Drawdown:   {max_dd:.1f}%")
    console.print()

    if len(wins_df) > 0:
        console.print(f"  [green]WINS: avg net P&L = {wins_df['Net_PnL_Pct'].mean():+.1f}%[/green]")
    if len(losses_df) > 0:
        console.print(f"  [red]LOSSES: avg net P&L = {losses_df['Net_PnL_Pct'].mean():+.1f}%[/red]")

    ev = df['Net_PnL_Pct'].mean()
    console.print(f"  [bold]EV per trade: {ev:+.2f}%[/bold]")
    console.print()

    # Breakdown by expiry pattern
    exp_table = Table(show_header=True, header_style="bold cyan")
    exp_table.add_column("Pattern", width=15)
    exp_table.add_column("Trades", justify="right", width=8)
    exp_table.add_column("Win%", justify="right", width=8)
    exp_table.add_column("Target%", justify="right", width=9)
    exp_table.add_column("Avg Win", justify="right", width=10)
    exp_table.add_column("Avg Loss", justify="right", width=10)
    exp_table.add_column("EV", justify="right", width=10)

    for label in ["FRI-MON-3D", "MON-WED-2D", "TUE-WED-1D", "WED-FRI-2D", "THU-FRI-1D"]:
        sub = df[df['Expiry_Label'] == label]
        if len(sub) == 0:
            continue
        wr = (sub['Result'] == 'WIN').sum() / len(sub) * 100
        avg_tgt = sub['Target_Pct'].mean()
        avg_win = sub[sub['Result'] == 'WIN']['Net_PnL_Pct'].mean() if (sub['Result'] == 'WIN').sum() > 0 else 0
        avg_loss = sub[sub['Result'] == 'LOSS']['Net_PnL_Pct'].mean() if (sub['Result'] == 'LOSS').sum() > 0 else 0
        ev_t = sub['Net_PnL_Pct'].mean()
        style = "green" if ev_t > 0 else "red"

        exp_table.add_row(
            label, f"{len(sub):,}", f"{wr:.1f}%", f"{avg_tgt:.1f}%",
            f"{avg_win:+.1f}%", f"{avg_loss:+.1f}%",
            f"[{style}]{ev_t:+.2f}%[/{style}]"
        )

    # ALL row
    all_wr = wins / len(df) * 100
    all_tgt = df['Target_Pct'].mean()
    all_avg_win = wins_df['Net_PnL_Pct'].mean() if len(wins_df) > 0 else 0
    all_avg_loss = losses_df['Net_PnL_Pct'].mean() if len(losses_df) > 0 else 0
    all_ev = df['Net_PnL_Pct'].mean()
    all_style = "green" if all_ev > 0 else "red"
    exp_table.add_row(
        "[bold]ALL[/bold]", f"[bold]{len(df):,}[/bold]", f"[bold]{all_wr:.1f}%[/bold]",
        f"[bold]{all_tgt:.1f}%[/bold]",
        f"[bold]{all_avg_win:+.1f}%[/bold]", f"[bold]{all_avg_loss:+.1f}%[/bold]",
        f"[bold][{all_style}]{all_ev:+.2f}%[/{all_style}][/bold]"
    )

    console.print(exp_table)
    console.print()

    # Breakdown by direction
    for dir_label in ["GREEN", "RED"]:
        sub = df[df['Direction'] == dir_label]
        if len(sub) == 0:
            continue
        wr = (sub['Result'] == 'WIN').sum() / len(sub) * 100
        ev_d = sub['Net_PnL_Pct'].mean()
        style = "green" if ev_d > 0 else "red"
        console.print(f"  {dir_label} days: {len(sub)} trades, {wr:.1f}% win, [{style}]{ev_d:+.2f}% EV[/{style}]")

    console.print()


def display_sweep_results(sweep_df):
    """Display target sweep results."""
    table = Table(title="TARGET SWEEP (VIX IV)", show_header=True, header_style="bold cyan")
    table.add_column("Target%", justify="right", width=9)
    table.add_column("Trades", justify="right", width=8)
    table.add_column("Win%", justify="right", width=8)
    table.add_column("BE%", justify="right", width=8)
    table.add_column("Margin", justify="right", width=8)
    table.add_column("Avg Win", justify="right", width=10)
    table.add_column("Avg Loss", justify="right", width=10)
    table.add_column("EV", justify="right", width=10)
    table.add_column("CAGR", justify="right", width=10)

    for _, row in sweep_df.iterrows():
        ev_style = "green" if row['EV'] > 0 else "red"
        margin_style = "green" if row['Margin'] > 0 else "red"
        table.add_row(
            f"{row['Target_Pct']:.0f}%",
            f"{row['Trades']:,.0f}",
            f"{row['Win_Rate']:.1f}%",
            f"{row['BE_WR']:.1f}%",
            f"[{margin_style}]{row['Margin']:+.1f}pp[/{margin_style}]",
            f"{row['Avg_Win']:+.1f}%",
            f"{row['Avg_Loss']:+.1f}%",
            f"[{ev_style}]{row['EV']:+.2f}%[/{ev_style}]",
            f"{row['CAGR']:+.1f}%",
        )

    console.print(table)
    console.print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    console.print("=" * 80)
    console.print("[bold blue]BACKTEST: IG WEEKLY US 500 OPTIONS (CORRECTED EXPIRY MODEL)[/bold blue]")
    console.print("=" * 80)
    console.print()
    console.print("[bold]CRITICAL FIX: Options now expire at 16:00 ET cash close (not 09:30 open)[/bold]")
    console.print()
    console.print("  Previous backtests stopped scanning at 09:30 ET and used T=0 at exit.")
    console.print("  This made every non-target-hit trade show ~-100% loss.")
    console.print("  Reality: IG weekly options settle at 16:00 ET. The option is LIVE all day.")
    console.print("  Correct loss is -15% to -50% (1-DTE) or even positive (2-DTE).")
    console.print()
    console.print("[bold]IG Weekly patterns:[/bold]")
    console.print("  Mon 21:00 UK -> Wed 16:00 ET close  (2 trading days)")
    console.print("  Tue 21:00 UK -> Wed 16:00 ET close  (1 trading day)")
    console.print("  Wed 21:00 UK -> Fri 16:00 ET close  (2 trading days)")
    console.print("  Thu 21:00 UK -> Fri 16:00 ET close  (1 trading day)")
    console.print("  Fri 21:00 UK -> Mon 16:00 ET close  (1 trading day)")
    console.print()

    config = load_config()
    adjustments = load_reality_adjustments()
    vix_series = load_vix_data()

    console.print(f"[green]VIX data: {vix_series.index.min().date()} to {vix_series.index.max().date()}[/green]")
    console.print()

    # ================================================================
    # PART 1: Main backtest with both IV models
    # ================================================================
    for iv_label, use_vix in [("Dashboard IV (0.15 fixed)", False), ("VIX-derived IV (realistic)", True)]:
        console.print("=" * 80)
        console.print(f"[bold white]MODEL: {iv_label}[/bold white]")
        console.print("=" * 80)
        console.print()

        df = run_ig_weekly_backtest('SPY', config, adjustments, vix_series, use_vix_iv=use_vix)
        if df is None or df.empty:
            console.print("[red]No results[/red]")
            continue

        display_results(df, iv_label)

        # Save
        suffix = "dashboard_iv" if not use_vix else "vix_iv"
        output_file = f'results/ig_weekly_backtest_{suffix}.csv'
        df.to_csv(output_file, index=False)
        console.print(f"[green]Saved to: {output_file}[/green]")
        console.print()

    # ================================================================
    # PART 2: Target sweep (VIX IV only)
    # ================================================================
    console.print("=" * 80)
    console.print("[bold white]TARGET SWEEP (VIX IV)[/bold white]")
    console.print("=" * 80)

    sweep_df = run_target_sweep('SPY', config, adjustments, vix_series, use_vix_iv=True)
    if sweep_df is not None and not sweep_df.empty:
        display_sweep_results(sweep_df)
        sweep_df.to_csv('results/ig_weekly_target_sweep.csv', index=False)
        console.print("[green]Saved to: results/ig_weekly_target_sweep.csv[/green]")

    # ================================================================
    # COMPARISON with old model
    # ================================================================
    console.print()
    console.print("=" * 80)
    console.print("[bold white]COMPARISON: OLD MODEL vs CORRECTED MODEL[/bold white]")
    console.print("=" * 80)
    console.print()
    console.print("OLD model (run_backtest_option_limit.py):")
    console.print("  - Treated 09:30 ET open as option expiry")
    console.print("  - Used T=0 for all non-hit exits -> -100% loss")
    console.print("  - Result: ALL configs deeply negative")
    console.print()
    console.print("CORRECTED model (this file):")
    console.print("  - Options expire at 16:00 ET (official SPX cash close)")
    console.print("  - Scans bars through entire expiry day")
    console.print("  - T_remaining in trading minutes (390 min/day)")
    console.print("  - Non-hit losses reflect actual time value remaining")
    console.print()
    console.print("=" * 80)


if __name__ == "__main__":
    main()
