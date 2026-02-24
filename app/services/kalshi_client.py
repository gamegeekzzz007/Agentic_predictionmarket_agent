"""
app/services/kalshi_client.py
Async wrapper around Kalshi's REST API v2.
Public endpoints (markets, orderbook) need no auth.
Authenticated endpoints (orders, positions, balance) use RSA-PSS signing.
"""

import base64
import logging
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from core.config import get_settings

logger = logging.getLogger(__name__)

PROD_URL = "https://trading-api.kalshi.com/trade-api/v2"
DEMO_URL = "https://demo-api.kalshi.co/trade-api/v2"


def _load_private_key(path: str) -> Any:
    """Load an RSA private key from a PEM file."""
    pem_bytes = Path(path).read_bytes()
    return serialization.load_pem_private_key(
        pem_bytes, password=None, backend=default_backend()
    )


def _sign_rsa_pss(private_key: Any, message: str) -> str:
    """Sign a message with RSA-PSS + SHA256 and return base64-encoded signature."""
    signature = private_key.sign(
        message.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("utf-8")


class KalshiClient:
    """Async client for Kalshi's REST API v2."""

    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = DEMO_URL if settings.KALSHI_USE_DEMO else PROD_URL
        self._api_key_id = settings.KALSHI_API_KEY_ID
        self._private_key: Any = None

        if self._api_key_id and settings.KALSHI_PRIVATE_KEY_PATH:
            try:
                self._private_key = _load_private_key(settings.KALSHI_PRIVATE_KEY_PATH)
            except Exception as exc:
                logger.warning("Kalshi private key not loaded: %s", exc)

        self._client = httpx.AsyncClient(timeout=15.0)

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        """Build authenticated headers with RSA-PSS signature."""
        timestamp = str(int(time.time() * 1000))
        # Kalshi signs the full path including /trade-api/v2 prefix
        full_path = f"/trade-api/v2{path}".split("?")[0]
        message = timestamp + method.upper() + full_path
        signature = _sign_rsa_pss(self._private_key, message)
        return {
            "Content-Type": "application/json",
            "KALSHI-ACCESS-KEY": self._api_key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
        }

    async def _get(self, path: str, params: Optional[dict] = None, auth: bool = False) -> dict:
        """GET request, optionally authenticated."""
        url = f"{self._base_url}{path}"
        headers = self._auth_headers("GET", path) if auth else {}
        resp = await self._client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, body: dict) -> dict:
        """Authenticated POST request."""
        url = f"{self._base_url}{path}"
        headers = self._auth_headers("POST", path)
        resp = await self._client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def _delete(self, path: str) -> dict:
        """Authenticated DELETE request."""
        url = f"{self._base_url}{path}"
        headers = self._auth_headers("DELETE", path)
        resp = await self._client.delete(url, headers=headers)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Public endpoints (no auth)
    # ------------------------------------------------------------------

    async def get_markets(
        self,
        status: str = "open",
        series_ticker: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> dict:
        """Fetch active markets. Returns {'markets': [...], 'cursor': ...}."""
        params: dict[str, Any] = {"limit": limit, "status": status}
        if series_ticker:
            params["series_ticker"] = series_ticker
        if cursor:
            params["cursor"] = cursor
        return await self._get("/markets", params=params)

    async def get_market(self, ticker: str) -> dict:
        """Get single market detail."""
        data = await self._get(f"/markets/{ticker}")
        return data.get("market", data)

    async def get_orderbook(self, ticker: str) -> dict:
        """Get current orderbook (bid/ask depth)."""
        return await self._get(f"/orderbook/{ticker}")

    async def get_event(self, event_ticker: str) -> dict:
        """Get an event with all its child markets."""
        data = await self._get(f"/events/{event_ticker}")
        return data.get("event", data)

    async def get_market_history(
        self, ticker: str, limit: int = 100
    ) -> list[dict]:
        """Get price/trade history for a market."""
        data = await self._get(
            f"/markets/{ticker}/history", params={"limit": limit}
        )
        return data.get("history", [])

    # ------------------------------------------------------------------
    # Authenticated endpoints (trading)
    # ------------------------------------------------------------------

    async def get_balance(self) -> float:
        """Get available balance in dollars."""
        data = await self._get("/portfolio/balance", auth=True)
        return data.get("balance", 0) / 100.0

    async def get_positions(self) -> list[dict]:
        """Get current open positions."""
        data = await self._get("/portfolio/positions", auth=True)
        return data.get("market_positions", [])

    async def place_order(
        self,
        ticker: str,
        side: str,
        action: str = "buy",
        count: int = 1,
        price: int = 50,
        order_type: str = "limit",
    ) -> dict:
        """
        Place an order.

        Parameters
        ----------
        ticker : str
            Market ticker.
        side : str
            "yes" or "no".
        action : str
            "buy" or "sell".
        count : int
            Number of contracts.
        price : int
            Price in cents (1-99).
        order_type : str
            Always "limit" — we are makers, never takers.
        """
        body = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            "yes_price": price if side == "yes" else None,
            "no_price": price if side == "no" else None,
            "type": order_type,
        }
        # Remove None values
        body = {k: v for k, v in body.items() if v is not None}
        data = await self._post("/portfolio/orders", body)
        return data.get("order", data)

    async def get_order(self, order_id: str) -> dict:
        """Get a single order's current status."""
        data = await self._get(f"/portfolio/orders/{order_id}", auth=True)
        return data.get("order", data)

    async def cancel_order(self, order_id: str) -> dict:
        """Cancel an open order."""
        return await self._delete(f"/portfolio/orders/{order_id}")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
