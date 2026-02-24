"""
agents/research_desk/researcher.py
Web research agent — searches for current information about a market
question and produces a probability estimate.

Uses smolagents CodeAgent + Tavily search tool + LiteLLM (OpenClaw).
"""

import json
import logging
import re

from smolagents import CodeAgent, LiteLLMModel, Tool
from tavily import TavilyClient

from agents import EstimateResult
from core.config import get_settings

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Tavily search tool (smolagents-compatible)
# ------------------------------------------------------------------

class TavilySearchTool(Tool):
    """Search the web via Tavily API."""

    name = "web_search"
    description = (
        "Search the web for current information. "
        "Returns relevant results with titles, URLs, and content snippets."
    )
    inputs = {
        "query": {
            "type": "string",
            "description": "The search query.",
        }
    }
    output_type = "string"

    def __init__(self, api_key: str) -> None:
        super().__init__()
        self._client = TavilyClient(api_key=api_key)

    def forward(self, query: str) -> str:
        response = self._client.search(query, max_results=5)
        results = response.get("results", [])
        if not results:
            return "No results found."
        lines: list[str] = []
        for r in results:
            lines.append(f"[{r['title']}]({r['url']})")
            lines.append(r.get("content", "")[:400])
            lines.append("")
        return "\n".join(lines)


# ------------------------------------------------------------------
# Parse agent output
# ------------------------------------------------------------------

def _parse_estimate(raw: str) -> dict:
    """Best-effort parse of agent output into probability fields."""
    # Try JSON
    try:
        match = re.search(r"\{[^{}]+\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
    except (json.JSONDecodeError, TypeError):
        pass

    # Regex fallback for key fields
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

    return result


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def run_research_desk(
    market_title: str,
    market_description: str | None,
    yes_price: float,
    category: str,
) -> EstimateResult:
    """
    Run the research desk agent for a single market.

    Returns an EstimateResult with probability, confidence, and reasoning.
    """
    settings = get_settings()

    model = LiteLLMModel(
        model_id=f"openai/{settings.OPENCLAW_MODEL}",
        api_base=settings.OPENCLAW_BASE_URL,
        api_key=settings.OPENCLAW_API_KEY,
    )

    search_tool = TavilySearchTool(api_key=settings.TAVILY_API_KEY)

    agent = CodeAgent(
        tools=[search_tool],
        model=model,
        verbosity_level=0,
    )

    prompt = f"""You are a research analyst estimating probabilities for prediction markets.

Market: "{market_title}"
Resolution criteria: "{market_description or 'Standard resolution'}"
Category: {category}
Current market price: {yes_price} (implies {yes_price*100:.1f}% probability)

Your job:
1. Search for the most relevant, recent information about this question
2. Identify key factors that affect the outcome
3. Estimate the TRUE probability (0.00 to 1.00) based on your research
4. Do NOT anchor on the market price - form your own independent view

Return ONLY a JSON object with these exact keys:
{{"probability": 0.XX, "confidence": 0.XX, "reasoning": "2-3 sentences"}}"""

    try:
        raw_result = agent.run(prompt)
        parsed = _parse_estimate(str(raw_result))

        probability = parsed.get("probability", 0.5)
        # Clamp to valid range
        probability = max(0.01, min(0.99, probability))

        return EstimateResult(
            desk="research",
            agent_name="research_analyst",
            probability=probability,
            confidence=parsed.get("confidence", 0.5),
            reasoning=parsed.get("reasoning", str(raw_result)[:500]),
        )
    except Exception as exc:
        logger.error("Research desk failed: %s", exc)
        return EstimateResult(
            desk="research",
            agent_name="research_analyst",
            probability=yes_price,  # Fall back to market price
            confidence=0.1,
            reasoning=f"Research desk failed: {exc}",
        )
