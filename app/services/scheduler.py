"""
app/services/scheduler.py
APScheduler-based background job scheduler.

Three recurring jobs:
  1. Market scan — every SCANNER_INTERVAL_HOURS (default 6h)
  2. Position monitor — every 60 seconds
  3. Resolution checker — every 1 hour

Uses lazy imports inside job functions to avoid circular imports.
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.config import get_settings

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _job_market_scan() -> None:
    """Scheduled job: run the market scanner."""
    try:
        from app.services.scanner_service import run_scan
        result = await run_scan()
        logger.info("Scheduled scan complete: %s", result.get("scan_id", "unknown"))
    except Exception as exc:
        logger.error("Scheduled scan failed: %s", exc)


async def _job_position_monitor() -> None:
    """Scheduled job: check pending fills and stop-losses."""
    try:
        from app.services.position_monitor import run_position_monitor
        await run_position_monitor()
    except Exception as exc:
        logger.error("Position monitor failed: %s", exc)


async def _job_resolution_checker() -> None:
    """Scheduled job: check for resolved markets."""
    try:
        from app.services.resolution_service import run_resolution_checker
        await run_resolution_checker()
    except Exception as exc:
        logger.error("Resolution checker failed: %s", exc)


def start_scheduler() -> None:
    """Initialize and start the APScheduler with all jobs."""
    global _scheduler

    if _scheduler is not None:
        logger.warning("Scheduler already running")
        return

    settings = get_settings()
    _scheduler = AsyncIOScheduler()

    # Job 1: Market scan
    _scheduler.add_job(
        _job_market_scan,
        "interval",
        hours=settings.SCANNER_INTERVAL_HOURS,
        id="market_scan",
        name="Market Scanner",
    )

    # Job 2: Position monitor (fill checks + stop-losses)
    _scheduler.add_job(
        _job_position_monitor,
        "interval",
        seconds=60,
        id="position_monitor",
        name="Position Monitor",
    )

    # Job 3: Resolution checker
    _scheduler.add_job(
        _job_resolution_checker,
        "interval",
        hours=1,
        id="resolution_checker",
        name="Resolution Checker",
    )

    _scheduler.start()
    logger.info(
        "Scheduler started: scan every %dh, monitor every 60s, resolution every 1h",
        settings.SCANNER_INTERVAL_HOURS,
    )


def stop_scheduler() -> None:
    """Shut down the scheduler gracefully."""
    global _scheduler

    if _scheduler is None:
        return

    _scheduler.shutdown(wait=False)
    _scheduler = None
    logger.info("Scheduler stopped")
