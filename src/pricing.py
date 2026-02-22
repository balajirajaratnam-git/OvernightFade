"""
Option pricing, IV estimation, and transaction cost models.

This is the single source of truth for all Black-Scholes pricing in the
OvernightFade system. All other files that compute option premiums must
import from here.

Modules:
  - Mathematical primitives: _norm_cdf, _norm_pdf
  - Black-Scholes pricing: black_scholes() -> dict with price + Greeks
  - IV estimation: estimate_iv_from_vix, load_vix_data, get_iv_for_date
  - Transaction cost models: TransactionCosts (percentage), FixedPointCosts (points)
  - Trade P&L: compute_trade_pnl()
  - ATR: compute_wilders_atr()
"""
import math
import os
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np


# ---------------------------------------------------------------------------
# Mathematical primitives (no scipy dependency)
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    """Cumulative distribution function for the standard normal distribution."""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def _norm_pdf(x: float) -> float:
    """Probability density function for the standard normal distribution."""
    return math.exp(-x * x / 2.0) / math.sqrt(2.0 * math.pi)


# ---------------------------------------------------------------------------
# Black-Scholes pricing
# ---------------------------------------------------------------------------

def black_scholes(S: float, K: float, T: float, r: float, sigma: float,
                  option_type: str = 'CALL') -> dict:
    """
    Black-Scholes option price and Greeks.

    Args:
        S: Underlying spot price.
        K: Strike price.
        T: Time to expiry in years (trading-time basis recommended).
        r: Risk-free rate (annualised, e.g. 0.05 for 5%).
        sigma: Implied volatility (annualised, e.g. 0.20 for 20%).
        option_type: 'CALL' or 'PUT'.

    Returns:
        dict with keys:
            price  - option premium (float, always >= 0)
            delta  - dPrice/dS
            gamma  - d²Price/dS²
            theta  - dPrice/dt per calendar day (negative for long options)
            vega   - dPrice/d(sigma) per 1% IV change

    Notes:
        When T <= 0, returns intrinsic value with delta=+/-1 and other Greeks=0.
        When sigma <= 0, sigma is floored to 0.001 to avoid division by zero.
    """
    opt = option_type.upper()
    if opt not in ('CALL', 'PUT'):
        raise ValueError(f"option_type must be 'CALL' or 'PUT', got {option_type!r}")

    # At expiry: intrinsic value only
    if T <= 0:
        if opt == 'CALL':
            price = max(S - K, 0.0)
            delta = 1.0 if S > K else 0.0
        else:
            price = max(K - S, 0.0)
            delta = -1.0 if K > S else 0.0
        return {'price': price, 'delta': delta, 'gamma': 0.0, 'theta': 0.0, 'vega': 0.0}

    if sigma <= 0:
        sigma = 0.001

    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT

    nd1 = _norm_pdf(d1)
    disc = math.exp(-r * T)

    if opt == 'CALL':
        price = S * _norm_cdf(d1) - K * disc * _norm_cdf(d2)
        delta = _norm_cdf(d1)
        theta = (-(S * nd1 * sigma) / (2 * sqrtT) - r * K * disc * _norm_cdf(d2)) / 365.0
    else:
        price = K * disc * _norm_cdf(-d2) - S * _norm_cdf(-d1)
        delta = _norm_cdf(d1) - 1.0
        theta = (-(S * nd1 * sigma) / (2 * sqrtT) + r * K * disc * _norm_cdf(-d2)) / 365.0

    gamma = nd1 / (S * sigma * sqrtT)
    vega = S * nd1 * sqrtT / 100.0   # per 1% IV change

    return {
        'price': max(price, 0.0),
        'delta': delta,
        'gamma': gamma,
        'theta': theta,
        'vega': vega,
    }


# ---------------------------------------------------------------------------
# IV estimation
# ---------------------------------------------------------------------------

def estimate_iv_from_vix(vix_close: float, dte: int = 1) -> float:
    """
    Convert VIX daily close to annualised implied volatility.

    Args:
        vix_close: VIX index level (e.g. 20.0 for VIX=20).
        dte: Days to expiry (unused in base conversion; reserved for term
             structure adjustments in future).

    Returns:
        Annualised IV as a fraction (e.g. 0.20 for 20%).
    """
    return vix_close / 100.0


def load_vix_data(cache_dir: str = "data") -> Optional[pd.Series]:
    """
    Load VIX daily close series. Tries local parquet cache first, then yfinance.

    Args:
        cache_dir: Directory containing vix_daily_cache.parquet.

    Returns:
        pd.Series indexed by tz-naive date with VIX close values,
        or None if both sources fail.
    """
    cache_file = Path(cache_dir) / "vix_daily_cache.parquet"

    if cache_file.exists():
        df = pd.read_parquet(cache_file)
    else:
        try:
            import yfinance as yf
            vix = yf.download("^VIX", start="2016-01-01", end="2026-12-31", progress=False)
            if vix.empty:
                return None
            df = vix[['Close']].copy()
            # Flatten multi-index columns if yfinance returns them
            if hasattr(df.columns, 'levels'):
                df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
            df.to_parquet(cache_file)
        except Exception:
            return None

    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    col = df.columns[0]
    return df[col]


def get_iv_for_date(date, vix_series: Optional[pd.Series],
                    daily_df: Optional[pd.DataFrame] = None) -> float:
    """
    Get implied volatility for a specific date.

    Primary:   VIX close / 100 (e.g. VIX 20 → 0.20).
    Fallback:  20-day realised vol from daily_df (if provided).
    Default:   0.20 if both sources unavailable.

    Args:
        date: The trade date (str, date, or Timestamp).
        vix_series: VIX daily close series (from load_vix_data).
        daily_df: Daily OHLCV DataFrame with 'Close' column (optional fallback).

    Returns:
        Annualised IV as a fraction.
    """
    if vix_series is not None:
        lookup = pd.Timestamp(date).normalize()
        if lookup in vix_series.index:
            return float(vix_series.loc[lookup]) / 100.0
        nearby = vix_series.index[abs(vix_series.index - lookup) <= pd.Timedelta(days=5)]
        if len(nearby) > 0:
            closest = nearby[abs(nearby - lookup).argmin()]
            return float(vix_series.loc[closest]) / 100.0

    if daily_df is not None:
        loc = daily_df.index.get_indexer([pd.Timestamp(date)], method='ffill')[0]
        if loc >= 20:
            returns = daily_df['Close'].iloc[loc - 20:loc].pct_change().dropna()
            if len(returns) >= 10:
                return float(returns.std() * math.sqrt(252))

    return 0.20


# ---------------------------------------------------------------------------
# Transaction cost models
# ---------------------------------------------------------------------------

@dataclass
class TransactionCosts:
    """
    Percentage-based cost model (legacy, for comparison with baseline).

    Costs are expressed as fractions of the option premium per side:
      spread_pct: fraction of premium lost to bid-ask spread (round-trip)
      slippage_pct: fraction of premium lost to fill slippage (round-trip)
      commission_per_contract: fixed dollar amount per contract

    These match the values in config/baseline_atm_vixiv.json:
      total_round_trip_pct = 0.05 (4% spread + 1% slippage)
    """
    spread_pct: float = 0.04
    slippage_pct: float = 0.01
    commission_per_contract: float = 0.65

    @property
    def total_round_trip_pct(self) -> float:
        """Total percentage cost (spread + slippage), excluding commission."""
        return self.spread_pct + self.slippage_pct

    @classmethod
    def from_config(cls, config_path: str = "config/config.json") -> 'TransactionCosts':
        """Load percentage costs from config file."""
        try:
            with open(config_path) as f:
                cfg = json.load(f)
            pct = cfg.get("cost_model", {}).get("percentage", {})
            return cls(
                spread_pct=pct.get("spread_pct", 0.04),
                slippage_pct=pct.get("slippage_pct", 0.01),
                commission_per_contract=pct.get("commission_per_contract", 0.65),
            )
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            return cls()


@dataclass
class FixedPointCosts:
    """
    Fixed-point spread model (realistic for IG options).

    Costs are expressed in points (same units as the option premium):
      half_spread_pts: half the bid-ask spread applied each side
      slippage_pts: additional slippage buffer applied each side

    Example: half_spread_pts=0.10, slippage_pts=0.02
      entry: pay mid + 0.12 pts (ask)
      exit:  receive mid - 0.12 pts (bid)
      total round-trip: 0.24 pts

    Use from_spread_samples() to build from measured bid-ask data.
    """
    half_spread_pts: float = 0.0
    slippage_pts: float = 0.03

    @property
    def total_one_side_pts(self) -> float:
        """Total one-side friction in points."""
        return self.half_spread_pts + self.slippage_pts

    @property
    def total_round_trip_pts(self) -> float:
        """Total round-trip friction in points."""
        return self.total_one_side_pts * 2

    @classmethod
    def from_spread_samples(cls, median_spread_pts: float,
                            slippage_buffer: float = 0.03) -> 'FixedPointCosts':
        """
        Create from measured spread data.

        Args:
            median_spread_pts: The FULL bid-ask spread in points (ask - bid).
            slippage_buffer: Additional slippage buffer in points each side.
        """
        return cls(half_spread_pts=median_spread_pts / 2.0, slippage_pts=slippage_buffer)

    @classmethod
    def from_config(cls, config_path: str = "config/config.json") -> 'FixedPointCosts':
        """
        Load calibrated fixed-point costs from config file.

        Returns default (zero half-spread) if config not calibrated yet.
        """
        try:
            with open(config_path) as f:
                cfg = json.load(f)
            fp = cfg.get("cost_model", {}).get("fixed_point", {})
            half_spread = fp.get("half_spread_pts")
            slippage = fp.get("slippage_pts", 0.03)
            if half_spread is None:
                return cls()  # AWAITING_CALIBRATION
            return cls(half_spread_pts=float(half_spread), slippage_pts=float(slippage))
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            return cls()


# ---------------------------------------------------------------------------
# Trade P&L calculation
# ---------------------------------------------------------------------------

def compute_trade_pnl(
    underlying_entry: float,
    underlying_exit: float,
    strike: float,
    option_type: str,
    T_entry: float,
    T_exit: float,
    sigma: float,
    r: float = 0.05,
    costs: Optional[TransactionCosts] = None,
    fixed_costs: Optional[FixedPointCosts] = None,
    position_dollars: float = 1000.0,
) -> dict:
    """
    Compute full trade P&L with transaction costs.

    Prices the option at entry and exit using Black-Scholes, then applies
    transaction costs. fixed_costs (points-based) takes precedence over
    costs (percentage-based) when both are provided.

    Args:
        underlying_entry: Underlying price at entry (16:00 ET close).
        underlying_exit: Underlying price at exit (09:30 ET open).
        strike: Option strike price.
        option_type: 'CALL' or 'PUT'.
        T_entry: Time to expiry at entry, in years (trading-time basis).
        T_exit: Time to expiry at exit, in years (trading-time basis).
        sigma: Annualised implied volatility.
        r: Risk-free rate.
        costs: Percentage-based cost model (optional).
        fixed_costs: Fixed-point cost model (optional, takes precedence).
        position_dollars: Dollar amount of premium budget for this trade.

    Returns:
        dict with keys:
            entry_mid        - BS mid price at entry
            exit_mid         - BS mid price at exit
            entry_cost       - actual entry price paid (mid + spread)
            exit_proceeds    - actual exit price received (mid - spread)
            gross_pnl_pct    - (exit_mid - entry_mid) / entry_mid * 100
            net_pnl_pct      - net return % after all costs (capped at -100%)
            net_pnl_dollars  - dollar P&L given position_dollars
            entry_greeks     - dict of Greeks at entry
            exit_greeks      - dict of Greeks at exit
    """
    bs_entry = black_scholes(underlying_entry, strike, T_entry, r, sigma, option_type)
    bs_exit = black_scholes(underlying_exit, strike, T_exit, r, sigma, option_type)

    entry_mid = bs_entry['price']
    exit_mid = bs_exit['price']

    # Apply costs
    if fixed_costs is not None:
        # Fixed-point model: add/subtract points each side
        one_side = fixed_costs.total_one_side_pts
        entry_cost = entry_mid + one_side
        exit_proceeds = max(exit_mid - one_side, 0.0)
    elif costs is not None:
        # Percentage model: deduct from return
        entry_cost = entry_mid
        exit_proceeds = exit_mid
        # Costs applied as a flat deduction on the net return below
    else:
        entry_cost = entry_mid
        exit_proceeds = exit_mid

    if entry_cost <= 0:
        return {
            'entry_mid': entry_mid, 'exit_mid': exit_mid,
            'entry_cost': entry_cost, 'exit_proceeds': exit_proceeds,
            'gross_pnl_pct': 0.0, 'net_pnl_pct': -100.0,
            'net_pnl_dollars': -position_dollars,
            'entry_greeks': bs_entry, 'exit_greeks': bs_exit,
        }

    gross_pnl_pct = (exit_mid - entry_mid) / entry_mid * 100.0 if entry_mid > 0 else 0.0

    if fixed_costs is not None:
        net_pnl_pct = (exit_proceeds - entry_cost) / entry_cost * 100.0
    elif costs is not None:
        net_pnl_pct = gross_pnl_pct - costs.total_round_trip_pct * 100.0
    else:
        net_pnl_pct = gross_pnl_pct

    net_pnl_pct = max(net_pnl_pct, -100.0)
    net_pnl_dollars = position_dollars * (net_pnl_pct / 100.0)

    return {
        'entry_mid': entry_mid,
        'exit_mid': exit_mid,
        'entry_cost': entry_cost,
        'exit_proceeds': exit_proceeds,
        'gross_pnl_pct': gross_pnl_pct,
        'net_pnl_pct': net_pnl_pct,
        'net_pnl_dollars': net_pnl_dollars,
        'entry_greeks': bs_entry,
        'exit_greeks': bs_exit,
    }


# ---------------------------------------------------------------------------
# ATR (Wilder's method)
# ---------------------------------------------------------------------------

def compute_wilders_atr(high: pd.Series, low: pd.Series, close: pd.Series,
                        period: int = 14) -> pd.Series:
    """
    Wilder's Average True Range using exponential smoothing.

    Matches TradingView's ATR calculation (not simple MA ATR).

    Args:
        high:   Series of daily highs.
        low:    Series of daily lows.
        close:  Series of daily closes.
        period: Smoothing period (default 14).

    Returns:
        pd.Series of ATR values (same index as input, NaN for first period-1 rows).
    """
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Wilder's smoothing: first value = simple mean, then EMA with alpha=1/period
    atr = tr.copy()
    alpha = 1.0 / period

    # Find first valid index
    first_valid = tr.first_valid_index()
    if first_valid is None:
        return pd.Series(np.nan, index=close.index)

    idx = close.index.get_loc(first_valid)
    if idx + period > len(tr):
        return pd.Series(np.nan, index=close.index)

    # Seed: simple mean of first `period` values
    seed_slice = tr.iloc[idx:idx + period]
    if seed_slice.isna().any():
        return pd.Series(np.nan, index=close.index)

    seed = seed_slice.mean()
    atr.iloc[:idx + period] = np.nan
    atr.iloc[idx + period - 1] = seed

    # Wilder's EMA from period onwards
    for i in range(idx + period, len(tr)):
        atr.iloc[i] = atr.iloc[i - 1] * (1 - alpha) + tr.iloc[i] * alpha

    return atr


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("src/pricing.py self-test")
    print("=" * 70)

    # 1. Known ATM call value: S=100, K=100, T=1, r=0.05, sigma=0.20
    # Black-Scholes formula gives ~10.45
    result = black_scholes(100, 100, 1.0, 0.05, 0.20, 'CALL')
    print(f"\n1. ATM call (S=100,K=100,T=1,r=5%,σ=20%): price={result['price']:.4f} (expect ~10.45)")
    assert 10.0 < result['price'] < 11.0, f"ATM call price out of range: {result['price']}"

    # 2. Put-call parity: C - P = S - K*exp(-rT)
    call = black_scholes(100, 100, 1.0, 0.05, 0.20, 'CALL')['price']
    put  = black_scholes(100, 100, 1.0, 0.05, 0.20, 'PUT')['price']
    parity = call - put
    theoretical = 100 - 100 * math.exp(-0.05 * 1.0)
    print(f"2. Put-call parity: C-P={parity:.4f}, S-K*e^(-rT)={theoretical:.4f}")
    assert abs(parity - theoretical) < 0.01, "Put-call parity violated"

    # 3. T=0 returns intrinsic as dict
    atm_expiry = black_scholes(105, 100, 0.0, 0.05, 0.20, 'CALL')
    print(f"3. T=0 intrinsic (S=105, K=100): {atm_expiry}")
    assert atm_expiry['price'] == 5.0
    assert isinstance(atm_expiry, dict)

    # 4. IV from VIX
    iv20 = estimate_iv_from_vix(20.0)
    iv35 = estimate_iv_from_vix(35.0)
    print(f"4. IV from VIX: VIX=20 → {iv20:.2f}, VIX=35 → {iv35:.2f}")
    assert iv20 == 0.20
    assert iv35 == 0.35

    # 5. Percentage costs reduce P&L
    pct_costs = TransactionCosts(spread_pct=0.04, slippage_pct=0.01)
    pnl = compute_trade_pnl(100, 101, 100, 'CALL', 0.01, 0.005, 0.20,
                             costs=pct_costs, position_dollars=1000)
    assert pnl['net_pnl_pct'] < pnl['gross_pnl_pct'], "Costs must reduce P&L"
    print(f"5. Pct cost P&L: gross={pnl['gross_pnl_pct']:.2f}%, net={pnl['net_pnl_pct']:.2f}%")

    # 6. Fixed-point costs reduce P&L and penalise cheap options more
    fp_costs = FixedPointCosts(half_spread_pts=0.10, slippage_pts=0.02)
    expensive = black_scholes(100, 100, 0.01, 0.05, 0.20, 'CALL')['price']   # ~0.80 pts
    cheap     = black_scholes(100, 105, 0.01, 0.05, 0.20, 'CALL')['price']   # ~0.05 pts (OTM)
    # Fixed spread as % of premium: hurts cheap more
    spread_pct_exp  = fp_costs.total_round_trip_pts / max(expensive, 0.01) * 100
    spread_pct_cheap = fp_costs.total_round_trip_pts / max(cheap, 0.01) * 100
    print(f"6. Fixed spread % of premium: expensive={spread_pct_exp:.1f}%, cheap={spread_pct_cheap:.1f}%")
    assert spread_pct_cheap > spread_pct_exp, "Fixed spread must penalise cheap options more"

    # 7. from_spread_samples
    fp = FixedPointCosts.from_spread_samples(median_spread_pts=1.80)
    print(f"7. from_spread_samples(1.80 pts): half_spread={fp.half_spread_pts:.2f}, "
          f"total one-side={fp.total_one_side_pts:.2f}")
    assert fp.half_spread_pts == 0.90

    print("\nAll self-tests passed.")
    print("=" * 70)
