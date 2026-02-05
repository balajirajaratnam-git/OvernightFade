"""
Strategy variants for overnight fade system.

Implements:
- BaselineStrategy: Original fade logic
- ExhaustionFilter: Skip when close is at extreme (momentum, not exhaustion)
- LastHourVeto: Skip when last hour trends same as day
- ATRRegimeFilter: Skip low volatility regimes
"""
import os
import pandas as pd
import numpy as np
from datetime import time
from dataclasses import dataclass
from typing import Optional, Tuple
from abc import ABC, abstractmethod

from session_utils import get_cash_session_window_utc, TZ_ET, TZ_UTC


@dataclass
class TradeSignal:
    """Represents a trade signal with optional filter results."""
    date: str
    direction: str  # "GREEN" or "RED"
    signal: str     # "FADE_GREEN", "FADE_RED", or "NO_TRADE"
    ref_price: float
    atr: float
    filter_reason: Optional[str] = None  # Why trade was filtered


class Strategy(ABC):
    """Base class for all strategies."""

    def __init__(self, config: dict, intraday_dir: str):
        self.config = config
        self.intraday_dir = intraday_dir
        self.FLAT_THRESHOLD_PCT = 0.10

    @abstractmethod
    def should_trade(self, day_data: pd.Series, date_str: str) -> TradeSignal:
        """Determine if we should trade based on day's data."""
        pass

    def _base_signal(self, day_data: pd.Series) -> str:
        """Get base signal from day direction."""
        if abs(day_data["Magnitude"]) < self.FLAT_THRESHOLD_PCT:
            return "NO_TRADE"

        if day_data["Direction"] == "GREEN" and self.config["filters"]["enable_fade_green"]:
            return "FADE_GREEN"
        elif day_data["Direction"] == "RED" and self.config["filters"]["enable_fade_red"]:
            return "FADE_RED"
        return "NO_TRADE"


class BaselineStrategy(Strategy):
    """Original overnight fade strategy without additional filters."""

    name = "Baseline"

    def should_trade(self, day_data: pd.Series, date_str: str) -> TradeSignal:
        signal = self._base_signal(day_data)
        return TradeSignal(
            date=date_str,
            direction=day_data["Direction"],
            signal=signal,
            ref_price=day_data["Close"],
            atr=day_data["ATR_14"],
        )


class ExhaustionFilter(Strategy):
    """
    Filter: Skip trades when close is at extreme of day's range.

    Rationale: If a green day closes near its high (or red day near its low),
    this suggests strong momentum rather than exhaustion. Fades work better
    when the move shows signs of exhaustion (close away from extreme).

    Parameter: extreme_threshold (0.0-1.0) - how close to extreme to filter
    """

    name = "Exhaustion"

    def __init__(self, config: dict, intraday_dir: str, extreme_threshold: float = 0.85):
        super().__init__(config, intraday_dir)
        self.extreme_threshold = extreme_threshold

    def should_trade(self, day_data: pd.Series, date_str: str) -> TradeSignal:
        signal = self._base_signal(day_data)

        if signal == "NO_TRADE":
            return TradeSignal(
                date=date_str,
                direction=day_data["Direction"],
                signal=signal,
                ref_price=day_data["Close"],
                atr=day_data["ATR_14"],
            )

        # Calculate where close is within day's range
        day_range = day_data["High"] - day_data["Low"]
        if day_range <= 0:
            return TradeSignal(
                date=date_str,
                direction=day_data["Direction"],
                signal=signal,
                ref_price=day_data["Close"],
                atr=day_data["ATR_14"],
            )

        close_position = (day_data["Close"] - day_data["Low"]) / day_range

        # For green days: filter if close near high (>threshold)
        # For red days: filter if close near low (<1-threshold)
        if day_data["Direction"] == "GREEN" and close_position > self.extreme_threshold:
            return TradeSignal(
                date=date_str,
                direction=day_data["Direction"],
                signal="NO_TRADE",
                ref_price=day_data["Close"],
                atr=day_data["ATR_14"],
                filter_reason=f"Close at {close_position:.0%} of range (>{self.extreme_threshold:.0%})"
            )
        elif day_data["Direction"] == "RED" and close_position < (1 - self.extreme_threshold):
            return TradeSignal(
                date=date_str,
                direction=day_data["Direction"],
                signal="NO_TRADE",
                ref_price=day_data["Close"],
                atr=day_data["ATR_14"],
                filter_reason=f"Close at {close_position:.0%} of range (<{1-self.extreme_threshold:.0%})"
            )

        return TradeSignal(
            date=date_str,
            direction=day_data["Direction"],
            signal=signal,
            ref_price=day_data["Close"],
            atr=day_data["ATR_14"],
        )


class LastHourVeto(Strategy):
    """
    Filter: Skip trades when last hour of cash session trends same as day.

    Rationale: If the last hour continues in the same direction as the day,
    momentum may carry into overnight. Fades work better when there's
    late-day reversal or consolidation.

    Parameter: veto_threshold (0.0-1.0) - what % of last hour move vetoes
    """

    name = "LastHourVeto"

    def __init__(self, config: dict, intraday_dir: str, veto_threshold: float = 0.3):
        super().__init__(config, intraday_dir)
        self.veto_threshold = veto_threshold

    def should_trade(self, day_data: pd.Series, date_str: str) -> TradeSignal:
        signal = self._base_signal(day_data)

        if signal == "NO_TRADE":
            return TradeSignal(
                date=date_str,
                direction=day_data["Direction"],
                signal=signal,
                ref_price=day_data["Close"],
                atr=day_data["ATR_14"],
            )

        # Load intraday data to check last hour
        intra_path = os.path.join(self.intraday_dir, f"{date_str}.parquet")
        if not os.path.exists(intra_path):
            # Can't check, allow trade
            return TradeSignal(
                date=date_str,
                direction=day_data["Direction"],
                signal=signal,
                ref_price=day_data["Close"],
                atr=day_data["ATR_14"],
            )

        df_intra = pd.read_parquet(intra_path)
        if df_intra.empty:
            return TradeSignal(
                date=date_str,
                direction=day_data["Direction"],
                signal=signal,
                ref_price=day_data["Close"],
                atr=day_data["ATR_14"],
            )

        # Get last hour (15:00-16:00 ET)
        if df_intra.index.tz is None:
            df_intra.index = df_intra.index.tz_localize('UTC')

        # Convert to ET for time filtering
        df_et = df_intra.copy()
        df_et.index = df_et.index.tz_convert(TZ_ET)

        last_hour = df_et.between_time('15:00', '16:00')
        if len(last_hour) < 5:
            return TradeSignal(
                date=date_str,
                direction=day_data["Direction"],
                signal=signal,
                ref_price=day_data["Close"],
                atr=day_data["ATR_14"],
            )

        last_hour_move = last_hour["Close"].iloc[-1] - last_hour["Open"].iloc[0]
        day_move = day_data["Close"] - day_data["Open"]

        # Check if last hour continues day's direction
        if day_move != 0:
            continuation_ratio = last_hour_move / day_move
            if continuation_ratio > self.veto_threshold:
                return TradeSignal(
                    date=date_str,
                    direction=day_data["Direction"],
                    signal="NO_TRADE",
                    ref_price=day_data["Close"],
                    atr=day_data["ATR_14"],
                    filter_reason=f"Last hour continuation {continuation_ratio:.0%} (>{self.veto_threshold:.0%})"
                )

        return TradeSignal(
            date=date_str,
            direction=day_data["Direction"],
            signal=signal,
            ref_price=day_data["Close"],
            atr=day_data["ATR_14"],
        )


class ATRRegimeFilter(Strategy):
    """
    Filter: Skip trades in low volatility regimes.

    Rationale: In low volatility periods, price movements are smaller and
    fade targets may not be reached. Better to trade when ATR indicates
    sufficient volatility for the strategy's profit targets.

    Parameter: atr_percentile (0-100) - skip if ATR below this percentile
    """

    name = "ATRRegime"

    def __init__(self, config: dict, intraday_dir: str, atr_percentile: float = 25, atr_history: pd.Series = None):
        super().__init__(config, intraday_dir)
        self.atr_percentile = atr_percentile
        self.atr_threshold = None

        if atr_history is not None and len(atr_history) > 0:
            self.atr_threshold = np.percentile(atr_history.dropna(), atr_percentile)

    def set_atr_threshold(self, atr_history: pd.Series):
        """Set ATR threshold from historical data."""
        if len(atr_history) > 0:
            self.atr_threshold = np.percentile(atr_history.dropna(), self.atr_percentile)

    def should_trade(self, day_data: pd.Series, date_str: str) -> TradeSignal:
        signal = self._base_signal(day_data)

        if signal == "NO_TRADE":
            return TradeSignal(
                date=date_str,
                direction=day_data["Direction"],
                signal=signal,
                ref_price=day_data["Close"],
                atr=day_data["ATR_14"],
            )

        # Check ATR regime
        current_atr = day_data["ATR_14"]
        if pd.isna(current_atr):
            return TradeSignal(
                date=date_str,
                direction=day_data["Direction"],
                signal=signal,
                ref_price=day_data["Close"],
                atr=current_atr,
            )

        if self.atr_threshold is not None and current_atr < self.atr_threshold:
            return TradeSignal(
                date=date_str,
                direction=day_data["Direction"],
                signal="NO_TRADE",
                ref_price=day_data["Close"],
                atr=current_atr,
                filter_reason=f"Low ATR {current_atr:.2f} < {self.atr_threshold:.2f} (p{self.atr_percentile})"
            )

        return TradeSignal(
            date=date_str,
            direction=day_data["Direction"],
            signal=signal,
            ref_price=day_data["Close"],
            atr=current_atr,
        )


class CombinedStrategy(Strategy):
    """
    Combines multiple filters. Trade only passes if all filters allow it.
    """

    def __init__(self, config: dict, intraday_dir: str, filters: list):
        super().__init__(config, intraday_dir)
        self.filters = filters
        self.name = "Combined_" + "_".join([f.name for f in filters])

    def should_trade(self, day_data: pd.Series, date_str: str) -> TradeSignal:
        # Check each filter in order
        for filt in self.filters:
            result = filt.should_trade(day_data, date_str)
            if result.signal == "NO_TRADE":
                return result  # First filter to reject wins

        # All filters passed, return base signal
        return self.filters[0].should_trade(day_data, date_str)


# Parameter grids for optimization (coarse)
PARAM_GRIDS = {
    "Exhaustion": {"extreme_threshold": [0.75, 0.80, 0.85, 0.90]},
    "LastHourVeto": {"veto_threshold": [0.20, 0.30, 0.40, 0.50]},
    "ATRRegime": {"atr_percentile": [15, 25, 35]},
}


def create_strategy(name: str, config: dict, intraday_dir: str, **kwargs) -> Strategy:
    """Factory function to create strategy instances."""
    if name == "Baseline":
        return BaselineStrategy(config, intraday_dir)
    elif name == "Exhaustion":
        return ExhaustionFilter(config, intraday_dir, **kwargs)
    elif name == "LastHourVeto":
        return LastHourVeto(config, intraday_dir, **kwargs)
    elif name == "ATRRegime":
        return ATRRegimeFilter(config, intraday_dir, **kwargs)
    else:
        raise ValueError(f"Unknown strategy: {name}")
