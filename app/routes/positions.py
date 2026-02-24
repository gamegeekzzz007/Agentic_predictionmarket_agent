"""
app/routes/positions.py
Position management endpoints — view, summarize, and close positions.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, func, col

from app.services.execution import close_position
from database.connection import get_session
from database.models import Position, PositionSide, PositionStatus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/positions", tags=["positions"])


# ------------------------------------------------------------------
# Response models
# ------------------------------------------------------------------

class PositionRow(BaseModel):
    """A single position."""
    id: int
    market_id: int
    platform: str
    side: str
    num_contracts: int
    entry_price: float
    total_cost: float
    exit_price: float | None
    pnl_dollars: float | None
    pnl_percent: float | None
    status: str
    platform_order_id: str | None
    opened_at: str
    closed_at: str | None


class PositionsResponse(BaseModel):
    """Response for GET /positions."""
    count: int
    positions: list[PositionRow]


class PortfolioSummary(BaseModel):
    """Response for GET /positions/summary."""
    total_positions: int
    open_positions: int
    closed_positions: int
    total_invested: float
    total_pnl: float
    win_rate: float | None
    best_trade_pnl: float | None
    worst_trade_pnl: float | None


class DailyPnlResponse(BaseModel):
    """Response for GET /portfolio/daily-pnl."""
    date: str
    realized_pnl: float
    open_positions: int
    drawdown_limit_pct: float
    kill_switch_active: bool


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _position_to_row(p: Position) -> PositionRow:
    return PositionRow(
        id=p.id,
        market_id=p.market_id,
        platform=p.platform.value,
        side=p.side.value,
        num_contracts=p.num_contracts,
        entry_price=p.entry_price,
        total_cost=p.total_cost,
        exit_price=p.exit_price,
        pnl_dollars=p.pnl_dollars,
        pnl_percent=p.pnl_percent,
        status=p.status.value,
        platform_order_id=p.platform_order_id,
        opened_at=p.opened_at.isoformat() if p.opened_at else "",
        closed_at=p.closed_at.isoformat() if p.closed_at else None,
    )


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.get("", response_model=PositionsResponse)
async def list_positions(
    status: str | None = Query(None, description="Filter by status: pending, open, closed_win, closed_loss, closed_early"),
    platform: str | None = Query(None, description="Filter by platform: kalshi, polymarket"),
    session: AsyncSession = Depends(get_session),
) -> PositionsResponse:
    """List all positions, optionally filtered."""
    query = select(Position).order_by(col(Position.opened_at).desc())

    if status:
        query = query.where(Position.status == status)
    if platform:
        query = query.where(Position.platform == platform)

    rows = (await session.execute(query)).scalars().all()
    positions = [_position_to_row(p) for p in rows]

    return PositionsResponse(count=len(positions), positions=positions)


@router.get("/summary", response_model=PortfolioSummary)
async def portfolio_summary(
    session: AsyncSession = Depends(get_session),
) -> PortfolioSummary:
    """Portfolio summary: total invested, P&L, win rate."""
    all_positions = (await session.execute(select(Position))).scalars().all()

    if not all_positions:
        return PortfolioSummary(
            total_positions=0, open_positions=0, closed_positions=0,
            total_invested=0.0, total_pnl=0.0, win_rate=None,
            best_trade_pnl=None, worst_trade_pnl=None,
        )

    open_statuses = {PositionStatus.OPEN, PositionStatus.PENDING}
    closed_statuses = {PositionStatus.CLOSED_WIN, PositionStatus.CLOSED_LOSS, PositionStatus.CLOSED_EARLY}

    open_positions = [p for p in all_positions if p.status in open_statuses]
    closed_positions = [p for p in all_positions if p.status in closed_statuses]

    total_invested = sum(p.total_cost for p in all_positions)
    total_pnl = sum(p.pnl_dollars or 0.0 for p in closed_positions)

    wins = [p for p in closed_positions if (p.pnl_dollars or 0) > 0]
    win_rate = len(wins) / len(closed_positions) if closed_positions else None

    pnls = [p.pnl_dollars for p in closed_positions if p.pnl_dollars is not None]
    best = max(pnls) if pnls else None
    worst = min(pnls) if pnls else None

    return PortfolioSummary(
        total_positions=len(all_positions),
        open_positions=len(open_positions),
        closed_positions=len(closed_positions),
        total_invested=round(total_invested, 2),
        total_pnl=round(total_pnl, 2),
        win_rate=round(win_rate, 4) if win_rate is not None else None,
        best_trade_pnl=round(best, 2) if best is not None else None,
        worst_trade_pnl=round(worst, 2) if worst is not None else None,
    )


@router.post("/{position_id}/close")
async def close_position_endpoint(
    position_id: int,
    exit_price: float | None = Query(None, description="Exit price per contract (optional)"),
    session: AsyncSession = Depends(get_session),
) -> PositionRow:
    """Manually close a position early."""
    position = (await session.execute(
        select(Position).where(Position.id == position_id)
    )).scalars().first()

    if not position:
        raise HTTPException(status_code=404, detail="Position not found")

    if position.status not in (PositionStatus.OPEN, PositionStatus.PENDING):
        raise HTTPException(status_code=400, detail=f"Position already {position.status.value}")

    updated = await close_position(position, session, exit_price=exit_price)
    return _position_to_row(updated)


@router.get("/daily-pnl", response_model=DailyPnlResponse)
async def daily_pnl(
    session: AsyncSession = Depends(get_session),
) -> DailyPnlResponse:
    """Today's P&L and kill-switch status."""
    from core.constants import MAX_DAILY_DRAWDOWN_PCT
    from core.config import get_settings
    settings = get_settings()

    today = datetime.now(timezone.utc).date()

    # Get positions closed today
    closed_today = (await session.execute(
        select(Position).where(
            Position.closed_at.isnot(None),
        )
    )).scalars().all()

    daily_realized = sum(
        p.pnl_dollars or 0.0
        for p in closed_today
        if p.closed_at and p.closed_at.date() == today
    )

    # Open position count
    open_count = (await session.execute(
        select(func.count()).select_from(Position).where(
            Position.status.in_([PositionStatus.OPEN, PositionStatus.PENDING])
        )
    )).scalar() or 0

    # Kill-switch: triggered if daily loss exceeds threshold
    drawdown_limit = settings.BANKROLL * (MAX_DAILY_DRAWDOWN_PCT)
    kill_switch = daily_realized < -drawdown_limit

    return DailyPnlResponse(
        date=today.isoformat(),
        realized_pnl=round(daily_realized, 2),
        open_positions=open_count,
        drawdown_limit_pct=MAX_DAILY_DRAWDOWN_PCT * 100,
        kill_switch_active=kill_switch,
    )
