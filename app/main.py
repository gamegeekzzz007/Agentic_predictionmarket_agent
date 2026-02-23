"""
app/main.py
FastAPI entry point for the Agentic Trading system.
"""

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import database.models as _models  # noqa: F401 — registers tables with SQLModel metadata
from app.routes.trades import router as trades_router
from app.routes.portfolio import router as portfolio_router
from app.routes.agents import router as agents_router
from app.services.alpaca import init_alpaca_service
from database.connection import get_session, init_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup: create DB tables + verify Alpaca connection."""
    await init_db()

    alpaca = init_alpaca_service()
    try:
        account = await alpaca.verify_connection()
        print(
            f"[OK] Alpaca connected — equity=${account.equity:,.2f}, "
            f"buying_power=${account.buying_power:,.2f}",
            flush=True,
        )
    except Exception as exc:
        print(f"[FAIL] Alpaca connection failed: {exc}", flush=True)
        raise

    yield


app = FastAPI(
    title="Agentic Trading API",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(trades_router)
app.include_router(portfolio_router)
app.include_router(agents_router)


@app.get("/health")
async def health_check(session: AsyncSession = Depends(get_session)) -> dict:
    """Prove the API and database are alive."""
    try:
        await session.execute(text("SELECT 1"))
        return {
            "status": "healthy",
            "db": "connected",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "db": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
