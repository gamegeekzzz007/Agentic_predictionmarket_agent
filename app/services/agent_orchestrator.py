"""
app/services/agent_orchestrator.py
Multi-agent orchestrator using LangGraph (state machine) + smolagents (agent nodes).

Graph topology:
    START -> [research_desk, base_rate_desk, model_desk] (parallel fan-out)
          -> consensus_node (fan-in: median or flag for debate)
          -> END

Phase 4 will add: debate subgraph when divergence > 10%.
"""

import asyncio
import logging
import statistics
from typing import Annotated, Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from agents import EstimateResult
from agents.research_desk.researcher import run_research_desk
from agents.base_rate_desk.base_rate import run_base_rate_desk
from agents.model_desk.statistical_model import run_model_desk
from core.constants import DEBATE_DIVERGENCE_THRESHOLD

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State definition
# ---------------------------------------------------------------------------

def _merge_estimates(left: list, right: list) -> list:
    """Reducer: merge estimate lists."""
    return left + right


class PipelineState(TypedDict):
    """Shared state for the probability estimation pipeline."""
    # Input
    market_title: str
    market_description: str
    yes_price: float
    category: str

    # Accumulated estimates from desks (reducer: append)
    estimates: Annotated[list[dict], _merge_estimates]

    # Output (set by consensus node)
    system_probability: float
    divergence: float
    debate_needed: bool
    consensus_reasoning: str


# ---------------------------------------------------------------------------
# Desk nodes (each runs an agent and appends its estimate)
# ---------------------------------------------------------------------------

def research_node(state: PipelineState) -> dict:
    """Run the research desk agent."""
    result = run_research_desk(
        market_title=state["market_title"],
        market_description=state["market_description"],
        yes_price=state["yes_price"],
        category=state["category"],
    )
    logger.info("Research desk: p=%.3f conf=%.2f", result.probability, result.confidence)
    return {"estimates": [_estimate_to_dict(result)]}


def base_rate_node(state: PipelineState) -> dict:
    """Run the base rate desk agent."""
    result = run_base_rate_desk(
        market_title=state["market_title"],
        market_description=state["market_description"],
        yes_price=state["yes_price"],
        category=state["category"],
    )
    logger.info("Base rate desk: p=%.3f conf=%.2f", result.probability, result.confidence)
    return {"estimates": [_estimate_to_dict(result)]}


def model_node(state: PipelineState) -> dict:
    """Run the statistical model desk agent."""
    result = run_model_desk(
        market_title=state["market_title"],
        market_description=state["market_description"],
        yes_price=state["yes_price"],
        category=state["category"],
    )
    logger.info("Model desk: p=%.3f conf=%.2f", result.probability, result.confidence)
    return {"estimates": [_estimate_to_dict(result)]}


def _estimate_to_dict(est: EstimateResult) -> dict:
    """Convert EstimateResult to a serializable dict."""
    return {
        "desk": est.desk,
        "agent_name": est.agent_name,
        "probability": est.probability,
        "confidence": est.confidence,
        "reasoning": est.reasoning,
        "model_type": est.model_type,
    }


# ---------------------------------------------------------------------------
# Consensus node (fan-in: combine estimates)
# ---------------------------------------------------------------------------

def consensus_node(state: PipelineState) -> dict:
    """
    Combine estimates from all desks into a single probability.

    If divergence > DEBATE_DIVERGENCE_THRESHOLD: flag for debate (Phase 4).
    Otherwise: take the weighted median by confidence.
    """
    estimates = state["estimates"]
    if not estimates:
        return {
            "system_probability": state["yes_price"],
            "divergence": 0.0,
            "debate_needed": False,
            "consensus_reasoning": "No estimates produced — falling back to market price.",
        }

    probabilities = [e["probability"] for e in estimates]
    confidences = [e["confidence"] for e in estimates]

    # Divergence = max - min
    divergence = max(probabilities) - min(probabilities)

    # Debate threshold check
    debate_needed = divergence > DEBATE_DIVERGENCE_THRESHOLD

    # Weighted average by confidence (more confident agents get more weight)
    total_weight = sum(confidences)
    if total_weight > 0:
        weighted_avg = sum(p * c for p, c in zip(probabilities, confidences)) / total_weight
    else:
        weighted_avg = statistics.median(probabilities)

    # If divergence is small, use median (more robust to outliers)
    # If divergence is large, use weighted average (confidence matters more)
    if debate_needed:
        system_probability = weighted_avg
        method = "weighted_avg (debate flagged)"
    else:
        system_probability = statistics.median(probabilities)
        method = "median"

    # Build reasoning summary
    desk_summaries = []
    for e in estimates:
        desk_summaries.append(f"{e['desk']}: {e['probability']:.3f} (conf={e['confidence']:.2f})")

    reasoning = (
        f"Method: {method} | Divergence: {divergence:.3f} | "
        f"Estimates: {', '.join(desk_summaries)}"
    )

    logger.info(
        "Consensus: p=%.3f divergence=%.3f debate=%s method=%s",
        system_probability, divergence, debate_needed, method,
    )

    return {
        "system_probability": round(system_probability, 4),
        "divergence": round(divergence, 4),
        "debate_needed": debate_needed,
        "consensus_reasoning": reasoning,
    }


# ---------------------------------------------------------------------------
# Graph wiring
# ---------------------------------------------------------------------------

def _build_graph() -> StateGraph:
    """Construct the LangGraph state machine."""
    graph = StateGraph(PipelineState)

    graph.add_node("research", research_node)
    graph.add_node("base_rate", base_rate_node)
    graph.add_node("model", model_node)
    graph.add_node("consensus", consensus_node)

    # Fan-out: START -> all three desks in parallel
    graph.add_edge(START, "research")
    graph.add_edge(START, "base_rate")
    graph.add_edge(START, "model")

    # Fan-in: all desks -> consensus
    graph.add_edge("research", "consensus")
    graph.add_edge("base_rate", "consensus")
    graph.add_edge("model", "consensus")

    # consensus -> END
    graph.add_edge("consensus", END)

    return graph


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def run_probability_estimation(
    market_title: str,
    market_description: str,
    yes_price: float,
    category: str,
) -> dict[str, Any]:
    """
    Run the full probability estimation pipeline for a single market.

    Returns dict with: system_probability, divergence, debate_needed,
    consensus_reasoning, and all individual estimates.
    """
    graph = _build_graph()
    compiled = graph.compile()

    initial_state: PipelineState = {
        "market_title": market_title,
        "market_description": market_description,
        "yes_price": yes_price,
        "category": category,
        "estimates": [],
        "system_probability": 0.0,
        "divergence": 0.0,
        "debate_needed": False,
        "consensus_reasoning": "",
    }

    # LangGraph invoke is synchronous; run in thread to keep event loop free
    final_state = await asyncio.to_thread(compiled.invoke, initial_state)

    return {
        "system_probability": final_state["system_probability"],
        "divergence": final_state["divergence"],
        "debate_needed": final_state["debate_needed"],
        "consensus_reasoning": final_state["consensus_reasoning"],
        "estimates": final_state["estimates"],
    }
