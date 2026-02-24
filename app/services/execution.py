"""
app/services/execution.py
Order execution service — places limit orders via Kalshi or Polymarket.

ALWAYS maker, NEVER taker. Research shows takers lose ~32% on Kalshi
while makers lose ~10%. The spread IS the edge for makers.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, func

from app.services.kalshi_client import KalshiClient
from app.services.polymarket_client import PolymarketClient
from core.config import get_settings
from core.constants import MAX_CONCURRENT_POSITIONS, MAX_DAILY_DRAWDOWN_PCT
from database.models import (
    EdgeAnalysis,
    Market,
    Platform,
    Position,
    PositionSide,
    PositionStatus,
)

logger = logging.getLogger(__name__)


async def _count_open_positions(session: AsyncSession) -> int:
    """Count currently open/pending positions."""
    result = await session.execute(
        select(func.count()).select_from(Position).where(
            Position.status.in_([PositionStatus.OPEN, PositionStatus.PENDING])
        )
    )
    return result.scalar() or 0


async def execute_trade(
    edge: EdgeAnalysis,
    market: Market,
    session: AsyncSession,
) -> Position | None:
    """
    Execute a trade based on an EdgeAnalysis that passed the Kelly gate.

    Checks safety limits, places a limit order, and creates a Position record.

    Returns
    -------
    Position | None
        The created Position, or None if the trade was blocked.
    """
    if not edge.tradeable:
        logger.warning("execute_trade called on non-tradeable edge %d", edge.id)
        return None

    settings = get_settings()

    # --- Safety check: max concurrent positions ---
    open_count = await _count_open_positions(session)
    if open_count >= MAX_CONCURRENT_POSITIONS:
        logger.warning(
            "Max concurrent positions reached (%d/%d). Skipping.",
            open_count, MAX_CONCURRENT_POSITIONS,
        )
        return None

    # --- Safety check: daily drawdown kill-switch ---
    today = datetime.now(timezone.utc).date()
    closed_today_result = await session.execute(
        select(Position).where(
            Position.closed_at.isnot(None),
        )
    )
    closed_today_positions = closed_today_result.scalars().all()
    daily_realized = sum(
        p.pnl_dollars or 0.0
        for p in closed_today_positions
        if p.closed_at and p.closed_at.date() == today
    )
    drawdown_limit = settings.BANKROLL * MAX_DAILY_DRAWDOWN_PCT
    if daily_realized < -drawdown_limit:
        logger.warning(
            "Daily drawdown kill-switch triggered: realized=$%.2f, limit=-$%.2f. Blocking trade.",
            daily_realized, drawdown_limit,
        )
        return None

    # --- Calculate order parameters ---
    if edge.recommended_side == PositionSide.YES:
        entry_price = market.yes_price
    else:
        entry_price = market.no_price

    total_cost = entry_price * edge.num_contracts

    # --- Place order ---
    platform_order_id = None

    if market.platform == Platform.KALSHI:
        platform_order_id = await _place_kalshi_order(
            ticker=market.platform_market_id,
            side=edge.recommended_side,
            count=edge.num_contracts,
            price=entry_price,
        )
    elif market.platform == Platform.POLYMARKET:
        platform_order_id = await _place_polymarket_order(
            token_id=market.platform_market_id,
            side=edge.recommended_side,
            size=edge.num_contracts,
            price=entry_price,
        )

    # --- Create Position record ---
    position = Position(
        market_id=market.id,
        edge_analysis_id=edge.id,
        platform=market.platform,
        side=edge.recommended_side,
        num_contracts=edge.num_contracts,
        entry_price=round(entry_price, 4),
        total_cost=round(total_cost, 2),
        status=PositionStatus.PENDING,
        platform_order_id=platform_order_id,
        opened_at=datetime.now(timezone.utc),
    )

    session.add(position)
    await session.commit()
    await session.refresh(position)

    logger.info(
        "Order placed: market=%s side=%s contracts=%d price=%.2f cost=$%.2f order_id=%s",
        market.platform_market_id, edge.recommended_side.value,
        edge.num_contracts, entry_price, total_cost, platform_order_id,
    )

    return position


# ------------------------------------------------------------------
# Platform-specific order placement
# ------------------------------------------------------------------

async def _place_kalshi_order(
    ticker: str,
    side: PositionSide,
    count: int,
    price: float,
) -> str | None:
    """Place a limit order on Kalshi. Returns order ID or None."""
    client = KalshiClient()
    try:
        # Kalshi prices are in cents (1-99)
        price_cents = max(1, min(99, int(price * 100)))

        result = await client.place_order(
            ticker=ticker,
            side=side.value,
            action="buy",
            count=count,
            price=price_cents,
            order_type="limit",
        )

        order_id = result.get("order_id", result.get("id"))
        logger.info("Kalshi order placed: %s", order_id)
        return str(order_id) if order_id else None

    except Exception as exc:
        logger.error("Kalshi order failed: %s", exc)
        return None
    finally:
        await client.close()


async def _place_polymarket_order(
    token_id: str,
    side: PositionSide,
    size: int,
    price: float,
) -> str | None:
    """Place a limit order on Polymarket. Returns order ID or None."""
    client = PolymarketClient()
    try:
        # Polymarket: BUY YES or BUY NO
        poly_side = "BUY"

        result = await client.place_order(
            token_id=token_id,
            side=poly_side,
            price=price,
            size=float(size),
        )

        order_id = result.get("orderID", result.get("id"))
        logger.info("Polymarket order placed: %s", order_id)
        return str(order_id) if order_id else None

    except Exception as exc:
        logger.error("Polymarket order failed: %s", exc)
        return None
    finally:
        await client.close()


# ------------------------------------------------------------------
# Position management
# ------------------------------------------------------------------

async def close_position(
    position: Position,
    session: AsyncSession,
    exit_price: float | None = None,
) -> Position:
    """
    Close a position early (before market resolution).

    If exit_price is not provided, marks as closed with no P&L calculation.
    """
    if exit_price is not None:
        if position.side == PositionSide.YES:
            pnl = (exit_price - position.entry_price) * position.num_contracts
        else:
            pnl = (position.entry_price - exit_price) * position.num_contracts

        position.exit_price = round(exit_price, 4)
        position.pnl_dollars = round(pnl, 2)
        position.pnl_percent = round(pnl / position.total_cost * 100, 2) if position.total_cost > 0 else 0.0

    position.status = PositionStatus.CLOSED_EARLY
    position.closed_at = datetime.now(timezone.utc)

    session.add(position)
    await session.commit()

    logger.info(
        "Position %d closed: pnl=$%.2f (%.2f%%)",
        position.id, position.pnl_dollars or 0, position.pnl_percent or 0,
    )

    return position
