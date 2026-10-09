import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from cloudos import db
from cloudos.outreach import review_campaign, sender

with db.get_conn() as conn:
    conn.execute("DELETE FROM outreach_queue WHERE campaign = 'google_reviews_maps_299' AND state = 'queued'")
    addr = sender.postal_address()
    res = review_campaign.queue_approved(conn, postal_address=addr)
    print("Requeue result:", res)
    audit = sender.audit_queue(conn)
    print("Audit result:", audit)
