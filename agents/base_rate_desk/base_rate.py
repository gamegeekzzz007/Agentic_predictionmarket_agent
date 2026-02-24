"""
agents/base_rate_desk/base_rate.py
Historical base rate agent — computes frequency of similar events
occurring in the past to produce a probability estimate.

Uses smolagents CodeAgent + Tavily search + LiteLLM (OpenClaw).
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
# Search tool (reused pattern)
# ------------------------------------------------------------------

class TavilySearchTool(Tool):
    """Search the web for historical data."""

    name = "web_search"
    description = (
        "Search the web for historical data and base rates. "
        "Returns results with titles, URLs, and content."
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
    """Best-effort parse of agent output into base rate fields."""
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
    sample_match = re.search(r"sample_size[\"']?\s*[:=]\s*(\d+)", raw, re.IGNORECASE)
    if sample_match:
        result["sample_size"] = int(sample_match.group(1))

    return result


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def run_base_rate_desk(
    market_title: str,
    market_description: str | None,
    yes_price: float,
    category: str,
) -> EstimateResult:
    """
    Run the base rate desk agent for a single market.

    Focuses purely on historical frequencies — not current news.
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

    prompt = f"""You are a statistical analyst focused on base rates and historical frequencies.

Market: "{market_title}"
Category: {category}

Your job:
1. Find the historical base rate for this type of event
   - For economic data: "In the last N releases, how often did X exceed Y?"
   - For politics: "What is the base rate for this type of political outcome?"
   - For weather: "Historical frequency of this weather event"
   - For crypto: "How often has this price target been reached historically?"
2. Adjust for any known trend or structural change
3. Produce a probability based PURELY on historical frequencies

Do NOT use current news or sentiment. Only historical data and frequencies.

Return ONLY a JSON object with these exact keys:
{{"probability": 0.XX, "confidence": 0.XX, "reasoning": "2-3 sentences about the base rate", "sample_size": N}}"""

    try:
        raw_result = agent.run(prompt)
        parsed = _parse_estimate(str(raw_result))

        probability = parsed.get("probability", 0.5)
        probability = max(0.01, min(0.99, probability))

        extra = {}
        if "sample_size" in parsed:
            extra["sample_size"] = parsed["sample_size"]
        if "base_rate_raw" in parsed:
            extra["base_rate_raw"] = parsed["base_rate_raw"]

        return EstimateResult(
            desk="base_rate",
            agent_name="base_rate_analyst",
            probability=probability,
            confidence=parsed.get("confidence", 0.4),
            reasoning=parsed.get("reasoning", str(raw_result)[:500]),
            extra=extra,
        )
    except Exception as exc:
        logger.error("Base rate desk failed: %s", exc)
        return EstimateResult(
            desk="base_rate",
            agent_name="base_rate_analyst",
            probability=yes_price,
            confidence=0.1,
            reasoning=f"Base rate desk failed: {exc}",
        )
