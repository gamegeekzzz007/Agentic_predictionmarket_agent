"""
app/services/resolution_service.py
Checks markets for resolution, closes positions with correct P&L,
and creates CalibrationRecords with Brier scores.

Called by the scheduler every 1 hour.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, col

from database.connection import async_session
from database.models import (
    CalibrationRecord,
    EdgeAnalysis,
    Market,
    MarketStatus,
    Platform,
    Position,
    PositionSide,
    PositionStatus,
    ProbabilityEstimate,
)

logger = logging.getLogger(__name__)


async def _check_kalshi_resolution(market: Market) -> tuple[bool, bool | None]:
    """
    Check if a Kalshi market has resolved.

    Returns (is_resolved, outcome) where outcome is True for YES, False for NO.
    """
    from app.services.kalshi_client import KalshiClient

    client = KalshiClient()
    try:
        data = await client.get_market(market.platform_market_id)
        status = data.get("status", "").lower()

        if status in ("finalized", "settled"):
            result = data.get("result", "").lower()
            if result == "yes":
                return True, True
            elif result == "no":
                return True, False
            else:
                logger.warning("Kalshi market %s finalized with unknown result: %s",
                               market.platform_market_id, result)
                return False, None

        return False, None
    finally:
        await client.close()


async def _check_polymarket_resolution(market: Market) -> tuple[bool, bool | None]:
    """
    Check if a Polymarket market has resolved.

    Returns (is_resolved, outcome) where outcome is True for YES, False for NO.
    """
    from app.services.polymarket_client import PolymarketClient

    client = PolymarketClient()
    try:
        data = await client.get_market(market.platform_market_id)

        if data.get("resolved", False):
            # Check outcome prices: YES token at 1.0 means YES won
            outcome_prices = data.get("outcomePrices", "")
            if outcome_prices:
                import json
                prices = json.loads(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
                yes_final = float(prices[0]) if prices else 0.5
                return True, yes_final > 0.5

            return True, None

        return False, None
    finally:
        await client.close()


async def _close_positions_for_market(
    session: AsyncSession,
    market: Market,
    outcome: bool,
) -> list[Position]:
    """Close all open/pending positions for a resolved market."""
    positions = (await session.execute(
        select(Position).where(
            Position.market_id == market.id,
            Position.status.in_([PositionStatus.OPEN, PositionStatus.PENDING]),
        )
    )).scalars().all()

    closed = []
    for position in positions:
        # Calculate P&L based on outcome
        if outcome:  # YES won
            if position.side == PositionSide.YES:
                # Paid entry_price, receive $1.00
                pnl = (1.0 - position.entry_price) * position.num_contracts
                position.status = PositionStatus.CLOSED_WIN
            else:
                # Paid (1 - entry_price) for NO, lose it all
                pnl = -position.total_cost
                position.status = PositionStatus.CLOSED_LOSS
        else:  # NO won
            if position.side == PositionSide.NO:
                # Paid (1 - market_price) for NO, receive $1.00
                pnl = position.entry_price * position.num_contracts
                position.status = PositionStatus.CLOSED_WIN
            else:
                # Paid entry_price for YES, lose it all
                pnl = -position.total_cost
                position.status = PositionStatus.CLOSED_LOSS

        position.exit_price = 1.0 if (
            (outcome and position.side == PositionSide.YES) or
            (not outcome and position.side == PositionSide.NO)
        ) else 0.0
        position.pnl_dollars = round(pnl, 2)
        position.pnl_percent = round(pnl / position.total_cost * 100, 2) if position.total_cost > 0 else 0.0
        position.closed_at = datetime.now(timezone.utc)

        session.add(position)
        closed.append(position)

    return closed


async def _create_calibration_record(
    session: AsyncSession,
    market: Market,
    outcome: bool,
) -> CalibrationRecord | None:
    """Create a CalibrationRecord with Brier score and per-desk estimates."""
    # Find the most recent EdgeAnalysis for this market
    edge = (await session.execute(
        select(EdgeAnalysis)
        .where(EdgeAnalysis.market_id == market.id)
        .order_by(col(EdgeAnalysis.created_at).desc())
        .limit(1)
    )).scalars().first()

    if not edge:
        logger.warning("No EdgeAnalysis found for market %d, skipping calibration", market.id)
        return None

    # Brier score: (forecast - outcome)^2
    outcome_val = 1.0 if outcome else 0.0
    brier = (edge.system_probability - outcome_val) ** 2

    # Look up per-desk estimates (most recent scan for this market)
    desk_estimates = (await session.execute(
        select(ProbabilityEstimate)
        .where(ProbabilityEstimate.market_id == market.id)
        .order_by(col(ProbabilityEstimate.created_at).desc())
    )).scalars().all()

    # Extract per-desk values (take the most recent for each desk)
    seen_desks: dict[str, float] = {}
    for est in desk_estimates:
        if est.desk not in seen_desks:
            seen_desks[est.desk] = est.probability

    record = CalibrationRecord(
        market_id=market.id,
        system_probability=edge.system_probability,
        market_price_at_entry=edge.market_price,
        actual_outcome=outcome,
        brier_score=round(brier, 6),
        research_estimate=seen_desks.get("research_desk"),
        base_rate_estimate=seen_desks.get("base_rate_desk"),
        model_estimate=seen_desks.get("model_desk"),
        category=market.category,
    )

    session.add(record)
    return record


async def check_resolutions(session: AsyncSession) -> int:
    """
    Check all ACTIVE markets with positions for resolution.

    Returns the number of markets resolved.
    """
    # Find ACTIVE markets that have open/pending positions
    markets_with_positions = (await session.execute(
        select(Market)
        .where(Market.status == MarketStatus.ACTIVE)
        .where(
            Market.id.in_(
                select(Position.market_id).where(
                    Position.status.in_([PositionStatus.OPEN, PositionStatus.PENDING])
                )
            )
        )
    )).scalars().all()

    if not markets_with_positions:
        return 0

    resolved_count = 0

    for market in markets_with_positions:
        try:
            if market.platform == Platform.KALSHI:
                is_resolved, outcome = await _check_kalshi_resolution(market)
            elif market.platform == Platform.POLYMARKET:
                is_resolved, outcome = await _check_polymarket_resolution(market)
            else:
                continue

            if not is_resolved or outcome is None:
                continue

            # Update market status
            market.status = MarketStatus.RESOLVED_YES if outcome else MarketStatus.RESOLVED_NO
            market.resolved_outcome = outcome
            market.resolution_time = datetime.now(timezone.utc)
            session.add(market)

            # Close all positions
            closed = await _close_positions_for_market(session, market, outcome)

            # Create calibration record
            cal_record = await _create_calibration_record(session, market, outcome)

            await session.commit()
            resolved_count += 1

            logger.info(
                "Market %d (%s) resolved %s: closed %d positions, brier=%.4f",
                market.id, market.title,
                "YES" if outcome else "NO",
                len(closed),
                cal_record.brier_score if cal_record else -1,
            )

        except Exception as exc:
            logger.error("Error checking resolution for market %d: %s", market.id, exc)
            await session.rollback()

    return resolved_count


async def run_resolution_checker() -> None:
    """Entry point called by the scheduler every 1 hour."""
    async with async_session() as session:
        resolved = await check_resolutions(session)
        if resolved:
            logger.info("Resolution checker: %d markets resolved", resolved)
