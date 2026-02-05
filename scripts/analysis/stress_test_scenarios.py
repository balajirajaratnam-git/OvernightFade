"""
Stress test: What happens during severe losing streaks?
"""
import pandas as pd

# Parameters
kelly_pct = 0.1309  # 13.09% from earlier calculation
max_position = 1000

def simulate_day(equity, position_strategy, num_wins, num_losses):
    """Simulate one trading day with specified wins/losses."""
    day_trades = []

    for i in range(num_wins):
        if position_strategy == 'fixed':
            position = 100
        else:  # Kelly + Equity
            position = min(equity * kelly_pct, max_position)

        pnl = position * 0.45  # Win
        equity += pnl
        day_trades.append(('WIN', position, pnl, equity))

    for i in range(num_losses):
        if position_strategy == 'fixed':
            position = 100
        else:  # Kelly + Equity
            position = min(equity * kelly_pct, max_position)

        pnl = position * -1.05  # Loss
        equity += pnl
        day_trades.append(('LOSS', position, pnl, equity))

    return equity, day_trades

print("="*80)
print("WORST-CASE SCENARIO STRESS TESTS")
print("="*80)
print()

# Scenario 1: 3 bad days (4 losses) + 2 good days (4 wins)
print("SCENARIO 1: 3 Bad Days (all 4 lose) + 2 Good Days (all 4 win)")
print("="*80)
print()

# Fixed $100
print("Fixed $100 Position Sizing:")
print("-"*80)
equity_fixed = 10000
print(f"Starting: ${equity_fixed:,.2f}")
print()

for day in range(1, 6):
    if day <= 3:  # Bad days
        equity_fixed, trades = simulate_day(equity_fixed, 'fixed', 0, 4)
        print(f"Day {day} (4 losses): ${equity_fixed:,.2f}  (Day P/L: -$420)")
    else:  # Good days
        equity_fixed, trades = simulate_day(equity_fixed, 'fixed', 4, 0)
        print(f"Day {day} (4 wins):   ${equity_fixed:,.2f}  (Day P/L: +$180)")

week_pnl_fixed = equity_fixed - 10000
print(f"\nWeek ending equity: ${equity_fixed:,.2f}")
print(f"Week P/L: ${week_pnl_fixed:+,.2f} ({week_pnl_fixed/10000*100:+.1f}%)")
print()

# Kelly + Equity
print("Kelly + Equity-Based:")
print("-"*80)
equity_kelly = 10000
print(f"Starting: ${equity_kelly:,.2f}")
print()

for day in range(1, 6):
    if day <= 3:  # Bad days
        day_start = equity_kelly
        equity_kelly, trades = simulate_day(equity_kelly, 'kelly', 0, 4)
        day_pnl = equity_kelly - day_start
        position_sizes = [t[1] for t in trades]
        print(f"Day {day} (4 losses): ${equity_kelly:,.2f}  (Day P/L: ${day_pnl:,.2f}, Positions: ${position_sizes[0]:.0f})")
    else:  # Good days
        day_start = equity_kelly
        equity_kelly, trades = simulate_day(equity_kelly, 'kelly', 4, 0)
        day_pnl = equity_kelly - day_start
        position_sizes = [t[1] for t in trades]
        print(f"Day {day} (4 wins):   ${equity_kelly:,.2f}  (Day P/L: ${day_pnl:+,.2f}, Positions: ${position_sizes[0]:.0f})")

week_pnl_kelly = equity_kelly - 10000
print(f"\nWeek ending equity: ${equity_kelly:,.2f}")
print(f"Week P/L: ${week_pnl_kelly:+,.2f} ({week_pnl_kelly/10000*100:+.1f}%)")
print()
print(f"Impact difference: Kelly loses ${abs(week_pnl_kelly - week_pnl_fixed):,.2f} MORE than Fixed")
print()
print()

# Scenario 2: 4 bad days + 1 good day
print("="*80)
print("SCENARIO 2: 4 Bad Days (all 4 lose) + 1 Good Day (all 4 win)")
print("="*80)
print()

# Fixed $100
print("Fixed $100 Position Sizing:")
print("-"*80)
equity_fixed = 10000
print(f"Starting: ${equity_fixed:,.2f}")
print()

for day in range(1, 6):
    if day <= 4:  # Bad days
        equity_fixed, trades = simulate_day(equity_fixed, 'fixed', 0, 4)
        print(f"Day {day} (4 losses): ${equity_fixed:,.2f}  (Day P/L: -$420)")
    else:  # Good day
        equity_fixed, trades = simulate_day(equity_fixed, 'fixed', 4, 0)
        print(f"Day {day} (4 wins):   ${equity_fixed:,.2f}  (Day P/L: +$180)")

week_pnl_fixed = equity_fixed - 10000
print(f"\nWeek ending equity: ${equity_fixed:,.2f}")
print(f"Week P/L: ${week_pnl_fixed:+,.2f} ({week_pnl_fixed/10000*100:+.1f}%)")
print()

# Kelly + Equity
print("Kelly + Equity-Based:")
print("-"*80)
equity_kelly = 10000
print(f"Starting: ${equity_kelly:,.2f}")
print()

for day in range(1, 6):
    if day <= 4:  # Bad days
        day_start = equity_kelly
        equity_kelly, trades = simulate_day(equity_kelly, 'kelly', 0, 4)
        day_pnl = equity_kelly - day_start
        position_sizes = [t[1] for t in trades]
        print(f"Day {day} (4 losses): ${equity_kelly:,.2f}  (Day P/L: ${day_pnl:,.2f}, Positions: ${position_sizes[0]:.0f})")
    else:  # Good day
        day_start = equity_kelly
        equity_kelly, trades = simulate_day(equity_kelly, 'kelly', 4, 0)
        day_pnl = equity_kelly - day_start
        position_sizes = [t[1] for t in trades]
        print(f"Day {day} (4 wins):   ${equity_kelly:,.2f}  (Day P/L: ${day_pnl:+,.2f}, Positions: ${position_sizes[0]:.0f})")

week_pnl_kelly = equity_kelly - 10000
print(f"\nWeek ending equity: ${equity_kelly:,.2f}")
print(f"Week P/L: ${week_pnl_kelly:+,.2f} ({week_pnl_kelly/10000*100:+.1f}%)")
print()

if equity_kelly < 0:
    print(f"*** ACCOUNT BLOWN! Negative equity ***")
else:
    print(f"Impact difference: Kelly loses ${abs(week_pnl_kelly - week_pnl_fixed):,.2f} MORE than Fixed")
print()
print()

# Scenario 3: TWO WEEKS of Scenario 2
print("="*80)
print("SCENARIO 3: TWO WEEKS of Scenario 2 (4 bad + 1 good, repeated)")
print("="*80)
print()

# Fixed $100
print("Fixed $100 Position Sizing:")
print("-"*80)
equity_fixed = 10000
print(f"Starting: ${equity_fixed:,.2f}")
print()

for week in range(1, 3):
    print(f"Week {week}:")
    for day in range(1, 6):
        if day <= 4:  # Bad days
            equity_fixed, trades = simulate_day(equity_fixed, 'fixed', 0, 4)
            print(f"  Day {day} (4 losses): ${equity_fixed:,.2f}")
        else:  # Good day
            equity_fixed, trades = simulate_day(equity_fixed, 'fixed', 4, 0)
            print(f"  Day {day} (4 wins):   ${equity_fixed:,.2f}")
    print()

total_pnl_fixed = equity_fixed - 10000
print(f"2-Week ending equity: ${equity_fixed:,.2f}")
print(f"2-Week P/L: ${total_pnl_fixed:+,.2f} ({total_pnl_fixed/10000*100:+.1f}%)")
print()

# Kelly + Equity
print("Kelly + Equity-Based:")
print("-"*80)
equity_kelly = 10000
print(f"Starting: ${equity_kelly:,.2f}")
print()

blown = False
for week in range(1, 3):
    print(f"Week {week}:")
    for day in range(1, 6):
        if equity_kelly <= 0:
            print(f"  *** ACCOUNT BLOWN - Cannot continue ***")
            blown = True
            break

        if day <= 4:  # Bad days
            day_start = equity_kelly
            equity_kelly, trades = simulate_day(equity_kelly, 'kelly', 0, 4)
            day_pnl = equity_kelly - day_start
            position_sizes = [t[1] for t in trades]
            print(f"  Day {day} (4 losses): ${equity_kelly:,.2f}  (Positions: ${position_sizes[0]:.0f})")
        else:  # Good day
            day_start = equity_kelly
            equity_kelly, trades = simulate_day(equity_kelly, 'kelly', 4, 0)
            day_pnl = equity_kelly - day_start
            position_sizes = [t[1] for t in trades]
            print(f"  Day {day} (4 wins):   ${equity_kelly:,.2f}  (Positions: ${position_sizes[0]:.0f})")

    if blown:
        break
    print()

if equity_kelly > 0:
    total_pnl_kelly = equity_kelly - 10000
    print(f"2-Week ending equity: ${equity_kelly:,.2f}")
    print(f"2-Week P/L: ${total_pnl_kelly:+,.2f} ({total_pnl_kelly/10000*100:+.1f}%)")
else:
    print(f"\n*** ACCOUNT BLOWN! Final equity: ${equity_kelly:,.2f} ***")

print()
print()
print("="*80)
print("KEY INSIGHTS")
print("="*80)
print()
print("1. Fixed $100 is MUCH more resilient during losing streaks")
print("2. Kelly + Equity has 10x larger losses during bad periods")
print("3. Position sizes DECREASE after losses (protective mechanism)")
print("4. But initial losses can be devastating with Kelly sizing")
print("5. Need substantial capital buffer ($20k+) for Kelly strategy")
print()
