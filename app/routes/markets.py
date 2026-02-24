"""
app/routes/markets.py
Market data endpoints — list, search, and inspect prediction markets
from both Kalshi and Polymarket.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.kalshi_client import KalshiClient
from app.services.polymarket_client import PolymarketClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/markets", tags=["markets"])


# ------------------------------------------------------------------
# Response models
# ------------------------------------------------------------------

class MarketSummary(BaseModel):
    """Unified market summary across platforms."""
    platform: str
    market_id: str
    title: str
    yes_price: float
    no_price: float
    spread: float
    volume: int
    status: str
    close_time: Optional[str] = None
    category: Optional[str] = None
    event_id: Optional[str] = None


class MarketsResponse(BaseModel):
    """Response for GET /markets."""
    count: int
    markets: list[MarketSummary]


# ------------------------------------------------------------------
# Helpers — normalize platform data to unified format
# ------------------------------------------------------------------

def _normalize_kalshi_market(m: dict) -> MarketSummary:
    """Convert a Kalshi market dict to MarketSummary."""
    yes_price = (m.get("yes_ask") or m.get("last_price") or 50) / 100.0
    no_price = 1.0 - yes_price
    yes_bid = (m.get("yes_bid") or 0) / 100.0
    yes_ask = (m.get("yes_ask") or 100) / 100.0
    spread = round(yes_ask - yes_bid, 4)

    return MarketSummary(
        platform="kalshi",
        market_id=m.get("ticker", ""),
        title=m.get("title", ""),
        yes_price=round(yes_price, 4),
        no_price=round(no_price, 4),
        spread=spread,
        volume=m.get("volume", 0),
        status=m.get("status", "unknown"),
        close_time=m.get("close_time"),
        category=m.get("category"),
        event_id=m.get("event_ticker"),
    )


def _normalize_poly_market(m: dict) -> MarketSummary:
    """Convert a Polymarket Gamma market dict to MarketSummary."""
    # Gamma API returns outcomePrices as a JSON string like "[\"0.85\",\"0.15\"]"
    outcome_prices = m.get("outcomePrices", "")
    yes_price = 0.5
    no_price = 0.5
    if outcome_prices:
        try:
            import json
            prices = json.loads(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
            yes_price = float(prices[0])
            no_price = float(prices[1]) if len(prices) > 1 else 1.0 - yes_price
        except (ValueError, IndexError, TypeError):
            pass

    spread_val = m.get("spread", 0)
    if isinstance(spread_val, str):
        try:
            spread_val = float(spread_val)
        except ValueError:
            spread_val = 0.0

    return MarketSummary(
        platform="polymarket",
        market_id=m.get("conditionId", m.get("id", "")),
        title=m.get("question", ""),
        yes_price=round(yes_price, 4),
        no_price=round(no_price, 4),
        spread=round(spread_val, 4),
        volume=int(float(m.get("volume", 0) or 0)),
        status="active" if m.get("active") else "closed",
        close_time=m.get("endDate"),
        category=m.get("category"),
        event_id=m.get("eventSlug"),
    )


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.get("", response_model=MarketsResponse)
async def list_markets(
    platform: Optional[str] = Query(None, description="Filter: 'kalshi' or 'polymarket'"),
    limit: int = Query(50, ge=1, le=200),
) -> MarketsResponse:
    """List active markets from Kalshi and/or Polymarket."""
    results: list[MarketSummary] = []

    if platform is None or platform == "kalshi":
        try:
            client = KalshiClient()
            data = await client.get_markets(limit=limit)
            for m in data.get("markets", []):
                results.append(_normalize_kalshi_market(m))
            await client.close()
        except Exception as exc:
            logger.error("Kalshi fetch failed: %s", exc)

    if platform is None or platform == "polymarket":
        try:
            client_poly = PolymarketClient()
            markets = await client_poly.get_markets(limit=limit)
            for m in markets:
                results.append(_normalize_poly_market(m))
            await client_poly.close()
        except Exception as exc:
            logger.error("Polymarket fetch failed: %s", exc)

    return MarketsResponse(count=len(results), markets=results)


@router.get("/{market_id}")
async def get_market_detail(
    market_id: str,
    platform: str = Query(..., description="'kalshi' or 'polymarket'"),
) -> dict:
    """Get detailed info for a single market."""
    if platform == "kalshi":
        client = KalshiClient()
        try:
            market = await client.get_market(market_id)
            orderbook = await client.get_orderbook(market_id)
            return {"platform": "kalshi", "market": market, "orderbook": orderbook}
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=exc.response.status_code, detail=str(exc))
        finally:
            await client.close()

    elif platform == "polymarket":
        client_poly = PolymarketClient()
        try:
            market = await client_poly.get_market(market_id)
            return {"platform": "polymarket", "market": market}
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=exc.response.status_code, detail=str(exc))
        finally:
            await client_poly.close()

    else:
        raise HTTPException(status_code=400, detail="platform must be 'kalshi' or 'polymarket'")
