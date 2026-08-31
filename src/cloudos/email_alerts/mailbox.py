"""Read recent mail over IMAP. Stdlib only.

IMAP (not a provider REST API) is deliberate: it is the one mailbox protocol
that works the same for Gmail, Outlook.com, Fastmail and most university mail,
using a single app password instead of a per-provider OAuth application the
owner would have to register and maintain.

Messages are fetched with BODY.PEEK so nothing is marked as read -- this job
must be invisible in the mailbox it watches.
"""
from __future__ import annotations

import email
import email.policy
import imaplib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from typing import List, Optional

SNIPPET_BYTES = 4000
MAX_MESSAGES = 120
HEADER_KEYS = ("list-unsubscribe", "list-id", "precedence",
               "x-campaign-id", "x-mailer-campaign")


@dataclass
class Message:
    uid: str
    message_id: str
    subject: str
    sender: str
    received: Optional[datetime]
    snippet: str
    headers: dict


def _decode(value) -> str:
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(str(value))))
    except Exception:
        return str(value)


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"&amp;?", "&", text)
    text = re.sub(r"&#\d+;?", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _body_snippet(msg) -> str:
    """Best-effort readable text, preferring text/plain over text/html."""
    plain, html = "", ""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                if ctype not in ("text/plain", "text/html"):
                    continue
                try:
                    chunk = part.get_content()
                except Exception:
                    payload = part.get_payload(decode=True) or b""
                    chunk = payload.decode("utf-8", "replace")
                if ctype == "text/plain" and not plain:
                    plain = chunk
                elif ctype == "text/html" and not html:
                    html = chunk
        else:
            try:
                body = msg.get_content()
            except Exception:
                body = (msg.get_payload(decode=True) or b"").decode("utf-8", "replace")
            # Single-part mail is very often text/html only (banks, LMS, most
            # transactional senders). Routing it into `plain` would hand raw
            # markup to the filter and wreck the scoring.
            if msg.get_content_type() == "text/html":
                html = body
            else:
                plain = body
    except Exception:
        pass
    text = plain or _strip_html(html)
    return re.sub(r"\s+", " ", text).strip()[:600]


def _parse_date(value: str) -> Optional[datetime]:
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except Exception:
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def fetch_recent(
    host: str,
    user: str,
    password: str,
    since_hours: int = 6,
    folder: str = "INBOX",
    port: int = 993,
) -> List[Message]:
    """Return messages received in the last ``since_hours`` hours.

    Raises on connection/auth failure -- a mailbox that cannot be read is a
    loud failure, not an empty result. Silently returning [] is how a broken
    watcher looks identical to a quiet day.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    # IMAP SINCE has day granularity only, so widen by a day and filter exactly
    # in Python afterwards.
    since_token = (cutoff - timedelta(days=1)).strftime("%d-%b-%Y")

    out: List[Message] = []
    conn = imaplib.IMAP4_SSL(host, port)
    try:
        conn.login(user, password)
        conn.select(folder, readonly=True)
        status, data = conn.search(None, "SINCE", since_token)
        if status != "OK":
            raise RuntimeError(f"IMAP search failed: {status}")
        uids = (data[0] or b"").split()
        for uid in uids[-MAX_MESSAGES:]:
            status, payload = conn.fetch(
                uid, f"(BODY.PEEK[HEADER] BODY.PEEK[TEXT]<0.{SNIPPET_BYTES}>)"
            )
            if status != "OK" or not payload:
                continue
            raw_header, raw_body = b"", b""
            for part in payload:
                if not isinstance(part, tuple) or len(part) < 2:
                    continue
                marker = (part[0] or b"").decode("ascii", "replace").upper()
                if "HEADER" in marker:
                    raw_header = part[1] or b""
                elif "TEXT" in marker:
                    raw_body = part[1] or b""
            if not raw_header:
                continue
            parsed = email.message_from_bytes(
                raw_header + b"\r\n" + raw_body, policy=email.policy.default
            )
            received = _parse_date(str(parsed.get("Date", "")))
            if received and received < cutoff:
                continue
            out.append(
                Message(
                    uid=uid.decode("ascii", "replace"),
                    message_id=_decode(parsed.get("Message-ID"))
                    or f"uid-{uid.decode('ascii', 'replace')}",
                    subject=_decode(parsed.get("Subject")),
                    sender=_decode(parsed.get("From")),
                    received=received,
                    snippet=_body_snippet(parsed),
                    headers={k: _decode(parsed.get(k)) for k in HEADER_KEYS
                             if parsed.get(k)},
                )
            )
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            conn.logout()
        except Exception:
            pass
    return out
