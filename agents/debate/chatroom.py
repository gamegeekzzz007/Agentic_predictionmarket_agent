"""
agents/debate/chatroom.py
Round-robin debate system for when agent estimates diverge by >10pp.

Debate flow:
  Round 1: Each agent posts their estimate + reasoning (opening statements)
  Round 2: Each agent critiques one other agent's estimate
  Round 3+: Open debate — agents defend or concede, update their estimates
  Max 5 rounds, then moderator forces consensus
  Convergence: all estimates within 5 percentage points

Uses LiteLLM routed through OpenClaw for all LLM calls.
"""

import logging
from typing import Any

from smolagents import LiteLLMModel

from core.config import get_settings
from core.constants import CONVERGENCE_THRESHOLD, MAX_DEBATE_ROUNDS

logger = logging.getLogger(__name__)


def _get_model() -> LiteLLMModel:
    """Create a LiteLLM model instance pointed at OpenClaw."""
    settings = get_settings()
    return LiteLLMModel(
        model_id=f"openai/{settings.OPENCLAW_MODEL}",
        api_base=settings.OPENCLAW_BASE_URL,
        api_key=settings.OPENCLAW_API_KEY,
    )


def _call_llm(model: LiteLLMModel, prompt: str) -> str:
    """Make a single LLM call and return the text response."""
    messages = [{"role": "user", "content": prompt}]
    response = model(messages, stop_sequences=None)
    # LiteLLMModel returns a ChatMessage; extract the text
    if hasattr(response, "content"):
        return response.content
    return str(response)


def _extract_updated_probability(text: str) -> float | None:
    """Try to extract an updated probability from agent debate response."""
    import re
    # Look for patterns like "updated probability: 0.XX" or "my estimate: 0.XX"
    patterns = [
        r"updated\s+(?:probability|estimate)[:\s]+([0-9]+\.?[0-9]*)",
        r"(?:my|revised|new|final)\s+(?:probability|estimate)[:\s]+([0-9]+\.?[0-9]*)",
        r"probability[:\s]+([0-9]+\.?[0-9]*)",
        r"([0-9]\.[0-9]{1,3})\s*(?:probability|chance|likelihood)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            val = float(match.group(1))
            if 0.0 < val < 1.0:
                return val
            elif 1.0 < val <= 100.0:
                return val / 100.0
    return None


# ------------------------------------------------------------------
# Core debate function
# ------------------------------------------------------------------

def run_debate(
    market_title: str,
    market_description: str,
    yes_price: float,
    category: str,
    estimates: list[dict],
) -> dict[str, Any]:
    """
    Run a multi-round debate between agent desks to resolve divergent estimates.

    Parameters
    ----------
    market_title : str
    market_description : str
    yes_price : float
    category : str
    estimates : list[dict]
        Initial estimates from the desks, each with 'desk', 'probability',
        'confidence', 'reasoning' keys.

    Returns
    -------
    dict with:
        consensus_probability: float
        converged: bool
        rounds_used: int
        transcript: list[dict]  — full debate log
    """
    model = _get_model()
    transcript: list[dict] = []

    # Current estimates (mutable — agents can update them)
    current_estimates: dict[str, float] = {
        e["desk"]: e["probability"] for e in estimates
    }
    reasoning_map: dict[str, str] = {
        e["desk"]: e["reasoning"] for e in estimates
    }

    desks = list(current_estimates.keys())

    # ---- Round 1: Opening statements ----
    for desk in desks:
        entry = {
            "round": 1,
            "agent": desk,
            "type": "opening",
            "message": (
                f"My estimate for '{market_title}' is {current_estimates[desk]:.3f}. "
                f"Reasoning: {reasoning_map[desk]}"
            ),
        }
        transcript.append(entry)

    logger.info("Debate round 1: opening statements from %d desks", len(desks))

    # ---- Rounds 2+: Critique and defend ----
    for round_num in range(2, MAX_DEBATE_ROUNDS + 1):
        # Check convergence
        probs = list(current_estimates.values())
        if max(probs) - min(probs) <= CONVERGENCE_THRESHOLD:
            logger.info("Debate converged at round %d", round_num)
            break

        # Build debate context
        debate_context = f"""Market: "{market_title}"
Description: "{market_description}"
Category: {category}
Current market price: {yes_price}

Current estimates:
"""
        for desk in desks:
            debate_context += f"  {desk}: {current_estimates[desk]:.3f}\n"

        debate_context += "\nDebate transcript so far:\n"
        for entry in transcript[-len(desks) * 2:]:  # Last 2 rounds of context
            debate_context += f"  [{entry['agent']}] {entry['message'][:300]}\n"

        # Each agent responds
        for desk in desks:
            other_desks = [d for d in desks if d != desk]

            if round_num == 2:
                # Round 2: critique another agent
                prompt = f"""{debate_context}

You are the {desk} desk. You must critique ONE other agent's estimate.
Pick the estimate you disagree with most and explain why their reasoning is flawed.
Then state your UPDATED probability (it can stay the same or change).

Format your response as:
CRITIQUE: [which desk you're critiquing and why]
UPDATED PROBABILITY: [0.XX]
REASONING: [1-2 sentences]"""
            else:
                # Round 3+: open debate
                prompt = f"""{debate_context}

You are the {desk} desk. Based on the critiques and arguments so far:
1. Have any valid points changed your view?
2. What is your UPDATED probability estimate?
3. Be willing to concede if the evidence is strong, but defend if you have data.

Format your response as:
RESPONSE: [address the strongest counter-argument]
UPDATED PROBABILITY: [0.XX]
REASONING: [1-2 sentences]"""

            try:
                response = _call_llm(model, prompt)
                updated_p = _extract_updated_probability(response)

                if updated_p is not None:
                    current_estimates[desk] = updated_p

                entry = {
                    "round": round_num,
                    "agent": desk,
                    "type": "critique" if round_num == 2 else "defense",
                    "message": response[:500],
                    "updated_probability": current_estimates[desk],
                }
                transcript.append(entry)

            except Exception as exc:
                logger.warning("Debate response failed for %s round %d: %s", desk, round_num, exc)
                transcript.append({
                    "round": round_num,
                    "agent": desk,
                    "type": "error",
                    "message": f"Failed to respond: {exc}",
                })

        logger.info(
            "Debate round %d: %s",
            round_num,
            {d: f"{p:.3f}" for d, p in current_estimates.items()},
        )

    # ---- Final consensus ----
    probs = list(current_estimates.values())
    converged = (max(probs) - min(probs)) <= CONVERGENCE_THRESHOLD

    if converged:
        # Take median of converged estimates
        import statistics
        consensus = statistics.median(probs)
        method = "median (converged)"
    else:
        # Moderator: use confidence-weighted average, biased toward conservative
        confidences = {e["desk"]: e["confidence"] for e in estimates}
        total_weight = sum(confidences.values())
        if total_weight > 0:
            consensus = sum(
                current_estimates[d] * confidences.get(d, 0.5)
                for d in desks
            ) / total_weight
        else:
            consensus = statistics.median(probs)

        # Safety bias: pull toward 0.5 slightly (conservative)
        consensus = consensus * 0.9 + 0.5 * 0.1
        method = "moderator_weighted (did not converge)"

        # Record moderator entry
        transcript.append({
            "round": MAX_DEBATE_ROUNDS + 1,
            "agent": "moderator",
            "type": "final_ruling",
            "message": (
                f"Agents did not converge after {MAX_DEBATE_ROUNDS} rounds. "
                f"Final estimates: {current_estimates}. "
                f"Using confidence-weighted average with conservative bias: {consensus:.3f}. "
                f"Method: {method}"
            ),
        })

    rounds_used = max(e["round"] for e in transcript) if transcript else 0

    logger.info(
        "Debate complete: consensus=%.3f converged=%s rounds=%d method=%s",
        consensus, converged, rounds_used, method,
    )

    return {
        "consensus_probability": round(consensus, 4),
        "converged": converged,
        "rounds_used": rounds_used,
        "transcript": transcript,
    }
