"""
Strategy evaluation framework.

Tests strategy variants through walk-forward validation and compares performance.
"""
import os
import json
import pandas as pd
import numpy as np
from datetime import timedelta
from dataclasses import dataclass
from typing import List, Dict, Optional
from rich.console import Console
from rich.table import Table

from session_utils import get_overnight_window_utc
from strategies import (
    Strategy, BaselineStrategy, ExhaustionFilter,
    LastHourVeto, ATRRegimeFilter, PARAM_GRIDS, create_strategy
)

console = Console()

CONFIG_PATH = os.path.join("config", "config.json")

# Walk-forward parameters
TRAIN_MONTHS = 6
TEST_MONTHS = 2
HOLDOUT_MONTHS = 3


@dataclass
class StrategyResult:
    """Results for a strategy variant."""
    name: str
    params: dict
    folds_tested: int
    avg_win_rate: float
    avg_pnl: float
    total_pnl: float
    profitable_folds: int
    stress_avg_pnl: float
    stress_collapses: int
    trades_per_fold: float


class StrategyEvaluator:
    """Evaluates strategy variants through walk-forward testing."""

    def __init__(self):
        self._load_config()
        self.ticker = self.config["ticker"]
        self.data_dir = os.path.join(self.config["directories"]["data"], self.ticker)
        self.daily_path = os.path.join(self.data_dir, "daily_OHLCV.parquet")
        self.intraday_dir = os.path.join(self.data_dir, "intraday")

        self.SLIPPAGE_PENALTY = 0.05

        if os.path.exists(self.daily_path):
            self.daily_df = pd.read_parquet(self.daily_path)
        else:
            raise FileNotFoundError(f"Daily data not found at {self.daily_path}")

    def _load_config(self):
        with open(CONFIG_PATH, "r") as f:
            self.config = json.load(f)

    def _create_folds(self):
        """Create train/test folds with holdout reservation."""
        data_start = self.daily_df.index.min().to_pydatetime()
        data_end = self.daily_df.index.max().to_pydatetime()

        holdout_start = data_end - timedelta(days=HOLDOUT_MONTHS * 30)
        evaluation_end = holdout_start - timedelta(days=1)

        folds = []
        fold_id = 0
        current_start = data_start

        while True:
            train_end = current_start + timedelta(days=TRAIN_MONTHS * 30)
            test_start = train_end + timedelta(days=1)
            test_end = test_start + timedelta(days=TEST_MONTHS * 30)

            if test_end > evaluation_end:
                break

            folds.append({
                "fold_id": fold_id,
                "train_start": current_start,
                "train_end": train_end,
                "test_start": test_start,
                "test_end": test_end,
            })

            current_start = test_start
            fold_id += 1

        return folds, holdout_start, data_end

    def _run_strategy_on_period(
        self,
        strategy: Strategy,
        daily_subset: pd.DataFrame,
        take_profit_atr: float,
        apply_stress: bool = False
    ) -> pd.DataFrame:
        """Run a strategy on a data subset."""
        import random

        trades = []
        valid_days = daily_subset[daily_subset.index.dayofweek < 4]

        for i in range(len(valid_days) - 1):
            day_t = valid_days.iloc[i]
            date_t = valid_days.index[i]
            date_str = date_t.strftime("%Y-%m-%d")

            # Get strategy signal
            signal_result = strategy.should_trade(day_t, date_str)

            if signal_result.signal == "NO_TRADE":
                continue

            if pd.isna(signal_result.atr):
                continue

            # Load intraday data
            intra_path = os.path.join(self.intraday_dir, f"{date_str}.parquet")
            if not os.path.exists(intra_path):
                continue

            df_intra = pd.read_parquet(intra_path)
            if df_intra.empty:
                continue

            # Apply stress noise
            if apply_stress:
                noise_bps = random.uniform(1, 3) / 10000
                for col in ["Open", "High", "Low", "Close"]:
                    if col in df_intra.columns:
                        noise = np.random.uniform(-noise_bps, noise_bps, len(df_intra))
                        df_intra[col] = df_intra[col] * (1 + noise)

            date_obj = date_t.to_pydatetime()
            start_utc, end_utc = get_overnight_window_utc(date_obj)

            if df_intra.index.tz is None:
                df_intra.index = df_intra.index.tz_localize('UTC')

            window = df_intra[(df_intra.index > start_utc) & (df_intra.index < end_utc)]
            if window.empty:
                continue

            # Apply stress time shift
            if apply_stress and random.random() < 0.15 and len(window) > 1:
                window = window.iloc[1:] if random.random() < 0.5 else window.iloc[:-1]

            target_dist = signal_result.atr * take_profit_atr
            outcome, gross_pnl = self._evaluate_trade(
                signal_result.signal, signal_result.ref_price, target_dist, window
            )
            net_pnl = gross_pnl - self.SLIPPAGE_PENALTY

            trades.append({
                "Date": date_str,
                "Signal": signal_result.signal,
                "Result": outcome,
                "PnL_Mult": round(net_pnl, 2),
                "PnL_Dollar": round(net_pnl * self.config["premium_budget"], 2),
            })

        return pd.DataFrame(trades)

    def _evaluate_trade(self, signal, ref_price, target_dist, window):
        """Evaluate trade outcome."""
        if signal == "FADE_GREEN":
            target_price = ref_price - target_dist
            hits = window[window["Low"] <= target_price]
            if not hits.empty:
                return "WIN", 0.5
            close_price = window["Close"].iloc[-1]
            fade_achieved = ref_price - close_price
        else:
            target_price = ref_price + target_dist
            hits = window[window["High"] >= target_price]
            if not hits.empty:
                return "WIN", 0.5
            close_price = window["Close"].iloc[-1]
            fade_achieved = close_price - ref_price

        progress = fade_achieved / target_dist if target_dist > 0 else 0
        progress = max(0.0, min(1.0, progress))
        pnl = -1.0 + (0.9 * progress)
        outcome = "LOSS" if pnl <= -0.5 else "SCRATCH"
        return outcome, pnl

    def _compute_metrics(self, trades_df):
        """Compute metrics from trades."""
        if trades_df.empty:
            return {"trades": 0, "win_rate": 0, "avg_pnl": 0, "total_pnl": 0}
        wins = len(trades_df[trades_df["Result"] == "WIN"])
        return {
            "trades": len(trades_df),
            "win_rate": wins / len(trades_df) * 100,
            "avg_pnl": trades_df["PnL_Mult"].mean(),
            "total_pnl": trades_df["PnL_Dollar"].sum(),
        }

    def evaluate_strategy(
        self,
        strategy: Strategy,
        take_profit_atr: float = None
    ) -> StrategyResult:
        """Run full walk-forward evaluation on a strategy."""
        if take_profit_atr is None:
            take_profit_atr = self.config["default_take_profit_atr"]

        folds, _, _ = self._create_folds()

        fold_results = []
        for fold in folds:
            train_mask = (self.daily_df.index >= fold["train_start"]) & (self.daily_df.index <= fold["train_end"])
            test_mask = (self.daily_df.index >= fold["test_start"]) & (self.daily_df.index <= fold["test_end"])

            train_daily = self.daily_df[train_mask]
            test_daily = self.daily_df[test_mask]

            # For ATRRegime, set threshold from training data
            if hasattr(strategy, 'set_atr_threshold'):
                strategy.set_atr_threshold(train_daily["ATR_14"])

            test_trades = self._run_strategy_on_period(strategy, test_daily, take_profit_atr)
            test_metrics = self._compute_metrics(test_trades)

            stress_trades = self._run_strategy_on_period(strategy, test_daily, take_profit_atr, apply_stress=True)
            stress_metrics = self._compute_metrics(stress_trades)

            stress_degradation = 0
            if test_metrics["avg_pnl"] != 0:
                stress_degradation = (test_metrics["avg_pnl"] - stress_metrics["avg_pnl"]) / abs(test_metrics["avg_pnl"]) * 100

            fold_results.append({
                "test_metrics": test_metrics,
                "stress_metrics": stress_metrics,
                "stress_degradation": stress_degradation,
            })

        # Aggregate results
        if not fold_results:
            return StrategyResult(
                name=strategy.name,
                params={},
                folds_tested=0,
                avg_win_rate=0,
                avg_pnl=0,
                total_pnl=0,
                profitable_folds=0,
                stress_avg_pnl=0,
                stress_collapses=0,
                trades_per_fold=0,
            )

        avg_wr = np.mean([f["test_metrics"]["win_rate"] for f in fold_results])
        avg_pnl = np.mean([f["test_metrics"]["avg_pnl"] for f in fold_results])
        total_pnl = sum([f["test_metrics"]["total_pnl"] for f in fold_results])
        profitable = sum(1 for f in fold_results if f["test_metrics"]["avg_pnl"] > 0)
        stress_avg = np.mean([f["stress_metrics"]["avg_pnl"] for f in fold_results])
        collapses = sum(1 for f in fold_results if f["stress_degradation"] > 50)
        trades_per_fold = np.mean([f["test_metrics"]["trades"] for f in fold_results])

        return StrategyResult(
            name=strategy.name,
            params=getattr(strategy, '__dict__', {}),
            folds_tested=len(folds),
            avg_win_rate=avg_wr,
            avg_pnl=avg_pnl,
            total_pnl=total_pnl,
            profitable_folds=profitable,
            stress_avg_pnl=stress_avg,
            stress_collapses=collapses,
            trades_per_fold=trades_per_fold,
        )

    def grid_search(self, strategy_name: str) -> List[StrategyResult]:
        """Run grid search over strategy parameters."""
        if strategy_name not in PARAM_GRIDS:
            console.print(f"[yellow]No parameter grid for {strategy_name}[/yellow]")
            return []

        grid = PARAM_GRIDS[strategy_name]
        param_names = list(grid.keys())

        # Generate all parameter combinations
        from itertools import product
        param_values = [grid[p] for p in param_names]
        combinations = list(product(*param_values))

        results = []
        for combo in combinations:
            params = dict(zip(param_names, combo))
            strategy = create_strategy(strategy_name, self.config, self.intraday_dir, **params)
            result = self.evaluate_strategy(strategy)
            result.params = params
            result.name = f"{strategy_name}({', '.join(f'{k}={v}' for k, v in params.items())})"
            results.append(result)

        return results

    def run_comparison(self) -> Dict[str, StrategyResult]:
        """Compare all strategies and return best performers."""
        console.print("\n[bold cyan]Strategy Comparison[/bold cyan]")
        console.print("=" * 60)

        all_results = {}

        # 1. Baseline
        console.print("\n[dim]Testing Baseline...[/dim]")
        baseline = BaselineStrategy(self.config, self.intraday_dir)
        all_results["Baseline"] = self.evaluate_strategy(baseline)

        # 2. Exhaustion Filter
        console.print("[dim]Testing Exhaustion Filter variants...[/dim]")
        exhaustion_results = self.grid_search("Exhaustion")
        if exhaustion_results:
            best_exhaustion = max(exhaustion_results, key=lambda x: x.avg_pnl)
            all_results["Exhaustion"] = best_exhaustion

        # 3. Last Hour Veto
        console.print("[dim]Testing Last Hour Veto variants...[/dim]")
        veto_results = self.grid_search("LastHourVeto")
        if veto_results:
            best_veto = max(veto_results, key=lambda x: x.avg_pnl)
            all_results["LastHourVeto"] = best_veto

        # 4. ATR Regime Filter
        console.print("[dim]Testing ATR Regime Filter variants...[/dim]")
        atr_results = self.grid_search("ATRRegime")
        if atr_results:
            best_atr = max(atr_results, key=lambda x: x.avg_pnl)
            all_results["ATRRegime"] = best_atr

        return all_results

    def display_results(self, results: Dict[str, StrategyResult]):
        """Display comparison results."""
        table = Table(title="Strategy Comparison Results")
        table.add_column("Strategy", justify="left")
        table.add_column("Win Rate", justify="right")
        table.add_column("Avg PnL", justify="right")
        table.add_column("Total PnL", justify="right")
        table.add_column("Profitable", justify="center")
        table.add_column("Stress PnL", justify="right")
        table.add_column("Collapses", justify="center")
        table.add_column("Trades/Fold", justify="right")

        # Sort by avg_pnl descending
        sorted_results = sorted(results.values(), key=lambda x: x.avg_pnl, reverse=True)

        for r in sorted_results:
            style = "bold green" if r.avg_pnl == sorted_results[0].avg_pnl else None
            table.add_row(
                r.name[:35],
                f"{r.avg_win_rate:.1f}%",
                f"{r.avg_pnl:+.3f}R",
                f"${r.total_pnl:,.0f}",
                f"{r.profitable_folds}/{r.folds_tested}",
                f"{r.stress_avg_pnl:+.3f}R",
                str(r.stress_collapses),
                f"{r.trades_per_fold:.1f}",
                style=style,
            )

        console.print(table)

        # Recommendation
        best = sorted_results[0]
        baseline = results.get("Baseline")

        console.print(f"\n[bold]Best Strategy:[/bold] {best.name}")
        if baseline and best.name != "Baseline":
            improvement = best.avg_pnl - baseline.avg_pnl
            console.print(f"[dim]Improvement over baseline: {improvement:+.3f}R[/dim]")

        if best.stress_collapses > 0:
            console.print("[yellow]Warning: Best strategy has stress collapses. Consider more robust alternative.[/yellow]")

        return best


if __name__ == "__main__":
    evaluator = StrategyEvaluator()
    results = evaluator.run_comparison()
    best = evaluator.display_results(results)
