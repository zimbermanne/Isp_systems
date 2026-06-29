"""
Moshi Hotspot — FastAPI Application
=====================================
Entry point for Railway deployment.

Start locally:
  uvicorn main:app --reload --port 8000

Environment variables (set in Railway dashboard):
  DATABASE_URL            - Railway PostgreSQL URL (auto-set by Railway)
  AZAMPAY_BASE_URL        - https://sandbox.azampay.co.tz (testing) or prod URL
  AZAMPAY_APP_NAME        - Your app name on Azampay portal
  AZAMPAY_CLIENT_ID       - From Azampay dashboard
  AZAMPAY_SECRET          - From Azampay dashboard
  AZAMPAY_WEBHOOK_SECRET  - Secret for verifying webhook calls
  OMADA_BASE_URL          - https://use1-omada-cloud.tplinkcloud.com
  OMADA_CLIENT_ID         - From Omada developer portal
  OMADA_CLIENT_SECRET     - From Omada developer portal
  OMADA_OMADAC_ID         - Your controller ID (in Omada portal URL)
  OMADA_SITE_NAME         - Your site name e.g. moshi-busstation
  ADMIN_API_KEY           - Secret key for /admin/* endpoints
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from models.database import init_db
from routers.portal import router as portal_router
from routers.admin import router as admin_router
from scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    logger.info("🚀 Starting Moshi Hotspot backend...")
    await init_db()          # create tables + seed packages
    start_scheduler()        # begin session expiry jobs
    yield
    stop_scheduler()
    logger.info("🛑 Shutting down.")


app = FastAPI(
    title       = "Moshi Hotspot API",
    description = "Captive portal billing system — solar-powered bus station WiFi",
    version     = "1.0.0",
    lifespan    = lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins  = ["*"],
    allow_methods  = ["*"],
    allow_headers  = ["*"],
)

app.include_router(portal_router)
app.include_router(admin_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "moshi-hotspot"}


@app.get("/")
async def root():
    return {
        "name":    "Moshi BusStop Hotspot",
        "version": "1.0.0",
        "docs":    "/docs",
    }
