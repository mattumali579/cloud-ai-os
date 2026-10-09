import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from cloudos import db
import json

with db.get_conn() as conn:
    q_rows = conn.execute("SELECT * FROM outreach_queue WHERE campaign = 'google_reviews_maps_299'").fetchall()
    c_rows = conn.execute("SELECT * FROM outreach_campaign_copy WHERE campaign = 'google_reviews_maps_299'").fetchall()
    print(f"Loaded {len(q_rows)} queue rows, {len(c_rows)} copy rows.")

    # Also check if these companies exist
    comp_ids = [r["company_id"] for r in q_rows]
    comps = conn.execute("SELECT company_id, company_name, industry, personalization FROM companies WHERE company_id = ANY(%s)", (comp_ids,)).fetchall()
    print(f"Loaded {len(comps)} corresponding company rows.")
