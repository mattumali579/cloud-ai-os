from cloudos import db

with db.get_conn() as conn:
    print("--- outreach_queue ---")
    rows = conn.execute("SELECT state, step, count(queue_id) as n FROM outreach_queue GROUP BY state, step").fetchall()
    for r in rows:
        print(f"  state={r['state']}, step={r['step']}, count={r['n']}")
    if not rows:
        print("  EMPTY")

    print("\n--- outreach_campaign_copy ---")
    rows = conn.execute("SELECT campaign, step, (qa_passed_at IS NOT NULL) as qa_passed, count(company_id) as n FROM outreach_campaign_copy GROUP BY campaign, step, (qa_passed_at IS NOT NULL)").fetchall()
    for r in rows:
        print(f"  campaign={r['campaign']}, step={r['step']}, qa_passed={r['qa_passed']}, count={r['n']}")
    if not rows:
        print("  EMPTY")

    print("\n--- audit_queue ---")
    from cloudos.outreach import sender
    print(sender.audit_queue(conn))
