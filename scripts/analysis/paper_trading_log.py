"""
Paper Trading Log & Comparison Framework

Purpose: Track actual IG.com paper trades and compare with backtest predictions

Daily Workflow:
1. Run backtest for today's date -> Get prediction
2. Place actual paper trade on IG.com -> Log actual fills
3. Track trade until close -> Log actual exit
4. Compare actual vs predicted -> Calculate adjustment factors
5. Update backtest parameters weekly

Files Created:
- logs/paper_trades.csv: All paper trades
- logs/backtest_predictions.csv: Backtest predictions for same dates
- logs/discrepancies.csv: Differences between actual and predicted
"""
import pandas as pd
import json
from datetime import datetime, timedelta
from pathlib import Path

# Ensure logs directory exists
LOGS_DIR = Path("logs")
LOGS_DIR.mkdir(exist_ok=True)

PAPER_TRADES_LOG = LOGS_DIR / "paper_trades.csv"
BACKTEST_PREDICTIONS_LOG = LOGS_DIR / "backtest_predictions.csv"
DISCREPANCIES_LOG = LOGS_DIR / "discrepancies.csv"

def log_backtest_prediction(
    date,
    ticker,
    signal,
    entry_price,
    strike,
    target_price,
    expiry_date,
    days_to_expiry,
    predicted_win_prob,
    predicted_pnl_pct
):
    """
    Log what the backtest predicts for today's trade

    Call this BEFORE placing the paper trade
    """
    prediction = {
        'Date': date,
        'Ticker': ticker,
        'Signal': signal,
        'Entry_Price': entry_price,
        'Strike': strike,
        'Target_Price': target_price,
        'Expiry_Date': expiry_date,
        'Days_To_Expiry': days_to_expiry,
        'Predicted_Win_Prob': predicted_win_prob,
        'Predicted_PnL_Pct': predicted_pnl_pct,
        'Logged_At': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }

    df = pd.DataFrame([prediction])

    if BACKTEST_PREDICTIONS_LOG.exists():
        df.to_csv(BACKTEST_PREDICTIONS_LOG, mode='a', header=False, index=False)
    else:
        df.to_csv(BACKTEST_PREDICTIONS_LOG, mode='w', header=True, index=False)

    print(f"Logged backtest prediction: {ticker} {signal}")
    return prediction

def log_paper_trade_entry(
    date,
    ticker,
    signal,
    option_type,
    strike,
    expiry_date,
    days_to_expiry,
    entry_time,
    underlying_price_at_entry,
    bid,
    ask,
    mid,
    filled_price,
    contracts,
    commission,
    order_id=None,
    notes=""
):
    """
    Log actual paper trade ENTRY on IG.com

    Call this AFTER placing the order and getting fill confirmation
    """
    spread_pct = ((ask - bid) / mid) * 100 if mid > 0 else 0
    slippage = filled_price - mid
    slippage_pct = (slippage / mid) * 100 if mid > 0 else 0

    entry = {
        'Trade_ID': f"{date}_{ticker}_{option_type}",
        'Date': date,
        'Ticker': ticker,
        'Signal': signal,
        'Option_Type': option_type,
        'Strike': strike,
        'Expiry_Date': expiry_date,
        'Days_To_Expiry': days_to_expiry,
        'Entry_Time': entry_time,
        'Underlying_At_Entry': underlying_price_at_entry,
        'Bid': bid,
        'Ask': ask,
        'Mid': mid,
        'Filled_Price': filled_price,
        'Contracts': contracts,
        'Commission': commission,
        'Spread_Pct': spread_pct,
        'Slippage': slippage,
        'Slippage_Pct': slippage_pct,
        'Order_ID': order_id,
        'Status': 'OPEN',
        'Exit_Time': None,
        'Exit_Price': None,
        'Exit_Underlying': None,
        'Result': None,
        'PnL': None,
        'PnL_Pct': None,
        'Notes': notes,
        'Logged_At': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }

    df = pd.DataFrame([entry])

    if PAPER_TRADES_LOG.exists():
        df.to_csv(PAPER_TRADES_LOG, mode='a', header=False, index=False)
    else:
        df.to_csv(PAPER_TRADES_LOG, mode='w', header=True, index=False)

    print(f"Logged paper trade entry: {ticker} {option_type} @ ${filled_price:.2f}")
    print(f"  Spread: {spread_pct:.1f}%, Slippage: {slippage_pct:.1f}%")
    return entry

def log_paper_trade_exit(
    trade_id,
    exit_time,
    exit_price,
    exit_underlying,
    result,
    exit_bid=None,
    exit_ask=None,
    exit_commission=0,
    notes=""
):
    """
    Log actual paper trade EXIT on IG.com

    Call this AFTER closing the position
    """
    # Load existing trades
    if not PAPER_TRADES_LOG.exists():
        print("ERROR: No paper trades log found!")
        return

    df = pd.read_csv(PAPER_TRADES_LOG)

    # Find the trade
    trade_idx = df[df['Trade_ID'] == trade_id].index

    if len(trade_idx) == 0:
        print(f"ERROR: Trade {trade_id} not found!")
        return

    idx = trade_idx[0]

    # Calculate P&L
    entry_price = df.loc[idx, 'Filled_Price']
    contracts = df.loc[idx, 'Contracts']
    entry_commission = df.loc[idx, 'Commission']

    gross_pnl = (exit_price - entry_price) * contracts
    total_commission = entry_commission + exit_commission
    net_pnl = gross_pnl - total_commission
    pnl_pct = (net_pnl / (entry_price * contracts)) * 100 if (entry_price * contracts) > 0 else 0

    # Update the trade
    df.loc[idx, 'Exit_Time'] = exit_time
    df.loc[idx, 'Exit_Price'] = exit_price
    df.loc[idx, 'Exit_Underlying'] = exit_underlying
    df.loc[idx, 'Result'] = result
    df.loc[idx, 'PnL'] = net_pnl
    df.loc[idx, 'PnL_Pct'] = pnl_pct
    df.loc[idx, 'Status'] = 'CLOSED'
    df.loc[idx, 'Notes'] = df.loc[idx, 'Notes'] + "; " + notes if df.loc[idx, 'Notes'] else notes

    # Save
    df.to_csv(PAPER_TRADES_LOG, index=False)

    print(f"Logged paper trade exit: {trade_id}")
    print(f"  P&L: ${net_pnl:.2f} ({pnl_pct:.1f}%)")
    print(f"  Result: {result}")

    return df.loc[idx].to_dict()

def compare_actual_vs_backtest(date):
    """
    Compare actual paper trading results vs backtest predictions for a given date

    Run this AFTER trade closes
    """
    if not PAPER_TRADES_LOG.exists() or not BACKTEST_PREDICTIONS_LOG.exists():
        print("ERROR: Missing log files!")
        return

    df_actual = pd.read_csv(PAPER_TRADES_LOG)
    df_predicted = pd.read_csv(BACKTEST_PREDICTIONS_LOG)

    # Filter by date
    actual_trades = df_actual[df_actual['Date'] == date]
    predicted_trades = df_predicted[df_predicted['Date'] == date]

    if len(actual_trades) == 0:
        print(f"No actual trades found for {date}")
        return

    if len(predicted_trades) == 0:
        print(f"No backtest predictions found for {date}")
        return

    print("="*80)
    print(f"COMPARISON: {date}")
    print("="*80)
    print()

    discrepancies = []

    for _, predicted in predicted_trades.iterrows():
        ticker = predicted['Ticker']

        # Find matching actual trade
        actual = actual_trades[actual_trades['Ticker'] == ticker]

        if len(actual) == 0:
            print(f"{ticker}: NO ACTUAL TRADE (predicted but not executed)")
            continue

        actual = actual.iloc[0]

        # Compare entry
        entry_diff = actual['Filled_Price'] - predicted['Entry_Price']

        # Compare result
        predicted_result = "WIN" if predicted['Predicted_Win_Prob'] > 50 else "LOSS"
        actual_result = actual['Result'] if pd.notna(actual['Result']) else "OPEN"

        # Compare P&L
        if pd.notna(actual['PnL_Pct']):
            pnl_diff = actual['PnL_Pct'] - predicted['Predicted_PnL_Pct']
        else:
            pnl_diff = None

        print(f"{ticker} {predicted['Signal']}:")
        print(f"  Entry Price:")
        print(f"    Predicted: ${predicted['Entry_Price']:.2f}")
        print(f"    Actual: ${actual['Filled_Price']:.2f} (diff: ${entry_diff:.2f})")
        print(f"  Spread: {actual['Spread_Pct']:.1f}%")
        print(f"  Slippage: {actual['Slippage_Pct']:.1f}%")
        print(f"  Result:")
        print(f"    Predicted: {predicted_result} ({predicted['Predicted_Win_Prob']:.0f}%)")
        print(f"    Actual: {actual_result}")
        if pnl_diff is not None:
            print(f"  P&L:")
            print(f"    Predicted: {predicted['Predicted_PnL_Pct']:.1f}%")
            print(f"    Actual: {actual['PnL_Pct']:.1f}% (diff: {pnl_diff:+.1f}pp)")
        print()

        # Log discrepancy
        discrepancy = {
            'Date': date,
            'Ticker': ticker,
            'Signal': predicted['Signal'],
            'Entry_Diff': entry_diff,
            'Spread_Pct': actual['Spread_Pct'],
            'Slippage_Pct': actual['Slippage_Pct'],
            'Predicted_Result': predicted_result,
            'Actual_Result': actual_result,
            'Predicted_PnL_Pct': predicted['Predicted_PnL_Pct'],
            'Actual_PnL_Pct': actual['PnL_Pct'] if pd.notna(actual['PnL_Pct']) else None,
            'PnL_Diff': pnl_diff
        }

        discrepancies.append(discrepancy)

    # Save discrepancies
    if discrepancies:
        df_disc = pd.DataFrame(discrepancies)
        if DISCREPANCIES_LOG.exists():
            df_disc.to_csv(DISCREPANCIES_LOG, mode='a', header=False, index=False)
        else:
            df_disc.to_csv(DISCREPANCIES_LOG, mode='w', header=True, index=False)

    return discrepancies

def calculate_adjustment_factors():
    """
    Calculate adjustment factors from accumulated paper trading data

    Run this WEEKLY to update backtest parameters
    """
    if not DISCREPANCIES_LOG.exists():
        print("No discrepancies log found. Need at least 1 week of paper trading!")
        return

    df = pd.read_csv(DISCREPANCIES_LOG)

    # Remove rows with missing data
    df = df.dropna(subset=['Actual_PnL_Pct', 'PnL_Diff'])

    if len(df) == 0:
        print("No completed trades to analyze yet!")
        return

    print("="*80)
    print("ADJUSTMENT FACTORS (from paper trading)")
    print("="*80)
    print()

    # Overall adjustment
    avg_predicted = df['Predicted_PnL_Pct'].mean()
    avg_actual = df['Actual_PnL_Pct'].mean()
    adjustment_factor = avg_actual / avg_predicted if avg_predicted != 0 else 1.0

    print(f"Overall:")
    print(f"  Predicted P&L (avg): {avg_predicted:.1f}%")
    print(f"  Actual P&L (avg): {avg_actual:.1f}%")
    print(f"  ADJUSTMENT FACTOR: {adjustment_factor:.2f}x")
    print()

    # By ticker
    print("By Ticker:")
    for ticker in df['Ticker'].unique():
        df_ticker = df[df['Ticker'] == ticker]
        ticker_predicted = df_ticker['Predicted_PnL_Pct'].mean()
        ticker_actual = df_ticker['Actual_PnL_Pct'].mean()
        ticker_adjustment = ticker_actual / ticker_predicted if ticker_predicted != 0 else 1.0

        avg_spread = df_ticker['Spread_Pct'].mean()
        avg_slippage = df_ticker['Slippage_Pct'].mean()

        print(f"  {ticker}:")
        print(f"    Predicted: {ticker_predicted:.1f}%")
        print(f"    Actual: {ticker_actual:.1f}%")
        print(f"    Adjustment: {ticker_adjustment:.2f}x")
        print(f"    Avg Spread: {avg_spread:.1f}%")
        print(f"    Avg Slippage: {avg_slippage:.1f}%")
        print()

    # Win rate accuracy
    df_closed = df[df['Actual_Result'].isin(['WIN', 'LOSS'])]
    if len(df_closed) > 0:
        predicted_wins = (df_closed['Predicted_Result'] == 'WIN').sum()
        actual_wins = (df_closed['Actual_Result'] == 'WIN').sum()
        predicted_wr = predicted_wins / len(df_closed) * 100
        actual_wr = actual_wins / len(df_closed) * 100

        print("Win Rate:")
        print(f"  Predicted: {predicted_wr:.1f}%")
        print(f"  Actual: {actual_wr:.1f}%")
        print(f"  Difference: {actual_wr - predicted_wr:+.1f}pp")
        print()

    return {
        'overall_adjustment': adjustment_factor,
        'avg_predicted_pnl': avg_predicted,
        'avg_actual_pnl': avg_actual
    }

# Example usage
if __name__ == "__main__":
    print("="*80)
    print("PAPER TRADING LOG FRAMEWORK")
    print("="*80)
    print()
    print("Usage Example:")
    print()
    print("1. BEFORE trading (morning):")
    print("   log_backtest_prediction('2026-02-05', 'SPY', 'BUY CALL', ...)")
    print()
    print("2. AFTER placing order (16:00 ET):")
    print("   log_paper_trade_entry('2026-02-05', 'SPY', 'BUY CALL', ...)")
    print()
    print("3. AFTER closing trade (next day):")
    print("   log_paper_trade_exit('2026-02-05_SPY_CALL', ...)")
    print()
    print("4. COMPARE results:")
    print("   compare_actual_vs_backtest('2026-02-05')")
    print()
    print("5. WEEKLY review:")
    print("   calculate_adjustment_factors()")
    print()
    print("Files created in logs/ directory:")
    print("  - paper_trades.csv")
    print("  - backtest_predictions.csv")
    print("  - discrepancies.csv")
