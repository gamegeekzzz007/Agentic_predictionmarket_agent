"""
agents/__init__.py
Agent registry and shared types for the prediction market agent desks.
"""

from dataclasses import dataclass, field


@dataclass
class EstimateResult:
    """Standard output from any agent desk."""
    desk: str
    agent_name: str
    probability: float        # 0.00 - 1.00
    confidence: float         # 0.00 - 1.00
    reasoning: str
    model_type: str | None = None
    extra: dict = field(default_factory=dict)


DESK_REGISTRY: dict[str, str] = {
    "research": "agents.research_desk.researcher",
    "base_rate": "agents.base_rate_desk.base_rate",
    "model": "agents.model_desk.statistical_model",
}
