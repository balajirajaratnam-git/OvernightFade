"""
Unit tests for src/pricing.py

Run with:
    python -m pytest tests/test_pricing.py -v
"""
import math
import sys
import pytest
import pandas as pd
import numpy as np

sys.path.insert(0, 'src')
from pricing import (
    _norm_cdf, _norm_pdf,
    black_scholes,
    estimate_iv_from_vix,
    TransactionCosts,
    FixedPointCosts,
    compute_trade_pnl,
    compute_wilders_atr,
)


# ---------------------------------------------------------------------------
# Mathematical primitives
# ---------------------------------------------------------------------------

class TestNormFunctions:
    def test_norm_cdf_zero(self):
        """CDF at 0 should be 0.5."""
        assert abs(_norm_cdf(0.0) - 0.5) < 1e-10

    def test_norm_cdf_large_positive(self):
        """CDF at large positive x should approach 1."""
        assert _norm_cdf(8.0) > 0.9999

    def test_norm_cdf_large_negative(self):
        """CDF at large negative x should approach 0."""
        assert _norm_cdf(-8.0) < 0.0001

    def test_norm_pdf_zero(self):
        """PDF at 0 = 1/sqrt(2*pi)."""
        expected = 1.0 / math.sqrt(2.0 * math.pi)
        assert abs(_norm_pdf(0.0) - expected) < 1e-10

    def test_norm_pdf_positive(self):
        """PDF is always positive."""
        for x in [-3.0, -1.0, 0.0, 1.0, 3.0]:
            assert _norm_pdf(x) > 0


# ---------------------------------------------------------------------------
# Black-Scholes pricing
# ---------------------------------------------------------------------------

class TestBlackScholes:
    # Reference values from standard BS tables:
    # S=100, K=100, T=1, r=5%, sigma=20% → call≈10.4506
    S, K, T, r, sigma = 100.0, 100.0, 1.0, 0.05, 0.20

    def test_atm_call_known_value(self):
        """ATM call price matches known BS value."""
        result = black_scholes(self.S, self.K, self.T, self.r, self.sigma, 'CALL')
        assert 10.0 < result['price'] < 11.0, f"ATM call = {result['price']}"

    def test_atm_put_known_value(self):
        """ATM put price matches known BS value (~5.57 for these inputs)."""
        result = black_scholes(self.S, self.K, self.T, self.r, self.sigma, 'PUT')
        assert 5.0 < result['price'] < 6.5, f"ATM put = {result['price']}"

    def test_put_call_parity(self):
        """Put-call parity: C - P = S - K*exp(-rT)."""
        call = black_scholes(self.S, self.K, self.T, self.r, self.sigma, 'CALL')['price']
        put  = black_scholes(self.S, self.K, self.T, self.r, self.sigma, 'PUT')['price']
        lhs = call - put
        rhs = self.S - self.K * math.exp(-self.r * self.T)
        assert abs(lhs - rhs) < 0.001, f"Put-call parity: {lhs:.4f} != {rhs:.4f}"

    def test_returns_dict_with_greeks(self):
        """black_scholes always returns a dict with all required keys."""
        result = black_scholes(self.S, self.K, self.T, self.r, self.sigma, 'CALL')
        for key in ('price', 'delta', 'gamma', 'theta', 'vega'):
            assert key in result, f"Missing key: {key}"

    def test_t_zero_returns_intrinsic_itm_call(self):
        """T=0 ITM call returns intrinsic (S-K) as dict."""
        result = black_scholes(105.0, 100.0, 0.0, 0.05, 0.20, 'CALL')
        assert isinstance(result, dict), "T=0 must return dict, not float"
        assert result['price'] == 5.0
        assert result['delta'] == 1.0
        assert result['gamma'] == 0.0

    def test_t_zero_returns_intrinsic_otm_call(self):
        """T=0 OTM call returns 0 intrinsic."""
        result = black_scholes(95.0, 100.0, 0.0, 0.05, 0.20, 'CALL')
        assert isinstance(result, dict)
        assert result['price'] == 0.0

    def test_t_zero_returns_intrinsic_itm_put(self):
        """T=0 ITM put returns intrinsic (K-S)."""
        result = black_scholes(95.0, 100.0, 0.0, 0.05, 0.20, 'PUT')
        assert isinstance(result, dict)
        assert result['price'] == 5.0
        assert result['delta'] == -1.0

    def test_price_always_non_negative(self):
        """Option price must never be negative."""
        for S in [50, 100, 150]:
            for K in [80, 100, 120]:
                for T in [0.0, 0.01, 0.1, 1.0]:
                    for opt in ('CALL', 'PUT'):
                        result = black_scholes(S, K, T, 0.05, 0.20, opt)
                        assert result['price'] >= 0.0, f"Negative price for {S},{K},{T},{opt}"

    def test_call_delta_between_0_and_1(self):
        """Call delta must be in (0, 1)."""
        result = black_scholes(100, 100, 1.0, 0.05, 0.20, 'CALL')
        assert 0 < result['delta'] < 1

    def test_put_delta_between_minus1_and_0(self):
        """Put delta must be in (-1, 0)."""
        result = black_scholes(100, 100, 1.0, 0.05, 0.20, 'PUT')
        assert -1 < result['delta'] < 0

    def test_invalid_option_type_raises(self):
        """Invalid option_type raises ValueError."""
        with pytest.raises(ValueError):
            black_scholes(100, 100, 1.0, 0.05, 0.20, 'STRADDLE')

    def test_sigma_floor_prevents_divide_by_zero(self):
        """sigma=0 should not raise; sigma is floored to 0.001."""
        result = black_scholes(100, 100, 1.0, 0.05, 0.0, 'CALL')
        assert result['price'] >= 0


# ---------------------------------------------------------------------------
# IV estimation
# ---------------------------------------------------------------------------

class TestIVEstimation:
    def test_vix_20_gives_0_20(self):
        """VIX=20 → IV=0.20."""
        assert estimate_iv_from_vix(20.0) == 0.20

    def test_vix_35_gives_0_35(self):
        """VIX=35 → IV=0.35."""
        assert estimate_iv_from_vix(35.0) == 0.35

    def test_vix_15_gives_0_15(self):
        assert estimate_iv_from_vix(15.0) == 0.15


# ---------------------------------------------------------------------------
# TransactionCosts
# ---------------------------------------------------------------------------

class TestTransactionCosts:
    def test_defaults_match_baseline(self):
        """Default values match baseline_atm_vixiv.json (4% spread + 1% slippage)."""
        tc = TransactionCosts()
        assert tc.spread_pct == 0.04
        assert tc.slippage_pct == 0.01
        assert tc.total_round_trip_pct == 0.05

    def test_custom_values(self):
        tc = TransactionCosts(spread_pct=0.02, slippage_pct=0.005)
        assert tc.total_round_trip_pct == 0.025


# ---------------------------------------------------------------------------
# FixedPointCosts
# ---------------------------------------------------------------------------

class TestFixedPointCosts:
    def test_from_spread_samples(self):
        """from_spread_samples splits full spread into half_spread."""
        fp = FixedPointCosts.from_spread_samples(median_spread_pts=1.80)
        assert fp.half_spread_pts == 0.90

    def test_total_one_side(self):
        fp = FixedPointCosts(half_spread_pts=0.10, slippage_pts=0.02)
        assert abs(fp.total_one_side_pts - 0.12) < 1e-10

    def test_total_round_trip(self):
        fp = FixedPointCosts(half_spread_pts=0.10, slippage_pts=0.02)
        assert abs(fp.total_round_trip_pts - 0.24) < 1e-10

    def test_from_config_flat_schema(self, tmp_path):
        """from_config() reads a flat standalone file {half_spread_pts, slippage_pts}."""
        import json
        cfg = tmp_path / "cost_model_fixed.json"
        cfg.write_text(json.dumps({"half_spread_pts": 0.90, "slippage_pts": 0.03}))
        fp = FixedPointCosts.from_config(str(cfg))
        assert fp.half_spread_pts == 0.90
        assert fp.slippage_pts == 0.03

    def test_from_config_nested_schema(self, tmp_path):
        """from_config() reads nested cost_model.fixed_point in full config.json."""
        import json
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({
            "cost_model": {"fixed_point": {"half_spread_pts": 0.45, "slippage_pts": 0.02}}
        }))
        fp = FixedPointCosts.from_config(str(cfg))
        assert fp.half_spread_pts == 0.45
        assert fp.slippage_pts == 0.02

    def test_from_config_missing_returns_defaults(self, tmp_path):
        """from_config() returns zero half-spread defaults when file is missing."""
        fp = FixedPointCosts.from_config(str(tmp_path / "nonexistent.json"))
        assert fp.half_spread_pts == 0.0  # default, awaiting calibration

    def test_fixed_costs_penalise_cheap_premiums_more(self):
        """Fixed-point spread hurts cheap options (OTM) more as % of premium."""
        fp = FixedPointCosts(half_spread_pts=0.10, slippage_pts=0.02)
        rt = fp.total_round_trip_pts

        # Expensive ATM: ~2.35 pts premium
        expensive_prem = 2.35
        # Cheap OTM: ~1.81 pts premium
        cheap_prem = 1.81

        spread_pct_exp = rt / expensive_prem * 100
        spread_pct_cheap = rt / cheap_prem * 100

        assert spread_pct_cheap > spread_pct_exp, (
            f"Fixed spread should penalise cheap premiums more: "
            f"cheap={spread_pct_cheap:.1f}% vs expensive={spread_pct_exp:.1f}%"
        )


# ---------------------------------------------------------------------------
# compute_trade_pnl
# ---------------------------------------------------------------------------

class TestComputeTradePnl:
    def test_percentage_costs_reduce_pnl(self):
        """Percentage-based costs must reduce P&L vs no costs."""
        costs = TransactionCosts(spread_pct=0.04, slippage_pct=0.01)
        pnl = compute_trade_pnl(100, 101, 100, 'CALL', 0.01, 0.005, 0.20, costs=costs)
        pnl_no_cost = compute_trade_pnl(100, 101, 100, 'CALL', 0.01, 0.005, 0.20)
        assert pnl['net_pnl_pct'] < pnl_no_cost['net_pnl_pct']

    def test_fixed_costs_reduce_pnl(self):
        """Fixed-point costs must reduce P&L vs no costs."""
        fp = FixedPointCosts(half_spread_pts=0.10, slippage_pts=0.02)
        pnl = compute_trade_pnl(100, 101, 100, 'CALL', 0.01, 0.005, 0.20, fixed_costs=fp)
        pnl_no_cost = compute_trade_pnl(100, 101, 100, 'CALL', 0.01, 0.005, 0.20)
        assert pnl['net_pnl_pct'] < pnl_no_cost['net_pnl_pct']

    def test_fixed_costs_take_precedence(self):
        """When both cost models provided, fixed_costs takes precedence."""
        pct = TransactionCosts(spread_pct=0.04, slippage_pct=0.01)
        fp = FixedPointCosts(half_spread_pts=0.10, slippage_pts=0.02)
        pnl_both = compute_trade_pnl(100, 101, 100, 'CALL', 0.01, 0.005, 0.20,
                                     costs=pct, fixed_costs=fp)
        pnl_fp_only = compute_trade_pnl(100, 101, 100, 'CALL', 0.01, 0.005, 0.20,
                                        fixed_costs=fp)
        assert abs(pnl_both['net_pnl_pct'] - pnl_fp_only['net_pnl_pct']) < 1e-6

    def test_pnl_capped_at_minus_100(self):
        """Net P&L can never go below -100%."""
        # Deep OTM option: premium near zero, will lose close to 100%
        fp = FixedPointCosts(half_spread_pts=5.0, slippage_pts=1.0)
        pnl = compute_trade_pnl(100, 100, 200, 'CALL', 0.001, 0.0001, 0.20, fixed_costs=fp)
        assert pnl['net_pnl_pct'] >= -100.0

    def test_returns_dict_with_required_keys(self):
        """compute_trade_pnl returns dict with all required keys."""
        pnl = compute_trade_pnl(100, 101, 100, 'CALL', 0.01, 0.005, 0.20)
        for key in ('entry_mid', 'exit_mid', 'entry_cost', 'exit_proceeds',
                    'gross_pnl_pct', 'net_pnl_pct', 'net_pnl_dollars',
                    'entry_greeks', 'exit_greeks'):
            assert key in pnl, f"Missing key: {key}"

    def test_dollar_pnl_matches_pct(self):
        """Dollar P&L = position_dollars * net_pnl_pct / 100."""
        pnl = compute_trade_pnl(100, 101, 100, 'CALL', 0.01, 0.005, 0.20,
                                 position_dollars=1000)
        expected_dollars = 1000 * pnl['net_pnl_pct'] / 100
        assert abs(pnl['net_pnl_dollars'] - expected_dollars) < 0.01


# ---------------------------------------------------------------------------
# compute_wilders_atr
# ---------------------------------------------------------------------------

class TestWildersATR:
    def _make_series(self, values, name="col"):
        return pd.Series(values, name=name)

    def test_wilder_differs_from_simple_ma(self):
        """Wilder's ATR should differ from a simple 14-period MA of TR."""
        np.random.seed(42)
        n = 100
        close = pd.Series(100 + np.random.randn(n).cumsum())
        high  = close + abs(np.random.randn(n)) * 0.5
        low   = close - abs(np.random.randn(n)) * 0.5

        wilder_atr = compute_wilders_atr(high, low, close, period=14)

        # Simple MA ATR
        prev_close = close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(),
                        (low - prev_close).abs()], axis=1).max(axis=1)
        simple_atr = tr.rolling(14).mean()

        # They should not be identical
        valid = ~wilder_atr.isna() & ~simple_atr.isna()
        diffs = (wilder_atr[valid] - simple_atr[valid]).abs()
        assert diffs.mean() > 0.001, "Wilder ATR should differ from simple MA ATR"

    def test_atr_always_non_negative(self):
        """ATR values must always be non-negative."""
        np.random.seed(0)
        n = 50
        close = pd.Series(100 + np.random.randn(n).cumsum())
        high  = close + abs(np.random.randn(n))
        low   = close - abs(np.random.randn(n))

        atr = compute_wilders_atr(high, low, close, period=14)
        valid = atr.dropna()
        assert (valid >= 0).all(), "ATR has negative values"

    def test_atr_nan_for_first_period(self):
        """First (period - 1) values should be NaN."""
        np.random.seed(1)
        n = 30
        close = pd.Series(100 + np.random.randn(n).cumsum())
        high  = close + 1
        low   = close - 1

        atr = compute_wilders_atr(high, low, close, period=14)
        # First 13 values should be NaN
        assert atr.iloc[:13].isna().all(), "First period-1 values should be NaN"
