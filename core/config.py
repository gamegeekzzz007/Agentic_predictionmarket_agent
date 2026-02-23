"""
core/config.py
Environment-based configuration using pydantic-settings.
Loads from .env file automatically; fails fast if required keys are missing.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    # --- Required (app won't start without these) ---
    ALPACA_API_KEY: str
    ALPACA_SECRET_KEY: str
    OPENAI_API_KEY: str

    # --- Optional (empty-string defaults) ---
    NEWS_API_KEY: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # --- OpenClaw (Claude proxy) ---
    OPENCLAW_BASE_URL: str = ""     # e.g. "http://76.13.241.178:18790/v1"
    OPENCLAW_API_KEY: str = ""      # Gateway bearer token
    OPENCLAW_MODEL_ID: str = "claude-4-6-sonnet-latest"

    # --- Defaults ---
    ALPACA_BASE_URL: str = "https://paper-api.alpaca.markets"
    DATABASE_URL: str = "sqlite+aiosqlite:///./agentic_trading.db"

    model_config = {
        "env_file": ".env",
        "extra": "ignore",
    }


@lru_cache
def get_settings() -> Settings:
    """Singleton access to application settings."""
    return Settings()
