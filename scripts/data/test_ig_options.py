"""
IG API Test — Step 2: Can we fetch ATM option prices?

The diagnostic showed the epic pattern:
  OP.D.SPXWED.{STRIKE}{C/P}.IP  — Wednesday expiry weekly

US 500 is at ~6924, so ATM strikes would be 6900 or 6925 (depending on granularity).
This script tries several epic patterns to find which ones IG accepts.

Usage:
    cd OvernightFade
    python scripts/data/test_ig_options.py
"""

import json
import sys
from pathlib import Path
from datetime import datetime

from trading_ig import IGService


def connect():
    """Connect to IG demo API."""
    # Try both credential file names
    for cred_name in ["ig_api_credentials.json", "ig_credentials.json"]:
        cred_path = Path(f"config/{cred_name}")
        if cred_path.exists():
            break
    else:
        print("[FAIL] No credentials file found")
        sys.exit(1)

    with open(cred_path) as f:
        creds = json.load(f)

    demo = creds["demo"]
    ig = IGService(
        username=demo["username"],
        password=demo["password"],
        api_key=demo["api_key"],
        acc_type=demo.get("acc_type", "DEMO"),
        acc_number=demo.get("acc_number"),
    )
    ig.create_session()
    print("[OK] Connected\n")
    return ig


def try_epic(ig, epic, label=""):
    """Try to fetch bid/ask for a given epic. Returns True if it works."""
    try:
        info = ig.fetch_market_by_epic(epic)
        snap = info.get("snapshot", {})
        inst = info.get("instrument", {})

        bid = snap.get("bid")
        ask = snap.get("offer")
        status = snap.get("marketStatus")
        name = inst.get("name", "N/A")
        expiry = inst.get("expiry", "N/A")

        if bid is not None and ask is not None:
            bid_f = float(bid)
            ask_f = float(ask)
            mid = (bid_f + ask_f) / 2
            spread = ask_f - bid_f
            spread_pct = (spread / mid * 100) if mid > 0 else 0

            print(f"  [OK] {label}")
            print(f"       Epic:    {epic}")
            print(f"       Name:    {name}")
            print(f"       Expiry:  {expiry}")
            print(f"       Status:  {status}")
            print(f"       Bid:     {bid_f:.2f}")
            print(f"       Ask:     {ask_f:.2f}")
            print(f"       Mid:     {mid:.2f}")
            print(f"       Spread:  {spread:.2f} pts ({spread_pct:.2f}%)")
            print()
            return True
        else:
            print(f"  [WARN] {label} — epic exists but no bid/ask (status: {status})")
            print(f"         Epic: {epic}, Name: {name}")
            print()
            return False

    except Exception as e:
        err = str(e)
        if "not found" in err.lower() or "invalid" in err.lower() or "404" in err:
            print(f"  [SKIP] {label} — epic not found: {epic}")
        else:
            print(f"  [FAIL] {label} — error: {e}")
        return False


def main():
    print("=" * 60)
    print("  IG OPTION PRICE TEST")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print()

    ig = connect()

    # First get the underlying price
    print("--- Underlying price ---")
    try:
        info = ig.fetch_market_by_epic("IX.D.SPTRD.DAILY.IP")
        snap = info["snapshot"]
        bid = float(snap["bid"])
        ask = float(snap["offer"])
        mid = (bid + ask) / 2
        print(f"  US 500: {mid:.1f} (bid {bid}, ask {ask})")
        print()
    except Exception as e:
        mid = 6924  # fallback from diagnostic
        print(f"  Using fallback: {mid}")
        print()

    # Calculate ATM strike candidates
    atm_round_100 = round(mid / 100) * 100       # e.g., 6900
    atm_round_50 = round(mid / 50) * 50           # e.g., 6950
    atm_round_25 = round(mid / 25) * 25           # e.g., 6925

    strikes_to_try = sorted(set([
        atm_round_100,
        atm_round_100 - 100,
        atm_round_100 + 100,
        atm_round_50,
        atm_round_25,
        int(round(mid)),
    ]))

    print(f"  ATM candidates: {strikes_to_try}")
    print()

    # Try different epic patterns for Wednesday weekly options
    print("=" * 60)
    print("  TEST A: Wednesday weekly options (SPXWED)")
    print("=" * 60)
    print()

    found_patterns = []
    for strike in strikes_to_try:
        for opt_type, suffix in [("CALL", "C"), ("PUT", "P")]:
            epic = f"OP.D.SPXWED.{strike}{suffix}.IP"
            label = f"Wed Weekly {strike} {opt_type}"
            if try_epic(ig, epic, label):
                found_patterns.append(epic)

    # Try Friday weekly pattern
    print("=" * 60)
    print("  TEST B: Friday weekly options (SPXFRI)")
    print("=" * 60)
    print()

    for strike in [atm_round_100]:
        for opt_type, suffix in [("CALL", "C"), ("PUT", "P")]:
            epic = f"OP.D.SPXFRI.{strike}{suffix}.IP"
            label = f"Fri Weekly {strike} {opt_type}"
            if try_epic(ig, epic, label):
                found_patterns.append(epic)

    # Try daily option pattern
    print("=" * 60)
    print("  TEST C: Daily options (various patterns)")
    print("=" * 60)
    print()

    for prefix in ["SPXDLY", "SPX0D", "SPXD", "SPXDAY"]:
        epic = f"OP.D.{prefix}.{atm_round_100}C.IP"
        label = f"Daily {prefix} {atm_round_100} CALL"
        if try_epic(ig, epic, label):
            found_patterns.append(epic)

    # Try the monthly/numbered patterns from the diagnostic
    print("=" * 60)
    print("  TEST D: Monthly options (SPX numbered patterns)")
    print("=" * 60)
    print()

    for n in ["3", "4", "5", "6"]:
        for strike in [atm_round_100]:
            for suffix in ["C", "P"]:
                epic = f"OP.D.SPX{n}.{strike}{suffix}.IP"
                label = f"SPX{n} {strike} {'CALL' if suffix == 'C' else 'PUT'}"
                if try_epic(ig, epic, label):
                    found_patterns.append(epic)

    # Try end-of-month
    print("=" * 60)
    print("  TEST E: End-of-month options")
    print("=" * 60)
    print()

    for prefix in ["SPXEOM", "SPXEMO"]:
        for strike in [atm_round_100]:
            for suffix in ["C", "P"]:
                epic = f"OP.D.{prefix}.{strike}{suffix}.IP"
                label = f"{prefix} {strike} {'CALL' if suffix == 'C' else 'PUT'}"
                if try_epic(ig, epic, label):
                    found_patterns.append(epic)

    # Also try a broader search for options near ATM
    print("=" * 60)
    print("  TEST F: Search for options near ATM")
    print("=" * 60)
    print()
    
    for search_term in [f"US 500 {atm_round_100}", f"US 500 Weekly {atm_round_100}", f"Weekly US 500"]:
        print(f"  Searching: '{search_term}'")
        try:
            results = ig.search_markets(search_term)
            if results is not None and len(results) > 0:
                for _, row in results.head(8).iterrows():
                    name = str(row.get("instrumentName", ""))
                    epic = row.get("epic", "")
                    print(f"    {name} — {epic}")
                    # Try to fetch price for any option result
                    if "OP." in str(epic):
                        try_epic(ig, epic, f"  → {name}")
            else:
                print("    No results")
        except Exception as e:
            print(f"    Error: {e}")
        print()

    # Summary
    print("=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print()

    if found_patterns:
        print(f"[OK] Found {len(found_patterns)} working option epic(s):")
        for ep in found_patterns:
            print(f"  {ep}")
        print()
        print("AUTOMATED SPREAD SAMPLING IS FEASIBLE.")
        print("The epic pattern for constructing option epics is confirmed.")
    else:
        print("[INFO] No ATM option epics returned bid/ask data.")
        print()
        print("Possible reasons:")
        print("  - Market is closed (status: EDITS_ONLY) — try during US trading hours")
        print("  - IG only shows prices for options during trading hours")
        print("  - The epic pattern for ATM strikes might differ")
        print()
        print("NEXT STEP: Re-run this script during US market hours")
        print("  (14:30 - 21:00 UK time, Monday - Friday)")
        print("  Option spreads are only meaningful when the market is live.")

    print()

    ig.logout()
    print("[OK] Disconnected")


if __name__ == "__main__":
    main()