"""
Backtest: OVERNIGHT FADE — Step 5A: Worst-Side Pricing (Spread Penalty)

Tests whether 0.3% OTM advantage survives realistic spread costs.

Current model applies 5% flat cost on premium (4% spread + 1% slippage).
This is proportionally the same for cheap and expensive options.

Reality: IG quotes spreads in POINTS, not percentages. A fixed-point
spread hurts cheaper (OTM) options MORE in percentage terms.

New model:
  entry_price = BS_mid * (1 + spread_pct/2)    # pay the ask
  exit_price  = BS_mid * (1 - spread_pct/2)     # sell at the bid

We also test with a POINTS-BASED spread model:
  entry_price = BS_mid + half_spread_points      # pay the ask
  exit_price  = BS_mid - half_spread_points      # sell at bid

Two spread regimes:
  Mild:     0.5 points half-spread each side (1.0 pt round-trip)
  Stressed: 1.0 points half-spread each side (2.0 pt round-trip)

Run for ATM and 0.3% OTM only.
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

TZ_ET = pytz.timezone('America/New_York')
TZ_UTC = pytz.utc

# ---------------------------------------------------------------------------
# Black-Scholes (same as baseline)
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
# Helpers (same as baseline)
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
    if entry_dow == 0: return 2, "MON-WED"
    elif entry_dow == 1: return 1, "TUE-WED"
    elif entry_dow == 2: return 2, "WED-FRI"
    elif entry_dow == 3: return 1, "THU-FRI"
    else: return None, None

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

def compute_strike(entry_price, option_type, moneyness_pct):
    if moneyness_pct == 0:
        return round(entry_price)
    if option_type == 'CALL':
        k_raw = entry_price * (1 - moneyness_pct)
    else:
        k_raw = entry_price * (1 + moneyness_pct)
    return round(k_raw)


# ---------------------------------------------------------------------------
# Core backtest with spread penalty
# ---------------------------------------------------------------------------

def run_overnight_fade_spread(ticker, vix_series, moneyness_pct=0.0,
                               half_spread_points=0.0,
                               slippage_points=0.0,
                               direction_filter="RED"):
    """
    Run overnight fade with POINTS-BASED spread model.

    Instead of flat percentage cost, apply fixed-point spread:
      entry_cost = BS_mid + half_spread_points  (you pay ask)
      exit_proceeds = BS_mid - half_spread_points  (you sell at bid)
      slippage applied additionally as points on each side

    No separate ig_spread_pct or ig_slippage_pct — all friction in points.
    """
    daily_file = f'data/{ticker}/daily_OHLCV.parquet'
    if not os.path.exists(daily_file):
        console.print(f"[red]Missing {daily_file}[/red]")
        return None

    df_daily = pd.read_parquet(daily_file)
    valid_days = df_daily[df_daily.index.dayofweek < 5]
    trading_dates_set = set(valid_days.index.normalize())

    r = 0.05
    total_half_spread = half_spread_points + slippage_points  # total one-side friction

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

        if direction_filter and direction != direction_filter:
            continue

        option_type = "PUT" if direction == "GREEN" else "CALL"
        entry_price = day_t['Close']
        atr = day_t['ATR_14']
        if pd.isna(atr):
            continue

        strike = compute_strike(entry_price, option_type, moneyness_pct)

        # Timestamps
        entry_dt_et = TZ_ET.localize(datetime(date_t.year, date_t.month, date_t.day, 16, 0))
        next_td = get_next_trading_day(date_t, df_daily)
        if next_td is None:
            continue
        exit_dt_et = TZ_ET.localize(datetime(next_td.year, next_td.month, next_td.day, 9, 30))
        expiry_dt_et = TZ_ET.localize(datetime(expiry_date.year, expiry_date.month, expiry_date.day, 16, 0))

        sigma = get_iv(date_t, vix_series)

        # Entry: BS mid + spread (you pay ask)
        T_entry = compute_T_remaining(entry_dt_et, expiry_dt_et, trading_dates_set)
        entry_mid = bs_price(entry_price, strike, T_entry, r, sigma, option_type)
        entry_prem = entry_mid + total_half_spread  # you pay the ask

        if entry_prem < 0.01 or entry_mid < 0.01:
            continue

        # Exit: BS mid - spread (you sell at bid)
        exit_underlying = get_bar_price_at(ticker, exit_dt_et, df_daily)
        if exit_underlying is None:
            if next_td in df_daily.index:
                exit_underlying = df_daily.loc[next_td, 'Open']
            else:
                continue

        T_at_exit = compute_T_remaining(exit_dt_et, expiry_dt_et, trading_dates_set)
        exit_mid = bs_price(exit_underlying, strike, T_at_exit, r, sigma, option_type)
        exit_prem = max(exit_mid - total_half_spread, 0.0)  # you sell at bid, floor 0

        # P&L on premium
        gross_pnl_pct = (exit_prem - entry_prem) / entry_prem if entry_prem > 0 else 0
        # No additional % cost — all friction is already in the points spread
        net_pnl_pct = max(gross_pnl_pct, -1.0)

        result = "WIN" if net_pnl_pct > 0 else "LOSS"

        trades.append({
            'Date': date_t.strftime("%Y-%m-%d"),
            'Pattern': expiry_label,
            'Direction': direction,
            'Signal': f"FADE_{direction}",
            'Option_Type': option_type,
            'Entry_Price': round(entry_price, 2),
            'Exit_Underlying': round(exit_underlying, 2),
            'Strike': strike,
            'Moneyness_Pct': round(moneyness_pct * 100, 2),
            'IV': round(sigma, 4),
            'T_Entry': round(T_entry, 6),
            'T_At_Exit': round(T_at_exit, 6),
            'Entry_Mid': round(entry_mid, 4),
            'Exit_Mid': round(exit_mid, 4),
            'Entry_Premium': round(entry_prem, 4),  # ask (what you paid)
            'Exit_Premium': round(exit_prem, 4),     # bid (what you received)
            'Spread_Points': round(total_half_spread, 4),
            'Net_PnL_Pct': round(net_pnl_pct * 100, 2),
            'PnL_Mult': round(net_pnl_pct, 6),
            'Result': result,
        })

    return pd.DataFrame(trades)


# ---------------------------------------------------------------------------
# Sized equity curve (inline)
# ---------------------------------------------------------------------------

def run_sized_equity_curve(df, starting_equity=10000, risk_pct=0.01,
                           daily_cap_pct=0.02, weekly_cap_pct=0.07):
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
                'Date': row['Date'], 'Result': row['Result'],
                'Net_PnL_Pct': row['Net_PnL_Pct'],
                'Size': 0, 'Premium_Spent': 0.0, 'Dollar_PnL': 0.0,
                'Equity': equity, 'Peak_Equity': peak_equity,
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
            'Date': row['Date'], 'Result': row['Result'],
            'Net_PnL_Pct': row['Net_PnL_Pct'],
            'Size': size, 'Premium_Spent': actual_spent,
            'Dollar_PnL': dollar_pnl, 'Equity': equity,
            'Peak_Equity': peak_equity, 'Drawdown_Pct': dd_pct,
            'Skipped': False,
        })

    return pd.DataFrame(results)


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def compute_stats(df_trades, df_curve, label, starting_equity=10000):
    n_total = len(df_trades)
    if n_total == 0:
        return None

    wins = df_trades[df_trades['Result'] == 'WIN']
    losses = df_trades[df_trades['Result'] == 'LOSS']
    win_rate = len(wins) / n_total * 100
    ev = df_trades['Net_PnL_Pct'].mean()

    pnl = df_trades['Net_PnL_Pct']
    p25 = pnl.quantile(0.25)
    p50 = pnl.quantile(0.50)
    p75 = pnl.quantile(0.75)
    p95 = pnl.quantile(0.95)
    p99 = pnl.quantile(0.99)

    avg_premium = df_trades['Entry_Premium'].mean()

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

    avg_size = traded['Size'].mean() if n_traded > 0 else 0

    return {
        'label': label,
        'trades': n_total,
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
        'final_equity': final_eq,
    }


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def display_grid(stats_list, title):
    console.print()
    table = Table(title=title, show_header=True, header_style="bold cyan")
    table.add_column("Metric", width=22)
    for s in stats_list:
        table.add_column(s['label'], justify="right", width=16)

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
        ("Final equity", lambda s: f"${s['final_equity']:,.0f}"),
    ]

    for label, fn in rows:
        vals = [fn(s) for s in stats_list]
        table.add_row(label, *vals)

    console.print(table)
    console.print()


def main():
    console.print("=" * 100)
    console.print("[bold blue]STEP 5A: Worst-Side Pricing (Spread Penalty) -- ATM vs 0.3% OTM[/bold blue]")
    console.print("=" * 100)
    console.print()
    console.print("[bold]Cost model change:[/bold]")
    console.print("  OLD: flat 5% of premium (4% spread + 1% slippage)")
    console.print("  NEW: fixed POINTS spread applied to entry (ask) and exit (bid)")
    console.print()
    console.print("[bold]Spread regimes:[/bold]")
    console.print("  Baseline:  ~0.06 pts half-spread (original 5% flat cost equivalent)")
    console.print("  Mild:      0.10 pts half-spread each side (0.24 pt round-trip incl slippage)")
    console.print("             ~2x baseline cost. Conservative real-world estimate.")
    console.print("  Stressed:  0.20 pts half-spread each side (0.48 pt round-trip incl slippage)")
    console.print("             ~4x baseline cost. Pessimistic: wide spreads during vol spikes.")
    console.print()
    console.print("[bold]Why this matters:[/bold]")
    console.print("  Fixed-point spread hurts cheaper options MORE in percentage terms.")
    console.print("  OTM premium ~1.81. A 0.12pt half-spread = 6.6% per side.")
    console.print("  ATM premium ~2.35. A 0.12pt half-spread = 5.1% per side.")
    console.print("  If OTM advantage disappears under points-based spread, it was an artefact.")
    console.print()

    vix_series = load_vix_data()
    console.print(f"[green]VIX: {vix_series.index.min().date()} to {vix_series.index.max().date()}[/green]")
    console.print()

    # Grid: (moneyness, half_spread_points, slippage_points, label)
    #
    # Calibration: the original flat 5% cost on a 2.35 ATM premium = 0.12 pts round-trip
    # = 0.06 pts half-spread. That's the baseline cost level.
    #
    # For this test we want to stress-test with LARGER spreads than baseline,
    # because we want to know if OTM survives when spreads are wider than
    # what the flat 5% model assumed.
    #
    # Mild:     0.10 pts half-spread (0.20 round-trip = ~8-11% of premium)
    #           This is ~2x the original baseline cost. Conservative real-world estimate.
    # Stressed: 0.20 pts half-spread (0.40 round-trip = ~17-22% of premium)
    #           This is ~4x baseline. Pessimistic: would represent wide spreads
    #           during low liquidity or vol spikes.
    #
    configs = [
        # Mild spread (0.10 pt half-spread + 0.02 pt slippage each side)
        (0.0,    0.10, 0.02, "ATM mild"),
        (-0.003, 0.10, 0.02, "OTM mild"),
        # Stressed spread (0.20 pt half-spread + 0.04 pt slippage each side)
        (0.0,    0.20, 0.04, "ATM stressed"),
        (-0.003, 0.20, 0.04, "OTM stressed"),
    ]

    all_stats = []

    for moneyness_pct, half_spread, slippage, label in configs:
        total_one_side = half_spread + slippage
        console.print(f"[cyan]Running: {label} (moneyness={moneyness_pct:+.3f}, "
                       f"half_spread={half_spread:.2f}, slippage={slippage:.2f}, "
                       f"total one-side={total_one_side:.2f} pts)...[/cyan]")

        df_trades = run_overnight_fade_spread(
            'SPY', vix_series,
            moneyness_pct=moneyness_pct,
            half_spread_points=half_spread,
            slippage_points=slippage,
            direction_filter="RED",
        )

        if df_trades is None or df_trades.empty:
            console.print(f"[red]  No trades for {label}[/red]")
            continue

        console.print(f"  {len(df_trades)} trades")

        df_curve = run_sized_equity_curve(df_trades)
        stats = compute_stats(df_trades, df_curve, label)
        if stats:
            all_stats.append(stats)

    # Display mild
    mild_stats = [s for s in all_stats if 'mild' in s['label']]
    if mild_stats:
        display_grid(mild_stats, "Mild Spread (0.5pt + 0.1pt slippage each side)")

    # Display stressed
    stressed_stats = [s for s in all_stats if 'stressed' in s['label']]
    if stressed_stats:
        display_grid(stressed_stats, "Stressed Spread (1.0pt + 0.2pt slippage each side)")

    # Combined comparison
    if all_stats:
        display_grid(all_stats, "Full Comparison: All Spread Regimes")

    # Delta analysis
    console.print("=" * 100)
    console.print("[bold white]OTM vs ATM DELTA UNDER EACH SPREAD REGIME[/bold white]")
    console.print("=" * 100)
    console.print()

    for regime in ['mild', 'stressed']:
        atm = next((s for s in all_stats if s['label'] == f'ATM {regime}'), None)
        otm = next((s for s in all_stats if s['label'] == f'OTM {regime}'), None)
        if atm and otm:
            console.print(f"[bold]{regime.upper()} spread:[/bold]")
            ev_d = otm['ev'] - atm['ev']
            dd_d = otm['max_dd'] - atm['max_dd']
            p50_d = otm['p50'] - atm['p50']
            p95_d = otm['p95'] - atm['p95']
            p99_d = otm['p99'] - atm['p99']

            ev_s = "green" if ev_d > 0 else "red"
            p50_s = "green" if p50_d > 0 else "red"

            console.print(f"  EV delta (OTM - ATM):  [{ev_s}]{ev_d:+.2f}%[/{ev_s}]")
            console.print(f"  DD delta:              {dd_d:+.1f}%")
            console.print(f"  P50 delta:             [{p50_s}]{p50_d:+.1f}%[/{p50_s}]")
            console.print(f"  P95 delta:             {p95_d:+.1f}%")
            console.print(f"  P99 delta:             {p99_d:+.1f}%")

            # Spread cost as % of premium (half_spread + slippage each side, *2 for round-trip)
            rt_pts = (0.12 if regime == 'mild' else 0.24) * 2  # round-trip points
            atm_spread_pct = rt_pts / atm['avg_premium'] * 100
            otm_spread_pct = rt_pts / otm['avg_premium'] * 100
            console.print(f"  Spread as % of premium: ATM={atm_spread_pct:.1f}%, OTM={otm_spread_pct:.1f}%")
            console.print()

    # Verdict
    console.print("=" * 100)
    console.print("[bold white]VERDICT[/bold white]")
    console.print("=" * 100)
    console.print()

    mild_atm = next((s for s in all_stats if s['label'] == 'ATM mild'), None)
    mild_otm = next((s for s in all_stats if s['label'] == 'OTM mild'), None)
    stressed_atm = next((s for s in all_stats if s['label'] == 'ATM stressed'), None)
    stressed_otm = next((s for s in all_stats if s['label'] == 'OTM stressed'), None)

    if mild_otm and mild_atm:
        if mild_otm['ev'] > mild_atm['ev']:
            console.print("[green]MILD: OTM still beats ATM after points-based spread[/green]")
        else:
            console.print("[red]MILD: OTM advantage erased by points-based spread[/red]")

    if stressed_otm and stressed_atm:
        if stressed_otm['ev'] > stressed_atm['ev']:
            console.print("[green]STRESSED: OTM still beats ATM even under doubled spread[/green]")
        else:
            console.print("[red]STRESSED: OTM advantage erased under stressed spread[/red]")

    console.print()
    console.print("=" * 100)


if __name__ == "__main__":
    main()
