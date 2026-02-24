"""
core/config.py
Environment-based configuration using pydantic-settings.
Loads from .env file automatically; fails fast if required keys are missing.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    # --- OpenClaw (Claude proxy — required) ---
    OPENCLAW_BASE_URL: str
    OPENCLAW_API_KEY: str
    OPENCLAW_MODEL: str = "claude-sonnet-4-6"

    # --- Search ---
    TAVILY_API_KEY: str = ""

    # --- Kalshi ---
    KALSHI_API_KEY_ID: str = ""
    KALSHI_PRIVATE_KEY_PATH: str = "./kalshi_private_key.pem"
    KALSHI_USE_DEMO: bool = True

    # --- Polymarket ---
    POLY_PRIVATE_KEY: str = ""
    POLY_SAFE_ADDRESS: str = ""

    # --- Scanner ---
    SCANNER_INTERVAL_HOURS: int = 6
    MIN_MARKET_VOLUME: int = 200
    MIN_EDGE_THRESHOLD: float = 0.05
    MAX_DAYS_TO_EXPIRY: int = 30

    # --- Safety (env-overridable, but constants.py has hard floors) ---
    MAX_POSITION_PCT: float = 5.0
    MAX_CONCURRENT_POSITIONS: int = 15
    DAILY_DRAWDOWN_LIMIT_PCT: float = 2.0
    BANKROLL: float = 10_000.0

    # --- Database ---
    DATABASE_URL: str = "sqlite+aiosqlite:///./prediction_market.db"

    model_config = {
        "env_file": ".env",
        "extra": "ignore",
    }


@lru_cache
def get_settings() -> Settings:
    """Singleton access to application settings."""
    return Settings()
