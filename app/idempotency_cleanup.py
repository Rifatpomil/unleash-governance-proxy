"""Idempotency key cleanup - purge expired keys."""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db.models import IdempotencyKey
from app.metrics import IDEMPOTENCY_CLEANUP_DELETED

logger = logging.getLogger(__name__)

CLEANUP_INTERVAL_SECONDS = 3600  # 1 hour


async def run_cleanup_loop():
    """Periodically delete expired idempotency keys."""
    settings = get_settings()
    engine = create_engine(settings.database_url)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    while True:
        try:
            session = Session()
            try:
                now = datetime.now(timezone.utc)
                result = session.execute(delete(IdempotencyKey).where(IdempotencyKey.expires_at < now))
                deleted = result.rowcount or 0
                session.commit()
                if deleted > 0:
                    IDEMPOTENCY_CLEANUP_DELETED.inc(deleted)
                    logger.info("idempotency_cleanup", deleted=deleted)
            finally:
                session.close()
        except Exception as e:
            logger.error("idempotency_cleanup_failed", error=str(e))
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


def run_cleanup_once() -> int:
    """Run cleanup once (for CLI or sync use). Returns count deleted."""
    settings = get_settings()
    engine = create_engine(settings.database_url)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = Session()
    try:
        now = datetime.now(timezone.utc)
        result = session.execute(delete(IdempotencyKey).where(IdempotencyKey.expires_at < now))
        deleted = result.rowcount or 0
        session.commit()
        if deleted > 0:
            IDEMPOTENCY_CLEANUP_DELETED.inc(deleted)
        return deleted
    finally:
        session.close()
