"""
Backtest: OVERNIGHT FADE — close-to-open option strategy on IG Weekly US 500

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
import pytz

console = Console()

# Timezones
TZ_ET = pytz.timezone('America/New_York')
TZ_UTC = pytz.utc
TZ_UK = pytz.timezone('Europe/London')


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

    Looks for the 1-min bar at or just before target_dt_et.

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

        # Bars at or before target time
        bars = df_intra[df_intra.index <= target_dt_et]
        if not bars.empty:
            return float(bars.iloc[-1]['Close'])

        # If no bars before target, try first bar after (tolerance 5 min)
        after = df_intra[df_intra.index <= target_dt_et + timedelta(minutes=5)]
        if not after.empty:
            return float(after.iloc[-1]['Close'])

    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Main backtest
# ---------------------------------------------------------------------------

def run_overnight_fade(ticker, vix_series, use_vix_iv=True):
    """
    Backtest: Overnight fade, close-to-open.

    For each valid trading day (Mon-Thu):
      1. ENTRY at 16:00 ET: buy ATM option, priced via BS
         - T_entry = trading time from 16:00 entry to contract expiry 16:00
         - After 16:00, today's RTH is over, so T_entry = intermediate_days + expiry_day
      2. EXIT at 09:30 ET next trading day: sell option at market
         - T_at_exit = trading time from 09:30 exit to contract expiry 16:00
         - Exit premium = BS(underlying_at_0930, strike, T_at_exit, r, IV)
      3. P&L = (exit_premium - entry_premium) / entry_premium - costs

    Returns:
        DataFrame of trades
    """
    daily_file = f'data/{ticker}/daily_OHLCV.parquet'
    if not os.path.exists(daily_file):
        console.print(f"[red]Missing {daily_file}[/red]")
        return None

    df_daily = pd.read_parquet(daily_file)
    valid_days = df_daily[df_daily.index.dayofweek < 5]
    trading_dates_set = set(valid_days.index.normalize())

    r = 0.05

    # IG costs on option premium
    ig_spread_pct = 0.04    # 4% round-trip (2% each side)
    ig_slippage_pct = 0.01  # 1% slippage

    trades = []

    for i in range(len(valid_days) - 1):  # -1 to ensure next day exists
        day_t = valid_days.iloc[i]
        date_t = valid_days.index[i]
        entry_dow = date_t.dayofweek

        # Skip Friday
        if entry_dow == 4:
            continue

        # Skip tiny moves
        magnitude = abs((day_t['Close'] - day_t['Open']) / day_t['Open']) * 100
        if magnitude < 0.10:
            continue

        # Expiry info
        expiry_offset, expiry_label = get_expiry_date(entry_dow)
        if expiry_offset is None:
            continue

        expiry_date = date_t + timedelta(days=expiry_offset)

        # Direction & signal
        direction = "GREEN" if day_t['Close'] > day_t['Open'] else "RED"
        option_type = "PUT" if direction == "GREEN" else "CALL"
        signal = f"FADE_{direction}"

        entry_price = day_t['Close']
        atr = day_t['ATR_14']
        if pd.isna(atr):
            continue

        strike = round(entry_price)

        # --- Timestamps (all in ET) ---
        # Entry: 16:00 ET on trade day
        entry_dt_et = TZ_ET.localize(
            datetime(date_t.year, date_t.month, date_t.day, 16, 0)
        )

        # Exit: 09:30 ET next trading day
        next_td = get_next_trading_day(date_t, df_daily)
        if next_td is None:
            continue
        exit_dt_et = TZ_ET.localize(
            datetime(next_td.year, next_td.month, next_td.day, 9, 30)
        )

        # Contract expiry: 16:00 ET on expiry date
        expiry_dt_et = TZ_ET.localize(
            datetime(expiry_date.year, expiry_date.month, expiry_date.day, 16, 0)
        )

        # UK display times
        entry_dt_uk = entry_dt_et.astimezone(TZ_UK)
        exit_dt_uk = exit_dt_et.astimezone(TZ_UK)

        # IV
        sigma = get_iv(date_t, vix_series) if use_vix_iv else 0.15

        # --- ENTRY PRICING ---
        # T_entry = trading time from 16:00 ET (entry) to expiry 16:00 ET
        # At 16:00, today's RTH is over. So T_entry only counts intermediate + expiry day.
        T_entry = compute_T_remaining(entry_dt_et, expiry_dt_et, trading_dates_set)

        entry_prem = bs_price(entry_price, strike, T_entry, r, sigma, option_type)
        if entry_prem < 0.01:
            continue

        # --- EXIT PRICING ---
        # Get underlying price at 09:30 ET next trading day
        exit_underlying = get_bar_price_at(ticker, exit_dt_et, df_daily)
        if exit_underlying is None:
            # Fallback: try daily open of next trading day
            if next_td in df_daily.index:
                exit_underlying = df_daily.loc[next_td, 'Open']
            else:
                continue

        # T_at_exit = trading time from 09:30 ET (exit) to expiry 16:00 ET
        T_at_exit = compute_T_remaining(exit_dt_et, expiry_dt_et, trading_dates_set)

        exit_prem = bs_price(exit_underlying, strike, T_at_exit, r, sigma, option_type)

        # --- P&L ---
        gross_pnl_pct = (exit_prem - entry_prem) / entry_prem if entry_prem > 0 else 0
        net_pnl_pct = gross_pnl_pct - ig_spread_pct - ig_slippage_pct
        net_pnl_pct = max(net_pnl_pct, -1.0)  # Cap at -100%

        result = "WIN" if net_pnl_pct > 0 else "LOSS"

        # DTE group
        if expiry_label in ("MON-WED", "WED-FRI"):
            dte_group = "2D-expiry"
        else:
            dte_group = "1D-expiry"

        trades.append({
            'Date': date_t.strftime("%Y-%m-%d"),
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
            'T_Entry': round(T_entry, 6),
            'T_At_Exit': round(T_at_exit, 6),
            'Entry_Premium': round(entry_prem, 4),
            'Exit_Premium': round(exit_prem, 4),
            'Gross_PnL_Pct': round(gross_pnl_pct * 100, 2),
            'Net_PnL_Pct': round(net_pnl_pct * 100, 2),
            'PnL_Mult': round(net_pnl_pct, 6),
            'Result': result,
            'Magnitude': round(magnitude, 2),
            'Entry_ET': entry_dt_et.strftime("%Y-%m-%d %H:%M ET"),
            'Exit_ET': exit_dt_et.strftime("%Y-%m-%d %H:%M ET"),
            'Entry_UK': entry_dt_uk.strftime("%Y-%m-%d %H:%M UK"),
            'Exit_UK': exit_dt_uk.strftime("%Y-%m-%d %H:%M UK"),
            'Expiry_ET': expiry_dt_et.strftime("%Y-%m-%d %H:%M ET"),
        })

    console.print(f"[green]{ticker}: {len(trades)} trades[/green]")
    return pd.DataFrame(trades)


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

    # Breakdown by direction x pattern
    dir_table = Table(show_header=True, header_style="bold cyan", title=f"{label} - Direction x Pattern")
    dir_table.add_column("Direction", width=10)
    dir_table.add_column("Pattern", width=12)
    dir_table.add_column("Trades", justify="right", width=8)
    dir_table.add_column("Win%", justify="right", width=8)
    dir_table.add_column("Avg Win", justify="right", width=9)
    dir_table.add_column("Avg Loss", justify="right", width=9)
    dir_table.add_column("EV", justify="right", width=10)

    for direction in ["RED", "GREEN"]:
        for pattern in ["MON-WED", "TUE-WED", "WED-FRI", "THU-FRI"]:
            sub = df[(df['Direction'] == direction) & (df['Pattern'] == pattern)]
            if len(sub) < 10:
                continue
            wr = (sub['Result'] == 'WIN').sum() / len(sub) * 100
            aw = sub[sub['Result'] == 'WIN']['Net_PnL_Pct'].mean() if (sub['Result'] == 'WIN').sum() > 0 else 0
            al = sub[sub['Result'] == 'LOSS']['Net_PnL_Pct'].mean() if (sub['Result'] == 'LOSS').sum() > 0 else 0
            ev_p = sub['Net_PnL_Pct'].mean()
            style = "green" if ev_p > 0 else "red"
            dir_table.add_row(
                direction, pattern, f"{len(sub):,}", f"{wr:.1f}%",
                f"{aw:+.1f}%", f"{al:+.1f}%",
                f"[{style}]{ev_p:+.2f}%[/{style}]",
            )

    console.print(dir_table)
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
            f"Prem: {row['Entry_Premium']:.2f} -> {row['Exit_Premium']:.2f} | "
            f"T_exit: {row['T_At_Exit']:.5f} | "
            f"[{style}]{row['Net_PnL_Pct']:+.1f}%[/{style}]"
        )
    console.print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    console.print("=" * 90)
    console.print("[bold blue]OVERNIGHT FADE: Close-to-Open Option Strategy[/bold blue]")
    console.print("=" * 90)
    console.print()
    console.print("[bold]Strategy:[/bold]")
    console.print("  Entry:  16:00 ET (21:00 UK) -- US cash close")
    console.print("  Exit:   09:30 ET (14:30 UK) -- next US cash open")
    console.print("  Skip:   Friday entries (no weekend risk)")
    console.print()
    console.print("[bold]Pricing:[/bold]")
    console.print("  exit_dt   = 09:30 ET next trading day (strategy)")
    console.print("  expiry_dt = contract settlement at 16:00 ET on expiry date (pricing)")
    console.print("  Exit premium = BS(underlying_at_0930, strike, T_to_expiry, r, IV)")
    console.print("  Option retains time value at exit because expiry is hours/days away")
    console.print()
    console.print("[bold]Patterns (Mon-Thu only):[/bold]")
    console.print("  MON -> exit TUE 09:30 -> contract expires WED 16:00 (T_rem ~1.5 td)")
    console.print("  TUE -> exit WED 09:30 -> contract expires WED 16:00 (T_rem ~0.5 td)")
    console.print("  WED -> exit THU 09:30 -> contract expires FRI 16:00 (T_rem ~1.5 td)")
    console.print("  THU -> exit FRI 09:30 -> contract expires FRI 16:00 (T_rem ~0.5 td)")
    console.print()

    vix_series = load_vix_data()
    console.print(f"[green]VIX: {vix_series.index.min().date()} to {vix_series.index.max().date()}[/green]")
    console.print()

    # Run with VIX IV
    console.print("=" * 90)
    console.print("[bold white]MODEL: VIX-derived IV[/bold white]")
    console.print("=" * 90)

    df_vix = run_overnight_fade('SPY', vix_series, use_vix_iv=True)
    if df_vix is not None and not df_vix.empty:
        display_sample_trades(df_vix)
        display_results(df_vix, "VIX IV")
        df_vix.to_csv('results/overnight_fade_vix_iv.csv', index=False)
        console.print(f"[green]Saved: results/overnight_fade_vix_iv.csv[/green]")

    # Run with fixed IV for comparison
    console.print()
    console.print("=" * 90)
    console.print("[bold white]MODEL: Fixed IV (0.15)[/bold white]")
    console.print("=" * 90)

    df_fixed = run_overnight_fade('SPY', vix_series, use_vix_iv=False)
    if df_fixed is not None and not df_fixed.empty:
        display_results(df_fixed, "Fixed IV (0.15)")
        df_fixed.to_csv('results/overnight_fade_fixed_iv.csv', index=False)
        console.print(f"[green]Saved: results/overnight_fade_fixed_iv.csv[/green]")

    # Final summary
    console.print()
    console.print("=" * 90)
    console.print("[bold white]KEY INSIGHT[/bold white]")
    console.print("=" * 90)
    console.print()
    console.print("This is a PURE overnight trade (close -> open).")
    console.print("The option is NOT held to expiry. It is sold at 09:30 ET.")
    console.print("At exit, the option still has TIME VALUE because the contract")
    console.print("doesn't settle until 16:00 ET on expiry day (hours or days away).")
    console.print()
    console.print("This means losses are NOT -100%. A flat overnight move typically")
    console.print("produces a loss of theta decay only (a few % of premium).")
    console.print()
    console.print("NOTE: Future experiments can test exit at 08:00, 10:00, 11:00 ET.")
    console.print()
    console.print("=" * 90)


if __name__ == "__main__":
    main()
