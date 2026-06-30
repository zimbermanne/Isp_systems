"""
Hotspot portal routes:
  GET  /portal          - captive portal landing page
  GET  /packages        - list available packages (JSON)
  POST /api/pay         - initiate payment
  POST /webhook/azampay - payment callback from Azampay
  GET  /api/session/{mac} - check session status
"""
import hmac
import hashlib
import logging
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from models.database import get_db, Package, Session, Transaction
from services.azampay import initiate_payment, detect_provider
from services.omada import authorize_client

logger = logging.getLogger(__name__)
router = APIRouter()

AZAMPAY_WEBHOOK_SECRET = os.getenv("AZAMPAY_WEBHOOK_SECRET", "")


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class PayRequest(BaseModel):
    phone:      str
    package_id: int
    mac:        str   # device MAC from Omada portal redirect


class AzampayWebhook(BaseModel):
    transactionId:  str | None = None
    externalId:     str | None = None   # our reference: "mac:package_id"
    amount:         str | None = None
    status:         str | None = None   # SUCCESS | FAILED
    msisdn:         str | None = None   # payer phone
    operator:       str | None = None


# ── Portal HTML ───────────────────────────────────────────────────────────────

PORTAL_HTML = """
<!DOCTYPE html>
<html lang="sw">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Moshi BusStop WiFi</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: #0D1117; color: #F7F6F2; min-height: 100vh;
      display: flex; flex-direction: column; align-items: center;
      padding: 32px 16px;
    }}
    .logo {{ font-size: 36px; margin-bottom: 8px; }}
    h1 {{ font-size: 22px; font-weight: 700; margin-bottom: 4px; }}
    .sub {{ font-size: 13px; color: #9CA3AF; margin-bottom: 32px; }}
    .packages {{ display: flex; flex-direction: column; gap: 12px; width: 100%; max-width: 360px; }}
    .pkg {{
      background: #1A1F2C; border: 1px solid #2D3748; border-radius: 12px;
      padding: 16px 20px; cursor: pointer; transition: border-color .2s;
      display: flex; justify-content: space-between; align-items: center;
    }}
    .pkg:hover, .pkg.selected {{ border-color: #1A6B4A; }}
    .pkg-name {{ font-weight: 600; font-size: 15px; }}
    .pkg-meta {{ font-size: 12px; color: #9CA3AF; margin-top: 2px; }}
    .pkg-price {{ font-size: 18px; font-weight: 700; color: #1A6B4A; font-family: monospace; }}
    .form {{ width: 100%; max-width: 360px; margin-top: 24px; }}
    input {{
      width: 100%; padding: 14px 16px; border-radius: 10px;
      border: 1px solid #2D3748; background: #1A1F2C; color: white;
      font-size: 16px; margin-bottom: 12px;
    }}
    input:focus {{ outline: none; border-color: #1A6B4A; }}
    button {{
      width: 100%; padding: 15px; background: #1A6B4A; color: white;
      border: none; border-radius: 10px; font-size: 16px; font-weight: 600;
      cursor: pointer; transition: background .2s;
    }}
    button:hover {{ background: #15573D; }}
    button:disabled {{ background: #374151; cursor: not-allowed; }}
    .msg {{ margin-top: 16px; font-size: 13px; text-align: center; color: #9CA3AF; }}
    .msg.error {{ color: #EF4444; }}
    .msg.success {{ color: #10B981; font-size: 15px; }}
  </style>
</head>
<body>
  <div class="logo">📶</div>
  <h1>Moshi BusStop WiFi</h1>
  <p class="sub">Lipa kwa simu yako — unaungwa mkono papo hapo</p>

  <div class="packages" id="pkgs">
    {package_html}
  </div>

  <div class="form">
    <input type="tel" id="phone" placeholder="Nambari ya simu (07XXXXXXXX)" maxlength="13">
    <button id="payBtn" onclick="pay()">Lipa Sasa</button>
    <p class="msg" id="msg"></p>
  </div>

  <script>
    const MAC = "{mac}";
    let selectedPkg = null;

    document.querySelectorAll('.pkg').forEach(el => {{
      el.addEventListener('click', () => {{
        document.querySelectorAll('.pkg').forEach(e => e.classList.remove('selected'));
        el.classList.add('selected');
        selectedPkg = el.dataset.id;
      }});
    }});

    // Auto-select first package
    document.querySelector('.pkg')?.click();

    async function pay() {{
      const phone = document.getElementById('phone').value.trim();
      const btn   = document.getElementById('payBtn');
      const msg   = document.getElementById('msg');

      if (!selectedPkg) {{ msg.textContent = 'Chagua kifurushi kwanza.'; msg.className = 'msg error'; return; }}
      if (!phone) {{ msg.textContent = 'Weka nambari ya simu.'; msg.className = 'msg error'; return; }}

      btn.disabled = true;
      msg.className = 'msg';
      msg.textContent = 'Inatuma ombi la malipo...';

      const res = await fetch('/api/pay', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ phone, package_id: parseInt(selectedPkg), mac: MAC }})
      }});

      const data = await res.json();

      if (res.ok) {{
        msg.className = 'msg success';
        msg.textContent = '✅ Thibitisha malipo kwenye simu yako. Utaunganishwa baada ya sekunde chache.';
        // Poll for session activation
        setTimeout(() => pollSession(), 5000);
      }} else {{
        msg.className = 'msg error';
        msg.textContent = data.detail || 'Kuna tatizo. Jaribu tena.';
        btn.disabled = false;
      }}
    }}

    async function pollSession(attempts = 0) {{
      if (attempts > 12) return; // give up after 60s
      const res  = await fetch('/api/session/' + encodeURIComponent(MAC));
      const data = await res.json();
      if (data.active) {{
        document.getElementById('msg').textContent = '🎉 Umefanikiwa! Inakuunganisha...';
        // Omada will redirect automatically once MAC is authorized
        setTimeout(() => window.location.reload(), 2000);
      }} else {{
        setTimeout(() => pollSession(attempts + 1), 5000);
      }}
    }}
  </script>
</body>
</html>
"""


@router.get("/portal", response_class=HTMLResponse)
async def portal(
    mac: str = "00:00:00:00:00:00",
    ap:  str = "",
    db:  AsyncSession = Depends(get_db),
):
    """Captive portal landing page — Omada redirects here."""
    result = await db.execute(select(Package).where(Package.is_active == True))
    packages = result.scalars().all()

    pkg_html = ""
    for p in packages:
        pkg_html += f"""
        <div class="pkg" data-id="{p.id}">
          <div>
            <div class="pkg-name">{p.name}</div>
            <div class="pkg-meta">{p.speed_mbps} Mbps · {p.max_devices} device(s)</div>
          </div>
          <div class="pkg-price">{p.price_tzs:,} TZS</div>
        </div>"""

    html = PORTAL_HTML.format(package_html=pkg_html, mac=mac)
    return HTMLResponse(content=html)


# ── Packages JSON ─────────────────────────────────────────────────────────────

@router.get("/packages")
async def list_packages(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Package).where(Package.is_active == True))
    packages = result.scalars().all()
    return [
        {
            "id":            p.id,
            "name":          p.name,
            "price_tzs":     p.price_tzs,
            "duration_mins": p.duration_mins,
            "speed_mbps":    p.speed_mbps,
            "max_devices":   p.max_devices,
        }
        for p in packages
    ]


# ── Initiate payment ──────────────────────────────────────────────────────────

@router.post("/api/pay")
async def initiate_pay(req: PayRequest, db: AsyncSession = Depends(get_db)):
    """
    1. Validate package exists
    2. Create a pending Transaction record
    3. Call Azampay to push USSD prompt to user's phone
    4. Return immediately — actual grant happens in webhook
    """
    # Validate package
    pkg = await db.get(Package, req.package_id)
    if not pkg or not pkg.is_active:
        raise HTTPException(status_code=404, detail="Kifurushi hakipatikani.")

    # Check if device already has an active session
    existing = await db.execute(
        select(Session).where(
            Session.mac_address == req.mac,
            Session.is_active   == True,
            Session.expires_at  > datetime.utcnow(),
        )
    )
    if existing.scalars().first():
        raise HTTPException(status_code=400, detail="Kifaa hiki kina muunganisho tayari.")

    # Detect MNO from phone number
    provider  = detect_provider(req.phone)
    reference = f"{req.mac}:{req.package_id}"

    # Record pending transaction
    txn = Transaction(
        phone       = req.phone,
        amount      = pkg.price_tzs,
        package_id  = pkg.id,
        mac_address = req.mac,
        status      = "pending",
    )
    db.add(txn)
    await db.commit()
    await db.refresh(txn)

    # Push payment to Azampay
    try:
        result = await initiate_payment(
            phone     = req.phone,
            amount    = pkg.price_tzs,
            reference = f"{reference}:{txn.id}",
            provider  = provider,
        )
    except Exception as e:
        logger.error("Azampay initiate error: %s", e)
        raise HTTPException(status_code=502, detail="Huduma ya malipo haifanyi kazi. Jaribu tena.")

    # Check Azampay responded OK
    if result.get("success") is False:
        raise HTTPException(status_code=400, detail=result.get("message", "Malipo hayakufanikiwa."))

    return {
        "message":        "Ombi limetumwa. Thibitisha kwenye simu yako.",
        "transaction_id": txn.id,
        "provider":       provider,
        "amount":         pkg.price_tzs,
    }


# ── Azampay webhook ───────────────────────────────────────────────────────────

@router.post("/webhook/azampay")
async def azampay_webhook(payload: AzampayWebhook, request: Request, db: AsyncSession = Depends(get_db)):
    """
    Azampay calls this after the user approves/declines the USSD prompt.
    On SUCCESS:
      - Update transaction to success
      - Create a Session record
      - Call Omada to authorize the MAC address
    """
    logger.info("Azampay webhook received: %s", payload.dict())

    # Parse our reference: "mac:package_id:txn_id"
    ref_parts = (payload.externalId or "").split(":")
    if len(ref_parts) < 3:
        logger.warning("Invalid webhook reference: %s", payload.externalId)
        return JSONResponse({"status": "ignored"})

    # MAC might have colons so reconstruct carefully
    # Format was: "AA:BB:CC:DD:EE:FF:package_id:txn_id"
    txn_id     = int(ref_parts[-1])
    package_id = int(ref_parts[-2])
    mac        = ":".join(ref_parts[:-2])

    # Fetch transaction
    txn = await db.get(Transaction, txn_id)
    if not txn:
        logger.error("Transaction %s not found", txn_id)
        return JSONResponse({"status": "not_found"})

    if txn.status != "pending":
        return JSONResponse({"status": "already_processed"})

    if payload.status != "SUCCESS":
        # Payment failed or cancelled
        txn.status = "failed"
        await db.commit()
        logger.info("Payment failed for txn %s", txn_id)
        return JSONResponse({"status": "recorded_failure"})

    # ── Payment succeeded ──
    pkg = await db.get(Package, package_id)
    if not pkg:
        logger.error("Package %s not found", package_id)
        return JSONResponse({"status": "error"})

    now        = datetime.utcnow()
    expires_at = now + timedelta(minutes=pkg.duration_mins)

    # Create session
    session = Session(
        mac_address = mac,
        phone       = payload.msisdn or txn.phone,
        package_id  = package_id,
        started_at  = now,
        expires_at  = expires_at,
        is_active   = True,
    )
    db.add(session)
    await db.flush()  # get session.id

    # Update transaction
    txn.status     = "success"
    txn.azampay_ref= payload.transactionId
    txn.session_id = session.id
    await db.commit()

    # Authorize on Omada
    try:
        authorized = await authorize_client(
            mac              = mac,
            duration_minutes = pkg.duration_mins,
            rate_limit_mbps  = pkg.speed_mbps,
        )
        if not authorized:
            logger.error("Omada authorization failed for MAC %s", mac)
    except Exception as e:
        logger.error("Omada authorize error: %s", e)
        # Don't fail the webhook — session is recorded, can retry Omada manually

    logger.info("Session created for MAC %s — expires %s", mac, expires_at)
    return JSONResponse({"status": "success"})


# ── Session status check ──────────────────────────────────────────────────────

@router.get("/api/session/{mac}")
async def get_session(mac: str, db: AsyncSession = Depends(get_db)):
    """Check if a device has an active session (used by portal JS to poll)."""
    result = await db.execute(
        select(Session).where(
            Session.mac_address == mac,
            Session.is_active   == True,
            Session.expires_at  > datetime.utcnow(),
        )
    )
    session = result.scalars().first()
    if not session:
        return {"active": False}

    remaining = session.expires_at - datetime.utcnow()
    return {
        "active":            True,
        "expires_at":        session.expires_at.isoformat(),
        "remaining_minutes": int(remaining.total_seconds() / 60),
        "package_id":        session.package_id,
    }
