"""
Fetch and cache IG US 500 underlying 1-minute candles.

Fetches mid-price OHLCV bars from IG's historical prices API for the
IX.D.SPTRD.DAILY.IP epic.  Day files are partitioned by UK calendar date
(Europe/London) and stored as UTC-indexed parquet in data/IG_US500_1m/.

Designed for unattended runs:
  - Incremental: skips dates that already have a valid cached file
  - Rate-limited: token-bucket + jitter, respects Retry-After
  - Resumable: persists state in logs/ig_us500_1m_state.json
  - Safe: backs off on allowance errors and exits cleanly

Usage:
    python scripts/data/fetch_ig_us500_1m.py --days 60
    python scripts/data/fetch_ig_us500_1m.py --days 2 --force
    python scripts/data/fetch_ig_us500_1m.py --days 60 --max-rpm 4

Timezone rule:
  - Day partitions keyed by UK calendar date (Europe/London)
  - Parquet files store UTC-indexed timestamps
  - This matches planned exit-variant backtests that use UK times
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
TZ_UTC = pytz.utc

DEFAULT_EPIC = "IX.D.SPTRD.DAILY.IP"
DEFAULT_OUTDIR = Path("data/IG_US500_1m")
STATE_FILE = Path("logs/ig_us500_1m_state.json")
LOG_FILE = Path("logs/ig_us500_1m_sync.log")

# IG API constraints
# The v2 /prices endpoint returns up to ~10,000 data points per request.
# For 1-min bars that's ~7 days.  We fetch 1 UK-day at a time for clean partitioning.
# trading-ig's conv_resol() converts pandas frequency strings to IG resolution names:
#   "1Min" -> "MINUTE", "5Min" -> "MINUTE_5", "1h" -> "HOUR", "D" -> "DAY"
RESOLUTION = "1Min"  # Pandas frequency string -> IG "MINUTE" (1-min bars)
IG_DATE_FMT_V2 = "%Y-%m-%d %H:%M:%S"  # v2 date format

# Integrity: minimum expected bars for a full trading day
# IG US 500 trades Sun 23:00 – Fri 22:00 UK, ~23 hours/day
# 1-min bars: ~1380 per 23h day.  Set threshold lower to account for holidays.
MIN_BARS_PER_DAY = 100  # below this, consider partial/corrupt


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(log_path: Path) -> logging.Logger:
    """Configure file + console logging."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ig_us500_1m")
    logger.setLevel(logging.DEBUG)

    # File handler (append)
    fh = logging.FileHandler(str(log_path), mode="a", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)

    # Console handler (INFO+)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)

    return logger


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def load_state(path: Path) -> dict:
    """Load persistent state or return defaults."""
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {
        "last_completed_date": None,
        "dates_missing": [],
        "last_error": None,
        "cooldown_until_utc": None,
        "total_requests_all_runs": 0,
    }


def save_state(path: Path, state: dict):
    """Persist state atomically."""
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
# Rate limiter (dedicated, uses project's token-bucket pattern)
# ---------------------------------------------------------------------------

class IGRateLimiter:
    """
    Token-bucket rate limiter with exponential backoff and cooldown.

    Reuses the pattern from src/rate_limiter.py but is self-contained
    with its own state file specific to this pipeline.
    """

    def __init__(self, max_rpm: int = 4, max_retries: int = 8,
                 max_total_sleep: int = 600, logger: logging.Logger = None):
        self.max_rpm = max_rpm
        self.max_retries = max_retries
        self.max_total_sleep = max_total_sleep
        self.log = logger or logging.getLogger("ig_us500_1m")

        self.request_times: list = []
        self.total_sleep: float = 0.0
        self.requests_this_run: int = 0

    def wait_for_slot(self):
        """Block until a request slot is available (token bucket)."""
        now = time.time()
        # Purge timestamps older than 60s
        self.request_times = [t for t in self.request_times if now - t < 60]

        if len(self.request_times) >= self.max_rpm:
            oldest = min(self.request_times)
            wait = 60.0 - (now - oldest)
            if wait > 0:
                jitter = random.uniform(0.5, 2.0)
                total_wait = wait + jitter
                if self.total_sleep + total_wait > self.max_total_sleep:
                    raise RuntimeError(
                        f"Rate limiter: would exceed max sleep "
                        f"({self.max_total_sleep}s). Exiting."
                    )
                self.log.debug(f"Rate limiter: sleeping {total_wait:.1f}s "
                               f"(slot wait {wait:.1f}s + jitter {jitter:.1f}s)")
                time.sleep(total_wait)
                self.total_sleep += total_wait

    def record_request(self):
        """Record a request timestamp."""
        self.request_times.append(time.time())
        self.requests_this_run += 1

    def backoff_sleep(self, attempt: int, retry_after: int = None) -> float:
        """
        Exponential backoff with jitter. Returns actual sleep time.

        Args:
            attempt: 0-indexed retry count
            retry_after: Retry-After header value in seconds (if present)

        Returns:
            Seconds slept. Raises RuntimeError if max sleep exceeded.
        """
        if retry_after and retry_after > 0:
            base = float(retry_after)
        else:
            base = 15.0 * (2 ** attempt)
        jitter = random.uniform(0, base * 0.15)
        wait = base + jitter

        if self.total_sleep + wait > self.max_total_sleep:
            raise RuntimeError(
                f"Backoff would exceed max sleep ({self.max_total_sleep}s). "
                f"Exiting for scheduler to retry later."
            )

        self.log.info(f"  Backoff: sleeping {wait:.1f}s (attempt {attempt+1})")
        time.sleep(wait)
        self.total_sleep += wait
        return wait


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def load_credentials() -> dict:
    """
    Load IG API credentials from config/ig_api_credentials.json.

    Returns dict with keys: username, password, api_key, acc_type, acc_number.
    Returns None if credentials are unavailable or placeholder.
    """
    cred_path = Path("config/ig_api_credentials.json")
    if not cred_path.exists():
        return None
    try:
        with open(cred_path) as f:
            creds = json.load(f)
    except (json.JSONDecodeError, IOError):
        return None

    # Use demo account (same as collect_ig_spreads.py)
    demo = creds.get("demo", {})
    if not demo.get("api_key") or not demo.get("username") or not demo.get("password"):
        return None

    # Check for placeholder values
    if "YOUR_" in demo.get("api_key", "") or "YOUR_" in demo.get("username", ""):
        return None

    return demo


def connect_ig(creds: dict, logger: logging.Logger):
    """
    Connect to IG API and return IGService instance.
    Reuses the pattern from collect_ig_spreads.py.
    """
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
    """Disconnect from IG API, ignoring errors."""
    try:
        ig.logout()
        logger.info("Disconnected from IG API")
    except Exception:
        logger.debug("Logout raised (ignored)")


# ---------------------------------------------------------------------------
# UK-date partitioning
# ---------------------------------------------------------------------------

def get_uk_dates_to_fetch(days: int) -> list:
    """
    Return list of UK calendar dates to fetch, from (today-days) to yesterday.

    We never fetch today's (incomplete) data — only completed UK days.

    Returns:
        List of datetime.date in ascending order.
    """
    now_uk = datetime.now(TZ_UK)
    # Yesterday UK is the latest complete day
    end_date = (now_uk - timedelta(days=1)).date()
    start_date = (now_uk - timedelta(days=days)).date()

    dates = []
    d = start_date
    while d <= end_date:
        dates.append(d)
        d += timedelta(days=1)
    return dates


def uk_date_to_utc_window(uk_date: date_type) -> tuple:
    """
    Convert a UK calendar date to a UTC datetime window [start, end).

    UK day starts at 00:00:00 Europe/London and ends at 23:59:59.
    We return UTC boundaries.

    Returns:
        (start_utc_str, end_utc_str) formatted for IG API v2.
    """
    # Start of UK day
    uk_start = TZ_UK.localize(datetime(uk_date.year, uk_date.month, uk_date.day, 0, 0, 0))
    # End of UK day (23:59:59 to capture all bars in the day)
    uk_end = TZ_UK.localize(datetime(uk_date.year, uk_date.month, uk_date.day, 23, 59, 59))

    utc_start = uk_start.astimezone(TZ_UTC)
    utc_end = uk_end.astimezone(TZ_UTC)

    return (
        utc_start.strftime(IG_DATE_FMT_V2),
        utc_end.strftime(IG_DATE_FMT_V2),
    )


# ---------------------------------------------------------------------------
# Parquet I/O with integrity
# ---------------------------------------------------------------------------

def parquet_path(outdir: Path, uk_date: date_type) -> Path:
    """Return the parquet path for a given UK date."""
    return outdir / f"{uk_date.isoformat()}.parquet"


def is_valid_cached(path: Path, logger: logging.Logger) -> bool:
    """
    Check if a cached parquet file passes structural integrity checks.

    Used by the sync loop to decide whether to skip re-fetching.
    Does NOT enforce MIN_BARS_PER_DAY — a file with 60 legitimate bars
    (e.g. Sunday open) is structurally valid even if incomplete.

    Checks:
      1. File exists and is readable as parquet
      2. Has at least 1 row (empty files are invalid)
      3. Index is DatetimeIndex and tz-aware UTC
      4. Timestamps are strictly increasing
    """
    if not path.exists():
        return False

    try:
        df = pd.read_parquet(path)
    except Exception as e:
        logger.warning(f"  Corrupt parquet {path.name}: {e}")
        return False

    # Must have at least 1 bar
    if len(df) == 0:
        logger.debug(f"  Empty file {path.name}")
        return False

    # Index must be datetime
    if not isinstance(df.index, pd.DatetimeIndex):
        logger.warning(f"  Non-datetime index in {path.name}")
        return False

    # Must be tz-aware UTC
    if df.index.tz is None:
        logger.warning(f"  Tz-naive index in {path.name}")
        return False

    # Strictly increasing
    if not df.index.is_monotonic_increasing:
        logger.warning(f"  Non-monotonic timestamps in {path.name}")
        return False

    return True


def write_parquet_atomic(df: pd.DataFrame, path: Path, logger: logging.Logger):
    """Write parquet atomically (temp file + rename)."""
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
# IG historical price fetching
# ---------------------------------------------------------------------------

def fetch_day_candles(
    ig, epic: str, uk_date: date_type,
    limiter: IGRateLimiter, logger: logging.Logger,
    max_retries: int = 8,
) -> pd.DataFrame:
    """
    Fetch 1-minute candles for one UK calendar day from IG API.

    Uses fetch_historical_prices_by_epic_and_date_range with MINUTE resolution.
    Mid-prices are computed by the trading-ig library's mid_prices formatter.

    Args:
        ig: Connected IGService instance.
        epic: IG instrument EPIC.
        uk_date: The UK calendar date to fetch.
        limiter: Rate limiter instance.
        logger: Logger.
        max_retries: Max retry attempts on transient errors.

    Returns:
        DataFrame with UTC-indexed mid-price OHLCV, or empty DataFrame if
        no data available (e.g. weekend/holiday).

    Raises:
        RuntimeError: On allowance/rate errors that require a long cooldown.
    """
    start_str, end_str = uk_date_to_utc_window(uk_date)

    for attempt in range(max_retries):
        limiter.wait_for_slot()

        try:
            logger.debug(f"  API call: {epic} {start_str} -> {end_str} "
                         f"(attempt {attempt+1})")
            result = ig.fetch_historical_prices_by_epic_and_date_range(
                epic=epic,
                resolution=RESOLUTION,
                start_date=start_str,
                end_date=end_str,
                format=ig.mid_prices,
                version="2",
            )
            limiter.record_request()

            # Extract DataFrame
            if result is None:
                logger.debug(f"  No data returned for {uk_date}")
                return pd.DataFrame()

            if isinstance(result, dict):
                df = result.get("prices", pd.DataFrame())
            else:
                df = result if isinstance(result, pd.DataFrame) else pd.DataFrame()

            if df is None or df.empty:
                logger.debug(f"  Empty response for {uk_date}")
                return pd.DataFrame()

            # Normalize index to UTC
            if df.index.tz is None:
                # IG v2 returns UTC-naive timestamps — localize
                df.index = pd.to_datetime(df.index).tz_localize(TZ_UTC)
            else:
                df.index = df.index.tz_convert(TZ_UTC)

            df.index.name = "DateTime_UTC"

            # Rename columns to standard OHLCV
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

            # Keep only OHLCV columns
            keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
            df = df[keep]

            # Drop any rows with all NaN
            df = df.dropna(how="all")

            # Sort by time (should already be sorted)
            df = df.sort_index()

            # Remove duplicates
            df = df[~df.index.duplicated(keep="first")]

            logger.debug(f"  Got {len(df)} bars for {uk_date}")
            return df

        except Exception as e:
            limiter.record_request()
            err = str(e).lower()
            logger.warning(f"  Error fetching {uk_date} (attempt {attempt+1}): {e}")

            # Check for allowance / rate limit errors
            is_rate_error = any(x in err for x in (
                "exceeded", "allowance", "rate", "429", "too many",
                "throttl", "limit",
            ))

            if is_rate_error:
                if attempt < max_retries - 1:
                    # Check for Retry-After in the exception
                    retry_after = None
                    if hasattr(e, "response") and e.response is not None:
                        ra = e.response.headers.get("Retry-After")
                        if ra:
                            try:
                                retry_after = int(ra)
                            except ValueError:
                                pass

                    try:
                        limiter.backoff_sleep(attempt, retry_after)
                    except RuntimeError:
                        # Max sleep exceeded — signal caller for long cooldown
                        raise RuntimeError(
                            f"IG API rate/allowance error after {attempt+1} retries. "
                            "Setting long cooldown."
                        )
                else:
                    raise RuntimeError(
                        f"IG API rate/allowance error after {max_retries} retries. "
                        "Setting long cooldown."
                    )
            else:
                # Non-rate error (auth, epic not found, etc.)
                # Check specific "no historical data" BEFORE generic "not found"
                # because the IG error message contains "not found" in both cases.
                if "historical price data not found" in err:
                    # No data for this date (weekend/holiday) — not an error
                    logger.debug(f"  No historical data for {uk_date} (expected for weekends)")
                    return pd.DataFrame()
                if any(x in err for x in ("not found", "invalid", "no instrument")):
                    logger.error(f"  Epic {epic} not found or invalid")
                    return pd.DataFrame()

                # Transient error — retry with backoff
                if attempt < max_retries - 1:
                    try:
                        limiter.backoff_sleep(attempt)
                    except RuntimeError:
                        raise
                else:
                    raise

    return pd.DataFrame()


# ---------------------------------------------------------------------------
# Main sync loop
# ---------------------------------------------------------------------------

def sync(
    epic: str,
    days: int,
    outdir: Path,
    force: bool,
    max_rpm: int,
    max_retries: int,
    logger: logging.Logger,
):
    """
    Main sync: fetch and cache IG US 500 1m candles for the last N days.

    Returns:
        (n_created, n_skipped, n_errors, n_no_data)
    """
    state = load_state(STATE_FILE)

    # Check cooldown
    if state.get("cooldown_until_utc"):
        cooldown_until = datetime.fromisoformat(state["cooldown_until_utc"])
        now = datetime.now(timezone.utc)
        if now < cooldown_until:
            remaining = (cooldown_until - now).total_seconds()
            logger.info(f"Cooldown active until {cooldown_until.isoformat()} "
                        f"({remaining:.0f}s remaining). Exiting.")
            return 0, 0, 0, 0
        else:
            state["cooldown_until_utc"] = None
            save_state(STATE_FILE, state)

    # Determine dates to fetch
    dates = get_uk_dates_to_fetch(days)
    logger.info(f"Date range: {dates[0]} to {dates[-1]} ({len(dates)} UK days)")

    # Create output dir
    outdir.mkdir(parents=True, exist_ok=True)

    # Load credentials
    creds = load_credentials()
    if creds is None:
        logger.info("IG credentials not found or incomplete. "
                     "Set up config/ig_api_credentials.json to use this script.")
        logger.info("Exiting cleanly (no error).")
        return 0, 0, 0, 0

    # Connect
    try:
        ig = connect_ig(creds, logger)
    except Exception as e:
        logger.error(f"Failed to connect to IG API: {e}")
        state["last_error"] = f"Connection failed: {e}"
        save_state(STATE_FILE, state)
        return 0, 0, 1, 0

    # Rate limiter
    limiter = IGRateLimiter(
        max_rpm=max_rpm,
        max_retries=max_retries,
        logger=logger,
    )

    n_created = 0
    n_skipped = 0
    n_errors = 0
    n_no_data = 0
    dates_missing = []

    try:
        for uk_date in dates:
            ppath = parquet_path(outdir, uk_date)

            # Skip if already cached and valid (unless --force)
            if not force and is_valid_cached(ppath, logger):
                n_skipped += 1
                continue

            logger.info(f"Fetching {uk_date} ...")

            try:
                df = fetch_day_candles(
                    ig, epic, uk_date, limiter, logger, max_retries
                )
            except RuntimeError as e:
                # Rate/allowance error — set long cooldown and exit
                logger.error(f"Rate limit error: {e}")
                cooldown_secs = 1800  # 30 minutes
                cooldown_until = (
                    datetime.now(timezone.utc) + timedelta(seconds=cooldown_secs)
                )
                state["cooldown_until_utc"] = cooldown_until.isoformat()
                state["last_error"] = str(e)
                state["dates_missing"] = dates_missing
                save_state(STATE_FILE, state)
                logger.info(f"Cooldown set until {cooldown_until.isoformat()}. "
                            "Re-run later to resume.")
                break
            except Exception as e:
                logger.error(f"  Unexpected error for {uk_date}: {e}")
                n_errors += 1
                dates_missing.append(uk_date.isoformat())
                state["last_error"] = f"{uk_date}: {e}"
                save_state(STATE_FILE, state)
                continue

            if df.empty:
                # Weekend or holiday — no data expected
                n_no_data += 1
                logger.debug(f"  {uk_date}: no data (weekend/holiday)")
                continue

            # Integrity check before writing
            if len(df) < MIN_BARS_PER_DAY:
                logger.warning(f"  {uk_date}: only {len(df)} bars (below {MIN_BARS_PER_DAY})")
                dates_missing.append(uk_date.isoformat())

            # Write atomically
            write_parquet_atomic(df, ppath, logger)
            n_created += 1

            # Update state
            state["last_completed_date"] = uk_date.isoformat()
            state["last_error"] = None
            state["total_requests_all_runs"] = (
                state.get("total_requests_all_runs", 0) + 1
            )
            save_state(STATE_FILE, state)

            logger.info(f"  {uk_date}: {len(df)} bars -> {ppath.name}")

    finally:
        # Always disconnect
        safe_logout(ig, logger)

        # Update final state
        state["dates_missing"] = dates_missing
        save_state(STATE_FILE, state)

    return n_created, n_skipped, n_errors, n_no_data


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify(outdir: Path, logger: logging.Logger):
    """Print summary of cached data."""
    files = sorted(outdir.glob("*.parquet"))
    if not files:
        logger.info("No parquet files found.")
        return

    logger.info(f"\n{'='*60}")
    logger.info(f"VERIFICATION: {outdir}")
    logger.info(f"{'='*60}")
    logger.info(f"  Day files: {len(files)}")

    # Load first and last to get time range
    df_first = pd.read_parquet(files[0])
    df_last = pd.read_parquet(files[-1])

    earliest = df_first.index.min()
    latest = df_last.index.max()
    logger.info(f"  Earliest timestamp: {earliest}")
    logger.info(f"  Latest timestamp:   {latest}")

    # Check bar counts
    bar_counts = []
    bad_files = []
    for f in files:
        try:
            df = pd.read_parquet(f)
            bar_counts.append(len(df))
            if len(df) < MIN_BARS_PER_DAY:
                bad_files.append((f.stem, len(df)))
        except Exception as e:
            bad_files.append((f.stem, f"ERROR: {e}"))

    if bar_counts:
        logger.info(f"  Bars per day: min={min(bar_counts)}, "
                     f"max={max(bar_counts)}, "
                     f"median={sorted(bar_counts)[len(bar_counts)//2]}")

    if bad_files:
        logger.info(f"  Partial/bad files ({len(bad_files)}):")
        for name, count in bad_files[:10]:
            logger.info(f"    {name}: {count}")

    # Show a sample from the latest file
    logger.info(f"\n  Sample from {files[-1].stem}:")
    df_sample = df_last.head(3)
    for ts, row in df_sample.iterrows():
        cols = ", ".join(f"{c}={row[c]:.2f}" for c in df_sample.columns
                         if pd.notna(row[c]))
        logger.info(f"    {ts} | {cols}")

    logger.info("")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch and cache IG US 500 1-minute underlying candles",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Timezone rule:\n"
            "  Day files are partitioned by UK calendar date (Europe/London).\n"
            "  Parquet files contain UTC-indexed timestamps.\n"
            "\nExamples:\n"
            "  %(prog)s --days 2          # Fetch last 2 days\n"
            "  %(prog)s --days 60         # Fetch last 60 days\n"
            "  %(prog)s --days 7 --force  # Refetch last 7 days\n"
        ),
    )
    parser.add_argument(
        "--days", type=int, default=60,
        help="Number of calendar days to look back (default: 60)",
    )
    parser.add_argument(
        "--epic", type=str, default=None,
        help=f"IG instrument EPIC (default: from config or {DEFAULT_EPIC})",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Refetch even if file already exists and passes integrity checks",
    )
    parser.add_argument(
        "--max-rpm", type=int, default=4,
        help="Max requests per minute (default: 4, conservative)",
    )
    parser.add_argument(
        "--max-retries", type=int, default=8,
        help="Max retry attempts per request (default: 8)",
    )
    parser.add_argument(
        "--outdir", type=str, default=str(DEFAULT_OUTDIR),
        help=f"Output directory (default: {DEFAULT_OUTDIR})",
    )
    parser.add_argument(
        "--verify-only", action="store_true",
        help="Only verify existing cached data, do not fetch",
    )
    return parser.parse_args()


def resolve_epic(args_epic: str) -> str:
    """Resolve EPIC from CLI arg, config, or env."""
    if args_epic:
        return args_epic

    # Try config.json
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

    # Try environment variable
    epic = os.environ.get("IG_US500_EPIC")
    if epic:
        return epic

    return DEFAULT_EPIC


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    logger = setup_logging(LOG_FILE)

    epic = resolve_epic(args.epic)
    outdir = Path(args.outdir)

    logger.info("=" * 60)
    logger.info("IG US 500 — 1-minute candle fetcher")
    logger.info(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info("=" * 60)
    logger.info(f"  EPIC:         {epic}")
    logger.info(f"  Days:         {args.days}")
    logger.info(f"  Output dir:   {outdir}")
    logger.info(f"  Force:        {args.force}")
    logger.info(f"  Max RPM:      {args.max_rpm}")
    logger.info(f"  Max retries:  {args.max_retries}")
    logger.info(f"  Verify only:  {args.verify_only}")
    logger.info("")

    if args.verify_only:
        verify(outdir, logger)
        return

    n_created, n_skipped, n_errors, n_no_data = sync(
        epic=epic,
        days=args.days,
        outdir=outdir,
        force=args.force,
        max_rpm=args.max_rpm,
        max_retries=args.max_retries,
        logger=logger,
    )

    logger.info("")
    logger.info(f"{'='*60}")
    logger.info("SUMMARY")
    logger.info(f"{'='*60}")
    logger.info(f"  Created:    {n_created} day files")
    logger.info(f"  Skipped:    {n_skipped} (already cached)")
    logger.info(f"  No data:    {n_no_data} (weekends/holidays)")
    logger.info(f"  Errors:     {n_errors}")
    logger.info("")

    # Post-sync verification
    verify(outdir, logger)


if __name__ == "__main__":
    main()
