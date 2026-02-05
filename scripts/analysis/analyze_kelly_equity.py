"""
Detailed Kelly + Equity-Based analysis with yearly breakdown and examples
"""
import pandas as pd
import numpy as np

# Load data
df = pd.read_csv('results/trade_log_MULTI_TICKER_10year_FINAL.csv')
df['Date'] = pd.to_datetime(df['Date'])
df['Year'] = df['Date'].dt.year
df = df.sort_values('Date').reset_index(drop=True)

# Kelly Criterion parameters
win_rate = (df['Result'] == 'WIN').mean()
wins = df[df['Result'] == 'WIN']['PnL_Mult']
losses = df[df['Result'] != 'WIN']['PnL_Mult']
avg_win = wins.mean()
avg_loss = abs(losses.mean())

# Calculate Kelly%
kelly_full = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win
kelly_fractional = kelly_full * 0.25  # 1/4 Kelly for safety
max_position = 1000  # Cap at $1000

print(f"Kelly Criterion Calculation:")
print(f"  Win Rate: {win_rate*100:.1f}%")
print(f"  Avg Win: {avg_win:.2f}x")
print(f"  Avg Loss: {avg_loss:.2f}x")
print(f"  Full Kelly: {kelly_full*100:.2f}%")
print(f"  1/4 Kelly: {kelly_fractional*100:.2f}%")
print(f"  Max position cap: ${max_position}")
print()

# Simulate Kelly + Equity-Based
print("="*80)
print("KELLY + EQUITY-BASED - YEARLY PERFORMANCE")
print("="*80)
print(f"Strategy: Risk {kelly_fractional*100:.2f}% of current equity per trade (max $1000)")
print(f"Starting Capital: $10,000")
print()

starting_equity = 10000
equity = starting_equity

yearly_stats = []

for year in sorted(df['Year'].unique()):
    year_df = df[df['Year'] == year]
    year_start_equity = equity

    # Process each trade
    for idx, row in year_df.iterrows():
        # Position size = Kelly % of current equity, capped
        position_size = min(equity * kelly_fractional, max_position)

        # Calculate P/L
        pnl = row['PnL_Mult'] * position_size
        equity += pnl

    # Year-end stats
    trades = len(year_df)
    wins = (year_df['Result'] == 'WIN').sum()
    win_rate_year = wins / trades * 100
    year_pnl = equity - year_start_equity
    roi_year = (year_pnl / year_start_equity) * 100

    print(f"{year}: {trades:3} trades, {win_rate_year:.1f}% WR, ${year_pnl:10,.0f} P/L")
    print(f"       ${year_start_equity:14,.0f} -> ${equity:14,.0f} ({roi_year:6.1f}% ROI)")
    print()

# Total stats
total_pnl = equity - starting_equity
total_roi = (total_pnl / starting_equity) * 100
cagr = (pow(equity/starting_equity, 1/10) - 1) * 100

print("="*80)
print("TOTAL (10 years):")
print(f"  Total P/L: ${total_pnl:,.0f}")
print(f"  Final Equity: ${equity:,.0f}")
print(f"  Total ROI: {total_roi:,.1f}%")
print(f"  Annualized ROI (CAGR): {cagr:.1f}%")
print()

# Now show example weeks
print("="*80)
print("EXAMPLE WEEK 1: Good Week with 1 Loss (Feb-Mar 2016)")
print("="*80)
print()

sample1 = df.head(28)
equity_example = 10000

day_num = 0
prev_date = None

for idx, row in sample1.iterrows():
    if row['Date'] != prev_date:
        if prev_date is not None:
            print(f"  End of day equity: ${equity_example:,.2f}")
            print()
        day_num += 1
        print(f"Day {day_num}: {row['Date'].strftime('%Y-%m-%d')}")
        prev_date = row['Date']

    # Kelly position size
    position_size = min(equity_example * kelly_fractional, max_position)
    pnl = row['PnL_Mult'] * position_size
    equity_example += pnl

    result_str = 'WIN' if row['Result'] == 'WIN' else 'LOSS'
    print(f"  {row['Ticker']}: {row['Signal']:12s} -> {result_str:4s} "
          f"(Pos: ${position_size:6.2f}, P/L: {pnl:+8.2f}, Equity: ${equity_example:,.2f})")

    if day_num >= 7:
        break

print(f"  End of day equity: ${equity_example:,.2f}")
print()
print(f"Week P/L: ${equity_example - 10000:+,.2f}")
print(f"Week ROI: {(equity_example - 10000) / 10000 * 100:+.2f}%")
print()

# Example week 2 - tougher week
print("="*80)
print("EXAMPLE WEEK 2: Tough Week with 4 Losses (Jan 2021)")
print("="*80)
print()

sample2 = df[df['Date'].dt.year == 2021].head(27)
equity_example2 = 10000

day_num = 0
prev_date = None

for idx, row in sample2.iterrows():
    if row['Date'] != prev_date:
        if prev_date is not None:
            print(f"  End of day equity: ${equity_example2:,.2f}")
            print()
        day_num += 1
        print(f"Day {day_num}: {row['Date'].strftime('%Y-%m-%d')}")
        prev_date = row['Date']

    # Kelly position size
    position_size = min(equity_example2 * kelly_fractional, max_position)
    pnl = row['PnL_Mult'] * position_size
    equity_example2 += pnl

    result_str = 'WIN' if row['Result'] == 'WIN' else 'LOSS'
    print(f"  {row['Ticker']}: {row['Signal']:12s} -> {result_str:4s} "
          f"(Pos: ${position_size:6.2f}, P/L: {pnl:+8.2f}, Equity: ${equity_example2:,.2f})")

    if day_num >= 7:
        break

print(f"  End of day equity: ${equity_example2:,.2f}")
print()
print(f"Week P/L: ${equity_example2 - 10000:+,.2f}")
print(f"Week ROI: {(equity_example2 - 10000) / 10000 * 100:+.2f}%")
print()

# Comparison table
print("="*80)
print("COMPARISON: Fixed $100 vs Kelly + Equity-Based")
print("="*80)
print()
print(f"{'Metric':<30} {'Fixed $100':>15} {'Kelly+Equity':>15} {'Difference':>15}")
print("-"*80)
print(f"{'Starting Capital':<30} {'$10,000':>15} {'$10,000':>15} {'$0':>15}")
print(f"{'Final Equity (10 years)':<30} {'$173,950':>15} ${equity:>14,.0f} ${equity-173950:>14,.0f}")
print(f"{'Total ROI':<30} {'1,639.5%':>15} {f'{total_roi:,.1f}%':>15} {f'+{total_roi-1639.5:,.1f}%':>15}")
print(f"{'CAGR (annualized)':<30} {'33.1%':>15} {f'{cagr:.1f}%':>15} {f'+{cagr-33.1:.1f}%':>15}")
print(f"{'Max position size':<30} {'$100':>15} {'$625-1000':>15} {'Variable':>15}")
print()
