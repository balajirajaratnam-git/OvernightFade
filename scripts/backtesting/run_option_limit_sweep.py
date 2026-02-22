"""
Parameter sweep for option limit order strategy.

Searches for viable configurations by varying:
- Target % on option premium (5%, 10%, 15%, 20%, 25%, 30%, 40%, 50%)
- DTE filter (1-day, 2-day, 3-day, all)
- IV model (dashboard 0.15, VIX-derived)
- Day-of-week patterns

For each combo, computes:
- Limit hit rate (must be high enough for positive EV)
- Breakeven hit rate (where EV = 0)
- Actual EV per trade
- CAGR

Goal: find any config where hit_rate > breakeven_rate.
"""
import sys
sys.path.insert(0, 'src')

import pandas as pd
import numpy as np
import json
import os
import math
from datetime import datetime, timedelta
from pathlib import Path
from rich.console import Console
from rich.table import Table

console = Console()

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

def load_vix_data():
    cache_file = Path("data/vix_daily_cache.parquet")
    if cache_file.exists():
        df = pd.read_parquet(cache_file)
    else:
        import yfinance as yf
        vix = yf.download("^VIX", start="2016-01-01", end="2026-12-31", progress=False)
        df = vix[['Close']].copy()
        df.columns = ['VIX_Close']
        if hasattr(df.columns, 'levels'):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df.to_parquet(cache_file)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.iloc[:, 0]

def get_iv(date, vix_series):
    if vix_series is not None:
        lookup = pd.Timestamp(date).normalize()
        if lookup in vix_series.index:
            return vix_series.loc[lookup] / 100.0
        nearby = vix_series.index[abs(vix_series.index - lookup) <= pd.Timedelta(days=5)]
        if len(nearby) > 0:
            return vix_series.loc[nearby[abs(nearby - lookup).argmin()]] / 100.0
    return 0.15

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


def precompute_setups(ticker, vix_series):
    """Pre-compute all trade setups with bar data once."""
    console.print(f"[cyan]Pre-computing setups for {ticker}...[/cyan]")

    daily_file = f'data/{ticker}/daily_OHLCV.parquet'
    df_daily = pd.read_parquet(daily_file)
    valid_days = df_daily[df_daily.index.dayofweek < 5]

    import pytz
    et_tz = pytz.timezone('America/New_York')
    r = 0.05

    setups = []

    for i in range(len(valid_days)):
        day_t = valid_days.iloc[i]
        date_t = valid_days.index[i]
        day_of_week = date_t.dayofweek

        magnitude = abs((day_t['Close'] - day_t['Open']) / day_t['Open']) * 100
        if magnitude < 0.10:
            continue

        if day_of_week == 0:
            expiry_date = get_next_wednesday(date_t)
            expiry_label = "MON-WED-2D"
            dte = 2
        elif day_of_week == 1:
            expiry_date = get_next_wednesday(date_t)
            expiry_label = "TUE-WED-1D"
            dte = 1
        elif day_of_week == 2:
            expiry_date = get_next_friday(date_t)
            expiry_label = "WED-FRI-2D"
            dte = 2
        elif day_of_week == 3:
            expiry_date = get_next_friday(date_t)
            expiry_label = "THU-FRI-1D"
            dte = 1
        elif day_of_week == 4:
            expiry_date = get_next_monday(date_t)
            expiry_label = "FRI-MON-3D"
            dte = 3
        else:
            continue

        direction = "GREEN" if day_t['Close'] > day_t['Open'] else "RED"
        option_type = "PUT" if direction == "GREEN" else "CALL"

        entry_price = day_t['Close']
        atr = day_t['ATR_14']
        if pd.isna(atr):
            continue

        strike = round(entry_price)
        T_entry = dte / 365.0

        # Dashboard premium (sigma=0.15)
        dash_prem = bs_price(entry_price, strike, T_entry, r, 0.15, option_type)
        if dash_prem < 0.01:
            continue

        # Real IV premium
        real_iv = get_iv(date_t, vix_series)
        real_prem = bs_price(entry_price, strike, T_entry, r, real_iv, option_type)
        if real_prem < 0.01:
            continue

        # Expiry time
        expiry_930 = et_tz.localize(
            datetime(expiry_date.year, expiry_date.month, expiry_date.day, 9, 30)
        )

        # Collect bars with pre-computed BS premiums at different IVs
        bars = []
        check_date = date_t
        while check_date <= expiry_date:
            intraday_file = f'data/{ticker}/intraday/{check_date.strftime("%Y-%m-%d")}.parquet'
            if os.path.exists(intraday_file):
                try:
                    df_intra = pd.read_parquet(intraday_file)
                    if df_intra.index.tz is not None:
                        df_intra.index = df_intra.index.tz_convert('America/New_York')
                    else:
                        df_intra.index = df_intra.index.tz_localize('UTC').tz_convert('America/New_York')

                    if check_date == date_t:
                        entry_dt = et_tz.localize(datetime(date_t.year, date_t.month, date_t.day, 16, 0))
                        df_window = df_intra[df_intra.index >= entry_dt]
                    elif check_date == expiry_date:
                        end_dt = et_tz.localize(datetime(expiry_date.year, expiry_date.month, expiry_date.day, 9, 30))
                        df_window = df_intra[df_intra.index <= end_dt]
                    else:
                        df_window = df_intra

                    for bar_time, bar in df_window.iterrows():
                        remaining = (expiry_930 - bar_time).total_seconds()
                        T_now = max(remaining / (365.25 * 24 * 3600), 0.0)

                        # Best price for option buyer
                        if option_type == 'CALL':
                            best_price = bar['High']
                        else:
                            best_price = bar['Low']

                        # Pre-compute premium at dashboard IV and real IV
                        prem_dash = bs_price(best_price, strike, T_now, r, 0.15, option_type)
                        prem_real = bs_price(best_price, strike, T_now, r, real_iv, option_type)

                        bars.append({
                            'prem_dash': prem_dash,
                            'prem_real': prem_real,
                        })

                except Exception:
                    pass

            check_date = get_next_trading_day(check_date, df_daily)
            if check_date is None or check_date > expiry_date:
                break

        # Expiry underlying for loss P&L
        expiry_underlying = None
        expiry_intra = f'data/{ticker}/intraday/{expiry_date.strftime("%Y-%m-%d")}.parquet'
        if os.path.exists(expiry_intra):
            try:
                df_exp = pd.read_parquet(expiry_intra)
                if df_exp.index.tz is not None:
                    df_exp.index = df_exp.index.tz_convert('America/New_York')
                else:
                    df_exp.index = df_exp.index.tz_localize('UTC').tz_convert('America/New_York')
                end_dt = et_tz.localize(datetime(expiry_date.year, expiry_date.month, expiry_date.day, 9, 30))
                morning = df_exp[df_exp.index <= end_dt]
                if not morning.empty:
                    expiry_underlying = morning.iloc[-1]['Close']
            except Exception:
                pass

        expiry_prem_dash = 0.0
        expiry_prem_real = 0.0
        if expiry_underlying is not None:
            expiry_prem_dash = bs_price(expiry_underlying, strike, 0.0, r, 0.15, option_type)
            expiry_prem_real = bs_price(expiry_underlying, strike, 0.0, r, real_iv, option_type)

        setups.append({
            'dte': dte,
            'expiry_label': expiry_label,
            'day_of_week': day_of_week,
            'dash_prem': dash_prem,
            'real_prem': real_prem,
            'real_iv': real_iv,
            'bars': bars,
            'expiry_prem_dash': expiry_prem_dash,
            'expiry_prem_real': expiry_prem_real,
        })

    console.print(f"[green]{len(setups)} setups pre-computed[/green]")
    return setups


def evaluate_config(setups, target_pct, dte_filter, use_vix_iv, day_filter=None):
    """
    Evaluate a single configuration.

    target_pct: fraction (e.g., 0.10 for 10% profit target on option)
    dte_filter: None=all, or 1/2/3
    use_vix_iv: True=use VIX IV for premium evaluation, False=use 0.15
    day_filter: None=all, or list of day_of_week ints to include
    """
    ig_spread_pct = 0.04  # 4% round-trip on premium
    ig_slippage_pct = 0.01  # 1%

    pnls = []
    wins = 0
    total = 0

    for s in setups:
        if dte_filter is not None and s['dte'] != dte_filter:
            continue
        if day_filter is not None and s['day_of_week'] not in day_filter:
            continue

        total += 1

        # Entry premium and target
        if use_vix_iv:
            entry_prem = s['real_prem']
            prem_key = 'prem_real'
            expiry_prem = s['expiry_prem_real']
        else:
            entry_prem = s['dash_prem']
            prem_key = 'prem_dash'
            expiry_prem = s['expiry_prem_dash']

        # limit_pts is computed from dashboard premium * target_pct
        # This matches how auto_trade_ig.py works
        limit_pts = s['dash_prem'] * target_pct
        target_prem = entry_prem + limit_pts

        # Check bars
        hit = False
        for bar in s['bars']:
            if bar[prem_key] >= target_prem:
                hit = True
                break

        if hit:
            actual_gain_pct = limit_pts / entry_prem  # what % of entry you actually gain
            net_pnl = actual_gain_pct - ig_spread_pct - ig_slippage_pct
            wins += 1
        else:
            gross_loss = (expiry_prem - entry_prem) / entry_prem if entry_prem > 0 else -1.0
            net_pnl = max(gross_loss - ig_spread_pct - ig_slippage_pct, -1.0)

        pnls.append(net_pnl)

    if total == 0:
        return None

    pnls = np.array(pnls)
    hit_rate = wins / total
    avg_win = pnls[pnls > -0.5].mean() if (pnls > -0.5).sum() > 0 else 0  # approximate
    avg_loss = pnls[pnls <= -0.5].mean() if (pnls <= -0.5).sum() > 0 else -1.0

    # Breakeven hit rate: win_rate * avg_win + (1-win_rate) * avg_loss = 0
    # => win_rate = -avg_loss / (avg_win - avg_loss)
    if avg_win > avg_loss and avg_win != 0:
        breakeven_rate = -avg_loss / (avg_win - avg_loss)
    else:
        breakeven_rate = 1.0

    ev = pnls.mean()

    # CAGR
    equity = 10000
    kelly = 0.0523
    max_pos = 1000
    for p in pnls:
        pos = min(equity * kelly, max_pos)
        equity += pos * p
        equity = max(equity, 1.0)
    years = 9.9
    cagr = (pow(equity / 10000, 1 / years) - 1) * 100 if equity > 10000 else \
           -((1 - pow(equity / 10000, 1 / years)) * 100) if equity > 1 else -99

    return {
        'total': total,
        'wins': wins,
        'hit_rate': hit_rate,
        'breakeven_rate': breakeven_rate,
        'avg_win_pct': avg_win * 100,
        'avg_loss_pct': avg_loss * 100,
        'ev_pct': ev * 100,
        'cagr': cagr,
        'final_equity': equity,
        'margin': hit_rate - breakeven_rate,  # positive = profitable
    }


def main():
    console.print("=" * 80)
    console.print("[bold blue]OPTION LIMIT SWEEP: Finding viable configurations[/bold blue]")
    console.print("=" * 80)
    console.print()

    vix_series = load_vix_data()
    console.print(f"[green]VIX: {vix_series.index.min().date()} to {vix_series.index.max().date()}[/green]")

    setups = precompute_setups('SPY', vix_series)
    console.print()

    # Parameter grid
    target_pcts = [0.03, 0.05, 0.08, 0.10, 0.12, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]
    dte_filters = [None, 1, 2, 3]
    iv_models = [(False, "IV=0.15"), (True, "VIX IV")]

    # Day-of-week combos
    day_filters = [
        (None, "All days"),
        ([0, 2], "Mon+Wed (2D)"),
        ([1, 3], "Tue+Thu (1D)"),
        ([0, 1, 2, 3], "Mon-Thu"),
        ([2, 3], "Wed+Thu"),
        ([0, 1], "Mon+Tue"),
    ]

    results = []

    total_combos = len(target_pcts) * len(dte_filters) * len(iv_models) * len(day_filters)
    console.print(f"Testing {total_combos} combinations...")
    console.print()

    done = 0
    for target_pct in target_pcts:
        for dte_f in dte_filters:
            for use_vix, iv_label in iv_models:
                for day_f, day_label in day_filters:
                    res = evaluate_config(setups, target_pct, dte_f, use_vix, day_f)
                    if res is not None and res['total'] >= 50:  # minimum sample
                        res['target_pct'] = target_pct
                        res['dte_filter'] = dte_f if dte_f else 'all'
                        res['iv_model'] = iv_label
                        res['day_filter'] = day_label
                        results.append(res)

                    done += 1
                    if done % 100 == 0:
                        console.print(f"  {done}/{total_combos}...")

    # Sort by margin (hit_rate - breakeven_rate), most positive first
    results.sort(key=lambda x: x['margin'], reverse=True)

    console.print()
    console.print("=" * 80)
    console.print("[bold white]TOP 30 CONFIGURATIONS (by margin over breakeven)[/bold white]")
    console.print("=" * 80)
    console.print()

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", width=3)
    table.add_column("Target", width=7)
    table.add_column("DTE", width=5)
    table.add_column("IV", width=8)
    table.add_column("Days", width=14)
    table.add_column("Trades", justify="right", width=7)
    table.add_column("Hit%", justify="right", width=7)
    table.add_column("BE%", justify="right", width=7)
    table.add_column("Margin", justify="right", width=8)
    table.add_column("AvgWin", justify="right", width=8)
    table.add_column("AvgLoss", justify="right", width=8)
    table.add_column("EV", justify="right", width=8)
    table.add_column("CAGR", justify="right", width=8)

    for i, r in enumerate(results[:30]):
        m_style = "green" if r['margin'] > 0 else "red"
        ev_style = "green" if r['ev_pct'] > 0 else "red"

        table.add_row(
            str(i+1),
            f"{r['target_pct']*100:.0f}%",
            str(r['dte_filter']),
            r['iv_model'],
            r['day_filter'],
            f"{r['total']:,}",
            f"{r['hit_rate']*100:.1f}%",
            f"{r['breakeven_rate']*100:.1f}%",
            f"[{m_style}]{r['margin']*100:+.1f}pp[/{m_style}]",
            f"{r['avg_win_pct']:+.1f}%",
            f"{r['avg_loss_pct']:+.1f}%",
            f"[{ev_style}]{r['ev_pct']:+.2f}%[/{ev_style}]",
            f"[{ev_style}]{r['cagr']:+.1f}%[/{ev_style}]",
        )

    console.print(table)
    console.print()

    # Check if any are positive
    positive = [r for r in results if r['ev_pct'] > 0]
    if positive:
        console.print(f"[bold green]FOUND {len(positive)} POSITIVE-EV CONFIGURATIONS![/bold green]")
        console.print()
        for r in positive[:10]:
            console.print(f"  target={r['target_pct']*100:.0f}%, DTE={r['dte_filter']}, "
                          f"IV={r['iv_model']}, days={r['day_filter']}")
            console.print(f"    Hit: {r['hit_rate']*100:.1f}%, BE: {r['breakeven_rate']*100:.1f}%, "
                          f"margin: {r['margin']*100:+.1f}pp, EV: {r['ev_pct']:+.2f}%, "
                          f"CAGR: {r['cagr']:+.1f}%")
            console.print()
    else:
        console.print("[bold red]NO positive-EV configuration found.[/bold red]")
        console.print()

        # Show closest to breakeven
        closest = min(results, key=lambda x: abs(x['margin']))
        console.print(f"Closest to breakeven: target={closest['target_pct']*100:.0f}%, "
                      f"DTE={closest['dte_filter']}, days={closest['day_filter']}")
        console.print(f"  Hit: {closest['hit_rate']*100:.1f}%, needs: {closest['breakeven_rate']*100:.1f}%, "
                      f"gap: {closest['margin']*100:+.1f}pp")

    # Save all results
    df = pd.DataFrame(results)
    df.to_csv('results/option_limit_sweep.csv', index=False)
    console.print(f"\n[green]All results saved to: results/option_limit_sweep.csv[/green]")
    console.print("=" * 80)


if __name__ == "__main__":
    main()
