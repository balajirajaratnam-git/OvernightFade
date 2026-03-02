"""
NYSE trading calendar helpers for OvernightFade backtesting.

Single source of truth for:
  - Which dates are trading days (NYSE schedule)
  - Entry/exit/expiry timestamp construction (timezone-aware, DST-safe)
  - Weekly expiry mapping (holiday-aware, using trading days not naive weekdays)

All datetimes are returned as America/New_York aware.

Uses pandas_market_calendars (NYSE) for the authoritative holiday list.
"""

from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Optional, Set

import pandas as pd
import pytz

import pandas_market_calendars as mcal

TZ_ET = pytz.timezone('America/New_York')
TZ_UK = pytz.timezone('Europe/London')

# ---------------------------------------------------------------------------
# Schedule cache — built once per date range
# ---------------------------------------------------------------------------

_nyse = mcal.get_calendar('NYSE')
_schedule_cache = {}    # (start, end) -> DataFrame
_trading_set_cache = {} # (start, end) -> set of date objects


def get_nyse_schedule(start_date, end_date):
    """
    Return the NYSE schedule DataFrame for the given range.

    The schedule has timezone-aware market_open / market_close columns (UTC).
    Results are cached so repeated calls are free.

    Args:
        start_date: str or date-like  (e.g. '2016-01-01')
        end_date:   str or date-like  (e.g. '2026-12-31')

    Returns:
        pd.DataFrame with DatetimeIndex (dates), columns: market_open, market_close
    """
    key = (str(start_date), str(end_date))
    if key not in _schedule_cache:
        sched = _nyse.schedule(start_date=start_date, end_date=end_date)
        _schedule_cache[key] = sched
        # Also build the fast lookup set of date objects
        _trading_set_cache[key] = set(sched.index.date)
    return _schedule_cache[key]


def _get_trading_dates_set(start_date='2000-01-01', end_date='2030-12-31') -> Set[date]:
    """Return cached set of NYSE trading dates as Python date objects."""
    key = (str(start_date), str(end_date))
    if key not in _trading_set_cache:
        get_nyse_schedule(start_date, end_date)
    return _trading_set_cache[key]


# ---------------------------------------------------------------------------
# Core calendar functions
# ---------------------------------------------------------------------------

def is_trading_day(d, trading_dates: Optional[Set[date]] = None) -> bool:
    """Check if a date is an NYSE trading day."""
    if trading_dates is not None:
        return _to_date(d) in trading_dates
    return _to_date(d) in _get_trading_dates_set()


def next_trading_day(d, trading_dates: Optional[Set[date]] = None) -> date:
    """
    Return the next NYSE trading day strictly after d.

    Scans forward up to 10 calendar days.
    Raises ValueError if no trading day found.
    """
    tds = trading_dates if trading_dates is not None else _get_trading_dates_set()
    check = _to_date(d) + timedelta(days=1)
    for _ in range(10):
        if check in tds:
            return check
        check += timedelta(days=1)
    raise ValueError(f"No trading day found within 10 days after {d}")


def prev_trading_day(d, trading_dates: Optional[Set[date]] = None) -> date:
    """
    Return the previous NYSE trading day strictly before d.

    Scans backward up to 10 calendar days.
    Raises ValueError if no trading day found.
    """
    tds = trading_dates if trading_dates is not None else _get_trading_dates_set()
    check = _to_date(d) - timedelta(days=1)
    for _ in range(10):
        if check in tds:
            return check
        check -= timedelta(days=1)
    raise ValueError(f"No trading day found within 10 days before {d}")


# ---------------------------------------------------------------------------
# Timestamp builders (DST-safe)
# ---------------------------------------------------------------------------

def make_entry_dt(trade_date) -> datetime:
    """
    Build 16:00 America/New_York on trade_date.

    Uses localize() so pytz handles DST correctly for that specific date.
    """
    d = _to_date(trade_date)
    return TZ_ET.localize(datetime(d.year, d.month, d.day, 16, 0))


def make_exit_dt(trade_date, trading_dates: Optional[Set[date]] = None) -> datetime:
    """
    Build 09:30 America/New_York on the next trading day after trade_date.

    Args:
        trade_date: The entry day.
        trading_dates: Optional pre-built set of trading dates.

    Returns:
        Timezone-aware datetime at 09:30 ET on the next trading day.
    """
    ntd = next_trading_day(trade_date, trading_dates)
    return TZ_ET.localize(datetime(ntd.year, ntd.month, ntd.day, 9, 30))


def make_exit_dt_at(trade_date, hour: int, minute: int,
                    trading_dates: Optional[Set[date]] = None) -> datetime:
    """
    Build hour:minute America/New_York on the next trading day after trade_date.

    Unlike make_exit_dt() which hardcodes 09:30, this allows arbitrary
    exit times for UK-time and fixed-ET-time exit variants.

    Args:
        trade_date: The entry day.
        hour: Hour in ET (0-23).
        minute: Minute (0-59).
        trading_dates: Optional pre-built set of trading dates.

    Returns:
        Timezone-aware datetime at hour:minute ET on the next trading day.
    """
    ntd = next_trading_day(trade_date, trading_dates)
    return TZ_ET.localize(datetime(ntd.year, ntd.month, ntd.day, hour, minute))


def uk_time_to_et(exit_date, uk_hour: int, uk_minute: int) -> datetime:
    """
    Convert a UK time on a specific date to an ET datetime.

    DST-safe: uses localize() on both sides so pytz handles
    UK/US DST mismatch windows correctly.

    Args:
        exit_date: Python date on which the UK time occurs.
        uk_hour: Hour in UK time (0-23).
        uk_minute: Minute (0-59).

    Returns:
        Timezone-aware datetime in America/New_York.
    """
    d = _to_date(exit_date)
    uk_dt = TZ_UK.localize(datetime(d.year, d.month, d.day, uk_hour, uk_minute))
    return uk_dt.astimezone(TZ_ET)


def make_expiry_dt(expiry_date) -> datetime:
    """
    Build 16:00 America/New_York on expiry_date (contract settlement time).
    """
    d = _to_date(expiry_date)
    return TZ_ET.localize(datetime(d.year, d.month, d.day, 16, 0))


# ---------------------------------------------------------------------------
# Weekly expiry mapping (holiday-aware)
# ---------------------------------------------------------------------------

def weekly_expiry_date(trade_date, trading_dates: Optional[Set[date]] = None) -> Optional[date]:
    """
    Map a trade entry date to its IG weekly expiry date using trading days.

    Rules (using the actual week of trade_date):
      Monday or Tuesday   -> Wednesday of that week (as trading day)
      Wednesday or Thursday -> Friday of that week (as trading day)
      Friday               -> None (skip — no trade)

    If the target expiry weekday is not a trading day (holiday), the expiry
    rolls FORWARD to the next trading day. This is logged by the caller.

    Args:
        trade_date: The entry day (must be a trading day).
        trading_dates: Optional pre-built set of trading dates for fast lookup.

    Returns:
        date: The expiry date, or None if trade_date is a Friday.
    """
    tds = trading_dates if trading_dates is not None else _get_trading_dates_set()
    d = _to_date(trade_date)
    dow = d.weekday()  # 0=Mon, 4=Fri

    if dow == 4:
        return None  # Friday — skip

    # Target weekday for expiry
    if dow in (0, 1):      # Mon/Tue -> Wed
        target_dow = 2
    elif dow in (2, 3):    # Wed/Thu -> Fri
        target_dow = 4
    else:
        return None

    # Calculate the target date (same week)
    days_ahead = target_dow - dow
    target_date = d + timedelta(days=days_ahead)

    # If target is a trading day, use it directly
    if target_date in tds:
        return target_date

    # Holiday roll: forward to next trading day
    rolled = target_date
    for _ in range(10):
        rolled += timedelta(days=1)
        if rolled in tds:
            return rolled

    raise ValueError(f"Could not find trading day near expiry target {target_date}")


# ---------------------------------------------------------------------------
# Utility to build trading dates set from schedule
# ---------------------------------------------------------------------------

def build_trading_dates_set(schedule: pd.DataFrame) -> Set[date]:
    """
    Convert a pandas_market_calendars schedule DataFrame to a set of date objects.

    Args:
        schedule: DataFrame from get_nyse_schedule()

    Returns:
        Set of Python date objects.
    """
    return set(schedule.index.date)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _to_date(d) -> date:
    """Coerce various date-like inputs to a Python date object."""
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    if isinstance(d, pd.Timestamp):
        return d.date()
    if isinstance(d, str):
        return pd.Timestamp(d).date()
    raise TypeError(f"Cannot convert {type(d)} to date: {d}")
