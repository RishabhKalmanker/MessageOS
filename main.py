"""
MessageOS — Phase 1: SLA Core
FastAPI backend for personal message intelligence.
"""

import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse, HTMLResponse
from pydantic import BaseModel
from thefuzz import process as fuzz_process

from database import init_db, get_connection
from contacts import get_or_create_contact, effective_tier, _normalize_phone
from sla import open_sla_clock, close_sla_clocks_for_contact, start_watchdog, stop_watchdog
from notifier import alert_vip_received, alert_important_received

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class IMessageReceived(BaseModel):
    sender: str
    phone: Optional[str] = None
    body: Optional[str] = None
    timestamp: Optional[str] = None


class IMessageSent(BaseModel):
    recipient: Optional[str] = None
    phone: Optional[str] = None
    timestamp: Optional[str] = None


class ContactItem(BaseModel):
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None


class ContactsSync(BaseModel):
    contacts: List[ContactItem]


class TierUpdate(BaseModel):
    name: str
    tier: str


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[STARTUP] Initializing MessageOS")
    init_db()
    watchdog_thread = start_watchdog()
    yield
    logger.info("[SHUTDOWN] Stopping SLA watchdog")
    stop_watchdog()
    watchdog_thread.join(timeout=5)


app = FastAPI(title="MessageOS", version="1.1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# iMessage webhooks
# ---------------------------------------------------------------------------

@app.post("/webhook/imessage-received")
def imessage_received(payload: IMessageReceived):
    logger.info(f"[WEBHOOK] Received from '{payload.sender}' phone='{payload.phone}'")

    contact = get_or_create_contact(name=payload.sender, phone=payload.phone)
    contact_id = contact["id"]
    tier = effective_tier(contact)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO messages
           (contact_id, source, direction, body, received_at, sla_tier, sla_started_at)
           VALUES (?, 'imessage', 'inbound', ?, ?, ?, ?)""",
        (
            contact_id,
            payload.body or "",
            payload.timestamp or datetime.now(timezone.utc).isoformat(),
            tier,
            datetime.now(timezone.utc).isoformat() if tier in ("vip", "important") else None,
        ),
    )
    conn.commit()
    message_id = cur.lastrowid
    conn.execute(
        "UPDATE contacts SET last_contacted = datetime('now') WHERE id = ?", (contact_id,)
    )
    conn.commit()
    conn.close()

    snippet = (payload.body or "")[:80]

    if tier == "vip":
        alert_vip_received(contact["name"], snippet)
        open_sla_clock(message_id, contact_id)
        logger.info(f"[WEBHOOK] VIP {contact['name']} - SLA clock started")
    elif tier == "important":
        alert_important_received(contact["name"], snippet)
        open_sla_clock(message_id, contact_id)
        logger.info(f"[WEBHOOK] Important {contact['name']} - SLA clock started")
    else:
        logger.info(f"[WEBHOOK] Normal {contact['name']} - logged only")

    return {"status": "ok", "contact": contact["name"], "tier": tier, "message_id": message_id}


@app.post("/webhook/imessage-sent")
def imessage_sent(payload: IMessageSent):
    recipient = payload.recipient or "Unknown"
    phone = payload.phone or payload.recipient
    logger.info(f"[WEBHOOK] Sent to '{recipient}'")

    contact = get_or_create_contact(name=recipient, phone=phone)
    contact_id = contact["id"]

    conn = get_connection()
    conn.execute(
        """INSERT INTO messages (contact_id, source, direction, body, received_at)
           VALUES (?, 'imessage', 'outbound', '', datetime('now'))""",
        (contact_id,),
    )
    conn.execute(
        "UPDATE contacts SET last_contacted = datetime('now') WHERE id = ?", (contact_id,)
    )
    conn.commit()
    conn.close()

    close_sla_clocks_for_contact(contact_id)
    return {"status": "ok", "contact": contact["name"], "sla_clocks_closed": True}


# ---------------------------------------------------------------------------
# Contacts — list + sync + tier update
# ---------------------------------------------------------------------------

@app.get("/contacts/list")
def contacts_list():
    """Return all contacts as JSON — powers the contacts web UI."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, name, phone, email, tier, health_score, reply_probability, last_contacted FROM contacts ORDER BY name COLLATE NOCASE"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/contacts/sync")
def contacts_sync(payload: ContactsSync):
    """
    Upsert contacts from Apple Contacts (via iOS Shortcut).
    Matches on phone OR email. New contacts default to Normal tier.
    Existing contacts keep their current tier.
    Returns {"synced": N, "new": N, "updated": N}
    """
    new_count = 0
    updated_count = 0
    conn = get_connection()
    cur = conn.cursor()

    for item in payload.contacts:
        name = (item.name or "").strip()
        if not name:
            continue

        norm_phone = _normalize_phone(item.phone) if item.phone else None
        norm_email = item.email.strip().lower() if item.email else None

        # Try to find existing contact by phone or email
        existing = None
        if norm_phone:
            cur.execute("SELECT * FROM contacts WHERE phone = ?", (norm_phone,))
            existing = cur.fetchone()
        if not existing and norm_email:
            cur.execute("SELECT * FROM contacts WHERE email = ?", (norm_email,))
            existing = cur.fetchone()

        if existing:
            # Update name and fill in any missing phone/email — preserve tier
            cur.execute(
                """UPDATE contacts
                   SET name = ?,
                       phone = COALESCE(phone, ?),
                       email = COALESCE(email, ?)
                   WHERE id = ?""",
                (name, norm_phone, norm_email, existing["id"]),
            )
            updated_count += 1
        else:
            # New contact — default tier normal
            try:
                cur.execute(
                    "INSERT INTO contacts (name, phone, email, tier) VALUES (?, ?, ?, 'normal')",
                    (name, norm_phone, norm_email),
                )
                new_count += 1
            except Exception:
                # Unique constraint race — treat as updated
                updated_count += 1

    conn.commit()
    conn.close()
    total = new_count + updated_count
    logger.info(f"[SYNC] Synced {total} contacts: {new_count} new, {updated_count} updated")
    return {"synced": total, "new": new_count, "updated": updated_count}


@app.post("/contacts/{contact_id}/tier")
def set_contact_tier(contact_id: int, payload: dict):
    """Update a single contact's tier directly by ID (used by the web UI)."""
    tier = (payload.get("tier") or "").strip().lower()
    if tier not in ("vip", "important", "normal"):
        raise HTTPException(status_code=400, detail="tier must be vip, important, or normal")

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM contacts WHERE id = ?", (contact_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Contact not found")

    conn.execute("UPDATE contacts SET tier = ? WHERE id = ?", (tier, contact_id))
    conn.commit()
    conn.close()
    logger.info(f"[TIER] {row['name']} -> {tier}")
    return {"status": "ok", "name": row["name"], "tier": tier}


# ---------------------------------------------------------------------------
# Siri voice endpoints
# ---------------------------------------------------------------------------

FUZZY_THRESHOLD = 80


def _fuzzy_find_contact(name_query: str):
    """
    Fuzzy match name_query against all contact names.
    Returns (contact_row_dict, ambiguous_list) — exactly one will be non-None.
    """
    conn = get_connection()
    rows = conn.execute("SELECT * FROM contacts").fetchall()
    conn.close()

    if not rows:
        return None, []

    contacts = [dict(r) for r in rows]
    name_map = {c["name"]: c for c in contacts}

    matches = fuzz_process.extractBests(
        name_query, name_map.keys(), score_cutoff=FUZZY_THRESHOLD
    )

    if not matches:
        return None, []
    if len(matches) == 1:
        return name_map[matches[0][0]], []

    # Multiple matches — return ambiguous list for Siri to read back
    ambiguous = [name_map[m[0]] for m in matches[:5]]
    return None, ambiguous


@app.post("/siri/tier", response_class=PlainTextResponse)
def siri_set_tier(payload: TierUpdate):
    """
    Set a contact's tier by fuzzy name match.
    Returns plain text that Siri reads aloud.
    """
    tier = payload.tier.strip().lower()
    if tier not in ("vip", "important", "normal"):
        return f"Invalid tier '{payload.tier}'. Say VIP, Important, or Normal."

    contact, ambiguous = _fuzzy_find_contact(payload.name)

    if ambiguous:
        names = " or ".join(c["name"] for c in ambiguous)
        return f"Did you mean {names}? Be more specific."

    if not contact:
        return "Contact not found. Try a different name."

    conn = get_connection()
    conn.execute("UPDATE contacts SET tier = ? WHERE id = ?", (tier, contact["id"]))
    conn.commit()
    conn.close()
    logger.info(f"[SIRI] {contact['name']} -> {tier}")
    return f"{contact['name']} moved to {tier.upper() if tier == 'vip' else tier.capitalize()} tier."


@app.get("/siri/tier", response_class=PlainTextResponse)
def siri_get_tier(name: str = Query(...)):
    """
    Get a contact's current tier by fuzzy name match.
    Returns plain text that Siri reads aloud.
    """
    contact, ambiguous = _fuzzy_find_contact(name)

    if ambiguous:
        names = " or ".join(c["name"] for c in ambiguous)
        return f"Did you mean {names}? Be more specific."

    if not contact:
        return "Contact not found. Try a different name."

    tier = contact.get("tier") or "normal"
    display = "VIP" if tier == "vip" else tier.capitalize()
    return f"{contact['name']} is on {display} tier."


@app.get("/siri/vip", response_class=PlainTextResponse)
def siri_list_vip():
    conn = get_connection()
    rows = conn.execute(
        "SELECT name FROM contacts WHERE tier = 'vip' ORDER BY name COLLATE NOCASE"
    ).fetchall()
    conn.close()
    if not rows:
        return "You have no VIP contacts."
    return "\n".join(r["name"] for r in rows)


@app.get("/siri/important", response_class=PlainTextResponse)
def siri_list_important():
    conn = get_connection()
    rows = conn.execute(
        "SELECT name FROM contacts WHERE tier = 'important' ORDER BY name COLLATE NOCASE"
    ).fetchall()
    conn.close()
    if not rows:
        return "You have no Important contacts."
    return "\n".join(r["name"] for r in rows)


# ---------------------------------------------------------------------------
# Calls log (Phase 2 stub used by Shortcut 3)
# ---------------------------------------------------------------------------

class CallLog(BaseModel):
    contact_name: str
    vibe: Optional[str] = None
    followup_needed: Optional[bool] = False
    note: Optional[str] = None


@app.post("/calls/log")
def log_call(payload: CallLog):
    contact, ambiguous = _fuzzy_find_contact(payload.contact_name)

    if ambiguous and not contact:
        contact = ambiguous[0]  # best match — good enough for a post-call log
    if not contact:
        contact = get_or_create_contact(name=payload.contact_name)

    conn = get_connection()
    conn.execute(
        "INSERT INTO call_logs (contact_id, vibe, followup_needed, note) VALUES (?, ?, ?, ?)",
        (contact["id"], payload.vibe, 1 if payload.followup_needed else 0, payload.note),
    )
    if payload.followup_needed:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        conn.execute(
            "INSERT INTO commitments (contact_id, description) VALUES (?, ?)",
            (contact["id"], f"Follow up after call on {today}"),
        )
    conn.commit()
    conn.close()
    return {"status": "ok", "contact": contact["name"], "vibe": payload.vibe}


# ---------------------------------------------------------------------------
# Contacts Web UI
# ---------------------------------------------------------------------------

CONTACTS_UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>MessageOS Contacts</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --bg: #0f0f13;
      --surface: #1a1a22;
      --surface2: #22222e;
      --border: #2e2e3e;
      --text: #f0f0f5;
      --text-muted: #8888aa;
      --vip: #f5c518;
      --vip-bg: #2a2408;
      --important: #4da6ff;
      --important-bg: #0a1828;
      --normal: #66cc88;
      --normal-bg: #0a1f12;
      --radius: 14px;
      --radius-sm: 8px;
    }

    body {
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif;
      min-height: 100vh;
      padding: env(safe-area-inset-top, 0) env(safe-area-inset-right, 0)
               env(safe-area-inset-bottom, 0) env(safe-area-inset-left, 0);
    }

    header {
      position: sticky;
      top: 0;
      background: var(--bg);
      border-bottom: 1px solid var(--border);
      padding: 16px 20px 12px;
      z-index: 100;
    }

    header h1 {
      font-size: 22px;
      font-weight: 700;
      letter-spacing: -0.3px;
      margin-bottom: 12px;
    }

    header h1 span { color: var(--vip); }

    #search {
      width: 100%;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      color: var(--text);
      font-size: 16px;
      padding: 10px 14px;
      outline: none;
      -webkit-appearance: none;
      appearance: none;
    }

    #search:focus { border-color: var(--important); }
    #search::placeholder { color: var(--text-muted); }

    #stats {
      padding: 10px 20px 4px;
      font-size: 13px;
      color: var(--text-muted);
    }

    #contact-list {
      padding: 8px 16px 40px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }

    .contact-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 14px 16px;
      transition: opacity 0.15s;
    }

    .contact-card.updating { opacity: 0.5; pointer-events: none; }

    .card-top {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 10px;
    }

    .avatar {
      width: 42px;
      height: 42px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 18px;
      font-weight: 700;
      flex-shrink: 0;
    }

    .avatar.vip      { background: var(--vip-bg);       color: var(--vip); }
    .avatar.important{ background: var(--important-bg);  color: var(--important); }
    .avatar.normal   { background: var(--normal-bg);     color: var(--normal); }

    .contact-info { flex: 1; min-width: 0; }

    .contact-name {
      font-size: 16px;
      font-weight: 600;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .contact-meta {
      font-size: 12px;
      color: var(--text-muted);
      margin-top: 2px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .tier-badge {
      font-size: 11px;
      font-weight: 700;
      padding: 3px 8px;
      border-radius: 20px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      flex-shrink: 0;
    }

    .tier-badge.vip       { background: var(--vip-bg);       color: var(--vip); }
    .tier-badge.important { background: var(--important-bg);  color: var(--important); }
    .tier-badge.normal    { background: var(--normal-bg);     color: var(--normal); }

    .tier-buttons {
      display: flex;
      gap: 8px;
    }

    .tier-btn {
      flex: 1;
      padding: 9px 6px;
      border: 1.5px solid var(--border);
      border-radius: var(--radius-sm);
      background: var(--surface2);
      color: var(--text-muted);
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      text-align: center;
      -webkit-tap-highlight-color: transparent;
      transition: all 0.12s;
      user-select: none;
    }

    .tier-btn:active { transform: scale(0.96); }

    .tier-btn.active-vip {
      background: var(--vip-bg);
      color: var(--vip);
      border-color: var(--vip);
    }

    .tier-btn.active-important {
      background: var(--important-bg);
      color: var(--important);
      border-color: var(--important);
    }

    .tier-btn.active-normal {
      background: var(--normal-bg);
      color: var(--normal);
      border-color: var(--normal);
    }

    .empty-state {
      text-align: center;
      color: var(--text-muted);
      padding: 60px 20px;
      font-size: 15px;
    }

    .toast {
      position: fixed;
      bottom: calc(24px + env(safe-area-inset-bottom, 0));
      left: 50%;
      transform: translateX(-50%) translateY(20px);
      background: #333344;
      color: var(--text);
      padding: 10px 20px;
      border-radius: 24px;
      font-size: 14px;
      font-weight: 500;
      opacity: 0;
      transition: all 0.25s;
      pointer-events: none;
      white-space: nowrap;
      z-index: 999;
    }

    .toast.show {
      opacity: 1;
      transform: translateX(-50%) translateY(0);
    }
  </style>
</head>
<body>
  <header>
    <h1>Message<span>OS</span> Contacts</h1>
    <input id="search" type="search" placeholder="Search contacts..." autocomplete="off" autocorrect="off" spellcheck="false">
  </header>

  <div id="stats"></div>
  <div id="contact-list"><div class="empty-state">Loading...</div></div>
  <div id="toast" class="toast"></div>

  <script>
    let allContacts = [];

    function initials(name) {
      return name.split(' ').slice(0, 2).map(w => w[0] || '').join('').toUpperCase() || '?';
    }

    function metaLine(c) {
      const parts = [];
      if (c.phone) parts.push(c.phone);
      if (c.email) parts.push(c.email);
      return parts.join(' · ') || 'No contact info';
    }

    function renderCard(c) {
      const tier = c.tier || 'normal';
      return `
        <div class="contact-card" id="card-${c.id}" data-name="${c.name.toLowerCase()}" data-tier="${tier}">
          <div class="card-top">
            <div class="avatar ${tier}">${initials(c.name)}</div>
            <div class="contact-info">
              <div class="contact-name">${escHtml(c.name)}</div>
              <div class="contact-meta">${escHtml(metaLine(c))}</div>
            </div>
            <span class="tier-badge ${tier}">${tier === 'vip' ? 'VIP' : tier.charAt(0).toUpperCase() + tier.slice(1)}</span>
          </div>
          <div class="tier-buttons">
            <button class="tier-btn ${tier === 'vip' ? 'active-vip' : ''}"
                    onclick="setTier(${c.id}, 'vip', this)">VIP</button>
            <button class="tier-btn ${tier === 'important' ? 'active-important' : ''}"
                    onclick="setTier(${c.id}, 'important', this)">Important</button>
            <button class="tier-btn ${tier === 'normal' ? 'active-normal' : ''}"
                    onclick="setTier(${c.id}, 'normal', this)">Normal</button>
          </div>
        </div>`;
    }

    function escHtml(s) {
      return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    function renderList(contacts) {
      const el = document.getElementById('contact-list');
      if (!contacts.length) {
        el.innerHTML = '<div class="empty-state">No contacts found</div>';
        return;
      }
      el.innerHTML = contacts.map(renderCard).join('');
    }

    function updateStats(filtered, total) {
      const el = document.getElementById('stats');
      const vip = allContacts.filter(c => c.tier === 'vip').length;
      const imp = allContacts.filter(c => c.tier === 'important').length;
      if (filtered === total) {
        el.textContent = `${total} contacts · ${vip} VIP · ${imp} Important`;
      } else {
        el.textContent = `${filtered} of ${total} contacts`;
      }
    }

    async function loadContacts() {
      try {
        const res = await fetch('/contacts/list');
        allContacts = await res.json();
        renderList(allContacts);
        updateStats(allContacts.length, allContacts.length);
      } catch(e) {
        document.getElementById('contact-list').innerHTML =
          '<div class="empty-state">Failed to load contacts</div>';
      }
    }

    document.getElementById('search').addEventListener('input', function() {
      const q = this.value.trim().toLowerCase();
      const filtered = q ? allContacts.filter(c => c.name.toLowerCase().includes(q)) : allContacts;
      renderList(filtered);
      updateStats(filtered.length, allContacts.length);
    });

    async function setTier(id, tier, btn) {
      const card = document.getElementById('card-' + id);
      card.classList.add('updating');

      try {
        const res = await fetch('/contacts/' + id + '/tier', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({tier})
        });
        if (!res.ok) throw new Error('Server error');
        const data = await res.json();

        // Update local data
        const contact = allContacts.find(c => c.id === id);
        if (contact) contact.tier = tier;

        // Re-render the card in place
        const tmp = document.createElement('div');
        tmp.innerHTML = renderCard({...contact, tier});
        const newCard = tmp.firstElementChild;
        card.replaceWith(newCard);

        showToast(`${data.name} moved to ${tier === 'vip' ? 'VIP' : tier.charAt(0).toUpperCase() + tier.slice(1)}`);
      } catch(e) {
        card.classList.remove('updating');
        showToast('Failed to update tier');
      }
    }

    let toastTimer;
    function showToast(msg) {
      const t = document.getElementById('toast');
      t.textContent = msg;
      t.classList.add('show');
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => t.classList.remove('show'), 2200);
    }

    loadContacts();
  </script>
</body>
</html>"""


@app.get("/contacts-ui", response_class=HTMLResponse)
def contacts_ui():
    return HTMLResponse(content=CONTACTS_UI_HTML)
