"""Read replies that arrive in the Hostinger outreach mailbox and hand them to the
existing reply layer (classification, status, suppression, drafts, Discord cards).

Only mail that belongs to a conversation we started is ever stored: a reply to
one of our Message-IDs, or mail from an address/business domain we emailed, or
a bounce about one of them. Everything else in the mailbox is left untouched.
Incremental by IMAP UID + UIDVALIDITY, so each message is handled once.
"""
from __future__ import annotations

import email
import email.policy
import imaplib
import re
from datetime import datetime, timezone

import psycopg

from cloudos.conversations import store
from cloudos.conversations.gmail_sync import Known, _date, _text
from cloudos.conversations.pipeline import process_inbound
from cloudos.conversations.text import addr, business_domain, msgid, msgids

HEADERS = "FROM TO CC SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES AUTO-SUBMITTED X-AUTOREPLY PRECEDENCE CONTENT-TYPE"
BOUNCER = re.compile(r"^(mailer-daemon|postmaster|mail-daemon)@", re.I)


def _relevant(raw: bytes, ours: set[str], known: Known) -> bool:
    m = email.message_from_bytes(raw, policy=email.policy.default)
    frm = addr(str(m.get("From", "")))
    if frm in ours:
        return False                      # our own sends are recorded by the sender itself
    refs = [msgid(m.get("In-Reply-To"))] + msgids(str(m.get("References", "")))
    if any(r and r in known.msgids for r in refs):
        return True
    if frm in known.addresses or (business_domain(frm) and business_domain(frm) in known.domains):
        return True
    return bool(BOUNCER.match(frm))       # bounces are matched on the failed address inside


def poll(conn, *, user: str, password: str, host: str = "imap.hostinger.com", port: int = 993,
         folder: str = "INBOX", imap_factory=imaplib.IMAP4_SSL, send=None, notify_owner: bool = True,
         first_run_days: int = 14, max_messages: int = 1500) -> dict:
    ours = {user.lower()}
    key = f"{user.lower()}:{folder}"
    res = {"scanned": 0, "relevant": 0, "inbound": {}, "first_run": False, "skipped_errors": 0}
    i = imap_factory(host, port)
    try:
        typ, _ = i.login(user, password)
        if typ != "OK":
            raise RuntimeError("imap login refused")
        typ, _ = i.select(f'"{folder}"', readonly=True)
        if typ != "OK":
            raise RuntimeError(f"cannot open {folder}")
        typ, data = i.status(f'"{folder}"', "(UIDVALIDITY)")
        uidvalidity = int(re.search(rb"UIDVALIDITY (\d+)", data[0]).group(1))
        st = conn.execute("SELECT * FROM reply_poll_state WHERE mailbox = %s", (key,)).fetchone()
        same = bool(st and st["uidvalidity"] == uidvalidity)
        if same:
            typ, data = i.uid("SEARCH", None, f"UID {st['last_uid'] + 1}:*")
        else:
            res["first_run"] = True
            since = datetime.now(timezone.utc).date().fromordinal(datetime.now(timezone.utc).date().toordinal() - first_run_days)
            typ, data = i.uid("SEARCH", None, "SINCE", since.strftime("%d-%b-%Y"))
        floor = st["last_uid"] if same else 0
        uids = sorted(u for u in (int(x) for x in (data[0] or b"").split()) if u > floor)[:max_messages]
        res["scanned"] = len(uids)
        known = Known(conn)
        last = floor
        for n in range(0, len(uids), 100):
            chunk = uids[n:n + 100]
            typ, data = i.uid("FETCH", ",".join(map(str, chunk)), f"(UID BODY.PEEK[HEADER.FIELDS ({HEADERS})])")
            wanted = []
            for part in data or []:
                if isinstance(part, tuple):
                    u = re.search(rb"UID (\d+)", part[0])
                    if u and _relevant(part[1], ours, known):
                        wanted.append(int(u.group(1)))
            if wanted:
                typ, data = i.uid("FETCH", ",".join(map(str, wanted)), "(UID BODY.PEEK[])")
                for part in data or []:
                    if not isinstance(part, tuple):
                        continue
                    uid = int(re.search(rb"UID (\d+)", part[0]).group(1))
                    res["relevant"] += 1
                    try:
                        label = _one(conn, part[1], uid, ours, folder, user, send, notify_owner)
                        res["inbound"][label] = res["inbound"].get(label, 0) + 1
                    except psycopg.OperationalError:
                        raise                  # database down: do not move past these messages
                    except Exception:  # noqa: BLE001 - one unreadable email must not block the rest
                        conn.rollback()
                        res["skipped_errors"] += 1
            last = max([last, *chunk])
            conn.execute("INSERT INTO reply_poll_state (mailbox, last_uid, uidvalidity, last_run_at, last_result) "
                         "VALUES (%s,%s,%s,now(),%s) ON CONFLICT (mailbox) DO UPDATE SET last_uid = EXCLUDED.last_uid, "
                         "uidvalidity = EXCLUDED.uidvalidity, last_run_at = now(), last_result = EXCLUDED.last_result",
                         (key, last, uidvalidity, store._j(res)))
            conn.commit()
        if not uids:
            conn.execute("INSERT INTO reply_poll_state (mailbox, last_uid, uidvalidity, last_run_at, last_result) "
                         "VALUES (%s,%s,%s,now(),%s) ON CONFLICT (mailbox) DO UPDATE SET last_run_at = now(), "
                         "uidvalidity = EXCLUDED.uidvalidity, last_result = EXCLUDED.last_result",
                         (key, floor, uidvalidity, store._j(res)))
            conn.commit()
    finally:
        try:
            i.logout()
        except Exception:  # noqa: BLE001
            pass
    return res


def _one(conn, raw: bytes, uid: int, ours: set[str], folder: str, user: str, send, notify_owner: bool) -> str:
    m = email.message_from_bytes(raw, policy=email.policy.default)
    out = process_inbound(conn, {
        "provider_message_id": msgid(m.get("Message-ID")) or f"hostinger-uid-{uid}@{user}",
        "thread_id": None, "in_reply_to": msgid(m.get("In-Reply-To")) or None,
        "references": msgids(str(m.get("References", ""))), "sender": str(m.get("From", "")),
        "recipient": str(m.get("To", "")), "subject": str(m.get("Subject", "")), "body": _text(m),
        "occurred_at": _date(m), "provider": "hostinger",
        "headers": {k: str(m.get(k)) for k in ("Auto-Submitted", "X-Autoreply", "X-Autorespond", "Precedence",
                                              "Content-Type") if m.get(k)},
        "source_ref": f"imap:hostinger:{folder}:{uid}"}, our_addresses=ours, send=send, notify_owner=notify_owner)
    return out.get("classification") or out["status"]
