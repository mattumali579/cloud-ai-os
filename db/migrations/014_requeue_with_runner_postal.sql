-- 014_requeue_with_runner_postal.sql
-- Clear dummy-addressed queued items so review_campaign.queue_approved
-- dynamically formats and queues each touch with the runner's genuine SENDER_POSTAL_ADDRESS.

DELETE FROM outreach_queue WHERE campaign = 'google_reviews_maps_299' AND state = 'queued';
