"""
Transaction cost models for OvernightFade backtesting.

Single source of truth for how costs are applied to option premiums.
All cost logic must go through one of these models — no other cost
adjustments should exist in the backtest runner.

Three models:
  1. PercentPremiumCostModel       — costs as a fraction of the option mid premium
  2. FixedPointCostModel           — costs as fixed point amounts on the premium
  3. CalibratedFixedPointCostModel — JSON-driven fixed-point spreads by bucket

All expose the same interface:
  apply_entry(mid) -> (fill, breakdown)
  apply_exit(mid)  -> (fill, breakdown)
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class PercentPremiumCostModel:
    """
    Percentage-based cost model.

    Costs are expressed as fractions of the option mid premium per side:
      half_spread_pct:  half the bid-ask spread (applied each side)
      slippage_pct:     slippage buffer (applied each side)

    Entry (buy-to-open): fill = mid * (1 + half_spread_pct + slippage_pct)
    Exit  (sell-to-close): fill = mid * (1 - half_spread_pct - slippage_pct)

    Example: half_spread_pct=0.02, slippage_pct=0.005
      Entry: pay mid * 1.025
      Exit:  receive mid * 0.975
      Total round-trip cost: ~5% of mid premium
    """
    half_spread_pct: float = 0.02
    slippage_pct: float = 0.005

    @property
    def one_side_pct(self) -> float:
        """Total one-side cost as a fraction of mid."""
        return self.half_spread_pct + self.slippage_pct

    @property
    def roundtrip_pct(self) -> float:
        """Approximate round-trip cost as fraction of mid (2 * one_side)."""
        return self.one_side_pct * 2

    def apply_entry(self, mid: float) -> tuple:
        """
        Apply costs to a buy-to-open trade.

        Args:
            mid: Black-Scholes mid premium at entry.

        Returns:
            (fill_premium, breakdown_dict)
        """
        spread_component = mid * self.half_spread_pct
        slippage_component = mid * self.slippage_pct
        fill = mid + spread_component + slippage_component
        total_cost_pts = fill - mid
        total_cost_pct = total_cost_pts / mid * 100 if mid > 0 else 0.0

        return fill, {
            "mid": round(mid, 6),
            "fill": round(fill, 6),
            "spread_component": round(spread_component, 6),
            "slippage_component": round(slippage_component, 6),
            "total_cost_pts": round(total_cost_pts, 6),
            "total_cost_pct": round(total_cost_pct, 4),
        }

    def apply_exit(self, mid: float) -> tuple:
        """
        Apply costs to a sell-to-close trade.

        Args:
            mid: Black-Scholes mid premium at exit.

        Returns:
            (fill_premium, breakdown_dict)
        """
        spread_component = mid * self.half_spread_pct
        slippage_component = mid * self.slippage_pct
        fill = mid - spread_component - slippage_component
        fill = max(fill, 0.0)  # Never go below zero
        total_cost_pts = mid - fill
        total_cost_pct = total_cost_pts / mid * 100 if mid > 0 else 0.0

        return fill, {
            "mid": round(mid, 6),
            "fill": round(fill, 6),
            "spread_component": round(spread_component, 6),
            "slippage_component": round(slippage_component, 6),
            "total_cost_pts": round(total_cost_pts, 6),
            "total_cost_pct": round(total_cost_pct, 4),
        }

    def describe(self) -> dict:
        """Return a dict describing this model for JSON serialisation."""
        return {
            "type": "percent",
            "half_spread_pct": self.half_spread_pct,
            "slippage_pct": self.slippage_pct,
            "one_side_pct": self.one_side_pct,
            "roundtrip_pct": self.roundtrip_pct,
        }


@dataclass
class FixedPointCostModel:
    """
    Fixed-point cost model (closer to IG reality when calibrated).

    Costs are fixed point amounts added/subtracted from the option mid premium:
      half_spread_pts:  half the bid-ask spread in points (each side)
      slippage_pts:     slippage buffer in points (each side)

    Entry (buy-to-open): fill = mid + half_spread_pts + slippage_pts
    Exit  (sell-to-close): fill = max(mid - half_spread_pts - slippage_pts, 0)

    Example: half_spread_pts=0.10, slippage_pts=0.00
      Entry: pay mid + 0.10
      Exit:  receive max(mid - 0.10, 0)
      Total round-trip cost: 0.20 points regardless of premium size
    """
    half_spread_pts: float = 0.10
    slippage_pts: float = 0.00

    @property
    def one_side_pts(self) -> float:
        """Total one-side cost in points."""
        return self.half_spread_pts + self.slippage_pts

    @property
    def roundtrip_pts(self) -> float:
        """Total round-trip cost in points."""
        return self.one_side_pts * 2

    def apply_entry(self, mid: float) -> tuple:
        """
        Apply costs to a buy-to-open trade.

        Args:
            mid: Black-Scholes mid premium at entry.

        Returns:
            (fill_premium, breakdown_dict)
        """
        spread_component = self.half_spread_pts
        slippage_component = self.slippage_pts
        fill = mid + spread_component + slippage_component
        total_cost_pts = fill - mid
        total_cost_pct = total_cost_pts / mid * 100 if mid > 0 else 0.0

        return fill, {
            "mid": round(mid, 6),
            "fill": round(fill, 6),
            "spread_component": round(spread_component, 6),
            "slippage_component": round(slippage_component, 6),
            "total_cost_pts": round(total_cost_pts, 6),
            "total_cost_pct": round(total_cost_pct, 4),
        }

    def apply_exit(self, mid: float) -> tuple:
        """
        Apply costs to a sell-to-close trade.

        Args:
            mid: Black-Scholes mid premium at exit.

        Returns:
            (fill_premium, breakdown_dict)
        """
        spread_component = self.half_spread_pts
        slippage_component = self.slippage_pts
        fill = mid - spread_component - slippage_component
        fill = max(fill, 0.0)  # Never go below zero
        total_cost_pts = mid - fill
        total_cost_pct = total_cost_pts / mid * 100 if mid > 0 else 0.0

        return fill, {
            "mid": round(mid, 6),
            "fill": round(fill, 6),
            "spread_component": round(spread_component, 6),
            "slippage_component": round(slippage_component, 6),
            "total_cost_pts": round(total_cost_pts, 6),
            "total_cost_pct": round(total_cost_pct, 4),
        }

    def describe(self) -> dict:
        """Return a dict describing this model for JSON serialisation."""
        return {
            "type": "fixed",
            "half_spread_pts": self.half_spread_pts,
            "slippage_pts": self.slippage_pts,
            "one_side_pts": self.one_side_pts,
            "roundtrip_pts": self.roundtrip_pts,
        }


# ---------------------------------------------------------------------------
# Calibration file loader
# ---------------------------------------------------------------------------

def load_cost_calibration(path: str) -> dict:
    """
    Load and validate a cost calibration JSON file.

    Supports two schema versions:
      v1.x — flat defaults + buckets (Step 7 template format)
      v2.x — nested spreads by expiry_pattern / time_bucket / strike_type

    Args:
        path: Path to the calibration JSON file.

    Returns:
        Parsed dict.

    Raises:
        FileNotFoundError: if file does not exist.
        ValueError: if required fields are missing.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Calibration file not found: {path}")

    with open(p, 'r') as f:
        cal = json.load(f)

    if "version" not in cal:
        raise ValueError(f"Calibration file missing 'version': {path}")

    version = cal["version"]

    if version.startswith("2"):
        # v2.x: requires global_defaults and spreads
        if "global_defaults" not in cal:
            raise ValueError(f"v2 calibration missing 'global_defaults': {path}")
        gd = cal["global_defaults"]
        if "half_spread_pts" not in gd:
            raise ValueError(
                f"v2 global_defaults missing 'half_spread_pts': {path}"
            )
        if "spreads" not in cal:
            cal["spreads"] = {}
    else:
        # v1.x: requires defaults with entry/exit keys
        if "defaults" not in cal:
            raise ValueError(f"Calibration file missing 'defaults': {path}")
        defaults = cal["defaults"]
        for required_key in ("half_spread_pts_entry", "half_spread_pts_exit"):
            if required_key not in defaults:
                raise ValueError(
                    f"Calibration defaults missing '{required_key}': {path}"
                )
        if "buckets" not in cal:
            cal["buckets"] = {}

    return cal


# ---------------------------------------------------------------------------
# v2 spread resolution helpers
# ---------------------------------------------------------------------------

def _resolve_v2_half_spread(
    cal: dict,
    expiry_pattern: Optional[str],
    time_bucket: str,
    strike_type: str,
) -> tuple:
    """
    Resolve a single half_spread_pts from a v2 calibration dict.

    Fallback chain (stops at first hit):
      1. spreads[expiry_pattern][time_bucket][strike_type]["half_spread_pts"]
      2. spreads[expiry_pattern][time_bucket]["ATM"]["half_spread_pts"]
      3. spreads[expiry_pattern]["ENTRY"][strike_type]["half_spread_pts"]
      4. spreads[expiry_pattern]["ENTRY"]["ATM"]["half_spread_pts"]
      5. global_defaults["half_spread_pts"]

    If expiry_pattern is None, skip steps 1-4 and go straight to 5.

    Returns:
        (half_spread_pts, source_description_string, sample_count_or_None)
    """
    spreads = cal.get("spreads", {})
    fallback_val = cal["global_defaults"]["half_spread_pts"]
    fallback_slip = cal["global_defaults"].get("slippage_pts", 0.0)

    if expiry_pattern is None:
        return fallback_val, "global_defaults", None

    ep_data = spreads.get(expiry_pattern)
    if ep_data is None:
        return fallback_val, f"global_defaults (no pattern '{expiry_pattern}')", None

    # Try exact time_bucket + strike_type
    tb_data = ep_data.get(time_bucket)
    if tb_data and strike_type in tb_data:
        node = tb_data[strike_type]
        return (
            node["half_spread_pts"],
            f"{expiry_pattern}/{time_bucket}/{strike_type}",
            node.get("n"),
        )

    # Try exact time_bucket + ATM fallback
    if tb_data and "ATM" in tb_data:
        node = tb_data["ATM"]
        return (
            node["half_spread_pts"],
            f"{expiry_pattern}/{time_bucket}/ATM (fallback from {strike_type})",
            node.get("n"),
        )

    # Try ENTRY time_bucket + strike_type (e.g. EXIT missing, reuse ENTRY)
    entry_data = ep_data.get("ENTRY")
    if entry_data and strike_type in entry_data:
        node = entry_data[strike_type]
        return (
            node["half_spread_pts"],
            f"{expiry_pattern}/ENTRY/{strike_type} (fallback from {time_bucket})",
            node.get("n"),
        )

    # Try ENTRY time_bucket + ATM
    if entry_data and "ATM" in entry_data:
        node = entry_data["ATM"]
        return (
            node["half_spread_pts"],
            f"{expiry_pattern}/ENTRY/ATM (fallback from {time_bucket}/{strike_type})",
            node.get("n"),
        )

    return fallback_val, f"global_defaults (no data for {expiry_pattern})", None


def _resolve_v2_half_spread_with_path(
    cal: dict,
    expiry_pattern: Optional[str],
    time_bucket: str,
    strike_type: str,
) -> tuple:
    """
    Like _resolve_v2_half_spread but also returns the full resolution path.

    Returns:
        (half_spread_pts, source_string, sample_count_or_None, resolution_path)
        resolution_path is a string like:
          "SPXWED/TIME_0830/ATM -> SPXWED/EXIT/ATM -> SPXWED/ENTRY/ATM"
    """
    spreads = cal.get("spreads", {})
    fallback_val = cal["global_defaults"]["half_spread_pts"]
    attempted = []

    if expiry_pattern is None:
        attempted.append("global_defaults")
        return fallback_val, "global_defaults", None, " -> ".join(attempted)

    ep_data = spreads.get(expiry_pattern)
    if ep_data is None:
        attempted.append(f"{expiry_pattern} (missing)")
        attempted.append("global_defaults")
        return fallback_val, f"global_defaults (no pattern '{expiry_pattern}')", None, " -> ".join(attempted)

    # Try exact time_bucket + strike_type
    attempted.append(f"{expiry_pattern}/{time_bucket}/{strike_type}")
    tb_data = ep_data.get(time_bucket)
    if tb_data and strike_type in tb_data:
        node = tb_data[strike_type]
        return (
            node["half_spread_pts"],
            f"{expiry_pattern}/{time_bucket}/{strike_type}",
            node.get("n"),
            " -> ".join(attempted),
        )

    # Try exact time_bucket + ATM fallback (skip if strike_type already ATM)
    if strike_type != "ATM":
        attempted.append(f"{expiry_pattern}/{time_bucket}/ATM")
        if tb_data and "ATM" in tb_data:
            node = tb_data["ATM"]
            return (
                node["half_spread_pts"],
                f"{expiry_pattern}/{time_bucket}/ATM (fallback from {strike_type})",
                node.get("n"),
                " -> ".join(attempted),
            )

    # Try ENTRY time_bucket + strike_type
    entry_data = ep_data.get("ENTRY")
    attempted.append(f"{expiry_pattern}/ENTRY/{strike_type}")
    if entry_data and strike_type in entry_data:
        node = entry_data[strike_type]
        return (
            node["half_spread_pts"],
            f"{expiry_pattern}/ENTRY/{strike_type} (fallback from {time_bucket})",
            node.get("n"),
            " -> ".join(attempted),
        )

    # Try ENTRY time_bucket + ATM (skip if strike_type already ATM)
    if strike_type != "ATM":
        attempted.append(f"{expiry_pattern}/ENTRY/ATM")
    if entry_data and "ATM" in entry_data:
        node = entry_data["ATM"]
        return (
            node["half_spread_pts"],
            f"{expiry_pattern}/ENTRY/ATM (fallback from {time_bucket}/{strike_type})",
            node.get("n"),
            " -> ".join(attempted),
        )

    attempted.append("global_defaults")
    return fallback_val, f"global_defaults (no data for {expiry_pattern})", None, " -> ".join(attempted)


# ---------------------------------------------------------------------------
# Calibrated fixed-point cost model (JSON-driven)
# ---------------------------------------------------------------------------

@dataclass
class CalibratedFixedPointCostModel:
    """
    JSON-driven fixed-point cost model with separate entry/exit spreads.

    Supports two calibration schema versions:

    v1.x (Step 7 template): flat defaults + buckets keyed by strike_type.
      Constructor params: calibration_path, bucket.

    v2.x (Step 10): nested spreads by expiry_pattern / time_bucket /
      strike_type. Enables different spreads per expiry and per time window.
      Constructor params: calibration_path, bucket, expiry_pattern_filter,
      time_bucket_entry, time_bucket_exit.

    Fallback chain for v2:
      1. Exact: spreads[pattern][time_bucket][strike_type]
      2. ATM fallback: spreads[pattern][time_bucket]["ATM"]
      3. ENTRY fallback: spreads[pattern]["ENTRY"][strike_type]
      4. ENTRY ATM: spreads[pattern]["ENTRY"]["ATM"]
      5. global_defaults

    Interface is identical to PercentPremiumCostModel / FixedPointCostModel.
    """
    calibration_path: str = ""
    bucket: str = "ATM"
    expiry_pattern_filter: Optional[str] = None
    time_bucket_entry: str = "ENTRY"
    time_bucket_exit: str = "EXIT"

    # Resolved parameters (set in __post_init__)
    half_spread_pts_entry: float = field(init=False, default=0.0)
    half_spread_pts_exit: float = field(init=False, default=0.0)
    slippage_pts_entry: float = field(init=False, default=0.0)
    slippage_pts_exit: float = field(init=False, default=0.0)
    calibration_version: str = field(init=False, default="")
    _calibration: dict = field(init=False, default_factory=dict, repr=False)
    _entry_source: str = field(init=False, default="")
    _exit_source: str = field(init=False, default="")
    _entry_n: Optional[int] = field(init=False, default=None)
    _exit_n: Optional[int] = field(init=False, default=None)

    def __post_init__(self):
        cal = load_cost_calibration(self.calibration_path)
        self._calibration = cal
        self.calibration_version = cal.get("version", "unknown")

        if self.calibration_version.startswith("2"):
            self._resolve_v2(cal)
        else:
            self._resolve_v1(cal)

    def _resolve_v1(self, cal: dict):
        """Resolve spreads from v1.x schema (flat defaults + buckets)."""
        defaults = cal["defaults"]
        params = {
            "half_spread_pts_entry": defaults["half_spread_pts_entry"],
            "half_spread_pts_exit": defaults["half_spread_pts_exit"],
            "slippage_pts_entry": defaults.get("slippage_pts_entry", 0.0),
            "slippage_pts_exit": defaults.get("slippage_pts_exit", 0.0),
        }

        buckets = cal.get("buckets", {})
        if self.bucket in buckets:
            for key, val in buckets[self.bucket].items():
                if key in params:
                    params[key] = val

        self.half_spread_pts_entry = params["half_spread_pts_entry"]
        self.half_spread_pts_exit = params["half_spread_pts_exit"]
        self.slippage_pts_entry = params["slippage_pts_entry"]
        self.slippage_pts_exit = params["slippage_pts_exit"]
        self._entry_source = f"v1/buckets/{self.bucket}" if self.bucket in buckets else "v1/defaults"
        self._exit_source = self._entry_source

    def _resolve_v2(self, cal: dict):
        """Resolve spreads from v2.x schema (nested by pattern/time/strike)."""
        gd = cal["global_defaults"]
        self.slippage_pts_entry = gd.get("slippage_pts", 0.0)
        self.slippage_pts_exit = gd.get("slippage_pts", 0.0)

        # Resolve entry half-spread
        entry_hs, entry_src, entry_n = _resolve_v2_half_spread(
            cal, self.expiry_pattern_filter,
            self.time_bucket_entry, self.bucket,
        )
        self.half_spread_pts_entry = entry_hs
        self._entry_source = entry_src
        self._entry_n = entry_n

        # Resolve exit half-spread
        exit_hs, exit_src, exit_n = _resolve_v2_half_spread(
            cal, self.expiry_pattern_filter,
            self.time_bucket_exit, self.bucket,
        )
        self.half_spread_pts_exit = exit_hs
        self._exit_source = exit_src
        self._exit_n = exit_n

    @property
    def entry_cost_pts(self) -> float:
        """Total one-side entry cost in points."""
        return self.half_spread_pts_entry + self.slippage_pts_entry

    @property
    def exit_cost_pts(self) -> float:
        """Total one-side exit cost in points."""
        return self.half_spread_pts_exit + self.slippage_pts_exit

    @property
    def roundtrip_pts(self) -> float:
        """Total round-trip cost in points."""
        return self.entry_cost_pts + self.exit_cost_pts

    def apply_entry(self, mid: float) -> tuple:
        """Apply calibrated entry costs (buy-to-open)."""
        spread_component = self.half_spread_pts_entry
        slippage_component = self.slippage_pts_entry
        fill = mid + spread_component + slippage_component
        total_cost_pts = fill - mid
        total_cost_pct = total_cost_pts / mid * 100 if mid > 0 else 0.0

        return fill, {
            "mid": round(mid, 6),
            "fill": round(fill, 6),
            "spread_component": round(spread_component, 6),
            "slippage_component": round(slippage_component, 6),
            "total_cost_pts": round(total_cost_pts, 6),
            "total_cost_pct": round(total_cost_pct, 4),
        }

    def apply_exit(self, mid: float) -> tuple:
        """Apply calibrated exit costs (sell-to-close)."""
        spread_component = self.half_spread_pts_exit
        slippage_component = self.slippage_pts_exit
        fill = mid - spread_component - slippage_component
        fill = max(fill, 0.0)
        total_cost_pts = mid - fill
        total_cost_pct = total_cost_pts / mid * 100 if mid > 0 else 0.0

        return fill, {
            "mid": round(mid, 6),
            "fill": round(fill, 6),
            "spread_component": round(spread_component, 6),
            "slippage_component": round(slippage_component, 6),
            "total_cost_pts": round(total_cost_pts, 6),
            "total_cost_pct": round(total_cost_pct, 4),
        }

    def apply_exit_dynamic(self, mid: float, time_bucket_override: str) -> tuple:
        """
        Apply exit costs with a dynamic time bucket (for TP-anytime scanning).

        Instead of using the pre-resolved half_spread_pts_exit, resolves
        the exit spread on-the-fly from the calibration data using
        time_bucket_override.

        For v1 calibrations (no time-bucket concept), falls back to
        the standard apply_exit() method.

        Args:
            mid: BS mid premium at the candidate exit time.
            time_bucket_override: e.g. "EXIT", "TIME_0200", "TIME_0600",
                                  "TIME_0830", "TIME_1600_2000", etc.

        Returns:
            (fill_premium, breakdown_dict) — same format as apply_exit(),
            with additional "source" and "time_bucket" keys in breakdown.
        """
        if not self.calibration_version.startswith("2"):
            fill, bd = self.apply_exit(mid)
            bd["source"] = self._exit_source
            bd["time_bucket"] = "EXIT"
            bd["fallback_used"] = False
            return fill, bd

        hs, source, n = _resolve_v2_half_spread(
            self._calibration,
            self.expiry_pattern_filter,
            time_bucket_override,
            self.bucket,
        )
        slippage = self._calibration["global_defaults"].get("slippage_pts", 0.0)

        fill = mid - hs - slippage
        fill = max(fill, 0.0)
        total_cost_pts = mid - fill
        total_cost_pct = total_cost_pts / mid * 100 if mid > 0 else 0.0

        # Determine if fallback was used (source doesn't exactly match request)
        exact_match = source == (
            f"{self.expiry_pattern_filter}/{time_bucket_override}/{self.bucket}"
        )

        return fill, {
            "mid": round(mid, 6),
            "fill": round(fill, 6),
            "spread_component": round(hs, 6),
            "slippage_component": round(slippage, 6),
            "total_cost_pts": round(total_cost_pts, 6),
            "total_cost_pct": round(total_cost_pct, 4),
            "source": source,
            "time_bucket": time_bucket_override,
            "fallback_used": not exact_match,
        }

    def resolve_params(self, expiry_pattern: Optional[str], time_bucket: str,
                       strike_type: str) -> tuple:
        """
        Resolve spread parameters with full resolution path tracking.

        Args:
            expiry_pattern: e.g. 'SPXWED' or None
            time_bucket: e.g. 'ENTRY', 'EXIT', 'TIME_0830'
            strike_type: e.g. 'ATM', 'OTM_0.3'

        Returns:
            (half_spread_pts, resolved_source, fallback_used, resolution_path)
            resolution_path is a string like:
              "SPXWED/TIME_0830/ATM -> SPXWED/EXIT/ATM -> SPXWED/ENTRY/ATM"
        """
        if not self.calibration_version.startswith("2"):
            # v1 — no fallback chain concept
            if self.bucket in self._calibration.get("buckets", {}):
                source = f"v1/buckets/{self.bucket}"
            else:
                source = "v1/defaults"
            return (
                self.half_spread_pts_entry,
                source,
                False,
                source,
            )

        hs, source, n, path = _resolve_v2_half_spread_with_path(
            self._calibration, expiry_pattern, time_bucket, strike_type,
        )
        # Exact match = first attempt resolved
        exact = " -> " not in path
        return hs, source, not exact, path

    def describe(self) -> dict:
        """Return a dict describing this model for JSON serialisation."""
        desc = {
            "type": "calibrated",
            "calibration_file": self.calibration_path,
            "calibration_version": self.calibration_version,
            "bucket": self.bucket,
            "half_spread_pts_entry": self.half_spread_pts_entry,
            "half_spread_pts_exit": self.half_spread_pts_exit,
            "slippage_pts_entry": self.slippage_pts_entry,
            "slippage_pts_exit": self.slippage_pts_exit,
            "entry_cost_pts": self.entry_cost_pts,
            "exit_cost_pts": self.exit_cost_pts,
            "roundtrip_pts": self.roundtrip_pts,
            "entry_source": self._entry_source,
            "exit_source": self._exit_source,
        }
        if self.expiry_pattern_filter is not None:
            desc["expiry_pattern_filter"] = self.expiry_pattern_filter
        if self.time_bucket_entry != "ENTRY" or self.time_bucket_exit != "EXIT":
            desc["time_bucket_entry"] = self.time_bucket_entry
            desc["time_bucket_exit"] = self.time_bucket_exit
        if self._entry_n is not None:
            desc["entry_sample_count"] = self._entry_n
        if self._exit_n is not None:
            desc["exit_sample_count"] = self._exit_n
        return desc
