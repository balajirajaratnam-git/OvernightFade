"""
Automatic Calibration from Paper Trading Data

Analyzes logged trades and calibrates reality_adjustments.json
Run after collecting 10+ paper trades.
"""

import sys
import json
from pathlib import Path
from typing import Dict, List
import numpy as np
from rich.console import Console
from rich.table import Table

# Add parent directories to path
sys.path.append(str(Path(__file__).parent.parent.parent))
sys.path.append(str(Path(__file__).parent.parent / "trading"))

from trade_logger import TradeLogger

console = Console()


class AutoCalibrator:
    """Automatic calibration from paper trading data."""

    def __init__(self):
        self.logger = TradeLogger()
        self.config_path = Path("config/reality_adjustments.json")

    def analyze_trades(self, trade_type: str) -> Dict:
        """
        Analyze trades and calculate average costs.

        Args:
            trade_type: 'ig_paper_2050', 'ig_paper_2100', or 'ibkr'

        Returns:
            Dictionary with calculated metrics
        """
        trades = self.logger.get_all_trades(trade_type)

        if not trades:
            return {'error': 'No trades found'}

        # Filter completed trades (have exit data)
        completed = [t for t in trades if 'exit_price' in t and t['exit_price'] is not None]

        if not completed:
            console.print(f"[yellow]{len(trades)} trades logged, but none completed yet[/yellow]")
            return {'error': 'No completed trades', 'pending': len(trades)}

        # Calculate metrics
        spreads = []
        slippages = []
        total_costs = []
        pnl_pcts = []

        for trade in completed:
            # Spread: (fill - mid) / mid
            if 'spread_pct' in trade:
                spreads.append(trade['spread_pct'])

            # Slippage: additional cost beyond spread
            if 'slippage_pct' in trade:
                slippages.append(trade['slippage_pct'])

            # Total cost: spread + slippage
            spread = trade.get('spread_pct', 0)
            slippage = trade.get('slippage_pct', 0)
            total_costs.append(spread + slippage)

            # P&L
            if 'pnl_pct' in trade:
                pnl_pcts.append(trade['pnl_pct'])

        # Calculate averages
        avg_spread = np.mean(spreads) if spreads else 0
        avg_slippage = np.mean(slippages) if slippages else 0
        avg_total_cost = np.mean(total_costs) if total_costs else 0

        # Wins/losses
        wins = [p for p in pnl_pcts if p > 0]
        losses = [p for p in pnl_pcts if p <= 0]

        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0
        win_rate = len(wins) / len(pnl_pcts) if pnl_pcts else 0

        return {
            'trade_count': len(completed),
            'avg_spread_pct': avg_spread,
            'avg_slippage_pct': avg_slippage,
            'avg_total_cost_pct': avg_total_cost,
            'win_rate': win_rate,
            'avg_win_pct': avg_win,
            'avg_loss_pct': avg_loss,
            'wins': len(wins),
            'losses': len(losses)
        }

    def compare_timing(self) -> Dict:
        """
        Compare 20:50 vs 21:00 entries to measure timing penalty.

        Returns:
            Dictionary with timing comparison data
        """
        trades_2050 = self.logger.get_all_trades('ig_paper_2050')
        trades_2100 = self.logger.get_all_trades('ig_paper_2100')

        # Match trades by date
        timing_comparison = []

        for t2050 in trades_2050:
            date = t2050.get('date')
            # Find matching 21:00 trade
            t2100 = next((t for t in trades_2100 if t.get('date') == date), None)

            if t2100:
                # Compare costs
                cost_2050 = t2050.get('spread_pct', 0) + t2050.get('slippage_pct', 0)
                cost_2100 = t2100.get('spread_pct', 0) + t2100.get('slippage_pct', 0)

                timing_penalty = cost_2100 - cost_2050

                timing_comparison.append({
                    'date': date,
                    'cost_2050': cost_2050,
                    'cost_2100': cost_2100,
                    'timing_penalty': timing_penalty
                })

        if not timing_comparison:
            return {'error': 'No matching trades to compare'}

        avg_timing_penalty = np.mean([t['timing_penalty'] for t in timing_comparison])

        return {
            'comparisons': len(timing_comparison),
            'avg_cost_2050': np.mean([t['cost_2050'] for t in timing_comparison]),
            'avg_cost_2100': np.mean([t['cost_2100'] for t in timing_comparison]),
            'avg_timing_penalty': avg_timing_penalty
        }

    def calculate_pnl_multiplier(self, avg_win_pct: float, avg_cost_pct: float) -> float:
        """
        Calculate PnL multiplier based on actual results.

        Backtest assumes +45% avg win.
        Reality = (45% * multiplier) - costs

        So: multiplier = (reality_avg_win + costs) / 45%

        Args:
            avg_win_pct: Actual average win percentage
            avg_cost_pct: Actual average cost percentage

        Returns:
            PnL multiplier
        """
        backtest_avg_win = 0.45  # 45% from backtest

        # Reality avg win includes costs already
        # We need: what multiplier would produce this result?
        # Reality = (Backtest * Multiplier) - Costs
        # avg_win_pct = (0.45 * multiplier) - avg_cost_pct
        # multiplier = (avg_win_pct + avg_cost_pct) / 0.45

        multiplier = (avg_win_pct + avg_cost_pct) / backtest_avg_win

        return multiplier

    def generate_calibrated_config(
        self,
        ig_metrics: Dict,
        ibkr_metrics: Dict,
        timing_data: Dict
    ) -> Dict:
        """
        Generate updated reality_adjustments.json values.

        Args:
            ig_metrics: IG.com metrics
            ibkr_metrics: IBKR metrics
            timing_data: Timing comparison data

        Returns:
            Dictionary with updated config values
        """
        # Load current config
        with open(self.config_path, 'r') as f:
            current_config = json.load(f)

        # Create updated config
        updated_config = current_config.copy()

        # Update spreads (use IG 20:50 data as baseline)
        if 'error' not in ig_metrics:
            updated_config['spread_costs']['SPY'] = ig_metrics['avg_spread_pct']

        # Update slippage
        if 'error' not in ig_metrics:
            updated_config['slippage_pct']['SPY'] = ig_metrics['avg_slippage_pct']

        # Update close timing penalty (from timing comparison)
        if 'error' not in timing_data:
            updated_config['close_timing_penalty']['SPY'] = timing_data['avg_timing_penalty']

        # Update PnL multiplier (from actual wins)
        if 'error' not in ig_metrics and ig_metrics['wins'] > 0:
            avg_cost = ig_metrics['avg_total_cost_pct']
            avg_win = ig_metrics['avg_win_pct']

            new_multiplier = self.calculate_pnl_multiplier(avg_win, avg_cost)

            updated_config['pnl_adjustments']['1_day']['SPY'] = new_multiplier

        return updated_config

    def print_analysis(
        self,
        ig_metrics: Dict,
        ibkr_metrics: Dict,
        timing_data: Dict
    ):
        """Print analysis results."""
        console.print("\n" + "=" * 80)
        console.print("[bold]CALIBRATION ANALYSIS[/bold]")
        console.print("=" * 80 + "\n")

        # IG.com metrics
        if 'error' not in ig_metrics:
            console.print("[bold cyan]IG.com Demo (20:50 entries)[/bold cyan]\n")

            table = Table(show_header=True)
            table.add_column("Metric", style="cyan")
            table.add_column("Value", style="white")

            table.add_row("Trades Analyzed", str(ig_metrics['trade_count']))
            table.add_row("Win Rate", f"{ig_metrics['win_rate']*100:.1f}%")
            table.add_row("Avg Spread", f"{ig_metrics['avg_spread_pct']*100:.2f}%")
            table.add_row("Avg Slippage", f"{ig_metrics['avg_slippage_pct']*100:.2f}%")
            table.add_row("Avg Total Cost", f"{ig_metrics['avg_total_cost_pct']*100:.2f}%")
            table.add_row("Avg Win", f"{ig_metrics['avg_win_pct']*100:.1f}%")
            table.add_row("Avg Loss", f"{ig_metrics['avg_loss_pct']*100:.1f}%")

            console.print(table)
            console.print()

        # Timing comparison
        if 'error' not in timing_data:
            console.print("[bold cyan]Timing Penalty Analysis (20:50 vs 21:00)[/bold cyan]\n")

            table = Table(show_header=True)
            table.add_column("Metric", style="cyan")
            table.add_column("Value", style="white")

            table.add_row("Trade Pairs", str(timing_data['comparisons']))
            table.add_row("Avg Cost @ 20:50", f"{timing_data['avg_cost_2050']*100:.2f}%")
            table.add_row("Avg Cost @ 21:00", f"{timing_data['avg_cost_2100']*100:.2f}%")
            table.add_row("Timing Penalty", f"{timing_data['avg_timing_penalty']*100:.2f}%")

            console.print(table)
            console.print()

        # IBKR metrics
        if 'error' not in ibkr_metrics:
            console.print("[bold cyan]IBKR (Manual entries)[/bold cyan]\n")

            table = Table(show_header=True)
            table.add_column("Metric", style="cyan")
            table.add_column("Value", style="white")

            table.add_row("Trades Analyzed", str(ibkr_metrics['trade_count']))
            table.add_row("Win Rate", f"{ibkr_metrics['win_rate']*100:.1f}%")
            table.add_row("Avg Spread", f"{ibkr_metrics['avg_spread_pct']*100:.2f}%")
            table.add_row("Avg Slippage", f"{ibkr_metrics['avg_slippage_pct']*100:.2f}%")
            table.add_row("Avg Total Cost", f"{ibkr_metrics['avg_total_cost_pct']*100:.2f}%")

            console.print(table)
            console.print()

    def print_recommendations(
        self,
        ig_metrics: Dict,
        updated_config: Dict
    ):
        """Print recommendations based on analysis."""
        console.print("=" * 80)
        console.print("[bold]RECOMMENDATIONS[/bold]")
        console.print("=" * 80 + "\n")

        if 'error' in ig_metrics:
            console.print("[yellow]Not enough data to make recommendations yet[/yellow]")
            return

        total_cost = ig_metrics['avg_total_cost_pct']

        # Determine viability
        if total_cost < 0.05:
            verdict = "[bold green]EXCELLENT[/bold green]"
            message = "Strategy should work very well (25-35% CAGR)"
        elif total_cost < 0.08:
            verdict = "[bold yellow]GOOD[/bold yellow]"
            message = "Strategy viable with good execution (15-25% CAGR)"
        elif total_cost < 0.10:
            verdict = "[bold yellow]MARGINAL[/bold yellow]"
            message = "Strategy barely profitable (10-15% CAGR), tight margins"
        else:
            verdict = "[bold red]NOT VIABLE[/bold red]"
            message = "Costs too high - strategy is NOT profitable"

        console.print(f"Verdict: {verdict}")
        console.print(f"Total costs: {total_cost*100:.2f}%")
        console.print(f"{message}\n")

        # Position sizing recommendation
        if total_cost < 0.08:
            console.print("[bold]Recommended Position Sizing:[/bold]")
            console.print("  - Start with 5% of account (Half Kelly)")
            console.print("  - Max drawdown: 30-40%")
            console.print("  - Expected CAGR: 10-20%")
        else:
            console.print("[bold]Recommendation:[/bold]")
            console.print("  - Continue paper trading to reduce costs")
            console.print("  - Or find better execution venue")
            console.print("  - DO NOT trade live with these costs")

        console.print()

    def run_calibration(self):
        """Run full calibration process."""
        console.print("\n" + "=" * 80)
        console.print("[bold]AUTO-CALIBRATION FROM PAPER TRADING[/bold]")
        console.print("=" * 80 + "\n")

        # Analyze IG.com trades
        console.print("[cyan]Analyzing IG.com paper trades...[/cyan]")
        ig_metrics = self.analyze_trades('ig_paper_2050')

        # Analyze timing
        console.print("[cyan]Analyzing timing penalty (20:50 vs 21:00)...[/cyan]")
        timing_data = self.compare_timing()

        # Analyze IBKR trades
        console.print("[cyan]Analyzing IBKR trades...[/cyan]")
        ibkr_metrics = self.analyze_trades('ibkr')

        # Print analysis
        self.print_analysis(ig_metrics, ibkr_metrics, timing_data)

        # Check if enough data
        if 'error' in ig_metrics:
            console.print("[yellow]Not enough IG.com data yet for calibration[/yellow]")
            console.print(f"Current: {ig_metrics.get('pending', 0)} trades")
            console.print("Need: 10+ completed trades\n")
            return

        # Generate calibrated config
        updated_config = self.generate_calibrated_config(
            ig_metrics,
            ibkr_metrics,
            timing_data
        )

        # Show recommendations
        self.print_recommendations(ig_metrics, updated_config)

        # Show proposed changes
        console.print("=" * 80)
        console.print("[bold]PROPOSED CONFIG UPDATES[/bold]")
        console.print("=" * 80 + "\n")

        with open(self.config_path, 'r') as f:
            current_config = json.load(f)

        changes = []

        # Compare changes
        if updated_config['spread_costs']['SPY'] != current_config['spread_costs']['SPY']:
            changes.append(('spread_costs.SPY',
                           current_config['spread_costs']['SPY'],
                           updated_config['spread_costs']['SPY']))

        if updated_config['slippage_pct']['SPY'] != current_config['slippage_pct']['SPY']:
            changes.append(('slippage_pct.SPY',
                           current_config['slippage_pct']['SPY'],
                           updated_config['slippage_pct']['SPY']))

        if updated_config['close_timing_penalty']['SPY'] != current_config['close_timing_penalty']['SPY']:
            changes.append(('close_timing_penalty.SPY',
                           current_config['close_timing_penalty']['SPY'],
                           updated_config['close_timing_penalty']['SPY']))

        if updated_config['pnl_adjustments']['1_day']['SPY'] != current_config['pnl_adjustments']['1_day']['SPY']:
            changes.append(('pnl_adjustments.1_day.SPY',
                           current_config['pnl_adjustments']['1_day']['SPY'],
                           updated_config['pnl_adjustments']['1_day']['SPY']))

        if changes:
            table = Table(show_header=True)
            table.add_column("Parameter", style="cyan")
            table.add_column("Current", style="yellow")
            table.add_column("Calibrated", style="green")

            for param, old, new in changes:
                table.add_row(param, f"{old:.4f}", f"{new:.4f}")

            console.print(table)
            console.print()
        else:
            console.print("[yellow]No changes needed - current config is accurate[/yellow]\n")

        # Ask for confirmation
        response = console.input("\n[bold]Update config/reality_adjustments.json? (yes/no): [/bold]")

        if response.lower() in ['yes', 'y']:
            # Backup current config
            backup_path = self.config_path.with_suffix('.json.backup')
            with open(backup_path, 'w') as f:
                json.dump(current_config, f, indent=2)

            console.print(f"[cyan]Backed up current config to {backup_path}[/cyan]")

            # Save updated config
            with open(self.config_path, 'w') as f:
                json.dump(updated_config, f, indent=2)

            console.print(f"[green]Updated {self.config_path}[/green]\n")

            # Offer to run backtest
            response2 = console.input("[bold]Run backtest with calibrated values? (yes/no): [/bold]")

            if response2.lower() in ['yes', 'y']:
                console.print("\n[cyan]Running backtest...[/cyan]\n")
                import subprocess
                subprocess.run([
                    "python",
                    "scripts/backtesting/run_backtest_ig_short_expiries_reality.py"
                ])
        else:
            console.print("[yellow]Config not updated[/yellow]")


def main():
    """Main execution."""
    calibrator = AutoCalibrator()
    calibrator.run_calibration()


if __name__ == "__main__":
    main()
