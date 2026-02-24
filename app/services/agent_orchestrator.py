"""
app/services/agent_orchestrator.py
Multi-agent orchestrator using LangGraph (state machine) + smolagents (agent nodes).

Graph topology:
    START -> [research_desk, base_rate_desk, model_desk] (parallel fan-out)
          -> consensus_node (fan-in: check divergence)
          -> IF divergence > 10%: debate_node -> END
          -> ELSE: END (median estimate)
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
from agents.debate.chatroom import run_debate
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

    # Consensus output
    system_probability: float
    divergence: float
    debate_needed: bool
    consensus_reasoning: str

    # Debate output
    debate_transcript: list[dict]
    debate_rounds: int
    debate_converged: bool


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
# Consensus node (fan-in: combine estimates, check divergence)
# ---------------------------------------------------------------------------

def consensus_node(state: PipelineState) -> dict:
    """
    Combine estimates from all desks.
    If divergence <= threshold: take median and finish.
    If divergence > threshold: flag for debate.
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
    divergence = max(probabilities) - min(probabilities)
    debate_needed = divergence > DEBATE_DIVERGENCE_THRESHOLD

    if not debate_needed:
        system_probability = statistics.median(probabilities)
        method = "median"
    else:
        # Temporary: weighted average (debate will refine this)
        total_weight = sum(confidences)
        if total_weight > 0:
            system_probability = sum(p * c for p, c in zip(probabilities, confidences)) / total_weight
        else:
            system_probability = statistics.median(probabilities)
        method = "weighted_avg (pre-debate)"

    desk_summaries = [
        f"{e['desk']}: {e['probability']:.3f} (conf={e['confidence']:.2f})"
        for e in estimates
    ]
    reasoning = (
        f"Method: {method} | Divergence: {divergence:.3f} | "
        f"Estimates: {', '.join(desk_summaries)}"
    )

    logger.info(
        "Consensus: p=%.3f divergence=%.3f debate=%s",
        system_probability, divergence, debate_needed,
    )

    return {
        "system_probability": round(system_probability, 4),
        "divergence": round(divergence, 4),
        "debate_needed": debate_needed,
        "consensus_reasoning": reasoning,
    }


# ---------------------------------------------------------------------------
# Debate node (only runs when divergence is high)
# ---------------------------------------------------------------------------

def debate_node(state: PipelineState) -> dict:
    """Run the debate chatroom to resolve divergent estimates."""
    logger.info("Debate triggered — divergence=%.3f", state["divergence"])

    result = run_debate(
        market_title=state["market_title"],
        market_description=state["market_description"],
        yes_price=state["yes_price"],
        category=state["category"],
        estimates=state["estimates"],
    )

    # Update system probability with debate consensus
    reasoning = (
        f"Debate result: converged={result['converged']}, "
        f"rounds={result['rounds_used']}, "
        f"consensus={result['consensus_probability']:.3f}"
    )

    logger.info(
        "Debate complete: p=%.3f converged=%s rounds=%d",
        result["consensus_probability"], result["converged"], result["rounds_used"],
    )

    return {
        "system_probability": result["consensus_probability"],
        "debate_transcript": result["transcript"],
        "debate_rounds": result["rounds_used"],
        "debate_converged": result["converged"],
        "consensus_reasoning": reasoning,
    }


# ---------------------------------------------------------------------------
# Conditional routing
# ---------------------------------------------------------------------------

def _should_debate(state: PipelineState) -> str:
    """Route to debate if needed, otherwise end."""
    if state.get("debate_needed", False):
        return "debate"
    return END


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
    graph.add_node("debate", debate_node)

    # Fan-out: START -> all three desks in parallel
    graph.add_edge(START, "research")
    graph.add_edge(START, "base_rate")
    graph.add_edge(START, "model")

    # Fan-in: all desks -> consensus
    graph.add_edge("research", "consensus")
    graph.add_edge("base_rate", "consensus")
    graph.add_edge("model", "consensus")

    # Conditional: consensus -> debate or END
    graph.add_conditional_edges("consensus", _should_debate, {"debate": "debate", END: END})

    # debate -> END
    graph.add_edge("debate", END)

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
    consensus_reasoning, estimates, and debate fields if triggered.
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
        "debate_transcript": [],
        "debate_rounds": 0,
        "debate_converged": False,
    }

    # LangGraph invoke is synchronous; run in thread to keep event loop free
    final_state = await asyncio.to_thread(compiled.invoke, initial_state)

    return {
        "system_probability": final_state["system_probability"],
        "divergence": final_state["divergence"],
        "debate_needed": final_state["debate_needed"],
        "consensus_reasoning": final_state["consensus_reasoning"],
        "estimates": final_state["estimates"],
        "debate_transcript": final_state.get("debate_transcript", []),
        "debate_rounds": final_state.get("debate_rounds", 0),
        "debate_converged": final_state.get("debate_converged", False),
    }
