"""
Core math logic for Agentic Trading.
EV (Expected Value) gating and Half-Kelly position sizing.
"""

from dataclasses import dataclass


@dataclass
class TradeSignal:
    """Output of the math engine for a single trade candidate."""
    symbol: str
    p_win: float          # Probability of winning (0-1), from LLM/model
    profit_pct: float     # Expected profit as decimal (e.g. 0.03 = 3%)
    loss_pct: float       # Expected loss as decimal  (e.g. 0.02 = 2%)
    ev: float             # Expected value per dollar risked
    kelly_fraction: float # Raw Kelly fraction
    position_pct: float   # Half-Kelly fraction (actual allocation)
    tradeable: bool       # True if EV > 0


def expected_value(p_win: float, profit_pct: float, loss_pct: float) -> float:
    """
    EV = (P_win * Profit) - (P_loss * Loss)

    Parameters
    ----------
    p_win : float
        Probability of a winning trade (0 to 1).
    profit_pct : float
        Expected gain as a positive decimal (e.g. 0.05 for 5%).
    loss_pct : float
        Expected loss as a positive decimal (e.g. 0.02 for 2%).

    Returns
    -------
    float
        Expected value per dollar risked. Positive means edge exists.
    """
    if not 0.0 <= p_win <= 1.0:
        raise ValueError(f"p_win must be in [0, 1], got {p_win}")
    if profit_pct < 0:
        raise ValueError(f"profit_pct must be >= 0, got {profit_pct}")
    if loss_pct < 0:
        raise ValueError(f"loss_pct must be >= 0, got {loss_pct}")

    p_loss = 1.0 - p_win
    return (p_win * profit_pct) - (p_loss * loss_pct)


def kelly_criterion(p_win: float, profit_pct: float, loss_pct: float) -> float:
    """
    Full Kelly fraction: f* = (p * b - q) / b

    where:
        p = probability of win
        q = probability of loss (1 - p)
        b = ratio of profit to loss (win/loss odds)

    Parameters
    ----------
    p_win : float
        Probability of a winning trade (0 to 1).
    profit_pct : float
        Expected gain as a positive decimal.
    loss_pct : float
        Expected loss as a positive decimal.

    Returns
    -------
    float
        Kelly fraction (fraction of bankroll to wager).
        Clamped to 0 if negative (no edge).
    """
    if not 0.0 <= p_win <= 1.0:
        raise ValueError(f"p_win must be in [0, 1], got {p_win}")
    if profit_pct <= 0:
        raise ValueError(f"profit_pct must be > 0, got {profit_pct}")
    if loss_pct <= 0:
        raise ValueError(f"loss_pct must be > 0, got {loss_pct}")

    b = profit_pct / loss_pct  # win/loss odds
    q = 1.0 - p_win
    kelly = (p_win * b - q) / b
    return max(kelly, 0.0)


def half_kelly(p_win: float, profit_pct: float, loss_pct: float) -> float:
    """
    Half-Kelly: more conservative sizing that reduces variance
    while retaining ~75% of the growth rate of full Kelly.

    Returns
    -------
    float
        Half of the Kelly fraction, clamped to [0, 0.25].
        The 25% cap is an additional safety rail.
    """
    full = kelly_criterion(p_win, profit_pct, loss_pct)
    return min(full / 2.0, 0.25)


def evaluate_trade(
    symbol: str,
    p_win: float,
    profit_pct: float,
    loss_pct: float,
) -> TradeSignal:
    """
    Full math pipeline for one trade candidate.

    1. Compute EV — gate on EV > 0.
    2. Compute Half-Kelly position size.
    3. Return a TradeSignal with all numbers attached.
    """
    ev = expected_value(p_win, profit_pct, loss_pct)
    full_kelly = kelly_criterion(p_win, profit_pct, loss_pct)
    position = half_kelly(p_win, profit_pct, loss_pct)

    return TradeSignal(
        symbol=symbol,
        p_win=p_win,
        profit_pct=profit_pct,
        loss_pct=loss_pct,
        ev=ev,
        kelly_fraction=full_kelly,
        position_pct=position,
        tradeable=ev > 0,
    )
