#!/usr/bin/env python
"""BrightReach reply layer - commands.

    python outreach_replies.py poll [--no-drafts] [--no-repair] [--quiet]
        read the outreach mailbox, store sends + replies, notify, push drafts to Gmail
    python outreach_replies.py check EMAIL KIND [--company ID]
        what a sender MUST run right before sending. KIND: cold|followup|reply|pricing|audit|proposal
        prints JSON; exit 0 = allowed, 3 = blocked
    python outreach_replies.py confirm-send --company ID --to EMAIL --subject S --body-file F --message-id ID
                                            [--thread ID] [--kind cold] [--sent-at ISO]
        record a PROVIDER-CONFIRMED send, then stamp Airtable Emailed At
    python outreach_replies.py resolve COMPANY_ID STATUS "note"     owner decision after review
    python outreach_replies.py won COMPANY_ID [--setup 1500] [--monthly 400]
    python outreach_replies.py lost COMPANY_ID "note"
    python outreach_replies.py feedback [--write]     performance feedback for the lead generator
    python outreach_replies.py migrate                apply pending database migrations
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from cloudos import db  # noqa: E402
from cloudos.conversations import gmail_sync, guard, reports  # noqa: E402

FEEDBACK_PATH = ROOT / "config" / "lead_engine_feedback.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("poll")
    p.add_argument("--no-drafts", action="store_true")
    p.add_argument("--no-repair", action="store_true")
    p.add_argument("--quiet", action="store_true", help="record everything but send no notifications")
    p.add_argument("--since", default="01-Sep-2026")
    c = sub.add_parser("check")
    c.add_argument("email")
    c.add_argument("kind")
    c.add_argument("--company")
    s = sub.add_parser("confirm-send")
    for f in ("company", "to", "subject", "body-file", "message-id"):
        s.add_argument(f"--{f}", required=True)
    s.add_argument("--thread")
    s.add_argument("--kind", default="cold")
    s.add_argument("--sent-at")
    s.add_argument("--from", dest="sender", default="")
    s.add_argument("--provider", default="gmail")
    r = sub.add_parser("resolve")
    r.add_argument("company_id")
    r.add_argument("status")
    r.add_argument("note")
    w = sub.add_parser("won")
    w.add_argument("company_id")
    w.add_argument("--setup", type=float)
    w.add_argument("--monthly", type=float)
    lo = sub.add_parser("lost")
    lo.add_argument("company_id")
    lo.add_argument("note")
    fb = sub.add_parser("feedback")
    fb.add_argument("--write", action="store_true")
    sub.add_parser("migrate")
    a = ap.parse_args(argv)

    if a.cmd == "migrate":
        print(json.dumps({"applied": db.migrate()}))
        return 0
    with db.get_conn() as conn:
        if a.cmd == "poll":
            send = (lambda payload: (False, "quiet mode")) if a.quiet else None
            res = gmail_sync.sync(conn, since=a.since, send=send, push_drafts=not a.no_drafts and not a.quiet,
                                  repair_emailed_at=not a.no_repair and not a.quiet)
            print(json.dumps(res, indent=2, default=str))
            return 0
        if a.cmd == "check":
            res = guard.check_and_alert(conn, a.email, a.kind, a.company)
            print(json.dumps(res, default=str))
            return 0 if res["allowed"] else 3
        if a.cmd == "confirm-send":
            sent_at = datetime.fromisoformat(a.sent_at.replace("Z", "+00:00")) if a.sent_at else datetime.now(timezone.utc)
            res = guard.confirm_send(conn, company_id=a.company, recipient=a.to, sender=a.sender, subject=a.subject,
                                     body=Path(a.body_file).read_text(encoding="utf-8"), sent_at=sent_at,
                                     provider=a.provider, provider_message_id=a.message_id, thread_id=a.thread, kind=a.kind)
            print(json.dumps(res, default=str))
            return 0 if res["recorded"] else 1
        if a.cmd == "resolve":
            print(json.dumps(guard.owner_set_status(conn, a.company_id, a.status, a.note), default=str))
            return 0
        if a.cmd == "won":
            print(json.dumps(guard.mark_won(conn, a.company_id, a.setup, a.monthly), default=str))
            return 0
        if a.cmd == "lost":
            print(json.dumps(guard.mark_lost(conn, a.company_id, a.note), default=str))
            return 0
        if a.cmd == "feedback":
            res = reports.feedback(conn)
            if a.write:
                FEEDBACK_PATH.write_text(json.dumps(res, indent=2, default=str) + "\n", encoding="utf-8")
            print(json.dumps(res, indent=2, default=str))
            return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
