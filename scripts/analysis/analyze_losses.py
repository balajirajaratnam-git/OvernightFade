"""
Analyze the impact of closing at 14:00 UK on losing trades.
"""
import pandas as pd

df = pd.read_csv('backtest_uk_exit_results.csv')

print("=" * 80)
print("LOSS ANALYSIS: Impact of Closing at 14:00 UK Instead of Expiration")
print("=" * 80)

losses = df[df['Result'] == 'LOSS']
scratches = df[df['Result'] == 'SCRATCH']
wins = df[df['Result'] == 'WIN']

print(f"\nTotal Trades: {len(df)}")
print(f"  - Wins: {len(wins)} ({len(wins)/len(df)*100:.1f}%)")
print(f"  - Losses: {len(losses)} ({len(losses)/len(df)*100:.1f}%)")
print(f"  - Scratches: {len(scratches)} ({len(scratches)/len(df)*100:.1f}%)")

print("\n" + "=" * 80)
print("SCRATCH TRADES (Partial Recovery from Closing at 14:00 UK)")
print("=" * 80)
print(scratches[['Date', 'Signal', 'PnL_Mult', 'PnL_Dollar', 'MFE_Pct']].to_string(index=False))

if len(scratches) > 0:
    print(f"\nScratch Summary:")
    print(f"  Total scratches: {len(scratches)}")
    print(f"  Average scratch P/L: ${scratches['PnL_Dollar'].mean():.2f}")
    print(f"  Best scratch: ${scratches['PnL_Dollar'].max():.2f} (+{scratches['PnL_Mult'].max():.2f}R)")
    print(f"  Worst scratch: ${scratches['PnL_Dollar'].min():.2f} ({scratches['PnL_Mult'].min():.2f}R)")
    print(f"  Total recovered from scratches: ${scratches['PnL_Dollar'].sum():.2f}")

print("\n" + "=" * 80)
print("LOSS TRADES (Still Lost Most/All Premium at 14:00 UK)")
print("=" * 80)

# Show losses that recovered something (not full -1.05R loss)
partial_losses = losses[losses['PnL_Mult'] > -1.05]
full_losses = losses[losses['PnL_Mult'] <= -1.05]

print(f"\nPartial Losses (recovered some premium): {len(partial_losses)}")
if len(partial_losses) > 0:
    print(partial_losses[['Date', 'Signal', 'PnL_Mult', 'PnL_Dollar', 'MFE_Pct']].to_string(index=False))

print(f"\nFull Losses (no intrinsic value at 14:00 UK): {len(full_losses)}")
print(f"  Average loss: ${full_losses['PnL_Dollar'].mean():.2f}")

print("\n" + "=" * 80)
print("OVERALL IMPACT OF 14:00 UK EXIT STRATEGY")
print("=" * 80)

# Calculate what P/L would be if all losses were full -1.05R
hypothetical_full_losses = len(losses) + len(scratches)  # All non-wins
hypothetical_loss_total = hypothetical_full_losses * -105.0  # Full loss each

# Actual losses + scratches
actual_loss_total = losses['PnL_Dollar'].sum() + scratches['PnL_Dollar'].sum()

recovered_amount = actual_loss_total - hypothetical_loss_total

print(f"\nIf ALL non-wins lost full premium:")
print(f"  Non-win trades: {hypothetical_full_losses}")
print(f"  Total loss: ${hypothetical_loss_total:,.2f}")

print(f"\nWith 14:00 UK exit strategy:")
print(f"  Actual total from non-wins: ${actual_loss_total:,.2f}")
print(f"  Amount recovered: ${recovered_amount:,.2f}")
print(f"  Recovery rate: {(recovered_amount / abs(hypothetical_loss_total)) * 100:.1f}% of potential losses")

print(f"\nTotal P/L comparison:")
print(f"  Current strategy total P/L: ${df['PnL_Dollar'].sum():,.2f}")
print(f"  Hypothetical (all non-wins = full loss): ${(wins['PnL_Dollar'].sum() + hypothetical_loss_total):,.2f}")
print(f"  Benefit from 14:00 UK exit: ${recovered_amount:,.2f}")

print("\n" + "=" * 80)
