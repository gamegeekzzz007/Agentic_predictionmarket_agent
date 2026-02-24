"""
app/main.py
FastAPI entry point for the Agentic Prediction Market system.
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
from app.routes.analyze import router as analyze_router
from app.routes.calibration import router as calibration_router
from app.routes.markets import router as markets_router
from app.routes.positions import router as positions_router
from app.routes.scanner import router as scanner_router
from database.connection import get_session, init_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup: create DB tables and start scheduler. Shutdown: stop scheduler."""
    from app.services.scheduler import start_scheduler, stop_scheduler

    await init_db()
    logger.info("Database initialized — tables created")
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="Agentic Prediction Market API",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(analyze_router)
app.include_router(calibration_router)
app.include_router(markets_router)
app.include_router(positions_router)
app.include_router(scanner_router)


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
