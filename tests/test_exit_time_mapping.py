"""
Unit tests for UK-time exit mapping: "first occurrence AFTER entry".

Tests three DST cases:
  1. Normal (no DST transition): UK-US offset is +5 hours (EST)
  2. US-springs-forward (second Sun of March): UK still GMT, US moves to EDT
     → offset shrinks from +5 to +4
  3. US-falls-back (first Sun of November): US moves from EDT to EST
     → offset grows from +4 to +5

The UK-time exit algorithm (from the runner's uk_time exit block):
  1. Convert entry_dt_et to UK time
  2. Build candidate UK exit at same UK calendar date, HH:MM
  3. If candidate <= entry_uk, advance one calendar day
  4. Localize via TZ_UK.localize(), convert to ET via .astimezone(TZ_ET)

This test validates the algorithm directly, ensuring the exit datetime
is always strictly AFTER entry regardless of DST transitions.
"""
import sys
import os
import pytest
from datetime import datetime, timedelta

import pytz

# Ensure src/ is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from trading_calendar import TZ_ET, TZ_UK, make_entry_dt


def _uk_exit_first_after_entry(entry_dt_et, uk_exit_h, uk_exit_m):
    """
    Replicate the runner's UK-time exit logic:
      "first occurrence of uk_exit_h:uk_exit_m UK AFTER entry"

    This is extracted verbatim from run_backtest_overnight_fade.py's
    uk_time exit block for testability.
    """
    _entry_uk = entry_dt_et.astimezone(TZ_UK)
    _cand_date = _entry_uk.date()
    _cand_naive = datetime(_cand_date.year, _cand_date.month, _cand_date.day,
                           uk_exit_h, uk_exit_m)
    _exit_dt_uk = TZ_UK.localize(_cand_naive)
    if _exit_dt_uk <= _entry_uk:
        _next_date = _cand_date + timedelta(days=1)
        _cand_naive = datetime(_next_date.year, _next_date.month, _next_date.day,
                               uk_exit_h, uk_exit_m)
        _exit_dt_uk = TZ_UK.localize(_cand_naive)
    actual_exit_dt_et = _exit_dt_uk.astimezone(TZ_ET)
    return actual_exit_dt_et, _exit_dt_uk


class TestUKExitFirstAfterEntry:
    """Test the 'first occurrence after entry' UK-time exit mapping."""

    # -----------------------------------------------------------------
    # Case 1: Normal — no DST transition (mid-January)
    #   US is EST (UTC-5), UK is GMT (UTC+0), offset = +5h
    #   Entry: Wed 2025-01-15 16:00 ET = 21:00 UK
    #   UK exit 08:30 → next occurrence is Thu 2025-01-16 08:30 UK
    #     = Thu 2025-01-16 03:30 ET (EST, UTC-5)
    # -----------------------------------------------------------------
    def test_normal_no_dst(self):
        entry_dt_et = make_entry_dt(datetime(2025, 1, 15).date())  # Wed 16:00 ET
        assert entry_dt_et.strftime("%H:%M") == "16:00"

        exit_dt_et, exit_dt_uk = _uk_exit_first_after_entry(entry_dt_et, 8, 30)

        # UK exit should be Thu 08:30 UK
        assert exit_dt_uk.date() == datetime(2025, 1, 16).date()
        assert exit_dt_uk.strftime("%H:%M") == "08:30"
        assert exit_dt_uk.tzname() == "GMT"

        # ET conversion: 08:30 GMT - 5h = 03:30 EST
        assert exit_dt_et.strftime("%H:%M") == "03:30"
        assert exit_dt_et.date() == datetime(2025, 1, 16).date()
        assert exit_dt_et.tzname() == "EST"

        # Must be strictly after entry
        assert exit_dt_et > entry_dt_et

    # -----------------------------------------------------------------
    # Case 2: US springs forward — second Sunday of March
    #   2025-03-09 is spring-forward Sunday (US clocks advance at 02:00)
    #   Entry: Thu 2025-03-06 16:00 ET (still EST, UTC-5)
    #     = Thu 2025-03-06 21:00 UK (GMT)
    #   UK exit 08:30 → Fri 2025-03-07 08:30 UK (GMT, UTC+0)
    #     = Fri 2025-03-07 03:30 ET (still EST — spring-forward hasn't happened)
    #
    #   Also test Mon 2025-03-10 (day after spring-forward):
    #   Entry: Mon 2025-03-10 16:00 ET (now EDT, UTC-4)
    #     = Mon 2025-03-10 20:00 UK (GMT)
    #   UK exit 08:30 → Tue 2025-03-11 08:30 UK (GMT, UTC+0)
    #     = Tue 2025-03-11 04:30 ET (EDT, UTC-4)
    # -----------------------------------------------------------------
    def test_us_spring_forward_before(self):
        """Before US spring-forward: US is EST, UK is GMT."""
        entry_dt_et = make_entry_dt(datetime(2025, 3, 6).date())  # Thu
        exit_dt_et, exit_dt_uk = _uk_exit_first_after_entry(entry_dt_et, 8, 30)

        assert exit_dt_uk.date() == datetime(2025, 3, 7).date()
        assert exit_dt_uk.strftime("%H:%M") == "08:30"
        # 08:30 GMT = 03:30 EST
        assert exit_dt_et.strftime("%H:%M") == "03:30"
        assert exit_dt_et.tzname() == "EST"
        assert exit_dt_et > entry_dt_et

    def test_us_spring_forward_after(self):
        """After US spring-forward: US is EDT, UK still GMT."""
        entry_dt_et = make_entry_dt(datetime(2025, 3, 10).date())  # Mon after DST
        assert entry_dt_et.tzname() == "EDT"

        exit_dt_et, exit_dt_uk = _uk_exit_first_after_entry(entry_dt_et, 8, 30)

        assert exit_dt_uk.date() == datetime(2025, 3, 11).date()
        assert exit_dt_uk.strftime("%H:%M") == "08:30"
        # 08:30 GMT = 04:30 EDT (offset now +4, not +5)
        assert exit_dt_et.strftime("%H:%M") == "04:30"
        assert exit_dt_et.tzname() == "EDT"
        assert exit_dt_et > entry_dt_et

    # -----------------------------------------------------------------
    # Case 3: US falls back — first Sunday of November
    #   2025-11-02 is fall-back Sunday (US clocks go back at 02:00)
    #   Entry: Thu 2025-10-30 16:00 ET (EDT, UTC-4)
    #     = Thu 2025-10-30 20:00 UK (GMT)
    #   UK exit 08:30 → Fri 2025-10-31 08:30 UK (GMT, UTC+0)
    #     = Fri 2025-10-31 04:30 ET (still EDT)
    #
    #   Also test Mon 2025-11-03 (day after fall-back):
    #   Entry: Mon 2025-11-03 16:00 ET (now EST, UTC-5)
    #     = Mon 2025-11-03 21:00 UK (GMT)
    #   UK exit 08:30 → Tue 2025-11-04 08:30 UK (GMT, UTC+0)
    #     = Tue 2025-11-04 03:30 ET (EST, UTC-5)
    # -----------------------------------------------------------------
    def test_us_fall_back_before(self):
        """Before US fall-back: US is EDT, UK is GMT."""
        entry_dt_et = make_entry_dt(datetime(2025, 10, 30).date())  # Thu
        assert entry_dt_et.tzname() == "EDT"

        exit_dt_et, exit_dt_uk = _uk_exit_first_after_entry(entry_dt_et, 8, 30)

        assert exit_dt_uk.date() == datetime(2025, 10, 31).date()
        assert exit_dt_uk.strftime("%H:%M") == "08:30"
        # 08:30 GMT = 04:30 EDT
        assert exit_dt_et.strftime("%H:%M") == "04:30"
        assert exit_dt_et.tzname() == "EDT"
        assert exit_dt_et > entry_dt_et

    def test_us_fall_back_after(self):
        """After US fall-back: US is EST, UK still GMT."""
        entry_dt_et = make_entry_dt(datetime(2025, 11, 3).date())  # Mon after fall-back
        assert entry_dt_et.tzname() == "EST"

        exit_dt_et, exit_dt_uk = _uk_exit_first_after_entry(entry_dt_et, 8, 30)

        assert exit_dt_uk.date() == datetime(2025, 11, 4).date()
        assert exit_dt_uk.strftime("%H:%M") == "08:30"
        # 08:30 GMT = 03:30 EST (offset +5 again)
        assert exit_dt_et.strftime("%H:%M") == "03:30"
        assert exit_dt_et.tzname() == "EST"
        assert exit_dt_et > entry_dt_et


class TestUKExitEdgeCases:
    """Edge cases for UK-time exit mapping."""

    def test_uk_exit_same_uk_day_is_advanced(self):
        """
        If UK exit time is earlier than entry in UK time on the same
        UK calendar day, the algorithm must advance to the next day.

        Entry: 16:00 ET = 21:00 UK (same day)
        UK exit at 06:00 → 06:00 UK same day is BEFORE 21:00
        → must advance to next day 06:00 UK
        """
        entry_dt_et = make_entry_dt(datetime(2025, 1, 15).date())
        exit_dt_et, exit_dt_uk = _uk_exit_first_after_entry(entry_dt_et, 6, 0)

        # Must be next day
        assert exit_dt_uk.date() == datetime(2025, 1, 16).date()
        assert exit_dt_uk.strftime("%H:%M") == "06:00"
        assert exit_dt_et > entry_dt_et

    def test_uk_bst_period(self):
        """
        During UK BST (last Sun March to last Sun October):
        UK is BST (UTC+1), US is EDT (UTC-4), offset = +5h.

        Entry: Mon 2025-06-16 16:00 ET (EDT, UTC-4)
          = Mon 2025-06-16 21:00 UK (BST, UTC+1)
        UK exit 08:30 → Tue 2025-06-17 08:30 UK (BST, UTC+1)
          = Tue 2025-06-17 03:30 ET (EDT, UTC-4)
        """
        entry_dt_et = make_entry_dt(datetime(2025, 6, 16).date())
        assert entry_dt_et.tzname() == "EDT"

        exit_dt_et, exit_dt_uk = _uk_exit_first_after_entry(entry_dt_et, 8, 30)

        assert exit_dt_uk.date() == datetime(2025, 6, 17).date()
        assert exit_dt_uk.strftime("%H:%M") == "08:30"
        assert exit_dt_uk.tzname() == "BST"
        # 08:30 BST (UTC+1) = 03:30 EDT (UTC-4)
        assert exit_dt_et.strftime("%H:%M") == "03:30"
        assert exit_dt_et.tzname() == "EDT"
        assert exit_dt_et > entry_dt_et

    def test_uk_spring_forward(self):
        """
        UK springs forward last Sunday of March (2025-03-30).
        US already on EDT (from 2025-03-09).

        Entry: Thu 2025-03-27 16:00 ET (EDT, UTC-4)
          = Thu 2025-03-27 20:00 UK (GMT, UTC+0)
        UK exit 08:30 → Fri 2025-03-28 08:30 UK (still GMT)
          = Fri 2025-03-28 04:30 ET (EDT)

        Then Mon 2025-03-31 (after UK spring-forward):
        Entry: Mon 2025-03-31 16:00 ET (EDT, UTC-4)
          = Mon 2025-03-31 21:00 UK (BST, UTC+1)
        UK exit 08:30 → Tue 2025-04-01 08:30 UK (BST, UTC+1)
          = Tue 2025-04-01 03:30 ET (EDT, UTC-4)
        """
        # Before UK spring-forward
        entry_before = make_entry_dt(datetime(2025, 3, 27).date())
        exit_before, exit_uk_before = _uk_exit_first_after_entry(entry_before, 8, 30)

        assert exit_uk_before.tzname() == "GMT"
        assert exit_before.strftime("%H:%M") == "04:30"  # 08:30 GMT = 04:30 EDT
        assert exit_before > entry_before

        # After UK spring-forward
        entry_after = make_entry_dt(datetime(2025, 3, 31).date())
        exit_after, exit_uk_after = _uk_exit_first_after_entry(entry_after, 8, 30)

        assert exit_uk_after.tzname() == "BST"
        assert exit_after.strftime("%H:%M") == "03:30"  # 08:30 BST (UTC+1) = 03:30 EDT
        assert exit_after > entry_after


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
