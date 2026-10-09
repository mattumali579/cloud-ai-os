import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from cloudos import db

with db.get_conn() as conn:
    c = conn.execute("SELECT count(*) as n FROM companies WHERE personalization ? 'google_reviews'").fetchone()["n"]
    print("Companies with google_reviews:", c)
    c_qual = conn.execute("SELECT count(*) as n FROM companies WHERE personalization->'review_campaign'->>'campaign' = 'google_reviews_maps_299'").fetchone()["n"]
    print("Companies qualified for review_campaign:", c_qual)
    copy_count = conn.execute("SELECT count(*) as n FROM outreach_campaign_copy WHERE campaign = 'google_reviews_maps_299'").fetchone()["n"]
    print("outreach_campaign_copy count:", copy_count)
    q_count = conn.execute("SELECT count(*) as n FROM outreach_queue WHERE campaign = 'google_reviews_maps_299'").fetchone()["n"]
    print("outreach_queue count:", q_count)
