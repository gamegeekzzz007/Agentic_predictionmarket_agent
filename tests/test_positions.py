"""
tests/test_positions.py
Tests for position closing logic in the execution service.
"""

import pytest
import pytest_asyncio
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.execution import close_position
from database.models import Platform, Position, PositionSide, PositionStatus


def _make_position(**overrides) -> Position:
    """Helper to create a Position with sensible defaults."""
    defaults = dict(
        id=1,
        market_id=1,
        platform=Platform.KALSHI,
        side=PositionSide.YES,
        num_contracts=10,
        entry_price=0.50,
        total_cost=5.00,
        status=PositionStatus.OPEN,
        opened_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return Position(**defaults)


@pytest.mark.asyncio
class TestClosePosition:
    async def test_close_yes_position_profit(self, async_db_session: AsyncSession):
        """Closing a YES position at higher price should yield profit."""
        pos = _make_position(side=PositionSide.YES, entry_price=0.40, num_contracts=10, total_cost=4.00)
        async_db_session.add(pos)
        await async_db_session.commit()
        await async_db_session.refresh(pos)

        closed = await close_position(pos, async_db_session, exit_price=0.60)

        assert closed.status == PositionStatus.CLOSED_EARLY
        assert closed.exit_price == 0.60
        # P&L = (0.60 - 0.40) * 10 = $2.00
        assert closed.pnl_dollars == pytest.approx(2.00, abs=0.01)
        assert closed.pnl_percent > 0

    async def test_close_no_position_profit(self, async_db_session: AsyncSession):
        """Closing a NO position when yes price drops should yield profit."""
        pos = _make_position(side=PositionSide.NO, entry_price=0.60, num_contracts=10, total_cost=6.00)
        async_db_session.add(pos)
        await async_db_session.commit()
        await async_db_session.refresh(pos)

        # For NO side: pnl = (entry_price - exit_price) * contracts
        closed = await close_position(pos, async_db_session, exit_price=0.40)

        assert closed.status == PositionStatus.CLOSED_EARLY
        # P&L = (0.60 - 0.40) * 10 = $2.00
        assert closed.pnl_dollars == pytest.approx(2.00, abs=0.01)

    async def test_close_without_exit_price(self, async_db_session: AsyncSession):
        """Closing without exit_price should set status but leave P&L null."""
        pos = _make_position()
        async_db_session.add(pos)
        await async_db_session.commit()
        await async_db_session.refresh(pos)

        closed = await close_position(pos, async_db_session)

        assert closed.status == PositionStatus.CLOSED_EARLY
        assert closed.pnl_dollars is None
        assert closed.exit_price is None
        assert closed.closed_at is not None
