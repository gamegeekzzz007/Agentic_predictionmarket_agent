"""
core/constants.py
Hard-coded safety rails and system constants.
These values are NOT configurable via environment — they are the law.
"""

from typing import Final

# ---------------------------------------------------------------------------
# Risk Limits (KEPT from original — these are battle-tested)
# ---------------------------------------------------------------------------
STOP_LOSS_PCT: Final[float] = 0.05              # 5% per-position stop-loss
MAX_DAILY_DRAWDOWN_PCT: Final[float] = 0.02     # 2% daily drawdown kill-switch
MAX_POSITION_PCT: Final[float] = 0.25           # 25% max allocation (half_kelly cap)

# ---------------------------------------------------------------------------
# Prediction Market Constants (NEW)
# ---------------------------------------------------------------------------
MAX_CONCURRENT_POSITIONS: Final[int] = 15       # Max open positions at once
MIN_EDGE_THRESHOLD: Final[float] = 0.05         # 5% minimum edge to trade
MAX_SPREAD: Final[float] = 0.15                 # Don't trade illiquid markets

# ---------------------------------------------------------------------------
# Debate System (NEW)
# ---------------------------------------------------------------------------
DEBATE_DIVERGENCE_THRESHOLD: Final[float] = 0.10  # 10pp divergence triggers debate
MAX_DEBATE_ROUNDS: Final[int] = 5                  # Max rounds before moderator forces
CONVERGENCE_THRESHOLD: Final[float] = 0.05         # Within 5pp = converged

# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------
SYSTEM_VERSION: Final[str] = "v2.0-prediction-markets"
