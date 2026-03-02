"""
Fetch IG US 500 5-minute candles for the SPY-missing overnight window.

SPY has no bars roughly 20:00–04:00 ET (≈ 01:00–09:00 UK winter).
This script fetches IG US500 CFD 5-minute candles for exactly that window,
caches per UK date, and is designed for unattended runs.

Allowance-aware:
  - Reads remainingAllowance from each IG API response.
  - Exits cleanly on weekly cap (no pointless retries).
  - Persists resume state.

Usage:
    python scripts/data/fetch_ig_us500_spy_gap_5m.py --days 10 --dry-run
    python scripts/data/fetch_ig_us500_spy_gap_5m.py --days 60 --env live
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

import pandas as pd
import pytz

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TZ_UK = pytz.timezone("Europe/London")
TZ_ET = pytz.timezone("US/Eastern")
TZ_UTC = pytz.utc

DEFAULT_EPIC = "IX.D.SPTRD.DAILY.IP"
DEFAULT_OUTDIR = Path("data/IG_US500_spy_gap_5m")
STATE_FILE = Path("logs/ig_us500_spy_gap_5m_state.json")
LOG_FILE = Path("logs/ig_us500_spy_gap_5m_sync.log")

RESOLUTION = "5Min"  # -> IG "MINUTE_5"
IG_DATE_FMT_V2 = "%Y-%m-%d %H:%M:%S"

# Default SPY-missing window in UK time
DEFAULT_WINDOW_UK = "21:00-09:00"

# Allowance safety: stop if remaining < this
ALLOWANCE_SAFETY_BUFFER = 300


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ig_spy_gap_5m")
    logger.setLevel(logging.DEBUG)
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
# State
# ---------------------------------------------------------------------------

def load_state(path: Path) -> dict:
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {
        "last_completed_uk_date": None,
        "pending_uk_dates": [],
        "last_error": None,
        "cooldown_until_utc": None,
        "total_datapoints_this_session": 0,
        "last_remaining_allowance": None,
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
# Rate limiter
# ---------------------------------------------------------------------------

class IGRateLimiter:
    def __init__(self, max_rpm: int = 4, logger: logging.Logger = None):
        self.max_rpm = max_rpm
        self.log = logger or logging.getLogger("ig_spy_gap_5m")
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
    logger.info(f"Connected to IG API ({creds.get('acc_type', 'DEMO')})")
    return ig


def safe_logout(ig, logger: logging.Logger):
    try:
        ig.logout()
        logger.info("Disconnected from IG API")
    except Exception:
        logger.debug("Logout raised (ignored)")


# ---------------------------------------------------------------------------
# Date selection
# ---------------------------------------------------------------------------

def get_uk_dates_for_spy_trading_days(days: int) -> list:
    """
    Return UK dates corresponding to SPY trading days in the backtest window.

    For each SPY trading day (Mon-Fri, excluding obvious holidays), the
    overnight window starts the EVENING BEFORE on the UK calendar.
    e.g. SPY trades on Tue → overnight window is Mon 21:00 UK to Tue 09:00 UK.

    For simplicity, we generate weekday UK dates that precede a SPY trading day
    (Mon evening through Thu evening cover Tue-Fri exits; Sun evening covers Mon).

    We return UK dates for which we want the gap data.
    These are the dates when the overnight SESSION starts.
    """
    now_uk = datetime.now(TZ_UK)
    end_date = (now_uk - timedelta(days=1)).date()
    start_date = (now_uk - timedelta(days=days)).date()

    dates = []
    d = start_date
    while d <= end_date:
        # Include all dates — weekends will return no data from IG (expected)
        # The caller's skip-cached logic handles efficiency
        dates.append(d)
        d += timedelta(days=1)
    return dates


def parse_window(window_str: str) -> tuple:
    """
    Parse window string like "21:00-09:00" into (start_hh, start_mm, end_hh, end_mm).
    Supports overnight windows where start > end (crosses midnight).
    """
    parts = window_str.split("-")
    if len(parts) != 2:
        raise ValueError(f"Invalid window format: {window_str}, expected HH:MM-HH:MM")
    sh, sm = map(int, parts[0].strip().split(":"))
    eh, em = map(int, parts[1].strip().split(":"))
    return sh, sm, eh, em


def uk_window_to_utc_range(uk_date: date_type, sh: int, sm: int,
                            eh: int, em: int) -> tuple:
    """
    Convert a UK date + HH:MM window to UTC strings for IG API.

    Handles overnight windows (e.g. 21:00-09:00 spans midnight):
      - Start = uk_date at sh:sm
      - End = uk_date at eh:em (same day if end > start, next day if end < start)

    Returns (start_utc_str, end_utc_str).
    """
    uk_start = TZ_UK.localize(
        datetime(uk_date.year, uk_date.month, uk_date.day, sh, sm, 0)
    )

    # If end time is before start time, it crosses midnight → end is next day
    if (eh * 60 + em) <= (sh * 60 + sm):
        end_date = uk_date + timedelta(days=1)
    else:
        end_date = uk_date

    uk_end = TZ_UK.localize(
        datetime(end_date.year, end_date.month, end_date.day, eh, em, 0)
    )

    utc_start = uk_start.astimezone(TZ_UTC)
    utc_end = uk_end.astimezone(TZ_UTC)

    return (
        utc_start.strftime(IG_DATE_FMT_V2),
        utc_end.strftime(IG_DATE_FMT_V2),
    )


# ---------------------------------------------------------------------------
# Parquet I/O
# ---------------------------------------------------------------------------

def gap_parquet_path(outdir: Path, uk_date: date_type) -> Path:
    return outdir / f"{uk_date.isoformat()}.parquet"


def is_valid_cached(path: Path, logger: logging.Logger) -> bool:
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
# Fetch
# ---------------------------------------------------------------------------

class AllowanceExhaustedError(Exception):
    """Raised when IG weekly historical data allowance is exhausted."""
    pass


def fetch_gap_candles(
    ig, epic: str, uk_date: date_type,
    sh: int, sm: int, eh: int, em: int,
    limiter: IGRateLimiter, logger: logging.Logger,
) -> tuple:
    """
    Fetch 5-minute candles for the SPY-missing window on a UK date.

    Returns (df, allowance_info).
    """
    start_str, end_str = uk_window_to_utc_range(uk_date, sh, sm, eh, em)

    limiter.wait_for_slot()

    try:
        logger.debug(f"  API: {epic} {start_str} -> {end_str}")
        result = ig.fetch_historical_prices_by_epic_and_date_range(
            epic=epic,
            resolution=RESOLUTION,
            start_date=start_str,
            end_date=end_str,
            format=ig.mid_prices,
            version="2",
        )
        limiter.record_request()

        # Extract allowance
        allowance_info = {}
        if isinstance(result, dict):
            raw = result.get("allowance", {})
            if raw:
                allowance_info = {
                    "remainingAllowance": raw.get("remainingAllowance"),
                    "allowanceExpiry": raw.get("allowanceExpiry"),
                }

        # Extract DataFrame
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

        # Add source column
        df["source"] = "IG_US500_5m"

        return df, allowance_info

    except Exception as e:
        limiter.record_request()
        err = str(e).lower()
        logger.warning(f"  Error fetching {uk_date}: {e}")

        # Allowance exhausted — exit immediately
        if "exceeded" in err and "allowance" in err:
            raise AllowanceExhaustedError(str(e))

        # No historical data (weekend/holiday)
        if "historical price data not found" in err:
            logger.debug(f"  No historical data for {uk_date} (weekend/holiday)")
            return pd.DataFrame(), {}

        # Auth/credential errors — fatal
        if "authentication" in err or "401" in err:
            raise

        # Server errors (503 etc.) — retry with backoff
        if "503" in err or "server" in err or "service unavailable" in err:
            for retry in range(1, 4):  # up to 3 retries
                wait = 20 * retry + random.uniform(0, 10)
                logger.info(f"  Server error, retry {retry}/3 after {wait:.0f}s...")
                time.sleep(wait)
                limiter.wait_for_slot()
                try:
                    result = ig.fetch_historical_prices_by_epic_and_date_range(
                        epic=epic, resolution=RESOLUTION,
                        start_date=start_str, end_date=end_str,
                        format=ig.mid_prices, version="2",
                    )
                    limiter.record_request()
                    # If we get here, success — parse result
                    if result is None:
                        return pd.DataFrame(), {}
                    allowance_info = {}
                    if isinstance(result, dict):
                        raw = result.get("allowance", {})
                        if raw:
                            allowance_info = {
                                "remainingAllowance": raw.get("remainingAllowance"),
                                "allowanceExpiry": raw.get("allowanceExpiry"),
                            }
                        df = result.get("prices", pd.DataFrame())
                    else:
                        df = result if isinstance(result, pd.DataFrame) else pd.DataFrame()
                    if df is None or (isinstance(df, pd.DataFrame) and df.empty):
                        return pd.DataFrame(), allowance_info
                    if df.index.tz is None:
                        df.index = pd.to_datetime(df.index).tz_localize(TZ_UTC)
                    else:
                        df.index = df.index.tz_convert(TZ_UTC)
                    df.index.name = "DateTime_UTC"
                    col_map = {}
                    for col in df.columns:
                        cl = col.lower()
                        if "open" in cl: col_map[col] = "Open"
                        elif "high" in cl: col_map[col] = "High"
                        elif "low" in cl: col_map[col] = "Low"
                        elif "close" in cl: col_map[col] = "Close"
                        elif "volume" in cl or "vol" in cl: col_map[col] = "Volume"
                    if col_map:
                        df = df.rename(columns=col_map)
                    keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
                    df = df[keep].dropna(how="all").sort_index()
                    df = df[~df.index.duplicated(keep="first")]
                    df["source"] = "IG_US500_5m"
                    return df, allowance_info
                except Exception as re:
                    re_err = str(re).lower()
                    if "exceeded" in re_err and "allowance" in re_err:
                        raise AllowanceExhaustedError(str(re))
                    if "historical price data not found" in re_err:
                        return pd.DataFrame(), {}
                    logger.warning(f"  Retry {retry}/3 failed: {re}")
            logger.warning(f"  All retries exhausted for {uk_date}")
            return pd.DataFrame(), {}

        # Other transient errors
        return pd.DataFrame(), {}


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
# Main sync
# ---------------------------------------------------------------------------

def sync(
    epic: str, days: int, outdir: Path, window_uk: str,
    force: bool, max_rpm: int, max_retries: int,
    env: str, logger: logging.Logger,
) -> dict:
    state = load_state(STATE_FILE)

    # Check cooldown
    if state.get("cooldown_until_utc"):
        try:
            cooldown_until = datetime.fromisoformat(state["cooldown_until_utc"])
            now = datetime.now(timezone.utc)
            if now < cooldown_until:
                remaining = (cooldown_until - now).total_seconds()
                logger.info(f"Cooldown active ({remaining:.0f}s remaining). Exiting.")
                return {"status": "cooldown", "created": 0, "skipped": 0,
                        "no_data": 0, "remaining": 0}
        except (ValueError, TypeError):
            pass
        state["cooldown_until_utc"] = None
        save_state(STATE_FILE, state)

    # Parse window
    sh, sm, eh, em = parse_window(window_uk)
    window_minutes = ((eh * 60 + em) - (sh * 60 + sm)) % 1440
    expected_bars_per_day = max(window_minutes // 5, 1)
    logger.info(f"Window: UK {sh:02d}:{sm:02d} - {eh:02d}:{em:02d} "
                f"({window_minutes} min -> ~{expected_bars_per_day} bars/day at 5m)")

    # Date list
    dates = get_uk_dates_for_spy_trading_days(days)
    logger.info(f"Date range: {dates[0]} to {dates[-1]} ({len(dates)} UK dates)")

    outdir.mkdir(parents=True, exist_ok=True)

    # Credentials
    creds = load_credentials(env)
    if creds is None:
        logger.error(f"IG {env} credentials not found or incomplete.")
        return {"status": "no_creds", "created": 0, "skipped": 0,
                "no_data": 0, "remaining": len(dates)}

    # Connect
    try:
        ig = connect_ig(creds, logger)
    except Exception as e:
        logger.error(f"Failed to connect: {e}")
        state["last_error"] = f"Connection failed: {e}"
        save_state(STATE_FILE, state)
        return {"status": "connect_error", "created": 0, "skipped": 0,
                "no_data": 0, "remaining": len(dates)}

    limiter = IGRateLimiter(max_rpm=max_rpm, logger=logger)

    n_created = 0
    n_skipped = 0
    n_no_data = 0
    pending = []
    last_allowance = None
    datapoints = 0

    try:
        for uk_date in dates:
            ppath = gap_parquet_path(outdir, uk_date)

            if not force and is_valid_cached(ppath, logger):
                n_skipped += 1
                continue

            logger.info(f"Fetching {uk_date} ...")

            try:
                df, allowance_info = fetch_gap_candles(
                    ig, epic, uk_date, sh, sm, eh, em,
                    limiter, logger,
                )
            except AllowanceExhaustedError as e:
                logger.warning(f"Allowance exhausted: {e}")
                pending = [d.isoformat() for d in dates if d >= uk_date
                           and not (not force and is_valid_cached(
                               gap_parquet_path(outdir, d), logger))]
                expiry = (allowance_info.get("allowanceExpiry")
                          if 'allowance_info' in dir() else None)
                if expiry and expiry > 0:
                    cooldown = datetime.now(timezone.utc) + timedelta(seconds=expiry)
                else:
                    cooldown = datetime.now(timezone.utc) + timedelta(days=7)
                state["cooldown_until_utc"] = cooldown.isoformat()
                state["last_error"] = str(e)
                state["pending_uk_dates"] = pending
                save_state(STATE_FILE, state)
                logger.info(f"Cooldown until {cooldown.isoformat()}. "
                            f"{len(pending)} dates remaining.")
                break
            except Exception as e:
                if "authentication" in str(e).lower() or "401" in str(e):
                    logger.error(f"Auth error: {e}")
                    state["last_error"] = str(e)
                    save_state(STATE_FILE, state)
                    break
                logger.error(f"  Unexpected error for {uk_date}: {e}")
                pending.append(uk_date.isoformat())
                continue

            # Track allowance
            if allowance_info:
                ra = allowance_info.get("remainingAllowance")
                if ra is not None:
                    last_allowance = ra
                    state["last_remaining_allowance"] = ra

            if df.empty:
                n_no_data += 1
                logger.debug(f"  {uk_date}: no data (weekend/holiday)")
                continue

            bars = len(df)
            datapoints += bars

            write_parquet_atomic(df, ppath, logger)
            n_created += 1

            state["last_completed_uk_date"] = uk_date.isoformat()
            state["last_error"] = None
            state["total_datapoints_this_session"] = datapoints
            save_state(STATE_FILE, state)

            logger.info(f"  {uk_date}: {bars} bars -> {ppath.name}"
                        f"  [allowance: {last_allowance}]")

            # Check if allowance is getting low
            if last_allowance is not None and last_allowance < ALLOWANCE_SAFETY_BUFFER:
                pending = [d.isoformat() for d in dates if d > uk_date
                           and not (not force and is_valid_cached(
                               gap_parquet_path(outdir, d), logger))]
                logger.info(f"  Allowance low ({last_allowance}). Stopping. "
                            f"{len(pending)} remaining.")
                state["pending_uk_dates"] = pending
                save_state(STATE_FILE, state)
                break

    finally:
        safe_logout(ig, logger)
        state["pending_uk_dates"] = pending
        save_state(STATE_FILE, state)

    return {
        "status": "completed" if not pending else "partial",
        "created": n_created,
        "skipped": n_skipped,
        "no_data": n_no_data,
        "remaining": len(pending),
        "datapoints": datapoints,
        "last_allowance": last_allowance,
    }


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

def dry_run(days: int, window_uk: str, outdir: Path, logger: logging.Logger):
    sh, sm, eh, em = parse_window(window_uk)
    window_minutes = ((eh * 60 + em) - (sh * 60 + sm)) % 1440
    expected_bars = max(window_minutes // 5, 1)

    dates = get_uk_dates_for_spy_trading_days(days)

    to_fetch = []
    to_skip = []
    for d in dates:
        p = gap_parquet_path(outdir, d)
        if is_valid_cached(p, logger):
            to_skip.append(d)
        else:
            to_fetch.append(d)

    logger.info("")
    logger.info("=" * 60)
    logger.info("DRY RUN — no network calls")
    logger.info("=" * 60)
    logger.info(f"  Window:          UK {sh:02d}:{sm:02d} - {eh:02d}:{em:02d}")
    logger.info(f"  Window duration: {window_minutes} min")
    logger.info(f"  Expected bars/day: ~{expected_bars}")
    logger.info(f"  Date range:      {dates[0]} to {dates[-1]}")
    logger.info(f"  Total dates:     {len(dates)}")
    logger.info(f"  Already cached:  {len(to_skip)}")
    logger.info(f"  To fetch:        {len(to_fetch)}")
    logger.info(f"  Est. datapoints: ~{len(to_fetch) * expected_bars}")
    logger.info(f"  Est. % of 10k weekly allowance: "
                f"~{len(to_fetch) * expected_bars / 100:.0f}%")
    logger.info("")

    if to_fetch:
        logger.info("  First 10 dates to fetch:")
        for d in to_fetch[:10]:
            start_str, end_str = uk_window_to_utc_range(d, sh, sm, eh, em)
            logger.info(f"    {d}  UTC: {start_str} -> {end_str}")
        if len(to_fetch) > 10:
            logger.info(f"    ... and {len(to_fetch) - 10} more")
    logger.info("")


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify(outdir: Path, logger: logging.Logger):
    files = sorted(outdir.glob("*.parquet"))
    if not files:
        logger.info("No gap parquet files found.")
        return

    logger.info(f"\n{'='*60}")
    logger.info(f"VERIFICATION: {outdir}")
    logger.info(f"{'='*60}")
    logger.info(f"  Day files:  {len(files)}")

    df_first = pd.read_parquet(files[0])
    df_last = pd.read_parquet(files[-1])
    logger.info(f"  Earliest:   {df_first.index.min()}")
    logger.info(f"  Latest:     {df_last.index.max()}")

    bar_counts = []
    for f in files:
        try:
            df = pd.read_parquet(f)
            bar_counts.append(len(df))
        except Exception:
            pass

    if bar_counts:
        logger.info(f"  Bars/day:   min={min(bar_counts)}, max={max(bar_counts)}, "
                     f"median={sorted(bar_counts)[len(bar_counts)//2]}")

    # Sample from latest file
    logger.info(f"\n  Sample from {files[-1].stem}:")
    sample = df_last.head(3)
    for ts, row in sample.iterrows():
        cols = ", ".join(f"{c}={row[c]}" for c in ["Open", "High", "Low", "Close"]
                         if c in sample.columns and pd.notna(row[c]))
        logger.info(f"    {ts} | {cols}")
    logger.info("")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch IG US 500 5m candles for SPY-missing overnight window",
    )
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--env", choices=["demo", "live"], default="live")
    parser.add_argument("--epic", type=str, default=None)
    parser.add_argument("--outdir", type=str, default=str(DEFAULT_OUTDIR))
    parser.add_argument("--window-uk", type=str, default=DEFAULT_WINDOW_UK,
                        help=f"SPY-missing window in UK time HH:MM-HH:MM "
                             f"(default: {DEFAULT_WINDOW_UK})")
    parser.add_argument("--max-rpm", type=int, default=4)
    parser.add_argument("--max-retries", type=int, default=6)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print plan only, no network calls")
    return parser.parse_args()


def main():
    args = parse_args()
    logger = setup_logging(LOG_FILE)

    epic = resolve_epic(args.epic)
    outdir = Path(args.outdir)

    logger.info("=" * 60)
    logger.info("IG US 500 — SPY-gap 5-minute fetcher")
    logger.info(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info("=" * 60)
    logger.info(f"  EPIC:       {epic}")
    logger.info(f"  Env:        {args.env}")
    logger.info(f"  Days:       {args.days}")
    logger.info(f"  Window UK:  {args.window_uk}")
    logger.info(f"  Output:     {outdir}")
    logger.info(f"  Force:      {args.force}")
    logger.info(f"  Max RPM:    {args.max_rpm}")
    logger.info(f"  Dry run:    {args.dry_run}")
    logger.info("")

    if args.dry_run:
        dry_run(args.days, args.window_uk, outdir, logger)
        return

    result = sync(
        epic=epic, days=args.days, outdir=outdir, window_uk=args.window_uk,
        force=args.force, max_rpm=args.max_rpm, max_retries=args.max_retries,
        env=args.env, logger=logger,
    )

    logger.info("")
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"  Status:     {result['status']}")
    logger.info(f"  Created:    {result['created']}")
    logger.info(f"  Skipped:    {result['skipped']}")
    logger.info(f"  No data:    {result['no_data']}")
    logger.info(f"  Remaining:  {result['remaining']}")
    logger.info(f"  Datapoints: {result.get('datapoints', 0)}")
    logger.info(f"  Allowance:  {result.get('last_allowance', 'N/A')}")
    logger.info("")

    verify(outdir, logger)


if __name__ == "__main__":
    main()
