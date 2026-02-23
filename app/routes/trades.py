"""
app/routes/trades.py
Trade evaluation and CRUD endpoints.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.services.alpaca import AlpacaService, get_alpaca_service
from core.constants import (
    DEFAULT_REFERENCE_EQUITY,
    MAX_DAILY_DRAWDOWN_PCT,
    STATUS_CLOSED,
    STATUS_FAILED,
    STATUS_PENDING,
    STOP_LOSS_PCT,
    STRATEGY_VERSION,
)
from core.math_utils import TradeSignal, evaluate_trade
from database.connection import get_session
from database.models import AuditLog, Trade

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trades", tags=["trades"])


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------


class EvaluateRequest(BaseModel):
    """Input for pure math evaluation (no DB write)."""

    symbol: str = Field(..., min_length=1, max_length=10)
    p_win: float = Field(..., ge=0.0, le=1.0)
    profit_pct: float = Field(..., gt=0.0)
    loss_pct: float = Field(..., gt=0.0)
    reasoning: Optional[str] = None

    @field_validator("symbol")
    @classmethod
    def uppercase_symbol(cls, v: str) -> str:
        return v.upper()


class CreateTradeRequest(EvaluateRequest):
    """Full trade submission — extends EvaluateRequest with order details."""

    side: str = Field(..., pattern=r"^(buy|sell)$")
    quantity: float = Field(..., gt=0.0)
    limit_price: Optional[float] = Field(default=None, gt=0.0)
    meta_data: Optional[Dict[str, Any]] = None


class EvaluateResponse(BaseModel):
    """Mirrors TradeSignal fields."""

    symbol: str
    p_win: float
    profit_pct: float
    loss_pct: float
    ev: float
    kelly_fraction: float
    position_pct: float
    tradeable: bool

    @classmethod
    def from_signal(cls, signal: TradeSignal) -> "EvaluateResponse":
        return cls(
            symbol=signal.symbol,
            p_win=signal.p_win,
            profit_pct=signal.profit_pct,
            loss_pct=signal.loss_pct,
            ev=signal.ev,
            kelly_fraction=signal.kelly_fraction,
            position_pct=signal.position_pct,
            tradeable=signal.tradeable,
        )


class AuditLogResponse(BaseModel):
    """Serialisable view of an AuditLog row."""

    id: int
    trade_id: int
    p_win: float
    profit_pct: float
    loss_pct: float
    ev: float
    kelly_fraction: float
    position_pct: float
    tradeable: bool
    reasoning: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class TradeResponse(BaseModel):
    """Serialisable view of a Trade row with optional nested audit_log."""

    id: int
    symbol: str
    side: str
    status: str
    strategy_version: str
    quantity: float
    limit_price: Optional[float]
    entry_price: Optional[float]
    exit_price: Optional[float]
    stop_loss_price: Optional[float]
    realized_pnl: Optional[float]
    alpaca_order_id: Optional[str]
    created_at: datetime
    closed_at: Optional[datetime]
    meta_data: Optional[Dict[str, Any]]
    audit_log: Optional[AuditLogResponse]

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_today_realized_pnl(session: AsyncSession) -> float:
    """Sum realized_pnl for trades closed today (UTC)."""
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    stmt = select(Trade).where(
        Trade.status == STATUS_CLOSED,
        Trade.closed_at >= today_start,
        Trade.realized_pnl.is_not(None),  # type: ignore[union-attr]
    )
    result = await session.execute(stmt)
    trades = result.scalars().all()
    return sum(t.realized_pnl for t in trades if t.realized_pnl is not None)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/evaluate", response_model=EvaluateResponse)
async def evaluate_signal(body: EvaluateRequest) -> EvaluateResponse:
    """Pure math evaluation — no DB write."""
    signal = evaluate_trade(
        symbol=body.symbol,
        p_win=body.p_win,
        profit_pct=body.profit_pct,
        loss_pct=body.loss_pct,
    )
    return EvaluateResponse.from_signal(signal)


@router.post("", response_model=TradeResponse, status_code=201)
async def create_trade(
    body: CreateTradeRequest,
    session: AsyncSession = Depends(get_session),
    alpaca: AlpacaService = Depends(get_alpaca_service),
) -> TradeResponse:
    """Evaluate -> gate on EV -> gate on drawdown -> persist Trade + AuditLog."""

    # 1. Math evaluation
    signal = evaluate_trade(
        symbol=body.symbol,
        p_win=body.p_win,
        profit_pct=body.profit_pct,
        loss_pct=body.loss_pct,
    )

    # 2. EV gate
    if not signal.tradeable:
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "negative_ev",
                "ev": signal.ev,
                "message": f"Trade rejected: EV is {signal.ev:.6f} (must be > 0).",
            },
        )

    # 3. Drawdown gate
    realized_pnl = await _get_today_realized_pnl(session)
    drawdown_limit = DEFAULT_REFERENCE_EQUITY * MAX_DAILY_DRAWDOWN_PCT
    if realized_pnl <= -drawdown_limit:
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "drawdown_kill_switch",
                "realized_pnl": realized_pnl,
                "drawdown_limit": -drawdown_limit,
                "message": "Daily drawdown limit hit. No new trades allowed today.",
            },
        )

    # 4. Calculate stop-loss price
    stop_loss_price: Optional[float] = None
    if body.limit_price is not None:
        if body.side == "buy":
            stop_loss_price = round(body.limit_price * (1 - STOP_LOSS_PCT), 2)
        else:
            stop_loss_price = round(body.limit_price * (1 + STOP_LOSS_PCT), 2)

    # 5. Create Trade
    trade = Trade(
        symbol=body.symbol,
        side=body.side,
        status=STATUS_PENDING,
        strategy_version=STRATEGY_VERSION,
        quantity=body.quantity,
        limit_price=body.limit_price,
        stop_loss_price=stop_loss_price,
        meta_data=body.meta_data,
    )
    session.add(trade)
    await session.flush()  # get trade.id

    # 6. Create AuditLog
    audit = AuditLog.from_trade_signal(
        trade_id=trade.id,  # type: ignore[arg-type]
        signal=signal,
        reasoning=body.reasoning,
    )
    session.add(audit)

    # 7. Submit order to Alpaca
    try:
        if body.limit_price is not None:
            order_result = await alpaca.submit_limit_order(
                symbol=body.symbol,
                qty=body.quantity,
                side=body.side,
                limit_price=body.limit_price,
            )
        else:
            order_result = await alpaca.submit_market_order(
                symbol=body.symbol,
                qty=body.quantity,
                side=body.side,
            )
        trade.alpaca_order_id = order_result.order_id
        trade.status = order_result.status
        if order_result.filled_avg_price is not None:
            trade.entry_price = order_result.filled_avg_price
    except HTTPException:
        trade.status = STATUS_FAILED
        await session.commit()
        raise

    await session.commit()

    # 8. Refresh to load relationship
    await session.refresh(trade, attribute_names=["audit_log"])

    return TradeResponse.from_orm(trade) if hasattr(TradeResponse, "from_orm") else TradeResponse.model_validate(trade)


@router.get("", response_model=List[TradeResponse])
async def list_trades(
    status: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
) -> List[TradeResponse]:
    """List trades, optionally filtered by status. Ordered by created_at DESC."""
    stmt = select(Trade).options(selectinload(Trade.audit_log)).order_by(Trade.created_at.desc())  # type: ignore[arg-type]
    if status is not None:
        stmt = stmt.where(Trade.status == status)
    result = await session.execute(stmt)
    trades = result.scalars().all()
    return [TradeResponse.model_validate(t) for t in trades]


@router.get("/{trade_id}", response_model=TradeResponse)
async def get_trade(
    trade_id: int,
    session: AsyncSession = Depends(get_session),
) -> TradeResponse:
    """Get a single trade with its audit_log."""
    stmt = (
        select(Trade)
        .options(selectinload(Trade.audit_log))
        .where(Trade.id == trade_id)
    )
    result = await session.execute(stmt)
    trade = result.scalar_one_or_none()
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")
    return TradeResponse.model_validate(trade)
