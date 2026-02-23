"""
app/services/agent_orchestrator.py
Multi-agent orchestrator using LangGraph (state machine) + smolagents (agent nodes).

Graph topology:
    START -> [scraper] -> [theorist, fact_checker] (parallel) -> [quant] -> END
"""

import asyncio
import ast
import json
import logging
import operator
import os
import re
from typing import Annotated, Any, Dict, List

from dotenv import load_dotenv

load_dotenv()  # ensure .env is loaded before model/tool init

from langgraph.graph import END, START, StateGraph
from smolagents import CodeAgent, LiteLLMModel, Tool
from tavily import TavilyClient
from typing_extensions import TypedDict

from core.config import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State definition
# ---------------------------------------------------------------------------


class AgentState(TypedDict):
    """Shared state passed between graph nodes."""

    news_catalyst: str  # overwritten by scraper
    theses: Annotated[List[str], operator.add]  # reducer: append
    verified_facts: Annotated[List[str], operator.add]  # reducer: append
    backtest_results: dict  # overwritten by quant


# ---------------------------------------------------------------------------
# Model & tool setup
# ---------------------------------------------------------------------------

# OpenClaw: OpenAI-compatible proxy → Claude (via LiteLLM)
_settings = get_settings()
model = LiteLLMModel(
    model_id=f"openai/{_settings.OPENCLAW_MODEL_ID}",
    api_base=_settings.OPENCLAW_BASE_URL,
    api_key=_settings.OPENCLAW_API_KEY,
)

# Tavily search client — uses TAVILY_API_KEY from .env
_tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY", ""))


class TavilySearchTool(Tool):
    """smolagents-compatible tool that searches the web via Tavily."""

    name = "web_search"
    description = (
        "Search the web for current information. "
        "Returns a list of relevant results with titles, URLs, and content snippets."
    )
    inputs = {
        "query": {
            "type": "string",
            "description": "The search query to look up.",
        }
    }
    output_type = "string"

    def forward(self, query: str) -> str:
        response = _tavily_client.search(query, max_results=5)
        results = response.get("results", [])
        if not results:
            return "No results found."
        lines: list[str] = []
        for r in results:
            lines.append(f"[{r['title']}]({r['url']})")
            lines.append(r.get("content", "")[:300])
            lines.append("")
        return "\n".join(lines)


def _get_search_tool() -> TavilySearchTool:
    """Return a fresh TavilySearchTool instance."""
    return TavilySearchTool()


# ---------------------------------------------------------------------------
# Node 1 — Scraper (finds today's macro headline)
# ---------------------------------------------------------------------------


def scraper_node(state: AgentState) -> dict:
    """Search the web for a breaking macroeconomic headline."""
    agent = CodeAgent(
        tools=[_get_search_tool()],
        model=model,
        verbosity_level=0,
    )
    prompt = (
        "Find one major breaking macroeconomic headline from today. "
        "Return only the headline and a one-sentence summary."
    )
    result = agent.run(prompt)
    logger.info("Scraper found: %s", result)
    return {"news_catalyst": str(result)}


# ---------------------------------------------------------------------------
# Node 2A — Theorist (parallel: generates a trading thesis)
# ---------------------------------------------------------------------------


def theorist_node(state: AgentState) -> dict:
    """Generate a second-order trading thesis from the news catalyst."""
    catalyst = state["news_catalyst"]
    agent = CodeAgent(
        tools=[],
        model=model,
        verbosity_level=0,
    )
    prompt = (
        f"Given this macroeconomic catalyst: '{catalyst}'. "
        "Generate a second-order trading thesis. "
        "Identify the most affected US-listed ticker symbol. "
        "Format: 'THESIS: ... | TICKER: ...'"
    )
    result = agent.run(prompt)
    logger.info("Theorist thesis: %s", result)
    return {"theses": [str(result)]}


# ---------------------------------------------------------------------------
# Node 2B — Fact-Checker (parallel: verifies the headline)
# ---------------------------------------------------------------------------


def fact_checker_node(state: AgentState) -> dict:
    """Verify the news catalyst against corroborating sources."""
    catalyst = state["news_catalyst"]
    agent = CodeAgent(
        tools=[_get_search_tool()],
        model=model,
        verbosity_level=0,
    )
    prompt = (
        f"Verify if this news headline is factually accurate: '{catalyst}'. "
        "Search for corroborating sources. "
        "Respond with exactly 'VERIFIED' or 'FALSE' followed by a brief explanation."
    )
    result = agent.run(prompt)
    logger.info("Fact-checker result: %s", result)
    return {"verified_facts": [str(result)]}


# ---------------------------------------------------------------------------
# Node 3 — Quant Sandbox (fan-in: backtests the thesis)
# ---------------------------------------------------------------------------


def _sanitize_numpy(obj: Any) -> Any:
    """Recursively convert numpy types to native Python types."""
    try:
        import numpy as np
        if isinstance(obj, dict):
            return {k: _sanitize_numpy(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_sanitize_numpy(v) for v in obj]
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, np.bool_):
            return bool(obj)
    except ImportError:
        pass
    return obj


def _strip_numpy_wrappers(raw: str) -> str:
    """Strip numpy type wrappers like np.float64(0.65) -> 0.65 from raw text."""
    return re.sub(r"(?:np|numpy)\.[\w]+\(([^)]+)\)", r"\1", raw)


def _parse_backtest_output(raw: str) -> Dict[str, Any]:
    """Best-effort parse of the CodeAgent's final answer into a dict."""
    raw = _strip_numpy_wrappers(raw)
    # Try JSON first
    try:
        return _sanitize_numpy(json.loads(raw))
    except (json.JSONDecodeError, TypeError):
        pass

    # Try Python literal
    try:
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, dict):
            return _sanitize_numpy(parsed)
    except (ValueError, SyntaxError):
        pass

    # Try extracting a dict-like substring
    match = re.search(r"\{[^{}]+\}", raw, re.DOTALL)
    if match:
        try:
            return _sanitize_numpy(json.loads(match.group()))
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(match.group())
                if isinstance(parsed, dict):
                    return _sanitize_numpy(parsed)
            except (ValueError, SyntaxError):
                pass

    # Fallback: return raw text wrapped in a dict
    return {"raw_output": raw}


def quant_sandbox_node(state: AgentState) -> dict:
    """Run a code-execution backtest based on the thesis and fact-check."""
    theses = state["theses"]
    verified_facts = state["verified_facts"]

    agent = CodeAgent(
        tools=[],
        model=model,
        verbosity_level=0,
        additional_authorized_imports=["yfinance", "pandas", "datetime"],
    )
    prompt = (
        f"Given thesis: {theses[0]} and verification: {verified_facts[0]}. "
        "Write and execute a Python backtest: download 30 days of price data "
        "for the ticker using yfinance, calculate a simple momentum signal, "
        "and compute the expected return and win rate. "
        "Return a dict with keys: 'ticker', 'p_win', 'profit_pct', "
        "'loss_pct', 'side', 'reasoning'."
    )
    result = agent.run(prompt)
    # If the agent returned a dict directly, sanitize numpy types; otherwise parse the string
    if isinstance(result, dict):
        parsed = _sanitize_numpy(result)
    else:
        parsed = _parse_backtest_output(str(result))
    logger.info("Quant sandbox results: %s", parsed)
    return {"backtest_results": parsed}


# ---------------------------------------------------------------------------
# Graph wiring
# ---------------------------------------------------------------------------


def _build_graph() -> StateGraph:
    """Construct the LangGraph state machine (not yet compiled)."""
    graph = StateGraph(AgentState)

    graph.add_node("scraper", scraper_node)
    graph.add_node("theorist", theorist_node)
    graph.add_node("fact_checker", fact_checker_node)
    graph.add_node("quant", quant_sandbox_node)

    # START -> scraper
    graph.add_edge(START, "scraper")

    # Fan-out: scraper -> theorist AND scraper -> fact_checker (parallel)
    graph.add_edge("scraper", "theorist")
    graph.add_edge("scraper", "fact_checker")

    # Fan-in: [theorist, fact_checker] -> quant
    graph.add_edge("theorist", "quant")
    graph.add_edge("fact_checker", "quant")

    # quant -> END
    graph.add_edge("quant", END)

    return graph


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_orchestrator() -> Dict[str, Any]:
    """Invoke the multi-agent graph and return the final state."""
    graph = _build_graph()
    compiled = graph.compile()

    initial_state: AgentState = {
        "news_catalyst": "",
        "theses": [],
        "verified_facts": [],
        "backtest_results": {},
    }

    # LangGraph's invoke is synchronous; run in thread to keep the event loop free
    final_state = await asyncio.to_thread(compiled.invoke, initial_state)

    logger.info("Orchestrator complete: %s", final_state.get("backtest_results"))
    return final_state
