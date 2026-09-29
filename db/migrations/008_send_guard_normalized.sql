-- 008_send_guard_normalized.sql - the send guard matches an address however it is written.
--
-- 'John.Smith+promo@googlemail.com' and 'johnsmith@gmail.com' are one mailbox. Before this, an
-- unsubscribe from one spelling did not block the other. outreach_norm_email() is the one
-- definition of "same address": lower-case, trimmed, '+tag' removed, and for Gmail the dots
-- in the name removed and googlemail.com folded into gmail.com. outreach_send_check() is
-- otherwise unchanged from 007 - every lookup by address now goes through the normal form.

CREATE OR REPLACE FUNCTION outreach_norm_email(p text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN position('@' IN e) = 0 THEN e ELSE
             (CASE WHEN d IN ('gmail.com', 'googlemail.com') THEN replace(l, '.', '') ELSE l END)
             || '@' || (CASE WHEN d = 'googlemail.com' THEN 'gmail.com' ELSE d END) END
      FROM (SELECT e, split_part(split_part(e, '@', 1), '+', 1) AS l, split_part(e, '@', 2) AS d
              FROM (SELECT lower(trim(coalesce(p, ''))) AS e) a) b
$$;

CREATE INDEX IF NOT EXISTS idx_suppression_norm_email ON email_suppressions (outreach_norm_email(email)) WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_contacts_norm_email ON contacts (outreach_norm_email(email));

CREATE OR REPLACE FUNCTION outreach_send_check(p_email text, p_kind text, p_company_id uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_email   text := lower(trim(coalesce(p_email, '')));
    v_norm    text := outreach_norm_email(p_email);
    v_domain  text := split_part(outreach_norm_email(p_email), '@', 2);
    v_company uuid := p_company_id;
    v_state   company_conversation_state%ROWTYPE;
    v_status  text;
    v_reasons text[] := '{}';
    v_generic boolean := p_kind IN ('cold', 'followup');
BEGIN
    IF p_kind NOT IN ('cold','followup','reply','pricing','audit','proposal','referral_intro','objection') THEN
        RETURN jsonb_build_object('allowed', false, 'reasons', jsonb_build_array('unknown_kind'));
    END IF;
    IF v_email !~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' THEN
        RETURN jsonb_build_object('allowed', false, 'reasons', jsonb_build_array('invalid_email'));
    END IF;
    IF v_company IS NULL THEN
        SELECT company_id INTO v_company FROM contacts WHERE outreach_norm_email(email) = v_norm LIMIT 1;
    END IF;
    IF v_company IS NULL THEN
        SELECT company_id INTO v_company FROM outreach_messages
         WHERE company_id IS NOT NULL AND (outreach_norm_email(recipient) = v_norm OR outreach_norm_email(sender) = v_norm)
         ORDER BY occurred_at DESC LIMIT 1;
    END IF;
    IF v_company IS NULL AND v_domain <> '' THEN
        -- another address at a known company's own website domain (lead engine never stores freemail here)
        SELECT company_id INTO v_company FROM companies WHERE normalized_domain = v_domain LIMIT 1;
    END IF;

    IF EXISTS (SELECT 1 FROM email_suppressions WHERE email IS NOT NULL AND outreach_norm_email(email) = v_norm) THEN
        v_reasons := array_append(v_reasons, 'email_suppressed'::text); END IF;
    IF v_domain <> '' AND EXISTS (SELECT 1 FROM email_suppressions WHERE domain = v_domain) THEN
        v_reasons := array_append(v_reasons, 'domain_suppressed'::text); END IF;

    IF v_company IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM email_suppressions WHERE company_id = v_company) THEN
            v_reasons := array_append(v_reasons, 'company_suppressed'::text); END IF;
        SELECT * INTO v_state FROM company_conversation_state WHERE company_id = v_company;
        IF FOUND THEN
            v_status := v_state.current_status;
            IF v_state.do_not_contact OR v_status = 'do_not_contact' THEN v_reasons := array_append(v_reasons, 'do_not_contact'::text); END IF;
            IF v_status = 'needs_review' THEN v_reasons := array_append(v_reasons, 'needs_review_pending'::text); END IF;
            IF v_state.bounced AND v_generic THEN v_reasons := array_append(v_reasons, 'bounced'::text); END IF;
            IF v_generic AND v_status NOT IN ('discovered','ready','emailed') THEN
                v_reasons := array_append(v_reasons, ('status_' || v_status || '_stops_generic_sequence')::text); END IF;
            IF v_generic AND v_status = 'emailed' AND NOT v_state.cold_sequence_active AND p_kind = 'followup' THEN
                v_reasons := array_append(v_reasons, 'sequence_stopped'::text); END IF;
        ELSE
            SELECT CASE outreach_status WHEN 'unsubscribed' THEN 'do_not_contact' WHEN 'do_not_contact' THEN 'do_not_contact'
                   ELSE NULL END INTO v_status FROM companies WHERE company_id = v_company;
            IF v_status = 'do_not_contact' THEN v_reasons := array_append(v_reasons, 'do_not_contact'::text); END IF;
        END IF;
        IF v_generic AND EXISTS (SELECT 1 FROM outreach_messages WHERE company_id = v_company AND direction = 'inbound'
                                 AND kind NOT IN ('auto_reply','bounce')) THEN
            v_reasons := array_append(v_reasons, 'reply_received'::text); END IF;
        IF EXISTS (SELECT 1 FROM human_attention_queue WHERE company_id = v_company AND state = 'open'
                   AND reason_code IN ('legal_or_angry','conflict_unsubscribe_and_positive')) THEN
            v_reasons := array_append(v_reasons, 'owner_must_handle'::text); END IF;
    END IF;

    -- duplicate first touch: any confirmed send to this company or address already
    IF p_kind = 'cold' AND (
         EXISTS (SELECT 1 FROM outreach_messages WHERE direction = 'outbound'
                 AND ((v_company IS NOT NULL AND company_id = v_company) OR outreach_norm_email(recipient) = v_norm))
      OR EXISTS (SELECT 1 FROM outreach_history WHERE status = 'sent'
                 AND ((v_company IS NOT NULL AND company_id = v_company) OR outreach_norm_email(email) = v_norm))
      OR EXISTS (SELECT 1 FROM companies WHERE company_id = v_company AND first_contacted_at IS NOT NULL)) THEN
        v_reasons := array_append(v_reasons, 'already_contacted'::text);
    END IF;
    IF p_kind = 'followup' AND v_company IS NULL
       AND NOT EXISTS (SELECT 1 FROM outreach_history WHERE outreach_norm_email(email) = v_norm AND status = 'sent') THEN
        v_reasons := array_append(v_reasons, 'no_first_touch_on_record'::text);
    END IF;

    RETURN jsonb_build_object('allowed', cardinality(v_reasons) = 0, 'reasons', to_jsonb(v_reasons),
                              'company_id', v_company, 'status', v_status, 'kind', p_kind, 'checked_at', now());
END $$;
