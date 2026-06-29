"""
TP-Link Omada Cloud Controller service.
Authorizes/deauthorizes client MAC addresses after payment.

Omada Cloud API docs:
  https://use1-omada-cloud.tplinkcloud.com/doc/

Environment vars needed:
  OMADA_BASE_URL   - e.g. https://use1-omada-cloud.tplinkcloud.com
  OMADA_CLIENT_ID
  OMADA_CLIENT_SECRET
  OMADA_OMADAC_ID  - your controller ID (found in Omada portal URL)
  OMADA_SITE_NAME  - your site name e.g. "moshi-busstation"
"""
import os
import httpx
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

OMADA_BASE      = os.getenv("OMADA_BASE_URL", "https://use1-omada-cloud.tplinkcloud.com")
OMADA_CLIENT_ID = os.getenv("OMADA_CLIENT_ID", "")
OMADA_SECRET    = os.getenv("OMADA_CLIENT_SECRET", "")
OMADA_ID        = os.getenv("OMADA_OMADAC_ID", "")
OMADA_SITE      = os.getenv("OMADA_SITE_NAME", "moshi-busstation")

_token_cache = {"token": None, "expires_at": datetime.min}


async def _get_token() -> str:
    global _token_cache
    if _token_cache["token"] and datetime.utcnow() < _token_cache["expires_at"]:
        return _token_cache["token"]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{OMADA_BASE}/openapi/authorize/token?grant_type=client_credentials",
            data={
                "client_id":     OMADA_CLIENT_ID,
                "client_secret": OMADA_SECRET,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data["result"]["accessToken"]
        _token_cache = {
            "token":      token,
            "expires_at": datetime.utcnow() + timedelta(hours=1),
        }
        return token


async def authorize_client(mac: str, duration_minutes: int, rate_limit_mbps: int = 1) -> bool:
    """
    Grant internet access to a device MAC for `duration_minutes`.
    Omada will automatically disconnect the client when time expires.
    Returns True on success.
    """
    token = await _get_token()

    # Omada expects MAC without colons, uppercase
    mac_clean = mac.replace(":", "").upper()

    payload = {
        "mac":        mac_clean,
        "duration":   duration_minutes * 60,  # Omada uses seconds
        "limitUp":    rate_limit_mbps * 1024, # KB/s upload
        "limitDown":  rate_limit_mbps * 1024, # KB/s download
        "trafficUp":  0,   # 0 = unlimited traffic quota
        "trafficDown":0,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{OMADA_BASE}/openapi/v1/{OMADA_ID}/sites/{OMADA_SITE}/hotspot/extPortal/authorize",
            json=payload,
            headers={
                "Authorization": f"AccessToken={token}",
                "Content-Type":  "application/json",
            },
            timeout=15,
        )

    data = resp.json()
    success = data.get("errorCode", -1) == 0
    if not success:
        logger.error("Omada authorize failed: %s", data)
    else:
        logger.info("Omada authorized MAC %s for %d minutes", mac, duration_minutes)
    return success


async def deauthorize_client(mac: str) -> bool:
    """Revoke internet access for a device (session expired or admin action)."""
    token = await _get_token()
    mac_clean = mac.replace(":", "").upper()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{OMADA_BASE}/openapi/v1/{OMADA_ID}/sites/{OMADA_SITE}/hotspot/extPortal/unauthorize",
            json={"mac": mac_clean},
            headers={
                "Authorization": f"AccessToken={token}",
                "Content-Type":  "application/json",
            },
            timeout=15,
        )

    data = resp.json()
    success = data.get("errorCode", -1) == 0
    logger.info("Omada deauthorize MAC %s — success=%s", mac, success)
    return success
