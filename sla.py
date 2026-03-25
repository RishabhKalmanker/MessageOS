import threading
import time
import logging
from datetime import datetime, timezone
from database import get_connection
from notifier import alert_sla_escalation

logger = logging.getLogger(__name__)

# SLA thresholds in minutes
SLA_VIP_WARN_MINUTES = 18
SLA_VIP_BREACH_MINUTES = 20
SLA_IMPORTANT_WARN_MINUTES = 110   # warn at 110 min
SLA_IMPORTANT_BREACH_MINUTES = 120  # breach at 2 hours

WATCHDOG_INTERVAL_SECONDS = 60

_stop_event = threading.Event()


def _elapsed_minutes(clock: dict) -> float:
    """
    Calculate effective elapsed minutes, subtracting any accumulated paused duration.
    """
    started = datetime.fromisoformat(clock["started_at"]).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    total_seconds = (now - started).total_seconds()

    paused_seconds = clock.get("paused_duration_seconds") or 0

    # If currently paused, don't count time since paused_at
    if clock.get("paused_at"):
        paused_since = datetime.fromisoformat(clock["paused_at"]).replace(tzinfo=timezone.utc)
        paused_seconds += (now - paused_since).total_seconds()

    effective_seconds = total_seconds - paused_seconds
    return effective_seconds / 60


def open_sla_clock(message_id: int, contact_id: int) -> int:
    """Create a new SLA clock entry. Returns clock id."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO sla_clocks (message_id, contact_id, started_at)
           VALUES (?, ?, datetime('now'))""",
        (message_id, contact_id),
    )
    conn.commit()
    clock_id = cur.lastrowid
    conn.close()
    logger.info(f"[SLA] Opened clock id={clock_id} for message_id={message_id} contact_id={contact_id}")
    return clock_id


def close_sla_clocks_for_contact(contact_id: int):
    """Close all open SLA clocks for a contact when they receive a reply."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """UPDATE sla_clocks
           SET closed_at = datetime('now')
           WHERE contact_id = ? AND closed_at IS NULL""",
        (contact_id,),
    )
    closed = cur.rowcount
    conn.commit()
    conn.close()
    if closed:
        logger.info(f"[SLA] Closed {closed} clock(s) for contact_id={contact_id}")


def _watchdog_tick():
    conn = get_connection()
    cur = conn.cursor()

    # Fetch all open (un-closed) SLA clocks with contact info
    cur.execute("""
        SELECT sc.*, c.name, c.tier, c.phone, c.email
        FROM sla_clocks sc
        JOIN contacts c ON c.id = sc.contact_id
        WHERE sc.closed_at IS NULL
    """)
    clocks = [dict(row) for row in cur.fetchall()]
    conn.close()

    for clock in clocks:
        elapsed = _elapsed_minutes(clock)
        tier = (clock.get("tier") or "normal").lower()

        # Determine thresholds based on tier
        if tier == "vip":
            warn_threshold = SLA_VIP_WARN_MINUTES
            breach_threshold = SLA_VIP_BREACH_MINUTES
        elif tier == "important":
            warn_threshold = SLA_IMPORTANT_WARN_MINUTES
            breach_threshold = SLA_IMPORTANT_BREACH_MINUTES
        else:
            continue  # Normal contacts: no SLA enforcement

        name = clock["name"]
        clock_id = clock["id"]

        # Fire escalation alert once when warn threshold is crossed
        if elapsed >= warn_threshold and not clock["escalation_sent"]:
            logger.info(f"[SLA] Escalation for {name} — {elapsed:.1f} min elapsed")
            alert_sla_escalation(
                name,
                int(elapsed),
                contact_id=clock["contact_id"],
                message_id=clock["message_id"] or 0,
            )
            _mark_escalation_sent(clock_id)

        # Mark breached when breach threshold is crossed
        if elapsed >= breach_threshold and not clock["breached"]:
            logger.warning(f"[SLA] BREACH for {name} — {elapsed:.1f} min elapsed")
            _mark_breached(clock_id)


def _mark_escalation_sent(clock_id: int):
    conn = get_connection()
    conn.execute(
        "UPDATE sla_clocks SET escalation_sent = 1 WHERE id = ?", (clock_id,)
    )
    conn.commit()
    conn.close()


def _mark_breached(clock_id: int):
    conn = get_connection()
    conn.execute(
        "UPDATE sla_clocks SET breached = 1 WHERE id = ?", (clock_id,)
    )
    # Also mark the linked message as breached
    cur = conn.cursor()
    cur.execute("SELECT message_id FROM sla_clocks WHERE id = ?", (clock_id,))
    row = cur.fetchone()
    if row and row["message_id"]:
        conn.execute(
            "UPDATE messages SET sla_breached = 1 WHERE id = ?", (row["message_id"],)
        )
    conn.commit()
    conn.close()


def watchdog_loop():
    logger.info(f"[SLA] Watchdog started — tick every {WATCHDOG_INTERVAL_SECONDS}s")
    while not _stop_event.is_set():
        try:
            _watchdog_tick()
        except Exception as e:
            logger.error(f"[SLA] Watchdog error: {e}")
        _stop_event.wait(timeout=WATCHDOG_INTERVAL_SECONDS)
    logger.info("[SLA] Watchdog stopped")


def start_watchdog() -> threading.Thread:
    t = threading.Thread(target=watchdog_loop, daemon=True, name="sla-watchdog")
    t.start()
    return t


def stop_watchdog():
    _stop_event.set()
