"""
database/models.py
SQLModel table definitions for the Agentic Prediction Market system.
Replaces the old equity-trading Trade/AuditLog models entirely.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import SQLModel, Field


def _utcnow() -> datetime:
    """Timezone-aware UTC now (replaces the deprecated utcnow call)."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Platform(str, Enum):
    KALSHI = "kalshi"
    POLYMARKET = "polymarket"


class MarketCategory(str, Enum):
    ECONOMICS = "economics"
    POLITICS = "politics"
    WEATHER = "weather"
    CRYPTO = "crypto"
    SPORTS = "sports"
    ENTERTAINMENT = "entertainment"
    OTHER = "other"


class MarketStatus(str, Enum):
    ACTIVE = "active"
    RESOLVED_YES = "resolved_yes"
    RESOLVED_NO = "resolved_no"
    EXPIRED = "expired"


class PositionSide(str, Enum):
    YES = "yes"
    NO = "no"


class PositionStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    CLOSED_WIN = "closed_win"
    CLOSED_LOSS = "closed_loss"
    CLOSED_EARLY = "closed_early"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Market — a single prediction market contract
# ---------------------------------------------------------------------------

class Market(SQLModel, table=True):
    """A single prediction market contract tracked by the system."""

    id: Optional[int] = Field(default=None, primary_key=True)

    # Platform identifiers
    platform: Platform
    platform_market_id: str = Field(index=True)
    platform_event_id: Optional[str] = None

    # Market details
    title: str
    category: MarketCategory
    description: Optional[str] = None
    resolution_source: Optional[str] = None

    # Pricing (updated on each scan)
    yes_price: float
    no_price: float
    spread: float
    volume_24h: int = 0

    # Timing
    close_time: Optional[datetime] = None
    resolution_time: Optional[datetime] = None
    days_to_expiry: Optional[int] = None

    # Status
    status: MarketStatus = MarketStatus.ACTIVE
    resolved_outcome: Optional[bool] = None

    # Metadata
    first_seen: datetime = Field(default_factory=_utcnow)
    last_updated: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# ProbabilityEstimate — one agent desk's estimate per scan cycle
# ---------------------------------------------------------------------------

class ProbabilityEstimate(SQLModel, table=True):
    """One agent desk's probability estimate for a market."""

    id: Optional[int] = Field(default=None, primary_key=True)

    market_id: int = Field(foreign_key="market.id", index=True)
    scan_id: str = Field(index=True)

    # Which desk produced this
    desk: str
    agent_name: Optional[str] = None

    # The estimate
    probability: float
    confidence: float
    reasoning: str

    # For model desk: which model was used
    model_type: Optional[str] = None

    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# EdgeAnalysis — Kelly gate output for a market
# ---------------------------------------------------------------------------

class EdgeAnalysis(SQLModel, table=True):
    """The final edge calculation and Kelly sizing for a market."""

    id: Optional[int] = Field(default=None, primary_key=True)

    market_id: int = Field(foreign_key="market.id", index=True)
    scan_id: str = Field(index=True)

    # Consensus probability
    system_probability: float
    market_price: float

    # Edge calculation
    edge: float
    expected_value: float

    # Kelly sizing
    kelly_fraction: float
    half_kelly_fraction: float
    position_size_dollars: float
    num_contracts: int

    # Decision
    recommended_side: PositionSide
    tradeable: bool
    rejection_reason: Optional[str] = None

    # Debate metadata
    debate_triggered: bool = False
    debate_transcript: Optional[str] = None
    estimates_divergence: float = 0.0

    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Position — an actual bet we placed
# ---------------------------------------------------------------------------

class Position(SQLModel, table=True):
    """An active or closed position in a prediction market."""

    id: Optional[int] = Field(default=None, primary_key=True)

    market_id: int = Field(foreign_key="market.id", index=True)
    edge_analysis_id: Optional[int] = Field(default=None, foreign_key="edgeanalysis.id")

    # Position details
    platform: Platform
    side: PositionSide
    num_contracts: int
    entry_price: float
    total_cost: float

    # Exit details (filled when position closes)
    exit_price: Optional[float] = None
    pnl_dollars: Optional[float] = None
    pnl_percent: Optional[float] = None

    # Status
    status: PositionStatus = PositionStatus.PENDING

    # Platform order ID for tracking
    platform_order_id: Optional[str] = None

    # Timing
    opened_at: datetime = Field(default_factory=_utcnow)
    closed_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# CalibrationRecord — post-resolution accuracy tracking
# ---------------------------------------------------------------------------

class CalibrationRecord(SQLModel, table=True):
    """Tracks prediction accuracy after markets resolve."""

    id: Optional[int] = Field(default=None, primary_key=True)

    market_id: int = Field(foreign_key="market.id", index=True)

    # What we predicted
    system_probability: float
    market_price_at_entry: float

    # What actually happened
    actual_outcome: bool

    # Accuracy metrics
    brier_score: float

    # Per-desk accuracy
    research_estimate: Optional[float] = None
    base_rate_estimate: Optional[float] = None
    model_estimate: Optional[float] = None

    # Category for per-category calibration
    category: MarketCategory

    resolved_at: datetime = Field(default_factory=_utcnow)
