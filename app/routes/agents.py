"""
app/routes/agents.py
POST /run-agents — triggers the multi-agent orchestrator and returns structured results.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.agent_orchestrator import run_orchestrator
from core.math_utils import evaluate_trade

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/run-agents", tags=["agents"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class BacktestQuantResult(BaseModel):
    """Parsed backtest output from the quant agent."""

    ticker: Optional[str] = None
    p_win: Optional[float] = None
    profit_pct: Optional[float] = None
    loss_pct: Optional[float] = None
    side: Optional[str] = None
    reasoning: Optional[str] = None
    raw_output: Optional[str] = None


class EVAnalysis(BaseModel):
    """EV / Kelly sizing computed from backtest numbers."""

    ev: float
    kelly_fraction: float
    position_pct: float
    tradeable: bool


class RunAgentsResponse(BaseModel):
    """Full response from the agent pipeline."""

    news_catalyst: str
    theses: List[str]
    verified_facts: List[str]
    backtest_results: BacktestQuantResult
    ev_analysis: Optional[EVAnalysis] = None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("", response_model=RunAgentsResponse)
async def run_agents() -> RunAgentsResponse:
    """Run the full 4-agent pipeline and return structured results."""

    try:
        state = await run_orchestrator()
    except Exception as exc:
        err_msg = str(exc)
        logger.exception("Orchestrator failed")
        raise HTTPException(
            status_code=502,
            detail=f"Agent pipeline failed: {err_msg[:300]}",
        ) from exc

    # --- Parse backtest_results into BacktestQuantResult ---
    raw_backtest: dict = state.get("backtest_results", {})

    try:
        backtest = BacktestQuantResult(
            ticker=raw_backtest.get("ticker"),
            p_win=float(raw_backtest["p_win"]) if "p_win" in raw_backtest else None,
            profit_pct=float(raw_backtest["profit_pct"]) if "profit_pct" in raw_backtest else None,
            loss_pct=float(raw_backtest["loss_pct"]) if "loss_pct" in raw_backtest else None,
            side=raw_backtest.get("side"),
            reasoning=raw_backtest.get("reasoning"),
            raw_output=raw_backtest.get("raw_output"),
        )
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("Could not parse backtest fields: %s", exc)
        backtest = BacktestQuantResult(raw_output=str(raw_backtest))

    # --- Compute EV analysis if all quant fields are present ---
    ev_analysis: Optional[EVAnalysis] = None

    if (
        backtest.ticker is not None
        and backtest.p_win is not None
        and backtest.profit_pct is not None
        and backtest.loss_pct is not None
        and backtest.profit_pct > 0
        and backtest.loss_pct > 0
    ):
        try:
            signal = evaluate_trade(
                symbol=backtest.ticker,
                p_win=backtest.p_win,
                profit_pct=backtest.profit_pct,
                loss_pct=backtest.loss_pct,
            )
            ev_analysis = EVAnalysis(
                ev=signal.ev,
                kelly_fraction=signal.kelly_fraction,
                position_pct=signal.position_pct,
                tradeable=signal.tradeable,
            )
        except ValueError as exc:
            logger.warning("evaluate_trade failed: %s", exc)

    return RunAgentsResponse(
        news_catalyst=state.get("news_catalyst", ""),
        theses=state.get("theses", []),
        verified_facts=state.get("verified_facts", []),
        backtest_results=backtest,
        ev_analysis=ev_analysis,
    )
