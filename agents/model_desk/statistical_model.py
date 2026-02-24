"""
agents/model_desk/statistical_model.py
Statistical model agent — uses Bayesian reasoning and quantitative
analysis to produce a calibrated probability estimate.

Uses smolagents CodeAgent + LiteLLM (OpenClaw).
No web search — this agent reasons from the prompt context and can
run Python code for statistical calculations.
"""

import json
import logging
import re

from smolagents import CodeAgent, LiteLLMModel

from agents import EstimateResult
from core.config import get_settings

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Parse agent output
# ------------------------------------------------------------------

def _parse_estimate(raw: str) -> dict:
    """Best-effort parse of agent output."""
    try:
        match = re.search(r"\{[^{}]+\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
    except (json.JSONDecodeError, TypeError):
        pass

    result: dict = {}
    prob_match = re.search(r"probability[\"']?\s*[:=]\s*([0-9.]+)", raw, re.IGNORECASE)
    if prob_match:
        result["probability"] = float(prob_match.group(1))
    conf_match = re.search(r"confidence[\"']?\s*[:=]\s*([0-9.]+)", raw, re.IGNORECASE)
    if conf_match:
        result["confidence"] = float(conf_match.group(1))
    reason_match = re.search(r"reasoning[\"']?\s*[:=]\s*[\"'](.+?)[\"']", raw, re.IGNORECASE | re.DOTALL)
    if reason_match:
        result["reasoning"] = reason_match.group(1)
    model_match = re.search(r"model_type[\"']?\s*[:=]\s*[\"'](.+?)[\"']", raw, re.IGNORECASE)
    if model_match:
        result["model_type"] = model_match.group(1)

    return result


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def run_model_desk(
    market_title: str,
    market_description: str | None,
    yes_price: float,
    category: str,
) -> EstimateResult:
    """
    Run the statistical model desk for a single market.

    This agent uses quantitative reasoning and can execute Python code
    for Bayesian calculations, simple regressions, or threshold analysis.
    """
    settings = get_settings()

    model = LiteLLMModel(
        model_id=f"openai/{settings.OPENCLAW_MODEL}",
        api_base=settings.OPENCLAW_BASE_URL,
        api_key=settings.OPENCLAW_API_KEY,
    )

    agent = CodeAgent(
        tools=[],
        model=model,
        verbosity_level=0,
        additional_authorized_imports=["math", "statistics"],
    )

    prompt = f"""You are a quantitative analyst building a statistical model for a prediction market.

Market: "{market_title}"
Resolution criteria: "{market_description or 'Standard resolution'}"
Category: {category}
Current market price: {yes_price} (implies {yes_price*100:.1f}% probability)

Your job:
1. Identify what quantitative framework best applies:
   - Bayesian: start with a prior, update with evidence
   - Threshold analysis: what conditions must be met for YES?
   - Mean reversion: is this event unusually priced vs fundamentals?
   - Trend extrapolation: what does the trend suggest?

2. Build a simple model using Python calculations if helpful

3. Produce a calibrated probability. Be honest about uncertainty.
   - If you have strong quantitative grounds, confidence should be 0.6-0.8
   - If the model is speculative, confidence should be 0.2-0.4
   - Never claim confidence > 0.85 for a single model

4. Do NOT simply copy the market price. Apply your own analysis.

Return ONLY a JSON object with these exact keys:
{{"probability": 0.XX, "confidence": 0.XX, "reasoning": "2-3 sentences about the model", "model_type": "bayesian|threshold|trend|mean_reversion"}}"""

    try:
        raw_result = agent.run(prompt)
        parsed = _parse_estimate(str(raw_result))

        probability = parsed.get("probability", 0.5)
        probability = max(0.01, min(0.99, probability))

        return EstimateResult(
            desk="model",
            agent_name="statistical_model",
            probability=probability,
            confidence=parsed.get("confidence", 0.4),
            reasoning=parsed.get("reasoning", str(raw_result)[:500]),
            model_type=parsed.get("model_type", "statistical"),
        )
    except Exception as exc:
        logger.error("Model desk failed: %s", exc)
        return EstimateResult(
            desk="model",
            agent_name="statistical_model",
            probability=yes_price,
            confidence=0.1,
            reasoning=f"Model desk failed: {exc}",
            model_type="fallback",
        )
