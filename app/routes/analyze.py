"""
app/routes/analyze.py
Analysis endpoint — runs the full pipeline for a single market:
  1. Fetch market from DB
  2. Run 3-desk probability estimation (+ debate if needed)
  3. Calculate edge via Kelly gate
  4. Return results (optionally execute trade)
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.services.agent_orchestrator import run_probability_estimation
from app.services.edge_calculator import calculate_edge
from app.services.execution import execute_trade
from core.config import get_settings
from database.connection import get_session
from database.models import EdgeAnalysis, Market

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/analyze", tags=["analyze"])


# ------------------------------------------------------------------
# Response models
# ------------------------------------------------------------------

class EstimateDetail(BaseModel):
    desk: str
    probability: float
    confidence: float
    reasoning: str


class AnalysisResponse(BaseModel):
    """Full analysis result for a market."""
    market_id: int
    market_title: str
    market_price: float

    # Agent estimates
    system_probability: float
    estimates: list[EstimateDetail]
    divergence: float
    debate_triggered: bool
    consensus_reasoning: str

    # Kelly gate
    recommended_side: str
    edge: float
    expected_value: float
    kelly_fraction: float
    half_kelly_fraction: float
    position_size_dollars: float
    num_contracts: int
    tradeable: bool
    rejection_reason: str | None

    # Execution (if requested)
    position_id: int | None = None
    order_placed: bool = False


# ------------------------------------------------------------------
# Endpoint
# ------------------------------------------------------------------

@router.post("/{market_id}", response_model=AnalysisResponse)
async def analyze_market(
    market_id: int,
    execute: bool = Query(False, description="If true, place the trade when tradeable"),
    session: AsyncSession = Depends(get_session),
) -> AnalysisResponse:
    """
    Run the full analysis pipeline for a single market.

    1. Fetches the market from the database
    2. Runs 3 agent desks in parallel to estimate probability
    3. Triggers debate if estimates diverge > 10%
    4. Calculates edge and Kelly sizing
    5. Optionally places a limit order if tradeable and execute=true
    """
    # --- Fetch market ---
    market = (await session.execute(
        select(Market).where(Market.id == market_id)
    )).scalars().first()

    if not market:
        raise HTTPException(status_code=404, detail="Market not found")

    logger.info("Analyzing market %d: %s (price=%.3f)", market.id, market.title, market.yes_price)

    # --- Run probability estimation ---
    estimation = await run_probability_estimation(
        market_title=market.title,
        market_description=market.description or market.title,
        yes_price=market.yes_price,
        category=market.category.value,
    )

    system_probability = estimation["system_probability"]
    estimates = estimation.get("estimates", [])

    # --- Calculate edge ---
    settings = get_settings()
    scan_id = f"analyze-{uuid.uuid4().hex[:8]}"

    edge_analysis = calculate_edge(
        system_probability=system_probability,
        market_price=market.yes_price,
        bankroll=settings.BANKROLL,
        scan_id=scan_id,
        market_id=market.id,
        estimates=estimates,
        debate_triggered=estimation.get("debate_needed", False),
        debate_transcript=str(estimation.get("debate_transcript", [])) if estimation.get("debate_transcript") else None,
    )

    # Save edge analysis to DB
    session.add(edge_analysis)
    await session.commit()
    await session.refresh(edge_analysis)

    # --- Optionally execute ---
    position_id = None
    order_placed = False

    if execute and edge_analysis.tradeable:
        position = await execute_trade(edge_analysis, market, session)
        if position:
            position_id = position.id
            order_placed = True
            logger.info("Trade executed: position %d", position.id)

    # --- Build response ---
    estimate_details = [
        EstimateDetail(
            desk=e.get("desk", "unknown"),
            probability=e.get("probability", 0.0),
            confidence=e.get("confidence", 0.0),
            reasoning=e.get("reasoning", "")[:500],
        )
        for e in estimates
    ]

    logger.info(
        "Analysis complete: market=%d system_p=%.3f edge=%.3f tradeable=%s",
        market.id, system_probability, edge_analysis.edge, edge_analysis.tradeable,
    )

    return AnalysisResponse(
        market_id=market.id,
        market_title=market.title,
        market_price=market.yes_price,
        system_probability=system_probability,
        estimates=estimate_details,
        divergence=estimation.get("divergence", 0.0),
        debate_triggered=estimation.get("debate_needed", False),
        consensus_reasoning=estimation.get("consensus_reasoning", ""),
        recommended_side=edge_analysis.recommended_side.value,
        edge=edge_analysis.edge,
        expected_value=edge_analysis.expected_value,
        kelly_fraction=edge_analysis.kelly_fraction,
        half_kelly_fraction=edge_analysis.half_kelly_fraction,
        position_size_dollars=edge_analysis.position_size_dollars,
        num_contracts=edge_analysis.num_contracts,
        tradeable=edge_analysis.tradeable,
        rejection_reason=edge_analysis.rejection_reason,
        position_id=position_id,
        order_placed=order_placed,
    )
