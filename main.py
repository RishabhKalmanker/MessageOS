"""
MessageOS — Phase 1: SLA Core
FastAPI backend for personal message intelligence.
"""

import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from database import init_db, get_connection
from contacts import get_or_create_contact, effective_tier
from sla import open_sla_clock, close_sla_clocks_for_contact, start_watchdog, stop_watchdog
from notifier import alert_vip_received, alert_important_received

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic request schemas
# ---------------------------------------------------------------------------

class IMessageReceived(BaseModel):
    sender: str
    phone: str | None = None
    body: str | None = None
    timestamp: str | None = None


class IMessageSent(BaseModel):
    recipient: str | None = None
    phone: str | None = None
    timestamp: str | None = None


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[STARTUP] Initializing MessageOS Phase 1")
    init_db()
    watchdog_thread = start_watchdog()
    yield
    logger.info("[SHUTDOWN] Stopping SLA watchdog")
    stop_watchdog()
    watchdog_thread.join(timeout=5)


app = FastAPI(title="MessageOS", version="1.0.0", lifespan=lifespan)


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
    """
    Receives an incoming iMessage from the iOS Shortcut.
    Identifies VIP/Important contacts and starts SLA clocks accordingly.
    """
    logger.info(f"[WEBHOOK] Received from '{payload.sender}' phone='{payload.phone}'")

    # Resolve or create contact
    contact = get_or_create_contact(
        name=payload.sender,
        phone=payload.phone,
    )
    contact_id = contact["id"]
    tier = effective_tier(contact)

    # Insert message record
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

    # Update last_contacted
    conn.execute(
        "UPDATE contacts SET last_contacted = datetime('now') WHERE id = ?",
        (contact_id,),
    )
    conn.commit()
    conn.close()

    snippet = (payload.body or "")[:80]

    # VIP — instant alert + SLA clock
    if tier == "vip":
        alert_vip_received(contact["name"], snippet)
        open_sla_clock(message_id, contact_id)
        logger.info(f"[WEBHOOK] VIP contact {contact['name']} — SLA clock started")

    # Important — soft alert + SLA clock (2-hour threshold handled in watchdog)
    elif tier == "important":
        alert_important_received(contact["name"], snippet)
        open_sla_clock(message_id, contact_id)
        logger.info(f"[WEBHOOK] Important contact {contact['name']} — SLA clock started")

    else:
        logger.info(f"[WEBHOOK] Normal contact {contact['name']} — logged only")

    return {
        "status": "ok",
        "contact": contact["name"],
        "tier": tier,
        "message_id": message_id,
    }


@app.post("/webhook/imessage-sent")
def imessage_sent(payload: IMessageSent):
    """
    Receives an outbound iMessage from the iOS Shortcut.
    Resets (closes) SLA clocks for the recipient.
    """
    recipient = payload.recipient or "Unknown"
    phone = payload.phone or payload.recipient  # fallback: recipient field may be phone
    logger.info(f"[WEBHOOK] Sent to '{recipient}'")

    contact = get_or_create_contact(name=recipient, phone=phone)
    contact_id = contact["id"]

    # Insert outbound message record
    conn = get_connection()
    conn.execute(
        """INSERT INTO messages (contact_id, source, direction, body, received_at)
           VALUES (?, 'imessage', 'outbound', '', datetime('now'))""",
        (contact_id,),
    )
    conn.execute(
        "UPDATE contacts SET last_contacted = datetime('now') WHERE id = ?",
        (contact_id,),
    )
    conn.commit()
    conn.close()

    # Close any open SLA clocks for this contact
    close_sla_clocks_for_contact(contact_id)

    return {"status": "ok", "contact": contact["name"], "sla_clocks_closed": True}
