"""Text helpers: addresses, message ids, and cutting quoted history out of a reply."""
from __future__ import annotations

import re
from email.utils import getaddresses, parseaddr

FREEMAIL = {
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "hotmail.com", "outlook.com", "live.com",
    "msn.com", "aol.com", "icloud.com", "me.com", "mac.com", "comcast.net", "att.net", "sbcglobal.net",
    "bellsouth.net", "verizon.net", "cox.net", "charter.net", "protonmail.com", "proton.me", "gmx.com",
    "mail.com", "zoho.com", "yandex.com", "earthlink.net", "frontier.com", "windstream.net",
}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def addr(value: str) -> str:
    """'John <John@ABC.com>' -> 'john@abc.com'."""
    return parseaddr(value or "")[1].strip().lower()


def addrs(value: str) -> list[str]:
    return [a.strip().lower() for _, a in getaddresses([value or ""]) if a]


def domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


def business_domain(email: str) -> str:
    """Domain usable as a company identity; freemail is never one."""
    d = domain_of(email)
    return "" if not d or d in FREEMAIL else d


def msgid(value: str | None) -> str:
    """Canonical Message-ID: no angle brackets, no whitespace, lower-case."""
    v = (value or "").strip()
    m = re.search(r"<([^>]+)>", v)
    return (m.group(1) if m else v).strip().lower()


def msgids(value: str | None) -> list[str]:
    return [m.strip().lower() for m in re.findall(r"<([^>]+)>", value or "")] or (
        [msgid(value)] if value and value.strip() else [])


_QUOTE_STARTS = [
    re.compile(r"^\s*On .{0,300}?wrote:\s*$", re.I | re.S),             # Gmail / Apple Mail
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}", re.I),
    re.compile(r"^\s*-{2,}\s*Forwarded message\s*-{2,}", re.I),
    re.compile(r"^\s*_{8,}\s*$"),                                         # Outlook separator
    re.compile(r"^\s*From:\s.+", re.I),                                  # Outlook header block
    re.compile(r"^\s*Sent from my (iPhone|iPad|Android|Galaxy|mobile)", re.I),
    re.compile(r"^\s*Get Outlook for", re.I),
]


def strip_quoted(body: str) -> str:
    """Return only what the sender newly wrote, without the thread they quoted.

    Our own pitch sits in the quoted part of every reply; classifying it would
    make every reply look like a price question.
    """
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith(">"):
            break
        # "On Mon, Sep 29, 2026 at 9:00 AM Matt <x@y.com>" + next line "wrote:"
        pair = line + " " + (lines[i + 1] if i + 1 < len(lines) else "")
        if re.match(r"^\s*On\s.{6,300}", line, re.I) and (
                re.search(r"wrote:\s*$", line, re.I) or re.search(r"wrote:\s*$", pair, re.I)):
            break
        if any(p.match(line) for p in _QUOTE_STARTS):
            break
        out.append(line)
        i += 1
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def one_line(text: str, limit: int = 300) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"
