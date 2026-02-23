"""
app/routes/portfolio.py
Portfolio-level endpoints (daily PnL, kill-switch status).
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.constants import DEFAULT_REFERENCE_EQUITY, MAX_DAILY_DRAWDOWN_PCT, STATUS_CLOSED
from database.connection import get_session
from database.models import Trade

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


class DailyPnlResponse(BaseModel):
    """Summary of today's realised PnL and drawdown status."""

    date: str
    realized_pnl: float
    trade_count: int
    drawdown_limit: float
    drawdown_remaining: float
    kill_switch_active: bool


@router.get("/daily-pnl", response_model=DailyPnlResponse)
async def daily_pnl(
    session: AsyncSession = Depends(get_session),
) -> DailyPnlResponse:
    """Sum realized_pnl for trades closed today (UTC). Report kill-switch status."""
    today = datetime.now(timezone.utc)
    today_start = today.replace(hour=0, minute=0, second=0, microsecond=0)

    stmt = select(Trade).where(
        Trade.status == STATUS_CLOSED,
        Trade.closed_at >= today_start,
        Trade.realized_pnl.is_not(None),  # type: ignore[union-attr]
    )
    result = await session.execute(stmt)
    trades = result.scalars().all()

    realized_pnl = sum(t.realized_pnl for t in trades if t.realized_pnl is not None)
    drawdown_limit = DEFAULT_REFERENCE_EQUITY * MAX_DAILY_DRAWDOWN_PCT
    drawdown_remaining = drawdown_limit + realized_pnl  # pnl is negative when losing
    kill_switch_active = realized_pnl <= -drawdown_limit

    return DailyPnlResponse(
        date=today.strftime("%Y-%m-%d"),
        realized_pnl=round(realized_pnl, 2),
        trade_count=len(trades),
        drawdown_limit=round(drawdown_limit, 2),
        drawdown_remaining=round(max(drawdown_remaining, 0.0), 2),
        kill_switch_active=kill_switch_active,
    )
