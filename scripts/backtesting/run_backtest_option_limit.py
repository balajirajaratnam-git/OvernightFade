"""
Backtest: OPTION PREMIUM LIMIT ORDER strategy

This models what actually happens:
1. At close, buy ATM option (BS-priced, sigma=0.15 per dashboard)
2. Set SELL LIMIT on option premium at entry * (1 + target_pct)
3. Scan intraday bars: at each bar, compute BS premium with current underlying price
4. If premium >= limit -> WIN (exit at limit, collect target_pct)
5. If premium never hits limit by expiry -> LOSS (option expires, collect intrinsic)

target_pct comes from reality_adjustments:
  SPY 1-day: 0.63 * 45% = 28.4%
  SPY 2-day: 0.50 * 45% = 22.5%
  SPY 3-day: 0.45 * 45% = 20.3%

This properly models the actual trading execution.
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

# ---------------------------------------------------------------------------
# Black-Scholes (matching auto_trade_ig.py: sigma=0.15, r=0.05)
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
# VIX for realistic IV (use actual VIX, not hardcoded 0.15)
# ---------------------------------------------------------------------------

def load_vix_data():
    """Load VIX daily data from CBOE parquet (1990-present, OHLC)."""
    cache_file = Path("data/vix_daily_cache.parquet")
    if not cache_file.exists():
        raise FileNotFoundError("data/vix_daily_cache.parquet not found. Run CBOE VIX download first.")
    df = pd.read_parquet(cache_file)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    # Return the Close column as a Series
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
# Main backtest
# ---------------------------------------------------------------------------

def run_option_limit_backtest(ticker, config, adjustments, vix_series, use_vix_iv=False):
    """
    Backtest with option premium limit orders.

    For each trade:
    1. Compute entry premium via BS at close
    2. Set target premium = entry * (1 + target_pct)
    3. Scan each bar: compute premium with new underlying price & remaining time
    4. If premium >= target -> exit at target_pct profit
    5. If no hit -> exit at expiry (intrinsic value)
    """
    console.print(f"\n[cyan]Running OPTION LIMIT backtest for {ticker}...[/cyan]")

    daily_file = f'data/{ticker}/daily_OHLCV.parquet'
    if not os.path.exists(daily_file):
        return None

    df_daily = pd.read_parquet(daily_file)
    valid_days = df_daily[df_daily.index.dayofweek < 5]

    import pytz
    et_tz = pytz.timezone('America/New_York')

    # BS params (matching auto_trade_ig.py)
    sigma_dashboard = 0.15  # hardcoded in dashboard
    r = 0.05

    # Spread/slippage on the option premium (IG charges spread on the option itself)
    # IG option spread on US 500 is typically 0.5-2 pts on a 15-25 pt option = ~3-10%
    # We'll model it as a percentage of premium
    ig_option_spread_pct = 0.04  # 4% round-trip on premium (2% each way)
    ig_slippage_pct = 0.01  # 1% slippage on premium

    trades = []

    for i in range(len(valid_days)):
        day_t = valid_days.iloc[i]
        date_t = valid_days.index[i]
        date_str = date_t.strftime("%Y-%m-%d")
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

        entry_price_spy = day_t['Close']
        atr = day_t['ATR_14']
        if pd.isna(atr):
            continue

        strike_spy = round(entry_price_spy)

        # Entry premium and limit computation
        T_entry = days_to_expiry / 365.0

        # Step 1: Dashboard computes limit_pts using sigma=0.15 (always)
        dash_prem = bs_price(entry_price_spy, strike_spy, T_entry, r, sigma_dashboard, option_type)
        if dash_prem < 0.01:
            continue

        expiry_key = f"{days_to_expiry}_day"
        pnl_mult = adjustments.get("pnl_adjustments", {}).get(expiry_key, {}).get(ticker, 0.50)
        target_pct_dash = pnl_mult * 0.45  # fraction
        limit_pts = dash_prem * target_pct_dash  # ABSOLUTE points (SPY scale)

        # Step 2: Real entry premium (what IG actually charges, at real IV)
        sigma_real = get_iv(date_t, vix_series) if use_vix_iv else sigma_dashboard
        entry_prem = bs_price(entry_price_spy, strike_spy, T_entry, r, sigma_real, option_type)
        if entry_prem < 0.01:
            continue

        # Step 3: Target = real entry + absolute limit_pts from dashboard
        # This is what the user actually types: sell when premium reaches entry + limit_pts
        target_prem = entry_prem + limit_pts
        target_pct = limit_pts / entry_prem  # actual % gain needed (for reporting)

        # Compute expiry datetime for time remaining calculations
        expiry_930 = et_tz.localize(
            datetime(expiry_date.year, expiry_date.month, expiry_date.day, 9, 30)
        )

        # Scan bars for premium hitting target
        limit_hit = False
        exit_prem = None
        exit_time = None
        exit_underlying = None

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

                    if not df_window.empty:
                        for bar_time, bar in df_window.iterrows():
                            # Compute time remaining to expiry
                            remaining = (expiry_930 - bar_time).total_seconds()
                            T_now = max(remaining / (365.25 * 24 * 3600), 0.0)

                            # Compute current premium with current bar's price
                            # For CALL: use bar High (best case for buyer)
                            # For PUT: use bar Low (best case for buyer)
                            if option_type == 'CALL':
                                bar_price = bar['High']
                            else:
                                bar_price = bar['Low']

                            current_prem = bs_price(bar_price, strike_spy, T_now, r, sigma_real, option_type)

                            if current_prem >= target_prem:
                                limit_hit = True
                                exit_prem = target_prem  # fill at limit
                                exit_time = bar_time
                                exit_underlying = bar_price
                                break

                        if limit_hit:
                            break

                except Exception:
                    pass

            check_date = get_next_trading_day(check_date, df_daily)
            if check_date is None or check_date > expiry_date:
                break

        # If limit not hit: option expires
        if not limit_hit:
            # Get underlying at expiry to compute intrinsic
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

            if expiry_underlying is not None:
                exit_prem = bs_price(expiry_underlying, strike_spy, 0.0, r, sigma_real, option_type)
                exit_underlying = expiry_underlying
            else:
                exit_prem = 0.0
                exit_underlying = entry_price_spy

        # P&L calculation
        if limit_hit:
            # Win: collect target_pct on premium, minus costs
            gross_pnl_pct = target_pct  # e.g., 0.2835 (28.35%)
            # Costs: spread + slippage on option premium (not underlying)
            net_pnl_pct = gross_pnl_pct - ig_option_spread_pct - ig_slippage_pct
            result = "WIN"
        else:
            # Loss: premium decays/expires
            gross_pnl_pct = (exit_prem - entry_prem) / entry_prem if entry_prem > 0 else -1.0
            net_pnl_pct = gross_pnl_pct - ig_option_spread_pct - ig_slippage_pct
            # Cap loss at -100% of premium
            net_pnl_pct = max(net_pnl_pct, -1.0)
            result = "LOSS"

        trades.append({
            'Date': date_str,
            'Ticker': ticker,
            'Day_of_Week': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'][day_of_week],
            'Expiry_Label': expiry_label,
            'Days_To_Expiry': days_to_expiry,
            'Signal': signal,
            'Option_Type': option_type,
            'Entry_Price': entry_price_spy,
            'Strike': strike_spy,
            'ATR': atr,
            'IV_Entry': sigma_real,
            'Entry_Premium': entry_prem,
            'Target_Premium': target_prem,
            'Target_Pct': target_pct * 100,
            'Exit_Premium': exit_prem,
            'Exit_Underlying': exit_underlying,
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


def calculate_equity_curve(df, starting_capital, kelly_pct, max_position, pnl_column):
    equity = starting_capital
    curve = []
    for _, row in df.iterrows():
        pos = min(equity * kelly_pct, max_position)
        equity += pos * row[pnl_column]
        equity = max(equity, 1.0)
        curve.append(equity)
    return curve


def main():
    console.print("=" * 80)
    console.print("[bold blue]BACKTEST: OPTION PREMIUM LIMIT ORDER (actual trading model)[/bold blue]")
    console.print("=" * 80)
    console.print()
    console.print("[bold]This models your actual IG.com execution:[/bold]")
    console.print("  1. Buy ATM option at close")
    console.print("  2. Set SELL LIMIT at entry_premium + limit_pts from dashboard")
    console.print("  3. If limit fills overnight -> WIN (collect ~29%)")
    console.print("  4. If limit doesn't fill by expiry -> LOSS (premium expires)")
    console.print()

    config = load_config()
    adjustments = load_reality_adjustments()
    vix_series = load_vix_data()

    console.print(f"[green]VIX data: {vix_series.index.min().date()} to {vix_series.index.max().date()}[/green]")
    console.print()

    # Run with both IV models
    for iv_label, use_vix in [("Dashboard IV (0.15 fixed)", False), ("VIX-derived IV (realistic)", True)]:
        console.print("=" * 80)
        console.print(f"[bold white]MODEL: {iv_label}[/bold white]")
        console.print("=" * 80)
        console.print()

        df = run_option_limit_backtest('SPY', config, adjustments, vix_series, use_vix_iv=use_vix)
        if df is None or df.empty:
            console.print("[red]No results[/red]")
            continue

        # Equity curve
        starting_capital = 10000
        kelly_pct = 0.0523
        max_position = 1000

        eq = calculate_equity_curve(df, starting_capital, kelly_pct, max_position, 'PnL_Mult')

        df['Equity'] = eq
        df['Date'] = pd.to_datetime(df['Date'])
        years = (df['Date'].max() - df['Date'].min()).days / 365.25

        final = eq[-1]
        cagr = (pow(final / starting_capital, 1 / years) - 1) * 100 if final > starting_capital else \
               -((1 - pow(final / starting_capital, 1 / years)) * 100)

        wins = (df['Result'] == 'WIN').sum()
        win_rate = wins / len(df) * 100

        running_max = pd.Series(eq).expanding().max()
        max_dd = ((pd.Series(eq) - running_max) / running_max * 100).min()

        # Display
        console.print(f"  Trades:         {len(df):,}")
        console.print(f"  Limit hit rate: [bold]{win_rate:.1f}%[/bold]")
        console.print(f"  Starting:       ${starting_capital:,.0f}")
        console.print(f"  Final:          [bold]${final:,.0f}[/bold]")
        console.print(f"  CAGR:           [bold]{cagr:+.1f}%[/bold]")
        console.print(f"  Max Drawdown:   {max_dd:.1f}%")
        console.print()

        wins_df = df[df['Result'] == 'WIN']
        losses_df = df[df['Result'] == 'LOSS']

        if len(wins_df) > 0:
            console.print(f"  [green]WINS: avg net P&L = {wins_df['Net_PnL_Pct'].mean():+.1f}%[/green]")
        if len(losses_df) > 0:
            console.print(f"  [red]LOSSES: avg net P&L = {losses_df['Net_PnL_Pct'].mean():+.1f}%[/red]")

        ev = df['Net_PnL_Pct'].mean()
        console.print(f"  [bold]EV per trade: {ev:+.2f}%[/bold]")
        console.print()

        # Breakdown by expiry
        exp_table = Table(show_header=True, header_style="bold cyan")
        exp_table.add_column("Pattern", width=15)
        exp_table.add_column("Trades", justify="right", width=8)
        exp_table.add_column("Limit Hit", justify="right", width=10)
        exp_table.add_column("Target%", justify="right", width=10)
        exp_table.add_column("Avg Win", justify="right", width=10)
        exp_table.add_column("Avg Loss", justify="right", width=10)
        exp_table.add_column("EV", justify="right", width=10)

        for label in sorted(df['Expiry_Label'].unique()):
            sub = df[df['Expiry_Label'] == label]
            wr = (sub['Result'] == 'WIN').sum() / len(sub) * 100
            avg_tgt = sub['Target_Pct'].mean()
            avg_win = sub[sub['Result'] == 'WIN']['Net_PnL_Pct'].mean() if (sub['Result'] == 'WIN').sum() > 0 else 0
            avg_loss = sub[sub['Result'] == 'LOSS']['Net_PnL_Pct'].mean() if (sub['Result'] == 'LOSS').sum() > 0 else 0
            ev_t = sub['Net_PnL_Pct'].mean()
            style = "green" if ev_t > 0 else "red"

            exp_table.add_row(label, f"{len(sub):,}", f"{wr:.1f}%", f"{avg_tgt:.1f}%",
                              f"{avg_win:+.1f}%", f"{avg_loss:+.1f}%",
                              f"[{style}]{ev_t:+.2f}%[/{style}]")

        console.print(exp_table)
        console.print()

        # Save
        suffix = "dashboard_iv" if not use_vix else "vix_iv"
        output_file = f'results/option_limit_backtest_{suffix}.csv'
        df.to_csv(output_file, index=False)
        console.print(f"[green]Saved to: {output_file}[/green]")
        console.print()

    # Final comparison
    console.print("=" * 80)
    console.print("[bold white]SUMMARY[/bold white]")
    console.print("=" * 80)
    console.print()
    console.print("The key question was: when you set a sell limit on the option premium")
    console.print("at ~29% above entry, how often does it actually get filled?")
    console.print()
    console.print("With dashboard IV (0.15): the limit is 'easy' to hit because the")
    console.print("  real market has higher IV, so the option premium is actually bigger")
    console.print("  than the dashboard thinks. Your limit may be below fair value.")
    console.print()
    console.print("With VIX IV (realistic): the limit is set relative to real premiums,")
    console.print("  so the fill rate reflects actual market dynamics.")
    console.print()
    console.print("=" * 80)


if __name__ == "__main__":
    main()
