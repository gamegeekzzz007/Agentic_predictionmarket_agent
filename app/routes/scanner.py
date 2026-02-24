"""
app/routes/scanner.py
Scanner endpoints — trigger scans, view results, scan history.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, func, col

from app.services.scanner_service import run_scan
from database.connection import get_session
from database.models import Market, MarketStatus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scan", tags=["scanner"])


# ------------------------------------------------------------------
# Response models
# ------------------------------------------------------------------

class ScanRunResponse(BaseModel):
    """Response for POST /scan/run."""
    scan_id: str
    total_fetched: int
    qualifying: int
    new_markets: int
    updated_markets: int
    errors: list[str]


class MarketRow(BaseModel):
    """A single market row for scan results."""
    id: int
    platform: str
    market_id: str
    title: str
    category: str
    yes_price: float
    no_price: float
    spread: float
    volume_24h: int
    days_to_expiry: int | None
    status: str


class ScanResultsResponse(BaseModel):
    """Response for GET /scan/results."""
    count: int
    markets: list[MarketRow]


class ScanHistoryEntry(BaseModel):
    """One entry in scan history."""
    timestamp: str
    total_markets: int
    platforms: dict[str, int]
    categories: dict[str, int]


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.post("/run", response_model=ScanRunResponse)
async def trigger_scan(
    session: AsyncSession = Depends(get_session),
) -> ScanRunResponse:
    """Trigger a full scan cycle across all platforms."""
    logger.info("Manual scan triggered")
    result = await run_scan(session)
    return ScanRunResponse(**result.to_dict())


@router.get("/results", response_model=ScanResultsResponse)
async def get_scan_results(
    platform: str | None = None,
    category: str | None = None,
    min_volume: int = 0,
    sort_by: str = "volume",
    session: AsyncSession = Depends(get_session),
) -> ScanResultsResponse:
    """
    Get all qualifying markets from the database.
    These are markets that passed the scanner filters.
    """
    query = select(Market).where(Market.status == MarketStatus.ACTIVE)

    if platform:
        query = query.where(Market.platform == platform)
    if category:
        query = query.where(Market.category == category)
    if min_volume > 0:
        query = query.where(col(Market.volume_24h) >= min_volume)

    if sort_by == "spread":
        query = query.order_by(col(Market.spread).asc())
    elif sort_by == "expiry":
        query = query.order_by(col(Market.days_to_expiry).asc())
    else:
        query = query.order_by(col(Market.volume_24h).desc())

    rows = (await session.execute(query)).scalars().all()

    markets = [
        MarketRow(
            id=m.id,
            platform=m.platform.value,
            market_id=m.platform_market_id,
            title=m.title,
            category=m.category.value,
            yes_price=m.yes_price,
            no_price=m.no_price,
            spread=m.spread,
            volume_24h=m.volume_24h,
            days_to_expiry=m.days_to_expiry,
            status=m.status.value,
        )
        for m in rows
    ]

    return ScanResultsResponse(count=len(markets), markets=markets)


@router.get("/history", response_model=ScanHistoryEntry)
async def get_scan_history(
    session: AsyncSession = Depends(get_session),
) -> ScanHistoryEntry:
    """Get a summary of all tracked markets (latest snapshot)."""
    total = (await session.execute(
        select(func.count()).select_from(Market).where(Market.status == MarketStatus.ACTIVE)
    )).scalar() or 0

    # Platform breakdown
    platform_rows = (await session.execute(
        select(Market.platform, func.count())
        .where(Market.status == MarketStatus.ACTIVE)
        .group_by(Market.platform)
    )).all()
    platforms = {str(row[0].value): row[1] for row in platform_rows}

    # Category breakdown
    category_rows = (await session.execute(
        select(Market.category, func.count())
        .where(Market.status == MarketStatus.ACTIVE)
        .group_by(Market.category)
    )).all()
    categories = {str(row[0].value): row[1] for row in category_rows}

    return ScanHistoryEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        total_markets=total,
        platforms=platforms,
        categories=categories,
    )
