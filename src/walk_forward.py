"""
Walk-forward evaluation with stress testing for strategy validation.

Implements:
- Rolling train/test splits (6 months train, 2 months test)
- Final holdout reservation (last 3 months)
- Stress testing with price noise and time shifts
"""
import os
import json
import random
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Optional, Tuple
from rich.console import Console
from rich.table import Table

console = Console()

CONFIG_PATH = os.path.join("config", "config.json")

# Walk-forward parameters
TRAIN_MONTHS = 6
TEST_MONTHS = 2
HOLDOUT_MONTHS = 3

# Stress test parameters
STRESS_NOISE_BPS_MIN = 1
STRESS_NOISE_BPS_MAX = 3
STRESS_TIME_SHIFT_MINUTES = 1
STRESS_AFFECTED_TRADE_PCT = 0.15  # 15% of trades affected


@dataclass
class FoldResult:
    """Results from a single train/test fold."""
    fold_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_trades: int
    test_trades: int
    test_win_rate: float
    test_avg_pnl: float
    test_total_pnl: float
    stress_win_rate: Optional[float] = None
    stress_avg_pnl: Optional[float] = None
    stress_degradation_pct: Optional[float] = None


class WalkForwardEvaluator:
    """
    Walk-forward evaluation framework for strategy validation.

    Splits historical data into rolling train/test windows and evaluates
    strategy performance with optional stress testing.
    """

    def __init__(self):
        self._load_config()
        self.ticker = self.config["ticker"]
        self.data_dir = os.path.join(self.config["directories"]["data"], self.ticker)
        self.daily_path = os.path.join(self.data_dir, "daily_OHLCV.parquet")
        self.intraday_dir = os.path.join(self.data_dir, "intraday")

        self.FLAT_THRESHOLD_PCT = 0.10
        self.SLIPPAGE_PENALTY = 0.05

        if os.path.exists(self.daily_path):
            self.daily_df = pd.read_parquet(self.daily_path)
        else:
            raise FileNotFoundError(f"Daily data not found at {self.daily_path}")

    def _load_config(self):
        with open(CONFIG_PATH, "r") as f:
            self.config = json.load(f)

    def _get_date_range(self) -> Tuple[datetime, datetime]:
        """Get the full date range of available data."""
        start = self.daily_df.index.min().to_pydatetime()
        end = self.daily_df.index.max().to_pydatetime()
        return start, end

    def _create_folds(self) -> List[dict]:
        """
        Create train/test folds with holdout reservation.

        Returns list of fold definitions with train and test date ranges.
        """
        data_start, data_end = self._get_date_range()

        # Reserve holdout period (last 3 months)
        holdout_start = data_end - timedelta(days=HOLDOUT_MONTHS * 30)
        evaluation_end = holdout_start - timedelta(days=1)

        console.print(f"[dim]Data range: {data_start.date()} to {data_end.date()}[/dim]")
        console.print(f"[dim]Holdout reserved: {holdout_start.date()} to {data_end.date()}[/dim]")
        console.print(f"[dim]Evaluation period: {data_start.date()} to {evaluation_end.date()}[/dim]")

        folds = []
        fold_id = 0

        # Minimum data needed: TRAIN_MONTHS + TEST_MONTHS
        min_days_needed = (TRAIN_MONTHS + TEST_MONTHS) * 30
        current_start = data_start

        while True:
            train_end = current_start + timedelta(days=TRAIN_MONTHS * 30)
            test_start = train_end + timedelta(days=1)
            test_end = test_start + timedelta(days=TEST_MONTHS * 30)

            # Stop if test period would exceed evaluation end
            if test_end > evaluation_end:
                break

            folds.append({
                "fold_id": fold_id,
                "train_start": current_start,
                "train_end": train_end,
                "test_start": test_start,
                "test_end": test_end,
            })

            # Roll forward by test period length
            current_start = test_start
            fold_id += 1

        return folds

    def _filter_daily_by_dates(self, start: datetime, end: datetime) -> pd.DataFrame:
        """Filter daily dataframe to date range."""
        mask = (self.daily_df.index >= start) & (self.daily_df.index <= end)
        return self.daily_df[mask].copy()

    def _run_backtest_on_period(
        self,
        daily_subset: pd.DataFrame,
        take_profit_atr: float,
        apply_stress: bool = False
    ) -> pd.DataFrame:
        """
        Run backtest on a subset of daily data.

        This is a simplified version of Backtester.run() that works on a subset.
        """
        from session_utils import get_overnight_window_utc

        trades = []
        valid_days = daily_subset[daily_subset.index.dayofweek < 4]

        for i in range(len(valid_days) - 1):
            day_t = valid_days.iloc[i]
            date_t = valid_days.index[i]

            if abs(day_t["Magnitude"]) < self.FLAT_THRESHOLD_PCT:
                continue

            signal = "NO_TRADE"
            if day_t["Direction"] == "GREEN" and self.config["filters"]["enable_fade_green"]:
                signal = "FADE_GREEN"
            elif day_t["Direction"] == "RED" and self.config["filters"]["enable_fade_red"]:
                signal = "FADE_RED"

            if signal == "NO_TRADE":
                continue

            day_str = date_t.strftime("%Y-%m-%d")
            intra_path = os.path.join(self.intraday_dir, f"{day_str}.parquet")

            if not os.path.exists(intra_path):
                continue

            df_intra = pd.read_parquet(intra_path)
            if df_intra.empty:
                continue

            # Apply stress: add noise to prices
            if apply_stress:
                df_intra = self._apply_stress_noise(df_intra)

            date_obj = date_t.to_pydatetime()
            start_utc, end_utc = get_overnight_window_utc(date_obj)

            if df_intra.index.tz is None:
                df_intra.index = df_intra.index.tz_localize('UTC')

            window = df_intra[(df_intra.index > start_utc) & (df_intra.index < end_utc)]
            if window.empty:
                continue

            # Apply stress: shift window times for some trades
            if apply_stress and random.random() < STRESS_AFFECTED_TRADE_PCT:
                window = self._apply_stress_time_shift(window)

            ref_price = day_t["Close"]
            atr = day_t["ATR_14"]
            if pd.isna(atr):
                continue

            target_dist = atr * take_profit_atr
            outcome, gross_pnl = self._evaluate_trade(signal, ref_price, target_dist, window)
            net_pnl = gross_pnl - self.SLIPPAGE_PENALTY

            trades.append({
                "Date": day_str,
                "Signal": signal,
                "Result": outcome,
                "PnL_Mult": round(net_pnl, 2),
                "PnL_Dollar": round(net_pnl * self.config["premium_budget"], 2),
            })

        return pd.DataFrame(trades)

    def _evaluate_trade(self, signal: str, ref_price: float, target_dist: float, window: pd.DataFrame) -> Tuple[str, float]:
        """Evaluate a single trade outcome."""
        if signal == "FADE_GREEN":
            target_price = ref_price - target_dist
            hits = window[window["Low"] <= target_price]
            if not hits.empty:
                return "WIN", 0.5
            else:
                close_price = window["Close"].iloc[-1]
                fade_achieved = ref_price - close_price
                return self._scratch_outcome(fade_achieved, target_dist)
        else:  # FADE_RED
            target_price = ref_price + target_dist
            hits = window[window["High"] >= target_price]
            if not hits.empty:
                return "WIN", 0.5
            else:
                close_price = window["Close"].iloc[-1]
                fade_achieved = close_price - ref_price
                return self._scratch_outcome(fade_achieved, target_dist)

    def _scratch_outcome(self, fade_achieved: float, target_dist: float) -> Tuple[str, float]:
        """Calculate scratch/loss outcome."""
        progress = fade_achieved / target_dist if target_dist > 0 else 0
        progress = max(0.0, min(1.0, progress))
        pnl = -1.0 + (0.9 * progress)
        outcome = "LOSS" if pnl <= -0.5 else "SCRATCH"
        return outcome, pnl

    def _apply_stress_noise(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add random noise to OHLC prices (1-3 bps)."""
        noise_bps = random.uniform(STRESS_NOISE_BPS_MIN, STRESS_NOISE_BPS_MAX) / 10000
        df = df.copy()
        for col in ["Open", "High", "Low", "Close"]:
            if col in df.columns:
                noise = np.random.uniform(-noise_bps, noise_bps, len(df))
                df[col] = df[col] * (1 + noise)
        return df

    def _apply_stress_time_shift(self, window: pd.DataFrame) -> pd.DataFrame:
        """Shift window by 1 minute (simulates execution timing variance)."""
        if len(window) <= STRESS_TIME_SHIFT_MINUTES:
            return window
        # Randomly shift start or end by 1 minute
        if random.random() < 0.5:
            return window.iloc[STRESS_TIME_SHIFT_MINUTES:]
        else:
            return window.iloc[:-STRESS_TIME_SHIFT_MINUTES]

    def _compute_metrics(self, trades_df: pd.DataFrame) -> dict:
        """Compute summary metrics from trades dataframe."""
        if trades_df.empty:
            return {"trades": 0, "win_rate": 0, "avg_pnl": 0, "total_pnl": 0}

        wins = len(trades_df[trades_df["Result"] == "WIN"])
        return {
            "trades": len(trades_df),
            "win_rate": wins / len(trades_df) * 100,
            "avg_pnl": trades_df["PnL_Mult"].mean(),
            "total_pnl": trades_df["PnL_Dollar"].sum(),
        }

    def run_walk_forward(self, take_profit_atr: float = None, run_stress: bool = True) -> List[FoldResult]:
        """
        Execute walk-forward evaluation across all folds.

        Args:
            take_profit_atr: ATR multiplier for take profit. Uses config default if None.
            run_stress: Whether to run stress tests on each fold.

        Returns:
            List of FoldResult objects with metrics for each fold.
        """
        if take_profit_atr is None:
            take_profit_atr = self.config["default_take_profit_atr"]

        folds = self._create_folds()
        if not folds:
            console.print("[red]Not enough data for walk-forward evaluation.[/red]")
            return []

        console.print(f"\n[cyan]Running walk-forward with {len(folds)} folds...[/cyan]")

        results = []
        for fold in folds:
            # Get data subsets
            train_daily = self._filter_daily_by_dates(fold["train_start"], fold["train_end"])
            test_daily = self._filter_daily_by_dates(fold["test_start"], fold["test_end"])

            # Run backtests
            train_trades = self._run_backtest_on_period(train_daily, take_profit_atr)
            test_trades = self._run_backtest_on_period(test_daily, take_profit_atr)

            train_metrics = self._compute_metrics(train_trades)
            test_metrics = self._compute_metrics(test_trades)

            fold_result = FoldResult(
                fold_id=fold["fold_id"],
                train_start=fold["train_start"].strftime("%Y-%m-%d"),
                train_end=fold["train_end"].strftime("%Y-%m-%d"),
                test_start=fold["test_start"].strftime("%Y-%m-%d"),
                test_end=fold["test_end"].strftime("%Y-%m-%d"),
                train_trades=train_metrics["trades"],
                test_trades=test_metrics["trades"],
                test_win_rate=test_metrics["win_rate"],
                test_avg_pnl=test_metrics["avg_pnl"],
                test_total_pnl=test_metrics["total_pnl"],
            )

            # Run stress test
            if run_stress and test_metrics["trades"] > 0:
                stress_trades = self._run_backtest_on_period(test_daily, take_profit_atr, apply_stress=True)
                stress_metrics = self._compute_metrics(stress_trades)

                fold_result.stress_win_rate = stress_metrics["win_rate"]
                fold_result.stress_avg_pnl = stress_metrics["avg_pnl"]

                if test_metrics["avg_pnl"] != 0:
                    degradation = (test_metrics["avg_pnl"] - stress_metrics["avg_pnl"]) / abs(test_metrics["avg_pnl"]) * 100
                    fold_result.stress_degradation_pct = degradation

            results.append(fold_result)

        return results

    def run_final_holdout(self, take_profit_atr: float = None) -> dict:
        """
        Run evaluation on the reserved holdout period.

        Only call this once after strategy selection is complete.
        """
        if take_profit_atr is None:
            take_profit_atr = self.config["default_take_profit_atr"]

        data_start, data_end = self._get_date_range()
        holdout_start = data_end - timedelta(days=HOLDOUT_MONTHS * 30)

        console.print(f"\n[bold magenta]FINAL HOLDOUT: {holdout_start.date()} to {data_end.date()}[/bold magenta]")

        holdout_daily = self._filter_daily_by_dates(holdout_start, data_end)
        trades = self._run_backtest_on_period(holdout_daily, take_profit_atr)
        metrics = self._compute_metrics(trades)

        # Also run stress
        stress_trades = self._run_backtest_on_period(holdout_daily, take_profit_atr, apply_stress=True)
        stress_metrics = self._compute_metrics(stress_trades)

        return {
            "period": f"{holdout_start.date()} to {data_end.date()}",
            "trades": metrics["trades"],
            "win_rate": metrics["win_rate"],
            "avg_pnl": metrics["avg_pnl"],
            "total_pnl": metrics["total_pnl"],
            "stress_win_rate": stress_metrics["win_rate"],
            "stress_avg_pnl": stress_metrics["avg_pnl"],
        }

    def display_results(self, results: List[FoldResult]):
        """Display walk-forward results in a formatted table."""
        table = Table(title="Walk-Forward Evaluation Results")
        table.add_column("Fold", justify="center")
        table.add_column("Test Period", justify="center")
        table.add_column("Trades", justify="right")
        table.add_column("Win Rate", justify="right")
        table.add_column("Avg PnL", justify="right")
        table.add_column("Stress WR", justify="right")
        table.add_column("Degradation", justify="right")

        for r in results:
            deg_str = f"{r.stress_degradation_pct:+.1f}%" if r.stress_degradation_pct is not None else "N/A"
            deg_style = "red" if r.stress_degradation_pct and r.stress_degradation_pct > 20 else None

            table.add_row(
                str(r.fold_id),
                f"{r.test_start} to {r.test_end}",
                str(r.test_trades),
                f"{r.test_win_rate:.1f}%",
                f"{r.test_avg_pnl:+.2f}R",
                f"{r.stress_win_rate:.1f}%" if r.stress_win_rate else "N/A",
                deg_str,
                style=deg_style,
            )

        console.print(table)

        # Summary
        if results:
            avg_wr = np.mean([r.test_win_rate for r in results])
            avg_pnl = np.mean([r.test_avg_pnl for r in results])
            profitable_folds = sum(1 for r in results if r.test_avg_pnl > 0)
            stress_collapses = sum(1 for r in results if r.stress_degradation_pct and r.stress_degradation_pct > 50)

            console.print(f"\n[bold]Summary:[/bold]")
            console.print(f"  Avg Win Rate: {avg_wr:.1f}%")
            console.print(f"  Avg PnL: {avg_pnl:+.2f}R")
            console.print(f"  Profitable Folds: {profitable_folds}/{len(results)}")
            console.print(f"  Stress Collapses (>50% degradation): {stress_collapses}")

            if stress_collapses > 0:
                console.print("[yellow]Warning: Strategy shows fragility under stress.[/yellow]")
            elif profitable_folds >= len(results) * 0.7:
                console.print("[green]Strategy appears robust across folds.[/green]")


def is_strategy_robust(results: List[FoldResult], min_profitable_folds: int = 3, max_stress_degradation: float = 50) -> bool:
    """
    Check if strategy passes robustness criteria.

    Args:
        results: List of fold results from walk-forward evaluation.
        min_profitable_folds: Minimum number of folds with positive avg PnL.
        max_stress_degradation: Maximum acceptable degradation % under stress.

    Returns:
        True if strategy is robust, False otherwise.
    """
    if not results:
        return False

    profitable_folds = sum(1 for r in results if r.test_avg_pnl > 0)
    stress_collapses = sum(1 for r in results if r.stress_degradation_pct and r.stress_degradation_pct > max_stress_degradation)

    return profitable_folds >= min_profitable_folds and stress_collapses == 0


if __name__ == "__main__":
    wf = WalkForwardEvaluator()
    results = wf.run_walk_forward()
    wf.display_results(results)

    if is_strategy_robust(results):
        console.print("\n[bold green]Strategy passed robustness check. Running final holdout...[/bold green]")
        holdout = wf.run_final_holdout()
        console.print(f"\n[bold]Final Holdout Results:[/bold]")
        console.print(f"  Period: {holdout['period']}")
        console.print(f"  Trades: {holdout['trades']}")
        console.print(f"  Win Rate: {holdout['win_rate']:.1f}%")
        console.print(f"  Avg PnL: {holdout['avg_pnl']:+.2f}R")
        console.print(f"  Total PnL: ${holdout['total_pnl']:,.2f}")
        console.print(f"  Stress Win Rate: {holdout['stress_win_rate']:.1f}%")
    else:
        console.print("\n[yellow]Strategy did not pass robustness check. Do not run final holdout.[/yellow]")
