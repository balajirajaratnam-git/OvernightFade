"""
Rate limiting and request budget management for API calls.
Implements token bucket limiter with exponential backoff and state persistence.
"""
import os
import json
import time
import random
from datetime import datetime, timedelta
from typing import Optional

# Default configuration
DEFAULT_MAX_REQUESTS_PER_RUN = 250
DEFAULT_MAX_REQUESTS_PER_MINUTE = 4
DEFAULT_MAX_RETRIES = 5
DEFAULT_MAX_TOTAL_SLEEP = 3600  # 1 hour max sleep total (for unlimited API plans)
DEFAULT_BASE_BACKOFF = 15  # seconds

# Persistent state file for tracking API usage and cooldowns across runs
# See logs/README.md for detailed documentation
STATE_FILE = os.path.join("logs", "state.json")


class RateLimitExceeded(Exception):
    """Raised when request budget is exhausted."""
    pass


class CooldownActive(Exception):
    """Raised when cooldown period is still active."""
    pass


class RateLimiter:
    """
    Token bucket rate limiter with request budget and state persistence.

    Features:
    - Enforces max_requests_per_minute with jitter
    - Tracks total requests per run against budget
    - Persists state to disk for resumable runs
    - Handles 429 responses with exponential backoff
    """

    def __init__(
        self,
        max_requests_per_run: int = DEFAULT_MAX_REQUESTS_PER_RUN,
        max_requests_per_minute: int = DEFAULT_MAX_REQUESTS_PER_MINUTE,
        max_retries: int = DEFAULT_MAX_RETRIES,
        max_total_sleep: int = DEFAULT_MAX_TOTAL_SLEEP,
    ):
        self.max_requests_per_run = max_requests_per_run
        self.max_requests_per_minute = max_requests_per_minute
        self.max_retries = max_retries
        self.max_total_sleep = max_total_sleep

        self.requests_this_run = 0
        self.total_sleep_this_run = 0
        self.last_request_time = 0
        self.request_times = []  # Track last N request timestamps

        # Load state
        self.state = self._load_state()

    def _load_state(self) -> dict:
        """Load state from disk."""
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {
            "last_successful_date": None,
            "requests_used_this_run": 0,
            "cooldown_until_utc": None,
            "last_error": None,
            "run_id": None
        }

    def _save_state(self):
        """Persist state to disk."""
        # Skip state persistence if file is locked (unlimited API plans don't need it)
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            self.state["requests_used_this_run"] = self.requests_this_run
            with open(STATE_FILE, 'w') as f:
                json.dump(self.state, f, indent=2, default=str)
        except (PermissionError, OSError):
            # Silently skip state persistence if file is locked
            # This is fine for unlimited API plans (Developer tier)
            pass

    def check_cooldown(self):
        """Check if we're still in a cooldown period from a previous 429."""
        if self.state.get("cooldown_until_utc"):
            cooldown_until = datetime.fromisoformat(self.state["cooldown_until_utc"])
            now = datetime.utcnow()
            if now < cooldown_until:
                remaining = (cooldown_until - now).total_seconds()
                raise CooldownActive(
                    f"Rate limit cooldown active. Resume after {cooldown_until.isoformat()} UTC "
                    f"({remaining:.0f} seconds remaining). Exiting cleanly."
                )
            else:
                # Cooldown expired, clear it
                self.state["cooldown_until_utc"] = None
                self._save_state()

    def check_budget(self):
        """Check if request budget allows another request."""
        if self.requests_this_run >= self.max_requests_per_run:
            self.state["last_error"] = "Request budget exhausted"
            self._save_state()
            raise RateLimitExceeded(
                f"Request budget exhausted ({self.requests_this_run}/{self.max_requests_per_run}). "
                "Run will resume on next execution."
            )

    def wait_for_slot(self):
        """
        Wait until we have a slot available (token bucket).
        Enforces max_requests_per_minute with jitter.
        """
        now = time.time()

        # Clean old request times (older than 60 seconds)
        self.request_times = [t for t in self.request_times if now - t < 60]

        # If we've hit the per-minute limit, wait
        if len(self.request_times) >= self.max_requests_per_minute:
            oldest = min(self.request_times)
            wait_time = 60 - (now - oldest)
            if wait_time > 0:
                # Add jitter (0-2 seconds)
                wait_time += random.uniform(0, 2)
                if self.total_sleep_this_run + wait_time > self.max_total_sleep:
                    self.state["last_error"] = "Max total sleep exceeded"
                    self._save_state()
                    raise RateLimitExceeded(
                        f"Would exceed max total sleep ({self.max_total_sleep}s). "
                        "Exiting to prevent long waits."
                    )
                time.sleep(wait_time)
                self.total_sleep_this_run += wait_time

    def record_request(self):
        """Record that a request was made."""
        self.requests_this_run += 1
        self.request_times.append(time.time())
        self._save_state()

    def record_success(self, date_str: str = None):
        """Record a successful request."""
        if date_str:
            self.state["last_successful_date"] = date_str
        self.state["last_error"] = None
        self._save_state()

    def handle_rate_limit(self, response, attempt: int) -> Optional[float]:
        """
        Handle a 429 response with exponential backoff.

        Args:
            response: The HTTP response object
            attempt: Current retry attempt number (0-indexed)

        Returns:
            Sleep time in seconds, or None if max retries exceeded.
        """
        if attempt >= self.max_retries:
            return None

        # Check Retry-After header
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                wait_time = int(retry_after)
            except ValueError:
                # Might be a date string
                wait_time = DEFAULT_BASE_BACKOFF * (2 ** attempt)
        else:
            # Exponential backoff with jitter
            wait_time = DEFAULT_BASE_BACKOFF * (2 ** attempt)
            wait_time += random.uniform(0, wait_time * 0.1)  # 10% jitter

        # Check if this would exceed max total sleep
        if self.total_sleep_this_run + wait_time > self.max_total_sleep:
            # Set cooldown for future runs
            cooldown_until = datetime.utcnow() + timedelta(seconds=wait_time)
            self.state["cooldown_until_utc"] = cooldown_until.isoformat()
            self.state["last_error"] = f"429 rate limited, cooldown set until {cooldown_until.isoformat()}"
            self._save_state()
            return None

        return wait_time

    def set_cooldown(self, seconds: int):
        """Set a cooldown period for future runs."""
        cooldown_until = datetime.utcnow() + timedelta(seconds=seconds)
        self.state["cooldown_until_utc"] = cooldown_until.isoformat()
        self._save_state()

    def get_status(self) -> dict:
        """Get current rate limiter status."""
        return {
            "requests_this_run": self.requests_this_run,
            "max_requests_per_run": self.max_requests_per_run,
            "total_sleep_this_run": self.total_sleep_this_run,
            "cooldown_until": self.state.get("cooldown_until_utc"),
            "last_successful_date": self.state.get("last_successful_date"),
            "last_error": self.state.get("last_error"),
        }
