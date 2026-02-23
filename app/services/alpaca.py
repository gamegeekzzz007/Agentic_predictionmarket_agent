"""
app/services/alpaca.py
Async-safe wrapper around the synchronous alpaca-py TradingClient.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException
from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

from core.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Response DTOs — frozen so no Alpaca SDK types leak into the rest of the app
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AccountInfo:
    equity: float
    buying_power: float


@dataclass(frozen=True)
class PositionInfo:
    symbol: str
    qty: float
    side: str
    market_value: float
    avg_entry_price: float
    unrealized_pl: float


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    status: str
    symbol: str
    qty: float
    side: str
    filled_avg_price: Optional[float]


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------


class AlpacaService:
    """Async-safe facade over the synchronous Alpaca TradingClient."""

    def __init__(self) -> None:
        settings = get_settings()
        paper = "paper" in settings.ALPACA_BASE_URL.lower()
        self._client = TradingClient(
            api_key=settings.ALPACA_API_KEY,
            secret_key=settings.ALPACA_SECRET_KEY,
            paper=paper,
        )
        logger.info("AlpacaService initialised (paper=%s)", paper)

    # --- Account / Position queries ----------------------------------------

    async def get_account(self) -> AccountInfo:
        """Return current equity and buying power."""
        try:
            acct = await asyncio.to_thread(self._client.get_account)
            return AccountInfo(
                equity=float(acct.equity),
                buying_power=float(acct.buying_power),
            )
        except APIError as exc:
            logger.error("Alpaca get_account failed: %s", exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    async def get_position(self, symbol: str) -> Optional[PositionInfo]:
        """Return position for *symbol*, or ``None`` if no position exists."""
        try:
            pos = await asyncio.to_thread(self._client.get_open_position, symbol)
            return PositionInfo(
                symbol=pos.symbol,
                qty=float(pos.qty),
                side=pos.side.value if hasattr(pos.side, "value") else str(pos.side),
                market_value=float(pos.market_value),
                avg_entry_price=float(pos.avg_entry_price),
                unrealized_pl=float(pos.unrealized_pl),
            )
        except APIError as exc:
            if exc.status_code == 404:
                return None
            logger.error("Alpaca get_position(%s) failed: %s", symbol, exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # --- Order submission --------------------------------------------------

    async def submit_market_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        time_in_force: TimeInForce = TimeInForce.GTC,
    ) -> OrderResult:
        """Submit a market order and return the broker response."""
        order_data = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide(side),
            time_in_force=time_in_force,
        )
        return await self._submit_order(order_data)

    async def submit_limit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        limit_price: float,
        time_in_force: TimeInForce = TimeInForce.DAY,
    ) -> OrderResult:
        """Submit a limit order and return the broker response."""
        order_data = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide(side),
            time_in_force=time_in_force,
            limit_price=limit_price,
        )
        return await self._submit_order(order_data)

    async def verify_connection(self) -> AccountInfo:
        """Startup health check — delegates to :meth:`get_account`."""
        return await self.get_account()

    # --- Private helpers ---------------------------------------------------

    async def _submit_order(
        self, order_data: MarketOrderRequest | LimitOrderRequest
    ) -> OrderResult:
        """Shared submission logic for market and limit orders."""
        try:
            order = await asyncio.to_thread(self._client.submit_order, order_data)
            return OrderResult(
                order_id=str(order.id),
                status=str(order.status.value if hasattr(order.status, "value") else order.status),
                symbol=order.symbol,
                qty=float(order.qty),
                side=order.side.value if hasattr(order.side, "value") else str(order.side),
                filled_avg_price=float(order.filled_avg_price) if order.filled_avg_price else None,
            )
        except APIError as exc:
            logger.error("Alpaca order submission failed: %s", exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Singleton access
# ---------------------------------------------------------------------------

_service: Optional[AlpacaService] = None


def init_alpaca_service() -> AlpacaService:
    """Create the module-level AlpacaService singleton. Call once at startup."""
    global _service  # noqa: PLW0603
    _service = AlpacaService()
    return _service


def get_alpaca_service() -> AlpacaService:
    """Return the singleton (usable as a FastAPI ``Depends()``)."""
    if _service is None:
        raise RuntimeError("AlpacaService not initialised — call init_alpaca_service() first")
    return _service
