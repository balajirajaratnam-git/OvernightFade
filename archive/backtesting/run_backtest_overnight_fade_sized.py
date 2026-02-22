"""
Backtest: OVERNIGHT FADE with Premium-Budget Position Sizing

Wraps the baseline overnight fade strategy (run_backtest_overnight_fade.py)
with proper position sizing to control drawdowns.

Position sizing rule:
  premium_budget = equity * risk_pct
  size = floor(premium_budget / option_premium)
  P&L = size * option_premium * net_pnl_pct

Caps:
  - Per-trade: risk_pct of equity (default 1%)
  - Daily: max 2 trades OR max daily_cap_pct of equity (default 2%)
  - Weekly: max weekly_cap_pct of equity (default 7%)

Compares multiple risk_pct values: 0.5%, 1.0%, 1.5%
"""
import sys
sys.path.insert(0, 'src')

import pandas as pd
import numpy as np
import math
from rich.console import Console
from rich.table import Table

console = Console()


def run_sized_equity_curve(trade_log_path, starting_equity=10000,
                           risk_pct=0.01, daily_cap_pct=0.02,
                           weekly_cap_pct=0.07):
    """
    Run equity curve simulation with premium-budget sizing.

    Args:
        trade_log_path: path to baseline trade CSV
        starting_equity: starting capital
        risk_pct: fraction of equity to risk per trade (premium budget)
        daily_cap_pct: max fraction of equity in premiums per day
        weekly_cap_pct: max fraction of equity in premiums per week

    Returns:
        DataFrame with equity curve and sizing details
    """
    df = pd.read_csv(trade_log_path)
    df['Date_dt'] = pd.to_datetime(df['Date'])

    equity = starting_equity
    peak_equity = starting_equity

    results = []
    daily_spent = {}    # date -> total premium spent
    weekly_spent = {}   # iso_week -> total premium spent

    for _, row in df.iterrows():
        trade_date = row['Date_dt'].date()
        iso_week = row['Date_dt'].isocalendar()[:2]  # (year, week)
        entry_premium = row['Entry_Premium']  # per-unit premium

        if entry_premium <= 0:
            continue

        # --- Premium budget ---
        premium_budget = equity * risk_pct

        # --- Daily cap check ---
        day_key = str(trade_date)
        already_spent_today = daily_spent.get(day_key, 0.0)
        daily_remaining = max(equity * daily_cap_pct - already_spent_today, 0.0)
        premium_budget = min(premium_budget, daily_remaining)

        # --- Weekly cap check ---
        week_key = f"{iso_week[0]}-W{iso_week[1]:02d}"
        already_spent_week = weekly_spent.get(week_key, 0.0)
        weekly_remaining = max(equity * weekly_cap_pct - already_spent_week, 0.0)
        premium_budget = min(premium_budget, weekly_remaining)

        # --- Size ---
        if premium_budget < entry_premium:
            # Can't afford even 1 unit — skip trade
            results.append({
                'Date': row['Date'],
                'Pattern': row['Pattern'],
                'Direction': row['Direction'],
                'Signal': row['Signal'],
                'Result': row['Result'],
                'Net_PnL_Pct': row['Net_PnL_Pct'],
                'Size': 0,
                'Premium_Spent': 0.0,
                'Dollar_PnL': 0.0,
                'Equity': equity,
                'Peak_Equity': peak_equity,
                'Drawdown_Pct': (equity - peak_equity) / peak_equity * 100 if peak_equity > 0 else 0,
                'Skipped': True,
            })
            continue

        size = math.floor(premium_budget / entry_premium)
        if size < 1:
            size = 1

        actual_spent = size * entry_premium

        # Track daily/weekly spend
        daily_spent[day_key] = already_spent_today + actual_spent
        weekly_spent[week_key] = already_spent_week + actual_spent

        # --- P&L ---
        net_pnl_pct = row['PnL_Mult']  # already a fraction (e.g., 0.03 for +3%)
        dollar_pnl = actual_spent * net_pnl_pct

        equity += dollar_pnl
        equity = max(equity, 1.0)  # floor at $1
        peak_equity = max(peak_equity, equity)
        dd_pct = (equity - peak_equity) / peak_equity * 100 if peak_equity > 0 else 0

        results.append({
            'Date': row['Date'],
            'Pattern': row['Pattern'],
            'Direction': row['Direction'],
            'Signal': row['Signal'],
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


def analyze_curve(df, label, starting_equity=10000):
    """Compute summary stats from sized equity curve."""
    traded = df[~df['Skipped']]
    n_traded = len(traded)
    n_skipped = df['Skipped'].sum()

    if n_traded == 0:
        return None

    final_eq = df['Equity'].iloc[-1]
    years = (pd.to_datetime(df['Date'].iloc[-1]) - pd.to_datetime(df['Date'].iloc[0])).days / 365.25
    cagr = (pow(final_eq / starting_equity, 1 / years) - 1) * 100 if years > 0 and final_eq > starting_equity else \
           -((1 - pow(max(final_eq, 1) / starting_equity, 1 / years)) * 100) if years > 0 else 0

    max_dd = df['Drawdown_Pct'].min()

    wins = traded[traded['Result'] == 'WIN']
    losses = traded[traded['Result'] == 'LOSS']
    win_rate = len(wins) / n_traded * 100

    ev_pct = traded['Net_PnL_Pct'].mean()
    avg_dollar_pnl = traded['Dollar_PnL'].mean()
    total_dollar_pnl = traded['Dollar_PnL'].sum()

    # Losing streaks (on traded only)
    streak = 0
    worst_streak = 0
    for _, row in traded.iterrows():
        if row['Result'] == 'LOSS':
            streak += 1
            worst_streak = max(worst_streak, streak)
        else:
            streak = 0

    # Avg premium spent
    avg_spent = traded['Premium_Spent'].mean()
    avg_size = traded['Size'].mean()

    return {
        'label': label,
        'trades_executed': n_traded,
        'trades_skipped': n_skipped,
        'win_rate': win_rate,
        'ev_per_trade': ev_pct,
        'avg_dollar_pnl': avg_dollar_pnl,
        'total_pnl': total_dollar_pnl,
        'starting_equity': starting_equity,
        'final_equity': final_eq,
        'cagr': cagr,
        'max_dd': max_dd,
        'worst_losing_streak': worst_streak,
        'avg_premium_spent': avg_spent,
        'avg_size': avg_size,
    }


def display_comparison(stats_list):
    """Display comparison table across risk_pct levels."""
    table = Table(title="Position Sizing Comparison", show_header=True, header_style="bold cyan")
    table.add_column("Metric", width=24)
    for s in stats_list:
        table.add_column(s['label'], justify="right", width=16)

    rows = [
        ("Trades executed", lambda s: f"{s['trades_executed']:,}"),
        ("Trades skipped", lambda s: f"{s['trades_skipped']:,}"),
        ("Win rate", lambda s: f"{s['win_rate']:.1f}%"),
        ("EV per trade", lambda s: f"{s['ev_per_trade']:+.2f}%"),
        ("Avg $ P&L/trade", lambda s: f"${s['avg_dollar_pnl']:+.2f}"),
        ("Total $ P&L", lambda s: f"${s['total_pnl']:+,.0f}"),
        ("Final equity", lambda s: f"${s['final_equity']:,.0f}"),
        ("CAGR", lambda s: f"{s['cagr']:+.1f}%"),
        ("Max drawdown", lambda s: f"{s['max_dd']:.1f}%"),
        ("Worst losing streak", lambda s: f"{s['worst_losing_streak']}"),
        ("Avg premium spent", lambda s: f"${s['avg_premium_spent']:.2f}"),
        ("Avg size (units)", lambda s: f"{s['avg_size']:.1f}"),
    ]

    for label, fn in rows:
        vals = [fn(s) for s in stats_list]
        table.add_row(label, *vals)

    console.print(table)
    console.print()


def main():
    console.print("=" * 90)
    console.print("[bold blue]OVERNIGHT FADE: Premium-Budget Position Sizing[/bold blue]")
    console.print("=" * 90)
    console.print()
    console.print("[bold]Sizing rule:[/bold]")
    console.print("  premium_budget = equity * risk_pct")
    console.print("  size = floor(premium_budget / option_premium)")
    console.print("  P&L  = size * option_premium * net_return_%")
    console.print()
    console.print("[bold]Caps:[/bold]")
    console.print("  Daily:  max 2% of equity in premiums per day")
    console.print("  Weekly: max 7% of equity in premiums per week")
    console.print()

    trade_log = 'results/baseline_20260208_overnight_fade_vix_iv.csv'

    risk_levels = [0.005, 0.010, 0.015]
    labels = ["0.5% risk", "1.0% risk", "1.5% risk"]

    all_stats = []
    all_curves = {}

    for risk_pct, label in zip(risk_levels, labels):
        console.print(f"[cyan]Running: {label} (daily cap 2%, weekly cap 7%)...[/cyan]")
        curve_df = run_sized_equity_curve(
            trade_log,
            starting_equity=10000,
            risk_pct=risk_pct,
            daily_cap_pct=0.02,
            weekly_cap_pct=0.07,
        )
        stats = analyze_curve(curve_df, label)
        if stats:
            all_stats.append(stats)
            all_curves[label] = curve_df

    console.print()
    display_comparison(all_stats)

    # Detail for 1.0% risk (primary)
    primary = all_curves.get("1.0% risk")
    if primary is not None:
        console.print("=" * 90)
        console.print("[bold white]DETAIL: 1.0% risk per trade[/bold white]")
        console.print("=" * 90)
        console.print()

        # Drawdown timeline
        traded = primary[~primary['Skipped']]
        console.print("[bold]Drawdown analysis:[/bold]")

        dd = traded['Drawdown_Pct']
        console.print(f"  Max drawdown:    {dd.min():.1f}%")
        console.print(f"  Avg drawdown:    {dd.mean():.1f}%")
        console.print(f"  % time in >10% DD: {(dd < -10).sum() / len(dd) * 100:.1f}%")
        console.print(f"  % time in >20% DD: {(dd < -20).sum() / len(dd) * 100:.1f}%")
        console.print()

        # By direction
        for d in ['RED', 'GREEN']:
            sub = traded[traded['Direction'] == d]
            if len(sub) == 0:
                continue
            wr = (sub['Result'] == 'WIN').sum() / len(sub) * 100
            ev = sub['Net_PnL_Pct'].mean()
            total_pnl = sub['Dollar_PnL'].sum()
            style = "green" if ev > 0 else "red"
            console.print(f"  {d}: {len(sub)} trades, {wr:.1f}% win, [{style}]{ev:+.2f}% EV[/{style}], ${total_pnl:+,.0f} total P&L")

        console.print()

        # By pattern
        pattern_table = Table(title="1.0% Risk - By Pattern", show_header=True, header_style="bold cyan")
        pattern_table.add_column("Pattern", width=12)
        pattern_table.add_column("Trades", justify="right", width=8)
        pattern_table.add_column("Win%", justify="right", width=8)
        pattern_table.add_column("EV", justify="right", width=10)
        pattern_table.add_column("$ P&L", justify="right", width=12)

        for pattern in ["MON-WED", "TUE-WED", "WED-FRI", "THU-FRI"]:
            sub = traded[traded['Pattern'] == pattern]
            if len(sub) == 0:
                continue
            wr = (sub['Result'] == 'WIN').sum() / len(sub) * 100
            ev = sub['Net_PnL_Pct'].mean()
            total = sub['Dollar_PnL'].sum()
            style = "green" if ev > 0 else "red"
            pattern_table.add_row(
                pattern, f"{len(sub):,}", f"{wr:.1f}%",
                f"[{style}]{ev:+.2f}%[/{style}]",
                f"${total:+,.0f}",
            )

        console.print(pattern_table)
        console.print()

        # Save curve
        primary.to_csv('results/overnight_fade_sized_1pct.csv', index=False)
        console.print("[green]Saved: results/overnight_fade_sized_1pct.csv[/green]")

    # Worst streak impact analysis for 1%
    if primary is not None:
        console.print()
        console.print("[bold]Worst losing streak impact (1% risk):[/bold]")
        traded = primary[~primary['Skipped']].reset_index(drop=True)

        # Find worst streak
        streak = 0
        worst_len = 0
        worst_end = 0
        for i, row in traded.iterrows():
            if row['Result'] == 'LOSS':
                streak += 1
                if streak > worst_len:
                    worst_len = streak
                    worst_end = i
            else:
                streak = 0

        worst_start = worst_end - worst_len + 1
        streak_df = traded.iloc[worst_start:worst_end + 1]

        eq_before = traded.iloc[worst_start]['Equity'] + abs(traded.iloc[worst_start]['Dollar_PnL'])
        eq_after = traded.iloc[worst_end]['Equity']
        streak_dd = (eq_after - eq_before) / eq_before * 100

        console.print(f"  Streak length:   {worst_len} trades")
        console.print(f"  Period:          {streak_df.iloc[0]['Date']} to {streak_df.iloc[-1]['Date']}")
        console.print(f"  Equity before:   ${eq_before:,.0f}")
        console.print(f"  Equity after:    ${eq_after:,.0f}")
        console.print(f"  Streak drawdown: {streak_dd:+.1f}%")
        console.print(f"  Avg loss/trade:  ${streak_df['Dollar_PnL'].mean():+.2f}")
        console.print()

    console.print("=" * 90)


if __name__ == "__main__":
    main()
