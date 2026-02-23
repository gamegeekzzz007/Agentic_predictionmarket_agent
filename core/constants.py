"""
core/constants.py
Hard-coded safety rails and system constants.
These values are NOT configurable via environment — they are the law.
"""

from typing import Final

# ---------------------------------------------------------------------------
# Risk Limits
# ---------------------------------------------------------------------------
STOP_LOSS_PCT: Final[float] = 0.05              # 5% per-position stop-loss
MAX_DAILY_DRAWDOWN_PCT: Final[float] = 0.02     # 2% daily drawdown kill-switch
MAX_POSITION_PCT: Final[float] = 0.25           # 25% max allocation (matches half_kelly cap)

# ---------------------------------------------------------------------------
# Market Hours (Eastern Time)
# ---------------------------------------------------------------------------
MARKET_OPEN_HOUR: Final[int] = 9
MARKET_OPEN_MINUTE: Final[int] = 30
MARKET_CLOSE_HOUR: Final[int] = 16
MARKET_CLOSE_MINUTE: Final[int] = 0

# ---------------------------------------------------------------------------
# Trade Status Strings
# ---------------------------------------------------------------------------
STATUS_PENDING: Final[str] = "pending"
STATUS_FILLED: Final[str] = "filled"
STATUS_CLOSED: Final[str] = "closed"
STATUS_CANCELLED: Final[str] = "cancelled"
STATUS_FAILED: Final[str] = "failed"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_REFERENCE_EQUITY: Final[float] = 100_000.0   # Alpaca paper default
STRATEGY_VERSION: Final[str] = "v1.0"
