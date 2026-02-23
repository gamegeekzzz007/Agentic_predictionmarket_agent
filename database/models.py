"""
database/models.py
SQLModel table definitions for the Agentic Trading system.
Trade lifecycle tracking + mathematical audit trail.
"""

from datetime import datetime
from typing import Any, Dict, Optional, TYPE_CHECKING

from sqlalchemy import Column, JSON
from sqlmodel import SQLModel, Field, Relationship

if TYPE_CHECKING:
    from core.math_utils import TradeSignal


# ---------------------------------------------------------------------------
# Trade — full lifecycle record
# ---------------------------------------------------------------------------

class Trade(SQLModel, table=True):
    """
    Tracks a trade from order placement through close.

    Status progression:
        pending  ->  filled  ->  closed
        pending  ->  cancelled
    """
    __tablename__ = "trades"

    id: Optional[int] = Field(default=None, primary_key=True)
    symbol: str = Field(index=True, max_length=10)
    side: str = Field(max_length=4)                       # "buy" | "sell"
    status: str = Field(default="pending", index=True, max_length=10)
    strategy_version: str = Field(default="v1.0", max_length=16)

    quantity: float
    limit_price: Optional[float] = Field(default=None)    # what we asked for
    entry_price: Optional[float] = Field(default=None)    # what we got
    exit_price: Optional[float] = Field(default=None)
    stop_loss_price: Optional[float] = Field(default=None)
    realized_pnl: Optional[float] = Field(default=None)

    alpaca_order_id: Optional[str] = Field(default=None, max_length=64)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    closed_at: Optional[datetime] = Field(default=None)

    # Flexible JSON blob for future data (e.g. greeks, tags)
    meta_data: Optional[Dict[str, Any]] = Field(
        default=None, sa_column=Column(JSON)
    )

    # One-to-one relationship: each Trade has exactly one AuditLog
    audit_log: Optional["AuditLog"] = Relationship(
        back_populates="trade",
        sa_relationship_kwargs={"uselist": False},
    )


# ---------------------------------------------------------------------------
# AuditLog — mathematical justification snapshot
# ---------------------------------------------------------------------------

class AuditLog(SQLModel, table=True):
    """
    Immutable snapshot of the math that justified a trade.
    Mirrors every field of core.math_utils.TradeSignal so we can
    always answer: 'Why did we take this trade?'
    """
    __tablename__ = "audit_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    trade_id: int = Field(foreign_key="trades.id", index=True, unique=True)

    # --- Math snapshot (mirrors TradeSignal) ---
    p_win: float
    profit_pct: float
    loss_pct: float
    ev: float
    kelly_fraction: float
    position_pct: float
    tradeable: bool

    reasoning: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Flexible JSON blob for future data (e.g. sentiment scores)
    meta_data: Optional[Dict[str, Any]] = Field(
        default=None, sa_column=Column(JSON)
    )

    # Back-reference to Trade
    trade: Optional[Trade] = Relationship(back_populates="audit_log")

    # --- Factory -----------------------------------------------------------

    @classmethod
    def from_trade_signal(
        cls,
        trade_id: int,
        signal: "TradeSignal",
        reasoning: Optional[str] = None,
        meta_data: Optional[Dict[str, Any]] = None,
    ) -> "AuditLog":
        """Create an AuditLog directly from a TradeSignal dataclass."""
        return cls(
            trade_id=trade_id,
            p_win=signal.p_win,
            profit_pct=signal.profit_pct,
            loss_pct=signal.loss_pct,
            ev=signal.ev,
            kelly_fraction=signal.kelly_fraction,
            position_pct=signal.position_pct,
            tradeable=signal.tradeable,
            reasoning=reasoning,
            meta_data=meta_data,
        )
