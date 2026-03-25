import os
import urllib.parse
import requests
import logging

logger = logging.getLogger(__name__)

NTFY_URL = os.getenv("NTFY_URL", "https://ntfy.sh/MessageOS-Rishabh")
NTFY_TOKEN = os.getenv("NTFY_TOKEN", "")


def send_notification(
    title: str,
    message: str,
    priority: str = "default",
    tags: list = None,
    click_url: str = None,
):
    """
    Send a push notification via ntfy.sh.
    priority: urgent | high | default | low | min
    'urgent' bypasses iOS Focus / DND modes.
    click_url: tapping the notification opens this URL (e.g. imessage://, message://)
    """
    # HTTP headers must be latin-1 safe; percent-encode any unicode in the title
    safe_title = urllib.parse.quote(title, safe=" :,!-()'")
    headers = {
        "Title": safe_title,
        "Priority": priority,
        "Content-Type": "text/plain; charset=utf-8",
    }
    if NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"
    if tags:
        headers["Tags"] = ",".join(tags)
    if click_url:
        headers["Click"] = click_url

    try:
        resp = requests.post(
            NTFY_URL, data=message.encode("utf-8"), headers=headers, timeout=10
        )
        resp.raise_for_status()
        logger.info(f"[NTFY] Sent '{title}' (priority={priority})")
        return True
    except Exception as e:
        logger.error(f"[NTFY] Failed to send notification: {e}")
        return False


def alert_vip_received(name: str, snippet: str = ""):
    """Instant urgent alert when a VIP sends an iMessage. Tapping opens Messages."""
    send_notification(
        title=f"VIP: {name} messaged you",
        message=f"{name} messaged you - VIP\n{snippet}".strip(),
        priority="urgent",
        tags=["bell", "vip"],
        click_url="imessage://",
    )


def alert_vip_email_received(name: str, snippet: str = ""):
    """Instant urgent alert when a VIP sends an email. Tapping opens Mail."""
    send_notification(
        title=f"VIP email: {name}",
        message=f"{name} emailed you - VIP\n{snippet}".strip(),
        priority="urgent",
        tags=["email", "vip"],
        click_url="message://",
    )


def alert_sla_escalation(name: str, elapsed_minutes: int, source: str = "imessage"):
    """Escalation alert when VIP SLA threshold is crossed. Tapping opens the right app."""
    click_url = "message://" if source == "email" else "imessage://"
    send_notification(
        title=f"VIP SLA: {name}",
        message=f"{name} messaged {elapsed_minutes} minutes ago - reply needed",
        priority="urgent",
        tags=["warning", "clock"],
        click_url=click_url,
    )


def alert_important_received(name: str, snippet: str = "", source: str = "imessage"):
    """Soft alert for Important contacts. Tapping opens the right app."""
    click_url = "message://" if source == "email" else "imessage://"
    send_notification(
        title=f"Message from {name}",
        message=f"{name} messaged you - Important\n{snippet}".strip(),
        priority="high",
        tags=["bell"],
        click_url=click_url,
    )


def alert_soft(title: str, message: str):
    """Generic soft alert with no deep link."""
    send_notification(title=title, message=message, priority="default")
