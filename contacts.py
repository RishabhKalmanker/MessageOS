import os
import logging
from database import get_connection

logger = logging.getLogger(__name__)


def _normalize_phone(phone: str) -> str:
    """Strip spaces/dashes/parens for consistent comparison."""
    if not phone:
        return ""
    return "".join(c for c in phone if c.isdigit() or c == "+")


def _get_vip_phones() -> set:
    raw = os.getenv("VIP_PHONES", "")
    return {_normalize_phone(p) for p in raw.split(",") if p.strip()}


def _get_vip_emails() -> set:
    raw = os.getenv("VIP_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _get_important_phones() -> set:
    raw = os.getenv("IMPORTANT_PHONES", "")
    return {_normalize_phone(p) for p in raw.split(",") if p.strip()}


def _get_important_emails() -> set:
    raw = os.getenv("IMPORTANT_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def resolve_tier_from_env(phone: str = None, email: str = None) -> str:
    """Determine tier purely from environment variable lists."""
    norm_phone = _normalize_phone(phone) if phone else ""
    norm_email = email.strip().lower() if email else ""

    if norm_phone and norm_phone in _get_vip_phones():
        return "vip"
    if norm_email and norm_email in _get_vip_emails():
        return "vip"
    if norm_phone and norm_phone in _get_important_phones():
        return "important"
    if norm_email and norm_email in _get_important_emails():
        return "important"
    return "normal"


def get_or_create_contact(name: str, phone: str = None, email: str = None) -> dict:
    """
    Look up contact by phone or email. If not found, create with name.
    Returns contact row as dict.
    """
    conn = get_connection()
    cur = conn.cursor()

    contact = None

    if phone:
        norm = _normalize_phone(phone)
        cur.execute("SELECT * FROM contacts WHERE phone = %s", (norm,))
        contact = cur.fetchone()

    if not contact and email:
        cur.execute("SELECT * FROM contacts WHERE email = %s", (email.strip().lower(),))
        contact = cur.fetchone()

    if contact:
        conn.close()
        return dict(contact)

    tier = resolve_tier_from_env(phone, email)
    norm_phone = _normalize_phone(phone) if phone else None
    norm_email = email.strip().lower() if email else None

    cur.execute(
        """INSERT INTO contacts (name, phone, email, tier)
           VALUES (%s, %s, %s, %s)
           RETURNING *""",
        (name or "Unknown", norm_phone, norm_email, tier),
    )
    conn.commit()
    contact = dict(cur.fetchone())
    conn.close()
    logger.info(f"[CONTACTS] Created new contact: {contact['name']} (tier={tier})")
    return contact


def get_contact_by_id(contact_id: int) -> dict | None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM contacts WHERE id = %s", (contact_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def effective_tier(contact: dict) -> str:
    """
    Returns the effective tier for a contact, preferring env-list matches
    over whatever is stored in DB (in case VIP_PHONES was updated).
    """
    env_tier = resolve_tier_from_env(contact.get("phone"), contact.get("email"))
    if env_tier != "normal":
        return env_tier
    return (contact.get("tier") or "normal").lower()
