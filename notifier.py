import os
import requests
import logging

logger = logging.getLogger(__name__)

NTFY_URL = os.getenv("NTFY_URL", "https://ntfy.sh/MessageOS-Rishabh")
NTFY_TOKEN = os.getenv("NTFY_TOKEN", "")


def send_notification(title: str, message: str, priority: str = "default", tags: list = None):
    """
    Send a push notification via ntfy.sh.
    priority: urgent | high | default | low | min
    'urgent' bypasses iOS Focus / DND modes.
    """
    # HTTP headers must be latin-1; encode unicode chars as percent-encoded UTF-8
    import urllib.parse
    safe_title = urllib.parse.quote(title, safe=" :,!-")
    headers = {
        "Title": safe_title,
        "Priority": priority,
        "Content-Type": "text/plain; charset=utf-8",
    }
    if NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"
    if tags:
        headers["Tags"] = ",".join(tags)

    try:
        resp = requests.post(NTFY_URL, data=message.encode("utf-8"), headers=headers, timeout=10)
        resp.raise_for_status()
        logger.info(f"[NTFY] Sent '{title}' (priority={priority})")
        return True
    except Exception as e:
        logger.error(f"[NTFY] Failed to send notification: {e}")
        return False


def alert_vip_received(name: str, snippet: str = ""):
    send_notification(
        title=f"VIP: {name} messaged you",
        message=f"{name} messaged you - VIP\n{snippet}".strip(),
        priority="urgent",
        tags=["bell", "vip"],
    )


def alert_sla_escalation(name: str, elapsed_minutes: int):
    send_notification(
        title=f"VIP SLA: {name}",
        message=f"{name} messaged {elapsed_minutes} minutes ago - reply needed",
        priority="urgent",
        tags=["warning", "clock"],
    )


def alert_important_received(name: str, snippet: str = ""):
    send_notification(
        title=f"Message from {name}",
        message=f"{name} messaged you - Important\n{snippet}".strip(),
        priority="high",
        tags=["bell"],
    )
