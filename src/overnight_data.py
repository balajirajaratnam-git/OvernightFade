"""
Overnight underlying data loader — stitches IG gap data with SPY bars.

SPY has no bars roughly 20:00–04:00 ET. IG US500 5m candles fill that gap.
This module provides functions to:
  1. Load IG gap parquet for a UK date
  2. Merge IG gap bars into an overnight bar series alongside SPY bars
  3. Look up a price at any overnight timestamp using the stitched series
"""

import os
from datetime import datetime, timedelta, date as date_type
from pathlib import Path

import pandas as pd
import pytz

TZ_UK = pytz.timezone("Europe/London")
TZ_ET = pytz.timezone("US/Eastern")
TZ_UTC = pytz.utc

DEFAULT_GAP_DIR = Path("data/IG_US500_spy_gap_5m")


def compute_ig_spy_scale_factor(
    entry_dt_et: datetime,
    spy_close: float,
    gap_dir: Path = DEFAULT_GAP_DIR,
) -> float:
    """
    Compute scale factor to convert IG US500 prices to SPY-equivalent.

    IG US500 is the S&P 500 index level (~5500), SPY is ~1/10th (~550).
    The ratio varies slightly day-to-day, so we compute it fresh per trade
    using the known SPY close and the nearest IG bar around entry time.

    Returns:
        scale_factor (float): multiply IG price by this to get SPY-equivalent.
        Returns 0.1 as fallback if no IG bar found near entry.
    """
    # Load IG bar near entry time (16:00 ET = SPY close)
    df = load_ig_gap_5m_for_et_datetime(entry_dt_et, gap_dir)
    if df.empty:
        return spy_close / (spy_close * 10.0)  # fallback: ~0.1

    # Find closest bar within ±30 minutes of entry
    window_start = entry_dt_et - timedelta(minutes=30)
    window_end = entry_dt_et + timedelta(minutes=30)
    bars = df[(df.index >= window_start) & (df.index <= window_end)]

    if bars.empty:
        # Try broader window: any bar from same session
        window_start = entry_dt_et - timedelta(hours=2)
        window_end = entry_dt_et + timedelta(hours=2)
        bars = df[(df.index >= window_start) & (df.index <= window_end)]

    if bars.empty:
        return spy_close / (spy_close * 10.0)  # fallback: ~0.1

    # Use the bar closest to entry time
    abs_delta = abs(bars.index - entry_dt_et)
    closest_bar = bars.iloc[abs_delta.argmin()]
    ig_close = float(closest_bar["Close"])

    if ig_close <= 0:
        return 0.1  # safety

    return spy_close / ig_close


def load_ig_gap_5m_for_uk_date(
    uk_date: date_type,
    gap_dir: Path = DEFAULT_GAP_DIR,
) -> pd.DataFrame:
    """
    Load IG gap 5m parquet file for a given UK date.

    Returns:
        DataFrame with UTC-indexed OHLCV + source column, or empty DataFrame.
    """
    path = gap_dir / f"{uk_date.isoformat()}.parquet"
    if not path.exists():
        return pd.DataFrame()

    try:
        df = pd.read_parquet(path)
    except Exception:
        return pd.DataFrame()

    if df.empty:
        return df

    # Ensure UTC-aware index
    if df.index.tz is None:
        df.index = pd.to_datetime(df.index).tz_localize(TZ_UTC)

    # Ensure 'source' column exists
    if "source" not in df.columns:
        df["source"] = "IG_US500_5m"

    return df


def load_ig_gap_5m_for_et_datetime(
    target_dt_et: datetime,
    gap_dir: Path = DEFAULT_GAP_DIR,
) -> pd.DataFrame:
    """
    Load IG gap data for a target ET datetime.

    The overnight window for an ET datetime may span two UK dates
    (e.g. ET 22:00 on Jan 20 = UK Jan 21 03:00 in winter).
    We determine the correct UK date from the target ET datetime.

    Returns:
        DataFrame with ET-indexed 5m bars, or empty DataFrame.
    """
    # Convert target to UK to find the right UK date file
    target_utc = target_dt_et.astimezone(TZ_UTC)
    target_uk = target_utc.astimezone(TZ_UK)
    uk_date = target_uk.date()

    df = load_ig_gap_5m_for_uk_date(uk_date, gap_dir)
    if df.empty:
        return df

    # Also try the previous UK date (in case the window straddles midnight UK)
    prev_uk_date = uk_date - timedelta(days=1)
    df_prev = load_ig_gap_5m_for_uk_date(prev_uk_date, gap_dir)
    if not df_prev.empty:
        df = pd.concat([df_prev, df])
        df = df[~df.index.duplicated(keep="first")]
        df = df.sort_index()

    # Convert to ET for consistency with SPY bars
    df.index = df.index.tz_convert(TZ_ET)
    df.index.name = "DateTime_ET"

    return df


def get_ig_gap_price_at(
    target_dt_et: datetime,
    gap_dir: Path = DEFAULT_GAP_DIR,
    lookback_minutes: int = 10,
    scale_factor: float = 1.0,
) -> tuple:
    """
    Get the IG gap underlying price at a specific ET datetime.

    Uses forward-fill logic: finds the most recent 5m bar at or before
    the target time (within lookback_minutes).

    Args:
        scale_factor: Multiply IG price by this to convert US500 → SPY level.
                      Computed as SPY_close / IG_US500_close at entry time.
                      Default 1.0 = no scaling (for testing or if already aligned).

    Returns:
        (price, source_str) or (None, None) if no bar available.
    """
    df = load_ig_gap_5m_for_et_datetime(target_dt_et, gap_dir)
    if df.empty:
        return None, None

    # Find bars within lookback window
    window_start = target_dt_et - timedelta(minutes=lookback_minutes)
    bars = df[(df.index >= window_start) & (df.index <= target_dt_et)]

    if bars.empty:
        return None, None

    # Use the most recent bar (ffill from 5m to exact time)
    last_bar = bars.iloc[-1]
    price = float(last_bar["Close"]) * scale_factor
    return price, "IG_GAP_5M"


def stitch_overnight_bars(
    spy_bars: pd.DataFrame,
    entry_dt_et: datetime,
    max_exit_dt_et: datetime,
    gap_dir: Path = DEFAULT_GAP_DIR,
    resample_mode: str = "ffill",
    scale_factor: float = 1.0,
) -> tuple:
    """
    Stitch SPY bars with IG gap data for a complete overnight series.

    Used by tp_anytime scanning: creates a combined 1-minute series where
    SPY provides bars for times it has data, and IG gap fills the rest.

    Args:
        spy_bars: DataFrame with ET-indexed SPY intraday bars (may have gaps)
        entry_dt_et: Trade entry time in ET
        max_exit_dt_et: Latest exit time in ET
        gap_dir: Directory with IG gap parquet files
        resample_mode: 'ffill' to forward-fill 5m→1m, 'none' to keep 5m intervals
        scale_factor: Multiply IG prices by this to convert US500 → SPY level.

    Returns:
        (combined_df, ig_bar_count) where:
          combined_df: DataFrame with ET index, 'Close' column, 'source' column
          ig_bar_count: number of bars sourced from IG gap data
    """
    # Load IG gap data for the overnight window
    # We need to check both UK dates that the overnight spans
    entry_utc = entry_dt_et.astimezone(TZ_UTC)
    exit_utc = max_exit_dt_et.astimezone(TZ_UTC)
    entry_uk_date = entry_utc.astimezone(TZ_UK).date()
    exit_uk_date = exit_utc.astimezone(TZ_UK).date()

    ig_frames = []
    for uk_d in set([entry_uk_date, exit_uk_date,
                     entry_uk_date + timedelta(days=1)]):
        df_ig = load_ig_gap_5m_for_uk_date(uk_d, gap_dir)
        if not df_ig.empty:
            ig_frames.append(df_ig)

    if not ig_frames:
        # No IG data — return SPY bars as-is with source column
        if spy_bars is not None and not spy_bars.empty:
            result = spy_bars[["Close"]].copy()
            result["source"] = "SPY"
            return result, 0
        return pd.DataFrame(), 0

    # Combine IG data
    ig_all = pd.concat(ig_frames)
    ig_all = ig_all[~ig_all.index.duplicated(keep="first")]
    ig_all = ig_all.sort_index()

    # Convert IG to ET
    ig_all.index = ig_all.index.tz_convert(TZ_ET)

    # Filter to our window
    ig_window = ig_all[
        (ig_all.index >= entry_dt_et) & (ig_all.index <= max_exit_dt_et)
    ].copy()

    # Apply scale factor to convert US500 → SPY price level
    if scale_factor != 1.0 and not ig_window.empty:
        ig_window["Close"] = ig_window["Close"] * scale_factor

    if resample_mode == "ffill" and not ig_window.empty:
        # Resample IG 5m → 1m via forward-fill
        full_idx = pd.date_range(
            start=ig_window.index.min(),
            end=ig_window.index.max(),
            freq="1min",
            tz=TZ_ET,
        )
        ig_resampled = ig_window[["Close"]].reindex(full_idx).ffill()
        ig_resampled["source"] = "IG_GAP_5M"
    elif not ig_window.empty:
        ig_resampled = ig_window[["Close"]].copy()
        ig_resampled["source"] = "IG_GAP_5M"
    else:
        ig_resampled = pd.DataFrame()

    # Prepare SPY bars
    if spy_bars is not None and not spy_bars.empty:
        spy_df = spy_bars[["Close"]].copy()
        spy_df["source"] = "SPY"
    else:
        spy_df = pd.DataFrame()

    # Merge: SPY takes priority where it has bars
    if spy_df.empty and ig_resampled.empty:
        return pd.DataFrame(), 0

    if spy_df.empty:
        combined = ig_resampled
    elif ig_resampled.empty:
        combined = spy_df
    else:
        # Start with IG (fills the gap), then overlay SPY where available
        combined = ig_resampled.copy()
        # Where SPY has bars, use SPY instead
        spy_aligned = spy_df.reindex(combined.index)
        spy_has_data = spy_aligned["Close"].notna()
        combined.loc[spy_has_data, "Close"] = spy_aligned.loc[spy_has_data, "Close"]
        combined.loc[spy_has_data, "source"] = "SPY"

        # Also add SPY bars outside the IG window
        spy_only = spy_df[~spy_df.index.isin(combined.index)]
        if not spy_only.empty:
            combined = pd.concat([combined, spy_only])

    combined = combined.sort_index()
    combined = combined[~combined.index.duplicated(keep="first")]

    # Trim to window
    combined = combined[
        (combined.index >= entry_dt_et) & (combined.index <= max_exit_dt_et)
    ]

    ig_bar_count = int((combined["source"] == "IG_GAP_5M").sum())

    return combined, ig_bar_count
