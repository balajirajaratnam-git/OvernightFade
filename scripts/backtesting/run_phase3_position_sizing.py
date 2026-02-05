"""
Phase 3: Dynamic Position Sizing
Test different position sizing strategies and compare to fixed sizing baseline
"""
import pandas as pd
import numpy as np
from datetime import datetime

def kelly_criterion(win_rate, avg_win, avg_loss):
    """
    Calculate Kelly Criterion optimal bet size.

    Kelly% = (W * P - L) / P
    where W = win rate, P = avg win, L = avg loss (absolute value)
    """
    if avg_loss == 0:
        return 0

    win_prob = win_rate
    loss_prob = 1 - win_rate
    win_amount = avg_win
    loss_amount = abs(avg_loss)

    # Kelly formula
    kelly = (win_prob * win_amount - loss_prob * loss_amount) / win_amount

    # Cap at 25% for safety (fractional Kelly)
    return min(max(kelly, 0), 0.25)

def calculate_position_sizes(df, strategy='fixed', initial_capital=10000):
    """
    Calculate position sizes based on different strategies.

    Strategies:
    - fixed: Fixed $100 per trade (baseline)
    - kelly: Kelly Criterion optimal sizing
    - volatility: Inverse volatility weighting
    - signal_strength: Size by signal magnitude
    - equity_based: Risk % of current equity
    - combined: Kelly + Signal Strength + Equity
    """
    results = df.copy()
    results['Position_Size'] = 100.0  # Default
    results['Account_Equity'] = initial_capital

    if strategy == 'fixed':
        # Baseline: Fixed $100 per trade
        results['Position_Size'] = 100.0

    elif strategy == 'kelly':
        # Kelly Criterion: Fixed size based on initial capital
        # Using 1/4 Kelly for safety (fractional Kelly)
        win_rate = (results['Result'] == 'WIN').mean()
        wins = results[results['Result'] == 'WIN']['PnL_Mult']
        losses = results[results['Result'] != 'WIN']['PnL_Mult']

        avg_win = wins.mean() if len(wins) > 0 else 0.45
        avg_loss = losses.mean() if len(losses) > 0 else -1.05

        kelly_pct = kelly_criterion(win_rate, avg_win, abs(avg_loss))

        # Use 1/4 Kelly (fractional Kelly for safety)
        fractional_kelly = kelly_pct * 0.25

        # Apply to initial capital only (no compounding)
        base_size = initial_capital * fractional_kelly
        results['Position_Size'] = base_size

        print(f"  Full Kelly%: {kelly_pct*100:.2f}%")
        print(f"  1/4 Kelly%: {fractional_kelly*100:.2f}% -> ${base_size:.0f} per trade")

    elif strategy == 'equity_based':
        # Risk 0.5% of current equity per trade (conservative)
        risk_pct = 0.005
        max_position = 1000  # Cap at $1000 per trade
        equity = initial_capital

        position_sizes = []
        equities = []

        for idx, row in results.iterrows():
            # Position size = 0.5% of current equity, capped
            position_size = min(equity * risk_pct, max_position)
            position_sizes.append(position_size)
            equities.append(equity)

            # Update equity
            pnl = row['PnL_Mult'] * position_size
            equity += pnl

        results['Position_Size'] = position_sizes
        results['Account_Equity'] = equities

        print(f"  Risk: 0.5% of equity per trade, capped at ${max_position}")

    elif strategy == 'signal_strength':
        # Not implemented without magnitude data
        # Use fixed for now
        results['Position_Size'] = 100.0

    elif strategy == 'combined':
        # Combined: 1/4 Kelly + Equity-based with cap
        win_rate = (results['Result'] == 'WIN').mean()
        wins = results[results['Result'] == 'WIN']['PnL_Mult']
        losses = results[results['Result'] != 'WIN']['PnL_Mult']

        avg_win = wins.mean() if len(wins) > 0 else 0.45
        avg_loss = losses.mean() if len(losses) > 0 else -1.05

        kelly_pct = kelly_criterion(win_rate, avg_win, abs(avg_loss))

        # Use 1/4 Kelly for safety
        fractional_kelly = kelly_pct * 0.25
        max_position = 1000  # Cap at $1000 per trade

        # Apply fractional Kelly with equity tracking
        equity = initial_capital
        position_sizes = []
        equities = []

        for idx, row in results.iterrows():
            # Fractional Kelly % of current equity, capped
            position_size = min(equity * fractional_kelly, max_position)
            position_sizes.append(position_size)
            equities.append(equity)

            # Update equity
            pnl = row['PnL_Mult'] * position_size
            equity += pnl

        results['Position_Size'] = position_sizes
        results['Account_Equity'] = equities

        print(f"  Full Kelly%: {kelly_pct*100:.2f}%")
        print(f"  1/4 Kelly%: {fractional_kelly*100:.2f}%, capped at ${max_position}")

    # Calculate actual P/L with new position sizes
    results['PnL_Actual'] = results['PnL_Mult'] * results['Position_Size']

    # Recalculate equity curve if not already done
    if 'Account_Equity' not in results.columns or strategy in ['fixed', 'kelly']:
        equity = initial_capital
        equities = []
        for pnl in results['PnL_Actual']:
            equities.append(equity)
            equity += pnl
        results['Account_Equity'] = equities

    return results

def calculate_metrics(df):
    """Calculate performance metrics."""
    total_trades = len(df)
    wins = (df['Result'] == 'WIN').sum()
    win_rate = wins / total_trades * 100

    total_pnl = df['PnL_Actual'].sum()
    avg_pnl = df['PnL_Actual'].mean()

    # Calculate max drawdown
    cumulative = df['PnL_Actual'].cumsum()
    running_max = cumulative.expanding().max()
    drawdown = cumulative - running_max
    max_drawdown = drawdown.min()

    # Final equity
    final_equity = df['Account_Equity'].iloc[-1] if 'Account_Equity' in df.columns else 10000 + total_pnl
    roi = (final_equity - 10000) / 10000 * 100

    # Sharpe-like ratio (simplified)
    if df['PnL_Actual'].std() > 0:
        sharpe = df['PnL_Actual'].mean() / df['PnL_Actual'].std() * np.sqrt(252)
    else:
        sharpe = 0

    return {
        'trades': total_trades,
        'win_rate': win_rate,
        'total_pnl': total_pnl,
        'avg_pnl': avg_pnl,
        'max_drawdown': max_drawdown,
        'final_equity': final_equity,
        'roi': roi,
        'sharpe': sharpe
    }

def main():
    print("="*80)
    print("PHASE 3: DYNAMIC POSITION SIZING ANALYSIS")
    print("="*80)
    print("\nComparing different position sizing strategies")
    print("Starting capital: $10,000\n")

    # Load baseline results
    df = pd.read_csv('results/trade_log_MULTI_TICKER_10year_FINAL.csv')
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.sort_values('Date').reset_index(drop=True)

    print(f"Loaded {len(df)} trades from 2016-2026\n")

    # Define strategies to test
    strategies = [
        ('fixed', 'Fixed $100 (Baseline)'),
        ('kelly', 'Kelly Criterion'),
        ('equity_based', '1% of Equity'),
        ('combined', 'Kelly + Equity-Based'),
    ]

    all_results = []

    for strategy_name, strategy_label in strategies:
        print(f"{'='*80}")
        print(f"Testing: {strategy_label}")
        print(f"{'='*80}")

        # Calculate position sizes
        results_df = calculate_position_sizes(df, strategy=strategy_name)

        # Calculate metrics
        metrics = calculate_metrics(results_df)
        metrics['strategy'] = strategy_label
        all_results.append(metrics)

        # Print summary
        print(f"\nResults:")
        print(f"  Trades: {metrics['trades']:,}")
        print(f"  Win Rate: {metrics['win_rate']:.1f}%")
        print(f"  Total P/L: ${metrics['total_pnl']:,.2f}")
        print(f"  Avg P/L: ${metrics['avg_pnl']:.2f}")
        print(f"  Max Drawdown: ${metrics['max_drawdown']:,.2f}")
        print(f"  Final Equity: ${metrics['final_equity']:,.2f}")
        print(f"  ROI: {metrics['roi']:.1f}%")
        print(f"  Sharpe Ratio: {metrics['sharpe']:.2f}")
        print()

    # Create comparison table
    print(f"{'='*80}")
    print("PHASE 3 RESULTS COMPARISON")
    print(f"{'='*80}\n")

    comparison = pd.DataFrame(all_results)
    comparison = comparison.set_index('strategy')

    # Calculate vs baseline
    baseline = comparison.loc['Fixed $100 (Baseline)']
    comparison['ROI_vs_Baseline'] = comparison['roi'] - baseline['roi']
    comparison['PnL_vs_Baseline'] = comparison['total_pnl'] - baseline['total_pnl']

    # Format for display
    display_df = comparison[[
        'trades', 'win_rate', 'total_pnl', 'avg_pnl',
        'max_drawdown', 'final_equity', 'roi', 'sharpe',
        'ROI_vs_Baseline', 'PnL_vs_Baseline'
    ]].copy()

    display_df['trades'] = display_df['trades'].astype(int)
    display_df = display_df.round(2)

    print(display_df.to_string())

    # Save results
    output_file = 'results/phase3_position_sizing_comparison.csv'
    display_df.to_csv(output_file)
    print(f"\n\nResults saved to: {output_file}")

    # Find best strategy
    best_roi = comparison['roi'].idxmax()
    best_sharpe = comparison['sharpe'].idxmax()

    print(f"\n{'='*80}")
    print("RECOMMENDATION")
    print(f"{'='*80}")

    print(f"\nBest ROI: {best_roi}")
    print(f"  ROI: {comparison.loc[best_roi, 'roi']:.1f}% (+{comparison.loc[best_roi, 'ROI_vs_Baseline']:.1f}%)")
    print(f"  Final Equity: ${comparison.loc[best_roi, 'final_equity']:,.2f}")
    print(f"  Total P/L: ${comparison.loc[best_roi, 'total_pnl']:,.2f}")
    print(f"  Max Drawdown: ${comparison.loc[best_roi, 'max_drawdown']:,.2f}")

    if best_sharpe != best_roi:
        print(f"\nBest Risk-Adjusted (Sharpe): {best_sharpe}")
        print(f"  Sharpe Ratio: {comparison.loc[best_sharpe, 'sharpe']:.2f}")
        print(f"  ROI: {comparison.loc[best_sharpe, 'roi']:.1f}%")
        print(f"  Max Drawdown: ${comparison.loc[best_sharpe, 'max_drawdown']:,.2f}")

    print(f"\n{'='*80}")
    print("PHASE 3 COMPLETE!")
    print(f"{'='*80}")

if __name__ == "__main__":
    main()
