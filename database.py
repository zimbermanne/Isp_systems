"""
Database models and connection setup.
Uses SQLAlchemy async with PostgreSQL (Railway).
"""
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, Numeric
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from datetime import datetime
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/hotspot")
# Railway gives postgres:// — fix for asyncpg
DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://").replace("postgres://", "postgresql+asyncpg://")

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()


# ── Models ────────────────────────────────────────────────────────────────────

class Package(Base):
    __tablename__ = "packages"

    id            = Column(Integer, primary_key=True)
    name          = Column(String(50), nullable=False)          # e.g. "2 Hours"
    price_tzs     = Column(Integer, nullable=False)             # e.g. 500
    duration_mins = Column(Integer, nullable=False)             # e.g. 120
    speed_mbps    = Column(Integer, nullable=False, default=1)  # bandwidth cap
    max_devices   = Column(Integer, nullable=False, default=1)
    is_active     = Column(Boolean, default=True)


class Session(Base):
    __tablename__ = "sessions"

    id            = Column(Integer, primary_key=True)
    mac_address   = Column(String(17), nullable=False, index=True)
    phone         = Column(String(15), nullable=False)
    package_id    = Column(Integer, ForeignKey("packages.id"))
    started_at    = Column(DateTime, default=datetime.utcnow)
    expires_at    = Column(DateTime, nullable=False)
    is_active     = Column(Boolean, default=True)

    package       = relationship("Package")
    transactions  = relationship("Transaction", back_populates="session")


class Transaction(Base):
    __tablename__ = "transactions"

    id            = Column(Integer, primary_key=True)
    azampay_ref   = Column(String(100), unique=True, nullable=True)
    phone         = Column(String(15), nullable=False)
    amount        = Column(Integer, nullable=False)
    package_id    = Column(Integer, ForeignKey("packages.id"))
    mac_address   = Column(String(17), nullable=False)
    status        = Column(String(20), default="pending")   # pending|success|failed
    session_id    = Column(Integer, ForeignKey("sessions.id"), nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    session       = relationship("Session", back_populates="transactions")
    package       = relationship("Package")


# ── DB helpers ────────────────────────────────────────────────────────────────

async def get_db():
    async with AsyncSessionLocal() as db:
        try:
            yield db
        finally:
            await db.close()


async def init_db():
    """Create tables and seed default packages if empty."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        from sqlalchemy import select
        result = await db.execute(select(Package))
        if not result.scalars().first():
            db.add_all([
                Package(name="2 Hours",  price_tzs=500,  duration_mins=120,  speed_mbps=1, max_devices=1),
                Package(name="6 Hours",  price_tzs=1000, duration_mins=360,  speed_mbps=2, max_devices=1),
                Package(name="24 Hours", price_tzs=2000, duration_mins=1440, speed_mbps=3, max_devices=2),
                Package(name="7 Days",   price_tzs=5000, duration_mins=10080,speed_mbps=5, max_devices=3),
            ])
            await db.commit()
