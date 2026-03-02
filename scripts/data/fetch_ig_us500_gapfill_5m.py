"""
Gap-fill missing intraday windows in IG US 500 1-minute data using 5-minute candles.

Phase A: Detect
  Scans existing 1m parquet files in data/IG_US500_1m/, computes per-minute-of-day
  coverage ratios, and identifies the largest contiguous missing window.

Phase B: Fetch
  For each UK date that already has a 1m file, fetches 5-minute candles covering
  ONLY the detected missing window from IG's historical prices API.

Why 5m?
  IG limits: 1m = 40 days retention, 5m = 360 days retention,
  10,000 historical datapoints per week.  5m uses 5x fewer points and has
  9x longer retention.

Allowance-aware:
  Reads remainingAllowance / allowanceExpiry from each IG API response.
  Exits cleanly when allowance runs low — no pointless retries on weekly caps.

Usage:
    python scripts/data/fetch_ig_us500_gapfill_5m.py --days 60 --scan-days 20
    python scripts/data/fetch_ig_us500_gapfill_5m.py --days 60 --detect-only
    python scripts/data/fetch_ig_us500_gapfill_5m.py --days 60 --force --env live
"""

import sys
import os
import json
import time
import random
import logging
import argparse
import tempfile
import shutil
from datetime import datetime, timedelta, timezone, date as date_type
from pathlib import Path

import numpy as np
import pandas as pd
import pytz

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TZ_UK = pytz.timezone("Europe/London")
TZ_ET = pytz.timezone("US/Eastern")
TZ_UTC = pytz.utc

DEFAULT_EPIC = "IX.D.SPTRD.DAILY.IP"
DEFAULT_1M_DIR = Path("data/IG_US500_1m")
DEFAULT_OUTDIR = Path("data/IG_US500_gapfill_5m")
STATE_FILE = Path("logs/ig_us500_gapfill_state.json")
LOG_FILE = Path("logs/ig_us500_gapfill_sync.log")
DETECTED_WINDOW_FILE = Path("logs/ig_us500_gapfill_detected_window.json")

RESOLUTION = "5Min"  # Pandas frequency -> IG "MINUTE_5"
IG_DATE_FMT_V2 = "%Y-%m-%d %H:%M:%S"

# Safety buffer: stop fetching if remainingAllowance drops below this
ALLOWANCE_SAFETY_BUFFER = 200


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(log_path: Path) -> logging.Logger:
    """Configure file + console logging."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ig_gapfill_5m")
    logger.setLevel(logging.DEBUG)

    # Avoid duplicate handlers on re-import
    if not logger.handlers:
        fh = logging.FileHandler(str(log_path), mode="a", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(fh)

        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(ch)

    return logger


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def load_state(path: Path) -> dict:
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {
        "last_completed_date": None,
        "remaining_dates": [],
        "last_error": None,
        "cooldown_until_utc": None,
        "total_datapoints_this_session": 0,
        "last_remaining_allowance": None,
        "last_allowance_expiry_secs": None,
    }


def save_state(path: Path, state: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2, default=str)
        shutil.move(str(tmp), str(path))
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


# ---------------------------------------------------------------------------
# Rate limiter (conservative, from 1m fetcher pattern)
# ---------------------------------------------------------------------------

class IGRateLimiter:
    def __init__(self, max_rpm: int = 4, logger: logging.Logger = None):
        self.max_rpm = max_rpm
        self.log = logger or logging.getLogger("ig_gapfill_5m")
        self.request_times: list = []
        self.requests_this_run: int = 0

    def wait_for_slot(self):
        now = time.time()
        self.request_times = [t for t in self.request_times if now - t < 60]
        if len(self.request_times) >= self.max_rpm:
            oldest = min(self.request_times)
            wait = 60.0 - (now - oldest)
            if wait > 0:
                jitter = random.uniform(0.5, 2.0)
                total_wait = wait + jitter
                self.log.debug(f"Rate limiter: sleeping {total_wait:.1f}s")
                time.sleep(total_wait)

    def record_request(self):
        self.request_times.append(time.time())
        self.requests_this_run += 1


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def load_credentials(env: str = "live") -> dict:
    """
    Load IG API credentials from config/ig_api_credentials.json.

    Args:
        env: 'live' or 'demo'
    """
    cred_path = Path("config/ig_api_credentials.json")
    if not cred_path.exists():
        return None
    try:
        with open(cred_path) as f:
            creds = json.load(f)
    except (json.JSONDecodeError, IOError):
        return None

    section = creds.get(env, {})
    if not section.get("api_key") or not section.get("username") or not section.get("password"):
        return None
    if "YOUR_" in section.get("api_key", "") or "YOUR_" in section.get("username", ""):
        return None

    return section


def connect_ig(creds: dict, logger: logging.Logger):
    from trading_ig import IGService
    ig = IGService(
        username=creds["username"],
        password=creds["password"],
        api_key=creds["api_key"],
        acc_type=creds.get("acc_type", "DEMO"),
        acc_number=creds.get("acc_number"),
    )
    ig.create_session()
    logger.info("Connected to IG API")
    return ig


def safe_logout(ig, logger: logging.Logger):
    try:
        ig.logout()
        logger.info("Disconnected from IG API")
    except Exception:
        logger.debug("Logout raised (ignored)")


# ---------------------------------------------------------------------------
# Phase A: Detect missing window
# ---------------------------------------------------------------------------

def detect_missing_window(
    in_1m_dir: Path,
    scan_days: int,
    coverage_threshold: float,
    logger: logging.Logger,
) -> dict:
    """
    Scan existing 1m parquet files and detect the largest missing time window.

    Algorithm:
      1. Load up to scan_days most recent full-day 1m files (>100 bars)
      2. For each file, convert timestamps to UK time and mark covered minutes
      3. Compute per-minute coverage ratio across all scanned days
      4. Define "missing" as coverage < threshold
      5. Find largest contiguous block of missing minutes

    Returns dict with detection results.
    """
    MIN_BARS_FULL_DAY = 100

    files = sorted(in_1m_dir.glob("*.parquet"))
    if not files:
        logger.warning("No 1m parquet files found for gap detection.")
        return {"status": "no_data", "files_scanned": 0}

    # Select most recent full-day files
    full_day_files = []
    for f in reversed(files):
        try:
            df = pd.read_parquet(f)
            if len(df) >= MIN_BARS_FULL_DAY:
                full_day_files.append(f)
                if len(full_day_files) >= scan_days:
                    break
        except Exception:
            continue

    full_day_files = list(reversed(full_day_files))  # back to ascending order

    if not full_day_files:
        logger.warning("No full-day 1m files found (all below 100 bars).")
        return {"status": "no_full_day_files", "files_scanned": 0}

    logger.info(f"Scanning {len(full_day_files)} full-day 1m files for gap detection...")

    # Build per-minute coverage array (1440 minutes in a day)
    n_files = len(full_day_files)
    coverage_count = np.zeros(1440, dtype=int)

    for f in full_day_files:
        df = pd.read_parquet(f)
        uk_times = df.index.tz_convert(TZ_UK)
        minutes_of_day = uk_times.hour * 60 + uk_times.minute
        unique_minutes = np.unique(minutes_of_day)
        coverage_count[unique_minutes] += 1

    coverage_ratio = coverage_count / n_files

    # Find missing minutes (below threshold)
    missing_mask = coverage_ratio < coverage_threshold

    # Find contiguous blocks of missing minutes
    diffs = np.diff(missing_mask.astype(int))
    starts = np.where(diffs == 1)[0] + 1
    ends = np.where(diffs == -1)[0] + 1
    if missing_mask[0]:
        starts = np.insert(starts, 0, 0)
    if missing_mask[-1]:
        ends = np.append(ends, 1440)

    missing_blocks = []
    for s, e in zip(starts, ends):
        sh, sm = divmod(int(s), 60)
        eh, em = divmod(int(e), 60)
        missing_blocks.append({
            "start_uk": f"{sh:02d}:{sm:02d}",
            "end_uk": f"{eh:02d}:{em:02d}",
            "duration_min": int(e - s),
        })

    # Find largest block
    if missing_blocks:
        largest = max(missing_blocks, key=lambda b: b["duration_min"])
        # Convert to ET for reference
        # Use a representative winter date for UK→ET conversion
        ref_date = datetime(2026, 1, 15)  # mid-winter
        start_uk_dt = TZ_UK.localize(datetime(
            ref_date.year, ref_date.month, ref_date.day,
            int(largest["start_uk"][:2]), int(largest["start_uk"][3:])
        ))
        end_uk_dt = TZ_UK.localize(datetime(
            ref_date.year, ref_date.month, ref_date.day,
            int(largest["end_uk"][:2]), int(largest["end_uk"][3:])
        ))
        start_et = start_uk_dt.astimezone(TZ_ET)
        end_et = end_uk_dt.astimezone(TZ_ET)

        largest["start_et"] = start_et.strftime("%H:%M")
        largest["end_et"] = end_et.strftime("%H:%M")
    else:
        largest = None

    # Coverage summary by hour
    hourly_coverage = {}
    for h in range(24):
        hr_ratios = coverage_ratio[h * 60:(h + 1) * 60]
        hourly_coverage[f"{h:02d}"] = {
            "avg": round(float(hr_ratios.mean()), 3),
            "min": round(float(hr_ratios.min()), 3),
            "mins_covered": int((hr_ratios >= coverage_threshold).sum()),
        }

    result = {
        "status": "detected",
        "files_scanned": n_files,
        "file_dates": [f.stem for f in full_day_files],
        "coverage_threshold": coverage_threshold,
        "total_missing_minutes": int(missing_mask.sum()),
        "missing_blocks": missing_blocks,
        "largest_missing_block": largest,
        "missing_window_uk_start": largest["start_uk"] if largest else None,
        "missing_window_uk_end": largest["end_uk"] if largest else None,
        "missing_window_et_start": largest["start_et"] if largest else None,
        "missing_window_et_end": largest["end_et"] if largest else None,
        "hourly_coverage": hourly_coverage,
    }

    return result


def print_detection_report(result: dict, logger: logging.Logger):
    """Pretty-print the detection results."""
    logger.info("")
    logger.info("=" * 60)
    logger.info("GAP DETECTION REPORT")
    logger.info("=" * 60)
    logger.info(f"  Files scanned:       {result['files_scanned']}")
    logger.info(f"  Coverage threshold:  {result['coverage_threshold']}")
    logger.info(f"  Total missing mins:  {result['total_missing_minutes']}")
    logger.info(f"  Missing blocks:      {len(result['missing_blocks'])}")

    if result["missing_blocks"]:
        logger.info("")
        logger.info("  Missing blocks:")
        for b in result["missing_blocks"]:
            logger.info(f"    UK {b['start_uk']} - {b['end_uk']} "
                         f"({b['duration_min']} min)")

    if result["largest_missing_block"]:
        lb = result["largest_missing_block"]
        logger.info("")
        logger.info(f"  >>> Largest gap: UK {lb['start_uk']} - {lb['end_uk']} "
                     f"({lb['duration_min']} min)")
        logger.info(f"      ET reference: {lb['start_et']} - {lb['end_et']} "
                     "(winter)")
    else:
        logger.info("")
        logger.info("  No missing window detected at this threshold.")
        logger.info("  1m data appears complete.")

    # Print hourly summary
    logger.info("")
    logger.info("  Hourly coverage (UK time):")
    for h in range(24):
        hc = result["hourly_coverage"][f"{h:02d}"]
        bar = "#" * int(hc["avg"] * 20)
        logger.info(f"    {h:02d}:00  {bar:<20s}  "
                     f"avg={hc['avg']:.2f}  min={hc['min']:.2f}  "
                     f"covered={hc['mins_covered']}/60")
    logger.info("")


# ---------------------------------------------------------------------------
# Parquet I/O
# ---------------------------------------------------------------------------

def gapfill_parquet_path(outdir: Path, uk_date: date_type) -> Path:
    return outdir / f"{uk_date.isoformat()}.parquet"


def is_valid_gapfill(path: Path, logger: logging.Logger) -> bool:
    """Check if a gapfill parquet file is structurally valid."""
    if not path.exists():
        return False
    try:
        df = pd.read_parquet(path)
    except Exception as e:
        logger.warning(f"  Corrupt parquet {path.name}: {e}")
        return False
    if len(df) == 0:
        return False
    if not isinstance(df.index, pd.DatetimeIndex):
        return False
    if df.index.tz is None:
        return False
    if not df.index.is_monotonic_increasing:
        return False
    return True


def write_parquet_atomic(df: pd.DataFrame, path: Path, logger: logging.Logger):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(suffix=".parquet", dir=str(path.parent))
    os.close(fd)
    try:
        df.to_parquet(tmp_path, engine="pyarrow")
        shutil.move(tmp_path, str(path))
        logger.debug(f"  Written {path.name} ({len(df)} bars)")
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


# ---------------------------------------------------------------------------
# Phase B: Fetch 5m candles for the missing window
# ---------------------------------------------------------------------------

def uk_window_to_utc(uk_date: date_type, start_hhmm: str, end_hhmm: str) -> tuple:
    """
    Convert a UK date + HH:MM window to UTC datetime strings for IG API.

    Returns (start_utc_str, end_utc_str) in IG v2 format.
    """
    sh, sm = int(start_hhmm[:2]), int(start_hhmm[3:])
    eh, em = int(end_hhmm[:2]), int(end_hhmm[3:])

    uk_start = TZ_UK.localize(datetime(uk_date.year, uk_date.month, uk_date.day, sh, sm, 0))
    uk_end = TZ_UK.localize(datetime(uk_date.year, uk_date.month, uk_date.day, eh, em, 59))

    utc_start = uk_start.astimezone(TZ_UTC)
    utc_end = uk_end.astimezone(TZ_UTC)

    return (
        utc_start.strftime(IG_DATE_FMT_V2),
        utc_end.strftime(IG_DATE_FMT_V2),
    )


def fetch_gapfill_candles(
    ig, epic: str, uk_date: date_type,
    start_hhmm: str, end_hhmm: str,
    limiter: IGRateLimiter, logger: logging.Logger,
) -> tuple:
    """
    Fetch 5-minute candles for a specific UK time window on a given date.

    Returns:
        (df, allowance_info) where:
          df: DataFrame with UTC-indexed 5m OHLCV (or empty if no data)
          allowance_info: dict with remainingAllowance and allowanceExpiry (or {})
    """
    start_str, end_str = uk_window_to_utc(uk_date, start_hhmm, end_hhmm)

    limiter.wait_for_slot()

    try:
        logger.debug(f"  API call: {epic} {start_str} -> {end_str}")
        result = ig.fetch_historical_prices_by_epic_and_date_range(
            epic=epic,
            resolution=RESOLUTION,
            start_date=start_str,
            end_date=end_str,
            format=ig.mid_prices,
            version="2",
        )
        limiter.record_request()

        # Extract allowance info
        allowance_info = {}
        if isinstance(result, dict):
            allowance_raw = result.get("allowance", {})
            if allowance_raw:
                allowance_info = {
                    "remainingAllowance": allowance_raw.get("remainingAllowance"),
                    "allowanceExpiry": allowance_raw.get("allowanceExpiry"),
                }

        # Extract price DataFrame
        if result is None:
            return pd.DataFrame(), allowance_info

        if isinstance(result, dict):
            df = result.get("prices", pd.DataFrame())
        else:
            df = result if isinstance(result, pd.DataFrame) else pd.DataFrame()

        if df is None or (isinstance(df, pd.DataFrame) and df.empty):
            return pd.DataFrame(), allowance_info

        # Normalize to UTC
        if df.index.tz is None:
            df.index = pd.to_datetime(df.index).tz_localize(TZ_UTC)
        else:
            df.index = df.index.tz_convert(TZ_UTC)
        df.index.name = "DateTime_UTC"

        # Rename to standard OHLCV
        col_map = {}
        for col in df.columns:
            cl = col.lower()
            if "open" in cl:
                col_map[col] = "Open"
            elif "high" in cl:
                col_map[col] = "High"
            elif "low" in cl:
                col_map[col] = "Low"
            elif "close" in cl:
                col_map[col] = "Close"
            elif "volume" in cl or "vol" in cl:
                col_map[col] = "Volume"
        if col_map:
            df = df.rename(columns=col_map)

        keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        df = df[keep]
        df = df.dropna(how="all")
        df = df.sort_index()
        df = df[~df.index.duplicated(keep="first")]

        return df, allowance_info

    except Exception as e:
        limiter.record_request()
        err = str(e).lower()
        logger.warning(f"  Error fetching {uk_date}: {e}")

        # Allowance exceeded — DO NOT retry, exit cleanly
        if "exceeded" in err and "allowance" in err:
            raise AllowanceExhaustedError(str(e))

        # No historical data — normal for weekends/holidays
        if "historical price data not found" in err:
            logger.debug(f"  No historical data for {uk_date}")
            return pd.DataFrame(), {}

        # Other errors — just return empty, don't retry for now
        return pd.DataFrame(), {}


class AllowanceExhaustedError(Exception):
    """Raised when IG weekly historical data allowance is exhausted."""
    pass


def get_dates_with_1m_files(in_1m_dir: Path, max_days: int) -> list:
    """
    Return UK dates (as date objects) for which we have 1m parquet files,
    most recent first, up to max_days.
    """
    files = sorted(in_1m_dir.glob("*.parquet"), reverse=True)
    dates = []
    for f in files:
        try:
            d = date_type.fromisoformat(f.stem)
            dates.append(d)
            if len(dates) >= max_days:
                break
        except ValueError:
            continue
    return list(reversed(dates))  # ascending order


def sync_gapfill(
    epic: str,
    in_1m_dir: Path,
    outdir: Path,
    detection: dict,
    days: int,
    force: bool,
    max_rpm: int,
    env: str,
    logger: logging.Logger,
) -> dict:
    """
    Main gapfill sync: fetch 5m candles for the detected missing window.

    Returns summary dict.
    """
    state = load_state(STATE_FILE)

    # Check cooldown
    if state.get("cooldown_until_utc"):
        try:
            cooldown_until = datetime.fromisoformat(state["cooldown_until_utc"])
            now = datetime.now(timezone.utc)
            if now < cooldown_until:
                remaining = (cooldown_until - now).total_seconds()
                logger.info(f"Cooldown active until {cooldown_until.isoformat()} "
                            f"({remaining:.0f}s remaining). Exiting.")
                return {"status": "cooldown", "files_created": 0}
        except (ValueError, TypeError):
            pass
        # Cooldown expired — clear it
        state["cooldown_until_utc"] = None
        save_state(STATE_FILE, state)

    # Get window
    window_start = detection.get("missing_window_uk_start")
    window_end = detection.get("missing_window_uk_end")
    if not window_start or not window_end:
        logger.info("No missing window detected — nothing to fetch.")
        return {"status": "no_gap", "files_created": 0}

    logger.info(f"Gapfill window: UK {window_start} - {window_end}")

    # Estimate expected bars per day (for allowance budgeting)
    sh, sm = int(window_start[:2]), int(window_start[3:])
    eh, em = int(window_end[:2]), int(window_end[3:])
    window_minutes = (eh * 60 + em) - (sh * 60 + sm)
    expected_bars_per_day = max(window_minutes // 5, 1)
    logger.info(f"  Window: {window_minutes} min -> ~{expected_bars_per_day} bars/day at 5m")

    # Get dates to process
    dates = get_dates_with_1m_files(in_1m_dir, days)
    logger.info(f"  Dates with 1m files: {len(dates)}")

    outdir.mkdir(parents=True, exist_ok=True)

    # Load credentials
    creds = load_credentials(env)
    if creds is None:
        logger.info(f"IG {env} credentials not found or incomplete. "
                     "Set up config/ig_api_credentials.json.")
        return {"status": "no_creds", "files_created": 0}

    # Connect
    try:
        ig = connect_ig(creds, logger)
    except Exception as e:
        logger.error(f"Failed to connect to IG API ({env}): {e}")
        state["last_error"] = f"Connection failed: {e}"
        save_state(STATE_FILE, state)
        return {"status": "connect_error", "files_created": 0}

    limiter = IGRateLimiter(max_rpm=max_rpm, logger=logger)

    n_created = 0
    n_skipped = 0
    n_no_data = 0
    n_remaining = 0
    remaining_dates = []
    datapoints_consumed = 0
    last_allowance = None

    try:
        for uk_date in dates:
            ppath = gapfill_parquet_path(outdir, uk_date)

            # Skip if already cached
            if not force and is_valid_gapfill(ppath, logger):
                n_skipped += 1
                continue

            logger.info(f"Fetching {uk_date} (5m, UK {window_start}-{window_end}) ...")

            try:
                df, allowance_info = fetch_gapfill_candles(
                    ig, epic, uk_date, window_start, window_end,
                    limiter, logger,
                )
            except AllowanceExhaustedError as e:
                logger.warning(f"Allowance exhausted: {e}")
                # Record remaining dates
                remaining_dates = [
                    d.isoformat() for d in dates
                    if d >= uk_date and not (
                        not force and is_valid_gapfill(
                            gapfill_parquet_path(outdir, d), logger
                        )
                    )
                ]
                n_remaining = len(remaining_dates)

                # Set cooldown: use allowanceExpiry if available, else 7 days
                expiry_secs = None
                if allowance_info and allowance_info.get("allowanceExpiry"):
                    expiry_secs = allowance_info["allowanceExpiry"]

                if expiry_secs and expiry_secs > 0:
                    cooldown_until = datetime.now(timezone.utc) + timedelta(
                        seconds=expiry_secs
                    )
                else:
                    cooldown_until = datetime.now(timezone.utc) + timedelta(days=7)

                state["cooldown_until_utc"] = cooldown_until.isoformat()
                state["last_error"] = str(e)
                state["remaining_dates"] = remaining_dates
                save_state(STATE_FILE, state)
                logger.info(f"Cooldown set until {cooldown_until.isoformat()}. "
                            f"{n_remaining} dates remaining.")
                break

            # Track allowance
            if allowance_info:
                ra = allowance_info.get("remainingAllowance")
                if ra is not None:
                    last_allowance = ra
                    state["last_remaining_allowance"] = ra
                    state["last_allowance_expiry_secs"] = allowance_info.get(
                        "allowanceExpiry"
                    )

            if df.empty:
                n_no_data += 1
                logger.debug(f"  {uk_date}: no 5m data in window")
                continue

            bars_count = len(df)
            datapoints_consumed += bars_count

            # Write atomically
            write_parquet_atomic(df, ppath, logger)
            n_created += 1

            # Update state
            state["last_completed_date"] = uk_date.isoformat()
            state["last_error"] = None
            state["total_datapoints_this_session"] = datapoints_consumed
            save_state(STATE_FILE, state)

            logger.info(f"  {uk_date}: {bars_count} bars -> {ppath.name}"
                        f"  [allowance: {last_allowance}]")

            # Check if allowance is getting low
            if last_allowance is not None:
                needed_next = expected_bars_per_day + ALLOWANCE_SAFETY_BUFFER
                if last_allowance < needed_next:
                    # Record remaining
                    remaining_dates = [
                        d.isoformat() for d in dates
                        if d > uk_date and not (
                            not force and is_valid_gapfill(
                                gapfill_parquet_path(outdir, d), logger
                            )
                        )
                    ]
                    n_remaining = len(remaining_dates)

                    expiry_secs = state.get("last_allowance_expiry_secs")
                    if expiry_secs and expiry_secs > 0:
                        cooldown_until = datetime.now(timezone.utc) + timedelta(
                            seconds=expiry_secs
                        )
                    else:
                        cooldown_until = datetime.now(timezone.utc) + timedelta(days=7)

                    state["cooldown_until_utc"] = cooldown_until.isoformat()
                    state["remaining_dates"] = remaining_dates
                    save_state(STATE_FILE, state)
                    logger.info(
                        f"  Allowance low ({last_allowance} remaining, "
                        f"need ~{needed_next}). Stopping. "
                        f"{n_remaining} dates remaining."
                    )
                    break

    finally:
        safe_logout(ig, logger)
        state["remaining_dates"] = remaining_dates
        save_state(STATE_FILE, state)

    return {
        "status": "completed" if n_remaining == 0 else "partial",
        "files_created": n_created,
        "files_skipped": n_skipped,
        "no_data_days": n_no_data,
        "datapoints_consumed": datapoints_consumed,
        "remaining_dates_count": n_remaining,
        "last_remaining_allowance": last_allowance,
    }


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_gapfill(outdir: Path, logger: logging.Logger):
    """Print summary of gapfill cached data."""
    files = sorted(outdir.glob("*.parquet"))
    if not files:
        logger.info("No gapfill parquet files found.")
        return

    logger.info(f"\n{'='*60}")
    logger.info(f"GAPFILL VERIFICATION: {outdir}")
    logger.info(f"{'='*60}")
    logger.info(f"  Day files: {len(files)}")

    df_first = pd.read_parquet(files[0])
    df_last = pd.read_parquet(files[-1])
    logger.info(f"  Earliest: {df_first.index.min()}")
    logger.info(f"  Latest:   {df_last.index.max()}")

    bar_counts = []
    for f in files:
        try:
            df = pd.read_parquet(f)
            bar_counts.append(len(df))
        except Exception:
            pass

    if bar_counts:
        logger.info(f"  Bars/day: min={min(bar_counts)}, max={max(bar_counts)}, "
                     f"median={sorted(bar_counts)[len(bar_counts)//2]}")

    logger.info("")


# ---------------------------------------------------------------------------
# EPIC resolution
# ---------------------------------------------------------------------------

def resolve_epic(args_epic: str) -> str:
    if args_epic:
        return args_epic
    config_path = Path("config/config.json")
    if config_path.exists():
        try:
            with open(config_path) as f:
                config = json.load(f)
            epic = config.get("ig_api", {}).get("underlying_epic")
            if epic:
                return epic
        except (json.JSONDecodeError, IOError):
            pass
    epic = os.environ.get("IG_US500_EPIC")
    if epic:
        return epic
    return DEFAULT_EPIC


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Gap-fill missing IG US 500 intraday windows with 5m candles",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--days", type=int, default=60,
        help="Number of 1m day files to process (default: 60)",
    )
    parser.add_argument(
        "--scan-days", type=int, default=20,
        help="Number of recent full-day files to scan for gap detection (default: 20)",
    )
    parser.add_argument(
        "--coverage-threshold", type=float, default=0.10,
        help="Coverage ratio below which a minute is 'missing' (default: 0.10)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Refetch even if gapfill file already exists",
    )
    parser.add_argument(
        "--max-rpm", type=int, default=4,
        help="Max requests per minute (default: 4)",
    )
    parser.add_argument(
        "--env", choices=["demo", "live"], default="live",
        help="IG account environment (default: live)",
    )
    parser.add_argument(
        "--epic", type=str, default=None,
        help=f"IG instrument EPIC (default: from config or {DEFAULT_EPIC})",
    )
    parser.add_argument(
        "--in-1m-dir", type=str, default=str(DEFAULT_1M_DIR),
        help=f"Directory with existing 1m parquet files (default: {DEFAULT_1M_DIR})",
    )
    parser.add_argument(
        "--outdir", type=str, default=str(DEFAULT_OUTDIR),
        help=f"Output directory for 5m gapfill files (default: {DEFAULT_OUTDIR})",
    )
    parser.add_argument(
        "--detect-only", action="store_true",
        help="Only detect the missing window, do not fetch",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    logger = setup_logging(LOG_FILE)

    epic = resolve_epic(args.epic)
    in_1m_dir = Path(args.in_1m_dir)
    outdir = Path(args.outdir)

    logger.info("=" * 60)
    logger.info("IG US 500 — 5-minute gap-fill fetcher")
    logger.info(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info("=" * 60)
    logger.info(f"  EPIC:         {epic}")
    logger.info(f"  Env:          {args.env}")
    logger.info(f"  Days:         {args.days}")
    logger.info(f"  Scan days:    {args.scan_days}")
    logger.info(f"  Threshold:    {args.coverage_threshold}")
    logger.info(f"  1m dir:       {in_1m_dir}")
    logger.info(f"  Output dir:   {outdir}")
    logger.info(f"  Force:        {args.force}")
    logger.info(f"  Max RPM:      {args.max_rpm}")
    logger.info(f"  Detect only:  {args.detect_only}")
    logger.info("")

    # Phase A: Detect
    detection = detect_missing_window(
        in_1m_dir=in_1m_dir,
        scan_days=args.scan_days,
        coverage_threshold=args.coverage_threshold,
        logger=logger,
    )

    print_detection_report(detection, logger)

    # Save detection result
    DETECTED_WINDOW_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DETECTED_WINDOW_FILE, "w") as f:
        json.dump(detection, f, indent=2)
    logger.info(f"Detection saved to {DETECTED_WINDOW_FILE}")

    if args.detect_only:
        logger.info("Detect-only mode — exiting.")
        return

    if detection.get("status") != "detected" or not detection.get("largest_missing_block"):
        logger.info("No missing window detected — nothing to fetch.")
        return

    # Phase B: Fetch
    summary = sync_gapfill(
        epic=epic,
        in_1m_dir=in_1m_dir,
        outdir=outdir,
        detection=detection,
        days=args.days,
        force=args.force,
        max_rpm=args.max_rpm,
        env=args.env,
        logger=logger,
    )

    logger.info("")
    logger.info("=" * 60)
    logger.info("GAPFILL SUMMARY")
    logger.info("=" * 60)
    logger.info(f"  Status:             {summary['status']}")
    logger.info(f"  Files created:      {summary.get('files_created', 0)}")
    logger.info(f"  Files skipped:      {summary.get('files_skipped', 0)}")
    logger.info(f"  No data days:       {summary.get('no_data_days', 0)}")
    logger.info(f"  Datapoints used:    {summary.get('datapoints_consumed', 0)}")
    logger.info(f"  Remaining:          {summary.get('remaining_dates_count', 0)} dates")
    logger.info(f"  Last allowance:     {summary.get('last_remaining_allowance', 'N/A')}")
    logger.info("")

    verify_gapfill(outdir, logger)


if __name__ == "__main__":
    main()
