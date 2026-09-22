"""Reviewable Hostinger SMTP outbox with a ten-recipient hard cap."""
from __future__ import annotations

import hashlib
import json
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from pathlib import Path

from cloudos.config import get_settings
from cloudos.contracts import CloudOSError, ErrorCode

MAX_EMAILS_PER_DRAFT = 10
_DRAFT_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


@dataclass(frozen=True)
class OutboxMessage:
    to: str
    subject: str
    text: str


def _draft_path(draft_id: str) -> Path:
    if not _DRAFT_ID.fullmatch(draft_id):
        raise CloudOSError(ErrorCode.VALIDATION_ERROR, "invalid email draft id")
    return Path(get_settings().email_outbox_path) / "pending" / f"{draft_id}.json"


def _load(draft_id: str) -> tuple[list[OutboxMessage], str]:
    path = _draft_path(draft_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise CloudOSError(ErrorCode.VALIDATION_ERROR, "email draft not found or invalid") from exc
    rows = raw.get("messages") if isinstance(raw, dict) else None
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_EMAILS_PER_DRAFT:
        raise CloudOSError(
            ErrorCode.VALIDATION_ERROR,
            f"email draft must contain 1-{MAX_EMAILS_PER_DRAFT} messages",
        )
    messages: list[OutboxMessage] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise CloudOSError(ErrorCode.VALIDATION_ERROR, "each email must be an object")
        to = str(row.get("to", "")).strip()
        _name, parsed = parseaddr(to)
        if parsed != to or "@" not in parsed or any(ch in to for ch in "\r\n"):
            raise CloudOSError(ErrorCode.VALIDATION_ERROR, "draft contains an invalid recipient")
        if to.casefold() in seen:
            raise CloudOSError(ErrorCode.VALIDATION_ERROR, "draft contains a duplicate recipient")
        seen.add(to.casefold())
        subject = str(row.get("subject", "")).strip()
        text = str(row.get("text", "")).strip()
        if not subject or not text or any(ch in subject for ch in "\r\n"):
            raise CloudOSError(ErrorCode.VALIDATION_ERROR, "every email needs a safe subject and body")
        messages.append(OutboxMessage(to=to, subject=subject[:200], text=text))
    canonical = json.dumps(
        [message.__dict__ for message in messages], sort_keys=True, separators=(",", ":")
    )
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return messages, fingerprint


def preview(draft_id: str) -> dict:
    messages, fingerprint = _load(draft_id)
    return {
        "draft_id": draft_id,
        "fingerprint": fingerprint,
        "count": len(messages),
        "messages": [{"to": m.to, "subject": m.subject, "text_preview": m.text[:240]} for m in messages],
    }


def send(draft_id: str, fingerprint: str) -> dict:
    settings = get_settings()
    messages, expected = _load(draft_id)
    if fingerprint.strip().lower() != expected:
        raise CloudOSError(ErrorCode.VALIDATION_ERROR, "email confirmation fingerprint does not match")
    if not settings.email_send_enabled:
        raise CloudOSError(
            ErrorCode.PAID_DISABLED,
            "email sending is disabled; set EMAIL_SEND_ENABLED=true after reviewing the draft",
        )
    if not settings.hostinger_smtp_username or not settings.hostinger_smtp_password:
        raise CloudOSError(ErrorCode.AUTH_REQUIRED, "Hostinger SMTP credentials are not configured")

    receipt_path = Path(settings.email_outbox_path) / "sent" / f"{draft_id}.{expected}.json"
    if receipt_path.exists():
        raise CloudOSError(ErrorCode.VALIDATION_ERROR, "this exact email draft was already attempted")

    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt = {
        "draft_id": draft_id,
        "fingerprint": expected,
        "status": "sending",
        "sent": [],
    }
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    context = ssl.create_default_context()
    sent: list[str] = []
    try:
        with smtplib.SMTP_SSL(
            settings.hostinger_smtp_host,
            settings.hostinger_smtp_port,
            context=context,
            timeout=30,
        ) as smtp:
            smtp.login(settings.hostinger_smtp_username, settings.hostinger_smtp_password)
            for item in messages:
                email = EmailMessage()
                sender = formataddr((settings.email_from_name, settings.hostinger_smtp_username))
                email["From"] = sender
                email["To"] = item.to
                email["Subject"] = item.subject
                email.set_content(item.text)
                smtp.send_message(email)
                sent.append(item.to)
                receipt["sent"] = list(sent)
                receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    except (OSError, smtplib.SMTPException) as exc:
        raise CloudOSError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Hostinger SMTP send failed",
            {"sent_before_failure": len(sent)},
        ) from exc

    receipt["status"] = "sent"
    receipt["sent"] = sent
    receipt_path.write_text(
        json.dumps(receipt, indent=2),
        encoding="utf-8",
    )
    return {"draft_id": draft_id, "fingerprint": expected, "sent_count": len(sent)}
