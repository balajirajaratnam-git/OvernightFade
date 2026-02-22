"""
Overnight Fade Strategy Backtester

This backtester compares two exit strategies:
1. Original: Hold positions until options expire (16:00 ET next trading day)
2. 09:35 ET Exit: Manually close non-winning positions at 09:35 ET

Key features:
- DST-safe timezone handling (ET, UK, UTC)
- Checks full trading day for wins (overnight + regular session)
- Intrinsic value calculation for early exits
- 5% slippage penalty per trade
"""

import os
import json
import pandas as pd
import numpy as np
from datetime import datetime, time, timedelta
import pytz

from session_utils import get_overnight_window_utc, TZ_ET, TZ_UTC

CONFIG_PATH = os.path.join("config", "config.json")
TZ_UK = pytz.timezone("Europe/London")


class Backtester:
    """
    Backtesting engine for overnight fade options strategy.

    The strategy:
    - Signals generated at 16:00 ET market close based on daily direction
    - Entry at 21:05 UK (approximately 16:05 ET)
    - Target profit: ATR-based multiple
    - Exit: Either target hit or option expiry (or manual early exit)
    """

    def __init__(self):
        self._load_config()
        self.ticker = self.config["ticker"]
        self.data_dir = os.path.join(self.config["directories"]["data"], self.ticker)
        self.daily_path = os.path.join(self.data_dir, "daily_OHLCV.parquet")
        self.intraday_dir = os.path.join(self.data_dir, "intraday")

        self.FLAT_THRESHOLD_PCT = 0.10  # Skip days with < 0.10% magnitude
        self.SLIPPAGE_PENALTY = 0.05    # Slippage cost per trade (5% of risk)

        self.tz_et = TZ_ET
        self.tz_utc = TZ_UTC

        if os.path.exists(self.daily_path):
            self.daily_df = pd.read_parquet(self.daily_path)
        else:
            self.daily_df = pd.DataFrame()

    def _load_config(self):
        """Load trading configuration from config.json"""
        with open(CONFIG_PATH, "r") as f:
            self.config = json.load(f)

    def _get_overnight_window(self, date_obj):
        """
        Get overnight session window (16:00 ET to 09:30 ET next day).
        DST-safe timezone conversion.
        """
        return get_overnight_window_utc(date_obj)

    def _get_cash_session_window(self, date_obj):
        """
        Get cash trading session window (09:30 ET to 16:00 ET) for given date.

        Args:
            date_obj: datetime or date object

        Returns:
            Tuple of (start_utc, end_utc) as timezone-aware UTC datetimes
        """
        if hasattr(date_obj, 'date'):
            date_obj = date_obj.date()

        # Create naive datetimes in ET
        naive_start = datetime.combine(date_obj, time(9, 30))  # Market open
        naive_end = datetime.combine(date_obj, time(16, 0))   # Market close

        # Localize to ET (handles DST automatically)
        loc_start = self.tz_et.localize(naive_start)
        loc_end = self.tz_et.localize(naive_end)

        # Convert to UTC
        return loc_start.astimezone(self.tz_utc), loc_end.astimezone(self.tz_utc)

    def _get_next_trading_day_file(self, current_date):
        """
        Get the next trading day's intraday data file path.

        Args:
            current_date: Current trading date

        Returns:
            Path to next trading day's file, or None if not found
        """
        # Start with next calendar day
        next_date = current_date + timedelta(days=1)

        # Try up to 5 days forward (to skip weekends/holidays)
        for _ in range(5):
            # Skip weekends
            if next_date.weekday() < 5:  # Monday=0, Friday=4
                file_path = os.path.join(self.intraday_dir, f"{next_date.strftime('%Y-%m-%d')}.parquet")
                if os.path.exists(file_path):
                    return file_path, next_date
            next_date += timedelta(days=1)

        return None, None

    def _calculate_pnl_multiple(self, result_type, fade_pct, target_pct):
        """
        Calculate P/L multiple based on result type and progress.

        Args:
            result_type: "WIN", "LOSS", or "SCRATCH"
            fade_pct: Actual fade amount achieved
            target_pct: Target fade amount

        Returns:
            P/L multiple (-1.0 to +0.5)
        """
        if result_type == "LOSS":
            return -1.0
        if result_type == "WIN":
            return 0.5
        if result_type == "SCRATCH":
            # Linear scaling from -1.0 to -0.1 based on progress toward target
            progress = fade_pct / target_pct
            progress = max(0.0, min(1.0, progress))
            return -1.0 + (0.9 * progress)
        return -1.0

    def _calculate_pnl_with_intrinsic(self, signal, strike_price, exit_price, target_dist):
        """
        Calculate P/L when closing position early based on intrinsic value.

        For PUT (FADE_GREEN): intrinsic = max(0, strike - underlying)
        For CALL (FADE_RED): intrinsic = max(0, underlying - strike)

        Args:
            signal: "FADE_GREEN" or "FADE_RED"
            strike_price: Option strike (approximately entry close price)
            exit_price: SPY price at early exit time
            target_dist: Target distance in points (ATR * multiplier)

        Returns:
            P/L multiple capped between -1.0 and +0.5
        """
        # Calculate intrinsic value
        if signal == "FADE_GREEN":  # PUT option
            intrinsic = max(0, strike_price - exit_price)
        else:  # FADE_RED - CALL option
            intrinsic = max(0, exit_price - strike_price)

        # Estimate premium paid (40% of target distance for ATM options)
        estimated_premium = 0.4 * target_dist

        if estimated_premium <= 0:
            return -1.0

        # Calculate P/L: (intrinsic_value / premium_paid) - 1
        pnl_mult = (intrinsic / estimated_premium) - 1.0

        # Cap at WIN level (+0.5) and minimum loss (-1.0)
        pnl_mult = max(-1.0, min(0.5, pnl_mult))

        return pnl_mult

    def run(self, take_profit_atr_mult=None, use_0935_exit=False):
        """
        Run backtest with specified exit strategy.

        Args:
            take_profit_atr_mult: ATR multiple for take profit target
            use_0935_exit: If True, manually close at 09:35 ET if target not hit
                          If False, hold until options expire at 16:00 ET next day
        """
        if self.daily_df.empty:
            return pd.DataFrame()
        if take_profit_atr_mult is None:
            take_profit_atr_mult = self.config["default_take_profit_atr"]

        trades = []
        valid_days = self.daily_df[self.daily_df.index.dayofweek < 4]
        missing_data_count = 0

        for i in range(len(valid_days) - 1):
            day_t = valid_days.iloc[i]
            date_t = valid_days.index[i]

            # Skip flat days
            if abs(day_t["Magnitude"]) < self.FLAT_THRESHOLD_PCT:
                continue

            # Determine signal
            signal = "NO_TRADE"
            if day_t["Direction"] == "GREEN" and self.config["filters"]["enable_fade_green"]:
                signal = "FADE_GREEN"
            elif day_t["Direction"] == "RED" and self.config["filters"]["enable_fade_red"]:
                signal = "FADE_RED"

            if signal == "NO_TRADE":
                continue

            # Load signal day's intraday data
            day_str = date_t.strftime("%Y-%m-%d")
            intra_path = os.path.join(self.intraday_dir, f"{day_str}.parquet")

            if not os.path.exists(intra_path):
                missing_data_count += 1
                continue

            df_intra = pd.read_parquet(intra_path)
            if df_intra.empty:
                continue

            date_obj = date_t.to_pydatetime()

            # Always check overnight window first (16:00 ET Day T to 09:30 ET Day T+1)
            start_utc, overnight_end_utc = self._get_overnight_window(date_obj)

            if df_intra.index.tz is None:
                df_intra.index = df_intra.index.tz_localize('UTC')
            window = df_intra[(df_intra.index > start_utc) & (df_intra.index < overnight_end_utc)]

            if window.empty:
                continue

            ref_price = day_t["Close"]
            atr = day_t["ATR_14"]
            if pd.isna(atr):
                continue

            target_dist = atr * take_profit_atr_mult

            outcome = ""
            gross_pnl = 0.0
            mfe = 0.0
            win_time_str = "N/A"

            # Process based on signal direction
            if signal == "FADE_GREEN":
                target_price = ref_price - target_dist
                lowest_low = window["Low"].min()
                mfe = max(0.0, (ref_price - lowest_low) / ref_price * 100)

                # Check if target hit in overnight window
                hits = window[window["Low"] <= target_price]

                if not hits.empty:
                    outcome = "WIN"
                    gross_pnl = self._calculate_pnl_multiple("WIN", 1.0, 1.0)
                    first_hit = hits.index[0]
                    win_time_str = first_hit.strftime("%H:%M UTC")
                else:
                    # Target not hit in overnight window
                    if use_0935_exit:
                        # STRATEGY 2: Close at 09:35 ET with intrinsic value
                        exit_et_naive = datetime.combine(date_obj + timedelta(days=1), time(9, 35))
                        exit_time_utc = self.tz_et.localize(exit_et_naive).astimezone(self.tz_utc)

                        # Find price at 09:35 ET
                        if exit_time_utc in window.index:
                            exit_price = window.loc[exit_time_utc, "Close"]
                        else:
                            before_exit = window[window.index <= exit_time_utc]
                            if not before_exit.empty:
                                exit_price = before_exit["Close"].iloc[-1]
                            else:
                                exit_price = window["Close"].iloc[-1]

                        strike_price = ref_price
                        gross_pnl = self._calculate_pnl_with_intrinsic(
                            signal, strike_price, exit_price, target_dist
                        )
                        outcome = "LOSS" if gross_pnl <= -0.5 else "SCRATCH"
                    else:
                        # STRATEGY 1: Check cash session on next trading day
                        next_file_path, next_date = self._get_next_trading_day_file(date_obj.date())

                        if next_file_path and next_date:
                            df_next = pd.read_parquet(next_file_path)
                            if not df_next.empty:
                                if df_next.index.tz is None:
                                    df_next.index = df_next.index.tz_localize('UTC')

                                # Get cash session window (09:30 ET to 16:00 ET next trading day)
                                cash_start_utc, cash_end_utc = self._get_cash_session_window(next_date)
                                cash_window = df_next[(df_next.index >= cash_start_utc) & (df_next.index <= cash_end_utc)]

                                if not cash_window.empty:
                                    # Check if target hit during cash session
                                    cash_hits = cash_window[cash_window["Low"] <= target_price]

                                    if not cash_hits.empty:
                                        outcome = "WIN"
                                        gross_pnl = self._calculate_pnl_multiple("WIN", 1.0, 1.0)
                                        first_hit = cash_hits.index[0]
                                        win_time_str = first_hit.strftime("%H:%M UTC")
                                    else:
                                        # Not hit - option expires worthless
                                        outcome = "LOSS"
                                        gross_pnl = -1.0
                                else:
                                    # No cash session data - assume loss
                                    outcome = "LOSS"
                                    gross_pnl = -1.0
                            else:
                                outcome = "LOSS"
                                gross_pnl = -1.0
                        else:
                            # No next day file - assume loss
                            outcome = "LOSS"
                            gross_pnl = -1.0

            elif signal == "FADE_RED":
                target_price = ref_price + target_dist
                highest_high = window["High"].max()
                mfe = max(0.0, (highest_high - ref_price) / ref_price * 100)

                # Check if target hit in overnight window
                hits = window[window["High"] >= target_price]

                if not hits.empty:
                    outcome = "WIN"
                    gross_pnl = self._calculate_pnl_multiple("WIN", 1.0, 1.0)
                    first_hit = hits.index[0]
                    win_time_str = first_hit.strftime("%H:%M UTC")
                else:
                    # Target not hit in overnight window
                    if use_0935_exit:
                        # STRATEGY 2: Close at 09:35 ET with intrinsic value
                        exit_et_naive = datetime.combine(date_obj + timedelta(days=1), time(9, 35))
                        exit_time_utc = self.tz_et.localize(exit_et_naive).astimezone(self.tz_utc)

                        # Find price at 09:35 ET
                        if exit_time_utc in window.index:
                            exit_price = window.loc[exit_time_utc, "Close"]
                        else:
                            before_exit = window[window.index <= exit_time_utc]
                            if not before_exit.empty:
                                exit_price = before_exit["Close"].iloc[-1]
                            else:
                                exit_price = window["Close"].iloc[-1]

                        strike_price = ref_price
                        gross_pnl = self._calculate_pnl_with_intrinsic(
                            signal, strike_price, exit_price, target_dist
                        )
                        outcome = "LOSS" if gross_pnl <= -0.5 else "SCRATCH"
                    else:
                        # STRATEGY 1: Check cash session on next trading day
                        next_file_path, next_date = self._get_next_trading_day_file(date_obj.date())

                        if next_file_path and next_date:
                            df_next = pd.read_parquet(next_file_path)
                            if not df_next.empty:
                                if df_next.index.tz is None:
                                    df_next.index = df_next.index.tz_localize('UTC')

                                # Get cash session window (09:30 ET to 16:00 ET next trading day)
                                cash_start_utc, cash_end_utc = self._get_cash_session_window(next_date)
                                cash_window = df_next[(df_next.index >= cash_start_utc) & (df_next.index <= cash_end_utc)]

                                if not cash_window.empty:
                                    # Check if target hit during cash session
                                    cash_hits = cash_window[cash_window["High"] >= target_price]

                                    if not cash_hits.empty:
                                        outcome = "WIN"
                                        gross_pnl = self._calculate_pnl_multiple("WIN", 1.0, 1.0)
                                        first_hit = cash_hits.index[0]
                                        win_time_str = first_hit.strftime("%H:%M UTC")
                                    else:
                                        # Not hit - option expires worthless
                                        outcome = "LOSS"
                                        gross_pnl = -1.0
                                else:
                                    # No cash session data - assume loss
                                    outcome = "LOSS"
                                    gross_pnl = -1.0
                            else:
                                outcome = "LOSS"
                                gross_pnl = -1.0
                        else:
                            # No next day file - assume loss
                            outcome = "LOSS"
                            gross_pnl = -1.0

            # Apply slippage
            net_pnl = gross_pnl - self.SLIPPAGE_PENALTY

            trades.append({
                "Date": day_str,
                "Signal": signal,
                "Result": outcome,
                "Win_Time": win_time_str,
                "PnL_Mult": round(net_pnl, 2),
                "PnL_Dollar": round(net_pnl * self.config["premium_budget"], 2),
                "MFE_Pct": round(mfe, 2)
            })

        if missing_data_count > 0:
            print(f"[Warning] Skipped {missing_data_count} days due to missing intraday data.")

        return pd.DataFrame(trades)


def main():
    """Main execution: Run both strategies and compare results"""
    bt = Backtester()

    print("="*80)
    print("OVERNIGHT FADE STRATEGY BACKTEST")
    print("="*80)
    print("\nComparing two exit strategies:")
    print("  1. Original: Hold until options expire (16:00 ET next trading day)")
    print("  2. 09:35 ET Exit: Manually close non-winners at 09:35 ET (14:35 UK)")
    print()

    # ========================================
    # Run Strategy 1: Original (hold until expiry)
    # ========================================
    print("Running Strategy 1 (Original)...")
    results_original = bt.run(use_0935_exit=False)

    # ========================================
    # Run Strategy 2: 09:35 ET exit
    # ========================================
    print("Running Strategy 2 (09:35 ET Exit)...")
    results_0935 = bt.run(use_0935_exit=True)

    if not results_original.empty and not results_0935.empty:
        # Ensure results directory exists
        results_dir = "results"
        if not os.path.exists(results_dir):
            os.makedirs(results_dir)

        # ========================================
        # Save Results
        # ========================================
        print("\n" + "="*80)
        print("SAVING RESULTS")
        print("="*80)

        # Save original
        try:
            orig_path = os.path.join(results_dir, "trade_log_ORIGINAL.csv")
            results_original.to_csv(orig_path, index=False)
            print(f"Strategy 1 saved: {orig_path}")
        except Exception as e:
            print(f"Warning: Could not save original results: {e}")

        # Save 09:35 ET
        try:
            et_path = os.path.join(results_dir, "trade_log_0935ET.csv")
            results_0935.to_csv(et_path, index=False)
            print(f"Strategy 2 saved: {et_path}")
        except Exception as e:
            print(f"Warning: Could not save 09:35 ET results: {e}")

        # ========================================
        # Display Strategy 1 Results
        # ========================================
        print("\n" + "="*80)
        print("STRATEGY 1: ORIGINAL (Hold until options expire)")
        print("="*80)
        print("\n--- SAMPLE TRADES (Last 10) ---")
        print(results_original[["Date", "Signal", "Result", "Win_Time", "PnL_Dollar"]].tail(10).to_string(index=False))

        print("\n--- SUMMARY ---")
        total_pnl_orig = results_original["PnL_Dollar"].sum()
        win_rate_orig = len(results_original[results_original["Result"]=="WIN"]) / len(results_original) * 100
        wins_orig = len(results_original[results_original["Result"]=="WIN"])
        losses_orig = len(results_original[results_original["Result"]=="LOSS"])
        scratches_orig = len(results_original[results_original["Result"]=="SCRATCH"])

        print(f"Total Trades: {len(results_original)}")
        print(f"Wins:         {wins_orig} ({win_rate_orig:.1f}%)")
        print(f"Losses:       {losses_orig}")
        print(f"Scratches:    {scratches_orig}")
        print(f"Total P/L:    ${total_pnl_orig:,.2f}")
        print(f"Avg Trade:    ${total_pnl_orig/len(results_original):.2f}")

        # ========================================
        # Display Strategy 2 Results
        # ========================================
        print("\n" + "="*80)
        print("STRATEGY 2: 09:35 ET EXIT (Close at 09:35 ET / 14:35 UK)")
        print("="*80)
        print("\n--- SAMPLE TRADES (Last 10) ---")
        print(results_0935[["Date", "Signal", "Result", "Win_Time", "PnL_Dollar"]].tail(10).to_string(index=False))

        print("\n--- SUMMARY ---")
        total_pnl_0935 = results_0935["PnL_Dollar"].sum()
        win_rate_0935 = len(results_0935[results_0935["Result"]=="WIN"]) / len(results_0935) * 100
        wins_0935 = len(results_0935[results_0935["Result"]=="WIN"])
        losses_0935 = len(results_0935[results_0935["Result"]=="LOSS"])
        scratches_0935 = len(results_0935[results_0935["Result"]=="SCRATCH"])

        print(f"Total Trades: {len(results_0935)}")
        print(f"Wins:         {wins_0935} ({win_rate_0935:.1f}%)")
        print(f"Losses:       {losses_0935}")
        print(f"Scratches:    {scratches_0935}")
        print(f"Total P/L:    ${total_pnl_0935:,.2f}")
        print(f"Avg Trade:    ${total_pnl_0935/len(results_0935):.2f}")

        # ========================================
        # Comparison
        # ========================================
        print("\n" + "="*80)
        print("STRATEGY COMPARISON")
        print("="*80)
        diff_pnl = total_pnl_0935 - total_pnl_orig
        diff_wins = wins_0935 - wins_orig
        diff_wr = win_rate_0935 - win_rate_orig

        print(f"P/L Difference:       ${diff_pnl:+,.2f} ({'Better' if diff_pnl > 0 else 'Worse'} with 09:35 ET exit)")
        print(f"Win Difference:       {diff_wins:+d} wins")
        print(f"Win Rate Difference:  {diff_wr:+.1f}%")
        print(f"\nRecommendation: {'Use 09:35 ET Exit Strategy' if diff_pnl > 0 else 'Use Original Strategy'}")

    else:
        print("No trades generated.")


if __name__ == "__main__":
    main()
