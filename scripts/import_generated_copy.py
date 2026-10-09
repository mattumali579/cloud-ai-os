import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cloudos import db
from cloudos.outreach import review_campaign
from scripts.generate_campaign_copy import build_unique_copies

with db.get_conn() as conn:
    leads = review_campaign.selected(conn)
    print(f"Loaded {len(leads)} selected leads.")
    copies = build_unique_copies(leads)
    
    # Exclude any that fail QA
    leads_map = {l["company_id"]: l for l in leads}
    valid_copies = []
    for c in copies:
        lead = leads_map[c["company_id"]]
        if not review_campaign.qa_copy(c["subject"], c["body"], lead):
            valid_copies.append(c)
            
    print(f"Valid QA-passing copies to import: {len(valid_copies)}")
    text_data = json.dumps(valid_copies)
    res = review_campaign.import_copy(conn, text_data)
    print("Import copy result:", res)
    
    # Check approved count
    approved = review_campaign.approved_count(conn)
    print(f"Total approved count in database: {approved}")
