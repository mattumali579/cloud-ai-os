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


def _connect(settings, context: ssl.SSLContext):
    """Open an SMTP session using the style the port implies.

    Port 465 is implicit TLS (SMTP_SSL). Port 587 is plaintext-then-STARTTLS.
    Hostinger offers both; Gmail and most other hosts only offer 587, so
    assuming 465 silently broke every non-Hostinger mailbox.
    """
    host = settings.hostinger_smtp_host
    port = int(settings.hostinger_smtp_port)
    if port == 465:
        return smtplib.SMTP_SSL(host, port, context=context, timeout=30)
    smtp = smtplib.SMTP(host, port, timeout=30)
    try:
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.ehlo()
    except Exception:
        smtp.close()
        raise
    return smtp


def _fenced_blocks(text: str) -> list[str]:
    """Every fenced code block body, longest first, plus any bare JSON object."""
    blocks = re.findall(r"```[^\n`]*\n(.*?)```", text, re.DOTALL)
    blocks = [b.strip() for b in blocks if b.strip()]
    # A model sometimes answers with a bare object and no fence at all.
    depth = 0
    start = -1
    for index, char in enumerate(text):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                blocks.append(text[start : index + 1])
                start = -1
    return sorted(set(blocks), key=len, reverse=True)


def _repair_json(block: str) -> str:
    """Escape raw newlines that appear inside a JSON string literal.

    Email bodies are multi-line by nature, and a model writing the draft will
    sometimes press enter inside the "text" value instead of writing an escape.
    That is invalid JSON but unambiguous, so repair it rather than discarding a
    campaign the owner already reviewed. Anything outside a string is untouched.
    """
    out = []
    in_string = False
    escaped = False
    for char in block:
        if escaped:
            out.append(char)
            escaped = False
            continue
        if char == chr(92):
            out.append(char)
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            out.append(char)
            continue
        if in_string and char in (chr(10), chr(13)):
            if char == chr(10):
                out.append('\\n')
            continue
        out.append(char)
    return "".join(out)


def extract_messages(text: str) -> list[dict]:
    """Pull the machine-readable draft out of an employee's written answer.

    The email-marketer employee is told to emit a JSON object with a "messages"
    list. Prose wraps it, so scan candidate blocks rather than parsing the whole
    reply. Raises if no usable block is present so the caller never guesses.
    """
    for block in _fenced_blocks(text or ""):
        try:
            data = json.loads(block)
        except ValueError:
            try:
                data = json.loads(_repair_json(block))
            except ValueError:
                continue
        rows = data.get("messages") if isinstance(data, dict) else None
        if isinstance(rows, list) and rows:
            return rows
    raise CloudOSError(
        ErrorCode.VALIDATION_ERROR,
        "no EMAIL_DRAFT JSON block found in that reply; ask the email marketer for the recipients",
    )


def create_draft(draft_id: str, messages: list) -> dict:
    """Write a pending draft, then re-read it through the same validator.

    Writing and validating are deliberately separate: the file on disk is the
    only thing send() trusts, so the preview must come from re-reading it, not
    from the in-memory list that produced it.
    """
    path = _draft_path(draft_id)
    if not isinstance(messages, list) or not messages:
        raise CloudOSError(ErrorCode.VALIDATION_ERROR, "a draft needs at least one message")
    if len(messages) > MAX_EMAILS_PER_DRAFT:
        raise CloudOSError(
            ErrorCode.VALIDATION_ERROR,
            f"{len(messages)} recipients exceeds the {MAX_EMAILS_PER_DRAFT}-recipient cap",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"messages": messages}, indent=2), encoding="utf-8")
    try:
        return preview(draft_id)
    except CloudOSError:
        path.unlink(missing_ok=True)
        raise


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
    from_address = settings.email_from_address or settings.hostinger_smtp_username
    try:
        with _connect(settings, context) as smtp:
            smtp.login(settings.hostinger_smtp_username, settings.hostinger_smtp_password)
            for item in messages:
                email = EmailMessage()
                sender = formataddr((settings.email_from_name, from_address))
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
