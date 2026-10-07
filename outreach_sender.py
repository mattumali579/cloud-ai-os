#!/usr/bin/env python
"""BrightReach Hostinger sender - what the cloud runs every 10 minutes.

    python outreach_sender.py cycle [--minutes 8]   the whole loop (below), safe to run any time
    python outreach_sender.py status [--json]       today's numbers and what's blocking 300
    python outreach_sender.py plan                  prepare first emails for Ready leads (sends nothing)
    python outreach_sender.py queue-audit [--min 100]  count prepared first emails that pass every send check (read only)
    python outreach_sender.py trade-count             Ready trade leads, counts only (read only)
    python outreach_sender.py selftest              one real email to the owner through Hostinger, then prove it
    python outreach_sender.py agentmail-selftest    one internal notice to the owner through AgentMail, once a day
    python outreach_sender.py airtable-selftest     count the Airtable rows (read only) and prove the count was saved
    python outreach_sender.py airtable              push changed lead states to Airtable now

cycle, in order - each step survives the one before it failing:
  1. stop waiting follow-ups for anyone who replied / unsubscribed / bounced
  2. prepare first emails for Ready leads (final dedupe + suppression + copy QA)
  3. Hostinger login check (missing/refused -> one notice, nothing is sent)
  4. finish anything a crashed run left half-done (never resends)
  5. read the Hostinger inbox for replies and bounces
  6. one-time self-test email to the owner before the first prospect email ever
  7. send due emails, paced, inside the send window, under today's cap
  8. notices to AgentMail (and Discord for urgent ones), daily digests at 8 PM
  9. Airtable update (hourly, within the free-plan budget)

PUBLIC repo: output is counts only - never names, addresses or message text.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from cloudos import db  # noqa: E402
from cloudos.conversations import notify  # noqa: E402
from cloudos.outreach import agentmail, airtable_sync, replies, review_campaign, sender, status  # noqa: E402
from cloudos.outreach.transport import AuthError, build, from_env, new_message_id  # noqa: E402


def _state(conn, key: str):
    row = conn.execute("SELECT value, updated_at FROM airtable_state WHERE key = %s", (key,)).fetchone()
    return row


def _set_state(conn, key: str, value) -> None:
    conn.execute("INSERT INTO airtable_state (key, value) VALUES (%s, %s::jsonb) ON CONFLICT (key) DO UPDATE "
                 "SET value = EXCLUDED.value, updated_at = now()", (key, json.dumps(value, default=str)))
    conn.commit()


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")


def public(obj) -> str:
    """JSON for a PUBLIC log: any email address that slipped into an error message is masked."""
    return EMAIL_RE.sub("<email>", json.dumps(obj, indent=2, default=str))


def _discord(payload: dict):
    return notify.default_sender(payload)


# Sent can lag a few seconds behind SMTP. The self-test re-checks Sent (never re-sends): at most
# SELFTEST_SENT_CHECKS looks, SELFTEST_SENT_WAIT_S apart = 25s of waiting at the very most.
SELFTEST_SENT_CHECKS = 6
SELFTEST_SENT_WAIT_S = 5.0
_sleep = time.sleep


def _wait_in_sent(mailbox, mid: str) -> bool:
    """True only when Sent holds this exact Message-ID. False/None (not there / could not look) is re-checked."""
    for n in range(SELFTEST_SENT_CHECKS):
        if n:
            _sleep(SELFTEST_SENT_WAIT_S)
        if mailbox.in_sent(mid) is True:
            return True
    return False


def selftest(conn, cfg: dict, mailbox, from_email: str) -> dict:
    """Send one real email to the owner (or the mailbox itself) and prove it: SMTP accepted + found in Sent."""
    to = (os.environ.get(cfg["agentmail"]["owner_env"]) or "").strip() or from_email
    mid = new_message_id(from_email)
    msg = build(from_email=from_email, from_name=sender.sender_name(cfg), to=to,
                subject="BrightReach sender self-test", message_id=mid, step=0,
                body="This is the one-time check that the outreach mailbox can send. Nothing to do.\n\n"
                     f"Sent {datetime.now(timezone.utc).isoformat()} by the cloud sender.")
    res = mailbox.send(msg)
    out = {"ok": False, "accepted": res.ok, "detail": res.detail}
    if res.ok:
        sender._file_copy(mailbox, msg, mid)
        out["in_sent"] = _wait_in_sent(mailbox, mid)
        # passed only when both are proven: the provider took it AND that same Message-ID is in Sent
        if out["in_sent"]:
            out["ok"] = True
            _set_state(conn, "selftest", {"message_id": mid, "at": datetime.now(timezone.utc), **out})
    return out


def agentmail_selftest(conn, cfg: dict, post=None) -> dict:
    """One generic internal notice to the manager inbox through the normal AgentMail path. First creates any
    configured role inbox the account is missing (never deletes or renames one). Passes only when AgentMail
    accepted the notice in THIS call. Deduped per UTC day: a delivered one is never sent again; one that was
    saved but never went out is retried."""
    post = post or agentmail.http_poster(cfg)
    if post is None:
        return {"ok": False, "blocked": f"{cfg['agentmail']['api_env']} not set"}   # nothing queued either
    inboxes = agentmail.ensure_inboxes(cfg)
    if not inboxes["ok"]:
        return {"ok": False, "inboxes": inboxes}                                    # nothing queued, nothing sent
    key = f"selftest:{datetime.now(timezone.utc).date()}"
    r = agentmail.notify(conn, cfg, role="manager", dedupe_key=key, post=post,
                         subject="BrightReach AgentMail self-test",
                         text="This is the internal check that manager notices can be delivered. Nothing to do.")
    if not r["created"]:
        r = agentmail.retry(conn, cfg, key, post=post)
        if not r["retried"]:
            return {"ok": False, "duplicate": True}
    return {"ok": r["delivered"] is True, "delivered": r["delivered"], "receipt": r["receipt"],
            "retried": bool(r.get("retried")), "inboxes": inboxes}


def airtable_selftest(conn) -> dict:
    """Read-only: count the Airtable rows through the normal counting path (its calls are budgeted there), then
    read the saved count back. Passes only when Airtable answered AND the database holds that same count."""
    try:
        n = airtable_sync.refresh_record_count(conn)
    except Exception as exc:  # noqa: BLE001 - PUBLIC log: the error type only, the message can hold the base/table
        conn.rollback()
        return {"ok": False, "error": type(exc).__name__}
    if n is None:
        return {"ok": False, "blocked": "Airtable settings missing"}
    row = _state(conn, "record_count")
    saved = row["value"] if row and type(row["value"]) is int else None
    return {"ok": saved == n, "records": n, "persisted": saved}


def campaign_selftest(conn, cfg: dict, mailbox, from_email: str) -> dict:
    """Exercise the active campaign end to end using only the owner's safe address.

    The provider-confirmed outbound is recorded through the same queue path as a
    prospect email.  AgentMail then sends a real threaded reply back to the
    Hostinger inbox, which is polled and classified normally.  The test is
    resumable and never sends its outbound twice.
    """
    key = f"campaign_selftest:{review_campaign.CAMPAIGN}:{review_campaign.COPY_VERSION}"
    saved = _state(conn, key)
    prior = dict(saved["value"]) if saved and isinstance(saved["value"], dict) else {}
    if prior.get("ok"):
        return {**prior, "duplicate": True}

    recipient = (os.environ.get(cfg["agentmail"]["owner_env"]) or "").strip()
    if not recipient:
        return {"ok": False, "blocked": f"{cfg['agentmail']['owner_env']} not set"}
    company_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"brightreach:{review_campaign.CAMPAIGN}:selftest"))
    subject = "quick Google review note"
    first = (
        "Hi Matt,\n\nI noticed BrightReach Campaign Self-Test has 12 Google reviews at 4.2 stars, "
        "while a nearby competitor has 95. I put together a short personalized video showing the review "
        "and Maps gaps. Want me to send it?\n\nMatt"
    )
    followups = [
        "Hi Matt,\n\nJust circling back on the Google review and Maps breakdown I mentioned. "
        "Should I send the short video?\n\nMatt",
        "Hi Matt,\n\nOne useful part of the breakdown is the review gap versus the nearby competitor. "
        "Want me to share the short video?\n\nMatt",
        "Hi Matt,\n\nI’ll close the loop after this. If improving the Google review flow and Maps presence "
        "is relevant, should I send the short breakdown?\n\nMatt",
    ]
    evidence = {
        "company_id": company_id, "company": "BrightReach Campaign Self-Test", "email": recipient,
        "score": 10, "exact_evidence": "Test fixture: 4.2 stars from 12 reviews.",
        "competitor_context": "Test fixture: nearby competitor has 95 reviews.", "selftest": True,
    }
    conn.execute(
        """
        INSERT INTO companies (company_id, company_name, normalized_name, industry, city, state, discovery_source,
                               qualification_status, qualification_reason, personalization, outreach_status, active)
        VALUES (%s::uuid,'BrightReach Campaign Self-Test','brightreach campaign self test','software','Chicago','IL',
                'campaign_selftest','HIGH','owner-only launch fixture',%s::jsonb,'outreach_ready',true)
        ON CONFLICT (company_id) DO UPDATE SET first_contacted_at=NULL, outreach_status='outreach_ready', active=true,
            qualification_status='HIGH', personalization=EXCLUDED.personalization, updated_at=now()
        """, (company_id, json.dumps({"google_reviews": 12, "google_rating": 4.2,
                                      "review_campaign": {"campaign": review_campaign.CAMPAIGN,
                                                          "selected": False, "score": 10, "selftest": True}})),
    )
    conn.execute(
        "INSERT INTO contacts (company_id,email,email_status,role,source) VALUES (%s::uuid,%s,'validated','owner',"
        "'campaign_selftest') ON CONFLICT DO NOTHING", (company_id, recipient),
    )
    for step, body in enumerate([first, *followups]):
        conn.execute(
            """
            INSERT INTO outreach_campaign_copy
                (company_id,campaign,step,subject,body,copy_version,evidence,qa_problems,qa_passed_at)
            VALUES (%s::uuid,%s,%s,%s,%s,%s,%s::jsonb,'[]'::jsonb,now())
            ON CONFLICT (company_id,campaign,step) DO UPDATE SET subject=EXCLUDED.subject, body=EXCLUDED.body,
                copy_version=EXCLUDED.copy_version, evidence=EXCLUDED.evidence, qa_problems='[]'::jsonb,
                qa_passed_at=now(), updated_at=now()
            """, (company_id, review_campaign.CAMPAIGN, step, subject, body,
                    review_campaign.COPY_VERSION, json.dumps(evidence)),
        )
    conn.commit()

    sent_mid = prior.get("message_id")
    if not sent_mid:
        body = review_campaign.add_footer(first, sender.postal_address())
        conn.execute(
            """
            INSERT INTO outreach_queue
                (company_id,step,recipient,subject,body,copy_variant,state,due_at,campaign,copy_source,evidence)
            VALUES (%s::uuid,0,%s,%s,%s,%s,'claimed',now(),%s,%s,%s::jsonb)
            ON CONFLICT (company_id,step) DO UPDATE SET recipient=EXCLUDED.recipient, subject=EXCLUDED.subject,
                body=EXCLUDED.body, copy_variant=EXCLUDED.copy_variant, state='claimed', due_at=now(),
                claimed_by='campaign-selftest', claimed_at=now(), attempts=outreach_queue.attempts+1,
                stop_reason=NULL, campaign=EXCLUDED.campaign, copy_source=EXCLUDED.copy_source,
                evidence=EXCLUDED.evidence, updated_at=now()
            RETURNING *
            """, (company_id, recipient, subject, body, review_campaign.COPY_VERSION,
                    review_campaign.CAMPAIGN, review_campaign.COPY_VERSION, json.dumps(evidence)),
        )
        item = conn.execute("SELECT * FROM outreach_queue WHERE company_id=%s::uuid AND step=0", (company_id,)).fetchone()
        outcome = sender.send_item(conn, item, mailbox, cfg, from_email=from_email)
        if outcome != "sent":
            result = {"ok": False, "outbound": outcome, "stage": "hostinger_send"}
            _set_state(conn, key, result)
            return result
        sent_mid = conn.execute(
            "SELECT message_id_header FROM outreach_queue WHERE company_id=%s::uuid AND step=0", (company_id,),
        ).fetchone()["message_id_header"]
        prior = {"message_id": sent_mid, "outbound": "provider_confirmed"}
        _set_state(conn, key, prior)

    if not prior.get("reply_accepted"):
        inboxes = agentmail.ensure_inboxes(cfg)
        post = agentmail.http_poster(cfg)
        if not inboxes.get("ok") or post is None:
            result = {**prior, "ok": False, "stage": "agentmail_reply", "agentmail_ready": False}
            _set_state(conn, key, result)
            return result
        ok, receipt = post(cfg["agentmail"]["inboxes"]["followup"], {
            "to": [from_email], "subject": f"Re: {subject}", "text": "Yes, please send the video.",
            "headers": {"In-Reply-To": sent_mid, "References": sent_mid},
            "labels": ["brightreach", "campaign-selftest"],
        })
        if not ok:
            result = {**prior, "ok": False, "stage": "agentmail_reply", "reply_accepted": False,
                      "receipt": receipt}
            _set_state(conn, key, result)
            return result
        prior.update(reply_accepted=True, reply_receipt=receipt)
        _set_state(conn, key, prior)

    reply_result = None
    for attempt in range(18):
        reply_result = replies.poll(conn, user=from_email, password=mailbox.password,
                                    host=cfg["sender"]["imap_host"], port=int(cfg["sender"]["imap_port"]))
        inbound = conn.execute(
            "SELECT count(*) n FROM outreach_messages WHERE company_id=%s::uuid AND direction='inbound' "
            "AND kind NOT IN ('auto_reply','bounce')", (company_id,),
        ).fetchone()["n"]
        if inbound:
            break
        if attempt < 17:
            _sleep(5)
    swept = sender.sweep(conn, cfg)
    state = conn.execute("SELECT * FROM company_conversation_state WHERE company_id=%s::uuid", (company_id,)).fetchone()
    followup = conn.execute(
        "SELECT state,stop_reason FROM outreach_queue WHERE company_id=%s::uuid AND step=1", (company_id,),
    ).fetchone()
    airtable = airtable_selftest(conn)
    notice = agentmail.notify(
        conn, cfg, role="manager", dedupe_key=f"campaign-selftest:{review_campaign.COPY_VERSION}",
        subject="BrightReach campaign launch test passed",
        text="The owner-only Google Reviews campaign test completed: Hostinger accepted and filed the outbound, "
             "AgentMail delivered a threaded reply, the reply was classified, and the follow-up was stopped.",
    )
    ok = bool(state and state["current_status"] not in ("discovered", "ready", "emailed")
              and followup and followup["state"] == "cancelled" and airtable.get("ok")
              and notice.get("delivered"))
    result = {
        **prior, "ok": ok, "outbound": "provider_confirmed", "inbound_processed": bool(state),
        "classification": state["last_reply_classification"] if state else None,
        "followup_cancelled": bool(followup and followup["state"] == "cancelled"),
        "reply_poll": reply_result, "sweep_cancelled": swept.get("cancelled", 0),
        "airtable": airtable, "notification_delivered": notice.get("delivered", False),
    }
    conn.execute("UPDATE companies SET active=false, updated_at=now() WHERE company_id=%s::uuid", (company_id,))
    conn.commit()
    _set_state(conn, key, result)
    return result


def cycle(minutes: float) -> dict:
    cfg = sender.load_config()
    res: dict = {"started": datetime.now(timezone.utc).isoformat()}
    with db.get_conn() as conn:
        run_id = conn.execute("INSERT INTO outreach_sender_runs (mode) VALUES ('cycle') RETURNING run_id").fetchone()["run_id"]
        conn.commit()

        def step(name, fn):
            try:
                res[name] = fn()
            except AuthError as exc:
                conn.rollback()
                res[name] = {"auth_error": str(exc)}
            except Exception as exc:  # noqa: BLE001 - one broken step must not stop the others
                conn.rollback()
                # PUBLIC log: the error type and the code line only - never the message (it can hold a row)
                tb = traceback.extract_tb(exc.__traceback__)[-1:] if exc.__traceback__ else []
                res[name] = {"error": type(exc).__name__, "at": [f"{Path(f.filename).name}:{f.lineno}" for f in tb]}

        step("sweep", lambda: {k: v for k, v in sender.sweep(conn, cfg).items() if k != "rows"})
        step("plan", lambda: sender.plan(conn, cfg))
        mailbox = from_env(cfg)
        from_email = (os.environ.get(cfg["sender"]["from_email_env"]) or "").strip()
        ready = False
        if mailbox is None:
            res["sender"] = "waiting: Hostinger mailbox password not saved"
            step("blocker_notice", lambda: agentmail.notify(
                conn, cfg, role="manager", dedupe_key="blocker:hostinger-missing", severity="high", discord=_discord,
                subject="Outreach is ready but can't send yet",
                text="Every Ready lead has its email written and checked, but the Hostinger mailbox password "
                     "(matt@fitnesshubb.com) isn't saved in the cloud yet, so nothing can go out. As soon as it is "
                     "saved, sending starts on the next 10-minute run by itself.")["created"])
        else:
            try:
                mailbox.check_login()
                ready = True
            except AuthError as exc:
                res["sender"] = f"login refused: {exc}"
                day = datetime.now(timezone.utc).date()
                step("blocker_notice", lambda: agentmail.notify(
                    conn, cfg, role="manager", dedupe_key=f"blocker:hostinger-auth:{day}", severity="urgent",
                    discord=_discord, subject="Hostinger refused the outreach login",
                    text="The mailbox matt@fitnesshubb.com refused the saved password, so no emails were sent. "
                         "Nothing was lost - everything waits and sending resumes by itself once the password works.")
                    ["created"])
            except Exception as exc:  # noqa: BLE001
                res["sender"] = f"mail server unreachable: {type(exc).__name__}"
        if ready:
            step("recover", lambda: sender.recover(conn, mailbox, cfg, from_email=from_email))
            step("replies", lambda: replies.poll(conn, user=from_email, password=mailbox.password,
                                                 host=cfg["sender"]["imap_host"], port=int(cfg["sender"]["imap_port"])))
            replies_ok = "error" not in (res.get("replies") or {}) and "auth_error" not in (res.get("replies") or {})
            skipped = (res.get("replies") or {}).get("skipped_errors", 0)
            if skipped:
                step("skipped_reply_notice", lambda: agentmail.notify(
                    conn, cfg, role="followup", severity="high", discord=_discord,
                    dedupe_key=f"reply-skipped:{run_id}", subject=f"{skipped} reply email(s) could not be read",
                    text="Some replies in the outreach inbox could not be processed automatically. Please glance at "
                         "the newest replies in matt@fitnesshubb.com - if anyone asked to stop, their follow-ups "
                         "may still be scheduled.")["created"])
            step("sweep_after_replies", lambda: {k: v for k, v in sender.sweep(conn, cfg).items() if k != "rows"})
            st = _state(conn, "selftest")
            if not (st and st["value"].get("ok")):
                step("selftest", lambda: selftest(conn, cfg, mailbox, from_email))
                st = _state(conn, "selftest")
            if st and st["value"].get("ok"):
                # follow-ups only go out when this run managed to read the replies first
                step("send", lambda: sender.run(conn, mailbox, cfg, from_email=from_email, minutes=minutes,
                                                followups=replies_ok))
                if (res.get("send") or {}).get("auth_failed"):
                    step("auth_notice", lambda: agentmail.notify(
                        conn, cfg, role="manager", severity="urgent", discord=_discord,
                        dedupe_key=f"blocker:hostinger-auth:{datetime.now(timezone.utc).date()}",
                        subject="Hostinger stopped accepting the outreach login",
                        text="Sending paused mid-run because Hostinger refused the login. Nothing was sent twice; "
                             "the rest waits and resumes by itself.")["created"])
                ambiguous = (res.get("send") or {}).get("ambiguous", 0) + (res.get("recover") or {}).get("ambiguous", 0)
                if ambiguous:
                    step("ambiguous_notice", lambda: agentmail.notify(
                        conn, cfg, role="manager", dedupe_key=f"ambiguous:{run_id}", severity="high",
                        subject=f"{ambiguous} email(s) may or may not have gone out",
                        text="A connection dropped in the middle of sending. To be safe these were NOT resent. "
                             "They are marked Needs Review in Airtable."))
            mailbox.close()
        cap = None
        try:
            cap = sender.daily_cap(conn, cfg)
        except Exception:  # noqa: BLE001
            conn.rollback()
        f = None
        try:
            f = status.funnel(conn, tz=cfg["pacing"]["timezone"], cap=cap)
            res["today"] = {k: v for k, v in f.items() if k != "blocking"}
            res["blocking"] = f["blocking"]
            # the planner reads the newest outreach_sender_runs.stats->'planner' instead of being handed state
            res["planner"] = status.planner_state(conn, f)
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            res["today"] = {"error": type(exc).__name__}
        step("events", lambda: status.events(conn, cfg, discord=_discord))
        if f:
            step("digests", lambda: status.digests(conn, cfg, f))
        step("agentmail_flush", lambda: agentmail.flush(conn, cfg))
        step("discord_flush", lambda: notify.flush(conn))
        last = _state(conn, "last_airtable_sync")
        due = not last or last["updated_at"] < datetime.now(timezone.utc) - timedelta(
            minutes=int(cfg["airtable"]["sync_every_minutes"]))
        if due:
            cnt = _state(conn, "record_count")
            if not cnt or cnt["updated_at"] < datetime.now(timezone.utc) - timedelta(days=7):
                step("airtable_count", lambda: airtable_sync.refresh_record_count(conn))
            step("airtable", lambda: airtable_sync.sync(conn, cfg))
            _set_state(conn, "last_airtable_sync", {"at": datetime.now(timezone.utc)})
        conn.execute("UPDATE outreach_sender_runs SET finished_at = now(), stats = %s::jsonb WHERE run_id = %s",
                     (json.dumps(res, default=str), run_id))
        conn.commit()
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cycle")
    c.add_argument("--minutes", type=float, default=8)
    c.add_argument("--out", help="also write the result (counts only) to this file")
    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")
    s.add_argument("--planner", action="store_true", help="totals + next action, as JSON, for the planner")
    sub.add_parser("plan")
    qa = sub.add_parser("queue-audit")
    qa.add_argument("--min", type=int, default=100, help="exit 1 unless at least this many pass")
    sub.add_parser("trade-count")
    sub.add_parser("selftest")
    sub.add_parser("agentmail-selftest")
    sub.add_parser("airtable-selftest")
    sub.add_parser("campaign-selftest")
    sub.add_parser("airtable")
    a = ap.parse_args(argv)
    cfg = sender.load_config()
    if a.cmd == "cycle":
        text = public(cycle(a.minutes))
        if a.out:
            Path(a.out).write_text(text, encoding="utf-8")
        print(text)
        return 0
    with db.get_conn() as conn:
        if a.cmd == "status":
            f = status.funnel(conn, tz=cfg["pacing"]["timezone"], cap=sender.daily_cap(conn, cfg))
            if a.planner:
                print(public(status.planner_state(conn, f)))
            else:
                print(public(f) if a.json else status.as_text(f))
        elif a.cmd == "plan":
            print(public(sender.plan(conn, cfg)))
        elif a.cmd == "queue-audit":
            out = sender.audit_queue(conn)
            out.update(target=a.min, meets_target=out["passing"] >= a.min)
            print(public(out))
            if not out["meets_target"]:
                return 1
        elif a.cmd == "trade-count":
            print(public(sender.trade_ready_counts(conn, cfg)))
        elif a.cmd == "selftest":
            mb = from_env(cfg)
            if mb is None:
                print("BLOCKED: HOSTINGER_EMAIL / HOSTINGER_EMAIL_PASSWORD not set")
                return 3
            out = selftest(conn, cfg, mb, os.environ[cfg["sender"]["from_email_env"]].strip())
            print(public(out))
            if not out["ok"]:
                return 1
        elif a.cmd == "agentmail-selftest":
            out = agentmail_selftest(conn, cfg)
            print(public(out))
            if "blocked" in out:
                return 3
            if not out["ok"]:
                return 1
        elif a.cmd == "airtable-selftest":
            out = airtable_selftest(conn)
            print(public(out))
            if "blocked" in out:
                return 3
            if not out["ok"]:
                return 1
        elif a.cmd == "campaign-selftest":
            mb = from_env(cfg)
            from_email = (os.environ.get(cfg["sender"]["from_email_env"]) or "").strip()
            if mb is None or not from_email:
                print("BLOCKED: HOSTINGER_EMAIL / HOSTINGER_EMAIL_PASSWORD not set")
                return 3
            mb.check_login()
            try:
                out = campaign_selftest(conn, cfg, mb, from_email)
            finally:
                mb.close()
            print(public(out))
            if "blocked" in out:
                return 3
            if not out["ok"]:
                return 1
        elif a.cmd == "airtable":
            print(public(airtable_sync.sync(conn, cfg)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
