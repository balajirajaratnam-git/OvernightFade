"""
Backtest: OvernightFade — pure overnight hold (RTH close -> next RTH open)

Tests the FADE_RED signal as a plain overnight equity position with no
intraday management.

STRATEGY
  Signal : RED day (close < open, magnitude > threshold) — go LONG SPY
  Entry  : 16:00 ET bar CLOSE from SPY intraday data (true RTH close)
  Exit   : 09:30 ET bar OPEN from next trading day's SPY intraday data (true RTH open)
  Days   : Mon / Tue / Wed / Thu  (Fridays always excluded)

WHY INTRADAY DATA FOR ENTRY/EXIT
  The daily OHLCV parquet Open/Close represent the EXTENDED-HOURS session
  (pre-market open / after-hours close), NOT the RTH 09:30 open and 16:00 close.
  Median difference: ~$0.33 on a $450 stock, i.e. ~0.07% per leg, ~0.15% per trade.
  Since avg trade EV is ~0.3%, this is a >50% systematic error.  Fixed here.

P&L
  gross_pnl_pct = (next_930_open - rth_close) / rth_close * 100
  net_pnl_pct   = gross_pnl_pct - spread_pct
  WIN           = net_pnl_pct > 0
  PnL_Mult      = net_pnl_pct / 100  (used by equity curve)

EQUITY CURVE
  position_pct = fraction of equity deployed per trade (default: auto half-Kelly)
  equity(n+1)  = equity(n) * (1 + position_pct * PnL_Mult)
  Computed Kelly: f* = p - (1-p)/b  where b = avg_win / avg_loss
  Default uses half-Kelly for conservative compounding.
  starting_capital = $10,000

USAGE
  python scripts/backtesting/run_backtest_overnight.py
  python scripts/backtesting/run_backtest_overnight.py --days mon-tue --min-vix 20
  python scripts/backtesting/run_backtest_overnight.py --days mon-tue --min-vix 20 --position-pct 25
"""
import sys
sys.path.insert(0, 'src')

import argparse
from pathlib import Path

import pandas as pd
import numpy as np
from rich.console import Console
from rich.table import Table

from pricing import load_vix_data

console = Console(highlight=False)

STARTING_CAP   = 10_000
MAG_THRESHOLD  = 0.10   # minimum RED candle magnitude %
INTRADAY_DIR   = Path('data/SPY/intraday')

DOW_NAMES = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri'}

VIX_BUCKETS = [
    (0,    0.15, "VIX < 15"),
    (0.15, 0.20, "VIX 15-20"),
    (0.20, 0.25, "VIX 20-25"),
    (0.25, 0.35, "VIX 25-35"),
    (0.35, 1.0,  "VIX > 35"),
]


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def load_daily(ticker: str) -> pd.DataFrame:
    """Load daily OHLCV; used only for signal detection, not prices."""
    path = f'data/{ticker}/daily_OHLCV.parquet'
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df.sort_index()


# Cache for intraday files to avoid re-loading the same file multiple times
_intraday_cache: dict = {}

def _load_intraday(date_str: str) -> pd.DataFrame | None:
    """Load 1-minute intraday parquet for a given date string (YYYY-MM-DD).
    Returns DataFrame with tz-naive index in America/New_York, or None if missing.
    """
    if date_str in _intraday_cache:
        return _intraday_cache[date_str]
    path = INTRADAY_DIR / f'{date_str}.parquet'
    if not path.exists():
        _intraday_cache[date_str] = None
        return None
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_convert('America/New_York').tz_localize(None)
    _intraday_cache[date_str] = df
    return df


def get_rth_close(date_str: str) -> float | None:
    """Return the 16:00 ET bar Close for the given date."""
    df = _load_intraday(date_str)
    if df is None:
        return None
    bar = df[df.index.time == pd.Timestamp('16:00').time()]
    if bar.empty:
        # Fall back to last RTH bar (15:59 or last bar before 16:01)
        rth = df[(df.index.time >= pd.Timestamp('09:30').time()) &
                 (df.index.time <= pd.Timestamp('16:00').time())]
        if rth.empty:
            return None
        return float(rth.iloc[-1]['Close'])
    return float(bar.iloc[0]['Close'])


def get_rth_open(date_str: str) -> float | None:
    """Return the 09:30 ET bar Open for the given date."""
    df = _load_intraday(date_str)
    if df is None:
        return None
    bar = df[df.index.time == pd.Timestamp('09:30').time()]
    if bar.empty:
        # Fall back to first bar >= 09:30
        rth = df[df.index.time >= pd.Timestamp('09:30').time()]
        if rth.empty:
            return None
        return float(rth.iloc[0]['Open'])
    return float(bar.iloc[0]['Open'])


# ---------------------------------------------------------------------------
# Core backtest
# ---------------------------------------------------------------------------

def run_overnight_backtest(
    ticker: str,
    vix_series: pd.Series,
    spread_pct: float = 0.02,
    min_vix: float = 0.0,
    days_filter: set = None,      # set of dow ints e.g. {0,1,2,3}; None = all Mon-Thu
) -> pd.DataFrame:
    """
    Returns a DataFrame with one row per trade.

    Entry:  16:00 ET bar Close on signal day   (from intraday)
    Exit:   09:30 ET bar Open on next trade day (from intraday)

    Columns:
      Date, DOW, DOW_Name, Entry_Price, Exit_Price,
      VIX, Gross_PnL_Pct, Net_PnL_Pct, PnL_Mult, Result
    """
    df = load_daily(ticker)

    if vix_series is not None:
        vix_idx = pd.to_datetime(vix_series.index).normalize()
        vix_aligned = pd.Series(vix_series.values, index=vix_idx)
        df['VIX'] = vix_aligned.reindex(df.index)
    else:
        df['VIX'] = np.nan

    df['dow'] = df.index.dayofweek

    if days_filter is None:
        days_filter = {0, 1, 2, 3}

    # Build sorted list of dates for next-day lookup
    all_dates = df.index.tolist()
    date_to_next = {}
    for i, d in enumerate(all_dates):
        if i + 1 < len(all_dates):
            date_to_next[d] = all_dates[i + 1]

    trades = []
    skipped_no_intraday = 0

    for date, row in df.iterrows():
        # --- Signal filters ---
        if row['dow'] not in days_filter:
            continue
        if row.get('Direction') != 'RED':
            continue
        if row.get('Magnitude', 0) <= MAG_THRESHOLD:
            continue
        if date not in date_to_next:
            continue

        vix_val = row['VIX'] if not pd.isna(row.get('VIX', np.nan)) else np.nan
        if not pd.isna(vix_val) and vix_val < min_vix:
            continue

        next_date = date_to_next[date]
        date_str  = date.strftime('%Y-%m-%d')
        next_str  = next_date.strftime('%Y-%m-%d')

        # --- Prices from intraday (RTH) ---
        entry = get_rth_close(date_str)
        exit_ = get_rth_open(next_str)

        if entry is None or exit_ is None:
            skipped_no_intraday += 1
            continue

        gross_pct = (exit_ - entry) / entry * 100.0
        net_pct   = gross_pct - spread_pct
        pnl_mult  = net_pct / 100.0
        result    = 'WIN' if net_pct > 0 else 'LOSS'

        trades.append({
            'Date':          date,
            'DOW':           int(row['dow']),
            'DOW_Name':      DOW_NAMES[int(row['dow'])],
            'Entry_Price':   round(entry, 2),
            'Exit_Price':    round(exit_, 2),
            'VIX':           round(vix_val, 2) if not pd.isna(vix_val) else np.nan,
            'Gross_PnL_Pct': round(gross_pct, 4),
            'Net_PnL_Pct':   round(net_pct, 4),
            'PnL_Mult':      round(pnl_mult, 6),
            'Result':        result,
        })

    if skipped_no_intraday > 0:
        console.print(
            f"[yellow]Skipped {skipped_no_intraday} trades: intraday file missing "
            f"(signal or next day)[/yellow]"
        )

    return pd.DataFrame(trades)


# ---------------------------------------------------------------------------
# Kelly calculation and equity curve
# ---------------------------------------------------------------------------

def calc_kelly(df: pd.DataFrame) -> float:
    """
    Full Kelly fraction: f* = p - (1-p)/b
      p = win rate
      b = avg_win / avg_loss  (both positive numbers)
    Returns 0.0 if insufficient data.
    """
    wins = df[df['Result'] == 'WIN']['Net_PnL_Pct']
    loss = df[df['Result'] == 'LOSS']['Net_PnL_Pct']
    if len(wins) == 0 or len(loss) == 0:
        return 0.0
    p   = len(wins) / len(df)
    b   = wins.mean() / abs(loss.mean())
    f   = p - (1 - p) / b
    return max(f, 0.0)


def calc_equity_curve(df: pd.DataFrame,
                      starting_capital: float = STARTING_CAP,
                      position_pct: float = 0.25):
    """
    position_pct: fraction of equity deployed per trade.
      e.g. 0.25 = 25% of equity in SPY overnight, 75% in cash.
    equity(n+1) = equity(n) * (1 + position_pct * PnL_Mult)
    """
    equity = starting_capital
    curve  = []
    for pnl_mult in df['PnL_Mult']:
        equity = max(equity * (1.0 + position_pct * pnl_mult), 1.0)
        curve.append(equity)
    return curve


def _equity_stats(df: pd.DataFrame, position_pct: float = 0.25) -> dict:
    if df.empty:
        return {}
    curve   = calc_equity_curve(df, position_pct=position_pct)
    final   = curve[-1]
    years   = (df['Date'].max() - df['Date'].min()).days / 365.25
    cagr    = (pow(final / STARTING_CAP, 1 / years) - 1) * 100 if years > 0 else 0.0
    series  = pd.Series(curve)
    run_max = series.expanding().max()
    max_dd  = ((series - run_max) / run_max * 100).min()
    return {'final': final, 'cagr': cagr, 'max_dd': max_dd, 'years': years}


# ---------------------------------------------------------------------------
# Table builders
# ---------------------------------------------------------------------------

def _vix_breakdown_table(df: pd.DataFrame) -> Table:
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("VIX Range",  width=14)
    t.add_column("Trades",     justify="right", width=8)
    t.add_column("Win Rate",   justify="right", width=10)
    t.add_column("Avg Win",    justify="right", width=12)
    t.add_column("Avg Loss",   justify="right", width=12)
    t.add_column("EV/trade",   justify="right", width=12)

    for lo, hi, lbl in VIX_BUCKETS:
        sub = df[(df['VIX'] >= lo * 100) & (df['VIX'] < hi * 100)]
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


def _dow_breakdown_table(df: pd.DataFrame, position_pct: float = 0.25) -> Table:
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Day",        width=8)
    t.add_column("Trades",     justify="right", width=8)
    t.add_column("Win Rate",   justify="right", width=10)
    t.add_column("Avg Win",    justify="right", width=12)
    t.add_column("Avg Loss",   justify="right", width=12)
    t.add_column("EV/trade",   justify="right", width=12)
    t.add_column("CAGR",       justify="right", width=10)

    for dow in sorted(df['DOW'].unique()):
        sub  = df[df['DOW'] == dow]
        name = DOW_NAMES.get(dow, str(dow))
        wr   = (sub['Result'] == 'WIN').mean() * 100
        wins = sub[sub['Result'] == 'WIN']['Net_PnL_Pct']
        loss = sub[sub['Result'] == 'LOSS']['Net_PnL_Pct']
        ev   = sub['Net_PnL_Pct'].mean()
        col  = 'green' if ev >= 0 else 'red'
        st   = _equity_stats(sub, position_pct=position_pct)
        cagr_col = 'green' if st.get('cagr', 0) >= 0 else 'red'
        t.add_row(
            name, f"{len(sub):,}", f"{wr:.1f}%",
            f"{wins.mean():+.3f}%" if len(wins) > 0 else "N/A",
            f"{loss.mean():+.3f}%" if len(loss) > 0 else "N/A",
            f"[{col}]{ev:+.3f}%[/{col}]",
            f"[{cagr_col}]{st.get('cagr', 0):+.1f}%[/{cagr_col}]",
        )
    return t


def _year_breakdown_table(df: pd.DataFrame) -> Table:
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Year",      width=8)
    t.add_column("Trades",    justify="right", width=8)
    t.add_column("Win Rate",  justify="right", width=10)
    t.add_column("Avg Win",   justify="right", width=12)
    t.add_column("Avg Loss",  justify="right", width=12)
    t.add_column("EV/trade",  justify="right", width=12)
    t.add_column("Cumul%",    justify="right", width=10)

    df = df.copy()
    df['Year'] = df['Date'].dt.year
    cumul = 0.0
    for yr in sorted(df['Year'].unique()):
        sub  = df[df['Year'] == yr]
        wr   = (sub['Result'] == 'WIN').mean() * 100
        wins = sub[sub['Result'] == 'WIN']['Net_PnL_Pct']
        loss = sub[sub['Result'] == 'LOSS']['Net_PnL_Pct']
        ev   = sub['Net_PnL_Pct'].mean()
        cumul += sub['Net_PnL_Pct'].sum()
        col   = 'green' if ev >= 0 else 'red'
        t.add_row(
            str(yr), f"{len(sub):,}", f"{wr:.1f}%",
            f"{wins.mean():+.3f}%" if len(wins) > 0 else "N/A",
            f"{loss.mean():+.3f}%" if len(loss) > 0 else "N/A",
            f"[{col}]{ev:+.3f}%[/{col}]",
            f"{cumul:+.2f}%",
        )
    return t


# ---------------------------------------------------------------------------
# Full results printer
# ---------------------------------------------------------------------------

def print_full_results(df: pd.DataFrame, spread_pct: float,
                       min_vix: float, days_label: str, out_path: str,
                       position_pct: float = 0.25):
    if df.empty:
        console.print("[red]No trades generated.[/red]")
        return

    df = df.copy()
    df['Date'] = pd.to_datetime(df['Date'])
    wr   = (df['Result'] == 'WIN').mean() * 100
    ev   = df['Net_PnL_Pct'].mean()
    wins = df[df['Result'] == 'WIN']
    loss = df[df['Result'] == 'LOSS']

    # Compute Kelly from actual trade results
    full_kelly = calc_kelly(df)
    half_kelly = full_kelly / 2.0
    years      = (df['Date'].max() - df['Date'].min()).days / 365.25

    # ---- Summary ----
    console.print("=" * 80)
    console.print(
        f"[bold white]RESULTS: OVERNIGHT FADE  "
        f"days={days_label}  spread={spread_pct:.2f}%"
        + (f"  min_vix={min_vix:.0f}" if min_vix > 0 else "")
        + "[/bold white]"
    )
    console.print("[bold white]Prices: 16:00 RTH close -> next-day 09:30 RTH open (intraday)[/bold white]")
    console.print("=" * 80)
    console.print()

    s = Table(show_header=True, header_style="bold cyan")
    s.add_column("Metric",  style="white", width=34)
    s.add_column("Value",   justify="right", width=24)

    s.add_row("Period",
              f"{df['Date'].min().strftime('%Y-%m-%d')} to "
              f"{df['Date'].max().strftime('%Y-%m-%d')}")
    s.add_row("Years",           f"{years:.1f}")
    s.add_row("Total Trades",    f"{len(df):,}")
    s.add_row("Win Rate",        f"{wr:.1f}%")
    s.add_row("EV / trade",      f"{ev:+.3f}%")
    s.add_row("", "")
    s.add_row("Avg win  (net)",
              f"{wins['Net_PnL_Pct'].mean():+.3f}%" if len(wins) > 0 else "N/A")
    s.add_row("Avg loss (net)",
              f"{loss['Net_PnL_Pct'].mean():+.3f}%" if len(loss) > 0 else "N/A")
    s.add_row("Win/Loss ratio",
              f"{wins['Net_PnL_Pct'].mean() / abs(loss['Net_PnL_Pct'].mean()):.2f}"
              if len(wins) > 0 and len(loss) > 0 else "N/A")
    s.add_row("", "")
    s.add_row("Full Kelly (computed)",  f"{full_kelly*100:.1f}%")
    s.add_row("Half Kelly",             f"{half_kelly*100:.1f}%")
    s.add_row("", "")

    # Show equity at multiple allocation levels
    s.add_row("Starting Capital", f"${STARTING_CAP:,.0f}")
    for alloc_pct, label in [
        (0.10,          "10% alloc"),
        (0.25,          "25% alloc"),
        (half_kelly,    "half-Kelly"),
        (full_kelly,    "full-Kelly"),
        (1.00,          "100% alloc"),
    ]:
        eq_st = _equity_stats(df, position_pct=alloc_pct)
        bold  = "[bold]" if abs(alloc_pct - position_pct) < 0.001 else ""
        endb  = "[/bold]" if bold else ""
        col   = "green" if eq_st['cagr'] >= 0 else "red"
        s.add_row(
            f"  {label:<18}  Final equity",
            f"{bold}[{col}]${eq_st['final']:>10,.0f}  CAGR {eq_st['cagr']:+.1f}%  "
            f"MaxDD {eq_st['max_dd']:.1f}%[/{col}]{endb}",
        )

    console.print(s)
    console.print()

    # ---- VIX breakdown ----
    console.print("=" * 80)
    console.print("[bold white]RETURNS BY VIX REGIME[/bold white]")
    console.print("=" * 80)
    console.print()
    console.print(_vix_breakdown_table(df))
    console.print()

    # ---- Day-of-week breakdown ----
    console.print("=" * 80)
    console.print("[bold white]RETURNS BY DAY OF WEEK[/bold white]")
    console.print("=" * 80)
    console.print()
    console.print(_dow_breakdown_table(df, position_pct=position_pct))
    console.print()

    # ---- Year-by-year ----
    console.print("=" * 80)
    console.print("[bold white]YEAR-BY-YEAR BREAKDOWN[/bold white]")
    console.print("=" * 80)
    console.print()
    console.print(_year_breakdown_table(df))
    console.print()

    # ---- Sample trades ----
    console.print("=" * 80)
    console.print("[bold white]SAMPLE TRADES (first 10 wins, first 5 losses)[/bold white]")
    console.print("=" * 80)
    console.print()

    st_tbl = Table(show_header=True, header_style="bold cyan", show_lines=True)
    st_tbl.add_column("Date",      width=12)
    st_tbl.add_column("Day",       width=5)
    st_tbl.add_column("RTH Close", justify="right", width=10)
    st_tbl.add_column("Next 09:30", justify="right", width=10)
    st_tbl.add_column("VIX",       justify="right", width=7)
    st_tbl.add_column("Gross%",    justify="right", width=8)
    st_tbl.add_column("Net%",      justify="right", width=8)
    st_tbl.add_column("Result",    width=6)

    sample = pd.concat([wins.head(10), loss.head(5)])
    for _, row in sample.iterrows():
        rs       = "green" if row['Result'] == 'WIN' else "red"
        date_str = row['Date'].strftime('%Y-%m-%d') if hasattr(row['Date'], 'strftime') else str(row['Date'])
        vix_str  = f"{row['VIX']:.1f}" if not pd.isna(row['VIX']) else "N/A"
        st_tbl.add_row(
            date_str,
            row['DOW_Name'],
            f"${row['Entry_Price']:.2f}",
            f"${row['Exit_Price']:.2f}",
            vix_str,
            f"{row['Gross_PnL_Pct']:+.3f}%",
            f"{row['Net_PnL_Pct']:+.3f}%",
            f"[{rs}]{row['Result']}[/{rs}]",
        )
    console.print(st_tbl)
    console.print()

    # ---- Save ----
    Path("results").mkdir(exist_ok=True)
    df.to_csv(out_path, index=False)
    console.print(f"[green]Full results saved to: {out_path}[/green]")
    console.print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Backtest OvernightFade (RTH 16:00 close -> next 09:30 open)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/backtesting/run_backtest_overnight.py\n"
            "  python scripts/backtesting/run_backtest_overnight.py --days mon-tue\n"
            "  python scripts/backtesting/run_backtest_overnight.py --min-vix 20\n"
            "  python scripts/backtesting/run_backtest_overnight.py --days mon-tue --min-vix 20\n"
        ),
    )
    parser.add_argument(
        '--spread-pct', type=float, default=0.02,
        help="Round-trip spread cost as %% of entry price (default 0.02%%).",
    )
    parser.add_argument(
        '--min-vix', type=float, default=0.0,
        help="Minimum VIX to take a trade (default 0 = no filter).",
    )
    parser.add_argument(
        '--days', type=str, default='all',
        choices=['all', 'mon-tue', 'wed-thu'],
        help=(
            "Which entry days to include: "
            "'all' = Mon/Tue/Wed/Thu (default), "
            "'mon-tue' = Mon and Tue only, "
            "'wed-thu' = Wed and Thu only."
        ),
    )
    parser.add_argument(
        '--position-pct', type=float, default=None,
        help=(
            "Fraction of equity deployed per trade as a percentage "
            "(e.g. 25 = 25%%).  Default: auto half-Kelly from trade results."
        ),
    )
    args = parser.parse_args()

    days_map = {
        'all':     {0, 1, 2, 3},
        'mon-tue': {0, 1},
        'wed-thu': {2, 3},
    }
    days_filter = days_map[args.days]

    vix_filter_tag = f"_vix{args.min_vix:.0f}" if args.min_vix > 0 else ""
    out_path = f"results/overnight_backtest_{args.days}{vix_filter_tag}.csv"

    console.print("=" * 80)
    console.print(
        "[bold blue]BACKTEST: OVERNIGHT FADE  "
        "(RTH 16:00 close -> next 09:30 open, no stop, no target)[/bold blue]"
    )
    console.print("=" * 80)
    console.print()

    vix_series = load_vix_data()
    if vix_series is None:
        console.print("[yellow]WARNING: No VIX data loaded.[/yellow]")
    else:
        console.print(
            f"[green]VIX data: "
            f"{pd.to_datetime(vix_series.index.min()).date()} to "
            f"{pd.to_datetime(vix_series.index.max()).date()}[/green]"
        )
    console.print()

    df = run_overnight_backtest(
        ticker='SPY',
        vix_series=vix_series,
        spread_pct=args.spread_pct,
        min_vix=args.min_vix,
        days_filter=days_filter,
    )
    console.print(
        f"[green]SPY: {len(df)} trades  "
        f"(days={args.days}, spread={args.spread_pct}%"
        + (f", min_vix={args.min_vix:.0f}" if args.min_vix > 0 else "")
        + ")[/green]"
    )
    console.print()

    # Resolve position_pct: explicit flag overrides auto half-Kelly
    if args.position_pct is not None:
        position_pct = args.position_pct / 100.0
    else:
        full_kelly   = calc_kelly(df)
        position_pct = full_kelly / 2.0   # default: half-Kelly

    print_full_results(
        df,
        spread_pct=args.spread_pct,
        min_vix=args.min_vix,
        days_label=args.days,
        out_path=out_path,
        position_pct=position_pct,
    )


if __name__ == '__main__':
    main()
