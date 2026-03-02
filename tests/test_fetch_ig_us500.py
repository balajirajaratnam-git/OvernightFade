"""
Unit tests for scripts/data/fetch_ig_us500_1m.py utility functions.

Tests pure logic without IG API credentials:
  - UK date range generation
  - UK date → UTC window conversion (DST-safe)
  - Parquet path naming
  - Parquet integrity checks
  - EPIC resolution chain
  - Rate limiter token bucket
  - State persistence
"""

import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import pandas as pd
import pytz
import pytest

# Import the module under test — adjust sys.path so it finds the script
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "data"))
from fetch_ig_us500_1m import (
    TZ_UK, TZ_UTC, MIN_BARS_PER_DAY,
    get_uk_dates_to_fetch,
    uk_date_to_utc_window,
    parquet_path,
    is_valid_cached,
    write_parquet_atomic,
    load_state, save_state,
    resolve_epic,
    IGRateLimiter,
)


# =====================================================================
# UK date generation
# =====================================================================

class TestGetUKDates:
    """Test get_uk_dates_to_fetch()."""

    def test_returns_correct_count(self):
        """days=3 should return 3 dates."""
        # Mock "now UK" to a fixed point: 2026-02-10 14:00 UK
        with mock.patch("fetch_ig_us500_1m.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 10, 14, 0, 0, tzinfo=TZ_UK)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            dates = get_uk_dates_to_fetch(3)
        assert len(dates) == 3
        assert dates[0] == date(2026, 2, 7)
        assert dates[-1] == date(2026, 2, 9)

    def test_never_includes_today(self):
        """Should never include the current UK date (incomplete day)."""
        with mock.patch("fetch_ig_us500_1m.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 1, 23, 59, 59, tzinfo=TZ_UK)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            dates = get_uk_dates_to_fetch(1)
        # Today is 2026-03-01, should return only 2026-02-28
        assert date(2026, 3, 1) not in dates
        assert dates[-1] == date(2026, 2, 28)

    def test_ascending_order(self):
        """Dates should be in ascending order."""
        with mock.patch("fetch_ig_us500_1m.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 15, 12, 0, 0, tzinfo=TZ_UK)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            dates = get_uk_dates_to_fetch(5)
        assert dates == sorted(dates)


# =====================================================================
# UK date → UTC window (DST-safe)
# =====================================================================

class TestUKDateToUTCWindow:
    """Test uk_date_to_utc_window() across DST transitions."""

    def test_winter_uk_equals_utc(self):
        """In winter (GMT), UK == UTC. No offset."""
        start, end = uk_date_to_utc_window(date(2026, 1, 15))
        # 2026-01-15 UK is GMT → UTC offset = 0
        assert start == "2026-01-15 00:00:00"
        assert end == "2026-01-15 23:59:59"

    def test_summer_uk_is_bst(self):
        """In summer (BST), UK is UTC+1. Window shifts 1h earlier in UTC."""
        # 2026 UK clocks go forward on 29 Mar (BST starts)
        start, end = uk_date_to_utc_window(date(2026, 7, 1))
        # 2026-07-01 00:00 BST = 2026-06-30 23:00 UTC
        assert start == "2026-06-30 23:00:00"
        # 2026-07-01 23:59:59 BST = 2026-07-01 22:59:59 UTC
        assert end == "2026-07-01 22:59:59"

    def test_spring_forward_boundary(self):
        """Day of UK spring forward (29 Mar 2026)."""
        # On this day, clocks go forward at 01:00 → 02:00 UK
        # 2026-03-29 00:00 UK is still GMT → UTC offset = 0
        start, end = uk_date_to_utc_window(date(2026, 3, 29))
        # Start: 00:00 GMT = 00:00 UTC
        assert start == "2026-03-29 00:00:00"
        # End: 23:59 BST = 22:59 UTC (BST is in effect by end of day)
        assert end == "2026-03-29 22:59:59"

    def test_fall_back_boundary(self):
        """Day of UK fall back (25 Oct 2026)."""
        # Clocks go back at 02:00 → 01:00 UK
        # Day starts in BST, ends in GMT
        start, end = uk_date_to_utc_window(date(2026, 10, 25))
        # Start: 00:00 BST = 23:00 previous day UTC
        assert start == "2026-10-24 23:00:00"
        # End: 23:59 GMT = 23:59 UTC
        assert end == "2026-10-25 23:59:59"


# =====================================================================
# Parquet path naming
# =====================================================================

class TestParquetPath:
    """Test parquet_path() naming."""

    def test_iso_format(self):
        p = parquet_path(Path("data/IG_US500_1m"), date(2026, 2, 28))
        assert p == Path("data/IG_US500_1m/2026-02-28.parquet")

    def test_different_dates(self):
        p1 = parquet_path(Path("out"), date(2026, 1, 1))
        p2 = parquet_path(Path("out"), date(2026, 12, 31))
        assert p1.name == "2026-01-01.parquet"
        assert p2.name == "2026-12-31.parquet"


# =====================================================================
# Integrity checks
# =====================================================================

class TestIntegrityChecks:
    """Test is_valid_cached() integrity logic."""

    def _make_good_df(self, n=200):
        """Create a valid UTC-indexed DataFrame with n rows."""
        idx = pd.date_range(
            "2026-02-10 00:00", periods=n, freq="1min", tz="UTC"
        )
        return pd.DataFrame(
            {"Open": range(n), "Close": range(n)},
            index=idx,
        )

    def test_valid_file_passes(self, tmp_path):
        import logging
        logger = logging.getLogger("test")
        df = self._make_good_df(200)
        path = tmp_path / "2026-02-10.parquet"
        df.to_parquet(path, engine="pyarrow")
        assert is_valid_cached(path, logger) is True

    def test_missing_file_fails(self, tmp_path):
        import logging
        logger = logging.getLogger("test")
        path = tmp_path / "nonexistent.parquet"
        assert is_valid_cached(path, logger) is False

    def test_partial_day_is_valid(self, tmp_path):
        """Files below MIN_BARS_PER_DAY are still structurally valid."""
        import logging
        logger = logging.getLogger("test")
        df = self._make_good_df(10)  # Below MIN_BARS_PER_DAY but structurally OK
        path = tmp_path / "2026-02-10.parquet"
        df.to_parquet(path, engine="pyarrow")
        assert is_valid_cached(path, logger) is True

    def test_empty_file_fails(self, tmp_path):
        """Files with 0 bars are invalid."""
        import logging
        logger = logging.getLogger("test")
        df = self._make_good_df(200).iloc[:0]  # Empty DataFrame
        path = tmp_path / "2026-02-10.parquet"
        df.to_parquet(path, engine="pyarrow")
        assert is_valid_cached(path, logger) is False

    def test_tz_naive_index_fails(self, tmp_path):
        import logging
        logger = logging.getLogger("test")
        idx = pd.date_range("2026-02-10 00:00", periods=200, freq="1min")
        df = pd.DataFrame({"Open": range(200)}, index=idx)
        path = tmp_path / "2026-02-10.parquet"
        df.to_parquet(path, engine="pyarrow")
        assert is_valid_cached(path, logger) is False

    def test_non_monotonic_fails(self, tmp_path):
        import logging
        logger = logging.getLogger("test")
        idx = pd.date_range(
            "2026-02-10 00:00", periods=200, freq="1min", tz="UTC"
        )
        # Reverse to make non-monotonic
        idx = idx[::-1]
        df = pd.DataFrame({"Open": range(200)}, index=idx)
        path = tmp_path / "2026-02-10.parquet"
        df.to_parquet(path, engine="pyarrow")
        assert is_valid_cached(path, logger) is False


# =====================================================================
# Atomic parquet write
# =====================================================================

class TestAtomicWrite:
    """Test write_parquet_atomic()."""

    def test_creates_file_and_subdir(self, tmp_path):
        import logging
        logger = logging.getLogger("test")
        outdir = tmp_path / "sub" / "dir"
        path = outdir / "2026-02-10.parquet"
        idx = pd.date_range("2026-02-10", periods=5, freq="1min", tz="UTC")
        df = pd.DataFrame({"Close": [1, 2, 3, 4, 5]}, index=idx)
        write_parquet_atomic(df, path, logger)
        assert path.exists()
        df2 = pd.read_parquet(path)
        assert len(df2) == 5

    def test_no_temp_file_left(self, tmp_path):
        import logging
        logger = logging.getLogger("test")
        path = tmp_path / "test.parquet"
        idx = pd.date_range("2026-02-10", periods=5, freq="1min", tz="UTC")
        df = pd.DataFrame({"Close": [1, 2, 3, 4, 5]}, index=idx)
        write_parquet_atomic(df, path, logger)
        # No .tmp or .parquet temp files should remain
        leftovers = list(tmp_path.glob("*.tmp")) + list(tmp_path.glob("tmp*"))
        assert len(leftovers) == 0


# =====================================================================
# State persistence
# =====================================================================

class TestState:
    """Test load_state() and save_state()."""

    def test_default_state(self, tmp_path):
        state = load_state(tmp_path / "nonexistent.json")
        assert state["last_completed_date"] is None
        assert state["dates_missing"] == []
        assert state["last_error"] is None
        assert state["cooldown_until_utc"] is None

    def test_roundtrip(self, tmp_path):
        path = tmp_path / "state.json"
        state = {
            "last_completed_date": "2026-02-10",
            "dates_missing": ["2026-02-08"],
            "last_error": None,
            "cooldown_until_utc": None,
            "total_requests_all_runs": 42,
        }
        save_state(path, state)
        loaded = load_state(path)
        assert loaded == state

    def test_corrupt_json_returns_default(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("{{not valid json")
        state = load_state(path)
        assert state["last_completed_date"] is None


# =====================================================================
# EPIC resolution chain
# =====================================================================

class TestEPICResolution:
    """Test resolve_epic() priority: CLI > config > env > default."""

    def test_cli_takes_priority(self, tmp_path):
        epic = resolve_epic("MY.CUSTOM.EPIC")
        assert epic == "MY.CUSTOM.EPIC"

    def test_config_json(self, tmp_path, monkeypatch):
        # Create a temporary config.json
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({
            "ig_api": {"underlying_epic": "IX.FROM.CONFIG"}
        }))
        # Monkey-patch Path to find our config
        monkeypatch.chdir(tmp_path)
        epic = resolve_epic(None)
        assert epic == "IX.FROM.CONFIG"

    def test_env_variable(self, monkeypatch, tmp_path):
        # No config.json, but env var set
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("IG_US500_EPIC", "IX.FROM.ENV")
        epic = resolve_epic(None)
        assert epic == "IX.FROM.ENV"

    def test_default_fallback(self, monkeypatch, tmp_path):
        # No CLI, no config, no env
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("IG_US500_EPIC", raising=False)
        epic = resolve_epic(None)
        assert epic == "IX.D.SPTRD.DAILY.IP"


# =====================================================================
# Rate limiter
# =====================================================================

class TestRateLimiter:
    """Test IGRateLimiter token bucket logic (no actual sleeping)."""

    def test_under_limit_no_wait(self):
        """Under RPM limit should not block."""
        import logging
        limiter = IGRateLimiter(max_rpm=4, logger=logging.getLogger("test"))
        # Record 3 requests (under limit of 4)
        for _ in range(3):
            limiter.record_request()
        assert limiter.requests_this_run == 3

    def test_max_sleep_exceeded_raises(self):
        """Should raise RuntimeError when max sleep would be exceeded."""
        import logging
        limiter = IGRateLimiter(
            max_rpm=4, max_total_sleep=0,  # 0 means any sleep raises
            logger=logging.getLogger("test"),
        )
        with pytest.raises(RuntimeError, match="exceed max sleep"):
            limiter.backoff_sleep(0)

    def test_backoff_respects_retry_after(self):
        """backoff_sleep with retry_after should use that value as base."""
        import logging
        limiter = IGRateLimiter(
            max_rpm=4, max_total_sleep=1000,
            logger=logging.getLogger("test"),
        )
        # Mock time.sleep to not actually sleep
        with mock.patch("fetch_ig_us500_1m.time.sleep"):
            slept = limiter.backoff_sleep(0, retry_after=30)
        # Should be ~30 + jitter (0-4.5)
        assert 30.0 <= slept <= 34.5

    def test_backoff_exponential(self):
        """Higher attempt should produce longer base wait."""
        import logging
        limiter = IGRateLimiter(
            max_rpm=4, max_total_sleep=99999,
            logger=logging.getLogger("test"),
        )
        with mock.patch("fetch_ig_us500_1m.time.sleep"):
            slept0 = limiter.backoff_sleep(0)  # base = 15
            slept2 = limiter.backoff_sleep(2)  # base = 60
        # Attempt 2 should be meaningfully longer than attempt 0
        assert slept2 > slept0
