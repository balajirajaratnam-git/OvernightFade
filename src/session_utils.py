"""
Session time utilities for DST-safe overnight window calculation.
"""
from datetime import datetime, time, timedelta
import pytz

TZ_ET = pytz.timezone("America/New_York")
TZ_UTC = pytz.UTC

# Session boundaries in Eastern Time
SESSION_CLOSE_ET = time(16, 0)   # 4:00 PM ET - cash session close
SESSION_OPEN_ET = time(9, 30)    # 9:30 AM ET - cash session open


def get_overnight_window_utc(date_obj: datetime) -> tuple:
    """
    Calculate the overnight session window for a given trading date.

    The overnight window is defined as:
    - Start: 16:00 ET on date_t (cash session close)
    - End: 09:30 ET on date_t+1 (cash session open next day)

    This function is DST-safe: it localizes naive datetimes in America/New_York
    timezone before converting to UTC, handling DST transitions correctly.

    Args:
        date_obj: A datetime or date object representing the trading day.

    Returns:
        Tuple of (start_utc, end_utc) as timezone-aware UTC datetimes.
    """
    if hasattr(date_obj, 'date'):
        date_obj = date_obj.date() if hasattr(date_obj, 'date') else date_obj

    # Create naive datetimes in ET
    naive_start = datetime.combine(date_obj, SESSION_CLOSE_ET)
    naive_end = datetime.combine(date_obj + timedelta(days=1), SESSION_OPEN_ET)

    # Localize to ET (handles DST automatically)
    loc_start = TZ_ET.localize(naive_start)
    loc_end = TZ_ET.localize(naive_end)

    # Convert to UTC
    return loc_start.astimezone(TZ_UTC), loc_end.astimezone(TZ_UTC)


def get_cash_session_window_utc(date_obj: datetime) -> tuple:
    """
    Calculate the cash trading session window for a given date.

    The cash session is defined as:
    - Start: 09:30 ET on date_t
    - End: 16:00 ET on date_t

    Args:
        date_obj: A datetime or date object representing the trading day.

    Returns:
        Tuple of (start_utc, end_utc) as timezone-aware UTC datetimes.
    """
    if hasattr(date_obj, 'date'):
        date_obj = date_obj.date() if hasattr(date_obj, 'date') else date_obj

    naive_start = datetime.combine(date_obj, SESSION_OPEN_ET)
    naive_end = datetime.combine(date_obj, SESSION_CLOSE_ET)

    loc_start = TZ_ET.localize(naive_start)
    loc_end = TZ_ET.localize(naive_end)

    return loc_start.astimezone(TZ_UTC), loc_end.astimezone(TZ_UTC)


def is_after_cash_close_et(check_time: datetime = None) -> bool:
    """
    Check if current time (or provided time) is after 16:05 ET.

    Used to determine if we can derive today's daily bar from minute data.

    Args:
        check_time: Optional datetime to check. If None, uses current time.

    Returns:
        True if after 16:05 ET, False otherwise.
    """
    if check_time is None:
        check_time = datetime.now(TZ_UTC)
    elif check_time.tzinfo is None:
        check_time = TZ_UTC.localize(check_time)

    check_time_et = check_time.astimezone(TZ_ET)
    return check_time_et.time() >= time(16, 5)
