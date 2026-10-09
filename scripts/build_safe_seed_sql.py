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
    comps = conn.execute("SELECT * FROM companies WHERE company_id = ANY(%s)", (comp_ids,)).fetchall()
    contacts = conn.execute("SELECT * FROM contacts WHERE company_id = ANY(%s)", (comp_ids,)).fetchall()

    lines = []
    lines.append("-- 013_seed_review_campaign_291.sql")
    lines.append("-- Safely update existing companies by normalized_domain / company_id, seed contacts, copy, and queue.")
    lines.append("")

    # Update companies by normalized_domain or company_id
    lines.append("-- Update companies")
    for comp in comps:
        cid = comp["company_id"]
        ndom = comp["normalized_domain"]
        pers_json = sql_escape(json.dumps(comp["personalization"]))
        qstatus = sql_escape(comp["qualification_status"])
        ostatus = sql_escape(comp["outreach_status"])
        
        if ndom:
            lines.append(
                f"UPDATE companies SET personalization = jsonb_set(coalesce(personalization, '{{}}'::jsonb), '{{review_campaign}}', ({pers_json}::jsonb)->'review_campaign', true), "
                f"qualification_status = {qstatus}, outreach_status = {ostatus}, updated_at = now() "
                f"WHERE normalized_domain = {sql_escape(ndom)} OR company_id = '{cid}'::uuid;"
            )
        else:
            lines.append(
                f"UPDATE companies SET personalization = jsonb_set(coalesce(personalization, '{{}}'::jsonb), '{{review_campaign}}', ({pers_json}::jsonb)->'review_campaign', true), "
                f"qualification_status = {qstatus}, outreach_status = {ostatus}, updated_at = now() "
                f"WHERE company_id = '{cid}'::uuid;"
            )
    lines.append("")

    # Upsert contacts using matching company_id
    lines.append("-- Upsert contacts")
    for ct in contacts:
        cid = ct["company_id"]
        em = sql_escape(ct["email"])
        estatus = sql_escape(ct["email_status"])
        role = sql_escape(ct["role"])
        src = sql_escape(ct["source"])
        srcurl = sql_escape(ct["source_url"])
        comp = next((c for c in comps if c["company_id"] == cid), None)
        ndom = comp["normalized_domain"] if comp else None

        if ndom:
            lines.append(
                f"INSERT INTO contacts (company_id, email, email_status, role, source, source_url) "
                f"SELECT c.company_id, {em}, {estatus}, {role}, {src}, {srcurl} "
                f"FROM companies c WHERE (c.normalized_domain = {sql_escape(ndom)} OR c.company_id = '{cid}'::uuid) "
                f"AND NOT EXISTS (SELECT 1 FROM contacts ct2 WHERE ct2.company_id = c.company_id AND lower(trim(ct2.email)) = lower(trim({em}))) "
                f"LIMIT 1;"
            )
        else:
            lines.append(
                f"INSERT INTO contacts (company_id, email, email_status, role, source, source_url) "
                f"SELECT c.company_id, {em}, {estatus}, {role}, {src}, {srcurl} "
                f"FROM companies c WHERE c.company_id = '{cid}'::uuid "
                f"AND NOT EXISTS (SELECT 1 FROM contacts ct2 WHERE ct2.company_id = c.company_id AND lower(trim(ct2.email)) = lower(trim({em}))) "
                f"LIMIT 1;"
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
        comp = next((c for c in comps if c["company_id"] == cid), None)
        ndom = comp["normalized_domain"] if comp else None

        if ndom:
            lines.append(
                f"INSERT INTO outreach_campaign_copy (company_id, campaign, step, subject, body, copy_version, evidence, qa_problems, qa_passed_at) "
                f"SELECT c.company_id, {sql_escape(camp)}, {step}, {sql_escape(subj)}, {sql_escape(body)}, {sql_escape(cver)}, {sql_escape(ev)}::jsonb, {sql_escape(qp)}::jsonb, now() "
                f"FROM companies c WHERE (c.normalized_domain = {sql_escape(ndom)} OR c.company_id = '{cid}'::uuid) "
                f"ON CONFLICT (company_id, campaign, step) DO NOTHING;"
            )
        else:
            lines.append(
                f"INSERT INTO outreach_campaign_copy (company_id, campaign, step, subject, body, copy_version, evidence, qa_problems, qa_passed_at) "
                f"SELECT c.company_id, {sql_escape(camp)}, {step}, {sql_escape(subj)}, {sql_escape(body)}, {sql_escape(cver)}, {sql_escape(ev)}::jsonb, {sql_escape(qp)}::jsonb, now() "
                f"FROM companies c WHERE c.company_id = '{cid}'::uuid "
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
        comp = next((c for c in comps if c["company_id"] == cid), None)
        ndom = comp["normalized_domain"] if comp else None

        if ndom:
            lines.append(
                f"INSERT INTO outreach_queue (company_id, step, recipient, subject, body, copy_variant, state, campaign, copy_source, evidence) "
                f"SELECT c.company_id, {step}, {sql_escape(recip)}, {sql_escape(subj)}, {sql_escape(body)}, {sql_escape(cvar)}, {sql_escape(state)}, {sql_escape(camp)}, {sql_escape(csrc)}, {sql_escape(ev)}::jsonb "
                f"FROM companies c WHERE (c.normalized_domain = {sql_escape(ndom)} OR c.company_id = '{cid}'::uuid) "
                f"ON CONFLICT (company_id, step) DO NOTHING;"
            )
        else:
            lines.append(
                f"INSERT INTO outreach_queue (company_id, step, recipient, subject, body, copy_variant, state, campaign, copy_source, evidence) "
                f"SELECT c.company_id, {step}, {sql_escape(recip)}, {sql_escape(subj)}, {sql_escape(body)}, {sql_escape(cvar)}, {sql_escape(state)}, {sql_escape(camp)}, {sql_escape(csrc)}, {sql_escape(ev)}::jsonb "
                f"FROM companies c WHERE c.company_id = '{cid}'::uuid "
                f"ON CONFLICT (company_id, step) DO NOTHING;"
            )

    out_path = Path("db/migrations/013_seed_review_campaign_291.sql")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Generated safe {out_path} with {len(lines)} lines.")
