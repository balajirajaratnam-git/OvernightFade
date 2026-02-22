"""
Parameter Sweep: Can ANY version of the overnight fade work with proper BS pricing?

Tests combinations of:
  - Target size: 0.1x, 0.25x, 0.5x, 0.75x, 1.0x, 1.5x, 2.0x ATR
  - Strike offset: ATM, 1% OTM, 2% OTM, 3% OTM
  - DTE filter: 1-day only, 2-day only, 3-day only, all
  - Exit strategy: target-only, hold-to-expiry (no target), time-based-morning-exit

Each combo is priced with Black-Scholes per trade using VIX-derived IV.
Costs (spread, slippage, commission) applied to all.

The goal: find ANY positive-EV configuration, or conclusively prove none exists.
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
from itertools import product

console = Console()

# ---------------------------------------------------------------------------
# Black-Scholes
# ---------------------------------------------------------------------------

def norm_cdf(x):
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

def bs_price(S, K, T, r, sigma, option_type):
    if T <= 0:
        if option_type == 'CALL':
            return max(S - K, 0.0)
        else:
            return max(K - S, 0.0)
    if sigma <= 0:
        sigma = 0.001
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if option_type == 'CALL':
        return max(S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2), 0.0)
    else:
        return max(K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1), 0.0)

# ---------------------------------------------------------------------------
# VIX / IV
# ---------------------------------------------------------------------------

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

def get_iv(date, vix_series, daily_df):
    if vix_series is not None:
        lookup = pd.Timestamp(date).normalize()
        if lookup in vix_series.index:
            return vix_series.loc[lookup] / 100.0
        nearby = vix_series.index[abs(vix_series.index - lookup) <= pd.Timedelta(days=5)]
        if len(nearby) > 0:
            return vix_series.loc[nearby[abs(nearby - lookup).argmin()]] / 100.0
    # Fallback
    if daily_df is not None:
        loc = daily_df.index.get_indexer([pd.Timestamp(date)], method='ffill')[0]
        if loc >= 20:
            rets = daily_df['Close'].iloc[loc-20:loc].pct_change().dropna()
            if len(rets) >= 10:
                return rets.std() * math.sqrt(252)
    return 0.20

# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

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
# Pre-compute all trade signals + intraday price paths
# (Do this ONCE, then replay with different parameters)
# ---------------------------------------------------------------------------

def precompute_trades(ticker, config, vix_series):
    """
    Build a list of trade setups with their full intraday price paths.
    This avoids re-reading parquet files for every parameter combo.
    """
    console.print(f"[cyan]Pre-computing trade setups for {ticker}...[/cyan]")

    daily_file = f'data/{ticker}/daily_OHLCV.parquet'
    if not os.path.exists(daily_file):
        return None, None

    df_daily = pd.read_parquet(daily_file)
    valid_days = df_daily[df_daily.index.dayofweek < 5]

    import pytz
    et_tz = pytz.timezone('America/New_York')

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
            days_to_expiry = 2
        elif day_of_week == 1:
            expiry_date = get_next_wednesday(date_t)
            expiry_label = "TUE-WED-1D"
            days_to_expiry = 1
        elif day_of_week == 2:
            expiry_date = get_next_friday(date_t)
            expiry_label = "WED-FRI-2D"
            days_to_expiry = 2
        elif day_of_week == 3:
            expiry_date = get_next_friday(date_t)
            expiry_label = "THU-FRI-1D"
            days_to_expiry = 1
        elif day_of_week == 4:
            expiry_date = get_next_monday(date_t)
            expiry_label = "FRI-MON-3D"
            days_to_expiry = 3
        else:
            continue

        direction = "GREEN" if day_t['Close'] > day_t['Open'] else "RED"
        if direction == "GREEN":
            signal = "FADE_GREEN"
            option_type = "PUT"
        else:
            signal = "FADE_RED"
            option_type = "CALL"

        entry_price = day_t['Close']
        atr = day_t['ATR_14']
        if pd.isna(atr):
            continue

        iv = get_iv(date_t, vix_series, df_daily)
        if iv is None or iv <= 0:
            continue

        # Collect ALL intraday bars from entry to expiry (with timestamps)
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
                        bars.append({
                            'time': bar_time,
                            'high': bar['High'],
                            'low': bar['Low'],
                            'close': bar['Close'],
                        })

                except Exception:
                    pass

            check_date = get_next_trading_day(check_date, df_daily)
            if check_date is None or check_date > expiry_date:
                break

        # Also get the underlying price at expiry open (for hold-to-expiry)
        expiry_open_price = None
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
                    expiry_open_price = morning.iloc[-1]['Close']
            except Exception:
                pass

        # Also get next-day open for "sell at open" strategy
        next_open_price = None
        next_open_time = None
        next_day = get_next_trading_day(date_t, df_daily)
        if next_day is not None:
            next_intra = f'data/{ticker}/intraday/{next_day.strftime("%Y-%m-%d")}.parquet'
            if os.path.exists(next_intra):
                try:
                    df_next = pd.read_parquet(next_intra)
                    if df_next.index.tz is not None:
                        df_next.index = df_next.index.tz_convert('America/New_York')
                    else:
                        df_next.index = df_next.index.tz_localize('UTC').tz_convert('America/New_York')
                    open_dt = et_tz.localize(datetime(next_day.year, next_day.month, next_day.day, 9, 30))
                    # Get the bar right at or after 9:30
                    morning = df_next[df_next.index >= open_dt].head(5)
                    if not morning.empty:
                        next_open_price = morning.iloc[0]['Open'] if 'Open' in morning.columns else morning.iloc[0]['Close']
                        next_open_time = morning.index[0]
                except Exception:
                    pass

        import pytz as _pytz
        expiry_930 = _pytz.timezone('America/New_York').localize(
            datetime(expiry_date.year, expiry_date.month, expiry_date.day, 9, 30)
        )

        setups.append({
            'date': date_t,
            'entry_price': entry_price,
            'atr': atr,
            'iv': iv,
            'option_type': option_type,
            'signal': signal,
            'direction': direction,
            'days_to_expiry': days_to_expiry,
            'expiry_label': expiry_label,
            'expiry_date': expiry_date,
            'expiry_930': expiry_930,
            'bars': bars,
            'expiry_open_price': expiry_open_price,
            'next_open_price': next_open_price,
            'next_open_time': next_open_time,
        })

    console.print(f"[green]{ticker}: {len(setups)} trade setups pre-computed[/green]")
    return setups, df_daily


def evaluate_params(setups, target_atr_mult, strike_offset_pct, dte_filter, exit_strategy,
                    risk_free_rate=0.045, spread_half=0.025, slippage=0.015, timing=0.02,
                    commission_round_trip=1.30):
    """
    Evaluate a single parameter combination across all pre-computed setups.

    target_atr_mult: target as multiple of ATR (e.g., 0.5)
    strike_offset_pct: 0 = ATM, 0.01 = 1% OTM, etc.
    dte_filter: None = all, or int (1, 2, 3) to filter
    exit_strategy: 'target' | 'morning_exit' | 'hold_to_expiry'

    Returns dict with summary stats.
    """
    pnls = []
    wins = 0
    total = 0

    for setup in setups:
        # DTE filter
        if dte_filter is not None and setup['days_to_expiry'] != dte_filter:
            continue

        entry_price = setup['entry_price']
        atr = setup['atr']
        iv = setup['iv']
        option_type = setup['option_type']
        dte = setup['days_to_expiry']

        # Strike: ATM with optional OTM offset
        if option_type == 'CALL':
            strike = round(entry_price * (1 + strike_offset_pct))
        else:  # PUT
            strike = round(entry_price * (1 - strike_offset_pct))

        # Entry premium
        T_entry = dte / 365.0
        entry_prem = bs_price(entry_price, strike, T_entry, risk_free_rate, iv, option_type)

        if entry_prem < 0.01:
            continue

        total += 1

        # Target distance
        target_dist = atr * target_atr_mult

        if setup['signal'] == 'FADE_GREEN':  # PUT - want underlying to drop
            target_price = entry_price - target_dist
        else:  # CALL - want underlying to rise
            target_price = entry_price + target_dist

        # Determine exit based on strategy
        exit_prem = None
        trade_result = None

        if exit_strategy == 'target':
            # Scan bars for target hit
            for bar in setup['bars']:
                hit = False
                if setup['signal'] == 'FADE_GREEN':
                    if bar['low'] <= target_price:
                        hit = True
                else:
                    if bar['high'] >= target_price:
                        hit = True

                if hit:
                    remaining = (setup['expiry_930'] - bar['time']).total_seconds()
                    T_exit = max(remaining / (365.25 * 24 * 3600), 0.0)
                    exit_prem = bs_price(target_price, strike, T_exit, risk_free_rate, iv, option_type)
                    trade_result = 'WIN'
                    break

            if exit_prem is None:
                # Target not hit - option at expiry
                if setup['expiry_open_price'] is not None:
                    exit_prem = bs_price(setup['expiry_open_price'], strike, 0.0, risk_free_rate, iv, option_type)
                else:
                    exit_prem = 0.0  # expires worthless if no data
                trade_result = 'LOSS'

        elif exit_strategy == 'morning_exit':
            # Always exit at next morning open, regardless of target
            if setup['next_open_price'] is not None and setup['next_open_time'] is not None:
                remaining = (setup['expiry_930'] - setup['next_open_time']).total_seconds()
                T_exit = max(remaining / (365.25 * 24 * 3600), 0.0)
                exit_prem = bs_price(setup['next_open_price'], strike, T_exit, risk_free_rate, iv, option_type)

                # Determine if underlying moved in our direction
                if setup['signal'] == 'FADE_GREEN':
                    trade_result = 'WIN' if setup['next_open_price'] < entry_price else 'LOSS'
                else:
                    trade_result = 'WIN' if setup['next_open_price'] > entry_price else 'LOSS'
            else:
                continue  # skip if no data

        elif exit_strategy == 'hold_to_expiry':
            # Hold to expiry, value = intrinsic
            if setup['expiry_open_price'] is not None:
                exit_prem = bs_price(setup['expiry_open_price'], strike, 0.0, risk_free_rate, iv, option_type)
            else:
                exit_prem = 0.0
            # Win if option has intrinsic value > entry premium
            trade_result = 'WIN' if exit_prem > entry_prem else 'LOSS'

        if exit_prem is None:
            continue

        # Apply costs
        entry_cost = entry_prem * (1.0 + spread_half + slippage + timing)
        exit_proceeds = exit_prem * max(1.0 - spread_half - slippage, 0.0)

        # Commission as pct of contract value
        comm_impact = commission_round_trip / (entry_cost * 100) if entry_cost > 0 else 0

        net_pnl_pct = ((exit_proceeds - entry_cost) / entry_cost - comm_impact) if entry_cost > 0 else -1.0

        pnls.append(net_pnl_pct)
        if trade_result == 'WIN':
            wins += 1

    if total == 0:
        return None

    pnls = np.array(pnls)
    win_rate = wins / total
    avg_pnl = pnls.mean()
    median_pnl = np.median(pnls)

    # Compute equity curve CAGR with Kelly sizing
    kelly_pct = 0.0523
    max_pos = 1000
    equity = 10000
    for p in pnls:
        pos = min(equity * kelly_pct, max_pos)
        equity += pos * p
        equity = max(equity, 1.0)

    years = 9.9  # approx from our dataset
    cagr = (pow(equity / 10000, 1 / years) - 1) * 100 if equity > 1 else -99.0

    # Fraction of trades that are profitable
    profitable_frac = (pnls > 0).sum() / len(pnls)

    return {
        'total_trades': total,
        'win_rate': win_rate,
        'profitable_frac': profitable_frac,
        'avg_pnl_pct': avg_pnl * 100,
        'median_pnl_pct': median_pnl * 100,
        'final_equity': equity,
        'cagr': cagr,
        'pnl_std': pnls.std() * 100,
    }


def main():
    console.print("=" * 80)
    console.print("[bold blue]PARAMETER SWEEP: BS-PRICED OVERNIGHT FADE[/bold blue]")
    console.print("=" * 80)
    console.print()
    console.print("Testing every reasonable combination to find if ANY version works.")
    console.print()

    config = json.load(open("config/config.json"))
    vix_series = load_vix_data()

    # Pre-compute once
    setups, df_daily = precompute_trades('SPY', config, vix_series)
    if not setups:
        console.print("[red]No setups found[/red]")
        return

    console.print()

    # Parameter grid
    target_mults = [0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0]
    strike_offsets = [0.0, 0.01, 0.02, 0.03]  # ATM, 1% OTM, 2% OTM, 3% OTM
    dte_filters = [None, 1, 2, 3]  # None = all
    exit_strategies = ['target', 'morning_exit', 'hold_to_expiry']

    # Cost scenarios
    cost_scenarios = {
        'zero_cost': {'spread_half': 0, 'slippage': 0, 'timing': 0, 'commission_round_trip': 0},
        'low_cost': {'spread_half': 0.01, 'slippage': 0.005, 'timing': 0.01, 'commission_round_trip': 1.30},
        'realistic': {'spread_half': 0.025, 'slippage': 0.015, 'timing': 0.02, 'commission_round_trip': 1.30},
    }

    results = []

    # Run sweep - target + strike combos with realistic costs first
    console.print("[bold]Phase 1: Target x Strike x DTE sweep (realistic costs)[/bold]")
    console.print()

    total_combos = len(target_mults) * len(strike_offsets) * len(dte_filters) * len(exit_strategies)
    console.print(f"Testing {total_combos} combinations...")
    console.print()

    done = 0
    for target_mult in target_mults:
        for strike_off in strike_offsets:
            for dte_f in dte_filters:
                for exit_strat in exit_strategies:
                    # For 'morning_exit' and 'hold_to_expiry', target doesn't matter much
                    # but we still compute it for consistency
                    res = evaluate_params(
                        setups, target_mult, strike_off, dte_f, exit_strat,
                        **cost_scenarios['realistic']
                    )

                    if res is not None:
                        res['target_mult'] = target_mult
                        res['strike_offset'] = strike_off
                        res['dte_filter'] = dte_f if dte_f else 'all'
                        res['exit_strategy'] = exit_strat
                        res['cost_scenario'] = 'realistic'
                        results.append(res)

                    done += 1
                    if done % 50 == 0:
                        console.print(f"  {done}/{total_combos} done...")

    # Also run the best ones with zero_cost and low_cost to separate
    # "strategy doesn't work" from "costs kill it"
    console.print()
    console.print("[bold]Phase 2: Re-running top configs at zero cost[/bold]")

    # Sort by avg_pnl to find best configs
    results_sorted = sorted(results, key=lambda x: x['avg_pnl_pct'], reverse=True)

    # Re-run top 20 with zero cost
    top_configs = results_sorted[:20]
    for cfg in top_configs:
        for cost_name, cost_params in [('zero_cost', cost_scenarios['zero_cost']),
                                        ('low_cost', cost_scenarios['low_cost'])]:
            dte_f = cfg['dte_filter'] if cfg['dte_filter'] != 'all' else None
            res = evaluate_params(
                setups, cfg['target_mult'], cfg['strike_offset'], dte_f,
                cfg['exit_strategy'], **cost_params
            )
            if res is not None:
                res['target_mult'] = cfg['target_mult']
                res['strike_offset'] = cfg['strike_offset']
                res['dte_filter'] = cfg['dte_filter']
                res['exit_strategy'] = cfg['exit_strategy']
                res['cost_scenario'] = cost_name
                results.append(res)

    # ---- Display results ----
    console.print()
    console.print("=" * 80)
    console.print("[bold white]TOP 30 CONFIGURATIONS BY AVERAGE P&L (REALISTIC COSTS)[/bold white]")
    console.print("=" * 80)
    console.print()

    realistic_results = [r for r in results if r['cost_scenario'] == 'realistic']
    realistic_sorted = sorted(realistic_results, key=lambda x: x['avg_pnl_pct'], reverse=True)

    top_table = Table(show_header=True, header_style="bold cyan")
    top_table.add_column("#", width=3)
    top_table.add_column("Target", width=7)
    top_table.add_column("Strike", width=8)
    top_table.add_column("DTE", width=5)
    top_table.add_column("Exit", width=12)
    top_table.add_column("Trades", justify="right", width=7)
    top_table.add_column("WinRate", justify="right", width=8)
    top_table.add_column("Profit%", justify="right", width=8)
    top_table.add_column("AvgP&L", justify="right", width=9)
    top_table.add_column("MedP&L", justify="right", width=9)
    top_table.add_column("CAGR", justify="right", width=9)
    top_table.add_column("Final$", justify="right", width=10)

    for i, r in enumerate(realistic_sorted[:30]):
        ev_style = "green" if r['avg_pnl_pct'] > 0 else "red"
        cagr_style = "green" if r['cagr'] > 0 else "red"

        top_table.add_row(
            str(i + 1),
            f"{r['target_mult']:.2f}x",
            f"{r['strike_offset']*100:.0f}%OTM" if r['strike_offset'] > 0 else "ATM",
            str(r['dte_filter']),
            r['exit_strategy'],
            f"{r['total_trades']:,}",
            f"{r['win_rate']*100:.1f}%",
            f"{r['profitable_frac']*100:.1f}%",
            f"[{ev_style}]{r['avg_pnl_pct']:+.2f}%[/{ev_style}]",
            f"{r['median_pnl_pct']:+.2f}%",
            f"[{cagr_style}]{r['cagr']:+.1f}%[/{cagr_style}]",
            f"${r['final_equity']:,.0f}",
        )

    console.print(top_table)
    console.print()

    # Show the BEST configs at different cost levels
    console.print("=" * 80)
    console.print("[bold white]BEST CONFIG AT EACH COST LEVEL[/bold white]")
    console.print("=" * 80)
    console.print()

    for cost_name in ['zero_cost', 'low_cost', 'realistic']:
        cost_results = [r for r in results if r['cost_scenario'] == cost_name]
        if cost_results:
            best = max(cost_results, key=lambda x: x['avg_pnl_pct'])
            style = "green" if best['avg_pnl_pct'] > 0 else "red"
            console.print(f"[bold]{cost_name.upper()}:[/bold]")
            console.print(f"  Config: target={best['target_mult']}x ATR, strike={best['strike_offset']*100:.0f}% OTM, "
                          f"DTE={best['dte_filter']}, exit={best['exit_strategy']}")
            console.print(f"  Trades: {best['total_trades']:,}, Win Rate: {best['win_rate']*100:.1f}%")
            console.print(f"  [{style}]Avg P&L: {best['avg_pnl_pct']:+.2f}%, CAGR: {best['cagr']:+.1f}%, "
                          f"Final: ${best['final_equity']:,.0f}[/{style}]")
            console.print()

    # Overall verdict
    console.print("=" * 80)
    console.print("[bold white]VERDICT[/bold white]")
    console.print("=" * 80)
    console.print()

    any_positive_realistic = any(r['avg_pnl_pct'] > 0 for r in realistic_results)
    any_positive_zero = any(r['avg_pnl_pct'] > 0 for r in results if r['cost_scenario'] == 'zero_cost')

    if any_positive_realistic:
        best_real = max(realistic_results, key=lambda x: x['avg_pnl_pct'])
        console.print(f"[bold green]FOUND POSITIVE-EV CONFIG (with realistic costs):[/bold green]")
        console.print(f"  {best_real['target_mult']}x ATR, {best_real['strike_offset']*100:.0f}% OTM, "
                      f"DTE={best_real['dte_filter']}, exit={best_real['exit_strategy']}")
        console.print(f"  Avg P&L: {best_real['avg_pnl_pct']:+.2f}%, CAGR: {best_real['cagr']:+.1f}%")
    elif any_positive_zero:
        best_zero = max([r for r in results if r['cost_scenario'] == 'zero_cost'],
                        key=lambda x: x['avg_pnl_pct'])
        console.print("[bold yellow]Strategy has positive EV only with ZERO costs.[/bold yellow]")
        console.print(f"  Best zero-cost: {best_zero['avg_pnl_pct']:+.2f}% avg P&L")
        console.print("[bold yellow]Real-world costs eliminate the edge entirely.[/bold yellow]")
    else:
        console.print("[bold red]NO configuration has positive expected value.[/bold red]")
        console.print("[bold red]The overnight fade strategy does not work with proper option pricing.[/bold red]")
        console.print("[bold red]This is true even with ZERO transaction costs.[/bold red]")

    console.print()

    # Save full results
    df_results = pd.DataFrame(results)
    df_results.to_csv('results/bs_parameter_sweep.csv', index=False)
    console.print("[green]Full sweep results saved to: results/bs_parameter_sweep.csv[/green]")
    console.print()
    console.print("=" * 80)


if __name__ == "__main__":
    main()
