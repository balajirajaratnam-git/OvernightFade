"""
Analysis: Final 2-hour intraday reversal patterns (14:00-16:00 ET)

Hypothesis: After a directional first segment (14:00 to split-point), the
remaining segment to the 16:00 close tends to REVERSE direction.

Splits tested:
  A: 60/60   seg1=14:00-14:59   seg2=15:00-16:00
  B: 90/30   seg1=14:00-15:29   seg2=15:30-16:00
  C: 105/15  seg1=14:00-15:44   seg2=15:45-16:00

Reversal trade:
  Entry:     open of first bar in seg2 (15:00, 15:30, or 15:45)
  Exit:      close of 16:00 bar
  Direction: LONG if seg1 DOWN, SHORT if seg1 UP
  Cost:      $0.06/share round-trip (0.6 IG US 500 cash pts equivalent)
  WIN:       net_pts > 0

Filters:
  all / prev-RED / prev-GREEN / VIX>20 /
  by DOW (Mon-Fri) / by VIX regime (<15/15-20/20-25/25-35/>35)

Regime stability: year-by-year for best split+filter combination.

USAGE
  python scripts/analysis/analyze_final_2hours_patterns.py
"""
import sys
import io
sys.path.insert(0, 'src')
# Force UTF-8 output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import math
from pathlib import Path

import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

from pricing import load_vix_data

console = Console(highlight=False)

INTRADAY_DIR = Path('data/SPY/intraday')
SPREAD       = 0.06      # $0.06/share round-trip (0.6 IG US 500 cash pts in SPY terms)

# Split definitions: (name, seg1_last_bar_time, seg2_first_bar_time, label)
SPLITS = [
    ('A', '14:59', '15:00', '60/60   (14:00-14:59 / 15:00-16:00)'),
    ('B', '15:29', '15:30', '90/30   (14:00-15:29 / 15:30-16:00)'),
    ('C', '15:44', '15:45', '105/15  (14:00-15:44 / 15:45-16:00)'),
]

DOW_NAMES   = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri'}
VIX_REGIMES = [
    (0,   15,  'VIX < 15'),
    (15,  20,  'VIX 15-20'),
    (20,  25,  'VIX 20-25'),
    (25,  35,  'VIX 25-35'),
    (35, 999,  'VIX > 35'),
]


# ---------------------------------------------------------------------------
# Chi-squared (2×2, Yates correction) without scipy
# ---------------------------------------------------------------------------

def _chi2_2x2(a: int, b: int, c: int, d: int):
    """
    Chi-squared with Yates continuity correction for 2×2 contingency table.
      [[a, b],
       [c, d]]
    Returns (chi2_stat, p_value) or (nan, nan) if not computable.
    """
    n = a + b + c + d
    if n == 0:
        return math.nan, math.nan
    r1, r2 = a + b, c + d
    c1, c2 = a + c, b + d
    if r1 == 0 or r2 == 0 or c1 == 0 or c2 == 0:
        return math.nan, math.nan
    # Yates-corrected chi-squared
    num  = n * (abs(a * d - b * c) - n / 2.0) ** 2
    denom = r1 * r2 * c1 * c2
    chi2 = num / denom
    # p-value: P(X² > chi2) for df=1 = erfc(sqrt(chi2/2))
    p = math.erfc(math.sqrt(chi2 / 2.0))
    return chi2, p


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_daily_spy() -> pd.DataFrame:
    df = pd.read_parquet('data/SPY/daily_OHLCV.parquet')
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df.sort_index()


_intra_cache: dict = {}

def _load_intraday(date_str: str) -> pd.DataFrame | None:
    if date_str in _intra_cache:
        return _intra_cache[date_str]
    p = INTRADAY_DIR / f'{date_str}.parquet'
    if not p.exists():
        _intra_cache[date_str] = None
        return None
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_convert('America/New_York').tz_localize(None)
    _intra_cache[date_str] = df
    return df


def _bar(df: pd.DataFrame, t_str: str):
    """Return the row at HH:MM (first matching bar), or None."""
    t = pd.Timestamp(t_str).time()
    rows = df[df.index.time == t]
    return rows.iloc[0] if not rows.empty else None


# ---------------------------------------------------------------------------
# Dataset construction — one row per trading day
# ---------------------------------------------------------------------------

def build_dataset() -> pd.DataFrame:
    daily = load_daily_spy()

    vix = load_vix_data()
    if vix is not None:
        vix_idx = pd.to_datetime(vix.index).normalize()
        vix_s   = pd.Series(vix.values, index=vix_idx).reindex(daily.index)
        daily['_vix'] = vix_s
    else:
        daily['_vix'] = np.nan

    # Previous trading day direction (RED / GREEN)
    prev_dir = daily['Direction'].shift(1)

    date_files = sorted(INTRADAY_DIR.glob('*.parquet'))
    console.print(f"[cyan]Processing {len(date_files)} intraday files...[/cyan]")

    rows = []
    for p in date_files:
        date_str = p.stem
        d = pd.Timestamp(date_str)
        if d not in daily.index:
            continue

        intra = _load_intraday(date_str)
        if intra is None:
            continue

        # Anchor bars required for all splits
        bar_1400 = _bar(intra, '14:00')
        bar_1600 = _bar(intra, '16:00')
        if bar_1400 is None or bar_1600 is None:
            continue

        seg1_open  = float(bar_1400['Open'])
        exit_price = float(bar_1600['Close'])

        row = {
            'date':     d,
            'dow':      d.dayofweek,
            'vix':      float(daily.loc[d, '_vix']) if not pd.isna(daily.loc[d, '_vix']) else np.nan,
            'prev_dir': (str(prev_dir.loc[d])
                         if d in prev_dir.index and not pd.isna(prev_dir.loc[d])
                         else None),
        }

        any_valid = False
        for sname, end_t, start_t, _ in SPLITS:
            bar_end   = _bar(intra, end_t)    # last bar of seg1
            bar_start = _bar(intra, start_t)  # first bar of seg2

            if bar_end is None or bar_start is None:
                row[f's{sname}_ok'] = False
                continue

            seg1_close = float(bar_end['Close'])
            seg2_open  = float(bar_start['Open'])

            # Segment directions
            seg1_pts = seg1_close - seg1_open
            seg1_pct = seg1_pts / seg1_open * 100.0
            seg1_dir = 'UP' if seg1_pts > 0 else 'DOWN'

            seg2_pts = exit_price - seg2_open
            seg2_pct = seg2_pts / seg2_open * 100.0
            seg2_dir = 'UP' if seg2_pts > 0 else 'DOWN'

            # Reversal trade: go opposite of seg1
            t_mult    = 1 if seg1_dir == 'DOWN' else -1   # +1=LONG, -1=SHORT
            gross_pts = t_mult * (exit_price - seg2_open)
            gross_pct = gross_pts / seg2_open * 100.0
            net_pts   = gross_pts - SPREAD
            net_pct   = net_pts   / seg2_open * 100.0

            row.update({
                f's{sname}_ok':         True,
                f's{sname}_seg1_dir':   seg1_dir,
                f's{sname}_seg1_pts':   round(seg1_pts, 4),
                f's{sname}_seg1_pct':   round(seg1_pct, 4),
                f's{sname}_seg2_dir':   seg2_dir,
                f's{sname}_seg2_pts':   round(seg2_pts, 4),
                f's{sname}_seg2_pct':   round(seg2_pct, 4),
                f's{sname}_entry':      round(seg2_open,  2),
                f's{sname}_exit':       round(exit_price, 2),
                f's{sname}_trade_dir':  'LONG' if t_mult == 1 else 'SHORT',
                f's{sname}_gross_pts':  round(gross_pts, 4),
                f's{sname}_gross_pct':  round(gross_pct, 4),
                f's{sname}_net_pts':    round(net_pts,   4),
                f's{sname}_net_pct':    round(net_pct,   4),
                f's{sname}_win':        net_pts > 0,
            })
            any_valid = True

        if any_valid:
            rows.append(row)

    df = pd.DataFrame(rows)
    console.print(f"[green]Dataset: {len(df)} trading days with at least one valid split.[/green]\n")
    return df


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------

def _valid(df: pd.DataFrame, sname: str) -> pd.DataFrame:
    ok_col = f's{sname}_ok'
    if ok_col not in df.columns:
        return df.iloc[:0]
    return df[df[ok_col] == True]


def pattern_stats(sub: pd.DataFrame, sname: str) -> dict | None:
    """2×2 contingency matrix (first_dir → second_dir) and chi-squared."""
    v = _valid(sub, sname)
    if v.empty:
        return None
    s1 = f's{sname}_seg1_dir'
    s2 = f's{sname}_seg2_dir'
    uu = int(((v[s1] == 'UP')   & (v[s2] == 'UP')).sum())
    ud = int(((v[s1] == 'UP')   & (v[s2] == 'DOWN')).sum())
    du = int(((v[s1] == 'DOWN') & (v[s2] == 'UP')).sum())
    dd = int(((v[s1] == 'DOWN') & (v[s2] == 'DOWN')).sum())
    n  = uu + ud + du + dd
    if n == 0:
        return None
    rev  = ud + du
    chi2, p = _chi2_2x2(uu, ud, du, dd)
    return {
        'n':        n,
        'UU':       uu,  'UD': ud,
        'DU':       du,  'DD': dd,
        'rev':      rev,
        'cont':     uu + dd,
        'rev_pct':  rev / n * 100,
        'chi2':     chi2,
        'p':        p,
    }


def trade_stats(sub: pd.DataFrame, sname: str) -> dict | None:
    """Win rate, EV, avg win/loss for the reversal trade."""
    v = _valid(sub, sname)
    if v.empty:
        return None
    net_col = f's{sname}_net_pts'
    pct_col = f's{sname}_net_pct'
    win_col = f's{sname}_win'
    wins = v[v[win_col] == True]
    loss = v[v[win_col] == False]
    n    = len(v)
    wr   = len(wins) / n * 100
    return {
        'n':    n,
        'wr':   wr,
        'ev':   v[net_col].mean(),
        'evp':  v[pct_col].mean(),
        'aw':   wins[net_col].mean() if len(wins) > 0 else math.nan,
        'al':   loss[net_col].mean() if len(loss) > 0 else math.nan,
        'awp':  wins[pct_col].mean() if len(wins) > 0 else math.nan,
        'alp':  loss[pct_col].mean() if len(loss) > 0 else math.nan,
        'nw':   len(wins),
        'nl':   len(loss),
    }


# ---------------------------------------------------------------------------
# Rich table printers
# ---------------------------------------------------------------------------

def _fmt_p(p) -> str:
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return "N/A"
    return f"{p:.4f}"


def _fmt_f(v, fmt='+.4f', na='N/A') -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return na
    return format(v, fmt)


def print_matrix_section(df: pd.DataFrame, sname: str, label: str,
                          filters: list[tuple[str, pd.DataFrame]]) -> None:
    """Print 2×2 matrix table for one split across multiple filters."""
    t = Table(
        title=f"[bold]Split {sname}: {label}  —  Pattern Matrix[/bold]",
        show_header=True, header_style="bold cyan", show_lines=False,
    )
    t.add_column("Filter",    width=16)
    t.add_column("N",         justify="right", width=6)
    t.add_column("UP->UP",    justify="right", width=9)
    t.add_column("UP->DOWN",  justify="right", width=9)
    t.add_column("DOWN->UP",  justify="right", width=9)
    t.add_column("DOWN->DOWN",justify="right", width=10)
    t.add_column("Rev%",      justify="right", width=7)
    t.add_column("p-value",   justify="right", width=8)

    for fname, sub in filters:
        s = pattern_stats(sub, sname)
        if s is None:
            t.add_row(fname, "—", "—", "—", "—", "—", "—", "—")
            continue
        rc = "green" if s['rev_pct'] > 55 else ("red" if s['rev_pct'] < 45 else "yellow")
        pc = "green" if not math.isnan(s['p']) and s['p'] < 0.05 else "white"
        t.add_row(
            fname,
            str(s['n']),
            f"{s['UU']} ({s['UU']/s['n']*100:.0f}%)",
            f"{s['UD']} ({s['UD']/s['n']*100:.0f}%)",
            f"{s['DU']} ({s['DU']/s['n']*100:.0f}%)",
            f"{s['DD']} ({s['DD']/s['n']*100:.0f}%)",
            f"[{rc}]{s['rev_pct']:.1f}%[/{rc}]",
            f"[{pc}]{_fmt_p(s['p'])}[/{pc}]",
        )
    console.print(t)
    console.print()


def print_trade_section(df: pd.DataFrame, sname: str, label: str,
                         filters: list[tuple[str, pd.DataFrame]]) -> None:
    """Print trade stats table for one split across multiple filters."""
    t = Table(
        title=f"[bold]Split {sname}: {label}  —  Reversal Trade Stats[/bold]",
        show_header=True, header_style="bold cyan", show_lines=False,
    )
    t.add_column("Filter",     width=16)
    t.add_column("N",          justify="right", width=6)
    t.add_column("Win%",       justify="right", width=7)
    t.add_column("EV pts",     justify="right", width=9)
    t.add_column("EV %",       justify="right", width=9)
    t.add_column("AvgW pts",   justify="right", width=9)
    t.add_column("AvgL pts",   justify="right", width=9)

    for fname, sub in filters:
        s = trade_stats(sub, sname)
        if s is None:
            t.add_row(fname, "—", "—", "—", "—", "—", "—")
            continue
        ev_c  = "green" if s['ev']  > 0 else "red"
        wr_c  = "green" if s['wr']  > 55 else ("red" if s['wr'] < 45 else "white")
        t.add_row(
            fname,
            str(s['n']),
            f"[{wr_c}]{s['wr']:.1f}%[/{wr_c}]",
            f"[{ev_c}]{s['ev']:+.4f}[/{ev_c}]",
            f"[{ev_c}]{s['evp']:+.4f}%[/{ev_c}]",
            _fmt_f(s['aw'], '+.4f'),
            _fmt_f(s['al'], '+.4f'),
        )
    console.print(t)
    console.print()


def print_dow_section(df: pd.DataFrame, sname: str, label: str) -> None:
    """Trade stats by day of week for one split."""
    t = Table(
        title=f"[bold]Split {sname} — By Day of Week[/bold]",
        show_header=True, header_style="bold cyan",
    )
    t.add_column("Day",      width=6)
    t.add_column("N",        justify="right", width=6)
    t.add_column("Win%",     justify="right", width=7)
    t.add_column("EV pts",   justify="right", width=9)
    t.add_column("EV %",     justify="right", width=9)
    t.add_column("AvgW pts", justify="right", width=9)
    t.add_column("AvgL pts", justify="right", width=9)

    for dow in range(5):
        sub = df[df['dow'] == dow]
        s   = trade_stats(sub, sname)
        name = DOW_NAMES.get(dow, str(dow))
        if s is None or s['n'] == 0:
            t.add_row(name, "0", "—", "—", "—", "—", "—")
            continue
        ev_c = "green" if s['ev'] > 0 else "red"
        wr_c = "green" if s['wr'] > 55 else ("red" if s['wr'] < 45 else "white")
        t.add_row(
            name, str(s['n']),
            f"[{wr_c}]{s['wr']:.1f}%[/{wr_c}]",
            f"[{ev_c}]{s['ev']:+.4f}[/{ev_c}]",
            f"[{ev_c}]{s['evp']:+.4f}%[/{ev_c}]",
            _fmt_f(s['aw'], '+.4f'),
            _fmt_f(s['al'], '+.4f'),
        )
    console.print(t)
    console.print()


def print_vix_section(df: pd.DataFrame, sname: str) -> None:
    """Trade stats by VIX regime for one split."""
    t = Table(
        title=f"[bold]Split {sname} — By VIX Regime[/bold]",
        show_header=True, header_style="bold cyan",
    )
    t.add_column("VIX",      width=12)
    t.add_column("N",        justify="right", width=6)
    t.add_column("Win%",     justify="right", width=7)
    t.add_column("EV pts",   justify="right", width=9)
    t.add_column("EV %",     justify="right", width=9)
    t.add_column("AvgW pts", justify="right", width=9)
    t.add_column("AvgL pts", justify="right", width=9)

    for lo, hi, lbl in VIX_REGIMES:
        sub = df[(df['vix'] >= lo) & (df['vix'] < hi)]
        s   = trade_stats(sub, sname)
        if s is None or s['n'] == 0:
            t.add_row(lbl, "0", "—", "—", "—", "—", "—")
            continue
        ev_c = "green" if s['ev'] > 0 else "red"
        wr_c = "green" if s['wr'] > 55 else ("red" if s['wr'] < 45 else "white")
        t.add_row(
            lbl, str(s['n']),
            f"[{wr_c}]{s['wr']:.1f}%[/{wr_c}]",
            f"[{ev_c}]{s['ev']:+.4f}[/{ev_c}]",
            f"[{ev_c}]{s['evp']:+.4f}%[/{ev_c}]",
            _fmt_f(s['aw'], '+.4f'),
            _fmt_f(s['al'], '+.4f'),
        )
    console.print(t)
    console.print()


def print_year_stability(df: pd.DataFrame, sname: str, filter_name: str,
                          sub_df: pd.DataFrame, overall_ev: float) -> None:
    """
    Year-by-year breakdown + 2-year rolling window stability check.
    Flags years/periods where result opposes the overall direction.
    """
    console.print(
        f"[bold white]=== REGIME STABILITY: Split {sname} + {filter_name} "
        f"(overall EV pts = {overall_ev:+.4f}) ===[/bold white]"
    )

    # Year-by-year
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Year",    width=6)
    t.add_column("N",       justify="right", width=6)
    t.add_column("Win%",    justify="right", width=7)
    t.add_column("EV pts",  justify="right", width=9)
    t.add_column("EV %",    justify="right", width=9)
    t.add_column("Flag",    width=10)

    sub_df = sub_df.copy()
    sub_df['year'] = sub_df['date'].dt.year
    yearly_evs = {}

    for yr in sorted(sub_df['year'].unique()):
        ysub = sub_df[sub_df['year'] == yr]
        s    = trade_stats(ysub, sname)
        if s is None or s['n'] == 0:
            t.add_row(str(yr), "0", "—", "—", "—", "—")
            continue
        ev_c  = "green" if s['ev'] > 0 else "red"
        wr_c  = "green" if s['wr'] > 55 else ("red" if s['wr'] < 45 else "white")
        flag  = ""
        if overall_ev > 0 and s['ev'] < 0:
            flag = "[red]FLIP[/red]"
        elif overall_ev < 0 and s['ev'] > 0:
            flag = "[red]FLIP[/red]"
        t.add_row(
            str(yr), str(s['n']),
            f"[{wr_c}]{s['wr']:.1f}%[/{wr_c}]",
            f"[{ev_c}]{s['ev']:+.4f}[/{ev_c}]",
            f"[{ev_c}]{s['evp']:+.4f}%[/{ev_c}]",
            flag,
        )
        yearly_evs[yr] = s['ev']

    console.print(t)
    console.print()

    # 2-year rolling windows
    years = sorted(yearly_evs.keys())
    console.print("[bold cyan]2-year rolling windows:[/bold cyan]")
    for i in range(len(years) - 1):
        y1, y2 = years[i], years[i + 1]
        pair   = sub_df[sub_df['year'].isin([y1, y2])]
        s      = trade_stats(pair, sname)
        if s is None or s['n'] == 0:
            continue
        flag   = ""
        if overall_ev > 0 and s['ev'] < 0:
            flag = "  [red]<-- OPPOSITE[/red]"
        elif overall_ev < 0 and s['ev'] > 0:
            flag = "  [red]<-- OPPOSITE[/red]"
        col = "green" if s['ev'] > 0 else "red"
        console.print(
            f"  {y1}-{y2}:  N={s['n']}  WR={s['wr']:.1f}%  "
            f"EV=[{col}]{s['ev']:+.4f}[/{col}]{flag}"
        )
    console.print()


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

def save_csvs(df: pd.DataFrame, all_filters: list[tuple[str, pd.DataFrame]]) -> None:
    Path("results").mkdir(exist_ok=True)

    # 1. Per-trade (per-day) data
    csv_path = "results/final_2hours_patterns.csv"
    df.to_csv(csv_path, index=False)
    console.print(f"[green]Saved: {csv_path}  ({len(df)} rows)[/green]")

    # 2. Matrix + trade stats summary
    mat_rows = []
    for sname, _, _, slabel in SPLITS:
        for fname, sub in all_filters:
            ps = pattern_stats(sub, sname)
            ts = trade_stats(sub, sname)
            row = {
                'split': sname,
                'split_label': slabel,
                'filter': fname,
                'n_pattern': ps['n']   if ps else 0,
                'UU':        ps['UU']  if ps else None,
                'UD':        ps['UD']  if ps else None,
                'DU':        ps['DU']  if ps else None,
                'DD':        ps['DD']  if ps else None,
                'rev_pct':   ps['rev_pct'] if ps else None,
                'chi2':      ps['chi2']    if ps else None,
                'p_value':   ps['p']       if ps else None,
                'n_trades':  ts['n']   if ts else 0,
                'win_rate':  ts['wr']  if ts else None,
                'ev_pts':    ts['ev']  if ts else None,
                'ev_pct':    ts['evp'] if ts else None,
                'avg_win_pts': ts['aw']  if ts else None,
                'avg_loss_pts': ts['al'] if ts else None,
            }
            mat_rows.append(row)

    mat_df = pd.DataFrame(mat_rows)
    mat_path = "results/final_2hours_matrices.csv"
    mat_df.to_csv(mat_path, index=False)
    console.print(f"[green]Saved: {mat_path}  ({len(mat_df)} rows)[/green]")
    console.print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    console.print("=" * 80)
    console.print(
        "[bold blue]ANALYSIS: FINAL 2-HOUR INTRADAY REVERSAL PATTERNS (14:00-16:00 ET)[/bold blue]"
    )
    console.print("=" * 80)
    console.print()

    df = build_dataset()

    # ---- Filter definitions ----
    prev_red   = df[df['prev_dir'] == 'RED']
    prev_green = df[df['prev_dir'] == 'GREEN']
    vix_gt20   = df[df['vix'] > 20]

    main_filters: list[tuple[str, pd.DataFrame]] = [
        ("All days",    df),
        ("Prev RED",    prev_red),
        ("Prev GREEN",  prev_green),
        ("VIX > 20",    vix_gt20),
    ]

    dow_filters: list[tuple[str, pd.DataFrame]] = [
        (DOW_NAMES[d], df[df['dow'] == d]) for d in range(5)
    ]

    vix_filters: list[tuple[str, pd.DataFrame]] = [
        (lbl, df[(df['vix'] >= lo) & (df['vix'] < hi)])
        for lo, hi, lbl in VIX_REGIMES
    ]

    # ---- Report per split ----
    for sname, end_t, start_t, slabel in SPLITS:
        console.print("=" * 80)
        console.print(f"[bold white]SPLIT {sname}: {slabel}[/bold white]")
        console.print("=" * 80)
        console.print()

        # 2×2 matrices
        print_matrix_section(df, sname, slabel, main_filters)

        # Reversal trade stats — main filters
        print_trade_section(df, sname, slabel, main_filters)

        # By day of week
        print_dow_section(df, sname, slabel)

        # By VIX regime
        print_vix_section(df, sname)

    # ---- Find best split+filter by EV ----
    console.print("=" * 80)
    console.print("[bold white]BEST SPLIT + FILTER COMBINATION[/bold white]")
    console.print("=" * 80)
    console.print()

    best_ev       = -math.inf
    best_sname    = None
    best_fname    = None
    best_sub      = None
    best_ts       = None

    # Search main filters × splits
    all_combos: list[tuple[str, pd.DataFrame]] = main_filters + dow_filters + vix_filters

    summary_rows = []
    for sname, _, _, slabel in SPLITS:
        for fname, sub in all_combos:
            ts = trade_stats(sub, sname)
            if ts is None or ts['n'] < 50:   # require at least 50 trades
                continue
            summary_rows.append((sname, fname, ts['n'], ts['wr'], ts['ev'], ts['evp']))
            if ts['ev'] > best_ev:
                best_ev    = ts['ev']
                best_sname = sname
                best_fname = fname
                best_sub   = sub
                best_ts    = ts

    # Print top-10 by EV
    summary_rows.sort(key=lambda x: x[4], reverse=True)
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Split",   width=7)
    t.add_column("Filter",  width=16)
    t.add_column("N",       justify="right", width=6)
    t.add_column("Win%",    justify="right", width=7)
    t.add_column("EV pts",  justify="right", width=9)
    t.add_column("EV %",    justify="right", width=9)

    for sname_r, fname_r, n_r, wr_r, ev_r, evp_r in summary_rows[:15]:
        ev_c = "green" if ev_r > 0 else "red"
        wr_c = "green" if wr_r > 55 else ("red" if wr_r < 45 else "white")
        t.add_row(
            sname_r, fname_r, str(n_r),
            f"[{wr_c}]{wr_r:.1f}%[/{wr_c}]",
            f"[{ev_c}]{ev_r:+.4f}[/{ev_c}]",
            f"[{ev_c}]{evp_r:+.4f}%[/{ev_c}]",
        )
    console.print(t)
    console.print()

    # ---- Regime stability for best combo ----
    if best_sname is not None:
        console.print(
            f"[bold cyan]Best combination: Split {best_sname} + {best_fname}  "
            f"EV={best_ev:+.4f} pts  WR={best_ts['wr']:.1f}%  N={best_ts['n']}[/bold cyan]"
        )
        console.print()
        print_year_stability(df, best_sname, best_fname, best_sub, best_ev)
    else:
        console.print("[yellow]No combination had >= 50 trades.[/yellow]")

    # ---- Save CSVs ----
    console.print("=" * 80)
    console.print("[bold white]SAVING OUTPUT FILES[/bold white]")
    console.print("=" * 80)
    console.print()
    save_csvs(df, main_filters + dow_filters + vix_filters)


if __name__ == '__main__':
    main()
