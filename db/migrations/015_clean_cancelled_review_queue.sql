-- 015_clean_cancelled_review_queue.sql
-- Purge previously cancelled queue entries for google_reviews_maps_299
-- so review_campaign.queue_approved cleanly queues the qualified leads.

DELETE FROM outreach_queue WHERE campaign = 'google_reviews_maps_299' AND state = 'cancelled';
