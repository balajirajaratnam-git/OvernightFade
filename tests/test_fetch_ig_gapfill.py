"""
Unit tests for scripts/data/fetch_ig_us500_gapfill_5m.py utility functions.

Tests pure logic without IG API credentials:
  - Gap detection algorithm
  - UK window → UTC conversion (DST-safe)
  - Allowance-aware exit logic
  - Credential env selection
"""

import json
import os
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import pytz
import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "data"))
from fetch_ig_us500_gapfill_5m import (
    TZ_UK, TZ_ET, TZ_UTC,
    detect_missing_window,
    uk_window_to_utc,
    gapfill_parquet_path,
    is_valid_gapfill,
    get_dates_with_1m_files,
    load_credentials,
    AllowanceExhaustedError,
    ALLOWANCE_SAFETY_BUFFER,
)


# =====================================================================
# Gap detection
# =====================================================================

class TestDetectMissingWindow:
    """Test the detection algorithm."""

    def _make_1m_file(self, path: Path, uk_date: date, hours: list):
        """
        Create a 1m parquet file covering specific UK hours.

        Args:
            hours: list of (start_hour, end_hour) tuples to include.
        """
        parts = []
        for sh, eh in hours:
            start = TZ_UK.localize(
                datetime(uk_date.year, uk_date.month, uk_date.day, sh, 0)
            )
            end = TZ_UK.localize(
                datetime(uk_date.year, uk_date.month, uk_date.day, eh, 59)
            )
            idx = pd.date_range(start, end, freq="1min", tz=TZ_UK)
            idx = idx.tz_convert(TZ_UTC)
            parts.append(pd.DataFrame({"Close": range(len(idx))}, index=idx))

        df = pd.concat(parts).sort_index()
        df = df[~df.index.duplicated(keep="first")]
        df.index.name = "DateTime_UTC"
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, engine="pyarrow")
        return df

    def test_full_coverage_no_gap(self, tmp_path):
        """Full 24h coverage should detect no gap."""
        import logging
        logger = logging.getLogger("test")

        for i, d in enumerate([date(2026, 1, 20), date(2026, 1, 21),
                                date(2026, 1, 22)]):
            p = tmp_path / f"{d.isoformat()}.parquet"
            self._make_1m_file(p, d, [(0, 23)])

        result = detect_missing_window(tmp_path, scan_days=20,
                                        coverage_threshold=0.10, logger=logger)
        assert result["status"] == "detected"
        assert result["total_missing_minutes"] == 0
        assert result["largest_missing_block"] is None

    def test_gap_detected(self, tmp_path):
        """Files missing UK 03:00-06:59 should detect that gap."""
        import logging
        logger = logging.getLogger("test")

        for d in [date(2026, 1, 20), date(2026, 1, 21), date(2026, 1, 22)]:
            p = tmp_path / f"{d.isoformat()}.parquet"
            # Cover 00:00-02:59 and 07:00-23:59 (skip 03:00-06:59 = 240 min)
            self._make_1m_file(p, d, [(0, 2), (7, 23)])

        result = detect_missing_window(tmp_path, scan_days=20,
                                        coverage_threshold=0.10, logger=logger)
        assert result["status"] == "detected"
        assert result["total_missing_minutes"] == 240
        lb = result["largest_missing_block"]
        assert lb is not None
        assert lb["start_uk"] == "03:00"
        assert lb["end_uk"] == "07:00"
        assert lb["duration_min"] == 240

    def test_threshold_affects_detection(self, tmp_path):
        """Higher threshold should detect more as 'missing'."""
        import logging
        logger = logging.getLogger("test")

        # 3 files: 2 have full coverage, 1 missing 10:00-10:59
        for d in [date(2026, 1, 20), date(2026, 1, 21)]:
            p = tmp_path / f"{d.isoformat()}.parquet"
            self._make_1m_file(p, d, [(0, 23)])

        p = tmp_path / "2026-01-22.parquet"
        self._make_1m_file(p, date(2026, 1, 22), [(0, 9), (11, 23)])

        # At threshold 0.10: 10:00-10:59 has coverage 2/3=0.67 → NOT missing
        r1 = detect_missing_window(tmp_path, scan_days=20,
                                    coverage_threshold=0.10, logger=logger)
        assert r1["total_missing_minutes"] == 0

        # At threshold 0.80: 10:00-10:59 has coverage 0.67 → IS missing
        r2 = detect_missing_window(tmp_path, scan_days=20,
                                    coverage_threshold=0.80, logger=logger)
        assert r2["total_missing_minutes"] == 60

    def test_no_files(self, tmp_path):
        import logging
        logger = logging.getLogger("test")
        result = detect_missing_window(tmp_path, scan_days=20,
                                        coverage_threshold=0.10, logger=logger)
        assert result["status"] == "no_data"

    def test_skips_partial_files(self, tmp_path):
        """Files with <100 bars should be skipped."""
        import logging
        logger = logging.getLogger("test")

        # One full file, one tiny file
        p1 = tmp_path / "2026-01-20.parquet"
        self._make_1m_file(p1, date(2026, 1, 20), [(0, 23)])  # ~1440 bars

        # Create tiny file (10 bars only)
        idx = pd.date_range("2026-01-25 23:00", periods=10, freq="1min", tz="UTC")
        df = pd.DataFrame({"Close": range(10)}, index=idx)
        p2 = tmp_path / "2026-01-25.parquet"
        df.to_parquet(p2, engine="pyarrow")

        result = detect_missing_window(tmp_path, scan_days=20,
                                        coverage_threshold=0.10, logger=logger)
        assert result["files_scanned"] == 1  # Only the full file


# =====================================================================
# UK window → UTC conversion (DST-safe)
# =====================================================================

class TestUKWindowToUTC:
    """Test uk_window_to_utc() across DST."""

    def test_winter_no_offset(self):
        """In GMT, UK times == UTC times."""
        start, end = uk_window_to_utc(date(2026, 1, 15), "03:00", "07:00")
        assert start == "2026-01-15 03:00:00"
        assert end == "2026-01-15 07:00:59"

    def test_summer_bst_minus_1h(self):
        """In BST, UK times are UTC-1."""
        start, end = uk_window_to_utc(date(2026, 7, 1), "03:00", "07:00")
        # BST = UTC+1, so UK 03:00 = UTC 02:00
        assert start == "2026-07-01 02:00:00"
        assert end == "2026-07-01 06:00:59"

    def test_spring_forward_day(self):
        """29 Mar 2026: clocks go forward at 01:00."""
        # UK 03:00 is in BST → UTC 02:00
        start, end = uk_window_to_utc(date(2026, 3, 29), "03:00", "07:00")
        assert start == "2026-03-29 02:00:00"
        assert end == "2026-03-29 06:00:59"


# =====================================================================
# Parquet path and validation
# =====================================================================

class TestGapfillParquet:

    def test_path_naming(self):
        p = gapfill_parquet_path(Path("data/gf"), date(2026, 2, 15))
        assert p == Path("data/gf/2026-02-15.parquet")

    def test_valid_file(self, tmp_path):
        import logging
        logger = logging.getLogger("test")
        idx = pd.date_range("2026-02-15 03:00", periods=24, freq="5min", tz="UTC")
        df = pd.DataFrame({"Close": range(24)}, index=idx)
        path = tmp_path / "test.parquet"
        df.to_parquet(path, engine="pyarrow")
        assert is_valid_gapfill(path, logger) is True

    def test_empty_file_invalid(self, tmp_path):
        import logging
        logger = logging.getLogger("test")
        idx = pd.DatetimeIndex([], dtype="datetime64[ns, UTC]")
        df = pd.DataFrame({"Close": pd.Series(dtype=float)}, index=idx)
        path = tmp_path / "test.parquet"
        df.to_parquet(path, engine="pyarrow")
        assert is_valid_gapfill(path, logger) is False

    def test_missing_file_invalid(self, tmp_path):
        import logging
        logger = logging.getLogger("test")
        assert is_valid_gapfill(tmp_path / "nope.parquet", logger) is False


# =====================================================================
# Dates with 1m files
# =====================================================================

class TestDatesWith1mFiles:

    def test_returns_dates_from_parquet_names(self, tmp_path):
        # Create some dummy parquet files
        for d in ["2026-01-20", "2026-01-21", "2026-01-22"]:
            idx = pd.date_range(f"{d} 00:00", periods=5, freq="1min", tz="UTC")
            df = pd.DataFrame({"Close": range(5)}, index=idx)
            df.to_parquet(tmp_path / f"{d}.parquet")

        dates = get_dates_with_1m_files(tmp_path, max_days=60)
        assert len(dates) == 3
        assert dates[0] == date(2026, 1, 20)
        assert dates[-1] == date(2026, 1, 22)

    def test_max_days_limits(self, tmp_path):
        for d in ["2026-01-20", "2026-01-21", "2026-01-22", "2026-01-23"]:
            idx = pd.date_range(f"{d} 00:00", periods=5, freq="1min", tz="UTC")
            df = pd.DataFrame({"Close": range(5)}, index=idx)
            df.to_parquet(tmp_path / f"{d}.parquet")

        dates = get_dates_with_1m_files(tmp_path, max_days=2)
        assert len(dates) == 2
        # Should be most recent 2
        assert dates[0] == date(2026, 1, 22)
        assert dates[1] == date(2026, 1, 23)

    def test_ascending_order(self, tmp_path):
        for d in ["2026-02-01", "2026-01-15", "2026-02-10"]:
            idx = pd.date_range(f"{d} 00:00", periods=5, freq="1min", tz="UTC")
            df = pd.DataFrame({"Close": range(5)}, index=idx)
            df.to_parquet(tmp_path / f"{d}.parquet")

        dates = get_dates_with_1m_files(tmp_path, max_days=60)
        assert dates == sorted(dates)


# =====================================================================
# Credentials env selection
# =====================================================================

class TestCredentialEnv:

    def test_live_env(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        creds = {
            "demo": {"api_key": "demo_key", "username": "demo_user", "password": "demo_pw"},
            "live": {"api_key": "live_key", "username": "live_user", "password": "live_pw",
                     "acc_type": "LIVE"},
        }
        (config_dir / "ig_api_credentials.json").write_text(json.dumps(creds))

        result = load_credentials("live")
        assert result["api_key"] == "live_key"
        assert result["acc_type"] == "LIVE"

    def test_demo_env(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        creds = {
            "demo": {"api_key": "demo_key", "username": "demo_user", "password": "demo_pw"},
            "live": {"api_key": "live_key", "username": "live_user", "password": "live_pw"},
        }
        (config_dir / "ig_api_credentials.json").write_text(json.dumps(creds))

        result = load_credentials("demo")
        assert result["api_key"] == "demo_key"

    def test_missing_env_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        creds = {"demo": {"api_key": "x", "username": "y", "password": "z"}}
        (config_dir / "ig_api_credentials.json").write_text(json.dumps(creds))

        result = load_credentials("live")
        assert result is None

    def test_placeholder_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        creds = {"live": {"api_key": "YOUR_API_KEY", "username": "YOUR_USER",
                          "password": "pw"}}
        (config_dir / "ig_api_credentials.json").write_text(json.dumps(creds))

        result = load_credentials("live")
        assert result is None


# =====================================================================
# AllowanceExhaustedError
# =====================================================================

class TestAllowanceError:

    def test_is_exception(self):
        with pytest.raises(AllowanceExhaustedError):
            raise AllowanceExhaustedError("exceeded")

    def test_message(self):
        try:
            raise AllowanceExhaustedError("weekly cap hit")
        except AllowanceExhaustedError as e:
            assert "weekly cap hit" in str(e)
