"""
Background scheduler — runs every 5 minutes.
Finds expired sessions, marks them inactive, deauthorizes on Omada.
Uses APScheduler (already in your Railway FastAPI setup).
"""
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, and_, update

from models.database import AsyncSessionLocal, Session
from services.omada import deauthorize_client

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


async def expire_sessions():
    """Mark expired sessions inactive and revoke Omada access."""
    logger.info("Running session expiry job — %s", datetime.utcnow())

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Session).where(
                and_(
                    Session.is_active  == True,
                    Session.expires_at <= datetime.utcnow(),
                )
            )
        )
        expired = result.scalars().all()

        for session in expired:
            session.is_active = False
            try:
                await deauthorize_client(session.mac_address)
                logger.info("Expired + deauthorized MAC %s", session.mac_address)
            except Exception as e:
                logger.error("Omada deauth error for %s: %s", session.mac_address, e)

        if expired:
            await db.commit()
            logger.info("Expired %d sessions", len(expired))


def start_scheduler():
    scheduler.add_job(
        expire_sessions,
        trigger  = "interval",
        minutes  = 5,
        id       = "expire_sessions",
        replace_existing = True,
    )
    scheduler.start()
    logger.info("Scheduler started — session expiry every 5 minutes")


def stop_scheduler():
    scheduler.shutdown()
