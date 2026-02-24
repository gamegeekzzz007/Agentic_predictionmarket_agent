"""
app/services/scanner_service.py
Market scanner — pulls active markets from both platforms, filters by
quality criteria, and stores qualifying markets in the database.

Phase 2 scope: scan + filter + store.
Phase 3+ will add: probability estimation, Kelly gate, order execution.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.services.kalshi_client import KalshiClient
from app.services.polymarket_client import PolymarketClient
from core.config import get_settings
from core.constants import MAX_SPREAD
from database.models import (
    Market,
    MarketCategory,
    MarketStatus,
    Platform,
)

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Response model for scan results
# ------------------------------------------------------------------

class ScanResult:
    """Summary of a completed scan cycle."""

    def __init__(self, scan_id: str) -> None:
        self.scan_id = scan_id
        self.total_fetched: int = 0
        self.qualifying: int = 0
        self.new_markets: int = 0
        self.updated_markets: int = 0
        self.errors: list[str] = []

    def to_dict(self) -> dict:
        return {
            "scan_id": self.scan_id,
            "total_fetched": self.total_fetched,
            "qualifying": self.qualifying,
            "new_markets": self.new_markets,
            "updated_markets": self.updated_markets,
            "errors": self.errors,
        }


# ------------------------------------------------------------------
# Category guessing (simple keyword match)
# ------------------------------------------------------------------

_CATEGORY_KEYWORDS: dict[MarketCategory, list[str]] = {
    MarketCategory.ECONOMICS: ["cpi", "gdp", "fed", "inflation", "jobs", "unemployment", "interest rate", "fomc", "payroll", "ppi"],
    MarketCategory.POLITICS: ["trump", "biden", "election", "democrat", "republican", "congress", "senate", "president", "vote", "governor"],
    MarketCategory.WEATHER: ["temperature", "hurricane", "storm", "weather", "rainfall", "snowfall", "celsius", "fahrenheit"],
    MarketCategory.CRYPTO: ["bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "dogecoin"],
    MarketCategory.SPORTS: ["win", "nba", "nfl", "mlb", "nhl", "match", "game", "score", "points", "team"],
    MarketCategory.ENTERTAINMENT: ["oscar", "grammy", "emmy", "movie", "box office", "tv show", "album"],
}


def _guess_category(title: str) -> MarketCategory:
    """Guess market category from title keywords."""
    lower = title.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return category
    return MarketCategory.OTHER


# ------------------------------------------------------------------
# Kalshi market normalization
# ------------------------------------------------------------------

def _normalize_kalshi(m: dict) -> Optional[Market]:
    """Convert a raw Kalshi market dict to a Market model."""
    try:
        yes_ask = (m.get("yes_ask") or 50) / 100.0
        yes_bid = (m.get("yes_bid") or 0) / 100.0
        no_ask = 1.0 - yes_bid if yes_bid else 0.5
        spread = round(yes_ask - yes_bid, 4) if yes_bid else 0.0

        close_time = None
        if m.get("close_time"):
            try:
                close_time = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        days_to_expiry = None
        if close_time:
            delta = close_time - datetime.now(timezone.utc)
            days_to_expiry = max(int(delta.total_seconds() / 86400), 0)

        return Market(
            platform=Platform.KALSHI,
            platform_market_id=m.get("ticker", ""),
            platform_event_id=m.get("event_ticker"),
            title=m.get("title", ""),
            category=_guess_category(m.get("title", "")),
            description=m.get("subtitle") or m.get("rules_primary"),
            resolution_source=m.get("settlement_source_url"),
            yes_price=round(yes_ask, 4),
            no_price=round(1.0 - yes_ask, 4),
            spread=spread,
            volume_24h=m.get("volume", 0) or 0,
            close_time=close_time,
            days_to_expiry=days_to_expiry,
            status=MarketStatus.ACTIVE,
            last_updated=datetime.now(timezone.utc),
        )
    except Exception as exc:
        logger.warning("Failed to normalize Kalshi market %s: %s", m.get("ticker"), exc)
        return None


# ------------------------------------------------------------------
# Polymarket market normalization
# ------------------------------------------------------------------

def _normalize_polymarket(m: dict) -> Optional[Market]:
    """Convert a raw Polymarket Gamma market dict to a Market model."""
    try:
        yes_price = 0.5
        no_price = 0.5
        outcome_prices = m.get("outcomePrices", "")
        if outcome_prices:
            prices = json.loads(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
            yes_price = float(prices[0])
            no_price = float(prices[1]) if len(prices) > 1 else 1.0 - yes_price

        spread_val = float(m.get("spread", 0) or 0)

        close_time = None
        if m.get("endDate"):
            try:
                close_time = datetime.fromisoformat(m["endDate"].replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        days_to_expiry = None
        if close_time:
            delta = close_time - datetime.now(timezone.utc)
            days_to_expiry = max(int(delta.total_seconds() / 86400), 0)

        return Market(
            platform=Platform.POLYMARKET,
            platform_market_id=m.get("conditionId", m.get("id", "")),
            platform_event_id=m.get("eventSlug"),
            title=m.get("question", ""),
            category=_guess_category(m.get("question", "")),
            description=m.get("description"),
            yes_price=round(yes_price, 4),
            no_price=round(no_price, 4),
            spread=round(spread_val, 4),
            volume_24h=int(float(m.get("volume", 0) or 0)),
            close_time=close_time,
            days_to_expiry=days_to_expiry,
            status=MarketStatus.ACTIVE,
            last_updated=datetime.now(timezone.utc),
        )
    except Exception as exc:
        logger.warning("Failed to normalize Polymarket market %s: %s", m.get("conditionId"), exc)
        return None


# ------------------------------------------------------------------
# Filtering
# ------------------------------------------------------------------

def _passes_filter(market: Market) -> bool:
    """Check if a market passes the scanner quality filters."""
    settings = get_settings()

    if market.volume_24h < settings.MIN_MARKET_VOLUME:
        return False

    if market.days_to_expiry is not None and market.days_to_expiry > settings.MAX_DAYS_TO_EXPIRY:
        return False

    if market.spread > MAX_SPREAD:
        return False

    # Skip markets priced at extremes (no edge possible)
    if market.yes_price <= 0.03 or market.yes_price >= 0.97:
        return False

    return True


# ------------------------------------------------------------------
# Core scan function
# ------------------------------------------------------------------

async def run_scan(session: AsyncSession) -> ScanResult:
    """
    Execute a full scan cycle:
    1. Fetch all active markets from Kalshi and Polymarket
    2. Filter by quality criteria
    3. Upsert qualifying markets into the database
    4. Return summary

    Phase 3+ will extend this to run probability estimation + Kelly gate.
    """
    scan_id = str(uuid.uuid4())[:8]
    result = ScanResult(scan_id)

    all_markets: list[Market] = []

    # --- Fetch from Kalshi (cap at 500 markets to avoid timeout) ---
    max_pages = 5
    kalshi = KalshiClient()
    try:
        cursor = None
        for _ in range(max_pages):
            data = await kalshi.get_markets(limit=100, cursor=cursor)
            raw_markets = data.get("markets", [])
            if not raw_markets:
                break
            for m in raw_markets:
                normalized = _normalize_kalshi(m)
                if normalized:
                    all_markets.append(normalized)
            cursor = data.get("cursor")
            if not cursor:
                break
    except Exception as exc:
        result.errors.append(f"Kalshi fetch error: {exc}")
        logger.error("Kalshi scan failed: %s", exc)
    finally:
        await kalshi.close()

    # --- Fetch from Polymarket (cap at 500 markets to avoid timeout) ---
    poly = PolymarketClient()
    try:
        offset = 0
        for _ in range(max_pages):
            raw_markets = await poly.get_markets(limit=100, offset=offset)
            if not raw_markets:
                break
            for m in raw_markets:
                normalized = _normalize_polymarket(m)
                if normalized:
                    all_markets.append(normalized)
            offset += len(raw_markets)
            if len(raw_markets) < 100:
                break
    except Exception as exc:
        result.errors.append(f"Polymarket fetch error: {exc}")
        logger.error("Polymarket scan failed: %s", exc)
    finally:
        await poly.close()

    result.total_fetched = len(all_markets)

    # --- Filter ---
    qualifying = [m for m in all_markets if _passes_filter(m)]
    result.qualifying = len(qualifying)

    # --- Upsert into database ---
    for market in qualifying:
        existing = (
            await session.execute(
                select(Market).where(
                    Market.platform == market.platform,
                    Market.platform_market_id == market.platform_market_id,
                )
            )
        ).scalars().first()

        if existing:
            # Update pricing data
            existing.yes_price = market.yes_price
            existing.no_price = market.no_price
            existing.spread = market.spread
            existing.volume_24h = market.volume_24h
            existing.days_to_expiry = market.days_to_expiry
            existing.last_updated = datetime.now(timezone.utc)
            session.add(existing)
            result.updated_markets += 1
        else:
            market.first_seen = datetime.now(timezone.utc)
            session.add(market)
            result.new_markets += 1

    await session.commit()

    logger.info(
        "Scan %s complete: %d fetched, %d qualifying, %d new, %d updated",
        scan_id, result.total_fetched, result.qualifying,
        result.new_markets, result.updated_markets,
    )

    return result
