"""
Azampay payment service.
Docs: https://developers.azampay.co.tz

Flow:
  1. POST /api/pay       → call azampay to push USSD prompt to user phone
  2. Azampay calls back  → POST /webhook/azampay with payment result
  3. On SUCCESS          → authorize MAC on Omada + create session
"""
import os
import httpx
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

AZAMPAY_BASE       = os.getenv("AZAMPAY_BASE_URL", "https://sandbox.azampay.co.tz")
AZAMPAY_APP_NAME   = os.getenv("AZAMPAY_APP_NAME", "MoshiHotspot")
AZAMPAY_CLIENT_ID  = os.getenv("AZAMPAY_CLIENT_ID", "")
AZAMPAY_SECRET     = os.getenv("AZAMPAY_SECRET", "")

# Cache the bearer token (expires every ~60 min)
_token_cache = {"token": None, "expires_at": datetime.min}


async def _get_token() -> str:
    """Fetch or return cached Azampay bearer token."""
    global _token_cache
    if _token_cache["token"] and datetime.utcnow() < _token_cache["expires_at"]:
        return _token_cache["token"]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{AZAMPAY_BASE}/AppRegistration/GenerateToken",
            json={
                "appName":   AZAMPAY_APP_NAME,
                "clientId":  AZAMPAY_CLIENT_ID,
                "clientSecret": AZAMPAY_SECRET,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data["data"]["accessToken"]
        _token_cache = {
            "token":      token,
            "expires_at": datetime.utcnow() + timedelta(minutes=55),
        }
        return token


async def initiate_payment(
    phone: str,
    amount: int,
    reference: str,          # we use  mac_address:package_id
    provider: str = "Mpesa", # Mpesa | Tigopesa | Airtel | Halopesa
) -> dict:
    """
    Push a mobile money USSD prompt to the user's phone.
    Returns Azampay's response dict.
    """
    token = await _get_token()

    # Normalise phone → 255XXXXXXXXX
    phone = _normalize_phone(phone)

    payload = {
        "accountNumber": phone,
        "amount":        str(amount),
        "currency":      "TZS",
        "externalId":    reference,
        "provider":      provider,
        "additionalProperties": {
            "description": f"Moshi Hotspot - {reference}"
        },
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{AZAMPAY_BASE}/azampay/mno/checkout",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )

    data = resp.json()
    logger.info("Azampay initiate_payment response: %s", data)
    return data


def _normalize_phone(phone: str) -> str:
    """Convert 07XXXXXXXX or +2557XXXXXXXX to 2557XXXXXXXX."""
    phone = phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("+"):
        phone = phone[1:]
    if phone.startswith("0"):
        phone = "255" + phone[1:]
    return phone


def detect_provider(phone: str) -> str:
    """
    Auto-detect MNO from phone prefix.
    Tanzanian prefixes as of 2024.
    """
    phone = _normalize_phone(phone)
    prefix = phone[3:6]  # after 255

    MPESA    = {"076", "077", "078"}
    TIGO     = {"071", "072", "073", "074", "075"}
    AIRTEL   = {"068", "069", "078"}  # some 078 overlap
    HALOTEL  = {"062", "061"}

    if prefix in MPESA:
        return "Mpesa"
    elif prefix in TIGO:
        return "Tigopesa"
    elif prefix in AIRTEL:
        return "Airtel"
    elif prefix in HALOTEL:
        return "Halopesa"
    else:
        return "Mpesa"  # fallback
