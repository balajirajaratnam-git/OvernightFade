"""
Automated IG Spread Collector (Step 6A automated)
==================================================
Connects to IG API, fetches bid/ask for weekly option strikes near ATM,
appends data to CSV. Run daily at ~21:00 UK during US market hours.

Usage:
    python scripts/data/collect_ig_spreads.py
    python scripts/data/collect_ig_spreads.py --dry-run   # print but don't save

Output: data/ig_spread_samples.csv (appended, never overwritten)

CSV columns:
  timestamp, day_of_week, market_status, underlying_bid, underlying_ask,
  underlying_mid, expiry_pattern, expiry_date, strike, option_type,
  strike_type, bid, ask, mid, spread_pts, spread_pct
"""
import sys
import json
import argparse
import csv
import os
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, 'src')
from rate_limiter import RateLimiter

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

UNDERLYING_EPIC = "IX.D.SPTRD.DAILY.IP"
OUTPUT_CSV = Path("data/ig_spread_samples.csv")

CSV_FIELDS = [
    "timestamp", "day_of_week", "market_status",
    "underlying_bid", "underlying_ask", "underlying_mid",
    "expiry_pattern", "expiry_date",
    "strike", "option_type", "strike_type",
    "bid", "ask", "mid", "spread_pts", "half_spread_pts", "spread_pct",
]

# Strike offsets to sample around ATM (in index points)
STRIKE_OFFSETS = {
    "ATM":    0,
    "ITM_10": -10,   # ITM for CALL (strike below spot)
    "OTM_10": +10,   # OTM for CALL (strike above spot)
    "ITM_25": -25,
    "OTM_25": +25,
}

# IG expiry patterns sampled every run.
# SPXWED is the confirmed primary target.
# SPXEMO / SPXEOM are the month-end Friday variants.
# SPXFRI is intentionally NOT listed here — it is probed separately via the
# discovery probe at the end of collect() so it is tried exactly once per run,
# not once per strike-type (which would waste ~10 API calls on a pattern that
# is unavailable most Sundays / month-end Fridays).
EXPIRY_PATTERNS = [
    ("SPXWED", "Wednesday weekly"),
    ("SPXEMO", "End-of-month near"),
    ("SPXEOM", "End-of-month far"),
]


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def load_credentials() -> dict:
    """Load IG API credentials from config/ig_api_credentials.json."""
    cred_path = Path("config/ig_api_credentials.json")
    if not cred_path.exists():
        raise FileNotFoundError(f"Credentials not found: {cred_path}")
    with open(cred_path) as f:
        creds = json.load(f)
    return creds["demo"]


# ---------------------------------------------------------------------------
# IG connection
# ---------------------------------------------------------------------------

def connect(creds: dict):
    """
    Connect to IG demo API and return the IGService instance.

    Args:
        creds: Dict with keys username, password, api_key, acc_type, acc_number.

    Returns:
        Connected IGService instance.
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
    return ig


def safe_logout(ig) -> None:
    """
    Disconnect from IG API, ignoring ApiExceededException and other errors.

    IG's logout endpoint sometimes raises ApiExceededException on demo accounts.
    """
    try:
        ig.logout()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Market data fetchers
# ---------------------------------------------------------------------------

def fetch_underlying(ig, limiter: RateLimiter) -> dict:
    """
    Fetch underlying US 500 bid/ask from IG.

    Args:
        ig: Connected IGService instance.
        limiter: RateLimiter instance.

    Returns:
        dict with keys: bid, ask, mid, status
    """
    limiter.check_cooldown()
    limiter.check_budget()
    limiter.wait_for_slot()

    info = ig.fetch_market_by_epic(UNDERLYING_EPIC)
    limiter.record_request()
    limiter.record_success()

    snap = info.get("snapshot", {})
    bid = float(snap.get("bid", 0))
    ask = float(snap.get("offer", 0))
    mid = (bid + ask) / 2 if (bid and ask) else 0
    status = snap.get("marketStatus", "UNKNOWN")

    return {"bid": bid, "ask": ask, "mid": mid, "status": status}


def fetch_option(ig, limiter: RateLimiter, epic: str) -> dict:
    """
    Fetch bid/ask for a single option epic from IG.

    Args:
        ig: Connected IGService instance.
        limiter: RateLimiter instance.
        epic: Full IG epic string (e.g. 'OP.D.SPXWED.6900C.IP').

    Returns:
        dict with keys: bid, ask, mid, spread_pts, spread_pct, status, expiry, name
        Returns None if epic not found or no bid/ask available.
    """
    limiter.check_cooldown()
    limiter.check_budget()
    limiter.wait_for_slot()

    try:
        info = ig.fetch_market_by_epic(epic)
        limiter.record_request()
        limiter.record_success()
    except Exception as e:
        limiter.record_request()
        err = str(e).lower()
        if any(x in err for x in ("not found", "invalid", "404", "no instrument")):
            return None  # Epic doesn't exist — expected for some patterns
        print(f"  [WARN] {epic}: {e}")
        return None

    snap = info.get("snapshot", {})
    inst = info.get("instrument", {})

    bid_raw = snap.get("bid")
    ask_raw = snap.get("offer")

    if bid_raw is None or ask_raw is None:
        return None

    bid = float(bid_raw)
    ask = float(ask_raw)
    mid = (bid + ask) / 2
    spread_pts = ask - bid
    spread_pct = (spread_pts / mid * 100) if mid > 0 else 0

    return {
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "spread_pts": spread_pts,
        "spread_pct": spread_pct,
        "status": snap.get("marketStatus", "UNKNOWN"),
        "expiry": inst.get("expiry", ""),
        "name": inst.get("name", ""),
    }


# ---------------------------------------------------------------------------
# ATM strike calculation
# ---------------------------------------------------------------------------

def compute_strikes(underlying_mid: float) -> dict:
    """
    Compute strike levels for each strike_type given the underlying mid price.

    Rounds to nearest 10 points for ATM, then applies offsets.

    Args:
        underlying_mid: Current underlying price.

    Returns:
        dict mapping strike_type -> strike level (int).
    """
    atm = round(underlying_mid / 10) * 10
    return {name: atm + offset for name, offset in STRIKE_OFFSETS.items()}


# ---------------------------------------------------------------------------
# CSV handling
# ---------------------------------------------------------------------------

def ensure_csv_header(path: Path) -> None:
    """Create CSV with header row if it doesn't exist."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()


def append_row(path: Path, row: dict) -> None:
    """Append a single row to the CSV (creating with header if needed)."""
    ensure_csv_header(path)
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writerow(row)


# ---------------------------------------------------------------------------
# Main collection logic
# ---------------------------------------------------------------------------

def collect(dry_run: bool = False) -> None:
    """
    Run the spread collection: connect to IG, fetch option prices, save to CSV.

    Args:
        dry_run: If True, print results but do not write to CSV.
    """
    print("=" * 70)
    print("  IG SPREAD COLLECTOR")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if dry_run:
        print("  [DRY RUN — no data will be saved]")
    print("=" * 70)
    print()

    # --- Rate limiter (conservative for IG API) ---
    limiter = RateLimiter(
        max_requests_per_run=30,        # We only need ~15 calls per run
        max_requests_per_minute=30,     # IG allows ~60/min; 30 is conservative
        max_retries=3,
        max_total_sleep=300,            # 5 min max wait total
    )

    # --- Connect ---
    creds = load_credentials()
    print("Connecting to IG demo API...")
    ig = connect(creds)
    print("[OK] Connected\n")

    # --- Timestamp and day ---
    now_utc = datetime.now(timezone.utc)
    timestamp = now_utc.isoformat()
    day_of_week = now_utc.strftime("%A")

    # --- Fetch underlying ---
    print(f"Fetching underlying ({UNDERLYING_EPIC})...")
    try:
        underlying = fetch_underlying(ig, limiter)
        print(f"  US 500: {underlying['mid']:.1f} "
              f"(bid {underlying['bid']:.1f}, ask {underlying['ask']:.1f}) "
              f"[{underlying['status']}]")
    except Exception as e:
        print(f"[FAIL] Could not fetch underlying: {e}")
        safe_logout(ig)
        return
    print()

    underlying_mid = underlying["mid"]
    strikes = compute_strikes(underlying_mid)
    print(f"ATM strike (rounded to nearest 10): {strikes['ATM']}")
    print(f"Strikes to test: {strikes}")
    print()

    # --- Collect options ---
    rows = []

    for pattern_code, pattern_label in EXPIRY_PATTERNS:
        print(f"--- {pattern_label} ({pattern_code}) ---")
        found_any = False

        for strike_type, strike in strikes.items():
            for option_type, suffix in [("CALL", "C"), ("PUT", "P")]:
                epic = f"OP.D.{pattern_code}.{strike}{suffix}.IP"

                result = fetch_option(ig, limiter, epic)

                if result is None:
                    # Expected for missing patterns — only log ATM failures
                    if strike_type == "ATM" and pattern_code == "SPXWED":
                        print(f"  [WARN] Core epic not found: {epic}")
                    continue

                found_any = True

                row = {
                    "timestamp": timestamp,
                    "day_of_week": day_of_week,
                    "market_status": result["status"],
                    "underlying_bid": round(underlying["bid"], 2),
                    "underlying_ask": round(underlying["ask"], 2),
                    "underlying_mid": round(underlying_mid, 2),
                    "expiry_pattern": pattern_code,
                    "expiry_date": result["expiry"],
                    "strike": strike,
                    "option_type": option_type,
                    "strike_type": strike_type,
                    "bid": round(result["bid"], 4),
                    "ask": round(result["ask"], 4),
                    "mid": round(result["mid"], 4),
                    "spread_pts": round(result["spread_pts"], 4),
                    "half_spread_pts": round(result["spread_pts"] / 2, 4),
                    "spread_pct": round(result["spread_pct"], 2),
                }
                rows.append(row)

                status_tag = f"[{result['status']}]"
                print(f"  {strike_type:7s} {option_type:4s} {strike:>5}  "
                      f"bid={result['bid']:.2f}  ask={result['ask']:.2f}  "
                      f"mid={result['mid']:.2f}  "
                      f"spread={result['spread_pts']:.2f}pts "
                      f"({result['spread_pct']:.1f}%)  {status_tag}")

        if not found_any:
            print(f"  [SKIP] No valid epics found for {pattern_code}")
        print()

    # --- SPXFRI discovery probe ---
    print("--- Friday weekly (SPXFRI) discovery probe ---")
    atm_strike = strikes["ATM"]
    fri_epic = f"OP.D.SPXFRI.{atm_strike}C.IP"
    result = fetch_option(ig, limiter, fri_epic)
    if result:
        print(f"  [FOUND] {fri_epic}: mid={result['mid']:.2f}, "
              f"spread={result['spread_pts']:.2f}pts")
    else:
        print(f"  [NOT FOUND] {fri_epic} — normal if this week is month-end")
    print()

    # --- Summary table ---
    print("=" * 70)
    print(f"  SUMMARY: {len(rows)} rows collected")
    print("=" * 70)
    print()

    if rows:
        # Group by strike_type for quick view
        from collections import defaultdict
        by_type = defaultdict(list)
        for r in rows:
            if r["market_status"] != "EDITS_ONLY":
                by_type[r["strike_type"]].append(r["spread_pts"])

        for st, spreads in sorted(by_type.items()):
            avg = sum(spreads) / len(spreads)
            print(f"  {st:8s}: n={len(spreads)}, avg spread = {avg:.3f} pts")
        print()

        if not dry_run:
            for r in rows:
                append_row(OUTPUT_CSV, r)
            print(f"[OK] Appended {len(rows)} rows to {OUTPUT_CSV}")
        else:
            print("[DRY RUN] Would have saved the above rows.")
    else:
        print("  No data collected. Market may be closed.")

    print()

    # --- Disconnect ---
    safe_logout(ig)
    print("[OK] Disconnected")
    print()

    # --- Rate limiter status ---
    status = limiter.get_status()
    print(f"Rate limiter: {status['requests_this_run']} requests used "
          f"(budget: {status['max_requests_per_run']})")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Collect IG option bid/ask spread samples",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print results without saving to CSV",
    )
    args = parser.parse_args()

    collect(dry_run=args.dry_run)
