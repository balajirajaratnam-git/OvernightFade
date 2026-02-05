"""
PHASE 3 - OPTION C: 1/10 Kelly (5.24%)
Complete end-to-end analysis with validation and historical week analysis
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

print("="*80)
print("PHASE 3 - OPTION C: 1/10 KELLY COMPLETE ANALYSIS")
print("="*80)
print()

# ============================================================================
# STEP 1: LOAD AND VALIDATE DATA
# ============================================================================
print("STEP 1: DATA VALIDATION")
print("-"*80)

df = pd.read_csv('results/trade_log_MULTI_TICKER_10year_FINAL.csv')
df['Date'] = pd.to_datetime(df['Date'])
df = df.sort_values('Date').reset_index(drop=True)

print(f"Total trades loaded: {len(df):,}")
print(f"Date range: {df['Date'].min().date()} to {df['Date'].max().date()}")
print(f"Tickers: {', '.join(df['Ticker'].unique())}")
print()

# Validate win rate
wins = (df['Result'] == 'WIN').sum()
losses = len(df) - wins
win_rate = wins / len(df)
print(f"Win rate: {win_rate*100:.2f}% ({wins:,} wins, {losses:,} losses)")

# Validate P/L multiples
win_mult = df[df['Result'] == 'WIN']['PnL_Mult'].mean()
loss_mult = df[df['Result'] != 'WIN']['PnL_Mult'].mean()
print(f"Avg win multiple: {win_mult:.2f}x")
print(f"Avg loss multiple: {loss_mult:.2f}x")
print()

# ============================================================================
# STEP 2: CALCULATE KELLY CRITERION
# ============================================================================
print("STEP 2: KELLY CRITERION CALCULATION")
print("-"*80)

# Kelly formula: (W*P - L) / P
# W = win probability, P = avg win payoff, L = avg loss payoff
W = win_rate
P = win_mult
L = abs(loss_mult)

kelly_full = (W * P - (1 - W) * L) / P
kelly_tenth = kelly_full * 0.1

print(f"Win probability (W): {W:.4f}")
print(f"Avg win payoff (P): {P:.4f}")
print(f"Avg loss payoff (L): {L:.4f}")
print()
print(f"Kelly formula: (W*P - (1-W)*L) / P")
print(f"Kelly = ({W:.4f} * {P:.4f} - {1-W:.4f} * {L:.4f}) / {P:.4f}")
print(f"Kelly = ({W*P:.4f} - {(1-W)*L:.4f}) / {P:.4f}")
print(f"Kelly = {W*P - (1-W)*L:.4f} / {P:.4f}")
print(f"Kelly = {kelly_full:.4f} = {kelly_full*100:.2f}%")
print()
print(f"1/10 Kelly = {kelly_full:.4f} * 0.1 = {kelly_tenth:.4f} = {kelly_tenth*100:.2f}%")
print()

# ============================================================================
# STEP 3: SIMULATE WITH 1/10 KELLY
# ============================================================================
print("STEP 3: SIMULATE FULL 10-YEAR PERIOD WITH 1/10 KELLY")
print("-"*80)

starting_capital = 10000
max_position = 1000
kelly_pct = kelly_tenth

equity = starting_capital
equity_curve = []
position_sizes = []
daily_pnls = []

print(f"Starting capital: ${starting_capital:,.2f}")
print(f"Kelly percentage: {kelly_pct*100:.2f}%")
print(f"Max position cap: ${max_position:,.2f}")
print()
print("Simulating all 6,960 trades...")
print()

for idx, row in df.iterrows():
    # Calculate position size
    position = min(equity * kelly_pct, max_position)

    # Calculate P/L
    pnl = position * row['PnL_Mult']

    # Update equity
    equity += pnl

    # Store for analysis
    equity_curve.append(equity)
    position_sizes.append(position)
    daily_pnls.append(pnl)

# Add to dataframe
df['Position_Size'] = position_sizes
df['PnL_Actual'] = daily_pnls
df['Equity'] = equity_curve

final_equity = equity
total_pnl = final_equity - starting_capital
total_roi = (total_pnl / starting_capital) * 100

# Calculate years (excluding partial 2026)
years = (df['Date'].max() - df['Date'].min()).days / 365.25
cagr = (pow(final_equity / starting_capital, 1/years) - 1) * 100

print(f"OK Simulation complete!")
print(f"Final equity: ${final_equity:,.2f}")
print(f"Total P/L: ${total_pnl:,.2f}")
print(f"Total ROI: {total_roi:,.1f}%")
print(f"CAGR: {cagr:.1f}%")
print()

# ============================================================================
# STEP 4: CALCULATE DRAWDOWNS
# ============================================================================
print("STEP 4: DRAWDOWN ANALYSIS")
print("-"*80)

df['Cumulative_PnL'] = df['PnL_Actual'].cumsum()
df['Running_Max'] = df['Equity'].expanding().max()
df['Drawdown'] = df['Equity'] - df['Running_Max']
df['Drawdown_Pct'] = (df['Drawdown'] / df['Running_Max']) * 100

max_drawdown = df['Drawdown'].min()
max_drawdown_pct = df['Drawdown_Pct'].min()
max_dd_date = df.loc[df['Drawdown'].idxmin(), 'Date']

print(f"Max drawdown: ${max_drawdown:,.2f} ({max_drawdown_pct:.1f}%)")
print(f"Occurred on: {max_dd_date.date()}")
print()

# ============================================================================
# STEP 5: YEARLY BREAKDOWN
# ============================================================================
print("STEP 5: YEAR-BY-YEAR PERFORMANCE")
print("-"*80)
print()

df['Year'] = df['Date'].dt.year
yearly_summary = []

year_start_equity = starting_capital

for year in sorted(df['Year'].unique()):
    year_df = df[df['Year'] == year]

    # Get equity at start and end of year
    year_end_equity = year_df['Equity'].iloc[-1]
    year_pnl = year_end_equity - year_start_equity
    year_roi = (year_pnl / year_start_equity) * 100

    # Stats
    trades = len(year_df)
    wins_year = (year_df['Result'] == 'WIN').sum()
    win_rate_year = wins_year / trades * 100

    yearly_summary.append({
        'Year': year,
        'Trades': trades,
        'Wins': wins_year,
        'Win_Rate': win_rate_year,
        'Start_Equity': year_start_equity,
        'End_Equity': year_end_equity,
        'Year_PnL': year_pnl,
        'Year_ROI': year_roi
    })

    print(f"{year}: {trades:3} trades, {win_rate_year:.1f}% WR, ${year_pnl:9,.0f} P/L")
    print(f"       ${year_start_equity:11,.0f} -> ${year_end_equity:11,.0f} ({year_roi:5.1f}% ROI)")
    print()

    year_start_equity = year_end_equity

print("-"*80)
print(f"TOTAL: ${total_pnl:,.0f} P/L, {total_roi:.1f}% ROI, {cagr:.1f}% CAGR")
print()

# ============================================================================
# STEP 6: WEEKLY ANALYSIS - FIND BEST AND WORST WEEKS
# ============================================================================
print("="*80)
print("STEP 6: HISTORICAL WEEKLY ANALYSIS (REAL DATA)")
print("="*80)
print()

# Create week identifier
df['Week'] = df['Date'].dt.to_period('W')

# Calculate weekly statistics
weekly_stats = df.groupby('Week').agg({
    'Date': ['first', 'last', 'count'],
    'PnL_Actual': 'sum',
    'Equity': ['first', 'last'],
    'Result': lambda x: (x == 'WIN').sum()
}).reset_index()

weekly_stats.columns = ['Week', 'Week_Start', 'Week_End', 'Trades', 'Week_PnL',
                        'Start_Equity', 'End_Equity', 'Wins']
weekly_stats['Week_ROI'] = (weekly_stats['Week_PnL'] / weekly_stats['Start_Equity']) * 100
weekly_stats['Win_Rate'] = (weekly_stats['Wins'] / weekly_stats['Trades']) * 100

# Find top 10 best and worst weeks
best_weeks = weekly_stats.nlargest(10, 'Week_PnL')
worst_weeks = weekly_stats.nsmallest(10, 'Week_PnL')

print("TOP 10 BEST WEEKS (Highest P/L)")
print("-"*80)
print(f"{'Week':<15} {'Trades':<7} {'Win Rate':<10} {'Start Equity':<15} {'Week P/L':<15} {'ROI':<10}")
print("-"*80)

for idx, row in best_weeks.iterrows():
    print(f"{row['Week_Start'].strftime('%Y-%m-%d'):<15} {int(row['Trades']):<7} "
          f"{row['Win_Rate']:>6.1f}%   ${row['Start_Equity']:>12,.0f}  "
          f"${row['Week_PnL']:>12,.0f}  {row['Week_ROI']:>6.1f}%")

print()
print()
print("TOP 10 WORST WEEKS (Lowest P/L)")
print("-"*80)
print(f"{'Week':<15} {'Trades':<7} {'Win Rate':<10} {'Start Equity':<15} {'Week P/L':<15} {'ROI':<10}")
print("-"*80)

for idx, row in worst_weeks.iterrows():
    print(f"{row['Week_Start'].strftime('%Y-%m-%d'):<15} {int(row['Trades']):<7} "
          f"{row['Win_Rate']:>6.1f}%   ${row['Start_Equity']:>12,.0f}  "
          f"${row['Week_PnL']:>12,.0f}  {row['Week_ROI']:>6.1f}%")

print()
print()

# ============================================================================
# STEP 7: DETAILED ANALYSIS OF BEST/WORST WEEKS
# ============================================================================
print("="*80)
print("STEP 7: DETAILED TRADE-BY-TRADE ANALYSIS")
print("="*80)
print()

print("BEST WEEK EXAMPLE (Week with highest P/L)")
print("-"*80)
best_week_start = best_weeks.iloc[0]['Week_Start']
best_week_end = best_weeks.iloc[0]['Week_End']
best_week_trades = df[(df['Date'] >= best_week_start) & (df['Date'] <= best_week_end)].copy()

print(f"Week: {best_week_start.strftime('%Y-%m-%d')} to {best_week_end.strftime('%Y-%m-%d')}")
print(f"Total trades: {len(best_week_trades)}")
print(f"Wins: {(best_week_trades['Result']=='WIN').sum()}, Losses: {(best_week_trades['Result']!='WIN').sum()}")
print()

prev_date = None
for idx, row in best_week_trades.iterrows():
    if row['Date'] != prev_date:
        if prev_date is not None:
            print()
        print(f"{row['Date'].strftime('%Y-%m-%d')}:")
        prev_date = row['Date']

    result = 'WIN' if row['Result'] == 'WIN' else 'LOSS'
    print(f"  {row['Ticker']}: {row['Signal']:12s} {result:4s} "
          f"(Pos: ${row['Position_Size']:6.0f}, P/L: ${row['PnL_Actual']:+7.0f}, "
          f"Equity: ${row['Equity']:,.0f})")

print()
print()

print("WORST WEEK EXAMPLE (Week with lowest P/L)")
print("-"*80)
worst_week_start = worst_weeks.iloc[0]['Week_Start']
worst_week_end = worst_weeks.iloc[0]['Week_End']
worst_week_trades = df[(df['Date'] >= worst_week_start) & (df['Date'] <= worst_week_end)].copy()

print(f"Week: {worst_week_start.strftime('%Y-%m-%d')} to {worst_week_end.strftime('%Y-%m-%d')}")
print(f"Total trades: {len(worst_week_trades)}")
print(f"Wins: {(worst_week_trades['Result']=='WIN').sum()}, Losses: {(worst_week_trades['Result']!='WIN').sum()}")
print()

prev_date = None
for idx, row in worst_week_trades.iterrows():
    if row['Date'] != prev_date:
        if prev_date is not None:
            print()
        print(f"{row['Date'].strftime('%Y-%m-%d')}:")
        prev_date = row['Date']

    result = 'WIN' if row['Result'] == 'WIN' else 'LOSS'
    print(f"  {row['Ticker']}: {row['Signal']:12s} {result:4s} "
          f"(Pos: ${row['Position_Size']:6.0f}, P/L: ${row['PnL_Actual']:+7.0f}, "
          f"Equity: ${row['Equity']:,.0f})")

print()
print()

# ============================================================================
# STEP 8: SUMMARY STATISTICS
# ============================================================================
print("="*80)
print("STEP 8: COMPREHENSIVE SUMMARY")
print("="*80)
print()

print("STRATEGY COMPARISON:")
print("-"*80)
print(f"{'Metric':<30} {'Fixed $100':<15} {'1/10 Kelly (5.2%)':<20}")
print("-"*80)
print(f"{'Starting Capital':<30} {'$10,000':<15} {'$10,000':<20}")
print(f"{'Final Equity':<30} {'$173,950':<15} ${final_equity:<19,.0f}")
print(f"{'Total P/L':<30} {'$163,950':<15} ${total_pnl:<19,.0f}")
print(f"{'Total ROI':<30} {'1,639.5%':<15} {f'{total_roi:.1f}%':<20}")
print(f"{'CAGR':<30} {'33.1%':<15} {f'{cagr:.1f}%':<20}")
print(f"{'Max Drawdown':<30} {'$-975':<15} ${max_drawdown:<19,.0f}")
print(f"{'Max Drawdown %':<30} {'-5.6%':<15} {f'{max_drawdown_pct:.1f}%':<20}")
print()

print("WEEKLY STATISTICS:")
print("-"*80)
print(f"Best week P/L: ${best_weeks.iloc[0]['Week_PnL']:,.0f} ({best_weeks.iloc[0]['Week_ROI']:.1f}% ROI)")
print(f"Worst week P/L: ${worst_weeks.iloc[0]['Week_PnL']:,.0f} ({worst_weeks.iloc[0]['Week_ROI']:.1f}% ROI)")
print(f"Avg week P/L: ${weekly_stats['Week_PnL'].mean():,.0f}")
print(f"Median week P/L: ${weekly_stats['Week_PnL'].median():,.0f}")
print()

print("RISK METRICS:")
print("-"*80)
print(f"Position size range: ${df['Position_Size'].min():.0f} - ${df['Position_Size'].max():.0f}")
print(f"Avg position size: ${df['Position_Size'].mean():.0f}")
print(f"Largest single loss: ${df['PnL_Actual'].min():.0f}")
print(f"Largest single win: ${df['PnL_Actual'].max():.0f}")
print()

# Save results
df.to_csv('results/phase3_option_c_detailed_results.csv', index=False)
weekly_stats.to_csv('results/phase3_option_c_weekly_stats.csv', index=False)

print("="*80)
print("OK ANALYSIS COMPLETE - All results saved")
print("="*80)
print()
print("Files saved:")
print("  - results/phase3_option_c_detailed_results.csv")
print("  - results/phase3_option_c_weekly_stats.csv")
