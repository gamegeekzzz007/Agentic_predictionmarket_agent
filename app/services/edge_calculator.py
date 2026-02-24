"""
app/services/edge_calculator.py
Edge calculation and Kelly gate for prediction market contracts.

Maps prediction market inputs (system_probability, market_price) to the
existing math_utils.py functions (p_win, profit_pct, loss_pct).

The math_utils.py functions are UNTOUCHED — they are the crown jewel.
This module is the adapter layer between prediction market concepts
and the generic Kelly/EV math engine.
"""

import logging
import math

from core.config import get_settings
from core.constants import MAX_CONCURRENT_POSITIONS, MIN_EDGE_THRESHOLD
from core.math_utils import expected_value, kelly_criterion, half_kelly
from database.models import EdgeAnalysis, PositionSide

logger = logging.getLogger(__name__)


def calculate_edge(
    system_probability: float,
    market_price: float,
    bankroll: float,
    scan_id: str,
    market_id: int,
    estimates: list[dict] | None = None,
    debate_triggered: bool = False,
    debate_transcript: str | None = None,
) -> EdgeAnalysis:
    """
    Core edge and Kelly calculation for a prediction market contract.

    A YES contract costs `market_price` and pays $1.00 if YES.
    A NO contract costs `(1 - market_price)` and pays $1.00 if NO.

    Parameters
    ----------
    system_probability : float
        Our estimated true probability of YES (0-1).
    market_price : float
        Current YES price on the platform (0-1).
    bankroll : float
        Total available capital.
    scan_id : str
        ID of the scan cycle that produced this.
    market_id : int
        Database ID of the market.
    estimates : list[dict] | None
        Individual desk estimates for divergence tracking.
    debate_triggered : bool
        Whether the debate chatroom was invoked.
    debate_transcript : str | None
        Full debate log if applicable.

    Returns
    -------
    EdgeAnalysis
        Full edge analysis record ready for database insertion.
    """
    settings = get_settings()
    min_edge = settings.MIN_EDGE_THRESHOLD
    max_position_pct = settings.MAX_POSITION_PCT / 100.0  # Convert from percent

    # --- Determine side ---
    # YES if we think the true probability > market price (YES is underpriced)
    # NO if we think the true probability < market price (NO is underpriced)
    if system_probability > market_price:
        side = PositionSide.YES
        p_win = system_probability
        profit_if_win = 1.0 - market_price   # YES pays $1, we paid market_price
        loss_if_lose = market_price           # We lose what we paid
    else:
        side = PositionSide.NO
        p_win = 1.0 - system_probability
        profit_if_win = market_price          # NO pays $1, we paid (1 - market_price)
        loss_if_lose = 1.0 - market_price     # We lose what we paid

    # --- Edge ---
    edge = abs(system_probability - market_price)

    # --- Rejection checks ---
    rejection_reason = None

    if edge < min_edge:
        rejection_reason = f"Edge {edge:.3f} below minimum {min_edge}"
    elif p_win <= 0.0 or p_win >= 1.0:
        rejection_reason = f"Invalid p_win: {p_win}"
    elif profit_if_win <= 0.0 or loss_if_lose <= 0.0:
        rejection_reason = f"Invalid payoff structure: profit={profit_if_win}, loss={loss_if_lose}"

    if rejection_reason:
        logger.info("Kelly gate REJECTED market %d: %s", market_id, rejection_reason)
        return EdgeAnalysis(
            market_id=market_id,
            scan_id=scan_id,
            system_probability=system_probability,
            market_price=market_price,
            edge=round(edge, 4),
            expected_value=0.0,
            kelly_fraction=0.0,
            half_kelly_fraction=0.0,
            position_size_dollars=0.0,
            num_contracts=0,
            recommended_side=side,
            tradeable=False,
            rejection_reason=rejection_reason,
            debate_triggered=debate_triggered,
            debate_transcript=debate_transcript,
            estimates_divergence=_calc_divergence(estimates),
        )

    # --- Call the sacred math engine ---
    ev = expected_value(p_win, profit_if_win, loss_if_lose)
    full_kelly = kelly_criterion(p_win, profit_if_win, loss_if_lose)
    half_kelly_frac = half_kelly(p_win, profit_if_win, loss_if_lose)

    # --- Position sizing ---
    # Half-Kelly fraction of bankroll, capped by max_position_pct
    position_dollars = min(half_kelly_frac * bankroll, max_position_pct * bankroll)

    # Contract cost depends on side
    contract_cost = market_price if side == PositionSide.YES else (1.0 - market_price)
    num_contracts = int(position_dollars / contract_cost) if contract_cost > 0 else 0

    # Final tradeable check: EV must be positive
    tradeable = ev > 0 and num_contracts > 0

    if not tradeable and not rejection_reason:
        rejection_reason = f"EV={ev:.4f} or contracts={num_contracts}"

    logger.info(
        "Kelly gate market %d: side=%s edge=%.3f ev=%.4f kelly=%.4f "
        "half_kelly=%.4f size=$%.2f contracts=%d tradeable=%s",
        market_id, side.value, edge, ev, full_kelly,
        half_kelly_frac, position_dollars, num_contracts, tradeable,
    )

    return EdgeAnalysis(
        market_id=market_id,
        scan_id=scan_id,
        system_probability=round(system_probability, 4),
        market_price=round(market_price, 4),
        edge=round(edge, 4),
        expected_value=round(ev, 6),
        kelly_fraction=round(full_kelly, 6),
        half_kelly_fraction=round(half_kelly_frac, 6),
        position_size_dollars=round(position_dollars, 2),
        num_contracts=num_contracts,
        recommended_side=side,
        tradeable=tradeable,
        rejection_reason=rejection_reason,
        debate_triggered=debate_triggered,
        debate_transcript=debate_transcript,
        estimates_divergence=round(_calc_divergence(estimates), 4),
    )


def _calc_divergence(estimates: list[dict] | None) -> float:
    """Calculate max divergence between estimates."""
    if not estimates or len(estimates) < 2:
        return 0.0
    probs = [e["probability"] for e in estimates]
    return max(probs) - min(probs)
