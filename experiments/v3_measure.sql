-- READ-ONLY queries for the v2-vs-v3 copy test. Run by hand against LEADS_DATABASE_URL (SELECT only).

-- Q1. Contact-data quality by industry: which 2-3 niches to narrow to (run BEFORE turning v3 on).
SELECT c.industry,
       count(*)                                                                   AS ready_unsent,
       count(*) FILTER (WHERE c.qualification_status = 'HIGH')                    AS high,
       count(*) FILTER (WHERE ct.email_status = 'validated'
                          AND split_part(lower(ct.email), '@', 2) = c.normalized_domain) AS own_domain_validated,
       count(*) FILTER (WHERE coalesce(c.personalization->>'emergency_service', '') NOT IN ('', 'false')
                           OR coalesce(c.personalization->>'free_estimate_offer', '') NOT IN ('', 'false')) AS has_trade_fact
FROM companies c
JOIN LATERAL (SELECT email, email_status FROM contacts WHERE company_id = c.company_id
              AND email_status IN ('validated', 'published')
              ORDER BY (email_status = 'validated') DESC, (role = 'generic') DESC, discovered_at LIMIT 1) ct ON true
WHERE c.outreach_status IN ('outreach_ready', 'handed_off') AND c.first_contacted_at IS NULL AND c.active
  AND c.qualification_status IN ('HIGH', 'MEDIUM')
GROUP BY c.industry
ORDER BY high DESC, own_domain_validated DESC;

-- Q2. What the live sender actually sent, by industry and copy variant (answers "who got the Oct 1-2 sends").
SELECT c.industry, q.copy_variant, count(*) AS first_touches_sent
FROM outreach_queue q JOIN companies c USING (company_id)
WHERE q.step = 0 AND q.state = 'sent'
GROUP BY 1, 2 ORDER BY 3 DESC;

-- Q3. Per-arm results. Arm = prefix of the FIRST TOUCH's copy_variant (v2-* / v3-*); follow-ups inherit it.
--     Only the v3-eligible industries are compared, so both arms are drawn from the same population.
WITH ft AS (
  SELECT q.company_id, split_part(q.copy_variant, '-', 1) AS arm, q.sent_at,
         t2.sent_at AS touch2_at, t3.sent_at AS touch3_at
  FROM outreach_queue q
  JOIN companies c USING (company_id)
  LEFT JOIN outreach_queue t2 ON t2.company_id = q.company_id AND t2.step = 1 AND t2.state = 'sent'
  LEFT JOIN outreach_queue t3 ON t3.company_id = q.company_id AND t3.step = 2 AND t3.state = 'sent'
  WHERE q.step = 0 AND q.state = 'sent'
    AND q.sent_at >= DATE '2026-10-05'                      -- set to the day v3 went live
    AND c.industry = ANY (ARRAY['hvac', 'plumbing', 'roofing'])
), rep AS (
  SELECT m.company_id, min(m.occurred_at) AS first_reply_at
  FROM outreach_messages m
  WHERE m.direction = 'inbound' AND m.kind NOT IN ('auto_reply', 'bounce')
  GROUP BY m.company_id
)
SELECT ft.arm,
       count(*)                                                                    AS first_touches,
       count(rep.company_id)                                                       AS human_replies,
       count(rep.company_id) FILTER (WHERE NOT coalesce(s.do_not_contact, false))  AS replies_not_optout,
       count(*) FILTER (WHERE s.current_status IN ('interested', 'meeting_requested', 'proposal_needed',
                                                   'proposal_sent', 'negotiating', 'won')) AS positive,
       count(*) FILTER (WHERE s.do_not_contact)                                    AS opted_out,
       count(*) FILTER (WHERE s.bounced)                                           AS bounced,
       count(rep.company_id) FILTER (WHERE rep.first_reply_at < coalesce(ft.touch2_at, 'infinity')) AS replied_after_t1,
       count(rep.company_id) FILTER (WHERE rep.first_reply_at >= ft.touch2_at
                                       AND rep.first_reply_at < coalesce(ft.touch3_at, 'infinity')) AS replied_after_t2,
       count(rep.company_id) FILTER (WHERE rep.first_reply_at >= ft.touch3_at)     AS replied_after_t3,
       round(100.0 * count(rep.company_id) FILTER (WHERE NOT coalesce(s.do_not_contact, false))
             / nullif(count(*), 0), 2)                                             AS reply_rate_pct
FROM ft
LEFT JOIN rep ON rep.company_id = ft.company_id AND rep.first_reply_at > ft.sent_at
LEFT JOIN company_conversation_state s ON s.company_id = ft.company_id
GROUP BY ft.arm ORDER BY ft.arm;
