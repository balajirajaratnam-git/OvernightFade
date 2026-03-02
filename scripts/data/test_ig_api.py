"""
IG API Diagnostic Test
======================
Run this to check whether automated spread sampling is feasible.
Tests each step independently and reports what works and what doesn't.

Usage:
    cd OvernightFade
    python scripts/data/test_ig_api.py

Prerequisites:
    1. pip install trading-ig
    2. Create config/ig_api_credentials.json (see below for format)
    3. IG demo account with API key from https://labs.ig.com/

Credential file format (config/ig_api_credentials.json):
{
    "demo": {
        "username": "your_demo_username",
        "password": "your_demo_password",
        "api_key": "your_api_key_from_ig_labs",
        "acc_type": "DEMO",
        "acc_number": "your_demo_account_number"
    }
}

NOTE: The credential template in this repo has a DIFFERENT flat format.
      Use the nested format above — the connector requires it.
"""

import sys
import os
import json
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# Step 0: Check prerequisites
# ---------------------------------------------------------------------------

def check_prerequisites():
    """Check everything is in place before attempting API calls."""
    print("=" * 60)
    print("STEP 0: PREREQUISITES CHECK")
    print("=" * 60)
    print()
    
    issues = []
    
    # Check trading-ig installed
    try:
        from trading_ig import IGService
        print("[OK] trading-ig package installed")
    except ImportError:
        print("[FAIL] trading-ig not installed")
        print("  Fix: pip install trading-ig")
        issues.append("trading-ig")
    
    # Check credentials file exists
    cred_path = Path("config/ig_api_credentials.json")
    template_path = Path("config/ig_api_credentials.json.template")
    
    if cred_path.exists():
        print(f"[OK] Credentials file found: {cred_path}")
        
        # Check format
        try:
            with open(cred_path) as f:
                creds = json.load(f)
            
            if "demo" in creds and isinstance(creds["demo"], dict):
                demo = creds["demo"]
                required = ["username", "password", "api_key"]
                missing = [k for k in required if k not in demo or demo[k].startswith("YOUR_")]
                
                if missing:
                    print(f"[FAIL] Credentials incomplete — missing or placeholder: {missing}")
                    issues.append("credentials_incomplete")
                else:
                    print("[OK] Credentials format correct (nested 'demo' structure)")
                    
            elif "api_key" in creds and "username" in creds:
                # Flat format from template — won't work with connector
                print("[FAIL] Credentials use FLAT format (from template)")
                print("  The connector expects NESTED format.")
                print("  Fix: restructure config/ig_api_credentials.json like this:")
                print()
                print('  {')
                print('    "demo": {')
                print(f'      "username": "{creds.get("username", "YOUR_USERNAME")}",')
                print(f'      "password": "{creds.get("password", "YOUR_PASSWORD")}",')
                print(f'      "api_key": "{creds.get("api_key", "YOUR_API_KEY")}",')
                print('      "acc_type": "DEMO",')
                print(f'      "acc_number": "{creds.get("account_id", "YOUR_ACCOUNT_ID")}"')
                print('    }')
                print('  }')
                print()
                issues.append("credentials_format")
            else:
                print("[FAIL] Credentials format unrecognised")
                issues.append("credentials_format")
                
        except json.JSONDecodeError:
            print("[FAIL] Credentials file is not valid JSON")
            issues.append("credentials_json")
    else:
        print(f"[FAIL] No credentials file at {cred_path}")
        if template_path.exists():
            print(f"  Template exists at {template_path}")
            print("  Fix: Copy template, rename, fill in your details")
            print("  BUT use the NESTED format (see top of this script)")
        else:
            print("  Create config/ig_api_credentials.json (see top of this script for format)")
        issues.append("credentials_missing")
    
    print()
    
    if issues:
        print(f"[BLOCKED] {len(issues)} issue(s) must be fixed before API test")
        print(f"  Issues: {', '.join(issues)}")
        return False
    else:
        print("[READY] All prerequisites met — proceeding to API test")
        return True


# ---------------------------------------------------------------------------
# Step 1: Test connection
# ---------------------------------------------------------------------------

def test_connection():
    """Test basic API connectivity."""
    print()
    print("=" * 60)
    print("STEP 1: CONNECTION TEST")
    print("=" * 60)
    print()
    
    from trading_ig import IGService
    
    cred_path = Path("config/ig_api_credentials.json")
    with open(cred_path) as f:
        creds = json.load(f)
    
    demo = creds["demo"]
    
    try:
        ig = IGService(
            username=demo["username"],
            password=demo["password"],
            api_key=demo["api_key"],
            acc_type=demo.get("acc_type", "DEMO"),
            acc_number=demo.get("acc_number")
        )
        
        ig.create_session()
        print("[OK] Connected to IG demo API")
        return ig
        
    except Exception as e:
        print(f"[FAIL] Connection failed: {e}")
        print()
        if "Invalid credentials" in str(e) or "authentication" in str(e).lower():
            print("  Check: username, password, and API key are correct")
            print("  Check: API key is for DEMO environment (not live)")
            print("  Check: Demo account is active at https://demo-api.ig.com")
        elif "rate" in str(e).lower() or "limit" in str(e).lower():
            print("  Check: You may have hit the API rate limit")
            print("  Wait 60 seconds and try again")
        else:
            print(f"  Full error: {repr(e)}")
        return None


# ---------------------------------------------------------------------------
# Step 2: Search for US 500 market
# ---------------------------------------------------------------------------

def test_market_search(ig):
    """Search for US 500 to find the epic."""
    print()
    print("=" * 60)
    print("STEP 2: MARKET SEARCH")
    print("=" * 60)
    print()
    
    try:
        results = ig.search_markets("US 500")
        
        if results is not None and len(results) > 0:
            print(f"[OK] Found {len(results)} market(s):")
            for i, row in results.head(5).iterrows():
                print(f"  {i}: {row.get('instrumentName', 'N/A')} — epic: {row.get('epic', 'N/A')}")
            
            # Find the main US 500 (not options)
            epics = {}
            for _, row in results.iterrows():
                name = str(row.get('instrumentName', ''))
                epic = row.get('epic', '')
                if 'US 500' in name:
                    epics[name] = epic
            
            print()
            print(f"  Relevant epics: {json.dumps(epics, indent=4)}")
            return epics
        else:
            print("[FAIL] No results for 'US 500'")
            return None
            
    except Exception as e:
        print(f"[FAIL] Market search error: {e}")
        return None


# ---------------------------------------------------------------------------
# Step 3: Fetch market prices (bid/ask/mid)
# ---------------------------------------------------------------------------

def test_market_price(ig, epic):
    """Fetch bid/ask/mid for a given epic."""
    print()
    print("=" * 60)
    print(f"STEP 3: FETCH MARKET PRICE — {epic}")
    print("=" * 60)
    print()
    
    try:
        market_info = ig.fetch_market_by_epic(epic)
        
        snapshot = market_info.get('snapshot', {})
        instrument = market_info.get('instrument', {})
        
        bid = snapshot.get('bid')
        ask = snapshot.get('offer')
        market_status = snapshot.get('marketStatus')
        
        print(f"[OK] Market data received:")
        print(f"  Instrument: {instrument.get('name', 'N/A')}")
        print(f"  Status:     {market_status}")
        print(f"  Bid:        {bid}")
        print(f"  Ask/Offer:  {ask}")
        
        if bid and ask:
            bid_f = float(bid)
            ask_f = float(ask)
            mid = (bid_f + ask_f) / 2
            spread = ask_f - bid_f
            print(f"  Mid:        {mid:.2f}")
            print(f"  Spread:     {spread:.2f} pts")
            print(f"  Spread %:   {(spread / mid * 100):.3f}%")
        
        # Also dump the keys available for debugging
        print()
        print(f"  Snapshot keys: {list(snapshot.keys())}")
        print(f"  Instrument keys: {list(instrument.keys())}")
        
        return market_info
        
    except Exception as e:
        print(f"[FAIL] Price fetch error: {e}")
        print(f"  Full error: {repr(e)}")
        return None


# ---------------------------------------------------------------------------
# Step 4: Test option chain access
# ---------------------------------------------------------------------------

def test_option_chain(ig):
    """Try to access US 500 options — this is the critical test."""
    print()
    print("=" * 60)
    print("STEP 4: OPTION CHAIN ACCESS (critical for spread sampling)")
    print("=" * 60)
    print()
    
    # Try multiple approaches since IG option API can be tricky
    
    # Approach A: Search for option-specific terms
    print("Approach A: Search for 'US 500 Weekly'...")
    try:
        results = ig.search_markets("US 500 Weekly")
        if results is not None and len(results) > 0:
            print(f"[OK] Found {len(results)} result(s):")
            for i, row in results.head(10).iterrows():
                name = str(row.get('instrumentName', ''))
                epic = row.get('epic', '')
                print(f"  {name} — {epic}")
        else:
            print("[INFO] No results for 'US 500 Weekly'")
    except Exception as e:
        print(f"[FAIL] Search error: {e}")
    
    print()
    
    # Approach B: Search for specific option terms
    print("Approach B: Search for 'US 500 Call'...")
    try:
        results = ig.search_markets("US 500 Call")
        if results is not None and len(results) > 0:
            print(f"[OK] Found {len(results)} result(s):")
            for i, row in results.head(10).iterrows():
                name = str(row.get('instrumentName', ''))
                epic = row.get('epic', '')
                print(f"  {name} — {epic}")
        else:
            print("[INFO] No results for 'US 500 Call'")
    except Exception as e:
        print(f"[FAIL] Search error: {e}")
    
    print()
    
    # Approach C: Try fetch_market_by_epic with known option epic patterns
    # IG US 500 option epics typically follow patterns like:
    # OPT-US500-YYYYMMDD-STRIKE-CALL/PUT
    print("Approach C: Try IG REST API /markets endpoint for option navigation...")
    try:
        # The trading-ig library has a method to browse market hierarchy
        # This might give us the option chain navigation
        node = ig.fetch_top_level_navigation()
        if node is not None:
            print(f"[OK] Top-level navigation received")
            if hasattr(node, 'keys'):
                print(f"  Keys: {list(node.keys())}")
            # Try to print the structure
            if isinstance(node, dict):
                nodes = node.get('nodes', [])
                for n in nodes[:10]:
                    print(f"  Node: {n.get('name', 'N/A')} — id: {n.get('id', 'N/A')}")
        else:
            print("[INFO] Navigation returned None")
    except Exception as e:
        print(f"[INFO] Navigation not available: {e}")
    
    print()
    print("-" * 60)
    print("OPTION CHAIN SUMMARY:")
    print("-" * 60)
    print()
    print("If Approaches A/B found option epics with bid/ask prices,")
    print("automated spread sampling IS feasible.")
    print()
    print("If nothing was found, we either need to:")
    print("  1. Navigate the IG market tree to find option epics, or")
    print("  2. Use the IG web platform's REST calls (not the public API), or")
    print("  3. Fall back to screenshot method")
    print()


# ---------------------------------------------------------------------------
# Step 5: Clean disconnect
# ---------------------------------------------------------------------------

def test_disconnect(ig):
    """Clean logout."""
    print()
    print("=" * 60)
    print("STEP 5: DISCONNECT")
    print("=" * 60)
    print()
    try:
        ig.logout()
        print("[OK] Disconnected cleanly")
    except Exception as e:
        print(f"[WARN] Disconnect issue (non-critical): {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print()
    print("=" * 60)
    print("  IG API DIAGNOSTIC TEST")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print()
    print("This tests whether automated spread sampling is feasible.")
    print("It will NOT place any orders or modify your account.")
    print()
    
    # Step 0: Prerequisites
    if not check_prerequisites():
        print()
        print("Fix the issues above and re-run this script.")
        sys.exit(1)
    
    # Step 1: Connect
    ig = test_connection()
    if ig is None:
        print()
        print("Cannot proceed without connection. Fix and re-run.")
        sys.exit(1)
    
    # Step 2: Market search
    epics = test_market_search(ig)
    
    # Step 3: Fetch a price (use first epic found, or try common ones)
    if epics:
        first_epic = list(epics.values())[0]
        test_market_price(ig, first_epic)
    else:
        # Try known common epic
        print()
        print("Trying common epic 'IX.D.SPTRD.DAILY.IP' (US 500 spread bet)...")
        test_market_price(ig, "IX.D.SPTRD.DAILY.IP")
    
    # Step 4: Option chain (the critical test)
    test_option_chain(ig)
    
    # Step 5: Disconnect
    test_disconnect(ig)
    
    # Final summary
    print()
    print("=" * 60)
    print("  DIAGNOSTIC COMPLETE")
    print("=" * 60)
    print()
    print("Copy the full output above and share it.")
    print("That will tell us exactly which automation path to take.")
    print()


if __name__ == "__main__":
    main()
