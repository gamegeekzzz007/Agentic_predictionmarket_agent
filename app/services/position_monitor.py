"""
app/services/position_monitor.py
Monitors pending order fills and enforces per-position stop-losses.

Called by the scheduler every 60 seconds.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from core.constants import STOP_LOSS_PCT
from database.connection import async_session
from database.models import Market, Platform, Position, PositionSide, PositionStatus

logger = logging.getLogger(__name__)


async def check_pending_fills(session: AsyncSession) -> int:
    """
    Check PENDING positions for order fill status on the platform.

    Returns the number of positions transitioned.
    """
    from app.services.kalshi_client import KalshiClient
    from app.services.polymarket_client import PolymarketClient

    pending = (await session.execute(
        select(Position).where(Position.status == PositionStatus.PENDING)
    )).scalars().all()

    if not pending:
        return 0

    transitioned = 0
    kalshi_client = None
    poly_client = None

    try:
        for position in pending:
            if not position.platform_order_id:
                logger.warning("Position %d has no platform_order_id, skipping", position.id)
                continue

            try:
                if position.platform == Platform.KALSHI:
                    if kalshi_client is None:
                        kalshi_client = KalshiClient()
                    order = await kalshi_client.get_order(position.platform_order_id)
                    status = order.get("status", "").lower()

                    if status in ("filled", "executed"):
                        position.status = PositionStatus.OPEN
                        session.add(position)
                        transitioned += 1
                        logger.info("Position %d filled on Kalshi", position.id)
                    elif status in ("canceled", "cancelled", "expired", "rejected"):
                        position.status = PositionStatus.CANCELLED
                        position.closed_at = datetime.now(timezone.utc)
                        session.add(position)
                        transitioned += 1
                        logger.info("Position %d cancelled on Kalshi: %s", position.id, status)

                elif position.platform == Platform.POLYMARKET:
                    # Polymarket CLOB doesn't have a simple get-order endpoint;
                    # check if we have open positions on the token
                    if poly_client is None:
                        poly_client = PolymarketClient()
                    # For now, transition to OPEN after a reasonable fill assumption
                    # A more robust approach would check the orderbook or trades
                    logger.debug("Polymarket fill check for position %d — manual review recommended", position.id)

            except Exception as exc:
                logger.error("Error checking fill for position %d: %s", position.id, exc)

        if transitioned:
            await session.commit()

    finally:
        if kalshi_client:
            await kalshi_client.close()
        if poly_client:
            await poly_client.close()

    return transitioned


async def check_stop_losses(session: AsyncSession) -> int:
    """
    Check OPEN positions for stop-loss triggers.

    If unrealized loss exceeds STOP_LOSS_PCT (5%) of entry cost, auto-close.

    Returns the number of positions closed.
    """
    from app.services.kalshi_client import KalshiClient
    from app.services.polymarket_client import PolymarketClient

    open_positions = (await session.execute(
        select(Position, Market).join(Market, Position.market_id == Market.id).where(
            Position.status == PositionStatus.OPEN
        )
    )).all()

    if not open_positions:
        return 0

    closed_count = 0
    kalshi_client = None
    poly_client = None

    try:
        for position, market in open_positions:
            try:
                # Fetch current market price
                current_yes_price = None

                if market.platform == Platform.KALSHI:
                    if kalshi_client is None:
                        kalshi_client = KalshiClient()
                    mkt = await kalshi_client.get_market(market.platform_market_id)
                    current_yes_price = (mkt.get("yes_ask") or mkt.get("last_price") or 50) / 100.0

                elif market.platform == Platform.POLYMARKET:
                    if poly_client is None:
                        poly_client = PolymarketClient()
                    price_data = await poly_client.get_price(market.platform_market_id)
                    current_yes_price = float(price_data.get("mid", 0.5))

                if current_yes_price is None:
                    continue

                # Calculate unrealized P&L
                if position.side == PositionSide.YES:
                    unrealized_pnl = (current_yes_price - position.entry_price) * position.num_contracts
                else:
                    unrealized_pnl = (position.entry_price - current_yes_price) * position.num_contracts

                # Stop-loss check: loss exceeds STOP_LOSS_PCT of total cost
                loss_threshold = -(position.total_cost * STOP_LOSS_PCT)
                if unrealized_pnl < loss_threshold:
                    exit_price = current_yes_price
                    position.exit_price = round(exit_price, 4)
                    position.pnl_dollars = round(unrealized_pnl, 2)
                    position.pnl_percent = round(
                        unrealized_pnl / position.total_cost * 100, 2
                    ) if position.total_cost > 0 else 0.0
                    position.status = PositionStatus.CLOSED_LOSS
                    position.closed_at = datetime.now(timezone.utc)
                    session.add(position)
                    closed_count += 1

                    logger.warning(
                        "Stop-loss triggered for position %d: unrealized=$%.2f, threshold=$%.2f",
                        position.id, unrealized_pnl, loss_threshold,
                    )

            except Exception as exc:
                logger.error("Error checking stop-loss for position %d: %s", position.id, exc)

        if closed_count:
            await session.commit()

    finally:
        if kalshi_client:
            await kalshi_client.close()
        if poly_client:
            await poly_client.close()

    return closed_count


async def run_position_monitor() -> None:
    """Entry point called by the scheduler every 60 seconds."""
    async with async_session() as session:
        fills = await check_pending_fills(session)
        stops = await check_stop_losses(session)
        if fills or stops:
            logger.info("Position monitor: %d fills transitioned, %d stop-losses triggered", fills, stops)
