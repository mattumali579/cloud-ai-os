#!/usr/bin/env python
"""BrightReach status - what happened today and what needs you.

    python outreach_status.py                 today's summary + top prospects + attention list
    python outreach_status.py --day 2026-09-28
    python outreach_status.py --queue         only the things that need you
    python outreach_status.py --company "ABC Roofing"   full conversation memory for one company
    python outreach_status.py --json          machine-readable
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from cloudos import db  # noqa: E402
from cloudos.conversations import reports, store  # noqa: E402

LABELS = [("emails_confirmed_sent", "Emails confirmed sent"), ("follow_ups_and_replies_sent", "Follow-ups / replies sent"),
          ("replies", "Replies"), ("positive_replies", "Positive replies"), ("price_questions", "Price questions"),
          ("meeting_requests", "Meeting requests"), ("interested", "Interested"), ("proposals_needed", "Proposals needed"),
          ("proposals_sent", "Proposals sent"), ("won", "Won"), ("lost", "Lost"), ("needs_review", "Needs review"),
          ("unsubscribes", "Unsubscribes"), ("bounces", "Bounces")]


def print_queue(items: list[dict]) -> None:
    if not items:
        print("Nothing needs you right now.")
        return
    for n, i in enumerate(items, 1):
        print(f"{n}. {i['company']}  [{i['urgency'].upper()}]")
        print(f"   Status: {(i['status'] or '?').upper()}")
        if i["last_reply"]:
            print(f"   Last message: \"{i['last_reply'][:200]}\"")
        print(f"   Why you: {i['reason_human_needed']}")
        print(f"   Next action: {i['recommended_action']}")
        if i["record_link"]:
            print(f"   Record: {i['record_link']}")
        print()


def print_company(conn, name: str) -> int:
    rows = conn.execute("SELECT company_id, company_name FROM companies WHERE company_name ILIKE %s OR normalized_domain ILIKE %s "
                        "LIMIT 5", (f"%{name}%", f"%{name}%")).fetchall()
    if not rows:
        print(f"No company matches {name!r}.")
        return 1
    for r in rows:
        cid = str(r["company_id"])
        st = conn.execute("SELECT * FROM company_conversation_state WHERE company_id = %s", (cid,)).fetchone()
        print(f"=== {r['company_name']} ===")
        if st:
            print(f"Status: {st['current_status']} (before: {st['previous_status']})  do-not-contact: {st['do_not_contact']}")
            print(f"Offer: {st['current_offer']}  Price: {st['current_price']}  Proposal: {st['proposal_status']}")
            print(f"Next: {st['next_action']}  due {st['next_action_due']}  follow-up on {st['follow_up_on']}")
        print("\nConversation:")
        for m in store.thread(conn, cid):
            arrow = "US  →" if m["direction"] == "outbound" else "THEM←"
            print(f"  {str(m['occurred_at'])[:16]} {arrow} [{m['kind']}] {m['subject'][:70]}")
            a = conn.execute("SELECT classification, confidence FROM reply_analyses WHERE message_id = %s ORDER BY analysis_id DESC "
                             "LIMIT 1", (m["message_id"],)).fetchone()
            if a:
                print(f"         → {a['classification']} ({float(a['confidence']):.2f})")
        facts = store.facts(conn, cid)
        if facts:
            print("\nRemembered:")
            for f in facts:
                print(f"  - [{f['fact_type']}] {f['fact_text']}")
        print()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--day")
    ap.add_argument("--queue", action="store_true")
    ap.add_argument("--company")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-airtable", action="store_true", help="count sends from the database instead of Airtable")
    a = ap.parse_args(argv)
    with db.get_conn() as conn:
        if a.company:
            return print_company(conn, a.company)
        if a.queue:
            q = reports.attention_queue(conn)
            print(json.dumps(q, indent=2, default=str)) if a.json else print_queue(q)
            return 0
        day = date.fromisoformat(a.day) if a.day else None
        try:
            rep = reports.status_report(conn, day, use_airtable=not a.no_airtable)
        except Exception as exc:  # Airtable down -> still report from the database
            print(f"(Airtable unavailable: {type(exc).__name__}; counting sends from the database)", file=sys.stderr)
            rep = reports.status_report(conn, day, use_airtable=False)
    if a.json:
        print(json.dumps(rep, indent=2, default=str))
        return 0
    t = rep["today"]
    print(f"BRIGHTREACH - {rep['day']} (America/Chicago)\n\nTODAY:")
    for k, label in LABELS:
        print(f"  {label:<26} {t[k]}")
    print(f"  (sends counted from: {t['emails_confirmed_sent_source']})")
    print("\nMOST IMPORTANT ACTIVE PROSPECTS:")
    if not rep["top_prospects"]:
        print("  none yet")
    for n, p in enumerate(rep["top_prospects"], 1):
        print(f"{n}. {p['company']}{'  (high value)' if p['high_value'] else ''}")
        print(f"   Status: {p['status'].upper()}" + (f" / {p['last_classification']}" if p["last_classification"] else ""))
        if p["last_message"]:
            print(f"   Last message: \"{p['last_message'][:160]}\"")
        print(f"   Next action: {p['next_action']}")
    print(f"\nNEEDS YOU ({len(rep['attention'])}):")
    print_queue(rep["attention"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
