"""
app/services/polymarket_client.py
Async wrapper around Polymarket's Gamma (market data) and CLOB (trading) APIs.
Public endpoints need no auth. Trading requires EIP-712 wallet signing via py-clob-client.
"""

import logging
from typing import Any, Optional

import httpx

from core.config import get_settings

logger = logging.getLogger(__name__)

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"


class PolymarketClient:
    """Async client for Polymarket APIs."""

    def __init__(self) -> None:
        settings = get_settings()
        self._private_key = settings.POLY_PRIVATE_KEY
        self._safe_address = settings.POLY_SAFE_ADDRESS
        self._clob_client: Any = None  # Lazy-init py-clob-client when needed
        self._client = httpx.AsyncClient(timeout=15.0)

    # ------------------------------------------------------------------
    # Public endpoints — Gamma API (market data, no auth)
    # ------------------------------------------------------------------

    async def get_markets(
        self,
        active: bool = True,
        closed: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Fetch markets from Gamma API."""
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
        }
        resp = await self._client.get(f"{GAMMA_URL}/markets", params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_market(self, condition_id: str) -> dict:
        """Get a single market by condition_id."""
        resp = await self._client.get(f"{GAMMA_URL}/markets/{condition_id}")
        resp.raise_for_status()
        return resp.json()

    async def get_events(
        self,
        active: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Fetch events (groups of markets) from Gamma API."""
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "active": str(active).lower(),
        }
        resp = await self._client.get(f"{GAMMA_URL}/events", params=params)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Public endpoints — CLOB API (orderbook, no auth)
    # ------------------------------------------------------------------

    async def get_orderbook(self, token_id: str) -> dict:
        """Get current orderbook for a token."""
        resp = await self._client.get(f"{CLOB_URL}/book", params={"token_id": token_id})
        resp.raise_for_status()
        return resp.json()

    async def get_price(self, token_id: str) -> dict:
        """Get current mid-price for a token."""
        resp = await self._client.get(f"{CLOB_URL}/price", params={"token_id": token_id})
        resp.raise_for_status()
        return resp.json()

    async def get_market_trades(
        self, condition_id: str, limit: int = 100
    ) -> list[dict]:
        """Get recent trades for a market."""
        resp = await self._client.get(
            f"{CLOB_URL}/trades",
            params={"asset_id": condition_id, "limit": limit},
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Authenticated endpoints (trading via py-clob-client)
    # ------------------------------------------------------------------

    def _ensure_clob_client(self) -> None:
        """Lazy-initialize the CLOB client for trading."""
        if self._clob_client is not None:
            return

        if not self._private_key or self._private_key.startswith("your-"):
            raise RuntimeError(
                "Polymarket trading requires POLY_PRIVATE_KEY in .env"
            )

        try:
            from py_clob_client.client import ClobClient

            self._clob_client = ClobClient(
                host=CLOB_URL,
                key=self._private_key,
                chain_id=137,  # Polygon mainnet
                funder=self._safe_address if self._safe_address else None,
            )
            logger.info("Polymarket CLOB client initialized")
        except ImportError:
            raise RuntimeError(
                "py-clob-client not installed. Run: pip install py-clob-client"
            )

    async def place_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
    ) -> dict:
        """
        Place a limit order via CLOB API.

        Parameters
        ----------
        token_id : str
            The token to trade (YES or NO token).
        side : str
            "BUY" or "SELL".
        price : float
            Limit price (0.01 - 0.99).
        size : float
            Number of contracts.
        """
        self._ensure_clob_client()
        from py_clob_client.order_builder.constants import BUY, SELL

        order_side = BUY if side.upper() == "BUY" else SELL
        order = self._clob_client.create_order(
            token_id=token_id,
            price=price,
            size=size,
            side=order_side,
        )
        result = self._clob_client.post_order(order)
        return result

    async def get_positions(self) -> list[dict]:
        """Get current open positions (requires auth)."""
        self._ensure_clob_client()
        # py-clob-client doesn't have a direct positions call;
        # use the REST API with API key headers
        api_creds = self._clob_client.get_api_credentials()
        headers = {
            "POLY-ADDRESS": api_creds.api_key,
            "POLY-SIGNATURE": api_creds.api_secret,
            "POLY-TIMESTAMP": api_creds.api_passphrase,
        }
        resp = await self._client.get(
            f"{CLOB_URL}/positions", headers=headers
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
