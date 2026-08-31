"""Push an important email to Discord.

One Discord embed per message. The embed carries the sender, why the filter
thought it mattered, and the first line of the body -- enough to decide from
the lock screen whether to open the mailbox.

Two delivery paths, in priority order:

1. The already-deployed Cloudflare Worker's authenticated ``/notify`` relay
   (``CLOUDOS_NOTIFY_URL`` + ``CLOUDOS_NOTIFY_TOKEN``). Preferred, because the
   Discord webhook then exists in exactly one place -- the Worker -- instead of
   being copied into every runtime that wants to send something.
2. A direct ``DISCORD_WEBHOOK_URL``, for running this locally or if the Worker
   is ever retired.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Optional

COLOR = {
    "high": 15158332,    # red
    "medium": 15844367,  # amber
    "normal": 3447003,   # blue
}
ICON = {
    "money": "\N{MONEY BAG}",
    "security": "\N{LOCK}",
    "school": "\N{GRADUATION CAP}",
    "job": "\N{BRIEFCASE}",
    "automation": "\N{WARNING SIGN}",
    "deadline": "\N{ALARM CLOCK}",
    "general": "\N{ENVELOPE}",
}
TIMEOUT = 20


def resolve_destination(webhook_url: str = "") -> tuple:
    """Return (url, headers, kind) for wherever this run should send."""
    relay = os.environ.get("CLOUDOS_NOTIFY_URL", "").strip()
    token = os.environ.get("CLOUDOS_NOTIFY_TOKEN", "").strip()
    headers = {"Content-Type": "application/json",
               "User-Agent": "cloudos-email-alerts/1.0"}
    if relay and token:
        headers["Authorization"] = f"Bearer {token}"
        return relay, headers, "worker-relay"
    return webhook_url, headers, "direct-webhook"


def _post(webhook_url: str, payload: dict) -> int:
    url, headers, _kind = resolve_destination(webhook_url)
    if not url:
        raise RuntimeError("no Discord destination configured")
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.status


def send_email_alert(webhook_url: str, message, classification) -> bool:
    """Deliver one alert. Returns True on 2xx; never raises."""
    icon = ICON.get(classification.label, ICON["general"])
    title = (message.subject or "(no subject)")[:240]
    why = "; ".join(classification.reasons[:3]) or "matched importance rules"
    body = (message.snippet or "")[:400]

    payload = {
        "embeds": [
            {
                "title": f"{icon} {title}",
                "description": body or "(no preview available)",
                "color": COLOR.get(classification.urgency, COLOR["normal"]),
                "fields": [
                    {"name": "From", "value": (message.sender or "unknown")[:200],
                     "inline": False},
                    {"name": "Why you're seeing this", "value": why[:200],
                     "inline": False},
                ],
                "footer": {"text": f"important email - {classification.label} - "
                                   f"score {classification.score}"},
            }
        ]
    }
    try:
        return 200 <= _post(webhook_url, payload) < 300
    except urllib.error.HTTPError as exc:
        print(f"discord rejected the alert: HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001 - delivery must never crash the run
        print(f"discord delivery failed: {type(exc).__name__}")
    return False


def send_plain(webhook_url: str, title: str, message: str,
               color: Optional[int] = None) -> bool:
    """Operational message (startup probe, failure notice). Never raises."""
    payload = {"embeds": [{"title": title[:240], "description": message[:1800],
                           "color": color or COLOR["normal"]}]}
    try:
        return 200 <= _post(webhook_url, payload) < 300
    except Exception as exc:  # noqa: BLE001
        print(f"discord delivery failed: {type(exc).__name__}")
        return False
