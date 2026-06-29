"""
Admin routes — revenue, sessions, transactions.
Protected by a simple API key header: X-Admin-Key
"""
import os
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from models.database import get_db, Session, Transaction, Package
from services.omada import deauthorize_client

router = APIRouter(prefix="/admin")
ADMIN_KEY = os.getenv("ADMIN_API_KEY", "change-me-in-env")


def require_admin(x_admin_key: str = Header(...)):
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("/revenue")
async def revenue_summary(
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
):
    """Revenue breakdown for the last N days."""
    since = datetime.utcnow() - timedelta(days=days)

    result = await db.execute(
        select(
            func.date(Transaction.created_at).label("date"),
            func.count(Transaction.id).label("transactions"),
            func.sum(Transaction.amount).label("revenue_tzs"),
        )
        .where(
            and_(
                Transaction.status     == "success",
                Transaction.created_at >= since,
            )
        )
        .group_by(func.date(Transaction.created_at))
        .order_by(func.date(Transaction.created_at).desc())
    )
    rows = result.all()

    total_result = await db.execute(
        select(func.sum(Transaction.amount))
        .where(
            and_(
                Transaction.status     == "success",
                Transaction.created_at >= since,
            )
        )
    )
    total = total_result.scalar() or 0

    return {
        "period_days":   days,
        "total_tzs":     total,
        "daily": [
            {
                "date":         str(r.date),
                "transactions": r.transactions,
                "revenue_tzs":  r.revenue_tzs or 0,
            }
            for r in rows
        ],
    }


@router.get("/sessions")
async def active_sessions(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
):
    """List all currently active sessions."""
    result = await db.execute(
        select(Session, Package)
        .join(Package, Session.package_id == Package.id)
        .where(
            and_(
                Session.is_active  == True,
                Session.expires_at > datetime.utcnow(),
            )
        )
        .order_by(Session.expires_at)
    )
    rows = result.all()

    return [
        {
            "id":                s.id,
            "mac":               s.mac_address,
            "phone":             s.phone,
            "package":           p.name,
            "started_at":        s.started_at.isoformat(),
            "expires_at":        s.expires_at.isoformat(),
            "remaining_minutes": max(0, int((s.expires_at - datetime.utcnow()).total_seconds() / 60)),
        }
        for s, p in rows
    ]


@router.post("/sessions/{session_id}/revoke")
async def revoke_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
):
    """Manually revoke a session (e.g. abuse, refund)."""
    session = await db.get(Session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session.is_active = False
    await db.commit()

    try:
        await deauthorize_client(session.mac_address)
    except Exception as e:
        pass  # Log but don't fail

    return {"status": "revoked", "mac": session.mac_address}


@router.get("/transactions")
async def recent_transactions(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
):
    result = await db.execute(
        select(Transaction, Package)
        .join(Package, Transaction.package_id == Package.id)
        .order_by(Transaction.created_at.desc())
        .limit(limit)
    )
    rows = result.all()

    return [
        {
            "id":           t.id,
            "phone":        t.phone,
            "amount":       t.amount,
            "package":      p.name,
            "status":       t.status,
            "azampay_ref":  t.azampay_ref,
            "mac":          t.mac_address,
            "created_at":   t.created_at.isoformat(),
        }
        for t, p in rows
    ]
