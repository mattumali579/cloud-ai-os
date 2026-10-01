"""The numbers, straight from the database, for one America/Chicago day - and what
is blocking progress toward today's 300 Ready. Every figure says what it counts.

Also turns new events (replies, stopped sequences, failures) into AgentMail
notices, and writes the three once-a-day digests.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from cloudos.outreach import agentmail

DAY = "(date_trunc('day', now() AT TIME ZONE %(tz)s) AT TIME ZONE %(tz)s)"
POSITIVE = ("INTERESTED", "MEETING_REQUEST", "READY_TO_BUY", "PRICE_QUESTION", "MORE_INFORMATION", "REFERRAL")


def funnel(conn, *, tz: str = "America/Chicago", ready_target: int = 300, cap: int | None = None) -> dict:
    p = {"tz": tz}
    one = lambda sql: conn.execute(sql.replace("{DAY}", DAY), p).fetchone()["n"]  # noqa: E731
    f = {
        "day": datetime.now(ZoneInfo(tz)).date().isoformat(),
        "discovered_today": one("SELECT count(*) n FROM companies WHERE NOT is_historical AND discovered_at >= {DAY}"),
        "researched_today": one("SELECT count(*) n FROM companies WHERE NOT is_historical AND discovered_at >= {DAY} "
                                "AND personalization <> '{}'::jsonb"),
        "emails_found_today": one("SELECT count(DISTINCT company_id) n FROM contacts WHERE discovered_at >= {DAY} "
                                  "AND email_status IN ('validated','published')"),
        "rejected_today": one("SELECT count(*) n FROM companies WHERE NOT is_historical AND discovered_at >= {DAY} "
                              "AND outreach_status = 'rejected'"),
        "no_usable_email_today": one("SELECT count(*) n FROM companies WHERE NOT is_historical AND discovered_at >= {DAY} "
                                     "AND outreach_status = 'not_ready'"),
        "duplicates_today": one("SELECT count(*) n FROM discovery_history WHERE outcome = 'duplicate' AND discovered_at >= {DAY}"),
        "ready_today": one("SELECT count(*) n FROM companies WHERE ready_at >= {DAY}"),
        "ready_waiting_total": one("SELECT count(*) n FROM companies WHERE outreach_status IN ('outreach_ready','handed_off') "
                                   "AND first_contacted_at IS NULL AND active"),
        "queued_first_touches": one("SELECT count(*) n FROM outreach_queue WHERE state = 'queued' AND step = 0"),
        "sent_today": one("SELECT count(*) n FROM outreach_queue WHERE state = 'sent' AND step = 0 AND sent_at >= {DAY}"),
        "followups_sent_today": one("SELECT count(*) n FROM outreach_queue WHERE state = 'sent' AND step > 0 "
                                    "AND sent_at >= {DAY}"),
        "failed_today": one("SELECT count(*) n FROM outreach_queue WHERE state IN ('failed','ambiguous') "
                            "AND updated_at >= {DAY}"),
        "replies_today": one("SELECT count(*) n FROM outreach_messages WHERE direction = 'inbound' "
                             "AND kind NOT IN ('auto_reply','bounce') AND occurred_at >= {DAY}"),
        "interested_today": one("SELECT count(*) n FROM reply_analyses WHERE created_at >= {DAY} AND classification IN "
                                "('INTERESTED','MEETING_REQUEST','READY_TO_BUY','PRICE_QUESTION','MORE_INFORMATION')"),
        "unsubscribed_today": one("SELECT count(*) n FROM email_suppressions WHERE reason IN ('unsubscribe','legal') "
                                  "AND created_at >= {DAY}"),
        "bounced_today": one("SELECT count(*) n FROM email_suppressions WHERE reason = 'hard_bounce' AND created_at >= {DAY}"),
        "sent_all_time_hostinger": one("SELECT count(*) n FROM outreach_queue WHERE state = 'sent'"),
        "followups_waiting": one("SELECT count(*) n FROM outreach_queue WHERE state = 'queued' AND step > 0"),
    }
    f["ready_target"] = ready_target
    f["ready_gap"] = max(ready_target - f["ready_today"], 0)
    if cap is not None:
        f["send_cap_today"] = cap
    f["blocking"] = blockers(conn, f)
    return f


def blockers(conn, f: dict) -> list[str]:
    out = []
    import os
    # The mailbox secrets live only in GitHub Actions, where the sender runs. A local process
    # not having them says nothing about whether the cloud run can send.
    in_cloud = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
    if in_cloud and not (os.environ.get("HOSTINGER_EMAIL") and os.environ.get("HOSTINGER_EMAIL_PASSWORD")):
        out.append("Hostinger mailbox password not saved yet - emails are written and waiting, none can go out")
    if not os.environ.get("AGENTMAIL_API_KEY"):
        out.append("AgentMail key not saved yet - status mail waits (urgent items go to Discord)")
    run = conn.execute("SELECT started_at, finished_at, stats FROM lead_runs ORDER BY started_at DESC LIMIT 1").fetchone()
    if run and isinstance(run["stats"], dict) and run["stats"].get("error"):
        out.append("last lead-finder run failed: " + str(run["stats"]["error"])[:120])
    if f["ready_gap"] > 0 and f["discovered_today"] and f["ready_today"] / max(f["discovered_today"], 1) < 0.15:
        out.append(f"only {f['ready_today']} of {f['discovered_today']} companies found today had a usable email "
                   "- the finder keeps searching more cities/industries")
    health = conn.execute("SELECT source, consecutive_failures FROM lead_source_health WHERE consecutive_failures >= 3"
                          ).fetchall() if _has(conn, "lead_source_health", "consecutive_failures") else []
    for h in health:
        out.append(f"lead source {h['source']} is benched after {h['consecutive_failures']} failures in a row")
    return out


def planner_state(conn, f: dict) -> dict:
    """Machine-readable state for the planner: the pipeline totals plus the next action the
    system itself would take, so nothing has to be pasted in or re-asked."""
    one = lambda sql: conn.execute(sql).fetchone()["n"]  # noqa: E731
    s = {
        "day": f["day"],
        "discovered_total": one("SELECT count(*) n FROM companies WHERE NOT is_historical"),
        "discovered_today": f["discovered_today"],
        "ready_waiting": f["ready_waiting_total"],
        "ready_today": f["ready_today"],
        "ready_target": f["ready_target"],
        "target_remaining": f["ready_gap"],
        "queued": f["queued_first_touches"],
        "claimed": one("SELECT count(*) n FROM outreach_queue WHERE state = 'claimed'"),
        "sent_today": f["sent_today"],
        "sent_total": f["sent_all_time_hostinger"],
        "send_cap_today": f.get("send_cap_today"),
        "failed_today": f["failed_today"],
        "ambiguous_total": one("SELECT count(*) n FROM outreach_queue WHERE state = 'ambiguous'"),
        "replied_total": one("SELECT count(DISTINCT company_id) n FROM outreach_messages WHERE direction = 'inbound' "
                             "AND kind NOT IN ('auto_reply','bounce') AND company_id IS NOT NULL"),
        "interested_total": one("SELECT count(DISTINCT company_id) n FROM reply_analyses WHERE classification IN "
                                "('INTERESTED','MEETING_REQUEST','READY_TO_BUY','PRICE_QUESTION','MORE_INFORMATION')"),
        "suppressed_total": one("SELECT count(*) n FROM email_suppressions"),
        "blocking": f["blocking"],
    }
    s["next_action"] = next_action(s)
    return s


def next_action(s: dict) -> dict:
    """First match wins: a human-only blocker, then money on the table, then broken sends,
    then supply; otherwise the scheduled jobs already do the work and nothing is needed."""
    hard = [b for b in s["blocking"] if b.startswith("Hostinger")]
    if hard:
        return {"decision": "human_review", "agent": None, "task": "save the Hostinger mailbox password as the "
                "HOSTINGER_EMAIL_PASSWORD secret; the next 10-minute run sends the self-test, then prospects",
                "why": hard[0]}
    if s["interested_total"]:
        return {"decision": "execute", "agent": "claude", "task": "draft replies for interested prospects in "
                "company_conversation_state (drafts only; a person approves anything customer-facing)",
                "why": f"{s['interested_total']} interested prospect(s)"}
    if s["failed_today"] or s["ambiguous_total"]:
        return {"decision": "execute", "agent": "codex", "task": "diagnose failed/ambiguous rows in outreach_queue",
                "why": f"{s['failed_today']} failed today, {s['ambiguous_total']} ambiguous"}
    if s["target_remaining"] and s["ready_waiting"] < (s["send_cap_today"] or 0) * 5:
        return {"decision": "execute", "agent": "codex", "task": "check why the lead engine is short of Ready",
                "why": f"{s['target_remaining']} Ready still needed today"}
    return {"decision": "stop", "agent": None, "task": "none - the 10-minute sender and lead engine continue on schedule",
            "why": "nothing is blocked"}


def _has(conn, table: str, col: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
                             (table, col)).fetchone())


def as_text(f: dict) -> str:
    lines = [f"BrightReach numbers for {f['day']} (Central time)", "",
             f"Found today: {f['discovered_today']} new businesses, {f['researched_today']} of them researched",
             f"Usable email found: {f['emails_found_today']} businesses",
             f"Not counted: {f['rejected_today']} rejected, {f['no_usable_email_today']} no usable email, "
             f"{f['duplicates_today']} duplicates of past leads",
             f"READY today: {f['ready_today']} of {f['ready_target']} (still needed: {f['ready_gap']})",
             f"Ready and waiting to be emailed (all days): {f['ready_waiting_total']}",
             "",
             f"First emails sent today: {f['sent_today']}" + (f" (today's limit {f['send_cap_today']})"
                                                              if "send_cap_today" in f else ""),
             f"Follow-ups sent today: {f['followups_sent_today']} ({f['followups_waiting']} scheduled)",
             f"Failed sends today: {f['failed_today']}",
             f"Replies today: {f['replies_today']} ({f['interested_today']} interested or asking)",
             f"Unsubscribed today: {f['unsubscribed_today']}, bounced: {f['bounced_today']}"]
    lines += ["", "What's in the way:"] + ([f"- {b}" for b in f["blocking"]] or ["- nothing"])
    return "\n".join(lines)


# ------------------------------------------------------------------ events
def events(conn, cfg: dict, post=None, discord=None) -> dict:
    """New replies and stopped sequences -> one AgentMail notice each (dedupe keys make reruns free)."""
    n = {"manager": 0, "followup": 0}
    rows = conn.execute(
        """
        SELECT ra.message_id::text mid, ra.classification, ra.interpretation, ra.recommended_action,
               c.company_name, m.sender, left(m.body, 600) body, m.company_id::text cid
        FROM reply_analyses ra JOIN outreach_messages m ON m.message_id = ra.message_id
        LEFT JOIN companies c ON c.company_id = m.company_id
        WHERE ra.created_at > now() - interval '3 days' AND m.provider = 'hostinger'
        ORDER BY ra.created_at
        """).fetchall()
    for r in rows:
        label = r["classification"]
        who = r["company_name"] or "an unknown sender"
        said = " ".join((r["body"] or "").split())[:400]
        if label in POSITIVE or label in ("OBJECTION", "NEEDS_REVIEW"):
            res = agentmail.notify(conn, cfg, role="manager", dedupe_key=f"reply:{r['mid']}", severity="high",
                                   company_id=r["cid"], post=post, discord=discord,
                                   subject=f"{who} replied: {label.replace('_', ' ').lower()}",
                                   text=f"{who} ({r['sender']}) wrote back.\n\nThey said: \"{said}\"\n\n"
                                        f"What it means: {r['interpretation']}\nWhat to do: {r['recommended_action']}\n\n"
                                        "Automatic follow-ups to them are stopped. A suggested reply is in the drafts.")
            n["manager"] += int(res["created"])
        if label == "DELIVERY_FAILURE":
            continue
        res = agentmail.notify(conn, cfg, role="followup", dedupe_key=f"seq:{r['mid']}", post=post,
                               company_id=r["cid"],
                               subject=f"Follow-ups stopped: {who} ({label.replace('_', ' ').lower()})",
                               text=f"{who} replied ({label.replace('_', ' ').lower()}). Their follow-up emails are "
                                    f"cancelled." + (" They are on the do-not-email list for good."
                                                     if label == "UNSUBSCRIBE" else ""))
        n["followup"] += int(res["created"])
    return n


def digests(conn, cfg: dict, f: dict, post=None) -> dict:
    """Once a day, after the digest hour, one summary per inbox."""
    tz = ZoneInfo(cfg["pacing"]["timezone"])
    now = datetime.now(timezone.utc).astimezone(tz)
    if now.hour < int(cfg["agentmail"]["digest_hour"]):
        return {"due": False}
    day = now.date().isoformat()
    text = as_text(f)
    out = {"due": True}
    out["manager"] = agentmail.notify(conn, cfg, role="manager", dedupe_key=f"digest:manager:{day}", post=post,
                                      subject=f"Daily summary {day}: {f['sent_today']} sent, {f['replies_today']} replies",
                                      text=text)["created"]
    out["research"] = agentmail.notify(
        conn, cfg, role="research", dedupe_key=f"digest:research:{day}", post=post,
        subject=f"Research {day}: {f['ready_today']}/{f['ready_target']} Ready",
        text=(f"Ready today: {f['ready_today']} of {f['ready_target']}.\nFound {f['discovered_today']} new businesses; "
              f"{f['emails_found_today']} had a usable email; {f['duplicates_today']} were already in our history; "
              f"{f['rejected_today']} were rejected (chains, dead sites, not a fit).\n\n"
              + ("\n".join(f"- {b}" for b in f["blocking"]) or "Nothing is blocking research.")))["created"]
    out["followup"] = agentmail.notify(
        conn, cfg, role="followup", dedupe_key=f"digest:followup:{day}", post=post,
        subject=f"Follow-ups {day}: {f['followups_sent_today']} sent, {f['replies_today']} replies",
        text=(f"Follow-ups sent today: {f['followups_sent_today']}\nScheduled next: {f['followups_waiting']}\n"
              f"Replies today: {f['replies_today']} ({f['interested_today']} interested or asking)\n"
              f"Unsubscribed: {f['unsubscribed_today']}  Bounced: {f['bounced_today']}"))["created"]
    return out
