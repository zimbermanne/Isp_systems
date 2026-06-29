# Moshi BusStop Hotspot — Billing System

FastAPI + PostgreSQL captive portal billing backend.  
Deployed on Railway · Payments via Azampay · WiFi via TP-Link Omada.

---

## Project Structure

```
hotspot/
├── main.py                  # FastAPI app entry point
├── scheduler.py             # APScheduler — session expiry every 5 min
├── requirements.txt
├── Procfile                 # Railway startup command
├── .env.example             # Copy variables to Railway dashboard
├── models/
│   └── database.py          # SQLAlchemy models + DB init
├── routers/
│   ├── portal.py            # GET /portal, POST /api/pay, POST /webhook/azampay
│   └── admin.py             # GET /admin/revenue, /admin/sessions etc.
└── services/
    ├── azampay.py           # Azampay payment API client
    └── omada.py             # TP-Link Omada authorize/deauthorize
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/portal?mac=XX:XX` | Captive portal page (Omada redirects here) |
| GET | `/packages` | List available packages |
| POST | `/api/pay` | Initiate Azampay payment |
| POST | `/webhook/azampay` | Payment confirmation callback |
| GET | `/api/session/{mac}` | Check session status |
| GET | `/admin/revenue?days=30` | Revenue report |
| GET | `/admin/sessions` | Active sessions |
| POST | `/admin/sessions/{id}/revoke` | Revoke a session |
| GET | `/admin/transactions` | Recent transactions |

---

## Railway Deployment Steps

### 1. Create Railway project
```bash
railway login
railway init
railway add postgresql   # adds DATABASE_URL automatically
```

### 2. Set environment variables
Copy `.env.example` → Railway Dashboard → Variables tab.

### 3. Deploy
```bash
git add . && git commit -m "hotspot billing system"
railway up
```

Railway will detect `Procfile` and run:
```
uvicorn main:app --host 0.0.0.0 --port $PORT
```

### 4. Note your Railway URL
e.g. `https://moshi-hotspot.up.railway.app`

---

## Omada Captive Portal Setup

In your Omada Cloud controller:

1. Go to **Site → Hotspot → Portal**
2. Select **External Portal Server**
3. Set Portal URL to:
   ```
   https://moshi-hotspot.up.railway.app/portal
   ```
4. Set Authentication Type: **External RADIUS** or **No Authentication** (let your app handle it)
5. In **Portal Customization**, set redirect after auth to your success page

Omada will append `?mac=XX:XX:XX:XX:XX:XX&ap=site-name` to the portal URL automatically.

---

## Azampay Setup

1. Register at https://azampay.co.tz/developer
2. Create an app → get `clientId` and `clientSecret`
3. Set webhook URL in Azampay dashboard:
   ```
   https://moshi-hotspot.up.railway.app/webhook/azampay
   ```
4. Start with sandbox (`https://sandbox.azampay.co.tz`) for testing
5. Switch `AZAMPAY_BASE_URL` to production after testing

---

## Payment Flow Summary

```
User connects → Omada redirects to /portal
  → User enters phone + selects package
  → POST /api/pay
  → Azampay pushes USSD to phone
  → User approves on phone
  → Azampay calls POST /webhook/azampay
  → Backend creates Session + calls Omada to authorize MAC
  → User gets internet access for purchased duration
  → APScheduler checks every 5 min → deauthorizes expired sessions
```

---

## Admin Usage

All `/admin/*` routes require header:
```
X-Admin-Key: your_admin_key
```

Check revenue:
```bash
curl https://moshi-hotspot.up.railway.app/admin/revenue?days=7 \
  -H "X-Admin-Key: your_key"
```

---

## Local Development

```bash
cd hotspot
pip install -r requirements.txt
cp .env.example .env   # fill in your values
uvicorn main:app --reload
# visit http://localhost:8000/portal?mac=AA:BB:CC:DD:EE:FF
```
