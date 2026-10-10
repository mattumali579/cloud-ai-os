"""Hostinger mailbox access: SMTP to send, IMAP to file the copy in Sent and to
prove after a crash whether a message really went out.

A send counts as confirmed ONLY when the SMTP server accepted the message for
the recipient (sendmail returned with no refused recipients). Everything else
is classified so the caller can decide: retry later, stop for good, or stop the
whole run (bad login).
"""
from __future__ import annotations

import imaplib
import os
import re
import smtplib
import socket
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid
from datetime import datetime, timezone


@dataclass
class SendResult:
    ok: bool
    kind: str               # sent | auth | permanent | transient (nothing delivered) | throttled | unknown (maybe delivered)
    detail: str = ""


class AuthError(RuntimeError):
    pass


# The server's OWN limits / policy - never the recipient's fault, so never a bounce.
_THROTTLE = re.compile(r"(?i)quota|limit|rate|too many|exceeded|try (again )?later|spam|policy|blocked|blacklist|"
                       r"reputation|sender address rejected|not allowed to send|5\.7\.|4\.7\.|5\.4\.5")
# The address itself does not exist - the only thing that is a hard bounce.
_NO_SUCH_USER = re.compile(r"(?i)5\.1\.[0-3]\b|5\.1\.10\b|user unknown|unknown user|no such (user|mailbox|recipient)|"
                           r"(mailbox|recipient|user|address)[^.]{0,30}(does not exist|not found|unavailable|invalid)|"
                           r"invalid (recipient|mailbox|address)|recipient address rejected: user unknown")


def classify_reject(code: int, text: str) -> str:
    """permanent (the address is dead) / throttled (the server's own limit or policy) / transient."""
    if _THROTTLE.search(text or "") and not re.search(r"(?i)5\.1\.1\b|user unknown", text or ""):
        return "throttled"
    if 500 <= int(code) < 600 and _NO_SUCH_USER.search(text or ""):
        return "permanent"
    return "transient"


def new_message_id(from_email: str) -> str:
    """RFC 5322 id WITHOUT angle brackets, lower-cased (how the reply layer stores ids)."""
    return make_msgid(domain=from_email.split("@")[-1]).strip("<>").lower()


def build(*, from_email: str, from_name: str, to: str, subject: str, body: str, message_id: str,
          in_reply_to: str | None = None, company_id: str = "", step: int = 0) -> EmailMessage:
    m = EmailMessage()
    m["From"] = formataddr((from_name, from_email))
    m["To"] = to
    m["Subject"] = subject
    m["Date"] = format_datetime(datetime.now(timezone.utc))
    m["Message-ID"] = f"<{message_id}>"
    if in_reply_to:
        m["In-Reply-To"] = f"<{in_reply_to}>"
        m["References"] = f"<{in_reply_to}>"
    m["Reply-To"] = from_email
    # one-click opt-out that mail providers show next to the sender name
    m["List-Unsubscribe"] = f"<mailto:{from_email}?subject=stop>"
    m["X-Leadgen-Outreach"] = "hostinger-first" if step == 0 else "hostinger-followup"
    if company_id:
        m["X-Leadgen-Company"] = company_id
    m.set_content(body)
    return m


class Mailbox:
    """One login for a whole run. Tests replace smtp_factory / imap_factory with fakes."""

    def __init__(self, *, user: str, password: str, smtp_host: str = "smtp.hostinger.com", smtp_port: int = 465,
                 imap_host: str = "imap.hostinger.com", imap_port: int = 993,
                 smtp_factory=smtplib.SMTP_SSL, imap_factory=imaplib.IMAP4_SSL, timeout: int = 40):
        self.user, self.password = user, password
        self.smtp_host, self.smtp_port, self.imap_host, self.imap_port = smtp_host, smtp_port, imap_host, imap_port
        self.smtp_factory, self.imap_factory, self.timeout = smtp_factory, imap_factory, timeout
        self._smtp = None
        self._sent_folder: str | None = None

    # ------------------------------------------------------------------ SMTP
    def _connect(self):
        if self._smtp is None:
            s = self.smtp_factory(self.smtp_host, self.smtp_port, timeout=self.timeout,
                                  context=ssl.create_default_context())
            try:
                s.login(self.user, self.password)
            except (smtplib.SMTPAuthenticationError, smtplib.SMTPServerDisconnected) as exc:
                # some servers answer a wrong password by hanging up; either way nothing was sent
                try:
                    s.close()
                except Exception:  # noqa: BLE001
                    pass
                raise AuthError(f"login refused ({getattr(exc, 'smtp_code', 'disconnected')})") from exc
            self._smtp = s
        return self._smtp

    def check_login(self) -> None:
        self._connect()

    def send(self, msg: EmailMessage) -> SendResult:
        """MAIL, RCPT and DATA are run one by one so a failure is placed exactly:
        before DATA nothing was delivered (safe to retry); once DATA has started the
        server may have taken it, so the answer is 'unknown' and the caller must never
        simply resend."""
        rcpt = str(msg["To"])
        for attempt in (1, 2):
            try:
                s = self._connect()
            except AuthError as exc:
                return SendResult(False, "auth", str(exc))
            except (smtplib.SMTPException, OSError, socket.timeout) as exc:
                self._drop()
                return SendResult(False, "transient", f"connect: {type(exc).__name__}")
            try:
                s.noop()                       # is the reused connection still alive?
                break
            except (smtplib.SMTPException, OSError, socket.timeout):
                self._drop()                   # idle connection closed by the server: open a fresh one
                if attempt == 2:
                    return SendResult(False, "transient", "connection kept closing")
        try:
            s.ehlo_or_helo_if_needed()
            code, resp = s.mail(self.user)
            if code != 250:
                s.rset()
                kind = "throttled" if classify_reject(code, _t(resp)) == "throttled" or code >= 500 else "transient"
                return SendResult(False, kind, f"sender refused {code} {_t(resp)}")
            code, resp = s.rcpt(rcpt)
            if code not in (250, 251):
                s.rset()
                return SendResult(False, classify_reject(code, _t(resp)), f"{code} {_t(resp)}")
        except (smtplib.SMTPException, OSError, socket.timeout) as exc:
            self._drop()
            return SendResult(False, "transient", f"before data: {type(exc).__name__}")
        try:
            code, resp = s.data(msg.as_bytes())
        except (smtplib.SMTPException, OSError, socket.timeout) as exc:
            self._drop()
            return SendResult(False, "unknown", f"during data: {type(exc).__name__}")
        if code == 250:
            return SendResult(True, "sent", f"250 {_t(resp)[:80]}")
        self._drop()
        return SendResult(False, classify_reject(code, _t(resp)), f"{code} {_t(resp)}")

    def _drop(self):
        try:
            if self._smtp is not None:
                self._smtp.quit()
        except Exception:  # noqa: BLE001
            pass
        self._smtp = None

    def close(self):
        self._drop()

    # ------------------------------------------------------------------ IMAP
    def _imap(self):
        try:
            i = self.imap_factory(self.imap_host, self.imap_port, timeout=self.timeout)
        except TypeError:
            i = self.imap_factory(self.imap_host, self.imap_port)
        typ, _ = i.login(self.user, self.password)
        if typ != "OK":
            raise AuthError("imap login refused")
        return i

    def sent_folder(self, i) -> str:
        if self._sent_folder:
            return self._sent_folder
        typ, rows = i.list()
        best = None
        for raw in rows or []:
            line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
            name = line.rsplit(' "', 1)[-1].strip().strip('"') if '"' in line else line.split()[-1]
            if "\\Sent" in line:
                best = name
                break
            if re.search(r"(?i)(^|[./])sent( items| messages)?$", name):
                best = best or name
        self._sent_folder = best or "INBOX.Sent"
        return self._sent_folder

    def file_in_sent(self, msg: EmailMessage) -> bool:
        """Hostinger's SMTP does not keep a copy; put one in Sent so the mailbox shows the conversation."""
        try:
            i = self._imap()
            try:
                folder = self.sent_folder(i)
                typ, _ = i.append(_q(folder), "(\\Seen)", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
                return typ == "OK"
            finally:
                _logout(i)
        except Exception:  # noqa: BLE001 - the send itself already succeeded
            return False

    def in_sent(self, message_id: str) -> bool | None:
        """True/False = the Sent folder does/doesn't hold this Message-ID. None = could not look."""
        try:
            i = self._imap()
            try:
                typ, _ = i.select(_q(self.sent_folder(i)), readonly=True)
                if typ != "OK":
                    return None
                typ, data = i.search(None, "HEADER", "Message-ID", f"<{message_id}>")
                return typ == "OK" and bool((data[0] or b"").split())
            finally:
                _logout(i)
        except Exception:  # noqa: BLE001
            return None


def _t(v) -> str:
    return (v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v or ""))[:160]


def _q(folder: str) -> str:
    return folder if folder.startswith('"') else f'"{folder}"'


def _logout(i) -> None:
    try:
        i.logout()
    except Exception:  # noqa: BLE001
        pass


def from_env(cfg: dict) -> Mailbox | None:
    s = cfg["sender"]
    user = (os.environ.get(s["from_email_env"]) or "").strip()
    pw = (os.environ.get(s["password_env"]) or "").strip()
    if not (user and pw):
        return None
    return Mailbox(user=user, password=pw, smtp_host=s["smtp_host"], smtp_port=int(s["smtp_port"]),
                   imap_host=s["imap_host"], imap_port=int(s["imap_port"]))
