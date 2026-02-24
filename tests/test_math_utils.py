"""
tests/test_math_utils.py
Tests for the core Kelly criterion and EV math engine.
These functions are the crown jewel — they must be bulletproof.
"""

import pytest

from core.math_utils import expected_value, kelly_criterion, half_kelly, evaluate_trade


# ---------------------------------------------------------------------------
# expected_value
# ---------------------------------------------------------------------------

class TestExpectedValue:
    def test_positive_ev(self):
        """When p_win is high enough, EV should be positive."""
        ev = expected_value(p_win=0.6, profit_pct=0.10, loss_pct=0.05)
        assert ev > 0

    def test_negative_ev(self):
        """When p_win is low, EV should be negative."""
        ev = expected_value(p_win=0.2, profit_pct=0.10, loss_pct=0.10)
        assert ev < 0

    def test_zero_edge(self):
        """Fair coin flip with equal payoffs → EV = 0."""
        ev = expected_value(p_win=0.5, profit_pct=0.10, loss_pct=0.10)
        assert abs(ev) < 1e-10

    def test_certain_win(self):
        """p_win=1.0 → EV equals the profit."""
        ev = expected_value(p_win=1.0, profit_pct=0.10, loss_pct=0.05)
        assert abs(ev - 0.10) < 1e-10

    def test_certain_loss(self):
        """p_win=0.0 → EV equals the negative loss."""
        ev = expected_value(p_win=0.0, profit_pct=0.10, loss_pct=0.05)
        assert abs(ev - (-0.05)) < 1e-10

    def test_invalid_p_win_above_one(self):
        with pytest.raises(ValueError):
            expected_value(p_win=1.5, profit_pct=0.10, loss_pct=0.05)

    def test_invalid_p_win_below_zero(self):
        with pytest.raises(ValueError):
            expected_value(p_win=-0.1, profit_pct=0.10, loss_pct=0.05)

    def test_negative_profit_raises(self):
        with pytest.raises(ValueError):
            expected_value(p_win=0.5, profit_pct=-0.10, loss_pct=0.05)

    def test_negative_loss_raises(self):
        with pytest.raises(ValueError):
            expected_value(p_win=0.5, profit_pct=0.10, loss_pct=-0.05)


# ---------------------------------------------------------------------------
# kelly_criterion
# ---------------------------------------------------------------------------

class TestKellyCriterion:
    def test_positive_edge(self):
        """With a clear edge, Kelly fraction should be positive."""
        k = kelly_criterion(p_win=0.6, profit_pct=0.10, loss_pct=0.05)
        assert k > 0

    def test_no_edge_clamped_to_zero(self):
        """When there's no edge, Kelly should be clamped to 0."""
        k = kelly_criterion(p_win=0.3, profit_pct=0.10, loss_pct=0.10)
        assert k == 0.0

    def test_certain_win(self):
        """p_win=1.0 → Kelly fraction = 1.0 (bet everything)."""
        k = kelly_criterion(p_win=1.0, profit_pct=0.10, loss_pct=0.05)
        assert abs(k - 1.0) < 1e-10

    def test_zero_profit_raises(self):
        with pytest.raises(ValueError):
            kelly_criterion(p_win=0.5, profit_pct=0.0, loss_pct=0.05)

    def test_zero_loss_raises(self):
        with pytest.raises(ValueError):
            kelly_criterion(p_win=0.5, profit_pct=0.10, loss_pct=0.0)


# ---------------------------------------------------------------------------
# half_kelly
# ---------------------------------------------------------------------------

class TestHalfKelly:
    def test_half_of_full_kelly(self):
        """half_kelly should be exactly half of kelly_criterion."""
        full = kelly_criterion(p_win=0.6, profit_pct=0.10, loss_pct=0.05)
        half = half_kelly(p_win=0.6, profit_pct=0.10, loss_pct=0.05)
        assert abs(half - full / 2.0) < 1e-10

    def test_cap_at_25_percent(self):
        """half_kelly is capped at 0.25 even if full Kelly is huge."""
        # p_win=0.99 with large b → full Kelly close to 1.0
        half = half_kelly(p_win=0.99, profit_pct=10.0, loss_pct=0.01)
        assert half <= 0.25

    def test_no_edge_returns_zero(self):
        half = half_kelly(p_win=0.3, profit_pct=0.10, loss_pct=0.10)
        assert half == 0.0


# ---------------------------------------------------------------------------
# evaluate_trade
# ---------------------------------------------------------------------------

class TestEvaluateTrade:
    def test_tradeable_when_positive_ev(self):
        signal = evaluate_trade("TEST", p_win=0.7, profit_pct=0.10, loss_pct=0.05)
        assert signal.tradeable is True
        assert signal.ev > 0

    def test_not_tradeable_when_negative_ev(self):
        signal = evaluate_trade("TEST", p_win=0.2, profit_pct=0.05, loss_pct=0.10)
        assert signal.tradeable is False
        assert signal.ev < 0

    def test_signal_fields(self):
        signal = evaluate_trade("BTC-YES", p_win=0.6, profit_pct=0.10, loss_pct=0.05)
        assert signal.symbol == "BTC-YES"
        assert signal.p_win == 0.6
        assert signal.position_pct >= 0
        assert signal.position_pct <= 0.25
