"""
app/routes/calibration.py
Calibration endpoints — Brier score tracking, per-agent accuracy, chart data.
"""

import logging
from collections import defaultdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, func

from database.connection import get_session
from database.models import CalibrationRecord, MarketCategory

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/calibration", tags=["calibration"])


# ------------------------------------------------------------------
# Response models
# ------------------------------------------------------------------

class CalibrationOverview(BaseModel):
    """Response for GET /calibration."""
    overall_brier_score: float | None
    num_resolved_markets: int
    per_category_scores: dict[str, float]


class AgentCalibration(BaseModel):
    """One agent's calibration stats."""
    agent_name: str
    brier_score: float | None
    num_predictions: int
    calibration_trend: str
    recent_accuracy: float | None


class AgentsCalibrationResponse(BaseModel):
    """Response for GET /calibration/agents."""
    agents: list[AgentCalibration]


class CalibrationBin(BaseModel):
    """One bin in the calibration chart."""
    bin_lower: float
    bin_upper: float
    predicted_avg: float | None
    actual_frequency: float | None
    count: int


class CalibrationChartResponse(BaseModel):
    """Response for GET /calibration/chart."""
    bins: list[CalibrationBin]
    total_predictions: int


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _brier_score_for_agent(
    records: list[CalibrationRecord],
    field: str,
) -> tuple[float | None, int]:
    """Calculate Brier score for a specific agent field."""
    valid = [
        (getattr(r, field), 1.0 if r.actual_outcome else 0.0)
        for r in records
        if getattr(r, field) is not None
    ]
    if not valid:
        return None, 0
    total = sum((pred - actual) ** 2 for pred, actual in valid)
    return round(total / len(valid), 4), len(valid)


def _calibration_trend(
    records: list[CalibrationRecord],
    field: str,
    window: int = 10,
) -> str:
    """Determine if agent is improving, degrading, or stable."""
    valid = [
        r for r in records
        if getattr(r, field) is not None
    ]
    if len(valid) < window * 2:
        return "stable"

    # Sort by resolved_at
    valid.sort(key=lambda r: r.resolved_at)
    older = valid[-window * 2:-window]
    recent = valid[-window:]

    def avg_brier(subset: list) -> float:
        scores = [(getattr(r, field) - (1.0 if r.actual_outcome else 0.0)) ** 2 for r in subset]
        return sum(scores) / len(scores) if scores else 0.0

    older_score = avg_brier(older)
    recent_score = avg_brier(recent)

    diff = recent_score - older_score
    if diff < -0.02:
        return "improving"
    elif diff > 0.02:
        return "degrading"
    return "stable"


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.get("", response_model=CalibrationOverview)
async def get_calibration(
    session: AsyncSession = Depends(get_session),
) -> CalibrationOverview:
    """Overall Brier score and per-category breakdown."""
    records = (await session.execute(select(CalibrationRecord))).scalars().all()

    if not records:
        return CalibrationOverview(
            overall_brier_score=None,
            num_resolved_markets=0,
            per_category_scores={},
        )

    # Overall Brier score
    overall = sum(r.brier_score for r in records) / len(records)

    # Per-category
    by_category: dict[str, list[float]] = defaultdict(list)
    for r in records:
        by_category[r.category.value].append(r.brier_score)

    per_category = {
        cat: round(sum(scores) / len(scores), 4)
        for cat, scores in by_category.items()
    }

    return CalibrationOverview(
        overall_brier_score=round(overall, 4),
        num_resolved_markets=len(records),
        per_category_scores=per_category,
    )


@router.get("/agents", response_model=AgentsCalibrationResponse)
async def get_agent_calibration(
    session: AsyncSession = Depends(get_session),
) -> AgentsCalibrationResponse:
    """Per-desk Brier scores and calibration trends."""
    records = (await session.execute(
        select(CalibrationRecord).order_by(CalibrationRecord.resolved_at)
    )).scalars().all()

    agents_config = [
        ("research_desk", "research_estimate"),
        ("base_rate_desk", "base_rate_estimate"),
        ("model_desk", "model_estimate"),
    ]

    agents = []
    for agent_name, field in agents_config:
        brier, count = _brier_score_for_agent(records, field)
        trend = _calibration_trend(records, field)

        # Recent accuracy: last 10 predictions
        valid_recent = [
            r for r in records
            if getattr(r, field) is not None
        ][-10:]
        if valid_recent:
            recent_brier = sum(
                (getattr(r, field) - (1.0 if r.actual_outcome else 0.0)) ** 2
                for r in valid_recent
            ) / len(valid_recent)
            recent_acc = round(1.0 - recent_brier, 4)
        else:
            recent_acc = None

        agents.append(AgentCalibration(
            agent_name=agent_name,
            brier_score=brier,
            num_predictions=count,
            calibration_trend=trend,
            recent_accuracy=recent_acc,
        ))

    return AgentsCalibrationResponse(agents=agents)


@router.get("/chart", response_model=CalibrationChartResponse)
async def get_calibration_chart(
    session: AsyncSession = Depends(get_session),
) -> CalibrationChartResponse:
    """Calibration chart data — 10 probability bins."""
    records = (await session.execute(select(CalibrationRecord))).scalars().all()

    # 10 bins: [0.0-0.1), [0.1-0.2), ..., [0.9-1.0]
    bin_edges = [i / 10.0 for i in range(11)]
    bins_data: list[dict] = []

    for i in range(10):
        lower = bin_edges[i]
        upper = bin_edges[i + 1]

        # Records in this bin
        in_bin = [
            r for r in records
            if lower <= r.system_probability < upper
            or (i == 9 and r.system_probability == 1.0)
        ]

        if in_bin:
            predicted_avg = sum(r.system_probability for r in in_bin) / len(in_bin)
            actual_freq = sum(1.0 if r.actual_outcome else 0.0 for r in in_bin) / len(in_bin)
        else:
            predicted_avg = None
            actual_freq = None

        bins_data.append({
            "lower": lower,
            "upper": upper,
            "predicted_avg": round(predicted_avg, 4) if predicted_avg is not None else None,
            "actual_frequency": round(actual_freq, 4) if actual_freq is not None else None,
            "count": len(in_bin),
        })

    return CalibrationChartResponse(
        bins=[
            CalibrationBin(
                bin_lower=b["lower"],
                bin_upper=b["upper"],
                predicted_avg=b["predicted_avg"],
                actual_frequency=b["actual_frequency"],
                count=b["count"],
            )
            for b in bins_data
        ],
        total_predictions=len(records),
    )
