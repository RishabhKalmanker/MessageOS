import os
import json
import requests
import logging

logger = logging.getLogger(__name__)

NTFY_URL = os.getenv("NTFY_URL", "https://ntfy.sh/MessageOS-Rishabh")
NTFY_TOKEN = os.getenv("NTFY_TOKEN", "")


def _sla_id(contact_id: int, message_id: int) -> str:
    """
    Stable notification ID shared by the initial VIP alert and its escalation
    for the same message. Ensures the 18-min escalation replaces the original
    notification instead of stacking a second one on top of it.
    Alerts for different messages/contacts always have distinct IDs.
    """
    return f"sla-{contact_id}-{message_id}"


def send_notification(
    title: str,
    message: str,
    priority: str = "default",
    tags: list = None,
    click_url: str = None,
    notif_id: str = None,
):
    """
    Send a push notification via ntfy.sh using JSON body.

    Using JSON avoids all HTTP header latin-1 encoding constraints, so titles
    can contain emoji, em dashes, and any other unicode freely.

    priority : urgent | high | default | low | min
               'urgent' bypasses iOS Focus / DND modes.
    click_url: tapping the notification opens this URL.
    notif_id : when two alerts share the same notif_id the second one REPLACES
               the first on-device rather than stacking alongside it.
    """
    topic = NTFY_URL.rstrip("/").split("/")[-1]
    base_url = NTFY_URL.rstrip("/").rsplit("/", 1)[0]
    endpoint = f"{base_url}/{topic}"

    payload: dict = {
        "topic": topic,
        "title": title,
        "message": message,
        "priority": priority,
    }
    if tags:
        payload["tags"] = tags
    if click_url:
        payload["click"] = click_url
    if notif_id:
        payload["id"] = notif_id

    headers = {"Content-Type": "application/json"}
    if NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"

    try:
        resp = requests.post(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        logger.info(f"[NTFY] Sent '{title}' (priority={priority}, id={notif_id})")
        return True
    except Exception as e:
        logger.error(f"[NTFY] Failed to send notification: {e}")
        return False


# ---------------------------------------------------------------------------
# Alert helpers — all accept contact_id + message_id so every notification
# for the same conversation carries the same notif_id. The initial VIP alert
# and the 18-min escalation are linked by sharing that ID, so the escalation
# silently replaces the earlier notification instead of adding a new one.
# ---------------------------------------------------------------------------

def alert_vip_received(
    name: str, snippet: str = "", contact_id: int = 0, message_id: int = 0
):
    """Instant urgent alert when a VIP sends an iMessage. Tapping opens Messages."""
    body = snippet[:100].strip() if snippet else f"{name} is waiting for a reply."
    send_notification(
        title=f"\U0001f534 {name} \u2014 VIP",
        message=body,
        priority="urgent",
        tags=["bell"],
        click_url="imessage://",
        notif_id=_sla_id(contact_id, message_id),
    )


def alert_vip_email_received(
    name: str, snippet: str = "", contact_id: int = 0, message_id: int = 0
):
    """Instant urgent alert when a VIP sends an email. Tapping opens Mail."""
    body = snippet[:100].strip() if snippet else f"{name} emailed you."
    send_notification(
        title=f"\U0001f534 {name} \u2014 VIP email",
        message=body,
        priority="urgent",
        tags=["email"],
        click_url="message://",
        notif_id=_sla_id(contact_id, message_id),
    )


def alert_sla_escalation(
    name: str,
    elapsed_minutes: int,
    contact_id: int = 0,
    message_id: int = 0,
    source: str = "imessage",
    snippet: str = "",
):
    """
    Escalation alert at 18 min. Uses the SAME notif_id as the initial VIP alert
    for this message, so it replaces that notification on-device rather than
    appearing as a second unread alert.
    """
    click_url = "message://" if source == "email" else "imessage://"
    body_prefix = f"{elapsed_minutes} min elapsed — reply needed."
    body = f"{body_prefix}\n{snippet[:100].strip()}" if snippet else body_prefix
    send_notification(
        title=f"\u26a0\ufe0f {name} \u2014 {elapsed_minutes} min elapsed",
        message=body,
        priority="urgent",
        tags=["warning"],
        click_url=click_url,
        notif_id=_sla_id(contact_id, message_id),
    )


def alert_important_received(
    name: str,
    snippet: str = "",
    contact_id: int = 0,
    message_id: int = 0,
    source: str = "imessage",
):
    """Soft alert for Important contacts. Tapping opens the right app."""
    click_url = "message://" if source == "email" else "imessage://"
    body = snippet[:100].strip() if snippet else f"Message from {name}."
    send_notification(
        title=f"{name} \u2014 Important",
        message=body,
        priority="high",
        tags=["bell"],
        click_url=click_url,
        notif_id=_sla_id(contact_id, message_id),
    )


def alert_soft(title: str, message: str):
    """Generic soft alert with no deep link and no deduplication ID."""
    send_notification(title=title, message=message, priority="default")
