"""
Run final holdout test with the best strategy.
Only run this ONCE after strategy selection is complete.
"""
import os
import json
import pandas as pd
import numpy as np
from datetime import timedelta
from rich.console import Console
from rich.panel import Panel

from session_utils import get_overnight_window_utc
from strategies import LastHourVeto

console = Console()

CONFIG_PATH = os.path.join("config", "config.json")
HOLDOUT_MONTHS = 3


def run_final_holdout():
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)

    ticker = config["ticker"]
    data_dir = os.path.join(config["directories"]["data"], ticker)
    daily_path = os.path.join(data_dir, "daily_OHLCV.parquet")
    intraday_dir = os.path.join(data_dir, "intraday")

    daily_df = pd.read_parquet(daily_path)
    data_end = daily_df.index.max().to_pydatetime()
    holdout_start = data_end - timedelta(days=HOLDOUT_MONTHS * 30)

    console.print(Panel.fit(
        f"[bold magenta]FINAL HOLDOUT TEST[/bold magenta]\n"
        f"Period: {holdout_start.date()} to {data_end.date()}\n"
        f"Strategy: LastHourVeto(veto_threshold=0.2)",
        title="One-Time Validation"
    ))

    # Create best strategy
    strategy = LastHourVeto(config, intraday_dir, veto_threshold=0.2)

    holdout_mask = daily_df.index >= holdout_start
    holdout_daily = daily_df[holdout_mask]

    SLIPPAGE_PENALTY = 0.05
    take_profit_atr = config["default_take_profit_atr"]

    trades = []
    valid_days = holdout_daily[holdout_daily.index.dayofweek < 4]

    for i in range(len(valid_days) - 1):
        day_t = valid_days.iloc[i]
        date_t = valid_days.index[i]
        date_str = date_t.strftime("%Y-%m-%d")

        signal_result = strategy.should_trade(day_t, date_str)

        if signal_result.signal == "NO_TRADE":
            continue

        if pd.isna(signal_result.atr):
            continue

        intra_path = os.path.join(intraday_dir, f"{date_str}.parquet")
        if not os.path.exists(intra_path):
            continue

        df_intra = pd.read_parquet(intra_path)
        if df_intra.empty:
            continue

        date_obj = date_t.to_pydatetime()
        start_utc, end_utc = get_overnight_window_utc(date_obj)

        if df_intra.index.tz is None:
            df_intra.index = df_intra.index.tz_localize('UTC')

        window = df_intra[(df_intra.index > start_utc) & (df_intra.index < end_utc)]
        if window.empty:
            continue

        target_dist = signal_result.atr * take_profit_atr

        if signal_result.signal == "FADE_GREEN":
            target_price = signal_result.ref_price - target_dist
            hits = window[window["Low"] <= target_price]
            if not hits.empty:
                outcome, gross_pnl = "WIN", 0.5
            else:
                close_price = window["Close"].iloc[-1]
                fade = signal_result.ref_price - close_price
                progress = max(0, min(1, fade / target_dist)) if target_dist > 0 else 0
                gross_pnl = -1.0 + (0.9 * progress)
                outcome = "LOSS" if gross_pnl <= -0.5 else "SCRATCH"
        else:
            target_price = signal_result.ref_price + target_dist
            hits = window[window["High"] >= target_price]
            if not hits.empty:
                outcome, gross_pnl = "WIN", 0.5
            else:
                close_price = window["Close"].iloc[-1]
                fade = close_price - signal_result.ref_price
                progress = max(0, min(1, fade / target_dist)) if target_dist > 0 else 0
                gross_pnl = -1.0 + (0.9 * progress)
                outcome = "LOSS" if gross_pnl <= -0.5 else "SCRATCH"

        net_pnl = gross_pnl - SLIPPAGE_PENALTY

        trades.append({
            "Date": date_str,
            "Signal": signal_result.signal,
            "Result": outcome,
            "PnL_Mult": round(net_pnl, 2),
            "PnL_Dollar": round(net_pnl * config["premium_budget"], 2),
        })

    trades_df = pd.DataFrame(trades)

    if trades_df.empty:
        console.print("[red]No trades in holdout period.[/red]")
        return

    wins = len(trades_df[trades_df["Result"] == "WIN"])
    win_rate = wins / len(trades_df) * 100
    avg_pnl = trades_df["PnL_Mult"].mean()
    total_pnl = trades_df["PnL_Dollar"].sum()

    console.print(f"\n[bold]Final Holdout Results:[/bold]")
    console.print(f"  Trades: {len(trades_df)}")
    console.print(f"  Wins: {wins}")
    console.print(f"  Win Rate: {win_rate:.1f}%")
    console.print(f"  Avg PnL: {avg_pnl:+.3f}R")
    console.print(f"  Total PnL: ${total_pnl:,.2f}")

    # Show trade details
    console.print(f"\n[dim]Trade Log:[/dim]")
    print(trades_df.to_string(index=False))

    # Verdict
    if avg_pnl > 0 and win_rate > 60:
        console.print(f"\n[bold green]PASSED: Strategy is profitable on unseen holdout data.[/bold green]")
    else:
        console.print(f"\n[yellow]CAUTION: Review results carefully before live trading.[/yellow]")


if __name__ == "__main__":
    run_final_holdout()
