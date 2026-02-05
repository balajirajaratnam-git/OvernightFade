"""
Withdrawal Analysis for 1/10 Kelly Strategy
Starting with $15,000, determine when cap is hit and sustainable withdrawal rates
"""
import pandas as pd
import numpy as np

print("="*80)
print("WITHDRAWAL ANALYSIS - 1/10 KELLY WITH $15,000 START")
print("="*80)
print()

# Parameters
starting_capital = 15000
kelly_pct = 0.0523
max_position = 1000

# Calculate equity needed to maintain $1000 position
min_equity_for_cap = max_position / kelly_pct

print("STEP 1: POSITION SIZE MECHANICS")
print("-"*80)
print(f"Kelly percentage: {kelly_pct*100:.2f}%")
print(f"Max position cap: ${max_position:,.0f}")
print(f"Starting capital: ${starting_capital:,.0f}")
print()
print(f"Position size formula: min(Equity × {kelly_pct:.4f}, ${max_position})")
print(f"To maintain $1,000 position: Equity >= ${min_equity_for_cap:,.0f}")
print()
print(f"Initial position size: ${starting_capital:,.0f} × {kelly_pct:.4f} = ${starting_capital * kelly_pct:,.0f}")
print()

# Load data
df = pd.read_csv('results/trade_log_MULTI_TICKER_10year_FINAL.csv')
df['Date'] = pd.to_datetime(df['Date'])
df = df.sort_values('Date').reset_index(drop=True)

print("="*80)
print("STEP 2: SIMULATE WITH $15,000 STARTING CAPITAL")
print("="*80)
print()

# Simulate with $15k start
equity = starting_capital
equity_curve = []
position_sizes = []
cap_hit_date = None
cap_hit_equity = None
cap_hit_trade = None

for idx, row in df.iterrows():
    # Calculate position size
    position = min(equity * kelly_pct, max_position)

    # Track when cap is first hit
    if position >= max_position and cap_hit_date is None:
        cap_hit_date = row['Date']
        cap_hit_equity = equity
        cap_hit_trade = idx + 1

    # Calculate P/L
    pnl = position * row['PnL_Mult']

    # Update equity
    equity += pnl

    equity_curve.append(equity)
    position_sizes.append(position)

df['Equity_15k'] = equity_curve
df['Position_15k'] = position_sizes
df['Year'] = df['Date'].dt.year

final_equity = equity

print(f"Cap hit on: {cap_hit_date.date()}")
print(f"After trade #{cap_hit_trade:,} of 6,960")
print(f"Equity when cap hit: ${cap_hit_equity:,.2f}")
print(f"Days into strategy: {(cap_hit_date - df['Date'].min()).days} days")
print()
print(f"Final equity (end of 10 years): ${final_equity:,.2f}")
print()

# Calculate performance from cap onwards
df_after_cap = df[df['Date'] >= cap_hit_date].copy()

print("="*80)
print("STEP 3: ANNUAL PERFORMANCE AFTER HITTING CAP")
print("="*80)
print()

yearly_performance = []

for year in sorted(df_after_cap['Year'].unique()):
    year_df = df_after_cap[df_after_cap['Year'] == year]

    year_start_equity = year_df['Equity_15k'].iloc[0]
    year_end_equity = year_df['Equity_15k'].iloc[-1]
    year_gain = year_end_equity - year_start_equity

    trades = len(year_df)

    yearly_performance.append({
        'Year': year,
        'Start_Equity': year_start_equity,
        'End_Equity': year_end_equity,
        'Annual_Gain': year_gain,
        'Trades': trades
    })

    print(f"{year}: ${year_start_equity:10,.0f} -> ${year_end_equity:10,.0f} "
          f"(+${year_gain:8,.0f}, {trades:3} trades)")

yearly_df = pd.DataFrame(yearly_performance)
avg_annual_gain = yearly_df['Annual_Gain'].mean()
median_annual_gain = yearly_df['Annual_Gain'].median()

print()
print(f"Average annual gain (after cap): ${avg_annual_gain:,.0f}")
print(f"Median annual gain (after cap): ${median_annual_gain:,.0f}")
print()

# Calculate sustainable withdrawal
print("="*80)
print("STEP 4: SUSTAINABLE WITHDRAWAL RATES")
print("="*80)
print()

# Conservative: Withdraw less than minimum annual gain
min_annual_gain = yearly_df['Annual_Gain'].min()
conservative_withdrawal = min_annual_gain * 0.9  # 90% of worst year

# Moderate: Withdraw average minus buffer for drawdowns
buffer = 20000  # Keep $20k buffer for max drawdown
moderate_withdrawal = avg_annual_gain - buffer

# Aggressive: Withdraw average
aggressive_withdrawal = avg_annual_gain

print(f"Minimum equity to maintain $1,000 position: ${min_equity_for_cap:,.0f}")
print(f"Recommended minimum equity (with buffer): ${min_equity_for_cap + 10000:,.0f}")
print()

print("WITHDRAWAL OPTIONS:")
print("-"*80)
print()

print(f"1. CONSERVATIVE (90% of worst year):")
print(f"   Annual withdrawal: ${conservative_withdrawal:,.0f}")
print(f"   Monthly withdrawal: ${conservative_withdrawal/12:,.0f}")
print(f"   Risk level: Very Low")
print(f"   Notes: Even in worst year, equity stays above ${min_equity_for_cap:,.0f}")
print()

print(f"2. MODERATE (Average - $20k buffer):")
print(f"   Annual withdrawal: ${moderate_withdrawal:,.0f}")
print(f"   Monthly withdrawal: ${moderate_withdrawal/12:,.0f}")
print(f"   Risk level: Low")
print(f"   Notes: Maintains buffer for drawdowns")
print()

print(f"3. AGGRESSIVE (Full average gain):")
print(f"   Annual withdrawal: ${aggressive_withdrawal:,.0f}")
print(f"   Monthly withdrawal: ${aggressive_withdrawal/12:,.0f}")
print(f"   Risk level: Medium")
print(f"   Notes: Withdraws all gains on average, may need to reduce in bad years")
print()

# Calculate what happens with moderate withdrawals
print("="*80)
print("STEP 5: SIMULATION WITH MODERATE WITHDRAWALS")
print("="*80)
print()

# Simulate with annual withdrawals
equity_with_wd = starting_capital
withdrawal_log = []

print(f"Strategy: Withdraw ${moderate_withdrawal:,.0f} at end of each year")
print()

for year in sorted(df['Year'].unique()):
    year_df = df[df['Year'] == year]
    year_start = equity_with_wd

    # Process all trades in year
    for idx, row in year_df.iterrows():
        position = min(equity_with_wd * kelly_pct, max_position)
        pnl = position * row['PnL_Mult']
        equity_with_wd += pnl

    year_end_before_wd = equity_with_wd

    # Withdraw at year end (if equity high enough and past cap date)
    if row['Date'] >= cap_hit_date and equity_with_wd > (min_equity_for_cap + moderate_withdrawal):
        withdrawal = moderate_withdrawal
        equity_with_wd -= withdrawal
    else:
        withdrawal = 0

    year_end_after_wd = equity_with_wd

    withdrawal_log.append({
        'Year': year,
        'Start': year_start,
        'Before_WD': year_end_before_wd,
        'Withdrawal': withdrawal,
        'After_WD': year_end_after_wd
    })

    if withdrawal > 0:
        print(f"{year}: ${year_start:10,.0f} -> ${year_end_before_wd:10,.0f} "
              f"- Withdraw ${withdrawal:8,.0f} = ${year_end_after_wd:10,.0f}")
    else:
        print(f"{year}: ${year_start:10,.0f} -> ${year_end_before_wd:10,.0f} "
              f"(Building to cap)")

wd_df = pd.DataFrame(withdrawal_log)
total_withdrawn = wd_df['Withdrawal'].sum()

print()
print(f"Total withdrawn over 10 years: ${total_withdrawn:,.0f}")
print(f"Final equity after withdrawals: ${equity_with_wd:,.0f}")
print(f"Total value (withdrawn + final): ${total_withdrawn + equity_with_wd:,.0f}")
print()

print("="*80)
print("SUMMARY & RECOMMENDATIONS")
print("="*80)
print()

print(f"Starting capital: ${starting_capital:,.0f}")
print(f"Cap hit after: {(cap_hit_date - df['Date'].min()).days} days (~{(cap_hit_date - df['Date'].min()).days/30:.0f} months)")
print(f"Equity at cap: ${cap_hit_equity:,.0f}")
print()

print("RECOMMENDED WITHDRAWAL STRATEGY:")
print("-"*80)
print(f"1. Build equity to ${min_equity_for_cap + 10000:,.0f} before withdrawing")
print(f"2. After that, withdraw ${moderate_withdrawal:,.0f}/year (${moderate_withdrawal/12:,.0f}/month)")
print(f"3. Maintain minimum ${min_equity_for_cap:,.0f} in account at all times")
print(f"4. In bad years, reduce withdrawals proportionally")
print()

print("EXPECTED OUTCOMES:")
print("-"*80)
print(f"Year 1-2: Build capital (no withdrawals)")
print(f"Year 3-10: Withdraw ${moderate_withdrawal:,.0f}/year on average")
print(f"Total 10-year withdrawals: ~${total_withdrawn:,.0f}")
print(f"Final account value: ~${equity_with_wd:,.0f}")
print(f"Total profit realized: ~${total_withdrawn + equity_with_wd - starting_capital:,.0f}")
print()

print("="*80)
print("OK Analysis complete")
print("="*80)
