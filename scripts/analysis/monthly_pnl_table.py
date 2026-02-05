"""
Generate monthly P/L table for 1/10 Kelly strategy
"""
import pandas as pd
import numpy as np

# Load the detailed results from Option C analysis
df = pd.read_csv('results/phase3_option_c_detailed_results.csv')
df['Date'] = pd.to_datetime(df['Date'])

# Create Year-Month columns
df['Year'] = df['Date'].dt.year
df['Month'] = df['Date'].dt.month
df['YearMonth'] = df['Date'].dt.to_period('M')

# Calculate monthly P/L
monthly_pnl = df.groupby(['Year', 'Month'])['PnL_Actual'].sum().reset_index()
monthly_pnl.columns = ['Year', 'Month', 'PnL']

# Pivot to create table with months as rows, years as columns
pivot_table = monthly_pnl.pivot(index='Month', columns='Year', values='PnL')

# Month names
month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
               'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

# Replace month numbers with names
pivot_table.index = [month_names[i-1] for i in pivot_table.index]

# Calculate yearly totals
yearly_totals = monthly_pnl.groupby('Year')['PnL'].sum()

# Add totals row
pivot_table.loc['TOTAL'] = yearly_totals

# Print the table
print("="*140)
print("MONTHLY P/L BREAKDOWN - 1/10 KELLY (5.2%) STRATEGY")
print("All values in USD")
print("="*140)
print()

# Format and print
pd.set_option('display.float_format', lambda x: f'${x:,.0f}' if pd.notna(x) else '-')
pd.set_option('display.max_columns', None)
pd.set_option('display.width', None)

print(pivot_table.to_string())

print()
print("="*140)
print()

# Also create a detailed year-by-year view
print("DETAILED YEARLY BREAKDOWN")
print("="*140)
print()

for year in sorted(df['Year'].unique()):
    year_df = df[df['Year'] == year]
    year_total = year_df['PnL_Actual'].sum()

    print(f"YEAR {year}:")
    print("-" * 100)

    monthly_data = []
    for month in range(1, 13):
        month_df = year_df[year_df['Month'] == month]
        if len(month_df) > 0:
            month_pnl = month_df['PnL_Actual'].sum()
            trades = len(month_df)
            wins = (month_df['Result'] == 'WIN').sum()
            win_rate = wins / trades * 100
            monthly_data.append({
                'Month': month_names[month-1],
                'Trades': trades,
                'Wins': wins,
                'Win_Rate': f'{win_rate:.1f}%',
                'P/L': f'${month_pnl:,.0f}'
            })

    if monthly_data:
        month_df_display = pd.DataFrame(monthly_data)
        print(month_df_display.to_string(index=False))

    print()
    print(f"Year {year} Total: ${year_total:,.0f}")
    print()
    print()

print("="*140)

# Save to CSV for Excel viewing
pivot_table.to_csv('results/monthly_pnl_table_1_10_kelly.csv')
print()
print("Table saved to: results/monthly_pnl_table_1_10_kelly.csv")
