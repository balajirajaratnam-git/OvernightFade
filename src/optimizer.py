import pandas as pd
import numpy as np
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import track
from backtester import Backtester

console = Console()

class Optimizer:
    def __init__(self):
        self.bt = Backtester()
        
    def run(self):
        console.clear()
        console.print(Panel.fit("Overnight Fade | Parameter Optimization Engine", style="bold magenta"))

        if self.bt.daily_df.empty:
            console.print("[red]Error: No Data Found. Run data_manager.py first.[/red]")
            return

        # Define the Range to Test (0.1x ATR to 2.0x ATR)
        test_range = np.arange(0.1, 2.1, 0.1)
        
        results = []
        
        # Run the Simulation Loop
        print("\n")
        for multiplier in track(test_range, description="Simulating Strategies..."):
            # Run Backtest with specific parameter
            df_res = self.bt.run(take_profit_atr_mult=multiplier)
            
            if df_res.empty:
                continue
            
            # Calculate Metrics
            total_trades = len(df_res)
            wins = len(df_res[df_res["Result"] == "WIN"])
            win_rate = (wins / total_trades) * 100
            total_pnl = df_res["PnL_Dollar"].sum()
            # FIX: Changed from 'Payout_Mult' to 'PnL_Mult'
            avg_pnl_mult = df_res["PnL_Mult"].mean()
            
            results.append({
                "Target_ATR": round(multiplier, 1),
                "Trades": total_trades,
                "Win_Rate": win_rate,
                "Total_PnL": total_pnl,
                "Avg_PnL_Mult": avg_pnl_mult
            })

        # Create DataFrame for Analysis
        df_opt = pd.DataFrame(results)
        
        if df_opt.empty:
            console.print("[red]No trades generated for any parameter.[/red]")
            return

        # Find the "Best" Strategy (Highest Total PnL)
        best_run = df_opt.loc[df_opt["Total_PnL"].idxmax()]
        
        # --- DISPLAY RESULTS ---
        
        table = Table(title="Optimization Results (Sorted by PnL)", border_style="cyan")
        table.add_column("Target (ATR)", justify="center", style="cyan")
        table.add_column("Win Rate", justify="center")
        table.add_column("Avg PnL (R)", justify="center")
        table.add_column("Total Trades", justify="center")
        table.add_column("Total PnL ($)", justify="right", style="bold green")
        
        # Sort by PnL Descending
        df_sorted = df_opt.sort_values(by="Total_PnL", ascending=False)
        
        for _, row in df_sorted.iterrows():
            # Highlight the winner
            style = "bold yellow" if row["Target_ATR"] == best_run["Target_ATR"] else None
            
            table.add_row(
                f"{row['Target_ATR']}x",
                f"{row['Win_Rate']:.1f}%",
                f"{row['Avg_PnL_Mult']:+.2f}R", # Display as Net R-Multiple
                str(int(row['Trades'])),
                f"${row['Total_PnL']:,.2f}",
                style=style
            )
            
        console.print(table)
        
        # The Verdict
        console.print(Panel(
            f"[bold]OPTIMAL STRATEGY FOUND:[/bold]\n\n"
            f"Target: [bold cyan]{best_run['Target_ATR']}x ATR[/bold cyan]\n"
            f"Expectancy: [green]${best_run['Total_PnL']:,.2f}[/green] Total Profit\n"
            f"Win Rate: {best_run['Win_Rate']:.1f}%\n\n"
            f"Advice: Set your default_take_profit_atr to [bold]{best_run['Target_ATR']}[/bold] in config.json.",
            title="Recommendation",
            border_style="green"
        ))
        
        df_opt.to_csv("optimization_results.csv", index=False)
        console.print("\n[dim]Detailed results saved to 'optimization_results.csv'[/dim]")

if __name__ == "__main__":
    opt = Optimizer()
    opt.run()