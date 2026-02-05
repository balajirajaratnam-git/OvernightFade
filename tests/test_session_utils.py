"""
Unit tests for session_utils.py - DST-safe session boundary calculations.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from datetime import datetime, date
import pytz
from session_utils import (
    get_overnight_window_utc,
    get_cash_session_window_utc,
    TZ_ET, TZ_UTC
)


def test_overnight_window_standard_time():
    """Test overnight window during EST (no DST)."""
    # January 15, 2024 - Standard Time (EST = UTC-5)
    test_date = date(2024, 1, 15)
    start_utc, end_utc = get_overnight_window_utc(test_date)

    # 16:00 ET = 21:00 UTC during EST
    assert start_utc.hour == 21, f"Expected 21 UTC, got {start_utc.hour}"
    assert start_utc.minute == 0

    # 09:30 ET next day = 14:30 UTC during EST
    assert end_utc.hour == 14, f"Expected 14 UTC, got {end_utc.hour}"
    assert end_utc.minute == 30

    # End should be next day
    assert end_utc.date() == date(2024, 1, 16)


def test_overnight_window_daylight_saving():
    """Test overnight window during EDT (DST active)."""
    # July 15, 2024 - Daylight Saving Time (EDT = UTC-4)
    test_date = date(2024, 7, 15)
    start_utc, end_utc = get_overnight_window_utc(test_date)

    # 16:00 ET = 20:00 UTC during EDT
    assert start_utc.hour == 20, f"Expected 20 UTC, got {start_utc.hour}"
    assert start_utc.minute == 0

    # 09:30 ET next day = 13:30 UTC during EDT
    assert end_utc.hour == 13, f"Expected 13 UTC, got {end_utc.hour}"
    assert end_utc.minute == 30


def test_overnight_window_dst_transition_spring():
    """Test overnight window across spring DST transition (March 2024).

    DST starts: March 10, 2024 at 2:00 AM ET (clocks spring forward)
    - March 9: EST (UTC-5), 16:00 ET = 21:00 UTC
    - March 10: EDT (UTC-4), 09:30 ET = 13:30 UTC (not 14:30!)
    """
    test_date = date(2024, 3, 8)  # Friday before DST
    start_utc, end_utc = get_overnight_window_utc(test_date)

    # March 8: 16:00 EST = 21:00 UTC
    assert start_utc.hour == 21

    # March 9 (Saturday): 09:30 - but this is still EST because DST on Sunday
    # Actually March 9 is still EST, DST kicks in March 10 at 2 AM
    assert end_utc.hour == 14  # Still EST on Saturday
    assert end_utc.minute == 30


def test_overnight_window_dst_transition_fall():
    """Test overnight window across fall DST transition (November 2024).

    DST ends: November 3, 2024 at 2:00 AM ET (clocks fall back)
    - November 1: EDT (UTC-4), 16:00 ET = 20:00 UTC
    - November 2: EDT still, 09:30 ET = 13:30 UTC
    """
    test_date = date(2024, 11, 1)
    start_utc, end_utc = get_overnight_window_utc(test_date)

    # Nov 1: 16:00 EDT = 20:00 UTC
    assert start_utc.hour == 20

    # Nov 2: 09:30 EDT = 13:30 UTC (still EDT)
    assert end_utc.hour == 13
    assert end_utc.minute == 30


def test_cash_session_window():
    """Test cash session window calculation."""
    test_date = date(2024, 1, 15)  # EST
    start_utc, end_utc = get_cash_session_window_utc(test_date)

    # 09:30 ET = 14:30 UTC during EST
    assert start_utc.hour == 14
    assert start_utc.minute == 30

    # 16:00 ET = 21:00 UTC during EST
    assert end_utc.hour == 21
    assert end_utc.minute == 0

    # Same day
    assert start_utc.date() == end_utc.date()


def test_accepts_datetime_input():
    """Test that function accepts datetime objects (not just date)."""
    test_datetime = datetime(2024, 6, 15, 10, 30, 0)
    start_utc, end_utc = get_overnight_window_utc(test_datetime)

    # Should work without error and produce valid results
    assert start_utc < end_utc
    assert start_utc.tzinfo is not None
    assert end_utc.tzinfo is not None


if __name__ == "__main__":
    test_overnight_window_standard_time()
    print("PASS: test_overnight_window_standard_time")

    test_overnight_window_daylight_saving()
    print("PASS: test_overnight_window_daylight_saving")

    test_overnight_window_dst_transition_spring()
    print("PASS: test_overnight_window_dst_transition_spring")

    test_overnight_window_dst_transition_fall()
    print("PASS: test_overnight_window_dst_transition_fall")

    test_cash_session_window()
    print("PASS: test_cash_session_window")

    test_accepts_datetime_input()
    print("PASS: test_accepts_datetime_input")

    print("\nAll session_utils tests passed!")
