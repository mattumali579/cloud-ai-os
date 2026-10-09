import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from cloudos import db
import json

def sql_escape(s):
    if s is None:
        return "NULL"
    return "'" + str(s).replace("'", "''") + "'"

with db.get_conn() as conn:
    q_rows = conn.execute("SELECT * FROM outreach_queue WHERE campaign = 'google_reviews_maps_299' ORDER BY queue_id").fetchall()
    c_rows = conn.execute("SELECT * FROM outreach_campaign_copy WHERE campaign = 'google_reviews_maps_299' ORDER BY company_id").fetchall()
    comp_ids = [r["company_id"] for r in q_rows]
    comps = conn.execute("SELECT company_id, personalization, qualification_status, outreach_status FROM companies WHERE company_id = ANY(%s)", (comp_ids,)).fetchall()

    lines = []
    lines.append("-- 013_seed_review_campaign_291.sql")
    lines.append("-- Populate approved Hormozi Google Reviews / Maps copy and queue 291 verified leads.")
    lines.append("")

    # Update company personalizations
    for comp in comps:
        cid = comp["company_id"]
        pers_json = json.dumps(comp["personalization"])
        lines.append(
            f"UPDATE companies SET personalization = {sql_escape(pers_json)}::jsonb, "
            f"qualification_status = {sql_escape(comp['qualification_status'])}, "
            f"outreach_status = {sql_escape(comp['outreach_status'])}, updated_at = now() "
            f"WHERE company_id = '{cid}'::uuid;"
        )
    lines.append("")

    # Insert outreach_campaign_copy
    lines.append("-- Seed outreach_campaign_copy")
    for r in c_rows:
        cid = r["company_id"]
        camp = r["campaign"]
        step = r["step"]
        subj = r["subject"]
        body = r["body"]
        cver = r["copy_version"]
        ev = json.dumps(r["evidence"] or {})
        qp = json.dumps(r["qa_problems"] or [])
        lines.append(
            f"INSERT INTO outreach_campaign_copy (company_id, campaign, step, subject, body, copy_version, evidence, qa_problems, qa_passed_at) "
            f"VALUES ('{cid}'::uuid, {sql_escape(camp)}, {step}, {sql_escape(subj)}, {sql_escape(body)}, {sql_escape(cver)}, {sql_escape(ev)}::jsonb, {sql_escape(qp)}::jsonb, now()) "
            f"ON CONFLICT (company_id, campaign, step) DO NOTHING;"
        )
    lines.append("")

    # Insert outreach_queue
    lines.append("-- Seed outreach_queue")
    for r in q_rows:
        cid = r["company_id"]
        step = r["step"]
        recip = r["recipient"]
        subj = r["subject"]
        body = r["body"]
        cvar = r["copy_variant"]
        state = r["state"]
        camp = r["campaign"]
        csrc = r["copy_source"]
        ev = json.dumps(r["evidence"] or {})
        lines.append(
            f"INSERT INTO outreach_queue (company_id, step, recipient, subject, body, copy_variant, state, campaign, copy_source, evidence) "
            f"VALUES ('{cid}'::uuid, {step}, {sql_escape(recip)}, {sql_escape(subj)}, {sql_escape(body)}, {sql_escape(cvar)}, {sql_escape(state)}, {sql_escape(camp)}, {sql_escape(csrc)}, {sql_escape(ev)}::jsonb) "
            f"ON CONFLICT (company_id, step) DO NOTHING;"
        )

    out_path = Path("db/migrations/013_seed_review_campaign_291.sql")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Generated {out_path} with {len(lines)} lines.")
