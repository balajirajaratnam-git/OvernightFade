"""
Backtest: OVERNIGHT FADE — Strike Selection Grid (Step 4)

Tests moneyness offsets on RED-only trades to find optimal strike.
All other parameters held fixed from Step 3 baseline.

Fixed:
  - Universe: RED-only, all RED patterns
  - Entry: 16:00 ET, Exit: 09:30 ET next day
  - IV: VIX-derived
  - Expiry: IG weekly Mon/Wed/Fri logic
  - Days: Mon-Thu only
  - Sizing: 1% premium budget, 2% daily cap, 7% weekly cap

Only change: Strike rule.
  ATM:       K = round(S)
  0.3% ITM:  K = round(S * (1 - 0.003))  for CALL (strike below spot)
  0.5% ITM:  K = round(S * (1 - 0.005))  for CALL
  0.3% OTM:  K = round(S * (1 + 0.003))  for CALL (strike above spot)

For RED days we buy CALLs. ITM call = strike below spot. OTM call = strike above spot.
"""
import sys
sys.path.insert(0, 'src')

import pandas as pd
import numpy as np
import math
import os
from datetime import datetime, timedelta
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

RTH_MINUTES_PER_DAY = 390
TRADING_MINUTES_PER_YEAR = 252 * RTH_MINUTES_PER_DAY


def get_next_trading_day(date, df_daily):
    nd = date + timedelta(days=1)
    for _ in range(10):
        if nd in df_daily.index:
            return nd
        nd += timedelta(days=1)
    return None


def get_expiry_date(entry_dow):
    if entry_dow == 0:
        return 2, "MON-WED"
    elif entry_dow == 1:
        return 1, "TUE-WED"
    elif entry_dow == 2:
        return 2, "WED-FRI"
    elif entry_dow == 3:
        return 1, "THU-FRI"
    else:
        return None, None


def compute_T_remaining(from_dt_et, expiry_1600_et, trading_dates_set):
    from_date = from_dt_et.date()
    expiry_date = expiry_1600_et.date()
    from_hour, from_min = from_dt_et.hour, from_dt_et.minute

    rth_open_total = 9 * 60 + 30
    rth_close_total = 16 * 60
    from_total = from_hour * 60 + from_min

    if from_total < rth_open_total:
        today_minutes = RTH_MINUTES_PER_DAY
    elif from_total >= rth_close_total:
        today_minutes = 0
    else:
        today_minutes = rth_close_total - from_total

    full_days = 0
    check = from_date + timedelta(days=1)
    while check < expiry_date:
        if pd.Timestamp(check) in trading_dates_set:
            full_days += 1
        check += timedelta(days=1)

    if from_date == expiry_date:
        total_minutes = today_minutes
    elif from_date < expiry_date:
        total_minutes = today_minutes + full_days * RTH_MINUTES_PER_DAY + RTH_MINUTES_PER_DAY
    else:
        total_minutes = 0

    return max(total_minutes / TRADING_MINUTES_PER_YEAR, 0.0)


def normalize_intraday(df_intra):
    if df_intra.index.tz is not None:
        df_intra.index = df_intra.index.tz_convert(TZ_ET)
    else:
        df_intra.index = df_intra.index.tz_localize(TZ_UTC).tz_convert(TZ_ET)
    return df_intra


def get_bar_price_at(ticker, target_dt_et, df_daily):
    target_date = target_dt_et.date()
    intraday_file = f'data/{ticker}/intraday/{target_date.strftime("%Y-%m-%d")}.parquet'
    if not os.path.exists(intraday_file):
        return None
    try:
        df_intra = pd.read_parquet(intraday_file)
        df_intra = normalize_intraday(df_intra)
        bars = df_intra[df_intra.index <= target_dt_et]
        if not bars.empty:
            return float(bars.iloc[-1]['Close'])
        after = df_intra[df_intra.index <= target_dt_et + timedelta(minutes=5)]
        if not after.empty:
            return float(after.iloc[-1]['Close'])
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Core backtest with parameterised strike
# ---------------------------------------------------------------------------

def compute_strike(entry_price, option_type, moneyness_pct):
    """
    Compute strike given moneyness offset.

    moneyness_pct > 0 means ITM:
      CALL ITM: strike below spot  -> K = S * (1 - moneyness_pct)
      PUT  ITM: strike above spot  -> K = S * (1 + moneyness_pct)

    moneyness_pct < 0 means OTM:
      CALL OTM: strike above spot  -> K = S * (1 - moneyness_pct) = S * (1 + |offset|)
      PUT  OTM: strike below spot  -> K = S * (1 + moneyness_pct) = S * (1 - |offset|)

    moneyness_pct == 0 means ATM.
    """
    if moneyness_pct == 0:
        return round(entry_price)

    if option_type == 'CALL':
        # ITM call = strike below spot
        k_raw = entry_price * (1 - moneyness_pct)
    else:
        # ITM put = strike above spot
        k_raw = entry_price * (1 + moneyness_pct)

    return round(k_raw)


def run_overnight_fade_strike(ticker, vix_series, moneyness_pct=0.0,
                               direction_filter="RED"):
    """
    Run overnight fade backtest with parameterised strike.

    Args:
        ticker: 'SPY' (data source)
        vix_series: VIX daily close series
        moneyness_pct: strike offset. 0=ATM, +0.003=0.3% ITM, -0.003=0.3% OTM
        direction_filter: "RED", "GREEN", or None for all

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
    ig_spread_pct = 0.04
    ig_slippage_pct = 0.01

    trades = []

    for i in range(len(valid_days) - 1):
        day_t = valid_days.iloc[i]
        date_t = valid_days.index[i]
        entry_dow = date_t.dayofweek

        if entry_dow == 4:
            continue

        magnitude = abs((day_t['Close'] - day_t['Open']) / day_t['Open']) * 100
        if magnitude < 0.10:
            continue

        expiry_offset, expiry_label = get_expiry_date(entry_dow)
        if expiry_offset is None:
            continue

        expiry_date = date_t + timedelta(days=expiry_offset)

        direction = "GREEN" if day_t['Close'] > day_t['Open'] else "RED"

        # Filter direction
        if direction_filter and direction != direction_filter:
            continue

        option_type = "PUT" if direction == "GREEN" else "CALL"
        signal = f"FADE_{direction}"

        entry_price = day_t['Close']
        atr = day_t['ATR_14']
        if pd.isna(atr):
            continue

        # --- STRIKE with moneyness offset ---
        strike = compute_strike(entry_price, option_type, moneyness_pct)

        # Timestamps
        entry_dt_et = TZ_ET.localize(
            datetime(date_t.year, date_t.month, date_t.day, 16, 0)
        )

        next_td = get_next_trading_day(date_t, df_daily)
        if next_td is None:
            continue
        exit_dt_et = TZ_ET.localize(
            datetime(next_td.year, next_td.month, next_td.day, 9, 30)
        )

        expiry_dt_et = TZ_ET.localize(
            datetime(expiry_date.year, expiry_date.month, expiry_date.day, 16, 0)
        )

        # IV
        sigma = get_iv(date_t, vix_series)

        # Entry pricing
        T_entry = compute_T_remaining(entry_dt_et, expiry_dt_et, trading_dates_set)
        entry_prem = bs_price(entry_price, strike, T_entry, r, sigma, option_type)
        if entry_prem < 0.01:
            continue

        # Exit pricing
        exit_underlying = get_bar_price_at(ticker, exit_dt_et, df_daily)
        if exit_underlying is None:
            if next_td in df_daily.index:
                exit_underlying = df_daily.loc[next_td, 'Open']
            else:
                continue

        T_at_exit = compute_T_remaining(exit_dt_et, expiry_dt_et, trading_dates_set)
        exit_prem = bs_price(exit_underlying, strike, T_at_exit, r, sigma, option_type)

        # P&L
        gross_pnl_pct = (exit_prem - entry_prem) / entry_prem if entry_prem > 0 else 0
        net_pnl_pct = gross_pnl_pct - ig_spread_pct - ig_slippage_pct
        net_pnl_pct = max(net_pnl_pct, -1.0)

        result = "WIN" if net_pnl_pct > 0 else "LOSS"

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
            'Moneyness_Pct': round(moneyness_pct * 100, 2),
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
        })

    return pd.DataFrame(trades)


# ---------------------------------------------------------------------------
# Sized equity curve (inline — same logic as run_backtest_overnight_fade_sized)
# ---------------------------------------------------------------------------

def run_sized_equity_curve(df, starting_equity=10000, risk_pct=0.01,
                           daily_cap_pct=0.02, weekly_cap_pct=0.07):
    """Run equity curve with premium-budget sizing on a trade DataFrame."""
    df = df.copy()
    df['Date_dt'] = pd.to_datetime(df['Date'])

    equity = starting_equity
    peak_equity = starting_equity

    results = []
    daily_spent = {}
    weekly_spent = {}

    for _, row in df.iterrows():
        trade_date = row['Date_dt'].date()
        iso_week = row['Date_dt'].isocalendar()[:2]
        entry_premium = row['Entry_Premium']

        if entry_premium <= 0:
            continue

        premium_budget = equity * risk_pct

        day_key = str(trade_date)
        already_spent_today = daily_spent.get(day_key, 0.0)
        daily_remaining = max(equity * daily_cap_pct - already_spent_today, 0.0)
        premium_budget = min(premium_budget, daily_remaining)

        week_key = f"{iso_week[0]}-W{iso_week[1]:02d}"
        already_spent_week = weekly_spent.get(week_key, 0.0)
        weekly_remaining = max(equity * weekly_cap_pct - already_spent_week, 0.0)
        premium_budget = min(premium_budget, weekly_remaining)

        if premium_budget < entry_premium:
            results.append({
                'Date': row['Date'],
                'Result': row['Result'],
                'Net_PnL_Pct': row['Net_PnL_Pct'],
                'Size': 0,
                'Premium_Spent': 0.0,
                'Dollar_PnL': 0.0,
                'Equity': equity,
                'Peak_Equity': peak_equity,
                'Drawdown_Pct': (equity - peak_equity) / peak_equity * 100,
                'Skipped': True,
            })
            continue

        size = math.floor(premium_budget / entry_premium)
        if size < 1:
            size = 1

        actual_spent = size * entry_premium

        daily_spent[day_key] = already_spent_today + actual_spent
        weekly_spent[week_key] = already_spent_week + actual_spent

        net_pnl_pct = row['PnL_Mult']
        dollar_pnl = actual_spent * net_pnl_pct

        equity += dollar_pnl
        equity = max(equity, 1.0)
        peak_equity = max(peak_equity, equity)
        dd_pct = (equity - peak_equity) / peak_equity * 100

        results.append({
            'Date': row['Date'],
            'Result': row['Result'],
            'Net_PnL_Pct': row['Net_PnL_Pct'],
            'Size': size,
            'Premium_Spent': actual_spent,
            'Dollar_PnL': dollar_pnl,
            'Equity': equity,
            'Peak_Equity': peak_equity,
            'Drawdown_Pct': dd_pct,
            'Skipped': False,
        })

    return pd.DataFrame(results)


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def compute_stats(df_trades, df_curve, label, starting_equity=10000):
    """Compute full stats for one moneyness level."""
    n_total = len(df_trades)
    if n_total == 0:
        return None

    # Raw trade stats (before sizing)
    wins = df_trades[df_trades['Result'] == 'WIN']
    losses = df_trades[df_trades['Result'] == 'LOSS']
    win_rate = len(wins) / n_total * 100
    ev = df_trades['Net_PnL_Pct'].mean()

    # Percentiles
    pnl = df_trades['Net_PnL_Pct']
    p25 = pnl.quantile(0.25)
    p50 = pnl.quantile(0.50)
    p75 = pnl.quantile(0.75)
    p95 = pnl.quantile(0.95)
    p99 = pnl.quantile(0.99)

    # Avg premium
    avg_premium = df_trades['Entry_Premium'].mean()

    # Sized equity curve stats
    traded = df_curve[~df_curve['Skipped']] if 'Skipped' in df_curve.columns else df_curve
    n_traded = len(traded)

    final_eq = df_curve['Equity'].iloc[-1] if len(df_curve) > 0 else starting_equity
    years = (pd.to_datetime(df_curve['Date'].iloc[-1]) - pd.to_datetime(df_curve['Date'].iloc[0])).days / 365.25 if len(df_curve) > 1 else 0

    if years > 0 and final_eq > starting_equity:
        cagr = (pow(final_eq / starting_equity, 1 / years) - 1) * 100
    elif years > 0:
        cagr = -((1 - pow(max(final_eq, 1) / starting_equity, 1 / years)) * 100)
    else:
        cagr = 0

    max_dd = df_curve['Drawdown_Pct'].min() if len(df_curve) > 0 else 0

    # Worst losing streak
    streak = 0
    worst_streak = 0
    for _, row in traded.iterrows():
        if row['Result'] == 'LOSS':
            streak += 1
            worst_streak = max(worst_streak, streak)
        else:
            streak = 0

    # Worst streak equity impact
    streak_impact = 0.0
    if worst_streak > 0 and len(traded) > 0:
        traded_reset = traded.reset_index(drop=True)
        s = 0
        ws_len = 0
        ws_end = 0
        for i, row in traded_reset.iterrows():
            if row['Result'] == 'LOSS':
                s += 1
                if s > ws_len:
                    ws_len = s
                    ws_end = i
            else:
                s = 0
        ws_start = ws_end - ws_len + 1
        if ws_start >= 0:
            eq_before = traded_reset.iloc[ws_start]['Equity'] + abs(traded_reset.iloc[ws_start]['Dollar_PnL'])
            eq_after = traded_reset.iloc[ws_end]['Equity']
            streak_impact = (eq_after - eq_before) / eq_before * 100 if eq_before > 0 else 0

    # Avg size
    avg_size = traded['Size'].mean() if n_traded > 0 else 0

    # Year-by-year EV
    df_trades_copy = df_trades.copy()
    df_trades_copy['Year'] = pd.to_datetime(df_trades_copy['Date']).dt.year
    yearly_ev = df_trades_copy.groupby('Year')['Net_PnL_Pct'].mean()

    return {
        'label': label,
        'trades': n_total,
        'trades_sized': n_traded,
        'win_rate': win_rate,
        'ev': ev,
        'cagr': cagr,
        'max_dd': max_dd,
        'p25': p25,
        'p50': p50,
        'p75': p75,
        'p95': p95,
        'p99': p99,
        'avg_premium': avg_premium,
        'avg_size': avg_size,
        'worst_streak': worst_streak,
        'streak_impact': streak_impact,
        'final_equity': final_eq,
        'yearly_ev': yearly_ev,
    }


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def display_grid(stats_list):
    """Display compact comparison table."""
    console.print()
    console.print("=" * 100)
    console.print("[bold blue]STEP 4A: Strike Selection Grid — RED-only, 1% Premium Budget[/bold blue]")
    console.print("=" * 100)
    console.print()

    table = Table(title="Strike Moneyness Comparison", show_header=True, header_style="bold cyan")
    table.add_column("Metric", width=22)
    for s in stats_list:
        table.add_column(s['label'], justify="right", width=14)

    rows = [
        ("Trades", lambda s: f"{s['trades']:,}"),
        ("Win rate", lambda s: f"{s['win_rate']:.1f}%"),
        ("EV per trade", lambda s: f"{s['ev']:+.2f}%"),
        ("CAGR (1% risk)", lambda s: f"{s['cagr']:+.1f}%"),
        ("Max DD", lambda s: f"{s['max_dd']:.1f}%"),
        ("P25", lambda s: f"{s['p25']:+.1f}%"),
        ("P50 (median)", lambda s: f"{s['p50']:+.1f}%"),
        ("P75", lambda s: f"{s['p75']:+.1f}%"),
        ("P95", lambda s: f"{s['p95']:+.1f}%"),
        ("P99", lambda s: f"{s['p99']:+.1f}%"),
        ("Avg premium", lambda s: f"{s['avg_premium']:.2f}"),
        ("Avg size (units)", lambda s: f"{s['avg_size']:.1f}"),
        ("Worst streak", lambda s: f"{s['worst_streak']}"),
        ("Streak impact", lambda s: f"{s['streak_impact']:+.1f}%"),
        ("Final equity", lambda s: f"${s['final_equity']:,.0f}"),
    ]

    for label, fn in rows:
        vals = [fn(s) for s in stats_list]
        table.add_row(label, *vals)

    console.print(table)
    console.print()

    # Year-by-year EV table
    console.print("[bold]Year-by-year EV per trade:[/bold]")
    yoy_table = Table(show_header=True, header_style="bold cyan")
    yoy_table.add_column("Year", width=8)
    for s in stats_list:
        yoy_table.add_column(s['label'], justify="right", width=14)

    # Get all years
    all_years = sorted(set().union(*[s['yearly_ev'].index.tolist() for s in stats_list]))
    for year in all_years:
        vals = []
        for s in stats_list:
            if year in s['yearly_ev'].index:
                ev_val = s['yearly_ev'].loc[year]
                style = "green" if ev_val > 0 else "red"
                vals.append(f"[{style}]{ev_val:+.2f}%[/{style}]")
            else:
                vals.append("-")
        yoy_table.add_row(str(year), *vals)

    console.print(yoy_table)
    console.print()


def main():
    console.print("=" * 100)
    console.print("[bold blue]OVERNIGHT FADE: Step 4 — Strike Selection Grid[/bold blue]")
    console.print("=" * 100)
    console.print()
    console.print("[bold]Fixed parameters:[/bold]")
    console.print("  Universe:  RED-only (buy CALL after RED day)")
    console.print("  Entry:     16:00 ET (21:00 UK)")
    console.print("  Exit:      09:30 ET next trading day")
    console.print("  IV:        VIX-derived")
    console.print("  Sizing:    1% premium budget, 2% daily cap, 7% weekly cap")
    console.print("  Days:      Mon-Thu only")
    console.print()
    console.print("[bold]Strike grid:[/bold]")
    console.print("  ATM:       K = round(S)")
    console.print("  0.3% ITM:  K = round(S * 0.997)  (CALL strike below spot)")
    console.print("  0.5% ITM:  K = round(S * 0.995)  (CALL strike further below)")
    console.print("  0.3% OTM:  K = round(S * 1.003)  (CALL strike above spot)")
    console.print()

    vix_series = load_vix_data()
    console.print(f"[green]VIX: {vix_series.index.min().date()} to {vix_series.index.max().date()}[/green]")
    console.print()

    # Define grid
    grid = [
        (0.0,    "ATM"),
        (0.003,  "0.3% ITM"),
        (0.005,  "0.5% ITM"),
        (-0.003, "0.3% OTM"),
    ]

    all_stats = []

    for moneyness_pct, label in grid:
        console.print(f"[cyan]Running: {label} (moneyness={moneyness_pct:+.3f})...[/cyan]")

        df_trades = run_overnight_fade_strike(
            'SPY', vix_series,
            moneyness_pct=moneyness_pct,
            direction_filter="RED",
        )

        if df_trades is None or df_trades.empty:
            console.print(f"[red]  No trades for {label}[/red]")
            continue

        console.print(f"  {len(df_trades)} trades generated")

        # Save trade log
        safe_label = label.replace(' ', '_').replace('%', 'pct').replace('.', 'p')
        trade_log_path = f'results/strike_grid_{safe_label}.csv'
        df_trades.to_csv(trade_log_path, index=False)

        # Run sized equity curve
        df_curve = run_sized_equity_curve(df_trades)

        stats = compute_stats(df_trades, df_curve, label)
        if stats:
            all_stats.append(stats)

    # Display comparison
    if all_stats:
        display_grid(all_stats)

    # Quick recommendation
    console.print("=" * 100)
    console.print("[bold white]QUICK ASSESSMENT[/bold white]")
    console.print("=" * 100)
    console.print()

    if len(all_stats) >= 2:
        atm = next((s for s in all_stats if s['label'] == 'ATM'), None)
        for s in all_stats:
            if s['label'] == 'ATM':
                continue
            if atm:
                ev_delta = s['ev'] - atm['ev']
                dd_delta = s['max_dd'] - atm['max_dd']
                p50_delta = s['p50'] - atm['p50']
                p95_delta = s['p95'] - atm['p95']
                p99_delta = s['p99'] - atm['p99']

                ev_style = "green" if ev_delta > 0 else "red"
                p50_style = "green" if p50_delta > 0 else "red"
                p95_style = "green" if p95_delta > 0 else ("red" if p95_delta < -5 else "yellow")
                p99_style = "green" if p99_delta > 0 else ("red" if p99_delta < -10 else "yellow")

                console.print(f"  {s['label']} vs ATM:")
                console.print(f"    EV delta:  [{ev_style}]{ev_delta:+.2f}%[/{ev_style}]")
                console.print(f"    DD delta:  {dd_delta:+.1f}%")
                console.print(f"    P50 delta: [{p50_style}]{p50_delta:+.1f}%[/{p50_style}]")
                console.print(f"    P95 delta: [{p95_style}]{p95_delta:+.1f}%[/{p95_style}]")
                console.print(f"    P99 delta: [{p99_style}]{p99_delta:+.1f}%[/{p99_style}]")

                # Check tail health
                if p99_delta < -10:
                    console.print(f"    [red bold]WARNING: P99 dropped hard (-{abs(p99_delta):.0f}%). Tail damage.[/red bold]")
                console.print()

    console.print("=" * 100)


if __name__ == "__main__":
    main()
