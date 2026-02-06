import os
import json
import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from rich.console import Console
from rich.progress import track
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

from session_utils import get_cash_session_window_utc, is_after_cash_close_et, TZ_ET, TZ_UTC
from rate_limiter import RateLimiter, RateLimitExceeded, CooldownActive

# Setup Rich Console
console = Console()


def fetch_yfinance_intraday(ticker: str, date_str: str) -> pd.DataFrame:
    """
    Fetch intraday minute data from yfinance as a fallback to Polygon.

    Args:
        ticker: Stock ticker (e.g., "SPY")
        date_str: Date string in YYYY-MM-DD format

    Returns:
        DataFrame with OHLCV data indexed by UTC datetime, or empty DataFrame on failure.
    """
    try:
        import yfinance as yf
        from datetime import datetime

        console.print(f"[cyan]Trying yfinance fallback for {ticker} on {date_str}...[/cyan]")

        # Parse target date
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        today = datetime.now(TZ_ET).date()

        # yfinance only provides recent intraday data (last 7 days for 1m interval)
        days_ago = (today - target_date).days
        if days_ago > 7:
            console.print(f"[yellow]yfinance: {date_str} is more than 7 days ago, 1m data unavailable.[/yellow]")
            return pd.DataFrame()

        # Fetch data
        yf_ticker = yf.Ticker(ticker)

        if days_ago == 0:
            # Today's data
            hist = yf_ticker.history(period="1d", interval="1m")
        else:
            # Recent historical - fetch a wider range and filter
            hist = yf_ticker.history(period="7d", interval="1m")

        if hist.empty:
            console.print(f"[yellow]yfinance: No data returned for {ticker}.[/yellow]")
            return pd.DataFrame()

        # yfinance returns data in exchange timezone (ET for US stocks)
        # Convert index to UTC to match Polygon format
        if hist.index.tz is None:
            # If no timezone, assume ET
            hist.index = hist.index.tz_localize(TZ_ET)

        hist.index = hist.index.tz_convert(TZ_UTC)

        # Filter to target date only
        target_start = TZ_UTC.localize(datetime.combine(target_date, datetime.min.time()))
        target_end = TZ_UTC.localize(datetime.combine(target_date + timedelta(days=1), datetime.min.time()))

        hist = hist[(hist.index >= target_start) & (hist.index < target_end)]

        if hist.empty:
            console.print(f"[yellow]yfinance: No data for {date_str} after filtering.[/yellow]")
            return pd.DataFrame()

        # Rename columns to match Polygon format (yfinance already uses these names)
        # yfinance columns: Open, High, Low, Close, Volume, Dividends, Stock Splits
        df = hist[['Open', 'High', 'Low', 'Close', 'Volume']].copy()

        console.print(f"[green]yfinance: Fetched {len(df)} minute bars for {date_str}.[/green]")
        return df

    except ImportError:
        console.print("[red]yfinance not installed. Run: pip install yfinance[/red]")
        return pd.DataFrame()
    except Exception as e:
        console.print(f"[red]yfinance error: {e}[/red]")
        return pd.DataFrame()


def assert_network_allowed():
    """
    Check if network access is allowed via ALLOW_NETWORK env var.

    Raises:
        RuntimeError: If ALLOW_NETWORK is not exactly "1".
    """
    if os.getenv("ALLOW_NETWORK") != "1":
        raise RuntimeError(
            "Network access denied. Set ALLOW_NETWORK=1 to enable data fetching. "
            "Run offline with existing parquet data or enable network access explicitly."
        )

# Load Config
CONFIG_PATH = os.path.join("config", "config.json")

class DataManager:
    def __init__(self, require_network=True):
        """
        Initialize DataManager.

        Args:
            require_network: If True (default), check ALLOW_NETWORK and require API key.
                             If False, allow offline operations only.
        """
        # Load environment variables from .env file
        load_dotenv()

        self._load_config()

        # Network check - fail fast if network disabled but required
        if require_network:
            assert_network_allowed()
            self.api_key = os.getenv("POLYGON_API_KEY")
            if not self.api_key:
                raise ValueError("POLYGON_API_KEY not found in environment variables")
        else:
            self.api_key = os.getenv("POLYGON_API_KEY")  # May be None for offline mode
        
        self.ticker = self.config["ticker"]
        self.base_dir = self.config["directories"]["data"]
        self.ticker_dir = os.path.join(self.base_dir, self.ticker)
        self.intraday_dir = os.path.join(self.ticker_dir, "intraday")
        
        # Ensure directories exist
        os.makedirs(self.intraday_dir, exist_ok=True)

        # Setup rate limiter with config values or defaults
        max_req_run = self.config.get("max_requests_per_run", 250)
        max_req_min = self.config.get("max_requests_per_minute", 4)
        self.rate_limiter = RateLimiter(
            max_requests_per_run=max_req_run,
            max_requests_per_minute=max_req_min
        )

        # Setup HTTP Session (without automatic retry - we handle it manually now)
        self.session = self._create_session()

    def _load_config(self):
        if not os.path.exists(CONFIG_PATH):
            raise FileNotFoundError(f"Config not found at {CONFIG_PATH}")
        with open(CONFIG_PATH, "r") as f:
            self.config = json.load(f)

    def _create_session(self):
        """Creates a basic requests session without automatic retries."""
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)
        session.mount('https://', adapter)
        session.mount('http://', adapter)
        return session

    def fetch_poly_aggs(self, ticker, from_date, to_date, multiplier, timespan):
        """
        Fetch aggregates from Polygon API with rate limiting and retry logic.

        Features:
        - Checks cooldown and budget before request
        - Loop-based retry with exponential backoff
        - Honors Retry-After header on 429
        - Persists state for resumable runs

        Raises:
            RuntimeError: If network access is not allowed.
            RateLimitExceeded: If request budget is exhausted.
            CooldownActive: If in cooldown from previous 429.
        """
        assert_network_allowed()

        # Check cooldown and budget
        self.rate_limiter.check_cooldown()
        self.rate_limiter.check_budget()

        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from_date}/{to_date}"
        params = {
            "adjusted": "true",
            "sort": "asc",
            "limit": 50000,
            "apiKey": self.api_key
        }

        # Loop-based retry
        for attempt in range(self.rate_limiter.max_retries + 1):
            try:
                # Wait for rate limit slot
                self.rate_limiter.wait_for_slot()

                # Make request
                response = self.session.get(url, params=params, timeout=13)
                self.rate_limiter.record_request()

                # Handle 429 with backoff
                if response.status_code == 429:
                    wait_time = self.rate_limiter.handle_rate_limit(response, attempt)
                    if wait_time is None:
                        console.print(f"[red]Rate limited. Max retries exceeded or cooldown set.[/red]")
                        raise RateLimitExceeded("429 rate limit exceeded max retries")
                    console.print(f"[yellow]Rate limited. Waiting {wait_time:.1f}s (attempt {attempt+1})...[/yellow]")
                    time.sleep(wait_time)
                    self.rate_limiter.total_sleep_this_run += wait_time
                    continue

                # Handle other errors
                if response.status_code >= 500:
                    if attempt < self.rate_limiter.max_retries:
                        wait_time = 5 * (2 ** attempt)
                        console.print(f"[yellow]Server error {response.status_code}. Retry in {wait_time}s...[/yellow]")
                        time.sleep(wait_time)
                        continue
                    response.raise_for_status()

                response.raise_for_status()

                # Parse successful response
                data = response.json()
                if "results" in data:
                    df = pd.DataFrame(data["results"])
                    df["t"] = pd.to_datetime(df["t"], unit="ms")
                    df.set_index("t", inplace=True)
                    df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"}, inplace=True)
                    self.rate_limiter.record_success(from_date)
                    return df
                else:
                    return pd.DataFrame()

            except requests.exceptions.Timeout:
                if attempt < self.rate_limiter.max_retries:
                    console.print(f"[yellow]Timeout. Retrying ({attempt+1})...[/yellow]")
                    continue
                console.print(f"[red]Timeout for {from_date} after {attempt+1} attempts.[/red]")
                return pd.DataFrame()

            except requests.exceptions.RequestException as e:
                console.print(f"[red]API Error for {from_date}: {e}[/red]")
                return pd.DataFrame()

        return pd.DataFrame()

    def update_daily_data(self):
        """
        Updates the Daily OHLCV file (Atomic Write).
        """
        console.print(f"[cyan]Checking Daily Data for {self.ticker}...[/cyan]")
        
        file_path = os.path.join(self.ticker_dir, "daily_OHLCV.parquet")
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        # Determine start date
        # Free (Stocks Basic): ~2 years (2024-01-01)
        # Starter ($29): 5 years available (2020-01-01)
        # Developer ($79): 10 years available (2015-01-01)
        start_date = "2015-01-01"  # Set for 10-year backtest with Stocks Developer plan
        existing_df = pd.DataFrame()
        
        if os.path.exists(file_path):
            try:
                existing_df = pd.read_parquet(file_path)
                if not existing_df.empty:
                    last_date = existing_df.index[-1]
                    last_date_str = last_date.strftime("%Y-%m-%d")

                    # Check if last date is today and we're after market close
                    # If so, re-fetch today to get the final close (not mid-day data)
                    if last_date_str == today_str and is_after_cash_close_et():
                        console.print("[yellow]Detected same-day data, re-fetching for final close...[/yellow]")
                        start_date = today_str  # Re-fetch today
                    else:
                        start_date = (last_date + timedelta(days=1)).strftime("%Y-%m-%d")
            except Exception as e:
                console.print(f"[bold red]Corrupt Daily File Detected![/bold red] Re-downloading all. ({e})")
                os.remove(file_path)

        # Check if up to date
        if start_date > today_str:
            console.print("[green]Daily data is up to date.[/green]")
            return

        # Fetch (may raise RateLimitExceeded or CooldownActive)
        console.print(f"Fetching Daily from {start_date}...")
        try:
            new_df = self.fetch_poly_aggs(self.ticker, start_date, today_str, 1, "day")
        except (RateLimitExceeded, CooldownActive):
            raise  # Propagate to run() handler
        
        if not new_df.empty:
            # Calculate Indicators
            new_df["Direction"] = new_df.apply(lambda row: "GREEN" if row["Close"] > row["Open"] else "RED", axis=1)
            new_df["Magnitude"] = abs((new_df["Close"] - new_df["Open"]) / new_df["Open"]) * 100
            
            # Combine
            if not existing_df.empty:
                full_df = pd.concat([existing_df, new_df])
                full_df = full_df[~full_df.index.duplicated(keep='last')]
            else:
                full_df = new_df
            
            # Recalculate ATR (Need continuous history)
            # Simple TR calculation
            full_df['H-L'] = full_df['High'] - full_df['Low']
            full_df['H-PC'] = abs(full_df['High'] - full_df['Close'].shift(1))
            full_df['L-PC'] = abs(full_df['Low'] - full_df['Close'].shift(1))
            full_df['TR'] = full_df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
            full_df['ATR_14'] = full_df['TR'].rolling(window=14).mean()
            
            # Cleanup
            full_df.drop(['H-L', 'H-PC', 'L-PC', 'TR'], axis=1, inplace=True)
            
            # ATOMIC WRITE: Save to temp, then rename
            temp_path = file_path + ".tmp"
            full_df.to_parquet(temp_path)
            os.replace(temp_path, file_path) # Atomic replacement
            
            console.print(f"[bold green]Updated Daily Data. Last: {full_df.index[-1]}[/bold green]")
        else:
            console.print("[yellow]No new daily data found.[/yellow]")

    def update_intraday_data(self):
        """
        Updates Intraday (1-minute) data.
        Free tier: Last 60 days
        Starter ($29): Last 5 years (365 * 5 = 1825 days)
        Developer ($79): Last 10 years (365 * 10 = 3650 days)
        Atomic Writes + Rate limiting with budget enforcement.
        """
        console.print(f"\n[cyan]Checking Intraday Data...[/cyan]")

        # 60 for free, 365*5 for Starter, 365*10 for Developer
        days_back = 365 * 10  # Set for 10-year backtest with Stocks Developer plan
        start_date = datetime.now() - timedelta(days=days_back)
        days_to_check = []

        for i in range(days_back):
            d = start_date + timedelta(days=i)
            if d.weekday() < 5:  # Skip weekends
                days_to_check.append(d.strftime("%Y-%m-%d"))

        fetched = 0
        skipped = 0

        for day_str in days_to_check:
            file_path = os.path.join(self.intraday_dir, f"{day_str}.parquet")

            if os.path.exists(file_path):
                skipped += 1
                continue  # Skip cached

            try:
                df = self.fetch_poly_aggs(self.ticker, day_str, day_str, 1, "minute")

                if not df.empty:
                    temp_path = file_path + ".tmp"
                    try:
                        df.to_parquet(temp_path)
                        os.replace(temp_path, file_path)
                        fetched += 1
                    except Exception as e:
                        console.print(f"[red]Write Error {day_str}: {e}[/red]")
                        if os.path.exists(temp_path):
                            os.remove(temp_path)

            except (RateLimitExceeded, CooldownActive) as e:
                console.print(f"[yellow]Stopping: {e}[/yellow]")
                console.print(f"[cyan]Progress: {fetched} fetched, {skipped} cached.[/cyan]")
                status = self.rate_limiter.get_status()
                console.print(f"[dim]Requests this run: {status['requests_this_run']}/{status['max_requests_per_run']}[/dim]")
                return  # Exit gracefully, state is saved

        console.print(f"[green]Intraday sync complete. {fetched} fetched, {skipped} cached.[/green]")

    def derive_daily_from_intraday(self, date_obj=None):
        """
        Derive daily OHLC from intraday minute bars for the cash session (09:30-16:00 ET).

        This enables same-evening dashboard runs without waiting for vendor daily bars
        which may not appear until UK morning.

        Args:
            date_obj: Date to derive. If None, uses today (if after 16:05 ET).

        Returns:
            True if daily was derived and updated, False otherwise.
        """
        if date_obj is None:
            now_utc = datetime.now(TZ_UTC)
            if not is_after_cash_close_et(now_utc):
                console.print("[yellow]Cash session not closed yet (before 16:05 ET). Skipping derivation.[/yellow]")
                return False
            date_obj = now_utc.astimezone(TZ_ET).date()
        elif isinstance(date_obj, str):
            # Convert string to date object
            date_obj = datetime.strptime(date_obj, "%Y-%m-%d").date()

        date_str = date_obj.strftime("%Y-%m-%d")

        # Load intraday data for this date
        intra_path = os.path.join(self.intraday_dir, f"{date_str}.parquet")
        if not os.path.exists(intra_path):
            console.print(f"[yellow]No intraday data for {date_str}. Cannot derive daily bar.[/yellow]")
            return False

        df_intra = pd.read_parquet(intra_path)
        if df_intra.empty:
            return False

        # Ensure UTC timezone
        if df_intra.index.tz is None:
            df_intra.index = df_intra.index.tz_localize('UTC')

        # Get cash session window
        cash_start, cash_end = get_cash_session_window_utc(date_obj)

        # Filter to cash session only
        cash_df = df_intra[(df_intra.index >= cash_start) & (df_intra.index <= cash_end)]

        if cash_df.empty or len(cash_df) < 10:  # Require at least 10 minutes of data
            console.print(f"[yellow]Insufficient cash session data for {date_str}.[/yellow]")
            return False

        # Derive OHLC
        derived_row = {
            'Open': cash_df['Open'].iloc[0],
            'High': cash_df['High'].max(),
            'Low': cash_df['Low'].min(),
            'Close': cash_df['Close'].iloc[-1],
            'Volume': cash_df['Volume'].sum() if 'Volume' in cash_df.columns else 0,
            'Is_Derived_Cash': True  # Flag to identify derived data
        }

        # Calculate Direction and Magnitude
        derived_row['Direction'] = "GREEN" if derived_row['Close'] > derived_row['Open'] else "RED"
        derived_row['Magnitude'] = abs((derived_row['Close'] - derived_row['Open']) / derived_row['Open']) * 100

        # Load existing daily data
        file_path = os.path.join(self.ticker_dir, "daily_OHLCV.parquet")
        if os.path.exists(file_path):
            daily_df = pd.read_parquet(file_path)
        else:
            daily_df = pd.DataFrame()

        # Create index for the new row (match existing index timezone)
        row_index = pd.Timestamp(date_str)
        if not daily_df.empty and daily_df.index.tz is not None:
            row_index = row_index.tz_localize(daily_df.index.tz)

        # Add or update the row
        for col, val in derived_row.items():
            if col not in daily_df.columns:
                daily_df[col] = pd.NA
            daily_df.loc[row_index, col] = val

        # Ensure Is_Derived_Cash column exists and old rows are marked False
        if 'Is_Derived_Cash' not in daily_df.columns:
            daily_df['Is_Derived_Cash'] = False
        daily_df['Is_Derived_Cash'] = daily_df['Is_Derived_Cash'].fillna(False).infer_objects(copy=False)

        # Recalculate ATR for new data
        daily_df = daily_df.sort_index()
        daily_df['H-L'] = daily_df['High'] - daily_df['Low']
        daily_df['H-PC'] = abs(daily_df['High'] - daily_df['Close'].shift(1))
        daily_df['L-PC'] = abs(daily_df['Low'] - daily_df['Close'].shift(1))
        daily_df['TR'] = daily_df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
        daily_df['ATR_14'] = daily_df['TR'].rolling(window=14).mean()
        daily_df.drop(['H-L', 'H-PC', 'L-PC', 'TR'], axis=1, inplace=True)

        # Atomic write
        temp_path = file_path + ".tmp"
        daily_df.to_parquet(temp_path)
        os.replace(temp_path, file_path)

        console.print(f"[bold green]Derived daily bar for {date_str} from intraday data.[/bold green]")
        return True

    def run(self):
        try:
            # Check cooldown before starting
            self.rate_limiter.check_cooldown()

            self.update_daily_data()
            self.update_intraday_data()
            # Try to derive today's daily bar if after cash close
            self.derive_daily_from_intraday()

            status = self.rate_limiter.get_status()
            console.print(f"\n[bold green]Sync complete.[/bold green]")
            console.print(f"[dim]Requests used: {status['requests_this_run']}/{status['max_requests_per_run']}[/dim]")

        except CooldownActive as e:
            console.print(f"[yellow]{e}[/yellow]")
            console.print("[dim]Run again after cooldown expires.[/dim]")

        except RateLimitExceeded as e:
            console.print(f"[yellow]{e}[/yellow]")
            console.print("[dim]Run again to resume from where we left off.[/dim]")

        except KeyboardInterrupt:
            console.print("\n[yellow]Sync Cancelled by User.[/yellow]")
            status = self.rate_limiter.get_status()
            console.print(f"[dim]Requests used: {status['requests_this_run']}[/dim]")

if __name__ == "__main__":
    # Network must be explicitly enabled for data fetching
    try:
        dm = DataManager(require_network=True)
        dm.run()
    except RuntimeError as e:
        console.print(f"[bold red]Network Error:[/bold red] {e}")
        console.print("[yellow]To run data_manager, set: ALLOW_NETWORK=1[/yellow]")