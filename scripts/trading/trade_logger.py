"""
Trade Logger
Logs all trade data for calibration and analysis.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from rich.console import Console

console = Console()


class TradeLogger:
    """Handles logging of trade data to JSON files."""

    def __init__(self, log_dir: str = "logs"):
        """Initialize trade logger."""
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)

        # Log file paths
        self.ig_paper_2050_log = self.log_dir / "ig_paper_trades_2050.json"
        self.ig_paper_2100_log = self.log_dir / "ig_paper_trades_2100.json"
        self.ig_live_log = self.log_dir / "ig_live_trades.json"
        self.ibkr_log = self.log_dir / "ibkr_trades.json"

    def _load_log(self, log_file: Path) -> List[Dict]:
        """Load existing log file."""
        if log_file.exists():
            with open(log_file, 'r') as f:
                return json.load(f)
        return []

    def _save_log(self, log_file: Path, data: List[Dict]):
        """Save log file."""
        with open(log_file, 'w') as f:
            json.dump(data, f, indent=2)

    def log_trade(
        self,
        trade_type: str,  # 'ig_paper_2050', 'ig_paper_2100', 'ig_live', 'ibkr'
        trade_data: Dict
    ):
        """
        Log a trade.

        Args:
            trade_type: Type of trade (determines which log file)
            trade_data: Dictionary with all trade information
        """
        # Determine log file
        log_file_map = {
            'ig_paper_2050': self.ig_paper_2050_log,
            'ig_paper_2100': self.ig_paper_2100_log,
            'ig_live': self.ig_live_log,
            'ibkr': self.ibkr_log
        }

        log_file = log_file_map.get(trade_type)
        if not log_file:
            console.print(f"[red]Unknown trade type: {trade_type}[/red]")
            return

        # Load existing trades
        trades = self._load_log(log_file)

        # Add trade ID if not present
        if 'trade_id' not in trade_data:
            trade_data['trade_id'] = len(trades) + 1

        # Add log timestamp
        trade_data['logged_at'] = datetime.now().isoformat()

        # Append new trade
        trades.append(trade_data)

        # Save
        self._save_log(log_file, trades)

        console.print(f"[green]Trade #{trade_data['trade_id']} logged to {log_file.name}[/green]")

    def update_trade(
        self,
        trade_type: str,
        trade_id: int,
        update_data: Dict
    ):
        """
        Update an existing trade (e.g., add exit data).

        Args:
            trade_type: Type of trade
            trade_id: Trade ID to update
            update_data: Dictionary with fields to update
        """
        log_file_map = {
            'ig_paper_2050': self.ig_paper_2050_log,
            'ig_paper_2100': self.ig_paper_2100_log,
            'ig_live': self.ig_live_log,
            'ibkr': self.ibkr_log
        }

        log_file = log_file_map.get(trade_type)
        if not log_file:
            console.print(f"[red]Unknown trade type: {trade_type}[/red]")
            return

        # Load trades
        trades = self._load_log(log_file)

        # Find trade
        for trade in trades:
            if trade['trade_id'] == trade_id:
                # Update fields
                trade.update(update_data)
                trade['updated_at'] = datetime.now().isoformat()

                # Save
                self._save_log(log_file, trades)

                console.print(f"[green]Trade #{trade_id} updated in {log_file.name}[/green]")
                return

        console.print(f"[yellow]Trade #{trade_id} not found in {log_file.name}[/yellow]")

    def get_trade(self, trade_type: str, trade_id: int) -> Optional[Dict]:
        """Get a specific trade by ID."""
        log_file_map = {
            'ig_paper_2050': self.ig_paper_2050_log,
            'ig_paper_2100': self.ig_paper_2100_log,
            'ig_live': self.ig_live_log,
            'ibkr': self.ibkr_log
        }

        log_file = log_file_map.get(trade_type)
        if not log_file:
            return None

        trades = self._load_log(log_file)

        for trade in trades:
            if trade['trade_id'] == trade_id:
                return trade

        return None

    def get_all_trades(self, trade_type: str) -> List[Dict]:
        """Get all trades of a given type."""
        log_file_map = {
            'ig_paper_2050': self.ig_paper_2050_log,
            'ig_paper_2100': self.ig_paper_2100_log,
            'ig_live': self.ig_live_log,
            'ibkr': self.ibkr_log
        }

        log_file = log_file_map.get(trade_type)
        if not log_file:
            return []

        return self._load_log(log_file)

    def get_trade_count(self, trade_type: str) -> int:
        """Get number of trades logged."""
        return len(self.get_all_trades(trade_type))

    def create_trade_summary(self, trade_type: str) -> Dict:
        """
        Create summary statistics for trades.

        Returns:
            Dictionary with summary statistics.
        """
        trades = self.get_all_trades(trade_type)

        if not trades:
            return {
                'count': 0,
                'message': 'No trades logged yet'
            }

        # Filter trades with exit data (completed)
        completed = [t for t in trades if 'exit_price' in t and t['exit_price'] is not None]

        if not completed:
            return {
                'count': len(trades),
                'completed': 0,
                'message': 'No completed trades yet'
            }

        # Calculate statistics
        spreads = [t.get('spread_pct', 0) for t in completed if 'spread_pct' in t]
        slippages = [t.get('slippage_pct', 0) for t in completed if 'slippage_pct' in t]
        pnl_pcts = [t.get('pnl_pct', 0) for t in completed if 'pnl_pct' in t]

        wins = [t for t in completed if t.get('pnl_pct', 0) > 0]
        losses = [t for t in completed if t.get('pnl_pct', 0) <= 0]

        summary = {
            'count': len(trades),
            'completed': len(completed),
            'win_rate': len(wins) / len(completed) * 100 if completed else 0,
            'avg_spread_pct': sum(spreads) / len(spreads) * 100 if spreads else 0,
            'avg_slippage_pct': sum(slippages) / len(slippages) * 100 if slippages else 0,
            'avg_pnl_pct': sum(pnl_pcts) / len(pnl_pcts) * 100 if pnl_pcts else 0,
            'avg_win_pct': sum([t['pnl_pct'] for t in wins]) / len(wins) * 100 if wins else 0,
            'avg_loss_pct': sum([t['pnl_pct'] for t in losses]) / len(losses) * 100 if losses else 0
        }

        return summary

    def print_summary(self, trade_type: str):
        """Print trade summary to console."""
        summary = self.create_trade_summary(trade_type)

        console.print(f"\n[bold]Trade Summary: {trade_type.upper()}[/bold]\n")

        if summary['count'] == 0:
            console.print("[yellow]No trades logged yet[/yellow]")
            return

        console.print(f"Total Trades:     {summary['count']}")
        console.print(f"Completed:        {summary['completed']}")

        if summary['completed'] > 0:
            console.print(f"Win Rate:         {summary['win_rate']:.1f}%")
            console.print(f"Avg Spread:       {summary['avg_spread_pct']:.2f}%")
            console.print(f"Avg Slippage:     {summary['avg_slippage_pct']:.2f}%")
            console.print(f"Avg P&L:          {summary['avg_pnl_pct']:.2f}%")
            console.print(f"Avg Win:          {summary['avg_win_pct']:.2f}%")
            console.print(f"Avg Loss:         {summary['avg_loss_pct']:.2f}%")

        console.print()


def test_logger():
    """Test trade logger."""
    logger = TradeLogger()

    # Test logging a trade
    test_trade = {
        'date': '2026-02-06',
        'signal': 'CALL',
        'strike': 6800,
        'theoretical_price': 21.5,
        'entry_bid': 20.0,
        'entry_ask': 23.0,
        'entry_mid': 21.5,
        'fill_price': 22.0,
        'spread_pct': (22.0 - 21.5) / 21.5,
        'size': 4.5,
        'premium_paid': 99.0
    }

    logger.log_trade('ig_paper_2050', test_trade)

    # Test retrieving trade
    trade = logger.get_trade('ig_paper_2050', 1)
    console.print(f"\nRetrieved trade: {trade}\n")

    # Test summary
    logger.print_summary('ig_paper_2050')


if __name__ == "__main__":
    test_logger()
