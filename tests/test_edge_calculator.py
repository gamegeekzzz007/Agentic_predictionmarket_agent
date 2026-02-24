"""
tests/test_edge_calculator.py
Tests for the prediction-market edge calculator (adapter between
market concepts and the Kelly math engine).
"""

import pytest

from app.services.edge_calculator import calculate_edge, _calc_divergence
from database.models import PositionSide


class TestCalculateEdge:
    def test_yes_side_when_underpriced(self):
        """If system_probability > market_price, recommend YES."""
        edge = calculate_edge(
            system_probability=0.70,
            market_price=0.55,
            bankroll=10000,
            scan_id="test-1",
            market_id=1,
        )
        assert edge.recommended_side == PositionSide.YES
        assert edge.edge == pytest.approx(0.15, abs=0.01)

    def test_no_side_when_overpriced(self):
        """If system_probability < market_price, recommend NO."""
        edge = calculate_edge(
            system_probability=0.30,
            market_price=0.55,
            bankroll=10000,
            scan_id="test-2",
            market_id=2,
        )
        assert edge.recommended_side == PositionSide.NO
        assert edge.edge == pytest.approx(0.25, abs=0.01)

    def test_rejected_when_edge_too_small(self):
        """Edge below MIN_EDGE_THRESHOLD should be rejected."""
        edge = calculate_edge(
            system_probability=0.52,
            market_price=0.50,
            bankroll=10000,
            scan_id="test-3",
            market_id=3,
        )
        assert edge.tradeable is False
        assert edge.rejection_reason is not None
        assert "below minimum" in edge.rejection_reason.lower()

    def test_position_size_capped(self):
        """Position size should never exceed max_position_pct * bankroll."""
        edge = calculate_edge(
            system_probability=0.90,
            market_price=0.50,
            bankroll=10000,
            scan_id="test-4",
            market_id=4,
        )
        # MAX_POSITION_PCT from settings is 5%, so max = $500
        assert edge.position_size_dollars <= 500.0

    def test_tradeable_with_good_edge(self):
        """A clear edge should produce a tradeable result."""
        edge = calculate_edge(
            system_probability=0.75,
            market_price=0.50,
            bankroll=10000,
            scan_id="test-5",
            market_id=5,
        )
        assert edge.tradeable is True
        assert edge.num_contracts > 0
        assert edge.expected_value > 0

    def test_debate_metadata_preserved(self):
        """Debate fields should be stored in the EdgeAnalysis."""
        edge = calculate_edge(
            system_probability=0.70,
            market_price=0.50,
            bankroll=10000,
            scan_id="test-6",
            market_id=6,
            debate_triggered=True,
            debate_transcript='[{"agent": "research", "message": "test"}]',
        )
        assert edge.debate_triggered is True
        assert edge.debate_transcript is not None


class TestCalcDivergence:
    def test_divergence_calculation(self):
        estimates = [
            {"probability": 0.60},
            {"probability": 0.40},
            {"probability": 0.55},
        ]
        assert _calc_divergence(estimates) == pytest.approx(0.20, abs=0.01)

    def test_no_divergence_single_estimate(self):
        assert _calc_divergence([{"probability": 0.5}]) == 0.0

    def test_no_divergence_empty(self):
        assert _calc_divergence([]) == 0.0
        assert _calc_divergence(None) == 0.0
