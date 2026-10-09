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
    lines.append("-- Populate verified companies, contacts, approved Hormozi Google Reviews / Maps copy and 291 queued leads.")
    lines.append("")

    # Upsert companies
    lines.append("-- Upsert companies")
    for comp in comps:
        cid = comp["company_id"]
        cname = sql_escape(comp["company_name"])
        nname = sql_escape(comp["normalized_name"])
        dom = sql_escape(comp["domain"])
        ndom = sql_escape(comp["normalized_domain"])
        web = sql_escape(comp["website"])
        ind = sql_escape(comp["industry"])
        city = sql_escape(comp["city"])
        state = sql_escape(comp["state"])
        cntry = sql_escape(comp["country"])
        ph = sql_escape(comp["phone"])
        nph = sql_escape(comp["normalized_phone"])
        source = sql_escape(comp["discovery_source"])
        qstatus = sql_escape(comp["qualification_status"])
        qreason = sql_escape(comp["qualification_reason"])
        ostatus = sql_escape(comp["outreach_status"])
        pers_json = sql_escape(json.dumps(comp["personalization"]))
        active = "true" if comp["active"] else "false"

        lines.append(
            f"INSERT INTO companies (company_id, company_name, normalized_name, domain, normalized_domain, website, industry, city, state, country, phone, normalized_phone, discovery_source, qualification_status, qualification_reason, personalization, outreach_status, active, updated_at) "
            f"VALUES ('{cid}'::uuid, {cname}, {nname}, {dom}, {ndom}, {web}, {ind}, {city}, {state}, {cntry}, {ph}, {nph}, {source}, {qstatus}, {qreason}, {pers_json}::jsonb, {ostatus}, {active}, now()) "
            f"ON CONFLICT (company_id) DO UPDATE SET personalization = EXCLUDED.personalization, qualification_status = EXCLUDED.qualification_status, outreach_status = EXCLUDED.outreach_status, active = EXCLUDED.active, updated_at = now();"
        )
    lines.append("")

    # Upsert contacts
    lines.append("-- Upsert contacts")
    for ct in contacts:
        ctid = ct["contact_id"]
        cid = ct["company_id"]
        em = sql_escape(ct["email"])
        estatus = sql_escape(ct["email_status"])
        role = sql_escape(ct["role"])
        src = sql_escape(ct["source"])
        srcurl = sql_escape(ct["source_url"])
        lines.append(
            f"INSERT INTO contacts (contact_id, company_id, email, email_status, role, source, source_url) "
            f"VALUES ('{ctid}'::uuid, '{cid}'::uuid, {em}, {estatus}, {role}, {src}, {srcurl}) "
            f"ON CONFLICT (contact_id) DO UPDATE SET email_status = EXCLUDED.email_status;"
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
