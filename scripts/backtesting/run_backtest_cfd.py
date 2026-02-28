"""
Backtest: OvernightFade as CFD / Spread-bet

Tests the same directional signal (FADE_RED / FADE_GREEN) with a linear
CFD position instead of options.  Removes all option-pricing assumptions —
P&L is purely based on price movement from entry to exit.

STRATEGY
  Entry  : SPY close price (16:00 ET) on signal day
  Signal : RED day (close < open) -> LONG;  GREEN day -> SHORT
  Target : entry +/- ATR14 x 0.1  (same as options backtest)
  Stop   : entry -/+ ATR14 x stop_mult  (default 0.2; flag: --stop-mult)
  Scan   : next-day RTH bars (09:30-16:00 ET), bar-by-bar;
           first target or stop hit wins;
           if neither hit by EOD of window last day, exit at close

EXPIRY WINDOWS  (same calendar dates as options backtest)
  MON entry -> scan through WED close  (2-day window)
  TUE entry -> scan through WED close  (1-day window)
  WED entry -> scan through FRI close  (2-day window)
  THU entry -> scan through FRI close  (1-day window)

P&L  (linear, no Black-Scholes)
  gross_pts = (exit_price - entry_price) x direction
  cost_pts  = spread_cost  (default 0.04 SPY pts = 0.4 IG pts / 10)
  net_pts   = gross_pts - cost_pts
  WIN       = net_pts > 0

USAGE
  python scripts/backtesting/run_backtest_cfd.py
  python scripts/backtesting/run_backtest_cfd.py --stop-mult 0.3
  python scripts/backtesting/run_backtest_cfd.py --spread-cost 0.06
  python scripts/backtesting/run_backtest_cfd.py --sweep
"""
import sys
sys.path.insert(0, 'src')

import os
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import numpy as np
import pytz
from rich.console import Console
from rich.table import Table

from pricing import compute_wilders_atr, load_vix_data, get_iv_for_date

console = Console(highlight=False)

TZ_ET = pytz.timezone('America/New_York')

# Constants matching the options backtest
TARGET_MULT  = 0.1      # ATR multiple for profit target (read from config; 0.1 is default)
STARTING_CAP = 10_000
KELLY_PCT    = 0.0523   # Same as options backtest for comparability
MAX_POSITION = 1_000


# ---------------------------------------------------------------------------
# Config / date helpers
# ---------------------------------------------------------------------------

def load_config():
    with open("config/config.json") as f:
        return json.load(f)


def _next_td(date, df_daily):
    """Next trading day after date."""
    nd = date + timedelta(days=1)
    for _ in range(10):
        if nd in df_daily.index:
            return nd
        nd += timedelta(days=1)
    return None


def _next_wednesday(date):
    dow = date.weekday()
    if dow in (0, 1):
        return date + timedelta(days=2 - dow)
    return date + timedelta(days=9 - dow)


def _next_friday(date):
    dow = date.weekday()
    if dow < 4:
        return date + timedelta(days=4 - dow)
    return date + timedelta(days=7)


def _next_monday(date):
    dow = date.weekday()
    return date + timedelta(days=3 if dow == 4 else 7 - dow)


# ---------------------------------------------------------------------------
# Core CFD backtest
# ---------------------------------------------------------------------------

def run_cfd_backtest(ticker, config, vix_series, stop_mult=0.2, spread_cost=0.04):
    """
    Run a single CFD backtest pass for one stop multiplier.

    Args:
        ticker:      Ticker symbol (e.g. 'SPY').
        config:      Loaded config dict.
        vix_series:  VIX close series from load_vix_data(); used for IV regime buckets.
        stop_mult:   ATR multiple for stop loss (e.g. 0.2 -> stop 0.2*ATR14 from entry).
        spread_cost: Fixed IG spread cost per round-trip in SPY points (default 0.04).

    Returns:
        pd.DataFrame of trades, or empty DataFrame if no data.
    """
    daily_file = f'data/{ticker}/daily_OHLCV.parquet'
    if not os.path.exists(daily_file):
        console.print(f"[red]No daily data for {ticker}[/red]")
        return pd.DataFrame()

    df_daily = pd.read_parquet(daily_file)

    # Compute ATR if the pre-built column is missing
    if 'ATR_14' not in df_daily.columns:
        df_daily['ATR_14'] = compute_wilders_atr(
            df_daily['High'], df_daily['Low'], df_daily['Close']
        )

    valid_days = df_daily[df_daily.index.dayofweek < 5]
    filters    = config.get('filters', {})
    target_mult = config.get('default_take_profit_atr', TARGET_MULT)

    trades = []

    for i in range(len(valid_days)):
        day_t  = valid_days.iloc[i]
        date_t = valid_days.index[i]
        dow    = date_t.dayofweek

        # ---- Filters (same as options backtest) ----
        magnitude = abs((day_t['Close'] - day_t['Open']) / day_t['Open']) * 100
        if magnitude < filters.get('min_magnitude_pct', 0.10):
            continue

        if dow == 4 and filters.get('exclude_fridays', False):
            continue

        # ---- Expiry window (same calendar dates as options) ----
        if dow == 0:
            expiry_date  = _next_wednesday(date_t); expiry_label = "MON-WED-2D"
        elif dow == 1:
            expiry_date  = _next_wednesday(date_t); expiry_label = "TUE-WED-1D"
        elif dow == 2:
            expiry_date  = _next_friday(date_t);    expiry_label = "WED-FRI-2D"
        elif dow == 3:
            expiry_date  = _next_friday(date_t);    expiry_label = "THU-FRI-1D"
        elif dow == 4:
            expiry_date  = _next_monday(date_t);    expiry_label = "FRI-MON-3D"
        else:
            continue

        # ---- Signal / direction ----
        direction_str = "GREEN" if day_t['Close'] > day_t['Open'] else "RED"
        signal = f"FADE_{direction_str}"

        if signal == "FADE_GREEN" and not filters.get('enable_fade_green', True):
            continue
        if signal == "FADE_RED"   and not filters.get('enable_fade_red',   True):
            continue

        entry_price = float(day_t['Close'])
        atr         = float(day_t['ATR_14'])
        if pd.isna(atr) or atr <= 0:
            continue

        # FADE_RED  = underlying closed DOWN -> go LONG (expect reversal up)
        # FADE_GREEN = underlying closed UP  -> go SHORT (expect reversal down)
        direction    = +1.0 if signal == "FADE_RED" else -1.0
        target_price = entry_price + direction * atr * target_mult
        stop_price   = entry_price - direction * atr * stop_mult

        # VIX for IV regime bucketing (not used in P&L, just for breakdown output)
        iv_entry = get_iv_for_date(date_t, vix_series, df_daily)

        # ---- Scan next-day RTH bars through expiry window ----
        target_hit = False
        stop_hit   = False
        exit_price = None
        last_close = None    # last RTH close seen — used for EOD fallback

        check_date = _next_td(date_t, df_daily)

        while check_date is not None and check_date <= expiry_date:
            intra_path = f'data/{ticker}/intraday/{check_date.strftime("%Y-%m-%d")}.parquet'

            if os.path.exists(intra_path):
                try:
                    df_intra = pd.read_parquet(intra_path)

                    if df_intra.index.tz is not None:
                        df_intra.index = df_intra.index.tz_convert('America/New_York')
                    else:
                        df_intra.index = df_intra.index.tz_localize('UTC').tz_convert('America/New_York')

                    # Filter to RTH: 09:30 - 16:00 ET
                    rth_s = TZ_ET.localize(datetime(
                        check_date.year, check_date.month, check_date.day, 9, 30))
                    rth_e = TZ_ET.localize(datetime(
                        check_date.year, check_date.month, check_date.day, 16, 0))
                    df_win = df_intra[
                        (df_intra.index >= rth_s) & (df_intra.index <= rth_e)
                    ]

                    if not df_win.empty:
                        for _bt, bar in df_win.iterrows():
                            if direction > 0:   # LONG: target up, stop down
                                if bar['High'] >= target_price:
                                    exit_price = target_price; target_hit = True; break
                                if bar['Low']  <= stop_price:
                                    exit_price = stop_price;   stop_hit   = True; break
                            else:               # SHORT: target down, stop up
                                if bar['Low']  <= target_price:
                                    exit_price = target_price; target_hit = True; break
                                if bar['High'] >= stop_price:
                                    exit_price = stop_price;   stop_hit   = True; break

                        if target_hit or stop_hit:
                            break

                        last_close = float(df_win.iloc[-1]['Close'])

                except Exception:
                    pass

            check_date = _next_td(check_date, df_daily)

        # ---- EOD fallback: exit at last close seen in window ----
        if not (target_hit or stop_hit):
            if last_close is not None:
                exit_price = last_close
            elif expiry_date in df_daily.index:
                exit_price = float(df_daily.loc[expiry_date, 'Close'])
            else:
                exit_price = entry_price   # last resort

        exit_type = "TARGET" if target_hit else ("STOP" if stop_hit else "EOD")

        # ---- P&L (linear, no options) ----
        gross_pts = (exit_price - entry_price) * direction
        net_pts   = gross_pts - spread_cost
        pnl_mult  = net_pts / entry_price   # fraction — used by equity curve
        result    = "WIN" if pnl_mult > 0 else "LOSS"

        trades.append({
            'Date':          date_t.strftime("%Y-%m-%d"),
            'Ticker':        ticker,
            'Day_of_Week':   ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'][dow],
            'Expiry_Label':  expiry_label,
            'Signal':        signal,
            'Direction':     'LONG' if direction > 0 else 'SHORT',
            'Entry_Price':   round(entry_price, 4),
            'Target_Price':  round(target_price, 4),
            'Stop_Price':    round(stop_price,   4),
            'Exit_Price':    round(exit_price,   4),
            'Exit_Type':     exit_type,
            'ATR':           round(atr, 4),
            'IV_Entry':      round(iv_entry, 4),
            'Target_Hit':    target_hit,
            'Stop_Hit':      stop_hit,
            'Gross_Pts':     round(gross_pts, 4),
            'Net_Pts':       round(net_pts, 4),
            'Gross_PnL_Pct': round(gross_pts / entry_price * 100, 4),
            'Net_PnL_Pct':   round(pnl_mult * 100, 4),
            'PnL_Mult':      pnl_mult,
            'Result':        result,
            'Magnitude':     round(magnitude, 4),
        })

    console.print(
        f"[green]{ticker}: {len(trades)} trades  "
        f"(stop_mult={stop_mult}, spread_cost={spread_cost})[/green]"
    )
    return pd.DataFrame(trades) if trades else pd.DataFrame()


# ---------------------------------------------------------------------------
# Equity curve  (same parameters as options backtest for comparability)
# ---------------------------------------------------------------------------

def calc_equity_curve(df,
                      starting_capital=STARTING_CAP,
                      kelly_pct=KELLY_PCT,
                      max_position=MAX_POSITION):
    equity = starting_capital
    curve  = []
    for _, row in df.iterrows():
        position = min(equity * kelly_pct, max_position)
        equity   = max(equity + position * row['PnL_Mult'], 1.0)
        curve.append(equity)
    return curve


# ---------------------------------------------------------------------------
# Rich table builders
# ---------------------------------------------------------------------------

def _iv_breakdown_table(df):
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("VIX Range",  width=14)
    t.add_column("Trades",     justify="right", width=8)
    t.add_column("Win Rate",   justify="right", width=10)
    t.add_column("Avg Win",    justify="right", width=10)
    t.add_column("Avg Loss",   justify="right", width=10)
    t.add_column("EV/trade",   justify="right", width=12)

    buckets = [
        (0,    0.15, "VIX < 15"),
        (0.15, 0.20, "VIX 15-20"),
        (0.20, 0.25, "VIX 20-25"),
        (0.25, 0.35, "VIX 25-35"),
        (0.35, 1.0,  "VIX > 35"),
    ]
    for lo, hi, lbl in buckets:
        sub = df[(df['IV_Entry'] >= lo) & (df['IV_Entry'] < hi)]
        if len(sub) == 0:
            continue
        wr   = (sub['Result'] == 'WIN').mean() * 100
        wins = sub[sub['Result'] == 'WIN']['Net_PnL_Pct']
        loss = sub[sub['Result'] == 'LOSS']['Net_PnL_Pct']
        ev   = sub['Net_PnL_Pct'].mean()
        col  = 'green' if ev >= 0 else 'red'
        t.add_row(
            lbl, f"{len(sub):,}", f"{wr:.1f}%",
            f"{wins.mean():+.3f}%" if len(wins) > 0 else "N/A",
            f"{loss.mean():+.3f}%" if len(loss) > 0 else "N/A",
            f"[{col}]{ev:+.3f}%[/{col}]",
        )
    return t


def _expiry_breakdown_table(df):
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Pattern",    width=14)
    t.add_column("Trades",     justify="right", width=8)
    t.add_column("Win Rate",   justify="right", width=10)
    t.add_column("Avg Win",    justify="right", width=10)
    t.add_column("Avg Loss",   justify="right", width=10)
    t.add_column("EV/trade",   justify="right", width=12)

    for lbl in sorted(df['Expiry_Label'].unique()):
        sub  = df[df['Expiry_Label'] == lbl]
        wr   = (sub['Result'] == 'WIN').mean() * 100
        wins = sub[sub['Result'] == 'WIN']['Net_PnL_Pct']
        loss = sub[sub['Result'] == 'LOSS']['Net_PnL_Pct']
        ev   = sub['Net_PnL_Pct'].mean()
        col  = 'green' if ev >= 0 else 'red'
        t.add_row(
            lbl, f"{len(sub):,}", f"{wr:.1f}%",
            f"{wins.mean():+.3f}%" if len(wins) > 0 else "N/A",
            f"{loss.mean():+.3f}%" if len(loss) > 0 else "N/A",
            f"[{col}]{ev:+.3f}%[/{col}]",
        )
    return t


# ---------------------------------------------------------------------------
# Full results printer (single stop_mult run)
# ---------------------------------------------------------------------------

def print_full_results(df, stop_mult, spread_cost):
    if df.empty:
        console.print("[red]No trades generated.[/red]")
        return

    df = df.copy()
    df['Date'] = pd.to_datetime(df['Date'])
    years = (df['Date'].max() - df['Date'].min()).days / 365.25

    eq_curve = calc_equity_curve(df)
    final    = eq_curve[-1]
    cagr     = (pow(final / STARTING_CAP, 1 / years) - 1) * 100 if years > 0 else 0.0

    running_max = pd.Series(eq_curve).expanding().max()
    max_dd      = ((pd.Series(eq_curve) - running_max) / running_max * 100).min()

    wins_df = df[df['Result'] == 'WIN']
    loss_df = df[df['Result'] == 'LOSS']
    wr      = len(wins_df) / len(df) * 100
    ev      = df['Net_PnL_Pct'].mean()

    n_target = (df['Exit_Type'] == 'TARGET').sum()
    n_stop   = (df['Exit_Type'] == 'STOP').sum()
    n_eod    = (df['Exit_Type'] == 'EOD').sum()

    avg_atr          = df['ATR'].mean()
    avg_target_dist  = avg_atr * stop_mult        # stop distance for labelling
    avg_tgt_dist_pct = avg_atr * TARGET_MULT / df['Entry_Price'].mean() * 100
    avg_stp_dist_pct = avg_atr * stop_mult / df['Entry_Price'].mean() * 100

    # ---- Summary table ----
    console.print("=" * 80)
    console.print(
        f"[bold white]RESULTS: CFD BACKTEST  "
        f"stop_mult={stop_mult}x ATR  spread_cost={spread_cost} pts[/bold white]"
    )
    console.print("=" * 80)
    console.print()

    s = Table(show_header=True, header_style="bold cyan")
    s.add_column("Metric",  style="white", width=34)
    s.add_column("Value",   justify="right", width=22)

    s.add_row("Period",
              f"{df['Date'].min().strftime('%Y-%m-%d')} to "
              f"{df['Date'].max().strftime('%Y-%m-%d')}")
    s.add_row("Years",          f"{years:.1f}")
    s.add_row("Total Trades",   f"{len(df):,}")
    s.add_row("Win Rate",       f"{wr:.1f}%")
    s.add_row("EV / trade",     f"{ev:+.3f}%")
    s.add_row("", "")
    s.add_row("Starting Capital", f"${STARTING_CAP:,.0f}")
    s.add_row("Final Equity",   f"[bold]${final:,.0f}[/bold]")
    s.add_row("CAGR",           f"[bold]{cagr:+.1f}%[/bold]")
    s.add_row("Max Drawdown",   f"{max_dd:.1f}%")
    s.add_row("", "")
    s.add_row("Target hits",    f"{n_target:,}  ({n_target/len(df)*100:.1f}%)")
    s.add_row("Stop hits",      f"{n_stop:,}  ({n_stop/len(df)*100:.1f}%)")
    s.add_row("EOD exits",      f"{n_eod:,}  ({n_eod/len(df)*100:.1f}%)")
    s.add_row("", "")
    s.add_row("Avg win  (net)",
              f"{wins_df['Net_PnL_Pct'].mean():+.3f}%" if len(wins_df) > 0 else "N/A")
    s.add_row("Avg loss (net)",
              f"{loss_df['Net_PnL_Pct'].mean():+.3f}%" if len(loss_df) > 0 else "N/A")
    s.add_row("", "")
    s.add_row("Avg ATR",        f"{avg_atr:.2f} pts")
    s.add_row("Target dist (avg)", f"{avg_atr*TARGET_MULT:.2f} pts  ({avg_tgt_dist_pct:.2f}%)")
    s.add_row("Stop dist (avg)",   f"{avg_atr*stop_mult:.2f} pts  ({avg_stp_dist_pct:.2f}%)")

    console.print(s)
    console.print()

    # ---- IV regime breakdown ----
    console.print("=" * 80)
    console.print("[bold white]RETURNS BY IV REGIME (VIX)[/bold white]")
    console.print("=" * 80)
    console.print()
    console.print(_iv_breakdown_table(df))
    console.print()

    # ---- Expiry pattern breakdown ----
    console.print("=" * 80)
    console.print("[bold white]RETURNS BY EXPIRY PATTERN[/bold white]")
    console.print("=" * 80)
    console.print()
    console.print(_expiry_breakdown_table(df))
    console.print()

    # ---- Sample trades ----
    console.print("=" * 80)
    console.print("[bold white]SAMPLE TRADES (first 10 wins, first 5 losses)[/bold white]")
    console.print("=" * 80)
    console.print()

    st = Table(show_header=True, header_style="bold cyan", show_lines=True)
    st.add_column("Date",    width=12)
    st.add_column("Dir",     width=6)
    st.add_column("Entry",   justify="right", width=8)
    st.add_column("Target",  justify="right", width=8)
    st.add_column("Stop",    justify="right", width=8)
    st.add_column("Exit",    justify="right", width=8)
    st.add_column("Type",    width=7)
    st.add_column("ATR",     justify="right", width=6)
    st.add_column("IV",      justify="right", width=6)
    st.add_column("Gross%",  justify="right", width=8)
    st.add_column("Net%",    justify="right", width=8)
    st.add_column("Result",  width=6)

    sample = pd.concat([wins_df.head(10), loss_df.head(5)])
    for _, row in sample.iterrows():
        rs = "green" if row['Result'] == 'WIN' else "red"
        date_str = (row['Date'].strftime('%Y-%m-%d')
                    if hasattr(row['Date'], 'strftime') else row['Date'])
        st.add_row(
            date_str,
            row['Direction'],
            f"${row['Entry_Price']:.1f}",
            f"${row['Target_Price']:.1f}",
            f"${row['Stop_Price']:.1f}",
            f"${row['Exit_Price']:.1f}",
            row['Exit_Type'],
            f"{row['ATR']:.2f}",
            f"{row['IV_Entry']:.0%}",
            f"{row['Gross_PnL_Pct']:+.3f}%",
            f"{row['Net_PnL_Pct']:+.3f}%",
            f"[{rs}]{row['Result']}[/{rs}]",
        )
    console.print(st)
    console.print()

    # ---- Save ----
    Path("results").mkdir(exist_ok=True)
    out = f"results/cfd_backtest_stop{stop_mult}.csv"
    df.to_csv(out, index=False)
    console.print(f"[green]Full results saved to: {out}[/green]")
    console.print()


# ---------------------------------------------------------------------------
# Sweep results printer (comparison across stop_mult values)
# ---------------------------------------------------------------------------

def print_sweep_results(sweep_data, spread_cost):
    console.print("=" * 80)
    console.print(
        f"[bold white]SWEEP: STOP MULTIPLIER COMPARISON  "
        f"(spread_cost={spread_cost} pts)[/bold white]"
    )
    console.print("=" * 80)
    console.print()

    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Stop Mult",  width=12)
    t.add_column("Trades",     justify="right", width=8)
    t.add_column("Win Rate",   justify="right", width=10)
    t.add_column("Target %",   justify="right", width=10)
    t.add_column("Stop %",     justify="right", width=9)
    t.add_column("EOD %",      justify="right", width=9)
    t.add_column("Avg Win",    justify="right", width=10)
    t.add_column("Avg Loss",   justify="right", width=10)
    t.add_column("EV/trade",   justify="right", width=11)
    t.add_column("CAGR",       justify="right", width=9)

    for sm, df in sweep_data:
        if df is None or df.empty:
            t.add_row(f"{sm}x", "N/A", *[""] * 8)
            continue

        df_c = df.copy()
        df_c['Date'] = pd.to_datetime(df_c['Date'])
        years = (df_c['Date'].max() - df_c['Date'].min()).days / 365.25
        eq    = calc_equity_curve(df_c)
        final = eq[-1]
        cagr  = (pow(final / STARTING_CAP, 1 / years) - 1) * 100 if years > 0 else 0.0

        wr   = (df_c['Result'] == 'WIN').mean() * 100
        ev   = df_c['Net_PnL_Pct'].mean()
        tgt  = (df_c['Exit_Type'] == 'TARGET').mean() * 100
        stp  = (df_c['Exit_Type'] == 'STOP').mean() * 100
        eod  = (df_c['Exit_Type'] == 'EOD').mean() * 100
        wins = df_c[df_c['Result'] == 'WIN']['Net_PnL_Pct']
        loss = df_c[df_c['Result'] == 'LOSS']['Net_PnL_Pct']

        ev_col   = 'green' if ev   >= 0 else 'red'
        cagr_col = 'green' if cagr >= 0 else 'red'

        t.add_row(
            f"{sm}x ATR",
            f"{len(df_c):,}",
            f"{wr:.1f}%",
            f"{tgt:.1f}%",
            f"{stp:.1f}%",
            f"{eod:.1f}%",
            f"{wins.mean():+.3f}%" if len(wins) > 0 else "N/A",
            f"{loss.mean():+.3f}%" if len(loss) > 0 else "N/A",
            f"[{ev_col}]{ev:+.3f}%[/{ev_col}]",
            f"[{cagr_col}]{cagr:+.1f}%[/{cagr_col}]",
        )

    console.print(t)
    console.print()

    # Per-stop IV and expiry breakdowns
    for sm, df in sweep_data:
        if df is None or df.empty:
            continue
        console.print(f"[bold cyan]--- stop_mult={sm}x --- IV regime breakdown ---[/bold cyan]")
        console.print(_iv_breakdown_table(df))
        console.print()
        console.print(f"[bold cyan]--- stop_mult={sm}x --- expiry pattern breakdown ---[/bold cyan]")
        console.print(_expiry_breakdown_table(df))
        console.print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Backtest OvernightFade as a CFD/spread-bet position (linear P&L, no options)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/backtesting/run_backtest_cfd.py\n"
            "  python scripts/backtesting/run_backtest_cfd.py --stop-mult 0.3\n"
            "  python scripts/backtesting/run_backtest_cfd.py --sweep\n"
        ),
    )
    parser.add_argument(
        '--stop-mult', type=float, default=0.2,
        help=(
            "Stop loss as ATR14 multiple (default 0.2). "
            "E.g. 0.2 -> stop placed 0.2 * ATR14 away from entry. "
            "Target is always 0.1 * ATR14 (same as options backtest)."
        ),
    )
    parser.add_argument(
        '--spread-cost', type=float, default=0.04,
        help=(
            "IG round-trip spread cost in SPY points per trade "
            "(default 0.04 = 0.4 IG pts / 10). "
            "Deducted from gross P&L regardless of direction."
        ),
    )
    parser.add_argument(
        '--sweep', action='store_true',
        help=(
            "Run with stop_mult in [0.1, 0.2, 0.3, 0.5] and print a "
            "comparison table. Saves one CSV per stop_mult to results/."
        ),
    )
    args = parser.parse_args()

    console.print("=" * 80)
    console.print(
        "[bold blue]BACKTEST: CFD / SPREAD-BET  "
        "(OvernightFade signal, linear P&L, no options)[/bold blue]"
    )
    console.print("=" * 80)
    console.print()

    config     = load_config()
    tickers    = config.get('tickers', ['SPY'])
    vix_series = load_vix_data()

    if vix_series is None:
        console.print(
            "[yellow]WARNING: No VIX data. "
            "IV regime buckets will use fallback 0.20.[/yellow]"
        )
    else:
        console.print(
            f"[green]VIX data: "
            f"{vix_series.index.min().date()} to "
            f"{vix_series.index.max().date()}[/green]"
        )
    console.print()

    if args.sweep:
        sweep_mults = [0.1, 0.2, 0.3, 0.5]
        console.print(
            f"[cyan]Sweep mode: stop_mult in {sweep_mults}  "
            f"spread_cost={args.spread_cost}[/cyan]"
        )
        console.print()

        sweep_data = []
        for sm in sweep_mults:
            all_dfs = []
            for ticker in tickers:
                df = run_cfd_backtest(
                    ticker, config, vix_series,
                    stop_mult=sm, spread_cost=args.spread_cost,
                )
                if not df.empty:
                    all_dfs.append(df)
            combined = (
                pd.concat(all_dfs, ignore_index=True)
                  .sort_values('Date')
                  .reset_index(drop=True)
                if all_dfs else pd.DataFrame()
            )
            sweep_data.append((sm, combined))

        console.print()
        print_sweep_results(sweep_data, args.spread_cost)

        # Save CSVs
        Path("results").mkdir(exist_ok=True)
        for sm, df in sweep_data:
            if not df.empty:
                out = f"results/cfd_backtest_stop{sm}.csv"
                df.to_csv(out, index=False)
                console.print(f"[green]Saved: {out}[/green]")

    else:
        console.print(f"[cyan]Tickers: {', '.join(tickers)}[/cyan]")
        console.print(
            f"[cyan]stop_mult={args.stop_mult}x ATR  "
            f"spread_cost={args.spread_cost} pts[/cyan]"
        )
        console.print()

        all_dfs = []
        for ticker in tickers:
            df = run_cfd_backtest(
                ticker, config, vix_series,
                stop_mult=args.stop_mult, spread_cost=args.spread_cost,
            )
            if not df.empty:
                all_dfs.append(df)

        if not all_dfs:
            console.print("[red]No results generated.[/red]")
            return

        df = (
            pd.concat(all_dfs, ignore_index=True)
              .sort_values('Date')
              .reset_index(drop=True)
        )
        console.print(f"[green]Total: {len(df):,} trades[/green]\n")
        print_full_results(df, args.stop_mult, args.spread_cost)

    console.print("=" * 80)


if __name__ == "__main__":
    main()
