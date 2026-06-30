"""
Admin routes — revenue, sessions, transactions.
Protected by a simple API key header: X-Admin-Key
"""
import os
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import HTMLResponse
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


# ── Admin dashboard (HTML) ──────────────────────────────────────────────────────
#
# This route serves only the page shell — no data is rendered server-side and
# the route itself is NOT behind require_admin (a plain browser GET can't send
# the X-Admin-Key header). Instead the page asks for the key once client-side,
# stores it in sessionStorage (cleared when the tab closes — intentional,
# since this may be opened from different/shared devices), and sends it as a
# header on every call to /admin/revenue, /admin/sessions, /admin/transactions.
# A 401 on any of those clears the stored key and re-prompts.

DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Moshi Hotspot — Admin</title>
<style>
  :root {
    --bg: #0D1117; --card: #1A1F2C; --border: #2D3748; --text: #F7F6F2;
    --muted: #9CA3AF; --accent: #1A6B4A; --accent-hover: #15573D;
    --error: #EF4444; --success: #10B981; --warn: #F59E0B;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg); color: var(--text); min-height: 100vh;
  }
  .num { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }

  header {
    position: sticky; top: 0; z-index: 5;
    display: flex; align-items: center; justify-content: space-between;
    padding: 16px 24px; border-bottom: 1px solid var(--border);
    background: rgba(13,17,23,0.92); backdrop-filter: blur(6px);
  }
  .brand { display: flex; align-items: center; gap: 10px; font-weight: 700; font-size: 16px; }
  .brand .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
  .brand .dot.live { background: var(--success); box-shadow: 0 0 0 3px rgba(16,185,129,0.18); }
  .brand .dot.down { background: var(--error); box-shadow: 0 0 0 3px rgba(239,68,68,0.18); }
  .hdr-actions { display: flex; align-items: center; gap: 10px; }
  .hdr-actions .updated { font-size: 12px; color: var(--muted); }
  button.ghost {
    background: transparent; border: 1px solid var(--border); color: var(--text);
    padding: 7px 12px; border-radius: 8px; font-size: 13px; cursor: pointer;
  }
  button.ghost:hover { border-color: var(--accent); }
  button.icon { width: 32px; height: 32px; padding: 0; display: flex; align-items: center; justify-content: center; }
  .spin { animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  main { max-width: 1080px; margin: 0 auto; padding: 24px 20px 60px; display: none; }
  main.show { display: block; }

  #banner {
    display: none; background: #3A1313; border: 1px solid var(--error); color: #FCA5A5;
    padding: 10px 16px; border-radius: 8px; font-size: 13px; margin-bottom: 18px;
  }
  #banner.show { display: block; }

  .stats { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 22px; }
  .stat {
    flex: 1; min-width: 150px; background: var(--card); border: 1px solid var(--border);
    border-radius: 12px; padding: 16px 18px;
  }
  .stat .label { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
  .stat .value { font-size: 24px; font-weight: 700; }
  .stat .value.accent { color: var(--accent); }

  section { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 18px 20px; margin-bottom: 18px; }
  .sec-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; flex-wrap: wrap; gap: 8px; }
  .sec-head h2 { font-size: 14px; font-weight: 600; }
  select {
    background: #11151D; border: 1px solid var(--border); color: var(--text);
    border-radius: 6px; padding: 6px 10px; font-size: 13px;
  }

  .chart { display: flex; align-items: flex-end; gap: 4px; height: 90px; }
  .bar { flex: 1; background: var(--accent); border-radius: 3px 3px 0 0; min-height: 2px; cursor: default; }
  .bar:hover { background: #21895c; }
  .chart-labels { display: flex; gap: 4px; margin-top: 6px; }
  .chart-labels span { flex: 1; font-size: 10px; color: var(--muted); text-align: center; }

  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; color: var(--muted); font-weight: 500; font-size: 11px; text-transform: uppercase; letter-spacing: .03em; padding: 8px 10px; border-bottom: 1px solid var(--border); }
  td { padding: 10px; border-bottom: 1px solid #1f2430; vertical-align: middle; }
  tr:last-child td { border-bottom: none; }
  .table-wrap { overflow-x: auto; }
  .badge { display: inline-block; padding: 3px 9px; border-radius: 20px; font-size: 11px; font-weight: 600; }
  .badge.success { background: rgba(16,185,129,0.15); color: var(--success); }
  .badge.pending { background: rgba(245,158,11,0.15); color: var(--warn); }
  .badge.failed { background: rgba(239,68,68,0.15); color: var(--error); }
  .remaining.low { color: var(--warn); font-weight: 600; }
  .empty { color: var(--muted); font-size: 13px; text-align: center; padding: 30px 0; }
  .revoke-btn { background: transparent; border: 1px solid var(--error); color: var(--error); padding: 5px 10px; border-radius: 6px; font-size: 12px; cursor: pointer; }
  .revoke-btn:hover { background: rgba(239,68,68,0.1); }
  .mac { color: var(--muted); font-size: 12px; }

  #gate {
    position: fixed; inset: 0; background: rgba(13,17,23,0.97);
    display: flex; align-items: center; justify-content: center; z-index: 20; padding: 20px;
  }
  .gate-card { width: 100%; max-width: 340px; }
  .gate-card .logo { font-size: 30px; margin-bottom: 10px; text-align: center; }
  .gate-card h1 { font-size: 17px; text-align: center; margin-bottom: 4px; }
  .gate-card p { font-size: 13px; color: var(--muted); text-align: center; margin-bottom: 22px; }
  .gate-card input {
    width: 100%; padding: 13px 14px; border-radius: 10px; border: 1px solid var(--border);
    background: #1A1F2C; color: var(--text); font-size: 15px; margin-bottom: 10px;
  }
  .gate-card input:focus { outline: none; border-color: var(--accent); }
  .gate-card button { width: 100%; padding: 13px; background: var(--accent); color: white; border: none; border-radius: 10px; font-size: 15px; font-weight: 600; cursor: pointer; }
  .gate-card button:hover { background: var(--accent-hover); }
  .gate-card button:disabled { background: #374151; cursor: not-allowed; }
  .gate-err { color: var(--error); font-size: 13px; text-align: center; margin-top: 10px; min-height: 16px; }

  @media (max-width: 600px) {
    .stats { flex-direction: column; }
    header { padding: 14px 16px; }
    main { padding: 18px 12px 50px; }
  }
</style>
</head>
<body>

<div id="gate">
  <div class="gate-card">
    <div class="logo">📶</div>
    <h1>Moshi Hotspot Admin</h1>
    <p>Enter the admin key to view revenue, sessions, and transactions.</p>
    <input type="password" id="keyInput" placeholder="Admin key" autocomplete="off">
    <button id="enterBtn">Enter</button>
    <p class="gate-err" id="gateErr"></p>
  </div>
</div>

<header>
  <div class="brand"><span class="dot" id="statusDot"></span>📶 Moshi Hotspot — Admin</div>
  <div class="hdr-actions">
    <span class="updated" id="updatedAt"></span>
    <button class="ghost icon" id="refreshBtn" title="Refresh">⟳</button>
    <button class="ghost" id="logoutBtn">Change key</button>
  </div>
</header>

<main id="main">
  <div id="banner"></div>

  <div class="stats">
    <div class="stat"><div class="label">Revenue (<span id="periodLabel">30</span>d)</div><div class="value accent num" id="statRevenue">—</div></div>
    <div class="stat"><div class="label">Active sessions</div><div class="value num" id="statSessions">—</div></div>
    <div class="stat"><div class="label">Transactions shown</div><div class="value num" id="statTxns">—</div></div>
  </div>

  <section>
    <div class="sec-head">
      <h2>Revenue</h2>
      <select id="periodSelect">
        <option value="7">Last 7 days</option>
        <option value="30" selected>Last 30 days</option>
        <option value="90">Last 90 days</option>
      </select>
    </div>
    <div class="chart" id="chart"></div>
    <div class="chart-labels" id="chartLabels"></div>
  </section>

  <section>
    <div class="sec-head"><h2>Active sessions</h2></div>
    <div class="table-wrap">
      <table id="sessionsTable">
        <thead><tr><th>Phone</th><th>Package</th><th>MAC</th><th>Remaining</th><th>Expires</th><th></th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
    <div class="empty" id="sessionsEmpty" style="display:none;">No active sessions right now.</div>
  </section>

  <section>
    <div class="sec-head">
      <h2>Recent transactions</h2>
      <select id="limitSelect">
        <option value="25">Last 25</option>
        <option value="50" selected>Last 50</option>
        <option value="100">Last 100</option>
      </select>
    </div>
    <div class="table-wrap">
      <table id="txnsTable">
        <thead><tr><th>Time</th><th>Phone</th><th>Package</th><th>Amount</th><th>Status</th><th>MAC</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
    <div class="empty" id="txnsEmpty" style="display:none;">No transactions yet.</div>
  </section>
</main>

<script>
(function() {
  const KEY_STORAGE = 'moshi_admin_key';
  let adminKey = sessionStorage.getItem(KEY_STORAGE) || '';
  let refreshTimer = null;

  const gate = document.getElementById('gate');
  const main = document.getElementById('main');
  const gateErr = document.getElementById('gateErr');
  const keyInput = document.getElementById('keyInput');
  const banner = document.getElementById('banner');
  const statusDot = document.getElementById('statusDot');
  const updatedAt = document.getElementById('updatedAt');
  const refreshBtn = document.getElementById('refreshBtn');

  function fmtTZS(n) { return (n || 0).toLocaleString() + ' TZS'; }

  function toUTCDate(iso) {
    if (!iso) return null;
    let s = iso;
    const dot = s.indexOf('.');
    if (dot !== -1) s = s.slice(0, dot + 4);
    if (!/Z|[+-]\d\d:\d\d$/.test(s)) s += 'Z';
    return new Date(s);
  }

  function fmtDateTime(iso) {
    const d = toUTCDate(iso);
    if (!d) return '—';
    return new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Africa/Dar_es_Salaam', day: '2-digit', month: 'short',
      hour: '2-digit', minute: '2-digit'
    }).format(d);
  }

  function fmtDayLabel(dateStr) {
    const d = new Date(dateStr + 'T00:00:00Z');
    return new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', day: '2-digit', month: 'short' }).format(d);
  }

  function fmtRemaining(mins) {
    if (mins >= 60) {
      const h = Math.floor(mins / 60), m = mins % 60;
      return h + 'h ' + m + 'm';
    }
    return mins + 'm';
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function(c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }

  function AuthError(msg) { this.message = msg; this.isAuthError = true; }
  AuthError.prototype = Object.create(Error.prototype);

  async function apiGet(path) {
    const res = await fetch(path, { headers: { 'X-Admin-Key': adminKey } });
    if (res.status === 401) throw new AuthError('Unauthorized');
    if (!res.ok) throw new Error('Request failed: ' + res.status);
    return res.json();
  }

  async function apiPost(path) {
    const res = await fetch(path, { method: 'POST', headers: { 'X-Admin-Key': adminKey } });
    if (res.status === 401) throw new AuthError('Unauthorized');
    if (!res.ok) throw new Error('Request failed: ' + res.status);
    return res.json();
  }

  function showGate(msg) {
    gate.style.display = 'flex';
    main.classList.remove('show');
    gateErr.textContent = msg || '';
    if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
    keyInput.focus();
  }

  function hideGate() {
    gate.style.display = 'none';
    main.classList.add('show');
  }

  function setStatus(ok) {
    statusDot.className = 'dot ' + (ok ? 'live' : 'down');
  }

  function showBanner(msg) {
    banner.textContent = msg;
    banner.classList.add('show');
  }
  function hideBanner() { banner.classList.remove('show'); }

  function renderRevenue(data) {
    document.getElementById('periodLabel').textContent = data.period_days;
    document.getElementById('statRevenue').textContent = fmtTZS(data.total_tzs);

    const days = data.daily.slice().reverse();
    const chart = document.getElementById('chart');
    const labels = document.getElementById('chartLabels');
    chart.innerHTML = ''; labels.innerHTML = '';
    if (days.length === 0) {
      chart.innerHTML = '<div class="empty" style="width:100%;">No revenue in this period.</div>';
      return;
    }
    const max = Math.max.apply(null, days.map(function(d) { return d.revenue_tzs; }).concat([1]));
    days.forEach(function(d) {
      const bar = document.createElement('div');
      bar.className = 'bar';
      bar.style.height = Math.max(2, (d.revenue_tzs / max) * 100) + '%';
      bar.title = d.date + ' — ' + fmtTZS(d.revenue_tzs) + ' (' + d.transactions + ' txns)';
      chart.appendChild(bar);
      const lbl = document.createElement('span');
      lbl.textContent = fmtDayLabel(d.date);
      labels.appendChild(lbl);
    });
  }

  function renderSessions(rows) {
    document.getElementById('statSessions').textContent = rows.length;
    const tbody = document.querySelector('#sessionsTable tbody');
    const empty = document.getElementById('sessionsEmpty');
    tbody.innerHTML = '';
    empty.style.display = rows.length ? 'none' : 'block';
    rows.forEach(function(s) {
      const tr = document.createElement('tr');
      const lowClass = s.remaining_minutes <= 10 ? 'remaining low' : 'remaining';
      tr.innerHTML =
        '<td>' + escapeHtml(s.phone) + '</td>' +
        '<td>' + escapeHtml(s.package) + '</td>' +
        '<td class="mac num">' + escapeHtml(s.mac) + '</td>' +
        '<td class="' + lowClass + ' num">' + fmtRemaining(s.remaining_minutes) + '</td>' +
        '<td>' + fmtDateTime(s.expires_at) + '</td>' +
        '<td><button class="revoke-btn" data-id="' + s.id + '">Revoke</button></td>';
      tbody.appendChild(tr);
    });
    tbody.querySelectorAll('.revoke-btn').forEach(function(btn) {
      btn.addEventListener('click', function() { revokeSession(btn.dataset.id, btn); });
    });
  }

  function renderTransactions(rows) {
    document.getElementById('statTxns').textContent = rows.length;
    const tbody = document.querySelector('#txnsTable tbody');
    const empty = document.getElementById('txnsEmpty');
    tbody.innerHTML = '';
    empty.style.display = rows.length ? 'none' : 'block';
    rows.forEach(function(t) {
      const tr = document.createElement('tr');
      tr.innerHTML =
        '<td>' + fmtDateTime(t.created_at) + '</td>' +
        '<td>' + escapeHtml(t.phone) + '</td>' +
        '<td>' + escapeHtml(t.package) + '</td>' +
        '<td class="num">' + fmtTZS(t.amount) + '</td>' +
        '<td><span class="badge ' + t.status + '">' + t.status + '</span></td>' +
        '<td class="mac num">' + escapeHtml(t.mac) + '</td>';
      tbody.appendChild(tr);
    });
  }

  async function revokeSession(id, btn) {
    if (!confirm('Revoke this session? The device will be disconnected immediately.')) return;
    btn.disabled = true;
    btn.textContent = '…';
    try {
      await apiPost('/admin/sessions/' + id + '/revoke');
      await loadSessions();
    } catch (e) {
      if (e && e.isAuthError) { onAuthError(); return; }
      showBanner('Could not revoke session. Try again.');
      btn.disabled = false;
      btn.textContent = 'Revoke';
    }
  }

  async function loadRevenue() {
    const days = document.getElementById('periodSelect').value;
    renderRevenue(await apiGet('/admin/revenue?days=' + days));
  }
  async function loadSessions() {
    renderSessions(await apiGet('/admin/sessions'));
  }
  async function loadTransactions() {
    const limit = document.getElementById('limitSelect').value;
    renderTransactions(await apiGet('/admin/transactions?limit=' + limit));
  }

  function onAuthError() {
    sessionStorage.removeItem(KEY_STORAGE);
    adminKey = '';
    setStatus(false);
    showGate('That key was rejected. Enter the correct admin key.');
  }

  async function loadAll(isManual) {
    if (isManual) refreshBtn.classList.add('spin');
    const results = await Promise.allSettled([loadRevenue(), loadSessions(), loadTransactions()]);
    if (isManual) refreshBtn.classList.remove('spin');

    const authFailed = results.some(function(r) { return r.status === 'rejected' && r.reason && r.reason.isAuthError; });
    if (authFailed) { onAuthError(); return; }

    const otherFailed = results.some(function(r) { return r.status === 'rejected'; });
    if (otherFailed) {
      setStatus(false);
      showBanner('Some data failed to load — check your connection and try refreshing.');
    } else {
      setStatus(true);
      hideBanner();
      updatedAt.textContent = 'Updated ' + new Intl.DateTimeFormat('en-GB', { timeZone: 'Africa/Dar_es_Salaam', hour: '2-digit', minute: '2-digit' }).format(new Date());
    }
  }

  async function tryEnter() {
    const val = keyInput.value.trim();
    if (!val) { gateErr.textContent = 'Enter a key.'; return; }
    document.getElementById('enterBtn').disabled = true;
    adminKey = val;
    try {
      await apiGet('/admin/sessions');
      sessionStorage.setItem(KEY_STORAGE, adminKey);
      hideGate();
      await loadAll();
      if (!refreshTimer) refreshTimer = setInterval(function() { loadAll(false); }, 30000);
    } catch (e) {
      adminKey = '';
      gateErr.textContent = 'Incorrect key. Try again.';
    } finally {
      document.getElementById('enterBtn').disabled = false;
    }
  }

  document.getElementById('enterBtn').addEventListener('click', tryEnter);
  keyInput.addEventListener('keydown', function(e) { if (e.key === 'Enter') tryEnter(); });
  refreshBtn.addEventListener('click', function() { loadAll(true); });
  document.getElementById('logoutBtn').addEventListener('click', function() {
    sessionStorage.removeItem(KEY_STORAGE);
    adminKey = '';
    if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
    keyInput.value = '';
    showGate('');
  });
  document.getElementById('periodSelect').addEventListener('change', function() {
    loadRevenue().catch(function(e) { if (e && e.isAuthError) onAuthError(); });
  });
  document.getElementById('limitSelect').addEventListener('change', function() {
    loadTransactions().catch(function(e) { if (e && e.isAuthError) onAuthError(); });
  });

  if (adminKey) {
    hideGate();
    loadAll().then(function() {
      if (!refreshTimer) refreshTimer = setInterval(function() { loadAll(false); }, 30000);
    });
  } else {
    showGate('');
  }
})();
</script>
</body>
</html>
"""


@router.get("/dashboard", response_class=HTMLResponse)
async def admin_dashboard():
    """Visual admin dashboard — revenue, active sessions, transactions.

    Serves the page shell only. Data loads client-side via the existing
    JSON endpoints above, authenticated with the X-Admin-Key the user
    enters once (kept in sessionStorage for that browser tab).
    """
    return HTMLResponse(content=DASHBOARD_HTML)
